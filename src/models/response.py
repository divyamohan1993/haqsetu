from __future__ import annotations

from typing import Literal

import orjson
from pydantic import BaseModel, Field, computed_field

from src.models.enums import ContentType, LanguageCode


def _orjson_dumps(v: object, *, default: object = None) -> str:
    return orjson.dumps(v, default=default).decode()


class LatencyBreakdown(BaseModel):
    asr_ms: float = 0.0
    language_detection_ms: float = 0.0
    translation_in_ms: float = 0.0
    rag_retrieval_ms: float = 0.0
    llm_reasoning_ms: float = 0.0
    translation_out_ms: float = 0.0
    tts_ms: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_ms(self) -> float:
        return (
            self.asr_ms
            + self.language_detection_ms
            + self.translation_in_ms
            + self.rag_retrieval_ms
            + self.llm_reasoning_ms
            + self.translation_out_ms
            + self.tts_ms
        )


class SchemeReference(BaseModel):
    scheme_id: str
    scheme_name: str
    relevance_score: float = 0.0
    matched_criteria: list[str] = Field(default_factory=list)


class SuggestedAction(BaseModel):
    type: Literal[
        "apply_scheme",
        "check_eligibility",
        "call_helpline",
        "visit_csc",
        "upload_document",
        "track_status",
        "escalate",
    ]
    description: str
    metadata: dict[str, object] | None = None


class ResponseMetadata(BaseModel):
    confidence: float
    latency: LatencyBreakdown
    schemes_referenced: list[SchemeReference] = Field(default_factory=list)
    requires_followup: bool = False
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)


class HaqSetuResponse(BaseModel):
    request_id: str
    session_id: str
    content: str
    content_type: ContentType
    language: LanguageCode
    metadata: ResponseMetadata
