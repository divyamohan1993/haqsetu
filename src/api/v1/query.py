"""Main query endpoints for HaqSetu API v1.

Provides text and voice query endpoints that delegate to the
:class:`QueryOrchestrator` for full pipeline processing.
"""

from __future__ import annotations

import base64
from uuid import uuid4

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from src.models.enums import ChannelType, ContentType, LanguageCode

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(tags=["query"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TextQueryRequest(BaseModel):
    """Body for the POST /api/v1/query endpoint."""

    text: str = Field(..., min_length=1, max_length=2000, description="User query text")
    session_id: str | None = Field(default=None, description="Session identifier for conversation continuity")
    language: str | None = Field(default=None, description="ISO 639-1 language code (auto-detected if omitted)")
    channel: str = Field(default="web", description="Channel type: web, whatsapp, ivr, etc.")


class TextQueryResponse(BaseModel):
    """Response body for text queries."""

    response: str
    language: str
    session_id: str
    schemes: list[dict]
    latency: dict


class VoiceQueryResponse(BaseModel):
    """Response body for voice queries."""

    response_text: str
    response_audio_base64: str | None = None
    language: str
    session_id: str
    latency: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/query", response_model=TextQueryResponse)
async def text_query(body: TextQueryRequest, request: Request) -> TextQueryResponse:
    """Process a text query through the full HaqSetu pipeline.

    Detects language, translates, classifies intent, searches schemes,
    generates a response, and translates back to the user's language.
    """
    orchestrator = request.app.state.orchestrator

    session_id = body.session_id or uuid4().hex

    # Resolve language enum
    lang_enum: LanguageCode | None = None
    if body.language:
        try:
            lang_enum = LanguageCode(body.language)
        except ValueError:
            logger.warning("api.invalid_language_code", code=body.language)

    # Resolve channel type
    try:
        channel_type = ChannelType(body.channel)
    except ValueError:
        channel_type = ChannelType.WEB

    # Build internal request
    from src.models.request import HaqSetuRequest, RequestMetadata

    haqsetu_request = HaqSetuRequest(
        session_id=session_id,
        channel_type=channel_type,
        content=body.text,
        content_type=ContentType.TEXT,
        language=lang_enum,
        metadata=RequestMetadata(phone_number="api-web-user"),
    )

    try:
        haqsetu_response = await orchestrator.process_text_query(haqsetu_request)
    except Exception:
        logger.error("api.text_query_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process query. Please try again.")

    # Build scheme list for API response
    schemes = [
        {
            "scheme_id": ref.scheme_id,
            "scheme_name": ref.scheme_name,
            "relevance_score": ref.relevance_score,
            "matched_criteria": ref.matched_criteria,
        }
        for ref in haqsetu_response.metadata.schemes_referenced
    ]

    latency = haqsetu_response.metadata.latency
    latency_dict = {
        "language_detection_ms": latency.language_detection_ms,
        "translation_in_ms": latency.translation_in_ms,
        "rag_retrieval_ms": latency.rag_retrieval_ms,
        "llm_reasoning_ms": latency.llm_reasoning_ms,
        "translation_out_ms": latency.translation_out_ms,
        "total_ms": latency.total_ms,
    }

    return TextQueryResponse(
        response=haqsetu_response.content,
        language=haqsetu_response.language.value,
        session_id=session_id,
        schemes=schemes,
        latency=latency_dict,
    )


@router.post("/voice", response_model=VoiceQueryResponse)
async def voice_query(
    request: Request,
    audio: UploadFile = File(..., description="Audio file (WAV, MP3, or OGG)"),
    session_id: str = Form(default=""),
    language: str = Form(default=""),
) -> VoiceQueryResponse:
    """Process a voice query through the full HaqSetu pipeline.

    Accepts a multipart form with an audio file, transcribes it,
    processes the text query, and optionally returns audio response.
    """
    orchestrator = request.app.state.orchestrator

    if not session_id:
        session_id = uuid4().hex

    # Resolve language enum
    lang_enum: LanguageCode | None = None
    if language:
        try:
            lang_enum = LanguageCode(language)
        except ValueError:
            logger.warning("api.invalid_language_code", code=language)

    # Read audio bytes with streaming size enforcement to prevent memory
    # exhaustion from oversized uploads (the check must happen DURING read,
    # not after loading the entire file into memory).
    max_audio_size = 10 * 1024 * 1024  # 10 MB
    try:
        size = 0
        chunks: list[bytes] = []
        while chunk := await audio.read(64 * 1024):  # 64 KB chunks
            size += len(chunk)
            if size > max_audio_size:
                raise HTTPException(status_code=413, detail="Audio file too large. Maximum size is 10 MB.")
            chunks.append(chunk)
        audio_bytes = b"".join(chunks)
    except HTTPException:
        raise
    except Exception:
        logger.error("api.audio_read_failed", exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to read audio file.") from None

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    # Build internal request
    from src.models.request import HaqSetuRequest, RequestMetadata

    haqsetu_request = HaqSetuRequest(
        session_id=session_id,
        channel_type=ChannelType.WEB,
        content=audio_bytes,
        content_type=ContentType.AUDIO,
        language=lang_enum,
        metadata=RequestMetadata(phone_number="api-voice-user"),
    )

    try:
        haqsetu_response = await orchestrator.process_voice_query(haqsetu_request)
    except RuntimeError as exc:
        logger.error("api.voice_query_stt_error", error=str(exc))
        raise HTTPException(status_code=503, detail="Speech-to-Text service is not available.")
    except Exception:
        logger.error("api.voice_query_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process voice query. Please try again.")

    # Extract audio response if available
    audio_b64: str | None = getattr(haqsetu_response, "_audio_b64", None)

    latency = haqsetu_response.metadata.latency
    latency_dict = {
        "asr_ms": latency.asr_ms,
        "language_detection_ms": latency.language_detection_ms,
        "translation_in_ms": latency.translation_in_ms,
        "rag_retrieval_ms": latency.rag_retrieval_ms,
        "llm_reasoning_ms": latency.llm_reasoning_ms,
        "translation_out_ms": latency.translation_out_ms,
        "tts_ms": latency.tts_ms,
        "total_ms": latency.total_ms,
    }

    return VoiceQueryResponse(
        response_text=haqsetu_response.content,
        response_audio_base64=audio_b64,
        language=haqsetu_response.language.value,
        session_id=session_id,
        latency=latency_dict,
    )
