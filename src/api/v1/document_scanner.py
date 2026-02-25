"""Document Scanner API endpoints for HaqSetu.

Allows citizens to upload photos of government documents, notices,
FIRs, court orders, or land records and receive plain-language
explanations in their preferred language.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/document", tags=["document-scanner"])


@router.post("/scan")
async def scan_document(
    request: Request,
    image: UploadFile = File(..., description="Photo of the document to scan"),  # noqa: B008
    language: str = Form(default="hi", description="Preferred language for explanation"),  # noqa: B008
) -> dict:
    """Scan a government document and get a plain-language explanation.

    Upload a photo of any government document (notice, FIR, court order,
    land record, etc.) and receive:
    - Extracted text from the document
    - Plain language summary
    - Action items and deadlines
    - Referenced laws and schemes
    - What you need to do next

    The explanation is provided in the user's preferred language.
    """
    scanner = getattr(request.app.state, "document_scanner", None)
    if scanner is None:
        raise HTTPException(status_code=503, detail="Document scanner not available")

    # Read image with size limit
    max_size = 10 * 1024 * 1024  # 10 MB
    size = 0
    chunks: list[bytes] = []
    try:
        while chunk := await image.read(64 * 1024):
            size += len(chunk)
            if size > max_size:
                raise HTTPException(status_code=413, detail="Image too large. Maximum 10 MB.")
            chunks.append(chunk)
        image_data = b"".join(chunks)
    except HTTPException:
        raise
    except Exception:
        logger.error("api.document.read_failed", exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to read image") from None

    if not image_data:
        raise HTTPException(status_code=400, detail="Empty image file")

    try:
        explanation = await scanner.scan_and_explain(image_data, language)
    except Exception:
        logger.error("api.document.scan_failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to scan document") from None

    return {
        "summary": explanation.summary,
        "plain_language_summary": explanation.plain_language_summary,
        "action_items": [
            {
                "description": item.description,
                "deadline": item.deadline,
                "priority": item.priority,
                "contact_info": item.contact_info,
            }
            for item in explanation.action_items
        ],
        "referenced_laws": explanation.referenced_laws,
        "referenced_schemes": explanation.referenced_schemes,
        "document_type": explanation.document_type,
        "original_text": explanation.original_text[:2000],
        "language": language,
        "disclaimer": (
            "This is an automated summary for informational purposes only. "
            "Please verify important details with the issuing authority."
        ),
    }
