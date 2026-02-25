"""Legal Rights API endpoints for HaqSetu.

Provides endpoints for identifying applicable laws and rights based
on a citizen's described situation. Every response includes a clear
disclaimer that this is NOT legal advice.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/legal-rights", tags=["legal-rights"])


class RightsQueryRequest(BaseModel):
    situation: str = Field(
        ..., min_length=10, max_length=5000,
        description="Describe your situation in your own words",
    )
    language: str = Field(default="hi", description="Preferred language")
    state: str | None = Field(default=None, description="State for state-specific laws")


@router.post("/identify")
async def identify_applicable_rights(
    body: RightsQueryRequest, request: Request
) -> dict:
    """Identify which laws, rights, and schemes may apply to a situation.

    The citizen describes their problem in their own words, and the
    system identifies potentially relevant legal provisions, government
    schemes, and helplines.

    DISCLAIMER: This is for educational purposes only. This is NOT
    legal advice. Consult DLSA or a lawyer for legal counsel.
    """
    legal_rights = getattr(request.app.state, "legal_rights", None)
    if legal_rights is None:
        raise HTTPException(status_code=503, detail="Legal rights service not available")

    try:
        analysis = await legal_rights.identify_applicable_laws(body.situation)
    except Exception:
        logger.error("api.legal_rights.identify_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to analyze situation") from None

    return {
        "situation_summary": analysis.situation_summary,
        "applicable_laws": [
            {
                "law": law.law,
                "description": law.description,
                "relevance": law.relevance,
                "bns_section": law.bns_section,
                "act_name": law.act_name,
            }
            for law in analysis.applicable_laws
        ],
        "applicable_rights": [
            {
                "right_name": right.right_name,
                "source_law": right.source_law,
                "description": right.description,
                "how_to_exercise": right.how_to_exercise,
            }
            for right in analysis.applicable_rights
        ],
        "recommended_actions": analysis.recommended_actions,
        "helplines": [
            {"name": h.name, "number": h.number, "description": h.description}
            for h in analysis.helplines
        ],
        "severity": analysis.severity,
        "disclaimer": analysis.disclaimer,
    }


@router.get("/helplines")
async def get_helplines(
    category: str = "general",
    request: Request = None,
) -> dict:
    """Get emergency and legal helpline numbers.

    Categories: general, women, children, sc_st, labor, consumer,
    cyber_crime, senior_citizen, disability.
    """
    legal_rights = getattr(request.app.state, "legal_rights", None)
    if legal_rights is None:
        raise HTTPException(status_code=503, detail="Legal rights service not available")

    helplines = legal_rights.get_helplines(category)
    return {
        "category": category,
        "helplines": [
            {
                "name": h.name,
                "number": h.number,
                "description": h.description,
                "hours": h.hours,
                "languages": h.languages,
            }
            for h in helplines
        ],
    }


@router.get("/bns/{section_number}")
async def get_bns_section(
    section_number: int, request: Request
) -> dict:
    """Get information about a specific BNS (Bharat Nyaya Sanhita) section.

    The BNS replaced the Indian Penal Code from 1 July 2024.
    """
    legal_rights = getattr(request.app.state, "legal_rights", None)
    if legal_rights is None:
        raise HTTPException(status_code=503, detail="Legal rights service not available")

    section = legal_rights.get_bns_section(section_number)
    if section is None:
        raise HTTPException(status_code=404, detail=f"BNS Section {section_number} not found")

    return {
        "section_number": section.section_number,
        "title": section.title,
        "description": section.description,
        "old_ipc_section": section.old_ipc_section,
        "punishment": section.punishment,
        "bailable": section.bailable,
        "cognizable": section.cognizable,
        "disclaimer": (
            "This information is for educational purposes only. "
            "This is NOT legal advice. Please consult a lawyer or DLSA."
        ),
    }
