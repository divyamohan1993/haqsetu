"""Accessibility API endpoints for HaqSetu.

Provides endpoints for generating accessible responses suitable
for blind, deaf, and users with other disabilities.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/accessibility", tags=["accessibility"])


class AccessibleResponseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    mode: str = Field(
        default="screen_reader",
        description="Mode: screen_reader, sign_language, simplified, audio_description, haptic, braille",
    )
    language: str = Field(default="hi")
    speech_speed: float = Field(default=1.0, ge=0.25, le=2.0)


@router.post("/generate")
async def generate_accessible_response(
    body: AccessibleResponseRequest, request: Request
) -> dict:
    """Generate an accessible version of text content.

    Supports multiple accessibility modes:
    - screen_reader: Optimized for screen reader software
    - sign_language: ISL gesture descriptions
    - simplified: Simple language for cognitive accessibility
    - audio_description: Enhanced audio narration
    - haptic: Vibration patterns for alerts
    - braille: Braille-ready formatted text
    """
    a11y = getattr(request.app.state, "accessibility", None)
    if a11y is None:
        raise HTTPException(status_code=503, detail="Accessibility service not available")

    try:
        from src.services.accessibility import AccessibilityMode
        mode = AccessibilityMode(body.mode)
        result = await a11y.generate_accessible_response(
            text=body.text,
            mode=mode,
            language=body.language,
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {body.mode}. Use: screen_reader, sign_language, simplified, audio_description, haptic, braille",
        ) from None
    except Exception:
        logger.error("api.accessibility.generate_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate accessible response") from None

    return {
        "mode": body.mode,
        "text": result.text,
        "screen_reader_text": result.screen_reader_text,
        "simplified_text": result.simplified_text,
        "braille_text": result.braille_text,
        "isl_description": (
            {
                "gestures": result.isl_description.gestures,
            }
            if result.isl_description
            else None
        ),
        "haptic_pattern": (
            {
                "pattern": result.haptic_pattern.pattern,
                "description": result.haptic_pattern.description,
            }
            if result.haptic_pattern
            else None
        ),
        "language": body.language,
    }


@router.post("/simplify")
async def simplify_text(
    text: str = "",
    language: str = "hi",
    request: Request = None,
) -> dict:
    """Simplify text for easy understanding.

    Reduces complex text to simple words and short sentences,
    suitable for users with limited literacy or cognitive challenges.
    """
    a11y = getattr(request.app.state, "accessibility", None)
    if a11y is None:
        raise HTTPException(status_code=503, detail="Accessibility service not available")

    simplified = await a11y.simplify_text(text)
    return {"original_length": len(text), "simplified": simplified}


@router.get("/haptic-pattern/{alert_type}")
async def get_haptic_pattern(
    alert_type: str, request: Request
) -> dict:
    """Get a haptic vibration pattern for a given alert type.

    Types: success, error, warning, urgent, notification, sos.
    """
    a11y = getattr(request.app.state, "accessibility", None)
    if a11y is None:
        raise HTTPException(status_code=503, detail="Accessibility service not available")

    pattern = a11y.get_haptic_pattern(alert_type)
    return {
        "alert_type": alert_type,
        "pattern": pattern.pattern,
        "description": pattern.description,
    }
