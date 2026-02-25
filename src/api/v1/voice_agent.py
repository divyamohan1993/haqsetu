"""Voice Agent API endpoints for HaqSetu.

Provides conversational voice agent endpoints that enable natural
multi-turn conversations where users describe their problems and
the system identifies applicable laws, rights, and schemes.

IMPORTANT: Every response includes a legal disclaimer. This service
does NOT provide legal advice.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/voice-agent", tags=["voice-agent"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class StartSessionRequest(BaseModel):
    language: str = Field(default="hi", description="Preferred language code")
    session_id: str | None = Field(default=None, description="Optional session ID")


class StartSessionResponse(BaseModel):
    session_id: str
    greeting: str
    disclaimer: str
    language: str


class ChatMessageRequest(BaseModel):
    session_id: str = Field(..., description="Session ID from start_session")
    message: str = Field(..., min_length=1, max_length=5000, description="User message")
    language: str | None = Field(default=None, description="Override language for this message")


class ChatMessageResponse(BaseModel):
    response_text: str
    session_id: str
    follow_up_question: str | None = None
    identified_laws: list[dict] = Field(default_factory=list)
    applicable_schemes: list[dict] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    helplines: list[dict] = Field(default_factory=list)
    severity: str = "low"
    disclaimer: str
    language: str


class CaseAnalysisResponse(BaseModel):
    case_id: str
    session_id: str
    summary: str
    identified_laws: list[dict]
    applicable_schemes: list[dict]
    recommended_actions: list[str]
    helplines: list[dict]
    severity: str
    disclaimer: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/start", response_model=StartSessionResponse)
async def start_voice_session(
    body: StartSessionRequest, request: Request
) -> StartSessionResponse:
    """Start a new voice agent conversation session.

    Returns a session ID and greeting in the user's preferred language.
    """
    voice_agent = getattr(request.app.state, "voice_agent", None)
    if voice_agent is None:
        raise HTTPException(status_code=503, detail="Voice agent service not available")

    try:
        session = await voice_agent.start_session(
            session_id=body.session_id,
            language=body.language,
        )
    except Exception:
        logger.error("api.voice_agent.start_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start session") from None

    from src.services.voice_agent import LEGAL_DISCLAIMER, LEGAL_DISCLAIMER_HI

    # Greeting based on language
    greetings = {
        "hi": (
            "नमस्ते! मैं हक़सेतु हूँ। आप अपनी समस्या बताइए, मैं आपको बताऊँगा "
            "कि कौन से कानून और सरकारी योजनाएं आपकी मदद कर सकती हैं। "
            "कृपया ध्यान दें -- यह कानूनी सलाह नहीं है।"
        ),
        "en": (
            "Hello! I am HaqSetu. Please describe your problem and I will help "
            "identify which laws and government schemes may be relevant to your "
            "situation. Please note -- this is not legal advice."
        ),
    }
    greeting = greetings.get(body.language, greetings["en"])
    disclaimer = LEGAL_DISCLAIMER_HI if body.language == "hi" else LEGAL_DISCLAIMER

    return StartSessionResponse(
        session_id=session.session_id,
        greeting=greeting,
        disclaimer=disclaimer,
        language=body.language,
    )


@router.post("/chat", response_model=ChatMessageResponse)
async def chat_with_agent(
    body: ChatMessageRequest, request: Request
) -> ChatMessageResponse:
    """Send a message to the voice agent and receive a response.

    The agent maintains conversation context and builds a case analysis
    over multiple turns. It identifies applicable laws, schemes, and
    concrete next steps based on the user's narrated problem.
    """
    voice_agent = getattr(request.app.state, "voice_agent", None)
    if voice_agent is None:
        raise HTTPException(status_code=503, detail="Voice agent service not available")

    try:
        result = await voice_agent.process_message(
            session_id=body.session_id,
            user_message=body.message,
            language=body.language,
        )
    except Exception:
        logger.error("api.voice_agent.chat_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process message") from None

    case = result.case_analysis
    return ChatMessageResponse(
        response_text=result.response_text,
        session_id=body.session_id,
        follow_up_question=result.follow_up_question,
        identified_laws=[
            {"law": law.law, "description": law.description, "relevance": law.relevance}
            for law in (case.identified_laws if case else [])
        ],
        applicable_schemes=[
            {"scheme": s.scheme, "relevance": s.relevance}
            for s in (case.applicable_schemes if case else [])
        ],
        recommended_actions=case.recommended_actions if case else [],
        helplines=[
            {"name": h.name, "number": h.number}
            for h in (case.helplines if case else [])
        ],
        severity=case.severity if case else "low",
        disclaimer=result.disclaimer,
        language=result.language,
    )


@router.get("/case/{session_id}", response_model=CaseAnalysisResponse)
async def get_case_analysis(session_id: str, request: Request) -> CaseAnalysisResponse:
    """Get the full case analysis built during the conversation.

    Returns all identified laws, applicable schemes, and recommended
    actions accumulated over the conversation turns.
    """
    voice_agent = getattr(request.app.state, "voice_agent", None)
    if voice_agent is None:
        raise HTTPException(status_code=503, detail="Voice agent service not available")

    case = voice_agent.get_case_analysis(session_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.services.voice_agent import LEGAL_DISCLAIMER

    return CaseAnalysisResponse(
        case_id=case.case_id,
        session_id=case.session_id,
        summary=case.summary,
        identified_laws=[
            {"law": law.law, "description": law.description, "relevance": law.relevance}
            for law in case.identified_laws
        ],
        applicable_schemes=[
            {"scheme": s.scheme, "relevance": s.relevance}
            for s in case.applicable_schemes
        ],
        recommended_actions=case.recommended_actions,
        helplines=[
            {"name": h.name, "number": h.number}
            for h in case.helplines
        ],
        severity=case.severity,
        disclaimer=LEGAL_DISCLAIMER,
    )
