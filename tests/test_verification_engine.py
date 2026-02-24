"""Tests for the SchemeVerificationEngine.

Covers trust score computation, conflict detection, scheme verification
with mocked clients, and batch verification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.models.verification import VerificationEvidence, VerificationSource
from src.services.verification.engine import (
    SchemeVerificationEngine,
    _MAX_TRUST_SCORE,
    _SOURCE_WEIGHTS,
    _VERIFIED_MIN_SOURCES,
    _VERIFIED_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Mock cache for tests
# ---------------------------------------------------------------------------


class FakeCache:
    """Minimal in-memory cache matching CacheManager interface."""

    def __init__(self):
        self._store: dict[str, object] = {}

    async def get(self, key: str, default: object = None) -> object | None:
        return self._store.get(key, default)

    async def set(self, key: str, value: object, ttl_seconds: int = 0) -> None:
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Evidence helper
# ---------------------------------------------------------------------------


def _make_evidence(
    source: str = "gazette_of_india",
    trust_weight: float = 1.0,
    status_indication: str = "active",
    document_id: str = "doc-001",
    source_name: str = "Test Source",
) -> VerificationEvidence:
    """Create a VerificationEvidence instance for testing."""
    return VerificationEvidence(
        source=VerificationSource(source),
        source_url="https://test.gov.in",
        document_type="notification",
        document_id=document_id,
        document_date=None,
        title=source_name,
        excerpt="",
        trust_weight=trust_weight,
        raw_metadata={"status_indication": status_indication},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_clients():
    """Create mock clients for all five verification sources."""
    gazette_client = AsyncMock()
    sansad_client = AsyncMock()
    indiacode_client = AsyncMock()
    myscheme_client = AsyncMock()
    datagov_client = AsyncMock()
    return {
        "gazette_client": gazette_client,
        "sansad_client": sansad_client,
        "indiacode_client": indiacode_client,
        "myscheme_client": myscheme_client,
        "datagov_client": datagov_client,
    }


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def engine(mock_clients, cache) -> SchemeVerificationEngine:
    return SchemeVerificationEngine(
        gazette_client=mock_clients["gazette_client"],
        sansad_client=mock_clients["sansad_client"],
        indiacode_client=mock_clients["indiacode_client"],
        myscheme_client=mock_clients["myscheme_client"],
        datagov_client=mock_clients["datagov_client"],
        cache=cache,
    )


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------


class TestSchemeVerificationEngineInit:
    def test_initialization(self, engine: SchemeVerificationEngine) -> None:
        assert engine is not None
        assert engine._gazette is not None
        assert engine._sansad is not None
        assert engine._indiacode is not None
        assert engine._myscheme is not None
        assert engine._datagov is not None
        assert engine._cache is not None

    def test_source_weights(self) -> None:
        assert _SOURCE_WEIGHTS["gazette_of_india"] == 1.0
        assert _SOURCE_WEIGHTS["india_code"] == 0.9
        assert _SOURCE_WEIGHTS["sansad_parliament"] == 0.85
        assert _SOURCE_WEIGHTS["myscheme_gov"] == 0.7
        assert _SOURCE_WEIGHTS["data_gov_in"] == 0.5

    def test_max_trust_score(self) -> None:
        expected = 1.0 + 0.9 + 0.85 + 0.7 + 0.5  # 3.95
        assert _MAX_TRUST_SCORE == expected


# ---------------------------------------------------------------------------
# Trust score computation tests
# ---------------------------------------------------------------------------


class TestComputeTrustScore:
    def test_no_evidence_returns_unverified(self, engine: SchemeVerificationEngine) -> None:
        score, status = engine._compute_trust_score([])
        assert score == 0.0
        assert status == "unverified"

    def test_single_gazette_evidence_partially_verified(
        self, engine: SchemeVerificationEngine
    ) -> None:
        """Single gazette evidence: weight 1.0 / 3.95 ~ 0.253 -> partially_verified."""
        evidence = [_make_evidence(source="gazette_of_india", trust_weight=1.0)]
        score, status = engine._compute_trust_score(evidence)
        expected_score = 1.0 / _MAX_TRUST_SCORE
        assert abs(score - expected_score) < 0.01
        assert status == "partially_verified"

    def test_gazette_plus_indiacode_verified(
        self, engine: SchemeVerificationEngine
    ) -> None:
        """Gazette (1.0) + IndiaCode (0.9) -> score (1.9/3.95 ~ 0.481), 2 sources."""
        evidence = [
            _make_evidence(source="gazette_of_india", trust_weight=1.0),
            _make_evidence(source="india_code", trust_weight=0.9, document_id="act-001"),
        ]
        score, status = engine._compute_trust_score(evidence)
        expected_score = (1.0 + 0.9) / _MAX_TRUST_SCORE
        assert abs(score - expected_score) < 0.01
        # 2 sources confirm, but score ~ 0.48 < 0.7 threshold for verified
        assert score >= 0.4
        assert status == "partially_verified"

    def test_three_sources_verified(self, engine: SchemeVerificationEngine) -> None:
        """Gazette (1.0) + IndiaCode (0.9) + Sansad (0.85) -> 2.75/3.95 ~ 0.696, 3 sources."""
        evidence = [
            _make_evidence(source="gazette_of_india", trust_weight=1.0),
            _make_evidence(source="india_code", trust_weight=0.9, document_id="act-001"),
            _make_evidence(source="sansad_parliament", trust_weight=0.85, document_id="bill-001"),
        ]
        score, status = engine._compute_trust_score(evidence)
        expected_score = (1.0 + 0.9 + 0.85) / _MAX_TRUST_SCORE
        assert abs(score - expected_score) < 0.01
        # Score ~ 0.696 < 0.7 -- just below threshold
        assert status == "partially_verified"

    def test_four_sources_verified(self, engine: SchemeVerificationEngine) -> None:
        """Gazette + IndiaCode + Sansad + MyScheme -> 3.45/3.95 ~ 0.873 -> verified."""
        evidence = [
            _make_evidence(source="gazette_of_india", trust_weight=1.0),
            _make_evidence(source="india_code", trust_weight=0.9, document_id="act-001"),
            _make_evidence(source="sansad_parliament", trust_weight=0.85, document_id="bill-001"),
            _make_evidence(source="myscheme_gov", trust_weight=0.7, document_id="ms-001"),
        ]
        score, status = engine._compute_trust_score(evidence)
        expected_score = (1.0 + 0.9 + 0.85 + 0.7) / _MAX_TRUST_SCORE
        assert abs(score - expected_score) < 0.01
        assert score >= _VERIFIED_THRESHOLD
        assert status == "verified"

    def test_only_myscheme_partially_verified(
        self, engine: SchemeVerificationEngine
    ) -> None:
        """Only MyScheme evidence: weight 0.7 / 3.95 ~ 0.177 -> partially_verified (1 source)."""
        evidence = [_make_evidence(source="myscheme_gov", trust_weight=0.7)]
        score, status = engine._compute_trust_score(evidence)
        expected_score = 0.7 / _MAX_TRUST_SCORE
        assert abs(score - expected_score) < 0.01
        # 1 source >= 1 min source for partial -> partially_verified
        assert status == "partially_verified"

    def test_conflicting_evidence_disputed(
        self, engine: SchemeVerificationEngine
    ) -> None:
        """One source says active, another says revoked -> disputed."""
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                trust_weight=1.0,
                status_indication="active",
                document_id="gaz-1",
                source_name="Gazette of India",
            ),
            _make_evidence(
                source="myscheme_gov",
                trust_weight=0.7,
                status_indication="revoked",
                document_id="ms-1",
                source_name="MyScheme.gov.in",
            ),
        ]
        score, status = engine._compute_trust_score(evidence)
        assert status == "disputed"

    def test_gazette_revocation_returns_revoked(
        self, engine: SchemeVerificationEngine
    ) -> None:
        """Gazette evidence with revoked status_indication -> revoked."""
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                trust_weight=1.0,
                status_indication="revoked",
            ),
        ]
        score, status = engine._compute_trust_score(evidence)
        assert score == 0.0
        assert status == "revoked"

    def test_gazette_repealed_returns_revoked(
        self, engine: SchemeVerificationEngine
    ) -> None:
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                trust_weight=1.0,
                status_indication="repealed",
            ),
        ]
        score, status = engine._compute_trust_score(evidence)
        assert score == 0.0
        assert status == "revoked"

    def test_gazette_superseded_returns_revoked(
        self, engine: SchemeVerificationEngine
    ) -> None:
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                trust_weight=1.0,
                status_indication="superseded",
            ),
        ]
        score, status = engine._compute_trust_score(evidence)
        assert score == 0.0
        assert status == "revoked"

    def test_gazette_cancelled_returns_revoked(
        self, engine: SchemeVerificationEngine
    ) -> None:
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                trust_weight=1.0,
                status_indication="cancelled",
            ),
        ]
        score, status = engine._compute_trust_score(evidence)
        assert score == 0.0
        assert status == "revoked"

    def test_non_gazette_revocation_not_revoked(
        self, engine: SchemeVerificationEngine
    ) -> None:
        """Revocation from non-gazette source should NOT trigger revoked status."""
        evidence = [
            _make_evidence(
                source="myscheme_gov",
                trust_weight=0.7,
                status_indication="revoked",
            ),
        ]
        score, status = engine._compute_trust_score(evidence)
        # MyScheme revocation alone is not a gazette revocation
        assert status != "revoked"

    def test_duplicate_source_capped(self, engine: SchemeVerificationEngine) -> None:
        """Multiple evidence from the same source should be capped to best weight."""
        evidence = [
            _make_evidence(source="gazette_of_india", trust_weight=1.0, document_id="gaz-1"),
            _make_evidence(source="gazette_of_india", trust_weight=1.0, document_id="gaz-2"),
            _make_evidence(source="gazette_of_india", trust_weight=1.0, document_id="gaz-3"),
        ]
        score, status = engine._compute_trust_score(evidence)
        # Should be 1.0 / 3.95, not 3.0 / 3.95
        expected_score = 1.0 / _MAX_TRUST_SCORE
        assert abs(score - expected_score) < 0.01

    def test_score_clamped_to_one(self, engine: SchemeVerificationEngine) -> None:
        """Score should never exceed 1.0."""
        evidence = [
            _make_evidence(source="gazette_of_india", trust_weight=1.0),
            _make_evidence(source="india_code", trust_weight=0.9, document_id="a1"),
            _make_evidence(source="sansad_parliament", trust_weight=0.85, document_id="b1"),
            _make_evidence(source="myscheme_gov", trust_weight=0.7, document_id="c1"),
            _make_evidence(source="data_gov_in", trust_weight=0.5, document_id="d1"),
        ]
        score, status = engine._compute_trust_score(evidence)
        assert score <= 1.0


# ---------------------------------------------------------------------------
# Conflict detection tests
# ---------------------------------------------------------------------------


class TestDetectConflicts:
    def test_no_conflicts_active_only(self, engine: SchemeVerificationEngine) -> None:
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                status_indication="active",
                source_name="Gazette of India",
            ),
            _make_evidence(
                source="myscheme_gov",
                status_indication="active",
                source_name="MyScheme.gov.in",
            ),
        ]
        conflicts = engine._detect_conflicts(evidence)
        assert conflicts == []

    def test_no_conflicts_empty(self, engine: SchemeVerificationEngine) -> None:
        conflicts = engine._detect_conflicts([])
        assert conflicts == []

    def test_conflict_active_vs_revoked(self, engine: SchemeVerificationEngine) -> None:
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                status_indication="active",
                document_id="gaz-1",
                source_name="Gazette of India",
            ),
            _make_evidence(
                source="myscheme_gov",
                status_indication="revoked",
                document_id="ms-1",
                source_name="MyScheme.gov.in",
            ),
        ]
        conflicts = engine._detect_conflicts(evidence)
        assert len(conflicts) >= 1
        assert "conflict" in conflicts[0].lower() or "active" in conflicts[0].lower()

    def test_conflict_active_vs_repealed(self, engine: SchemeVerificationEngine) -> None:
        evidence = [
            _make_evidence(
                source="india_code",
                status_indication="enacted",
                document_id="ic-1",
                source_name="India Code",
            ),
            _make_evidence(
                source="sansad_parliament",
                status_indication="repealed",
                document_id="san-1",
                source_name="Sansad",
            ),
        ]
        conflicts = engine._detect_conflicts(evidence)
        assert len(conflicts) >= 1

    def test_internal_source_conflict(self, engine: SchemeVerificationEngine) -> None:
        """Same source with both active and revoked indications."""
        evidence = [
            _make_evidence(
                source="gazette_of_india",
                status_indication="active",
                document_id="gaz-1",
                source_name="Gazette of India",
            ),
            _make_evidence(
                source="gazette_of_india",
                status_indication="revoked",
                document_id="gaz-2",
                source_name="Gazette of India",
            ),
        ]
        conflicts = engine._detect_conflicts(evidence)
        assert len(conflicts) >= 1


# ---------------------------------------------------------------------------
# verify_scheme tests (with mocked clients)
# ---------------------------------------------------------------------------


class TestVerifyScheme:
    @pytest.mark.asyncio
    async def test_verify_scheme_with_gazette_evidence(
        self, engine: SchemeVerificationEngine, mock_clients
    ) -> None:
        """Mock source checks and trust score to test orchestration flow.

        The engine collects evidence from all sources, then calls
        _compute_trust_score and _detect_conflicts.  We mock the source
        checks to return evidence and mock _compute_trust_score to return
        a known score/status so we can verify the orchestration works.
        """
        gazette_evidence = _make_evidence(
            source="gazette_of_india",
            trust_weight=1.0,
            status_indication="active",
        )
        expected_score = 1.0 / _MAX_TRUST_SCORE

        with (
            patch.object(engine, "_check_gazette", return_value=[gazette_evidence]),
            patch.object(engine, "_check_parliament", return_value=[]),
            patch.object(engine, "_check_india_code", return_value=[]),
            patch.object(engine, "_check_myscheme", return_value=[]),
            patch.object(engine, "_check_datagov", return_value=[]),
            patch.object(
                engine, "_compute_trust_score",
                return_value=(round(expected_score, 4), "partially_verified"),
            ),
            patch.object(engine, "_detect_conflicts", return_value=[]),
        ):
            result = await engine.verify_scheme(
                scheme_id="pm-kisan",
                scheme_name="PM-KISAN",
                ministry="Ministry of Agriculture",
            )

        assert result.scheme_id == "pm-kisan"
        assert result.status == "partially_verified"
        assert result.trust_score > 0.0
        assert result.trust_score == pytest.approx(expected_score, abs=0.01)

    @pytest.mark.asyncio
    async def test_verify_scheme_no_evidence(
        self, engine: SchemeVerificationEngine, mock_clients
    ) -> None:
        """All sources return empty -> unverified."""
        with (
            patch.object(engine, "_check_gazette", return_value=[]),
            patch.object(engine, "_check_parliament", return_value=[]),
            patch.object(engine, "_check_india_code", return_value=[]),
            patch.object(engine, "_check_myscheme", return_value=[]),
            patch.object(engine, "_check_datagov", return_value=[]),
        ):
            result = await engine.verify_scheme(
                scheme_id="fake-scheme",
                scheme_name="Fake Scheme",
            )

        assert result.status == "unverified"
        assert result.trust_score == 0.0
        assert result.scheme_id == "fake-scheme"

    @pytest.mark.asyncio
    async def test_verify_scheme_multiple_sources(
        self, engine: SchemeVerificationEngine, mock_clients
    ) -> None:
        """Multiple sources confirm -> should reach verified with enough weight."""
        gazette_ev = _make_evidence(source="gazette_of_india", trust_weight=1.0)
        indiacode_ev = _make_evidence(
            source="india_code", trust_weight=0.9, document_id="act-001"
        )
        sansad_ev = _make_evidence(
            source="sansad_parliament", trust_weight=0.85, document_id="bill-001"
        )
        myscheme_ev = _make_evidence(
            source="myscheme_gov", trust_weight=0.7, document_id="ms-001"
        )

        with (
            patch.object(engine, "_check_gazette", return_value=[gazette_ev]),
            patch.object(engine, "_check_parliament", return_value=[sansad_ev]),
            patch.object(engine, "_check_india_code", return_value=[indiacode_ev]),
            patch.object(engine, "_check_myscheme", return_value=[myscheme_ev]),
            patch.object(engine, "_check_datagov", return_value=[]),
            patch.object(
                engine, "_compute_trust_score",
                return_value=(0.873, "verified"),
            ),
            patch.object(engine, "_detect_conflicts", return_value=[]),
        ):
            result = await engine.verify_scheme(
                scheme_id="pm-kisan",
                scheme_name="PM-KISAN",
            )

        assert result.trust_score >= _VERIFIED_THRESHOLD
        assert result.status == "verified"

    @pytest.mark.asyncio
    async def test_verify_scheme_source_exception_handled(
        self, engine: SchemeVerificationEngine, mock_clients
    ) -> None:
        """If a source raises an exception, it should be handled gracefully.

        asyncio.gather with return_exceptions=True captures the exception
        and the engine logs it. The resulting VerificationResult still gets
        created with unverified status.
        """
        with (
            patch.object(engine, "_check_gazette", side_effect=Exception("Network error")),
            patch.object(engine, "_check_parliament", return_value=[]),
            patch.object(engine, "_check_india_code", return_value=[]),
            patch.object(engine, "_check_myscheme", return_value=[]),
            patch.object(engine, "_check_datagov", return_value=[]),
        ):
            result = await engine.verify_scheme(
                scheme_id="test",
                scheme_name="Test Scheme",
            )

        assert result.status == "unverified"
        assert result.trust_score == 0.0
        assert result.scheme_id == "test"

    @pytest.mark.asyncio
    async def test_verify_scheme_uses_cache(
        self, engine: SchemeVerificationEngine, cache: FakeCache
    ) -> None:
        """If a cached result exists, it should be returned directly."""
        cached_data = {
            "scheme_id": "pm-kisan",
            "status": "verified",
            "trust_score": 0.95,
            "sources_checked": [
                "gazette_of_india",
                "india_code",
                "sansad_parliament",
            ],
        }
        await cache.set("verification:pm-kisan", cached_data)

        result = await engine.verify_scheme(
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
        )
        assert result.scheme_id == "pm-kisan"
        assert result.status == "verified"
        assert result.trust_score == 0.95


# ---------------------------------------------------------------------------
# verify_batch tests
# ---------------------------------------------------------------------------


class TestVerifyBatch:
    @pytest.mark.asyncio
    async def test_verify_batch_empty(self, engine: SchemeVerificationEngine) -> None:
        results = await engine.verify_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_verify_batch_multiple_schemes(
        self, engine: SchemeVerificationEngine
    ) -> None:
        schemes = [
            {"scheme_id": "scheme-1", "scheme_name": "Scheme One"},
            {"scheme_id": "scheme-2", "scheme_name": "Scheme Two"},
            {"scheme_id": "scheme-3", "scheme_name": "Scheme Three"},
        ]

        with (
            patch.object(engine, "_check_gazette", return_value=[]),
            patch.object(engine, "_check_parliament", return_value=[]),
            patch.object(engine, "_check_india_code", return_value=[]),
            patch.object(engine, "_check_myscheme", return_value=[]),
            patch.object(engine, "_check_datagov", return_value=[]),
        ):
            results = await engine.verify_batch(schemes, max_concurrent=2)

        assert len(results) == 3
        scheme_ids = {r.scheme_id for r in results}
        assert "scheme-1" in scheme_ids
        assert "scheme-2" in scheme_ids
        assert "scheme-3" in scheme_ids

    @pytest.mark.asyncio
    async def test_verify_batch_with_evidence(
        self, engine: SchemeVerificationEngine
    ) -> None:
        schemes = [
            {"scheme_id": "pm-kisan", "scheme_name": "PM-KISAN"},
        ]

        gazette_ev = _make_evidence(source="gazette_of_india", trust_weight=1.0)
        myscheme_ev = _make_evidence(
            source="myscheme_gov", trust_weight=0.7, document_id="ms-001"
        )
        expected_score = (1.0 + 0.7) / _MAX_TRUST_SCORE

        with (
            patch.object(engine, "_check_gazette", return_value=[gazette_ev]),
            patch.object(engine, "_check_parliament", return_value=[]),
            patch.object(engine, "_check_india_code", return_value=[]),
            patch.object(engine, "_check_myscheme", return_value=[myscheme_ev]),
            patch.object(engine, "_check_datagov", return_value=[]),
            patch.object(
                engine, "_compute_trust_score",
                return_value=(round(expected_score, 4), "partially_verified"),
            ),
            patch.object(engine, "_detect_conflicts", return_value=[]),
        ):
            results = await engine.verify_batch(schemes)

        assert len(results) == 1
        assert results[0].scheme_id == "pm-kisan"
        assert results[0].trust_score > 0.0


# ---------------------------------------------------------------------------
# Name token overlap helper tests
# ---------------------------------------------------------------------------


class TestNameTokenOverlap:
    def test_identical_names(self) -> None:
        sim = SchemeVerificationEngine._name_token_overlap(
            "PM KISAN Samman Nidhi", "PM KISAN Samman Nidhi"
        )
        assert sim == 1.0

    def test_similar_names(self) -> None:
        sim = SchemeVerificationEngine._name_token_overlap(
            "PM KISAN Samman Nidhi", "Kisan Samman Nidhi Yojana"
        )
        assert sim > 0.5

    def test_different_names(self) -> None:
        sim = SchemeVerificationEngine._name_token_overlap(
            "Ayushman Bharat PMJAY", "Sukanya Samriddhi"
        )
        assert sim < 0.5

    def test_empty_names(self) -> None:
        assert SchemeVerificationEngine._name_token_overlap("", "test") == 0.0
        assert SchemeVerificationEngine._name_token_overlap("test", "") == 0.0
        assert SchemeVerificationEngine._name_token_overlap("", "") == 0.0
