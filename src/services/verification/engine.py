"""Core verification engine for multi-source government scheme verification.

This module orchestrates the verification of government schemes against
multiple authoritative Indian government data sources.  It ONLY trusts
official government documents as proof of a scheme's existence, legality,
and current status.

Trust Hierarchy (highest to lowest)
------------------------------------
1. **Gazette of India** notification -- weight 1.0
   The official record of all government orders, acts, and statutory rules.
2. **India Code** Act/legislation -- weight 0.9
   The authoritative repository of all Central Acts maintained by the
   Legislative Department.
3. **Sansad (Parliament)** bill/act record -- weight 0.85
   Records from the Lok Sabha / Rajya Sabha tracking bills and enacted
   legislation.
4. **MyScheme.gov.in** listing -- weight 0.7
   The Government of India's official scheme discovery portal.
5. **data.gov.in** dataset -- weight 0.5
   Open Government Data platform with scheme expenditure and beneficiary
   datasets.

Trust Score Calculation
-----------------------
- ``trust_score = weighted_sum(evidence_weights) / max_possible_score``
- Max possible score is ``1.0 + 0.9 + 0.85 + 0.7 + 0.5 = 3.95``.
- **verified**: ``trust_score >= 0.7`` AND at least 2 sources confirm.
- **partially_verified**: ``trust_score >= 0.4`` OR 1 source confirms.
- **disputed**: conflicting evidence found across sources.
- **revoked**: official Gazette revocation notice found.

Idempotency
------------
Repeated verification of the same scheme produces the same result
given the same upstream data.  Evidence is collected deterministically
and trust scores are computed from a fixed formula.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.models.verification import (
        VerificationEvidence,
        VerificationResult,
        VerificationStatus,
    )
    from src.services.cache import CacheManager
    from src.services.ingestion.data_gov_client import DataGovClient
    from src.services.ingestion.myscheme_client import MySchemeClient
    from src.services.verification.gazette_client import GazetteClient
    from src.services.verification.indiacode_client import IndiaCodeClient
    from src.services.verification.sansad_client import SansadClient

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERIFICATION_CACHE_TTL = 24 * 60 * 60  # 24 hours

# Trust weights for each source (highest = most authoritative)
_SOURCE_WEIGHTS: dict[str, float] = {
    "gazette_of_india": 1.0,
    "india_code": 0.9,
    "sansad_parliament": 0.85,
    "myscheme_gov": 0.7,
    "data_gov_in": 0.5,
}

# Maximum possible trust score (sum of all source weights)
_MAX_TRUST_SCORE = sum(_SOURCE_WEIGHTS.values())  # 3.95

# Status thresholds
_VERIFIED_THRESHOLD = 0.7
_VERIFIED_MIN_SOURCES = 2
_PARTIAL_THRESHOLD = 0.4
_PARTIAL_MIN_SOURCES = 1

# Default staleness window for re-verification (7 days)
_DEFAULT_MAX_AGE_HOURS = 168


# ---------------------------------------------------------------------------
# SchemeVerificationEngine
# ---------------------------------------------------------------------------


class SchemeVerificationEngine:
    """Orchestrates multi-source verification of government schemes.

    Queries five authoritative Indian government data sources in parallel,
    collects evidence, computes a weighted trust score, and determines
    the final verification status of each scheme.

    Parameters
    ----------
    gazette_client:
        Client for querying the Gazette of India.
    sansad_client:
        Client for querying Sansad (Parliament) records.
    indiacode_client:
        Client for querying India Code legislation database.
    myscheme_client:
        Client for querying MyScheme.gov.in.
    datagov_client:
        Client for querying data.gov.in OGD platform.
    cache:
        Shared cache manager for caching verification results.
    """

    def __init__(
        self,
        gazette_client: GazetteClient,
        sansad_client: SansadClient,
        indiacode_client: IndiaCodeClient,
        myscheme_client: MySchemeClient,
        datagov_client: DataGovClient,
        cache: CacheManager,
    ) -> None:
        self._gazette = gazette_client
        self._sansad = sansad_client
        self._indiacode = indiacode_client
        self._myscheme = myscheme_client
        self._datagov = datagov_client
        self._cache = cache

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def verify_scheme(
        self,
        scheme_id: str,
        scheme_name: str,
        ministry: str | None = None,
        existing_evidence: list[VerificationEvidence] | None = None,
    ) -> VerificationResult:
        """Run full verification of a single scheme across ALL sources.

        Queries every source in parallel using :func:`asyncio.gather`,
        collects all returned evidence, merges with any pre-existing
        evidence, computes a weighted trust score, and determines the
        final verification status.

        Parameters
        ----------
        scheme_id:
            Unique identifier of the scheme to verify.
        scheme_name:
            Human-readable name of the scheme (used for searching
            government databases).
        ministry:
            Optional ministry name to narrow searches.
        existing_evidence:
            Previously collected evidence to merge with fresh results.
            Useful for incremental re-verification.

        Returns
        -------
        VerificationResult
            Complete verification outcome including trust score, status,
            all evidence collected, and any detected conflicts.
        """
        from src.models.verification import VerificationResult

        start = time.monotonic()

        logger.info(
            "verification.scheme_start",
            scheme_id=scheme_id,
            scheme_name=scheme_name,
            ministry=ministry,
        )

        # -- Check cache for recent verification ----------------------------
        cache_key = f"verification:{scheme_id}"
        cached: dict | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.info(
                "verification.cache_hit",
                scheme_id=scheme_id,
            )
            return VerificationResult(**cached)

        # -- Query all sources in parallel ----------------------------------
        gazette_task = self._check_gazette(scheme_name, ministry)
        parliament_task = self._check_parliament(scheme_name, ministry)
        indiacode_task = self._check_india_code(scheme_name, ministry)
        myscheme_task = self._check_myscheme(scheme_name)
        datagov_task = self._check_datagov(scheme_name)

        results = await asyncio.gather(
            gazette_task,
            parliament_task,
            indiacode_task,
            myscheme_task,
            datagov_task,
            return_exceptions=True,
        )

        # -- Collect evidence from each source ------------------------------
        all_evidence: list[VerificationEvidence] = []
        sources_checked: list[str] = []
        errors: list[str] = []

        source_names = [
            "gazette_of_india",
            "sansad_parliament",
            "india_code",
            "myscheme_gov",
            "data_gov_in",
        ]

        for source_name, result in zip(source_names, results):
            sources_checked.append(source_name)
            if isinstance(result, Exception):
                error_msg = f"{source_name} check failed: {result}"
                errors.append(error_msg)
                logger.error(
                    "verification.source_failed",
                    scheme_id=scheme_id,
                    source=source_name,
                    error=str(result),
                )
            elif isinstance(result, list):
                all_evidence.extend(result)
                logger.info(
                    "verification.source_checked",
                    scheme_id=scheme_id,
                    source=source_name,
                    evidence_count=len(result),
                )

        # -- Merge with existing evidence (if any) --------------------------
        if existing_evidence:
            # Avoid duplicates by checking (source, document_id) pairs
            existing_keys = set()
            for ev in all_evidence:
                existing_keys.add((ev.source, ev.document_id))

            for ev in existing_evidence:
                if (ev.source, ev.document_id) not in existing_keys:
                    all_evidence.append(ev)

            logger.info(
                "verification.merged_existing_evidence",
                scheme_id=scheme_id,
                existing_count=len(existing_evidence),
                total_count=len(all_evidence),
            )

        # -- Detect conflicts -----------------------------------------------
        conflicts = self._detect_conflicts(all_evidence)
        if conflicts:
            logger.warning(
                "verification.conflicts_detected",
                scheme_id=scheme_id,
                conflict_count=len(conflicts),
                conflicts=conflicts,
            )

        # -- Compute trust score and status ---------------------------------
        trust_score, status = self._compute_trust_score(all_evidence)

        duration = time.monotonic() - start

        # Determine which sources provided evidence
        sources_confirmed = list({ev.source for ev in all_evidence})

        now = datetime.now(timezone.utc)
        verification_result = VerificationResult(
            scheme_id=scheme_id,
            status=status,
            trust_score=trust_score,
            evidences=all_evidence,
            sources_checked=sources_checked,
            sources_confirmed=sources_confirmed,
            verification_started_at=now,
            verification_completed_at=now,
            notes=errors + [f"Conflict: {c}" for c in conflicts],
        )

        logger.info(
            "verification.scheme_complete",
            scheme_id=scheme_id,
            status=status,
            trust_score=round(trust_score, 4),
            evidence_count=len(all_evidence),
            conflict_count=len(conflicts),
            duration_s=round(duration, 2),
        )

        # -- Cache the result -----------------------------------------------
        try:
            await self._cache.set(
                cache_key,
                verification_result.to_dict(),
                ttl_seconds=_VERIFICATION_CACHE_TTL,
            )
        except Exception:
            logger.warning(
                "verification.cache_set_failed",
                scheme_id=scheme_id,
                exc_info=True,
            )

        return verification_result

    async def verify_batch(
        self,
        schemes: list[dict],
        max_concurrent: int = 3,
    ) -> list[VerificationResult]:
        """Verify multiple schemes with bounded concurrency.

        Uses an :class:`asyncio.Semaphore` to limit the number of
        concurrent verification tasks, preventing overload on upstream
        government APIs.

        Parameters
        ----------
        schemes:
            List of scheme dictionaries.  Each must contain at least
            ``scheme_id`` and ``scheme_name`` keys.  An optional
            ``ministry`` key narrows the search.
        max_concurrent:
            Maximum number of schemes to verify simultaneously.
            Defaults to 3.

        Returns
        -------
        list[VerificationResult]
            Verification results in the same order as the input list.
        """
        if not schemes:
            return []

        semaphore = asyncio.Semaphore(max_concurrent)
        results: list[VerificationResult | None] = [None] * len(schemes)

        logger.info(
            "verification.batch_start",
            scheme_count=len(schemes),
            max_concurrent=max_concurrent,
        )

        async def _verify_with_semaphore(
            index: int, scheme: dict
        ) -> None:
            async with semaphore:
                try:
                    result = await self.verify_scheme(
                        scheme_id=scheme["scheme_id"],
                        scheme_name=scheme["scheme_name"],
                        ministry=scheme.get("ministry"),
                    )
                    results[index] = result
                except Exception as exc:
                    logger.error(
                        "verification.batch_item_failed",
                        scheme_id=scheme.get("scheme_id"),
                        error=str(exc),
                    )
                    # Create a failed result so the caller gets a
                    # result for every input scheme
                    from src.models.verification import VerificationResult

                    results[index] = VerificationResult(
                        scheme_id=scheme.get("scheme_id", "unknown"),
                        status="unverified",
                        trust_score=0.0,
                        evidences=[],
                        sources_checked=[],
                        sources_confirmed=[],
                        notes=[f"Verification failed: {exc}"],
                        verification_completed_at=datetime.now(timezone.utc),
                    )

        tasks = [
            _verify_with_semaphore(i, scheme)
            for i, scheme in enumerate(schemes)
        ]
        await asyncio.gather(*tasks)

        # Filter out any remaining None entries (should not happen, but
        # defensive programming)
        final_results: list[VerificationResult] = [
            r for r in results if r is not None
        ]

        logger.info(
            "verification.batch_complete",
            total=len(schemes),
            verified=len(final_results),
        )

        return final_results

    async def reverify_stale(
        self,
        all_results: list[VerificationResult],
        max_age_hours: int = _DEFAULT_MAX_AGE_HOURS,
        scheme_names: dict[str, str] | None = None,
    ) -> list[VerificationResult]:
        """Re-verify schemes whose verification is older than *max_age_hours*.

        Only re-checks sources that previously failed or returned low
        confidence evidence, making this more efficient than a full
        re-verification pass.

        Parameters
        ----------
        all_results:
            List of previous :class:`VerificationResult` objects.
        max_age_hours:
            Maximum age (in hours) before a result is considered stale.
            Defaults to 168 (7 days).
        scheme_names:
            Optional mapping of scheme_id to scheme_name for search queries.
            Falls back to scheme_id when not provided.

        Returns
        -------
        list[VerificationResult]
            Updated verification results for schemes that were stale.
            Results that are still fresh are returned unchanged.
        """
        now = datetime.now(timezone.utc)
        updated_results: list[VerificationResult] = []
        stale_count = 0

        logger.info(
            "verification.reverify_stale_start",
            total_results=len(all_results),
            max_age_hours=max_age_hours,
        )

        for result in all_results:
            # Determine age of the verification
            if result.verified_at is None:
                age_hours = max_age_hours + 1  # Force re-verification
            else:
                age_delta = now - result.verified_at
                age_hours = age_delta.total_seconds() / 3600

            if age_hours <= max_age_hours:
                # Still fresh -- keep as-is
                updated_results.append(result)
                continue

            stale_count += 1

            logger.info(
                "verification.reverifying_stale",
                scheme_id=result.scheme_id,
                age_hours=round(age_hours, 1),
            )

            # Identify sources that previously succeeded with good evidence
            # so we can skip them and only re-check failed/weak sources
            strong_evidence: list[VerificationEvidence] = []
            for ev in result.evidences:
                if ev.trust_weight >= _SOURCE_WEIGHTS.get("myscheme_gov", 0.7):
                    strong_evidence.append(ev)

            _names = scheme_names or {}
            _name = _names.get(result.scheme_id, result.scheme_id)

            try:
                refreshed = await self.verify_scheme(
                    scheme_id=result.scheme_id,
                    scheme_name=_name,
                    ministry=None,
                    existing_evidence=strong_evidence,
                )
                updated_results.append(refreshed)
            except Exception as exc:
                logger.error(
                    "verification.reverify_failed",
                    scheme_id=result.scheme_id,
                    error=str(exc),
                )
                # Keep the old result on failure
                updated_results.append(result)

        logger.info(
            "verification.reverify_stale_complete",
            total=len(all_results),
            stale=stale_count,
            refreshed=stale_count,
        )

        return updated_results

    # ------------------------------------------------------------------
    # Source-specific checks
    # ------------------------------------------------------------------

    async def _check_gazette(
        self,
        scheme_name: str,
        ministry: str | None,
    ) -> list[VerificationEvidence]:
        """Check the Gazette of India for notifications about the scheme.

        The Gazette of India is the highest-authority source.  A gazette
        notification constitutes definitive proof that a scheme was
        officially established, amended, or revoked.

        Parameters
        ----------
        scheme_name:
            Name of the scheme to search for.
        ministry:
            Optional ministry to narrow the gazette search.

        Returns
        -------
        list[VerificationEvidence]
            Evidence records found in the Gazette, each carrying a
            trust weight of 1.0.
        """
        from src.models.verification import VerificationEvidence

        evidence: list[VerificationEvidence] = []

        try:
            results = await self._gazette.search_notifications(
                query=scheme_name,
                ministry=ministry,
            )

            for record in results:
                evidence.append(
                    VerificationEvidence(
                        source="gazette_of_india",
                        source_url=record.get("url", ""),
                        document_type="gazette_notification",
                        document_id=record.get("notification_id", ""),
                        title=record.get("title", "Gazette of India"),
                        document_date=record.get("date"),
                        trust_weight=_SOURCE_WEIGHTS["gazette_of_india"],
                        excerpt=record.get("snippet", "")[:500],
                        raw_metadata={
                            "status_indication": record.get("status", "active"),
                            **(record.get("metadata") or {}),
                        },
                    )
                )

            logger.info(
                "verification.gazette_checked",
                scheme_name=scheme_name,
                evidence_count=len(evidence),
            )

        except Exception as exc:
            logger.error(
                "verification.gazette_check_failed",
                scheme_name=scheme_name,
                error=str(exc),
            )
            raise

        return evidence

    async def _check_parliament(
        self,
        scheme_name: str,
        ministry: str | None,
    ) -> list[VerificationEvidence]:
        """Check Sansad (Parliament) records for bills and acts related to the scheme.

        Parliamentary records provide strong evidence when a scheme was
        established through legislation (as opposed to an executive
        order or gazette notification).

        Parameters
        ----------
        scheme_name:
            Name of the scheme to search for.
        ministry:
            Optional ministry to narrow the parliamentary search.

        Returns
        -------
        list[VerificationEvidence]
            Evidence records found in Sansad records, each carrying a
            trust weight of 0.85.
        """
        from src.models.verification import VerificationEvidence

        evidence: list[VerificationEvidence] = []

        try:
            results = await self._sansad.search_bills_and_acts(
                query=scheme_name,
                ministry=ministry,
            )

            for record in results:
                evidence.append(
                    VerificationEvidence(
                        source="sansad_parliament",
                        source_url=record.get("url", ""),
                        document_type="parliamentary_record",
                        document_id=record.get("bill_id", ""),
                        title=record.get("title", "Sansad (Parliament of India)"),
                        document_date=record.get("date"),
                        trust_weight=_SOURCE_WEIGHTS["sansad_parliament"],
                        excerpt=record.get("snippet", "")[:500],
                        raw_metadata={
                            "status_indication": record.get("status", "active"),
                            **(record.get("metadata") or {}),
                        },
                    )
                )

            logger.info(
                "verification.parliament_checked",
                scheme_name=scheme_name,
                evidence_count=len(evidence),
            )

        except Exception as exc:
            logger.error(
                "verification.parliament_check_failed",
                scheme_name=scheme_name,
                error=str(exc),
            )
            raise

        return evidence

    async def _check_india_code(
        self,
        scheme_name: str,
        ministry: str | None,
    ) -> list[VerificationEvidence]:
        """Check India Code for enabling legislation related to the scheme.

        India Code (indiacode.nic.in) is the official repository of all
        Central and State Acts.  Finding an enabling Act provides very
        strong evidence of a scheme's legal basis.

        Parameters
        ----------
        scheme_name:
            Name of the scheme to search for.
        ministry:
            Optional ministry to narrow the search.

        Returns
        -------
        list[VerificationEvidence]
            Evidence records from India Code, each carrying a trust
            weight of 0.9.
        """
        from src.models.verification import VerificationEvidence

        evidence: list[VerificationEvidence] = []

        try:
            results = await self._indiacode.search_acts(
                query=scheme_name,
                ministry=ministry,
            )

            for record in results:
                evidence.append(
                    VerificationEvidence(
                        source="india_code",
                        source_url=record.get("url", ""),
                        document_type="legislation",
                        document_id=record.get("act_id", ""),
                        title=record.get("title", "India Code"),
                        document_date=record.get("date"),
                        trust_weight=_SOURCE_WEIGHTS["india_code"],
                        excerpt=record.get("snippet", "")[:500],
                        raw_metadata={
                            "status_indication": record.get("status", "active"),
                            **(record.get("metadata") or {}),
                        },
                    )
                )

            logger.info(
                "verification.indiacode_checked",
                scheme_name=scheme_name,
                evidence_count=len(evidence),
            )

        except Exception as exc:
            logger.error(
                "verification.indiacode_check_failed",
                scheme_name=scheme_name,
                error=str(exc),
            )
            raise

        return evidence

    async def _check_myscheme(
        self,
        scheme_name: str,
    ) -> list[VerificationEvidence]:
        """Check MyScheme.gov.in for the scheme listing.

        MyScheme is the Government of India's official scheme discovery
        portal.  A listing here confirms the scheme is recognised by the
        government, though it carries less weight than gazette or
        legislative sources.

        Parameters
        ----------
        scheme_name:
            Name of the scheme to search for.

        Returns
        -------
        list[VerificationEvidence]
            Evidence records from MyScheme.gov.in, each carrying a trust
            weight of 0.7.
        """
        from src.models.verification import VerificationEvidence

        evidence: list[VerificationEvidence] = []

        try:
            results = await self._myscheme.fetch_all_schemes(max_concurrent=1)

            # Search for matching schemes by name similarity
            scheme_name_lower = scheme_name.lower()
            for record in results:
                record_name = record.get("name", "").lower()
                if (
                    scheme_name_lower in record_name
                    or record_name in scheme_name_lower
                    or self._name_token_overlap(scheme_name, record.get("name", "")) >= 0.6
                ):
                    evidence.append(
                        VerificationEvidence(
                            source="myscheme_gov",
                            source_url=record.get("website", ""),
                            document_type="scheme_portal",
                            document_id=record.get("scheme_id", ""),
                            title=record.get("name", "MyScheme.gov.in"),
                            document_date=record.get("last_updated"),
                            trust_weight=_SOURCE_WEIGHTS["myscheme_gov"],
                            excerpt=record.get("description", "")[:500],
                            raw_metadata={
                                "status_indication": "active",
                                "ministry": record.get("ministry", ""),
                                "category": record.get("category", ""),
                            },
                        )
                    )

            logger.info(
                "verification.myscheme_checked",
                scheme_name=scheme_name,
                evidence_count=len(evidence),
            )

        except Exception as exc:
            logger.error(
                "verification.myscheme_check_failed",
                scheme_name=scheme_name,
                error=str(exc),
            )
            raise

        return evidence

    async def _check_datagov(
        self,
        scheme_name: str,
    ) -> list[VerificationEvidence]:
        """Check data.gov.in datasets for references to the scheme.

        The Open Government Data platform carries expenditure and
        beneficiary data.  A dataset mentioning the scheme provides
        supplementary evidence that the scheme is operational, though
        at a lower trust weight.

        Parameters
        ----------
        scheme_name:
            Name of the scheme to search for.

        Returns
        -------
        list[VerificationEvidence]
            Evidence records from data.gov.in, each carrying a trust
            weight of 0.5.
        """
        from src.models.verification import VerificationEvidence

        evidence: list[VerificationEvidence] = []

        try:
            datasets = await self._datagov.search_datasets(
                query=scheme_name,
                limit=10,
            )

            for record in datasets:
                title = (
                    record.get("title")
                    or record.get("name")
                    or record.get("resource_name")
                    or ""
                )
                # Only include datasets that appear relevant to the scheme
                if self._name_token_overlap(scheme_name, title) >= 0.4:
                    evidence.append(
                        VerificationEvidence(
                            source="data_gov_in",
                            source_url=record.get("url", ""),
                            document_type="government_dataset",
                            document_id=record.get("resource_id", record.get("id", "")),
                            title=title or "data.gov.in",
                            document_date=record.get("updated_date"),
                            trust_weight=_SOURCE_WEIGHTS["data_gov_in"],
                            excerpt=record.get("description", "")[:500],
                            raw_metadata={
                                "status_indication": "active",
                                "org": record.get("org", ""),
                                "sector": record.get("sector", ""),
                            },
                        )
                    )

            logger.info(
                "verification.datagov_checked",
                scheme_name=scheme_name,
                evidence_count=len(evidence),
            )

        except Exception as exc:
            logger.error(
                "verification.datagov_check_failed",
                scheme_name=scheme_name,
                error=str(exc),
            )
            raise

        return evidence

    # ------------------------------------------------------------------
    # Trust score computation
    # ------------------------------------------------------------------

    def _compute_trust_score(
        self,
        evidences: list[VerificationEvidence],
    ) -> tuple[float, VerificationStatus]:
        """Compute the overall trust score and verification status.

        Algorithm
        ---------
        1. Sum all evidence ``trust_weight`` values (capped per source
           so that multiple results from the same source do not inflate
           the score).
        2. Normalise to the ``[0, 1]`` range by dividing by
           ``_MAX_TRUST_SCORE`` (3.95).
        3. Check for revocation notices (overrides everything).
        4. Check for conflicting evidence (disputed).
        5. Apply status thresholds.

        Parameters
        ----------
        evidences:
            All collected :class:`VerificationEvidence` records.

        Returns
        -------
        tuple[float, VerificationStatus]
            A ``(trust_score, status)`` pair where ``trust_score`` is in
            ``[0.0, 1.0]`` and ``status`` is the final determination.
        """
        if not evidences:
            return 0.0, "unverified"

        # -- Check for revocation first -------------------------------------
        for ev in evidences:
            status_indication = (ev.raw_metadata.get("status_indication") or "").lower()
            if ev.source == "gazette_of_india" and status_indication in (
                "revoked",
                "repealed",
                "superseded",
                "cancelled",
            ):
                logger.info(
                    "verification.revocation_detected",
                    source=ev.source,
                    document_id=ev.document_id,
                )
                return 0.0, "revoked"

        # -- Cap contribution per source ------------------------------------
        source_best_weight: dict[str, float] = {}
        for ev in evidences:
            current_best = source_best_weight.get(ev.source, 0.0)
            if ev.trust_weight > current_best:
                source_best_weight[ev.source] = ev.trust_weight

        weighted_sum = sum(source_best_weight.values())
        trust_score = weighted_sum / _MAX_TRUST_SCORE

        # Clamp to [0, 1]
        trust_score = max(0.0, min(1.0, trust_score))

        # -- Count confirming sources ---------------------------------------
        confirming_sources = len(source_best_weight)

        # -- Detect conflicting evidence ------------------------------------
        conflicts = self._detect_conflicts(evidences)
        if conflicts:
            return trust_score, "disputed"

        # -- Apply status thresholds ----------------------------------------
        if (
            trust_score >= _VERIFIED_THRESHOLD
            and confirming_sources >= _VERIFIED_MIN_SOURCES
        ):
            return trust_score, "verified"

        if (
            trust_score >= _PARTIAL_THRESHOLD
            or confirming_sources >= _PARTIAL_MIN_SOURCES
        ):
            return trust_score, "partially_verified"

        return trust_score, "unverified"

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _detect_conflicts(
        self,
        evidences: list[VerificationEvidence],
    ) -> list[str]:
        """Detect contradictory evidence across sources.

        Looks for cases where one source indicates a scheme is active
        while another indicates it has been revoked, repealed, or
        superseded.  Also detects date-range conflicts (e.g. a source
        says the scheme was created after another says it was revoked).

        Parameters
        ----------
        evidences:
            All collected evidence records.

        Returns
        -------
        list[str]
            Human-readable descriptions of each detected conflict.
            Returns an empty list when no conflicts are found.
        """
        conflicts: list[str] = []

        if not evidences:
            return conflicts

        # Partition evidence by status indication
        active_sources: list[VerificationEvidence] = []
        revoked_sources: list[VerificationEvidence] = []

        revocation_statuses = {"revoked", "repealed", "superseded", "cancelled", "inactive"}

        for ev in evidences:
            status = (ev.raw_metadata.get("status_indication") or "").lower()
            if status in revocation_statuses:
                revoked_sources.append(ev)
            elif status in ("active", "enacted", "in_force", "operational"):
                active_sources.append(ev)

        # Conflict: some sources say active, others say revoked
        if active_sources and revoked_sources:
            active_names = [
                f"{ev.title} ({ev.document_id})"
                for ev in active_sources
            ]
            revoked_names = [
                f"{ev.title} ({ev.document_id})"
                for ev in revoked_sources
            ]
            conflicts.append(
                f"Status conflict: {', '.join(active_names)} indicate active"
                f" but {', '.join(revoked_names)} indicate revoked/repealed"
            )

        # Conflict: same source returns multiple records with different
        # status indications
        source_statuses: dict[str, set[str]] = {}
        for ev in evidences:
            status = (ev.raw_metadata.get("status_indication") or "").lower()
            if status:
                source_statuses.setdefault(ev.source, set()).add(status)

        for source, statuses in source_statuses.items():
            active_set = statuses & {"active", "enacted", "in_force", "operational"}
            revoked_set = statuses & revocation_statuses
            if active_set and revoked_set:
                conflicts.append(
                    f"Internal conflict in {source}: found both"
                    f" {', '.join(sorted(active_set))} and"
                    f" {', '.join(sorted(revoked_set))} indications"
                )

        return conflicts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _name_token_overlap(name_a: str, name_b: str) -> float:
        """Compute normalised token overlap between two scheme names.

        Strips common noise words (articles, generic government terms)
        and computes Jaccard similarity over the remaining tokens.

        Parameters
        ----------
        name_a:
            First name string.
        name_b:
            Second name string.

        Returns
        -------
        float
            Similarity in ``[0.0, 1.0]``.
        """
        noise = {
            "the", "of", "for", "and", "in", "to", "a", "an", "is",
            "scheme", "yojana", "yojna", "mission", "abhiyan", "pradhan",
            "mantri", "pm", "national", "central", "government", "india",
        }

        def _tokenise(name: str) -> set[str]:
            tokens = set()
            for token in name.lower().split():
                token = token.strip("()[]{}.,;:-\"'")
                if token and token not in noise and len(token) > 1:
                    tokens.add(token)
            return tokens

        tokens_a = _tokenise(name_a)
        tokens_b = _tokenise(name_b)

        if not tokens_a or not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        return len(intersection) / len(union) if union else 0.0
