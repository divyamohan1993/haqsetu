"""RTI (Right to Information) API endpoints for HaqSetu.

Provides endpoints for generating RTI application drafts and
filing guidance under the RTI Act, 2005.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/rti", tags=["rti"])


class RTIGenerateRequest(BaseModel):
    applicant_name: str = Field(..., min_length=2, max_length=200)
    applicant_address: str = Field(..., min_length=10, max_length=500)
    subject: str = Field(..., min_length=5, max_length=500)
    questions: list[str] = Field(..., min_length=1, max_length=10)
    public_authority: str = Field(..., min_length=2, max_length=300)
    authority_address: str = Field(default="", max_length=500)
    is_bpl: bool = Field(default=False, description="Below Poverty Line status (fee exempt)")
    language: str = Field(default="en", description="Language for the RTI draft")


class RTIFromProblemRequest(BaseModel):
    problem_description: str = Field(
        ..., min_length=20, max_length=5000,
        description="Describe your problem and what information you need",
    )
    applicant_name: str = Field(..., min_length=2, max_length=200)
    applicant_address: str = Field(..., min_length=10, max_length=500)
    language: str = Field(default="en")
    is_bpl: bool = False


@router.post("/generate")
async def generate_rti_draft(
    body: RTIGenerateRequest, request: Request
) -> dict:
    """Generate a complete RTI application draft.

    Produces a properly formatted RTI application under Section 6
    of the RTI Act, 2005, ready for submission.
    """
    rti_service = getattr(request.app.state, "rti_generator", None)
    if rti_service is None:
        raise HTTPException(status_code=503, detail="RTI generator not available")

    try:
        from src.services.rti_generator import RTIRequest
        rti_request = RTIRequest(
            applicant_name=body.applicant_name,
            address=body.applicant_address,
            subject=body.subject,
            questions=body.questions,
            public_authority=body.public_authority,
            authority_address=body.authority_address,
            bpl_status=body.is_bpl,
        )
        draft = await rti_service.generate_rti_draft(rti_request)
    except Exception:
        logger.error("api.rti.generate_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate RTI draft") from None

    return {
        "application_text": draft.application_text,
        "subject": draft.subject,
        "public_authority": draft.public_authority,
        "fee_amount": draft.fee_amount,
        "filing_method": draft.filing_method,
        "reference_sections": draft.reference_sections,
        "language": draft.language,
        "generated_at": draft.generated_at.isoformat(),
        "note": (
            "RTI is a fundamental right under Article 19(1)(a) of the Constitution. "
            "File this application at rtionline.gov.in or by post to the relevant PIO."
        ),
    }


@router.post("/from-problem")
async def generate_rti_from_problem(
    body: RTIFromProblemRequest, request: Request
) -> dict:
    """Generate RTI questions and draft from a problem description.

    Describe your problem and the system will automatically generate
    relevant RTI questions and a complete application draft.
    """
    rti_service = getattr(request.app.state, "rti_generator", None)
    getattr(request.app.state, "llm", None)
    if rti_service is None:
        raise HTTPException(status_code=503, detail="RTI generator not available")

    # Use LLM to generate questions from problem description
    try:
        from src.services.rti_generator import RTIRequest

        # Auto-generate questions using the service
        questions = await rti_service.auto_generate_questions(body.problem_description)
        authority = await rti_service.identify_authority(body.problem_description)

        rti_request = RTIRequest(
            applicant_name=body.applicant_name,
            address=body.applicant_address,
            subject=f"Information regarding: {body.problem_description[:100]}",
            questions=questions,
            public_authority=authority,
            bpl_status=body.is_bpl,
        )
        draft = await rti_service.generate_rti_draft(rti_request)
    except Exception:
        logger.error("api.rti.from_problem_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate RTI from problem") from None

    return {
        "auto_generated_questions": questions,
        "identified_authority": authority,
        "application_text": draft.application_text,
        "fee_amount": draft.fee_amount,
        "filing_method": draft.filing_method,
        "language": draft.language,
    }


@router.get("/fee-info/{authority_level}")
async def get_rti_fee_info(
    authority_level: str, state: str = "central", request: Request = None
) -> dict:
    """Get RTI fee information for a specific authority level.

    authority_level: 'central', 'state', or 'local'
    """
    rti_service = getattr(request.app.state, "rti_generator", None)
    if rti_service is None:
        raise HTTPException(status_code=503, detail="RTI generator not available")

    fee_info = rti_service.get_fee_info(authority_level, state)
    return {
        "authority_level": authority_level,
        "state": state,
        "fee_amount": fee_info.amount,
        "payment_modes": fee_info.payment_modes,
        "bpl_exempt": fee_info.bpl_exempt,
        "notes": fee_info.state_specific_notes,
    }


@router.get("/filing-instructions/{authority_level}")
async def get_filing_instructions(
    authority_level: str, request: Request
) -> dict:
    """Get step-by-step instructions for filing an RTI application."""
    rti_service = getattr(request.app.state, "rti_generator", None)
    if rti_service is None:
        raise HTTPException(status_code=503, detail="RTI generator not available")

    instructions = rti_service.get_filing_instructions(authority_level)
    return {
        "authority_level": authority_level,
        "online_url": instructions.online_url,
        "steps": instructions.steps,
        "documents_needed": instructions.documents_needed,
    }
