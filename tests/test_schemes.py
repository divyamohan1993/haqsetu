"""Tests for scheme loading, search, and SchemeSearchService."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.data.seed import load_schemes, _parse_scheme
from src.models.scheme import EligibilityCriteria, SchemeCategory, SchemeDocument
from src.services.cache import CacheManager
from src.services.rag import RAGService
from src.services.scheme_search import SchemeSearchService


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

_CENTRAL_SCHEMES_PATH = Path(__file__).resolve().parent.parent / "src" / "data" / "schemes" / "central_schemes.json"


@pytest.fixture
def sample_schemes() -> list[SchemeDocument]:
    """Create a small set of sample schemes for testing."""
    return [
        SchemeDocument(
            scheme_id="pm-kisan",
            name="PM-KISAN (Pradhan Mantri Kisan Samman Nidhi)",
            description="Direct income support of Rs 6,000 per year to all landholding farmer families.",
            category=SchemeCategory.AGRICULTURE,
            ministry="Ministry of Agriculture & Farmers Welfare",
            state=None,
            eligibility=EligibilityCriteria(
                occupation="farmer",
                land_holding_acres=0.01,
                custom_criteria=["Must have cultivable land"],
            ),
            benefits="Rs 6,000 per year in 3 installments",
            application_process="Apply online at pmkisan.gov.in",
            documents_required=["Aadhaar Card", "Land Records", "Bank Account Details"],
            helpline="155261",
            website="https://pmkisan.gov.in",
            last_updated="2025-12-01T00:00:00Z",
            popularity_score=0.95,
        ),
        SchemeDocument(
            scheme_id="ayushman-bharat",
            name="Ayushman Bharat - Pradhan Mantri Jan Arogya Yojana (AB-PMJAY)",
            description="Health cover of Rs 5 lakh per family per year for secondary and tertiary care hospitalisation.",
            category=SchemeCategory.HEALTH,
            ministry="Ministry of Health & Family Welfare",
            state=None,
            eligibility=EligibilityCriteria(
                is_bpl=True,
                custom_criteria=["Families identified from SECC 2011 data"],
            ),
            benefits="Health cover of Rs 5,00,000 per family per year",
            application_process="Check eligibility at mera.pmjay.gov.in",
            documents_required=["Aadhaar Card", "Ration Card"],
            helpline="14555",
            website="https://pmjay.gov.in",
            last_updated="2025-12-01T00:00:00Z",
            popularity_score=0.94,
        ),
        SchemeDocument(
            scheme_id="pm-awas",
            name="Pradhan Mantri Awas Yojana - Gramin (PMAY-G)",
            description="Housing for All scheme providing financial assistance for pucca houses in rural areas.",
            category=SchemeCategory.HOUSING,
            ministry="Ministry of Rural Development",
            state=None,
            eligibility=EligibilityCriteria(is_bpl=True),
            benefits="Rs 1,20,000 in plain areas",
            application_process="Apply through Gram Panchayat",
            documents_required=["Aadhaar Card", "Bank Account Details"],
            last_updated="2025-11-15T00:00:00Z",
            popularity_score=0.92,
        ),
        SchemeDocument(
            scheme_id="mgnrega",
            name="MGNREGA",
            description="Employment guarantee of 100 days of unskilled manual work.",
            category=SchemeCategory.EMPLOYMENT,
            ministry="Ministry of Rural Development",
            state=None,
            eligibility=EligibilityCriteria(),
            benefits="100 days guaranteed employment",
            application_process="Apply at Gram Panchayat",
            documents_required=["Aadhaar Card"],
            last_updated="2025-10-01T00:00:00Z",
            popularity_score=0.90,
        ),
        SchemeDocument(
            scheme_id="sukanya-samriddhi",
            name="Sukanya Samriddhi Yojana",
            description="Savings scheme for the girl child with attractive interest rate.",
            category=SchemeCategory.WOMEN_CHILD,
            ministry="Ministry of Finance",
            state=None,
            eligibility=EligibilityCriteria(
                gender="female",
                max_age=10,
            ),
            benefits="High interest rate savings for girl child education and marriage",
            application_process="Open account at any post office or authorized bank",
            documents_required=["Birth Certificate", "Parent Aadhaar Card"],
            last_updated="2025-09-01T00:00:00Z",
            popularity_score=0.80,
        ),
    ]


@pytest.fixture
async def search_service(sample_schemes: list[SchemeDocument]) -> SchemeSearchService:
    """Create and initialize a SchemeSearchService with sample schemes."""
    rag = RAGService(embedding_dim=768)
    cache = CacheManager(redis_url=None, namespace="test:")
    service = SchemeSearchService(rag=rag, cache=cache)
    await service.initialize(sample_schemes)
    return service


# -----------------------------------------------------------------------
# load_schemes tests
# -----------------------------------------------------------------------


class TestLoadSchemes:
    def test_load_all_30_schemes(self) -> None:
        schemes = load_schemes(_CENTRAL_SCHEMES_PATH)
        assert len(schemes) == 30, (
            f"central_schemes.json should contain 30 schemes, got {len(schemes)}"
        )

    def test_all_schemes_have_required_fields(self) -> None:
        schemes = load_schemes(_CENTRAL_SCHEMES_PATH)
        for scheme in schemes:
            assert scheme.scheme_id, f"scheme should have a scheme_id"
            assert scheme.name, f"scheme {scheme.scheme_id} should have a name"
            assert scheme.description, f"scheme {scheme.scheme_id} should have a description"
            assert scheme.ministry, f"scheme {scheme.scheme_id} should have a ministry"
            assert scheme.benefits, f"scheme {scheme.scheme_id} should have benefits"
            assert scheme.application_process, f"scheme {scheme.scheme_id} should have an application_process"

    def test_scheme_categories_are_valid(self) -> None:
        schemes = load_schemes(_CENTRAL_SCHEMES_PATH)
        valid_categories = {cat.value for cat in SchemeCategory}
        for scheme in schemes:
            assert scheme.category.value in valid_categories, (
                f"scheme {scheme.scheme_id} has invalid category: {scheme.category}"
            )

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_schemes(Path("/nonexistent/path/schemes.json"))

    def test_pm_kisan_present(self) -> None:
        schemes = load_schemes(_CENTRAL_SCHEMES_PATH)
        ids = {s.scheme_id for s in schemes}
        assert "pm-kisan" in ids, "PM-KISAN should be present in the scheme data"

    def test_ayushman_bharat_present(self) -> None:
        schemes = load_schemes(_CENTRAL_SCHEMES_PATH)
        ids = {s.scheme_id for s in schemes}
        assert "ayushman-bharat-pmjay" in ids, "Ayushman Bharat should be present in the scheme data"


# -----------------------------------------------------------------------
# SchemeSearchService initialization tests
# -----------------------------------------------------------------------


class TestSchemeSearchServiceInit:
    async def test_initialization(self, search_service: SchemeSearchService) -> None:
        all_schemes = await search_service.get_all_schemes()
        assert len(all_schemes) == 5, "service should have 5 schemes after initialization"

    async def test_empty_initialization(self) -> None:
        rag = RAGService(embedding_dim=768)
        cache = CacheManager(redis_url=None)
        service = SchemeSearchService(rag=rag, cache=cache)
        await service.initialize([])
        all_schemes = await service.get_all_schemes()
        assert len(all_schemes) == 0, "service with no schemes should have 0 schemes"


# -----------------------------------------------------------------------
# SchemeSearchService.search tests
# -----------------------------------------------------------------------


class TestSchemeSearch:
    async def test_search_farmer_returns_results(self, search_service: SchemeSearchService) -> None:
        results = await search_service.search("farmer", top_k=5)
        assert len(results) > 0, "searching for 'farmer' should return at least one result"

        # At least one result should be agriculture-related.
        categories = [r.get("category") for r in results]
        assert "agriculture" in categories, (
            "searching for 'farmer' should return agriculture schemes"
        )

    async def test_search_health_insurance_returns_results(self, search_service: SchemeSearchService) -> None:
        results = await search_service.search("health insurance", top_k=5)
        assert len(results) > 0, "searching for 'health insurance' should return results"

        # The health scheme should be among the results.
        scheme_ids = [r.get("scheme_id") for r in results if "scheme_id" in r]
        # In the metadata-based results, check category instead if scheme_id is not present.
        categories = [r.get("category") for r in results]
        assert "health" in categories or "ayushman-bharat" in scheme_ids, (
            "health insurance search should include health-related schemes"
        )

    async def test_search_returns_limited_results(self, search_service: SchemeSearchService) -> None:
        results = await search_service.search("scheme", top_k=2)
        assert len(results) <= 2, "search should respect top_k limit"

    async def test_search_empty_index(self) -> None:
        rag = RAGService(embedding_dim=768)
        cache = CacheManager(redis_url=None)
        service = SchemeSearchService(rag=rag, cache=cache)
        # Do not initialize -- index is empty.
        results = await service.search("farmer")
        assert results == [], "search on empty index should return empty list"


# -----------------------------------------------------------------------
# SchemeSearchService accessor tests
# -----------------------------------------------------------------------


class TestSchemeAccessors:
    async def test_get_scheme_by_id_found(self, search_service: SchemeSearchService) -> None:
        scheme = await search_service.get_scheme_by_id("pm-kisan")
        assert scheme is not None, "get_scheme_by_id should return a scheme for valid ID"
        assert scheme.scheme_id == "pm-kisan"
        assert scheme.name == "PM-KISAN (Pradhan Mantri Kisan Samman Nidhi)"

    async def test_get_scheme_by_id_not_found(self, search_service: SchemeSearchService) -> None:
        scheme = await search_service.get_scheme_by_id("nonexistent-scheme")
        assert scheme is None, "get_scheme_by_id should return None for unknown ID"

    async def test_get_schemes_by_category(self, search_service: SchemeSearchService) -> None:
        agri_schemes = await search_service.get_schemes_by_category("agriculture")
        assert len(agri_schemes) == 1, "should have exactly 1 agriculture scheme in sample data"
        assert agri_schemes[0].scheme_id == "pm-kisan"

    async def test_get_schemes_by_category_empty(self, search_service: SchemeSearchService) -> None:
        tribal_schemes = await search_service.get_schemes_by_category("tribal")
        assert tribal_schemes == [], "no tribal schemes in sample data"

    async def test_get_all_schemes(self, search_service: SchemeSearchService) -> None:
        schemes = await search_service.get_all_schemes()
        assert len(schemes) == 5


# -----------------------------------------------------------------------
# _text_to_embedding tests
# -----------------------------------------------------------------------


class TestTextToEmbedding:
    def test_produces_768_dim_vector(self) -> None:
        embedding = SchemeSearchService._text_to_embedding("farmer agriculture scheme")
        assert len(embedding) == 768, "embedding should have 768 dimensions"

    def test_consistent_for_same_text(self) -> None:
        emb1 = SchemeSearchService._text_to_embedding("PM Kisan farmer scheme")
        emb2 = SchemeSearchService._text_to_embedding("PM Kisan farmer scheme")
        assert emb1 == emb2, "same text should produce identical embeddings (deterministic)"

    def test_different_for_different_text(self) -> None:
        emb1 = SchemeSearchService._text_to_embedding("farmer agriculture")
        emb2 = SchemeSearchService._text_to_embedding("health insurance hospital")
        assert emb1 != emb2, "different text should produce different embeddings"

    def test_embedding_is_normalized(self) -> None:
        embedding = SchemeSearchService._text_to_embedding("farmer agriculture kisan")
        vec = np.array(embedding)
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 1e-5, f"embedding should be L2-normalized (norm={norm})"

    def test_empty_text_returns_zero_vector(self) -> None:
        embedding = SchemeSearchService._text_to_embedding("")
        assert all(v == 0.0 for v in embedding), "empty text should produce zero vector"

    def test_single_short_word_returns_zero_vector(self) -> None:
        # Words with length <= 1 are skipped.
        embedding = SchemeSearchService._text_to_embedding("I a")
        assert all(v == 0.0 for v in embedding), "single-letter words should be skipped"

    def test_embedding_values_are_floats(self) -> None:
        embedding = SchemeSearchService._text_to_embedding("test embedding")
        assert all(isinstance(v, float) for v in embedding), "all embedding values should be floats"


# -----------------------------------------------------------------------
# SchemeSearchService.search_schemes tests
# -----------------------------------------------------------------------


class TestSearchSchemes:
    async def test_search_schemes_returns_enriched_results(self, search_service: SchemeSearchService) -> None:
        results = await search_service.search_schemes("farmer kisan", language="en", top_k=3)
        assert len(results) > 0, "search_schemes should return results for 'farmer kisan'"

        # Check that results contain enriched fields.
        first = results[0]
        assert "scheme_id" in first, "result should contain scheme_id"
        assert "name" in first, "result should contain name"
        assert "description" in first, "result should contain description"
        assert "score" in first, "result should contain score"
        assert "benefits" in first, "result should contain benefits"

    async def test_search_schemes_caching(self, search_service: SchemeSearchService) -> None:
        """Same query should use cache on second call."""
        results1 = await search_service.search_schemes("farmer", language="en", top_k=3)
        results2 = await search_service.search_schemes("farmer", language="en", top_k=3)
        assert results1 == results2, "identical queries should return identical cached results"

    async def test_search_schemes_with_user_profile(self, search_service: SchemeSearchService) -> None:
        results = await search_service.search_schemes(
            "scheme",
            language="en",
            user_profile={"state": "Bihar", "occupation": "farmer", "is_bpl": True},
            top_k=5,
        )
        assert len(results) > 0, "search with user profile should return results"
