"""Tests for the ingestion pipeline components.

Covers MySchemeClient parsing, pipeline deduplication/validation/checksums,
DataGovClient parameter building, and IngestionResult serialization.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.services.ingestion.myscheme_client import MySchemeClient, _map_category
from src.services.ingestion.pipeline import (
    IngestionResult,
    SchemeIngestionPipeline,
    _name_similarity,
    _normalise_name,
)
from src.services.ingestion.data_gov_client import DataGovClient


# ---------------------------------------------------------------------------
# Mock cache for tests
# ---------------------------------------------------------------------------


class FakeCache:
    """Minimal in-memory cache matching CacheManager interface."""

    def __init__(self):
        self._store: dict[str, object] = {}

    async def get(self, key: str) -> object | None:
        return self._store.get(key)

    async def set(self, key: str, value: object, ttl_seconds: int = 0) -> None:
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


# ---------------------------------------------------------------------------
# IngestionResult
# ---------------------------------------------------------------------------


class TestIngestionResult:
    def test_defaults(self):
        r = IngestionResult()
        assert r.total_fetched == 0
        assert r.new_schemes == 0
        assert r.updated_schemes == 0
        assert r.failed_schemes == 0
        assert r.sources_used == []
        assert r.errors == []
        assert r.duration_seconds == 0.0
        assert r.timestamp is not None

    def test_to_dict(self):
        r = IngestionResult(
            total_fetched=100,
            new_schemes=10,
            updated_schemes=5,
            failed_schemes=2,
            sources_used=["myscheme.gov.in"],
            duration_seconds=12.345,
        )
        d = r.to_dict()
        assert d["total_fetched"] == 100
        assert d["new_schemes"] == 10
        assert d["updated_schemes"] == 5
        assert d["failed_schemes"] == 2
        assert d["sources_used"] == ["myscheme.gov.in"]
        assert d["duration_seconds"] == 12.35  # Rounded
        assert "timestamp" in d
        assert "errors" in d


# ---------------------------------------------------------------------------
# Name normalization and similarity
# ---------------------------------------------------------------------------


class TestNameNormalization:
    def test_normalise_removes_noise_words(self):
        tokens = _normalise_name("Pradhan Mantri Kisan Samman Nidhi Yojana")
        assert "pradhan" not in tokens
        assert "mantri" not in tokens
        assert "yojana" not in tokens
        assert "kisan" in tokens
        assert "samman" in tokens
        assert "nidhi" in tokens

    def test_normalise_strips_punctuation(self):
        tokens = _normalise_name("PM-KISAN (Samman Nidhi)")
        assert "kisan" in tokens or "pm-kisan" in tokens
        assert "samman" in tokens
        assert "nidhi" in tokens

    def test_normalise_empty_string(self):
        tokens = _normalise_name("")
        assert tokens == set()

    def test_normalise_only_noise_words(self):
        tokens = _normalise_name("the scheme for India")
        assert len(tokens) == 0

    def test_similarity_identical(self):
        sim = _name_similarity("PM KISAN Samman Nidhi", "PM KISAN Samman Nidhi")
        assert sim == 1.0

    def test_similarity_high_overlap(self):
        sim = _name_similarity(
            "Pradhan Mantri Kisan Samman Nidhi",
            "PM-KISAN Samman Nidhi Yojana",
        )
        assert sim >= 0.5  # "kisan", "samman", "nidhi" overlap

    def test_similarity_low_overlap(self):
        sim = _name_similarity(
            "Ayushman Bharat PMJAY",
            "Sukanya Samriddhi Yojana",
        )
        assert sim < 0.5

    def test_similarity_empty(self):
        assert _name_similarity("", "test") == 0.0
        assert _name_similarity("test", "") == 0.0
        assert _name_similarity("", "") == 0.0


# ---------------------------------------------------------------------------
# MySchemeClient -- __NEXT_DATA__ parsing
# ---------------------------------------------------------------------------


class TestMySchemeClientParsing:
    @pytest.fixture
    def client(self):
        cache = FakeCache()
        return MySchemeClient(cache=cache, rate_limit_delay=0)

    def test_parse_next_data_valid(self, client):
        html = '''
        <html>
        <head></head>
        <body>
        <script id="__NEXT_DATA__" type="application/json">
        {"buildId": "abc123", "props": {"pageProps": {"data": []}}}
        </script>
        </body>
        </html>
        '''
        result = client._parse_next_data(html)
        assert result is not None
        assert result["buildId"] == "abc123"

    def test_parse_next_data_missing(self, client):
        html = "<html><body>No next data here</body></html>"
        result = client._parse_next_data(html)
        assert result is None

    def test_parse_next_data_invalid_json(self, client):
        html = '''
        <script id="__NEXT_DATA__" type="application/json">
        {invalid json here
        </script>
        '''
        result = client._parse_next_data(html)
        assert result is None

    def test_normalize_scheme_basic(self, client):
        raw = {
            "schemeData": {
                "id": 1234,
                "schemeName": "Test Scheme",
                "schemeDescription": "A long enough description for validation testing purposes here.",
                "benefits": "Financial help",
                "applicationProcess": "Apply at CSC",
                "documentsRequired": ["Aadhaar Card"],
                "ministry": "Test Ministry",
                "category": "agriculture",
            }
        }
        result = client._normalize_scheme(raw, slug="test-scheme")
        assert result["scheme_id"] == "myscheme-1234"
        assert result["name"] == "Test Scheme"
        assert result["category"] == "agriculture"
        assert result["ministry"] == "Test Ministry"
        assert result["source"] == "myscheme.gov.in"
        assert result["source_slug"] == "test-scheme"

    def test_normalize_scheme_string_id(self, client):
        raw = {
            "schemeData": {
                "id": "pm-kisan",
                "schemeName": "PM KISAN",
                "schemeDescription": "Farmer support scheme with regular payments for farming families.",
                "benefits": "Rs 6000/year",
            }
        }
        result = client._normalize_scheme(raw, slug="pm-kisan")
        assert result["scheme_id"] == "pm-kisan"  # Not prefixed

    def test_normalize_scheme_html_stripped(self, client):
        raw = {
            "schemeData": {
                "id": "test",
                "schemeName": "Test",
                "schemeDescription": "<p>Hello <b>World</b></p> with enough text for testing.",
                "benefits": "<ul><li>Benefit 1</li></ul>",
            }
        }
        result = client._normalize_scheme(raw, slug="test")
        assert "<p>" not in result["description"]
        assert "<b>" not in result["description"]
        assert "<ul>" not in result["benefits"]

    def test_normalize_scheme_list_benefits(self, client):
        raw = {
            "schemeData": {
                "id": "test",
                "schemeName": "Test",
                "schemeDescription": "Description for the test scheme with enough characters.",
                "benefits": ["Benefit A", "Benefit B"],
            }
        }
        result = client._normalize_scheme(raw, slug="test")
        assert "Benefit A" in result["benefits"]
        assert "Benefit B" in result["benefits"]

    def test_normalize_scheme_state_central(self, client):
        """'All India' state should be normalized to None (central scheme)."""
        raw = {
            "schemeData": {
                "id": "test",
                "schemeName": "Test",
                "schemeDescription": "A central scheme available for all states of India.",
                "state": "All India",
            }
        }
        result = client._normalize_scheme(raw, slug="test")
        assert result["state"] is None

    def test_normalize_scheme_slug_as_website_fallback(self, client):
        raw = {
            "schemeData": {
                "id": "test",
                "schemeName": "Test",
                "schemeDescription": "A test scheme for verifying URL generation fallback behavior.",
            }
        }
        result = client._normalize_scheme(raw, slug="my-test")
        assert result["website"] == "https://www.myscheme.gov.in/schemes/my-test"


# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------


class TestCategoryMapping:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Agriculture,Rural & Environment", "agriculture"),
            ("Health & Wellness", "health"),
            ("Education & Learning", "education"),
            ("Housing & Shelter", "housing"),
            ("Social Welfare & Empowerment", "social_security"),
            ("Women and Child", "women_child"),
            ("", "other"),
            ("Some Unknown Category", "other"),
        ],
    )
    def test_map_category(self, raw, expected):
        assert _map_category(raw) == expected


# ---------------------------------------------------------------------------
# Pipeline -- validation
# ---------------------------------------------------------------------------


class TestPipelineValidation:
    @pytest.fixture
    def pipeline(self):
        cache = FakeCache()
        # Create minimal mock clients
        myscheme = MySchemeClient(cache=cache, rate_limit_delay=0)
        datagov = DataGovClient(cache=cache, api_key="test")
        return SchemeIngestionPipeline(
            myscheme=myscheme,
            datagov=datagov,
            cache=cache,
        )

    @pytest.mark.asyncio
    async def test_validate_valid_scheme(self, pipeline):
        scheme = {
            "scheme_id": "test-1",
            "name": "Test Scheme",
            "description": "A valid description that is certainly longer than fifty characters for testing.",
            "category": "agriculture",
            "ministry": "Test Ministry",
        }
        result = await pipeline._validate_scheme(scheme)
        assert result is not None
        assert result["name"] == "Test Scheme"

    @pytest.mark.asyncio
    async def test_validate_rejects_no_name(self, pipeline):
        scheme = {"description": "Some description that is long enough", "scheme_id": "no-name"}
        result = await pipeline._validate_scheme(scheme)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_rejects_no_description(self, pipeline):
        scheme = {"name": "Test", "scheme_id": "no-desc"}
        result = await pipeline._validate_scheme(scheme)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_rejects_short_description(self, pipeline):
        scheme = {"name": "Test", "description": "Too short", "scheme_id": "short"}
        result = await pipeline._validate_scheme(scheme)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_rejects_placeholder(self, pipeline):
        scheme = {
            "name": "Test",
            "description": "Lorem ipsum dolor sit amet, this is a long enough placeholder text.",
            "scheme_id": "placeholder",
        }
        result = await pipeline._validate_scheme(scheme)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_generates_auto_id(self, pipeline):
        scheme = {
            "name": "New Auto ID Scheme",
            "description": "A description for an auto-generated ID scheme that is long enough.",
        }
        result = await pipeline._validate_scheme(scheme)
        assert result is not None
        assert result["scheme_id"].startswith("auto-")

    @pytest.mark.asyncio
    async def test_validate_adds_defaults(self, pipeline):
        scheme = {
            "scheme_id": "test-defaults",
            "name": "Defaults Test",
            "description": "A valid description that is definitely longer than fifty characters for testing.",
        }
        result = await pipeline._validate_scheme(scheme)
        assert result is not None
        assert result["category"] == "other"
        assert result["ministry"] == "Government of India"
        assert result["documents_required"] == []


# ---------------------------------------------------------------------------
# Pipeline -- merge and deduplication
# ---------------------------------------------------------------------------


class TestPipelineMergeDedup:
    @pytest.fixture
    def pipeline(self):
        cache = FakeCache()
        myscheme = MySchemeClient(cache=cache, rate_limit_delay=0)
        datagov = DataGovClient(cache=cache, api_key="test")
        return SchemeIngestionPipeline(
            myscheme=myscheme,
            datagov=datagov,
            cache=cache,
        )

    @pytest.mark.asyncio
    async def test_dedup_by_id(self, pipeline):
        source1 = [
            {"scheme_id": "s1", "name": "Scheme One", "last_updated": "2025-01-01"},
        ]
        source2 = [
            {"scheme_id": "s1", "name": "Scheme One Updated", "last_updated": "2025-06-01"},
        ]
        merged = await pipeline._merge_and_deduplicate([source1, source2])
        assert len(merged) == 1

    @pytest.mark.asyncio
    async def test_dedup_prefers_more_recent(self, pipeline):
        source1 = [
            {
                "scheme_id": "s1",
                "name": "Scheme",
                "description": "Old description",
                "last_updated": "2025-01-01",
            },
        ]
        source2 = [
            {
                "scheme_id": "s1",
                "name": "Scheme",
                "description": "Newer and much longer description",
                "last_updated": "2025-06-01",
            },
        ]
        merged = await pipeline._merge_and_deduplicate([source1, source2])
        assert len(merged) == 1
        # The longer description should be preferred
        assert "longer" in merged[0]["description"]

    @pytest.mark.asyncio
    async def test_dedup_different_schemes_preserved(self, pipeline):
        source1 = [{"scheme_id": "s1", "name": "One"}]
        source2 = [{"scheme_id": "s2", "name": "Two"}]
        merged = await pipeline._merge_and_deduplicate([source1, source2])
        assert len(merged) == 2

    @pytest.mark.asyncio
    async def test_dedup_empty_sources(self, pipeline):
        merged = await pipeline._merge_and_deduplicate([[], []])
        assert merged == []

    @pytest.mark.asyncio
    async def test_dedup_single_source(self, pipeline):
        source = [
            {"scheme_id": "s1", "name": "One"},
            {"scheme_id": "s2", "name": "Two"},
        ]
        merged = await pipeline._merge_and_deduplicate([source])
        assert len(merged) == 2


# ---------------------------------------------------------------------------
# Pipeline -- checksums
# ---------------------------------------------------------------------------


class TestPipelineChecksums:
    @pytest.fixture
    def pipeline(self):
        cache = FakeCache()
        myscheme = MySchemeClient(cache=cache, rate_limit_delay=0)
        datagov = DataGovClient(cache=cache, api_key="test")
        return SchemeIngestionPipeline(
            myscheme=myscheme,
            datagov=datagov,
            cache=cache,
        )

    def test_checksum_deterministic(self, pipeline):
        scheme = {"name": "Test", "description": "Desc", "benefits": "Ben"}
        assert pipeline._compute_checksum(scheme) == pipeline._compute_checksum(scheme)

    def test_checksum_different_for_different_content(self, pipeline):
        s1 = {"name": "Scheme A", "description": "Description A"}
        s2 = {"name": "Scheme B", "description": "Description B"}
        assert pipeline._compute_checksum(s1) != pipeline._compute_checksum(s2)

    def test_checksum_ignores_metadata(self, pipeline):
        """Timestamps and popularity should not affect checksums."""
        s1 = {"name": "Test", "description": "Desc", "last_updated": "2025-01-01", "popularity_score": 0.5}
        s2 = {"name": "Test", "description": "Desc", "last_updated": "2025-06-01", "popularity_score": 0.9}
        assert pipeline._compute_checksum(s1) == pipeline._compute_checksum(s2)

    def test_checksum_is_hex_string(self, pipeline):
        scheme = {"name": "T"}
        cs = pipeline._compute_checksum(scheme)
        assert isinstance(cs, str)
        assert len(cs) == 16
        int(cs, 16)  # Should not raise


# ---------------------------------------------------------------------------
# DataGovClient -- parameter building
# ---------------------------------------------------------------------------


class TestDataGovClient:
    def test_build_params_with_api_key(self):
        cache = FakeCache()
        client = DataGovClient(cache=cache, api_key="my-key")
        params = client._build_params(format="json", limit=100)
        assert params["api-key"] == "my-key"
        assert params["format"] == "json"
        assert params["limit"] == "100"

    def test_build_params_without_api_key(self):
        cache = FakeCache()
        client = DataGovClient(cache=cache, api_key=None)
        params = client._build_params(format="json")
        assert "api-key" not in params
        assert params["format"] == "json"

    def test_build_params_skips_none(self):
        cache = FakeCache()
        client = DataGovClient(cache=cache)
        params = client._build_params(format="json", filter=None)
        assert "filter" not in params
