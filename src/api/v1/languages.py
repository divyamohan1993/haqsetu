"""Language-related API endpoints for HaqSetu v1.

Exposes metadata about the 22 Scheduled Languages of India plus
English, including native names, scripts, and GCP service support.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config.languages import LANGUAGES, get_language, get_supported_languages

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/languages", tags=["languages"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class LanguageInfo(BaseModel):
    """Public representation of a single supported language."""

    code: str
    name_english: str
    name_native: str
    script: str
    has_tts: bool
    has_stt: bool
    is_high_priority: bool
    population_millions: float


class LanguageListResponse(BaseModel):
    """List of all supported languages."""

    languages: list[LanguageInfo]
    total: int


class LanguageDetailResponse(BaseModel):
    """Full detail for a single language."""

    code: str
    name_english: str
    name_native: str
    script: str
    gcp_translation_code: str
    gcp_tts_code: str | None
    gcp_stt_code: str | None
    has_tts: bool
    has_stt: bool
    is_high_priority: bool
    population_millions: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=LanguageListResponse)
async def list_languages() -> LanguageListResponse:
    """List all 23 supported languages (22 Scheduled Languages + English).

    Results are sorted by speaker population in descending order.
    """
    all_langs = get_supported_languages()

    languages = [
        LanguageInfo(
            code=lang.code,
            name_english=lang.name_english,
            name_native=lang.name_native,
            script=lang.script,
            has_tts=lang.gcp_tts_code is not None,
            has_stt=lang.gcp_stt_code is not None,
            is_high_priority=lang.is_high_priority,
            population_millions=lang.population_millions,
        )
        for lang in all_langs
    ]

    return LanguageListResponse(languages=languages, total=len(languages))


@router.get("/{code}", response_model=LanguageDetailResponse)
async def get_language_detail(code: str) -> LanguageDetailResponse:
    """Get full details for a specific language by its ISO code.

    Accepts both ISO 639-1 (e.g. ``hi``) and ISO 639-3 (e.g. ``mai``)
    codes, as well as common aliases (e.g. ``hin`` for Hindi).
    """
    lang = get_language(code)

    if lang is None:
        raise HTTPException(
            status_code=404,
            detail=f"Language code '{code}' not found. Use GET /api/v1/languages to see all supported codes.",
        )

    return LanguageDetailResponse(
        code=lang.code,
        name_english=lang.name_english,
        name_native=lang.name_native,
        script=lang.script,
        gcp_translation_code=lang.gcp_translation_code,
        gcp_tts_code=lang.gcp_tts_code,
        gcp_stt_code=lang.gcp_stt_code,
        has_tts=lang.gcp_tts_code is not None,
        has_stt=lang.gcp_stt_code is not None,
        is_high_priority=lang.is_high_priority,
        population_millions=lang.population_millions,
    )
