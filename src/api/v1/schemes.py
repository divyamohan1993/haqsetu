"""Scheme-related API endpoints for HaqSetu v1.

Provides endpoints for listing, searching, and checking eligibility
of government schemes stored in the system.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.models.scheme import SchemeCategory, SchemeDocument

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/schemes", tags=["schemes"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SchemeListResponse(BaseModel):
    """Paginated list of schemes."""

    schemes: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class SchemeDetailResponse(BaseModel):
    """Full detail of a single scheme."""

    scheme_id: str
    name: str
    description: str
    category: str
    ministry: str
    state: str | None
    benefits: str
    eligibility: dict[str, Any]
    application_process: str
    documents_required: list[str]
    helpline: str | None
    website: str | None
    deadline: str | None


class SchemeSearchResponse(BaseModel):
    """Search results for schemes."""

    results: list[dict[str, Any]]
    query: str
    language: str
    total: int


class EligibilityCheckResponse(BaseModel):
    """Result of an eligibility check."""

    eligible_schemes: list[dict[str, Any]]
    total: int
    profile: dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=SchemeListResponse)
async def list_schemes(
    request: Request,
    category: str | None = Query(default=None, description="Filter by scheme category"),
    state: str | None = Query(default=None, description="Filter by state (None for central schemes)"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Results per page"),
) -> SchemeListResponse:
    """List government schemes with optional filters.

    Supports filtering by category (agriculture, health, education, etc.)
    and by state.  Central schemes have state=None.
    """
    scheme_data: list[SchemeDocument] = getattr(request.app.state, "scheme_data", [])

    # Apply filters
    filtered = scheme_data
    if category:
        try:
            cat_enum = SchemeCategory(category)
            filtered = [s for s in filtered if s.category == cat_enum]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category '{category}'. Valid categories: {[c.value for c in SchemeCategory]}",
            )

    if state is not None:
        filtered = [s for s in filtered if s.state and s.state.lower() == state.lower()]

    total = len(filtered)

    # Paginate
    start = (page - 1) * page_size
    end = start + page_size
    page_schemes = filtered[start:end]

    schemes_out = [
        {
            "scheme_id": s.scheme_id,
            "name": s.name,
            "category": s.category.value,
            "ministry": s.ministry,
            "state": s.state,
            "benefits": s.benefits[:200] if s.benefits else "",
            "popularity_score": s.popularity_score,
        }
        for s in page_schemes
    ]

    return SchemeListResponse(
        schemes=schemes_out,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/search", response_model=SchemeSearchResponse)
async def search_schemes(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    lang: str = Query(default="en", description="Language code for the query"),
    top_k: int = Query(default=10, ge=1, le=50, description="Number of results"),
) -> SchemeSearchResponse:
    """Search schemes by text query with optional language support.

    Uses the RAG-based scheme search service for semantic matching.
    If the query is in a non-English language, it is translated first.
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    scheme_search = getattr(request.app.state, "scheme_search", None)
    translation = getattr(request.app.state, "translation", None)

    search_query = q

    # Translate to English if needed for semantic search
    if lang != "en" and translation is not None:
        try:
            search_query = await translation.translate(q, source_lang=lang, target_lang="en")
        except Exception:
            logger.warning("api.schemes.search_translation_failed", exc_info=True)
            search_query = q

    # Perform search
    results: list = []
    if scheme_search is not None:
        try:
            results = await scheme_search.search(query=search_query, top_k=top_k)
        except Exception:
            logger.error("api.schemes.search_failed", exc_info=True)
            raise HTTPException(status_code=500, detail="Scheme search failed. Please try again.")
    else:
        # Fallback: simple text matching against loaded scheme data
        scheme_data: list[SchemeDocument] = getattr(request.app.state, "scheme_data", [])
        query_lower = search_query.lower()
        for s in scheme_data:
            if (
                query_lower in s.name.lower()
                or query_lower in s.description.lower()
                or query_lower in s.benefits.lower()
            ):
                results.append(s)
            if len(results) >= top_k:
                break

    results_out = []
    for s in results[:top_k]:
        if hasattr(s, "scheme_id"):
            results_out.append({
                "scheme_id": s.scheme_id,
                "name": s.name,
                "description": s.description[:300] if s.description else "",
                "category": s.category.value if hasattr(s.category, "value") else str(s.category),
                "benefits": s.benefits[:200] if s.benefits else "",
                "relevance_score": getattr(s, "relevance_score", s.popularity_score),
            })
        elif isinstance(s, dict):
            results_out.append({
                "scheme_id": s.get("scheme_id", ""),
                "name": s.get("name", ""),
                "description": s.get("description", "")[:300],
                "category": s.get("category", ""),
                "benefits": s.get("benefits", "")[:200],
                "relevance_score": s.get("relevance_score", 0.0),
            })

    return SchemeSearchResponse(
        results=results_out,
        query=q,
        language=lang,
        total=len(results_out),
    )


@router.get("/eligible", response_model=EligibilityCheckResponse)
async def check_eligibility(
    request: Request,
    age: int | None = Query(default=None, description="Age of the applicant"),
    gender: str | None = Query(default=None, description="Gender: male, female, other"),
    income: float | None = Query(default=None, description="Annual income in INR"),
    category: str | None = Query(default=None, description="Social category: SC, ST, OBC, General"),
    state: str | None = Query(default=None, description="State of residence"),
    occupation: str | None = Query(default=None, description="Occupation"),
    is_bpl: bool | None = Query(default=None, description="Below Poverty Line status"),
    land_holding_acres: float | None = Query(default=None, description="Land holding in acres"),
) -> EligibilityCheckResponse:
    """Check which schemes a user is eligible for given their profile.

    Matches the provided profile parameters against the eligibility
    criteria of all loaded schemes.
    """
    scheme_data: list[SchemeDocument] = getattr(request.app.state, "scheme_data", [])

    profile = {
        "age": age,
        "gender": gender,
        "income": income,
        "category": category,
        "state": state,
        "occupation": occupation,
        "is_bpl": is_bpl,
        "land_holding_acres": land_holding_acres,
    }
    # Remove None values for cleaner output
    profile = {k: v for k, v in profile.items() if v is not None}

    eligible: list[dict[str, Any]] = []

    for scheme in scheme_data:
        elig = scheme.eligibility
        is_eligible = True
        matched_criteria: list[str] = []

        # Age check
        if age is not None:
            if elig.min_age is not None and age < elig.min_age:
                is_eligible = False
            elif elig.max_age is not None and age > elig.max_age:
                is_eligible = False
            else:
                if elig.min_age is not None or elig.max_age is not None:
                    matched_criteria.append("age")

        # Gender check
        if gender is not None and elig.gender is not None:
            if elig.gender.lower() != "all" and elig.gender.lower() != gender.lower():
                is_eligible = False
            else:
                matched_criteria.append("gender")

        # Income check
        if income is not None and elig.income_limit is not None:
            if income > elig.income_limit:
                is_eligible = False
            else:
                matched_criteria.append("income")

        # Social category check
        if category is not None and elig.category is not None:
            if elig.category.lower() != "all" and category.lower() not in elig.category.lower():
                is_eligible = False
            else:
                matched_criteria.append("category")

        # State check — central schemes (state=None) are available nationwide
        if state is not None:
            if scheme.state is not None and scheme.state.lower() != state.lower():
                is_eligible = False
            elif scheme.state is not None:
                matched_criteria.append("state")

        # Occupation check
        if occupation is not None and elig.occupation is not None:
            if elig.occupation.lower() != "all" and occupation.lower() not in elig.occupation.lower():
                is_eligible = False
            else:
                matched_criteria.append("occupation")

        # BPL check
        if is_bpl is not None and elig.is_bpl is not None:
            if elig.is_bpl and not is_bpl:
                is_eligible = False
            else:
                matched_criteria.append("bpl_status")

        # Land holding check
        if land_holding_acres is not None and elig.land_holding_acres is not None:
            if land_holding_acres > elig.land_holding_acres:
                is_eligible = False
            else:
                matched_criteria.append("land_holding")

        if is_eligible:
            eligible.append({
                "scheme_id": scheme.scheme_id,
                "name": scheme.name,
                "category": scheme.category.value,
                "benefits": scheme.benefits[:200] if scheme.benefits else "",
                "matched_criteria": matched_criteria,
                "application_process": scheme.application_process[:200] if scheme.application_process else "",
            })

    logger.info(
        "api.eligibility_check",
        profile_params=len(profile),
        total_schemes=len(scheme_data),
        eligible_count=len(eligible),
    )

    return EligibilityCheckResponse(
        eligible_schemes=eligible,
        total=len(eligible),
        profile=profile,
    )


@router.get("/{scheme_id}", response_model=SchemeDetailResponse)
async def get_scheme_detail(scheme_id: str, request: Request) -> SchemeDetailResponse:
    """Get full details of a specific scheme by its ID."""
    scheme_data: list[SchemeDocument] = getattr(request.app.state, "scheme_data", [])

    for scheme in scheme_data:
        if scheme.scheme_id == scheme_id:
            elig_dict: dict[str, Any] = {}
            elig = scheme.eligibility
            if elig.min_age is not None:
                elig_dict["min_age"] = elig.min_age
            if elig.max_age is not None:
                elig_dict["max_age"] = elig.max_age
            if elig.gender is not None:
                elig_dict["gender"] = elig.gender
            if elig.income_limit is not None:
                elig_dict["income_limit"] = elig.income_limit
            if elig.category is not None:
                elig_dict["category"] = elig.category
            if elig.occupation is not None:
                elig_dict["occupation"] = elig.occupation
            if elig.state is not None:
                elig_dict["state"] = elig.state
            if elig.is_bpl is not None:
                elig_dict["is_bpl"] = elig.is_bpl
            if elig.land_holding_acres is not None:
                elig_dict["land_holding_acres"] = elig.land_holding_acres
            if elig.custom_criteria:
                elig_dict["custom_criteria"] = elig.custom_criteria

            return SchemeDetailResponse(
                scheme_id=scheme.scheme_id,
                name=scheme.name,
                description=scheme.description,
                category=scheme.category.value,
                ministry=scheme.ministry,
                state=scheme.state,
                benefits=scheme.benefits,
                eligibility=elig_dict,
                application_process=scheme.application_process,
                documents_required=scheme.documents_required,
                helpline=scheme.helpline,
                website=scheme.website,
                deadline=scheme.deadline,
            )

    raise HTTPException(status_code=404, detail=f"Scheme '{scheme_id}' not found.")
