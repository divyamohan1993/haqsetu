"""Data seeding utilities for loading and indexing government scheme data.

Loads scheme definitions from the bundled ``central_schemes.json`` file
and indexes them into the RAG engine via the
:class:`~src.services.scheme_search.SchemeSearchService`.  Designed to
run once at application startup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.models.scheme import EligibilityCriteria, SchemeCategory, SchemeDocument

if TYPE_CHECKING:
    from src.services.scheme_search import SchemeSearchService

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR: Path = Path(__file__).resolve().parent / "schemes"
_CENTRAL_SCHEMES_PATH: Path = _DATA_DIR / "central_schemes.json"

# ---------------------------------------------------------------------------
# Category mapping -- JSON string -> SchemeCategory enum
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, SchemeCategory] = {
    "agriculture": SchemeCategory.AGRICULTURE,
    "health": SchemeCategory.HEALTH,
    "education": SchemeCategory.EDUCATION,
    "housing": SchemeCategory.HOUSING,
    "employment": SchemeCategory.EMPLOYMENT,
    "social_security": SchemeCategory.SOCIAL_SECURITY,
    "financial_inclusion": SchemeCategory.FINANCIAL_INCLUSION,
    "women_child": SchemeCategory.WOMEN_CHILD,
    "tribal": SchemeCategory.TRIBAL,
    "disability": SchemeCategory.DISABILITY,
    "senior_citizen": SchemeCategory.SENIOR_CITIZEN,
    "skill_development": SchemeCategory.SKILL_DEVELOPMENT,
    "infrastructure": SchemeCategory.INFRASTRUCTURE,
    "other": SchemeCategory.OTHER,
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_schemes(path: Path | None = None) -> list[SchemeDocument]:
    """Load government scheme data from a JSON file.

    Reads and parses the bundled ``central_schemes.json`` (or a custom
    path) into validated :class:`SchemeDocument` instances.

    Parameters
    ----------
    path:
        Path to the JSON file.  Defaults to the bundled
        ``central_schemes.json``.

    Returns
    -------
    list[SchemeDocument]
        Parsed and validated scheme documents.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist.
    json.JSONDecodeError
        If the JSON is malformed.
    """
    file_path = path or _CENTRAL_SCHEMES_PATH

    if not file_path.exists():
        raise FileNotFoundError(f"Scheme data file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        raw_schemes: list[dict] = json.load(f)

    schemes: list[SchemeDocument] = []
    for raw in raw_schemes:
        try:
            scheme = _parse_scheme(raw)
            schemes.append(scheme)
        except Exception:
            logger.warning(
                "seed.parse_error",
                scheme_id=raw.get("scheme_id", "unknown"),
                exc_info=True,
            )

    logger.info("seed.loaded_schemes", count=len(schemes), source=str(file_path))
    return schemes


def _parse_scheme(raw: dict) -> SchemeDocument:
    """Parse a raw JSON dict into a validated :class:`SchemeDocument`."""
    # Parse eligibility criteria.
    raw_elig = raw.get("eligibility", {})
    eligibility = EligibilityCriteria(
        min_age=raw_elig.get("min_age"),
        max_age=raw_elig.get("max_age"),
        gender=raw_elig.get("gender"),
        income_limit=raw_elig.get("income_limit"),
        category=raw_elig.get("category"),
        occupation=raw_elig.get("occupation"),
        state=raw_elig.get("state"),
        is_bpl=raw_elig.get("is_bpl"),
        land_holding_acres=raw_elig.get("land_holding_acres"),
        custom_criteria=raw_elig.get("custom_criteria", []),
    )

    # Map category string to enum.
    category_str = raw.get("category", "other")
    category = _CATEGORY_MAP.get(category_str, SchemeCategory.OTHER)

    return SchemeDocument(
        scheme_id=raw["scheme_id"],
        name=raw["name"],
        description=raw["description"],
        category=category,
        ministry=raw["ministry"],
        state=raw.get("state"),
        eligibility=eligibility,
        benefits=raw["benefits"],
        application_process=raw["application_process"],
        documents_required=raw.get("documents_required", []),
        helpline=raw.get("helpline"),
        website=raw.get("website"),
        deadline=raw.get("deadline"),
        last_updated=raw["last_updated"],
        popularity_score=raw.get("popularity_score", 0.0),
    )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def seed_scheme_data(
    scheme_search: SchemeSearchService,
    *,
    path: Path | None = None,
) -> list[SchemeDocument]:
    """Load schemes from JSON and index them into the search service.

    This is the primary entry point for data seeding at application
    startup.  It loads all scheme documents from the bundled JSON file
    and passes them to the :class:`SchemeSearchService` for embedding
    generation and RAG indexing.

    Parameters
    ----------
    scheme_search:
        The :class:`SchemeSearchService` instance to initialize with
        the loaded scheme data.
    path:
        Optional path to the schemes JSON file.  Defaults to the
        bundled ``central_schemes.json``.

    Returns
    -------
    list[SchemeDocument]
        The loaded and indexed schemes.
    """
    schemes = load_schemes(path)

    if not schemes:
        logger.warning("seed.no_schemes_loaded")
        return []

    logger.info("seed.indexing_schemes", count=len(schemes))
    await scheme_search.initialize(schemes)
    logger.info("seed.complete", indexed=len(schemes))

    return schemes
