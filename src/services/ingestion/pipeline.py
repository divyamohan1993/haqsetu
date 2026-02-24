"""Scheme ingestion pipeline -- orchestrates data collection from multiple sources.

This module is the heart of the automated data pipeline.  It coordinates
fetching scheme data from government sources, merging and deduplicating
records, validating data quality, optionally translating into India's
22 Scheduled Languages, and persisting the results.

Sources (in priority order)
---------------------------
1. **MyScheme.gov.in** -- Primary source, the most comprehensive government
   scheme catalogue with 2,316+ schemes.
2. **data.gov.in** -- Supplementary financial and beneficiary data from the
   Open Government Data platform.
3. **Bundled seed data** -- Offline fallback loaded from
   ``src/data/schemes/central_schemes.json``.

Deduplication
-------------
Schemes are matched across sources using:
  - Exact ``scheme_id`` match.
  - Name similarity via normalised token overlap (>80%).
  - Ministry + category match as a tiebreaker.

Freshness
---------
Each scheme carries a ``last_updated`` timestamp and ``source`` field.
The pipeline prefers the most recently updated record from any source.

Idempotency
-----------
The pipeline is safe to run multiple times.  Duplicate runs produce the
same output.  Content-based checksums are used for change detection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.services.cache import CacheManager
    from src.services.changelog import SchemeChangelogService
    from src.services.ingestion.data_gov_client import DataGovClient
    from src.services.ingestion.myscheme_client import MySchemeClient
    from src.services.translation import TranslationService
    from src.services.verification.engine import SchemeVerificationEngine

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INGESTION_CACHE_TTL = 4 * 60 * 60  # 4 hours
_TRANSLATION_CACHE_TTL = 30 * 24 * 60 * 60  # 30 days
_SCHEMES_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "schemes"
_SCHEMES_OUTPUT_PATH = _SCHEMES_OUTPUT_DIR / "ingested_schemes.json"
_SEED_PATH = _SCHEMES_OUTPUT_DIR / "central_schemes.json"

# Languages for translation -- all 22 Scheduled Languages of India + English
_SCHEDULED_LANGUAGES = [
    "hi", "bn", "te", "mr", "ta", "ur", "gu", "kn", "or", "ml",
    "pa", "as", "ne", "sd", "sa", "ks", "mai", "kok", "doi", "mni",
    "brx", "sat",
]

# Minimum similarity threshold for name-based deduplication
_NAME_SIMILARITY_THRESHOLD = 0.80

# Minimum quality thresholds
_MIN_DESCRIPTION_LENGTH = 50


# ---------------------------------------------------------------------------
# IngestionResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class IngestionResult:
    """Report produced by an ingestion run."""

    total_fetched: int = 0
    new_schemes: int = 0
    updated_schemes: int = 0
    failed_schemes: int = 0
    sources_used: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    errors: list[str] = field(default_factory=list)
    verification_queued: int = 0
    changes_detected: int = 0

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "total_fetched": self.total_fetched,
            "new_schemes": self.new_schemes,
            "updated_schemes": self.updated_schemes,
            "failed_schemes": self.failed_schemes,
            "sources_used": self.sources_used,
            "duration_seconds": round(self.duration_seconds, 2),
            "timestamp": self.timestamp.isoformat(),
            "errors": self.errors,
            "verification_queued": self.verification_queued,
            "changes_detected": self.changes_detected,
        }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _normalise_name(name: str) -> set[str]:
    """Normalise a scheme name into a set of lowercase tokens for comparison."""
    # Remove common prefixes/suffixes, punctuation, and noise words
    noise = {
        "the", "of", "for", "and", "in", "to", "a", "an", "is",
        "scheme", "yojana", "yojna", "mission", "abhiyan", "pradhan",
        "mantri", "pm", "national", "central", "government", "india",
    }
    tokens = set()
    for token in name.lower().split():
        token = token.strip("()[]{}.,;:-\"'")
        if token and token not in noise and len(token) > 1:
            tokens.add(token)
    return tokens


def _name_similarity(name_a: str, name_b: str) -> float:
    """Compute token-overlap similarity between two scheme names.

    Returns a value in [0.0, 1.0] where 1.0 means identical token sets.
    """
    tokens_a = _normalise_name(name_a)
    tokens_b = _normalise_name(name_b)

    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b

    return len(intersection) / len(union) if union else 0.0


# ---------------------------------------------------------------------------
# SchemeIngestionPipeline
# ---------------------------------------------------------------------------


class SchemeIngestionPipeline:
    """Orchestrates scheme data collection from multiple government sources.

    Parameters
    ----------
    myscheme:
        Client for MyScheme.gov.in.
    datagov:
        Client for data.gov.in OGD API.
    cache:
        Shared cache manager.
    translation:
        Optional translation service for multi-language support.
    verification_engine:
        Optional verification engine for auto-verifying ingested schemes.
    changelog_service:
        Optional changelog service for detecting scheme changes.
    """

    def __init__(
        self,
        myscheme: MySchemeClient,
        datagov: DataGovClient,
        cache: CacheManager,
        translation: TranslationService | None = None,
        verification_engine: SchemeVerificationEngine | None = None,
        changelog_service: SchemeChangelogService | None = None,
    ) -> None:
        self._myscheme = myscheme
        self._datagov = datagov
        self._cache = cache
        self._translation = translation
        self._verification_engine = verification_engine
        self._changelog_service = changelog_service
        self._last_result: IngestionResult | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def last_result(self) -> IngestionResult | None:
        """The result of the most recent ingestion run."""
        return self._last_result

    async def run_full_ingestion(self) -> IngestionResult:
        """Run a complete scheme ingestion from all sources.

        Steps:
        1. Fetch all schemes from MyScheme.gov.in.
        2. Fetch supplementary data from data.gov.in.
        3. Load bundled seed data as fallback.
        4. Merge and deduplicate across all sources.
        5. Validate and clean each scheme record.
        6. Translate scheme names/descriptions (if translation service is
           available).
        7. Save to persistent storage (JSON file + cache).
        8. Return an ingestion report.

        This method is idempotent -- running it multiple times produces
        the same output for the same upstream data.

        Returns
        -------
        IngestionResult
            Detailed report of the ingestion run.
        """
        start = time.monotonic()
        result = IngestionResult()

        logger.info("ingestion.full_run_start")

        # -- Step 1: Fetch from MyScheme.gov.in ----------------------------
        myscheme_schemes: list[dict] = []
        try:
            myscheme_schemes = await self._myscheme.fetch_all_schemes()
            if myscheme_schemes:
                result.sources_used.append("myscheme.gov.in")
            logger.info(
                "ingestion.myscheme_fetched",
                count=len(myscheme_schemes),
            )
        except Exception as exc:
            error_msg = f"MyScheme fetch failed: {exc}"
            result.errors.append(error_msg)
            logger.error("ingestion.myscheme_failed", error=str(exc))

        # -- Step 2: Fetch supplementary data from data.gov.in -------------
        datagov_data: dict[str, list[dict]] = {}
        try:
            datagov_data = await self._datagov.fetch_all_supplementary_data()
            if any(datagov_data.values()):
                result.sources_used.append("data.gov.in")
            logger.info(
                "ingestion.datagov_fetched",
                expenditure_records=len(datagov_data.get("expenditure", [])),
                beneficiary_records=len(datagov_data.get("beneficiaries", [])),
            )
        except Exception as exc:
            error_msg = f"data.gov.in fetch failed: {exc}"
            result.errors.append(error_msg)
            logger.error("ingestion.datagov_failed", error=str(exc))

        # -- Step 3: Load seed data as fallback ----------------------------
        seed_schemes: list[dict] = []
        try:
            seed_schemes = self._load_seed_data()
            if seed_schemes:
                result.sources_used.append("bundled_seed_data")
            logger.info(
                "ingestion.seed_data_loaded", count=len(seed_schemes)
            )
        except Exception as exc:
            error_msg = f"Seed data load failed: {exc}"
            result.errors.append(error_msg)
            logger.warning("ingestion.seed_data_failed", error=str(exc))

        # -- Step 4: Merge and deduplicate ---------------------------------
        all_sources = [myscheme_schemes, seed_schemes]
        merged = await self._merge_and_deduplicate(all_sources)
        logger.info("ingestion.merged", count=len(merged))

        # -- Step 4b: Enrich with data.gov.in data -------------------------
        if datagov_data:
            merged = self._enrich_with_supplementary(merged, datagov_data)

        # -- Step 5: Validate and clean ------------------------------------
        validated: list[dict] = []
        for scheme in merged:
            cleaned = await self._validate_scheme(scheme)
            if cleaned is not None:
                validated.append(cleaned)
            else:
                result.failed_schemes += 1

        logger.info(
            "ingestion.validated",
            accepted=len(validated),
            rejected=result.failed_schemes,
        )

        # -- Step 6: Translate (if service available) ----------------------
        if self._translation is not None:
            try:
                validated = await self._translate_schemes_batch(validated)
                logger.info("ingestion.translation_complete")
            except Exception as exc:
                error_msg = f"Translation failed: {exc}"
                result.errors.append(error_msg)
                logger.warning(
                    "ingestion.translation_failed", error=str(exc)
                )

        # -- Step 7: Determine new vs updated counts -----------------------
        existing = await self._load_existing_schemes()
        existing_ids = {s.get("scheme_id") for s in existing}
        existing_by_id: dict[str, dict] = {
            s.get("scheme_id", ""): s for s in existing
        }

        for scheme in validated:
            sid = scheme.get("scheme_id")
            if sid in existing_ids:
                # Check if content actually changed
                existing_scheme = existing_by_id.get(sid)
                if existing_scheme and self._compute_checksum(
                    scheme
                ) != self._compute_checksum(existing_scheme):
                    result.updated_schemes += 1
            else:
                result.new_schemes += 1

        result.total_fetched = len(validated)

        # -- Step 7b: Detect changes (if changelog service available) ------
        if self._changelog_service is not None:
            result.changes_detected = await self._detect_and_record_changes(
                validated, existing_by_id
            )

        # -- Step 8: Save -------------------------------------------------
        await self.save_schemes(validated)

        result.duration_seconds = time.monotonic() - start
        self._last_result = result

        logger.info(
            "ingestion.full_run_complete",
            total=result.total_fetched,
            new=result.new_schemes,
            updated=result.updated_schemes,
            failed=result.failed_schemes,
            duration_s=round(result.duration_seconds, 2),
            sources=result.sources_used,
        )

        # Cache the result for status queries
        await self._cache.set(
            "ingestion:last_result",
            result.to_dict(),
            ttl_seconds=_INGESTION_CACHE_TTL,
        )

        # -- Step 9: Queue verification (non-blocking) --------------------
        if self._verification_engine is not None and validated:
            result.verification_queued = len(validated)
            asyncio.create_task(
                self._run_post_ingestion_verification(validated)
            )
            logger.info(
                "ingestion.verification_queued",
                scheme_count=result.verification_queued,
            )

        return result

    async def run_incremental_update(self) -> IngestionResult:
        """Update only schemes that have changed since the last ingestion.

        Compares ``last_updated`` timestamps and content checksums to
        detect changes, then fetches and updates only the modified
        records.  Much faster than a full ingestion for daily updates.

        Returns
        -------
        IngestionResult
            Report of the incremental update.
        """
        start = time.monotonic()
        result = IngestionResult()

        logger.info("ingestion.incremental_start")

        # Load existing schemes
        existing = await self._load_existing_schemes()
        existing_by_id: dict[str, dict] = {
            s.get("scheme_id", ""): s for s in existing
        }
        existing_checksums: dict[str, str] = {
            sid: self._compute_checksum(s)
            for sid, s in existing_by_id.items()
        }

        # Discover current slugs from MyScheme
        try:
            slugs = await self._myscheme.fetch_scheme_slugs()
            result.sources_used.append("myscheme.gov.in")
        except Exception as exc:
            error_msg = f"Slug discovery failed: {exc}"
            result.errors.append(error_msg)
            logger.error("ingestion.incremental_slug_failed", error=str(exc))
            result.duration_seconds = time.monotonic() - start
            self._last_result = result
            return result

        # Fetch details for schemes we don't have or that may have changed
        updated_schemes: list[dict] = []

        for slug in slugs:
            try:
                detail = await self._myscheme.fetch_scheme_detail(slug)
                if detail is None:
                    result.failed_schemes += 1
                    continue

                sid = detail.get("scheme_id", "")
                new_checksum = self._compute_checksum(detail)

                if sid not in existing_by_id:
                    # New scheme
                    cleaned = await self._validate_scheme(detail)
                    if cleaned:
                        updated_schemes.append(cleaned)
                        result.new_schemes += 1
                elif new_checksum != existing_checksums.get(sid, ""):
                    # Changed scheme
                    cleaned = await self._validate_scheme(detail)
                    if cleaned:
                        updated_schemes.append(cleaned)
                        result.updated_schemes += 1

            except Exception as exc:
                result.failed_schemes += 1
                result.errors.append(f"Failed to process {slug}: {exc}")
                logger.warning(
                    "ingestion.incremental_scheme_failed",
                    slug=slug,
                    error=str(exc),
                )

        # Merge updates into existing
        if updated_schemes:
            updated_ids = {s.get("scheme_id") for s in updated_schemes}
            # Keep existing schemes that weren't updated
            final = [
                s for s in existing if s.get("scheme_id") not in updated_ids
            ]
            final.extend(updated_schemes)

            # Translate new/updated schemes
            if self._translation is not None:
                try:
                    updated_schemes = await self._translate_schemes_batch(
                        updated_schemes
                    )
                except Exception:
                    logger.warning(
                        "ingestion.incremental_translation_failed",
                        exc_info=True,
                    )

            # Detect changes (if changelog service available)
            if self._changelog_service is not None:
                result.changes_detected = await self._detect_and_record_changes(
                    updated_schemes, existing_by_id
                )

            await self.save_schemes(final)

        result.total_fetched = len(updated_schemes)
        result.duration_seconds = time.monotonic() - start
        self._last_result = result

        logger.info(
            "ingestion.incremental_complete",
            new=result.new_schemes,
            updated=result.updated_schemes,
            failed=result.failed_schemes,
            duration_s=round(result.duration_seconds, 2),
        )

        await self._cache.set(
            "ingestion:last_result",
            result.to_dict(),
            ttl_seconds=_INGESTION_CACHE_TTL,
        )

        # Queue verification for updated schemes (non-blocking)
        if self._verification_engine is not None and updated_schemes:
            result.verification_queued = len(updated_schemes)
            asyncio.create_task(
                self._run_post_ingestion_verification(updated_schemes)
            )
            logger.info(
                "ingestion.verification_queued",
                scheme_count=result.verification_queued,
            )

        return result

    # ------------------------------------------------------------------
    # Merge & Deduplication
    # ------------------------------------------------------------------

    async def _merge_and_deduplicate(
        self, sources: list[list[dict]]
    ) -> list[dict]:
        """Merge schemes from multiple sources, preferring the most recent data.

        Matching strategy:
        1. Exact ``scheme_id`` match.
        2. Name similarity via normalised token overlap (>80%).
        3. Ministry + category match as a tiebreaker.

        When duplicates are found, the record with the most recent
        ``last_updated`` timestamp is preferred.

        Parameters
        ----------
        sources:
            List of scheme lists from different data sources.

        Returns
        -------
        list[dict]
            Deduplicated, merged scheme list.
        """
        # Flatten all sources
        all_schemes: list[dict] = []
        for source in sources:
            all_schemes.extend(source)

        if not all_schemes:
            return []

        # Index by scheme_id for fast lookup
        by_id: dict[str, dict] = {}
        unmatched: list[dict] = []

        for scheme in all_schemes:
            sid = scheme.get("scheme_id", "")
            if sid and sid in by_id:
                # Duplicate by ID -- prefer the more recently updated one
                existing = by_id[sid]
                if self._is_more_recent(scheme, existing):
                    by_id[sid] = self._merge_records(existing, scheme)
                else:
                    by_id[sid] = self._merge_records(scheme, existing)
            elif sid:
                by_id[sid] = scheme
            else:
                unmatched.append(scheme)

        # Deduplicate unmatched by name similarity
        merged_list = list(by_id.values())

        for scheme in unmatched:
            name = scheme.get("name", "")
            if not name:
                continue

            found_match = False
            for existing in merged_list:
                existing_name = existing.get("name", "")
                similarity = _name_similarity(name, existing_name)

                if similarity >= _NAME_SIMILARITY_THRESHOLD:
                    # Additional check: ministry and category should align
                    same_ministry = (
                        scheme.get("ministry", "").lower()
                        == existing.get("ministry", "").lower()
                    )
                    same_category = (
                        scheme.get("category", "")
                        == existing.get("category", "")
                    )

                    if same_ministry or same_category:
                        # Merge into existing
                        if self._is_more_recent(scheme, existing):
                            idx = merged_list.index(existing)
                            merged_list[idx] = self._merge_records(
                                existing, scheme
                            )
                        found_match = True
                        break

            if not found_match:
                merged_list.append(scheme)

        return merged_list

    def _is_more_recent(self, scheme_a: dict, scheme_b: dict) -> bool:
        """Return True if scheme_a has a more recent last_updated than scheme_b."""
        ts_a = scheme_a.get("last_updated", "")
        ts_b = scheme_b.get("last_updated", "")
        return str(ts_a) > str(ts_b)

    def _merge_records(self, base: dict, override: dict) -> dict:
        """Merge two scheme records, preferring non-empty fields from override."""
        merged = dict(base)
        for key, value in override.items():
            if value is not None and value != "" and value != []:
                # Don't override non-empty base fields with empty override
                existing = merged.get(key)
                if existing is None or existing == "" or existing == []:
                    merged[key] = value
                elif key == "last_updated":
                    # Always take the more recent timestamp
                    if str(value) > str(existing):
                        merged[key] = value
                elif key in ("description", "benefits", "application_process"):
                    # Prefer longer content
                    if isinstance(value, str) and isinstance(existing, str):
                        if len(value) > len(existing):
                            merged[key] = value
                elif key == "documents_required":
                    # Merge document lists
                    if isinstance(value, list) and isinstance(existing, list):
                        combined = list(existing)
                        for doc in value:
                            if doc not in combined:
                                combined.append(doc)
                        merged[key] = combined
                elif key == "custom_criteria":
                    # Merge criteria lists
                    if isinstance(value, list) and isinstance(existing, list):
                        combined = list(existing)
                        for item in value:
                            if item not in combined:
                                combined.append(item)
                        merged[key] = combined

        return merged

    def _enrich_with_supplementary(
        self,
        schemes: list[dict],
        supplementary: dict[str, list[dict]],
    ) -> list[dict]:
        """Enrich scheme records with data.gov.in supplementary data.

        Adds expenditure and beneficiary information where available.
        Matching is done by scheme name similarity since data.gov.in
        uses different identifiers.
        """
        expenditure = supplementary.get("expenditure", [])
        beneficiaries = supplementary.get("beneficiaries", [])

        if not expenditure and not beneficiaries:
            return schemes

        # Build name-based index of supplementary data
        expenditure_by_name: dict[str, dict] = {}
        for record in expenditure:
            name = (
                record.get("scheme_name")
                or record.get("scheme")
                or record.get("name")
                or ""
            )
            if name:
                expenditure_by_name[name.lower().strip()] = record

        beneficiary_by_name: dict[str, dict] = {}
        for record in beneficiaries:
            name = (
                record.get("scheme_name")
                or record.get("scheme")
                or record.get("name")
                or ""
            )
            if name:
                beneficiary_by_name[name.lower().strip()] = record

        for scheme in schemes:
            scheme_name = scheme.get("name", "").lower().strip()
            scheme_tokens = _normalise_name(scheme_name)

            # Try to match expenditure data
            for exp_name, exp_data in expenditure_by_name.items():
                exp_tokens = _normalise_name(exp_name)
                if scheme_tokens and exp_tokens:
                    overlap = len(scheme_tokens & exp_tokens) / len(
                        scheme_tokens | exp_tokens
                    )
                    if overlap >= 0.6:
                        scheme["expenditure_data"] = {
                            "amount": exp_data.get("amount")
                            or exp_data.get("expenditure"),
                            "financial_year": exp_data.get("financial_year")
                            or exp_data.get("year"),
                        }
                        break

            # Try to match beneficiary data
            for ben_name, ben_data in beneficiary_by_name.items():
                ben_tokens = _normalise_name(ben_name)
                if scheme_tokens and ben_tokens:
                    overlap = len(scheme_tokens & ben_tokens) / len(
                        scheme_tokens | ben_tokens
                    )
                    if overlap >= 0.6:
                        scheme["beneficiary_data"] = {
                            "count": ben_data.get("beneficiary_count")
                            or ben_data.get("beneficiaries"),
                            "year": ben_data.get("year")
                            or ben_data.get("financial_year"),
                        }
                        break

        return schemes

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def _validate_scheme(self, scheme: dict) -> dict | None:
        """Validate that a scheme has minimum required fields and data quality.

        Rejects schemes that:
        - Are missing a name or description.
        - Have a description shorter than 50 characters.
        - Contain clearly templated/placeholder text.

        Parameters
        ----------
        scheme:
            Raw scheme dictionary.

        Returns
        -------
        dict | None
            The cleaned scheme dictionary, or ``None`` if it fails validation.
        """
        name = scheme.get("name", "").strip()
        description = scheme.get("description", "").strip()

        # Must have name
        if not name:
            logger.debug(
                "ingestion.validation_no_name",
                scheme_id=scheme.get("scheme_id"),
            )
            return None

        # Must have description
        if not description:
            logger.debug(
                "ingestion.validation_no_description",
                scheme_id=scheme.get("scheme_id"),
                name=name,
            )
            return None

        # Description must be substantive
        if len(description) < _MIN_DESCRIPTION_LENGTH:
            logger.debug(
                "ingestion.validation_short_description",
                scheme_id=scheme.get("scheme_id"),
                name=name,
                desc_len=len(description),
            )
            return None

        # Reject placeholder/template content
        placeholder_patterns = [
            "lorem ipsum",
            "sample text",
            "test scheme",
            "placeholder",
            "coming soon",
            "to be updated",
            "tbd",
            "n/a" * 5,
        ]
        desc_lower = description.lower()
        for pattern in placeholder_patterns:
            if pattern in desc_lower:
                logger.debug(
                    "ingestion.validation_placeholder",
                    scheme_id=scheme.get("scheme_id"),
                    name=name,
                    pattern=pattern,
                )
                return None

        # Ensure scheme_id exists
        if not scheme.get("scheme_id"):
            # Generate a stable ID from the name
            name_hash = hashlib.sha256(name.encode()).hexdigest()[:12]
            scheme["scheme_id"] = f"auto-{name_hash}"

        # Ensure required fields have defaults
        scheme.setdefault("category", "other")
        scheme.setdefault("ministry", "Government of India")
        scheme.setdefault("benefits", "")
        scheme.setdefault("application_process", "")
        scheme.setdefault("documents_required", [])
        scheme.setdefault("eligibility", {"custom_criteria": []})
        scheme.setdefault(
            "last_updated", datetime.now(timezone.utc).isoformat()
        )
        scheme.setdefault("popularity_score", 0.0)

        # Clean up the name and description
        scheme["name"] = name
        scheme["description"] = description

        return scheme

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    async def _translate_schemes_batch(
        self, schemes: list[dict]
    ) -> list[dict]:
        """Translate scheme names and descriptions into all Scheduled Languages.

        Uses the translation service's batch API for efficiency.  Skips
        languages where a translation already exists.  Caches translations
        with a 30-day TTL.

        Parameters
        ----------
        schemes:
            List of scheme dictionaries to translate.

        Returns
        -------
        list[dict]
            Schemes with ``name_translations`` and ``description_translations``
            populated.
        """
        if self._translation is None:
            return schemes

        logger.info(
            "ingestion.translation_start",
            scheme_count=len(schemes),
            language_count=len(_SCHEDULED_LANGUAGES),
        )

        for lang in _SCHEDULED_LANGUAGES:
            # Collect names/descriptions that need translation
            names_to_translate: list[str] = []
            desc_to_translate: list[str] = []
            name_indices: list[int] = []
            desc_indices: list[int] = []

            for idx, scheme in enumerate(schemes):
                existing_name_trans = scheme.get("name_translations", {})
                existing_desc_trans = scheme.get("description_translations", {})

                if lang not in existing_name_trans and scheme.get("name"):
                    names_to_translate.append(scheme["name"])
                    name_indices.append(idx)

                if lang not in existing_desc_trans and scheme.get("description"):
                    # Truncate long descriptions for translation efficiency
                    desc = scheme["description"][:500]
                    desc_to_translate.append(desc)
                    desc_indices.append(idx)

            # Batch translate names
            if names_to_translate:
                try:
                    translated_names = await self._translation.translate_batch(
                        names_to_translate,
                        source_lang="en",
                        target_lang=lang,
                    )
                    for i, translated in zip(name_indices, translated_names):
                        if "name_translations" not in schemes[i]:
                            schemes[i]["name_translations"] = {}
                        schemes[i]["name_translations"][lang] = translated
                except Exception:
                    logger.warning(
                        "ingestion.name_translation_failed",
                        lang=lang,
                        exc_info=True,
                    )

            # Batch translate descriptions
            if desc_to_translate:
                try:
                    translated_descs = await self._translation.translate_batch(
                        desc_to_translate,
                        source_lang="en",
                        target_lang=lang,
                    )
                    for i, translated in zip(desc_indices, translated_descs):
                        if "description_translations" not in schemes[i]:
                            schemes[i]["description_translations"] = {}
                        schemes[i]["description_translations"][lang] = translated
                except Exception:
                    logger.warning(
                        "ingestion.desc_translation_failed",
                        lang=lang,
                        exc_info=True,
                    )

        return schemes

    async def _translate_scheme(self, scheme: dict) -> dict:
        """Translate a single scheme's name and description to all languages.

        This is a convenience method that delegates to the batch translator.

        Parameters
        ----------
        scheme:
            Scheme dictionary to translate.

        Returns
        -------
        dict
            Scheme with translations added.
        """
        result = await self._translate_schemes_batch([scheme])
        return result[0] if result else scheme

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def save_schemes(
        self, schemes: list[dict], path: str | None = None
    ) -> None:
        """Save ingested schemes to a JSON file and update the cache.

        Parameters
        ----------
        schemes:
            List of validated scheme dictionaries.
        path:
            Optional output file path.  Defaults to
            ``src/data/schemes/ingested_schemes.json``.
        """
        output_path = Path(path) if path else _SCHEMES_OUTPUT_PATH
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Sort by scheme_id for deterministic output (idempotency)
        sorted_schemes = sorted(
            schemes, key=lambda s: s.get("scheme_id", "")
        )

        try:
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(sorted_schemes, f, ensure_ascii=False, indent=2, default=str)
            logger.info(
                "ingestion.saved_to_file",
                path=str(output_path),
                count=len(sorted_schemes),
            )
        except OSError as exc:
            logger.error(
                "ingestion.save_file_failed",
                path=str(output_path),
                error=str(exc),
            )

        # Also update the cache
        try:
            await self._cache.set(
                "ingestion:all_schemes",
                sorted_schemes,
                ttl_seconds=_INGESTION_CACHE_TTL,
            )
            logger.info("ingestion.saved_to_cache", count=len(sorted_schemes))
        except Exception:
            logger.warning("ingestion.save_cache_failed", exc_info=True)

    async def _load_existing_schemes(self) -> list[dict]:
        """Load previously ingested schemes from cache or file."""
        # Try cache first
        cached = await self._cache.get("ingestion:all_schemes")
        if cached is not None and isinstance(cached, list):
            return cached

        # Try file
        if _SCHEMES_OUTPUT_PATH.exists():
            try:
                with _SCHEMES_OUTPUT_PATH.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        return []

    def _load_seed_data(self) -> list[dict]:
        """Load the bundled seed data as a fallback source."""
        if not _SEED_PATH.exists():
            logger.debug("ingestion.no_seed_data", path=str(_SEED_PATH))
            return []

        try:
            with _SEED_PATH.open("r", encoding="utf-8") as f:
                raw_schemes: list[dict] = json.load(f)

            # Tag each with the source
            for scheme in raw_schemes:
                scheme.setdefault("source", "bundled_seed_data")

            return raw_schemes
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "ingestion.seed_data_load_error", error=str(exc)
            )
            return []

    # ------------------------------------------------------------------
    # Checksums for change detection
    # ------------------------------------------------------------------

    def _compute_checksum(self, scheme: dict) -> str:
        """Compute a content-based hash for change detection.

        Only includes fields that represent substantive content (not
        metadata like timestamps or popularity scores).

        Parameters
        ----------
        scheme:
            Scheme dictionary.

        Returns
        -------
        str
            Hex digest of the content hash.
        """
        content_fields = [
            scheme.get("name", ""),
            scheme.get("description", ""),
            scheme.get("benefits", ""),
            scheme.get("application_process", ""),
            scheme.get("ministry", ""),
            scheme.get("category", ""),
            str(scheme.get("documents_required", [])),
            str(scheme.get("eligibility", {})),
        ]
        content_str = "|".join(content_fields)
        return hashlib.sha256(content_str.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Post-ingestion verification
    # ------------------------------------------------------------------

    async def _run_post_ingestion_verification(
        self, schemes: list[dict]
    ) -> None:
        """Verify ingested schemes in the background with bounded concurrency.

        Uses a bounded semaphore (3 concurrent) to avoid overloading
        upstream government APIs.  Runs as a fire-and-forget background
        task so that the main ingestion is not blocked.

        Parameters
        ----------
        schemes:
            List of validated scheme dictionaries to verify.
        """
        if self._verification_engine is None:
            return

        semaphore = asyncio.BoundedSemaphore(3)

        logger.info(
            "ingestion.post_verification_start",
            scheme_count=len(schemes),
        )

        async def _verify_one(scheme: dict) -> None:
            async with semaphore:
                sid = scheme.get("scheme_id", "")
                name = scheme.get("name", "")
                try:
                    result = await self._verification_engine.verify_scheme(
                        scheme_id=sid,
                        scheme_name=name,
                        ministry=scheme.get("ministry"),
                    )
                    logger.info(
                        "ingestion.post_verification_result",
                        scheme_id=sid,
                        status=result.status,
                        trust_score=round(result.trust_score, 4),
                    )
                except Exception as exc:
                    logger.error(
                        "ingestion.post_verification_failed",
                        scheme_id=sid,
                        error=str(exc),
                    )

        tasks = [_verify_one(scheme) for scheme in schemes]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            "ingestion.post_verification_complete",
            scheme_count=len(schemes),
        )

    # ------------------------------------------------------------------
    # Changelog detection
    # ------------------------------------------------------------------

    async def _detect_and_record_changes(
        self,
        new_schemes: list[dict],
        existing_by_id: dict[str, dict],
    ) -> int:
        """Compare new scheme data with existing data and record changes.

        For each scheme that already exists, calls the changelog service's
        :meth:`detect_changes` method and persists any detected changes
        via :meth:`record_changes`.

        Parameters
        ----------
        new_schemes:
            List of newly ingested/validated scheme dictionaries.
        existing_by_id:
            Mapping from ``scheme_id`` to the previously stored scheme
            dictionary.

        Returns
        -------
        int
            Total number of individual field-level changes detected.
        """
        if self._changelog_service is None:
            return 0

        total_changes = 0

        for scheme in new_schemes:
            sid = scheme.get("scheme_id", "")
            old_scheme = existing_by_id.get(sid)
            if old_scheme is None:
                # New scheme -- no previous version to diff
                continue

            try:
                changes = self._changelog_service.detect_changes(
                    old_scheme, scheme
                )
                if changes:
                    await self._changelog_service.record_changes(changes)
                    total_changes += len(changes)
                    logger.info(
                        "ingestion.changelog_recorded",
                        scheme_id=sid,
                        change_count=len(changes),
                    )
            except Exception as exc:
                logger.error(
                    "ingestion.changelog_detection_failed",
                    scheme_id=sid,
                    error=str(exc),
                )

        if total_changes:
            logger.info(
                "ingestion.changelog_detection_complete",
                total_changes=total_changes,
            )

        return total_changes
