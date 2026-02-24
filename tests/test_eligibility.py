"""Tests for the smart eligibility engine -- family-level scheme matching.

Covers EligibilityEngine initialization, individual matching, family
matching, the internal rules engine (_check_eligibility), priority
scoring, document-readiness checks, next-steps generation, and the
category/occupation pre-index used for O(1) lookups.

Uses REAL scheme data from central_schemes.json via load_schemes().
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.data.seed import load_schemes
from src.models.scheme import EligibilityCriteria, SchemeCategory, SchemeDocument
from src.models.user_profile import FamilyMember, UserProfile
from src.services.eligibility import (
    EligibilityEngine,
    EligibilityResult,
    FamilyEligibilityReport,
    _extract_amount,
    _parse_deadline,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_schemes() -> list[SchemeDocument]:
    """Load the full bundled central_schemes.json."""
    return load_schemes()


@pytest.fixture(scope="module")
def engine(real_schemes: list[SchemeDocument]) -> EligibilityEngine:
    """EligibilityEngine initialized with real scheme data."""
    return EligibilityEngine(real_schemes)


@pytest.fixture
def farmer_profile() -> dict:
    """Flat profile dict for a 45-year-old BPL farmer (OBC, 2 acres)."""
    return {
        "age": 45,
        "gender": "male",
        "state": "Uttar Pradesh",
        "district": "Lucknow",
        "annual_income": 60000.0,
        "is_bpl": True,
        "category": "obc",
        "occupation": "farmer",
        "land_holding_acres": 2.0,
        "family_size": 5,
        "has_aadhaar": True,
        "has_bank_account": True,
        "has_ration_card": True,
        "has_land_records": True,
        "has_income_certificate": True,
        "has_caste_certificate": True,
        "relation": "self",
        "name": None,
    }


@pytest.fixture
def elderly_bpl_profile() -> dict:
    """Flat profile dict for a 70-year-old BPL elderly female."""
    return {
        "age": 70,
        "gender": "female",
        "state": "Uttar Pradesh",
        "annual_income": 20000.0,
        "is_bpl": True,
        "category": "obc",
        "occupation": None,
        "land_holding_acres": None,
        "family_size": 5,
        "has_aadhaar": True,
        "has_bank_account": True,
        "has_ration_card": True,
        "has_land_records": None,
        "has_income_certificate": None,
        "has_caste_certificate": None,
        "relation": "parent",
        "name": "Kamla",
    }


@pytest.fixture
def realistic_family() -> UserProfile:
    """A realistic five-person rural family.

    - Farmer (45, male, OBC, BPL, 2 acres, Rs 60k income)
    - Wife (40, female, homemaker)
    - Daughter (18, female, student)
    - Son (8, male, student)
    - Mother (70, female, elderly)
    """
    return UserProfile(
        age=45,
        gender="male",
        state="Uttar Pradesh",
        district="Lucknow",
        pin_code="226001",
        annual_income=60000.0,
        is_bpl=True,
        category="obc",
        occupation="farmer",
        land_holding_acres=2.0,
        family_members=[
            FamilyMember(
                name="Sita",
                relation="spouse",
                age=40,
                gender="female",
                occupation="homemaker",
            ),
            FamilyMember(
                name="Priya",
                relation="child",
                age=18,
                gender="female",
                is_student=True,
                education="higher_secondary",
            ),
            FamilyMember(
                name="Ravi",
                relation="child",
                age=8,
                gender="male",
                is_student=True,
                education="primary",
            ),
            FamilyMember(
                name="Kamla",
                relation="parent",
                age=70,
                gender="female",
                has_chronic_illness=True,
            ),
        ],
        has_aadhaar=True,
        has_bank_account=True,
        has_ration_card=True,
        has_land_records=True,
        has_income_certificate=True,
        has_caste_certificate=True,
        has_domicile_certificate=True,
        preferred_language="hi",
        preferred_channel="whatsapp",
        consent_given=True,
        consent_timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# EligibilityEngine initialization
# ---------------------------------------------------------------------------


class TestEligibilityEngineInit:
    """Tests for engine construction and pre-indexing."""

    def test_initialization_with_schemes(
        self, engine: EligibilityEngine, real_schemes: list[SchemeDocument]
    ) -> None:
        assert engine._schemes is real_schemes
        assert len(engine._schemes) == 30

    def test_category_index_built(self, engine: EligibilityEngine) -> None:
        """Category index should contain entries for categories present in data."""
        assert len(engine._category_index) > 0
        # Agriculture schemes must be indexed
        assert SchemeCategory.AGRICULTURE in engine._category_index
        ag_schemes = engine._category_index[SchemeCategory.AGRICULTURE]
        assert any(s.scheme_id == "pm-kisan" for s in ag_schemes)

    def test_occupation_index_built(self, engine: EligibilityEngine) -> None:
        """Occupation index should contain 'farmer' keyword."""
        assert "farmer" in engine._occupation_index
        farmer_schemes = engine._occupation_index["farmer"]
        assert len(farmer_schemes) > 0

    def test_category_index_o1_lookup(self, engine: EligibilityEngine) -> None:
        """Looking up schemes by category should be O(1) -- a dict lookup."""
        # This verifies the structure is a defaultdict/dict, not a list scan
        assert isinstance(engine._category_index, dict)
        # Direct key access, no iteration needed
        health_schemes = engine._category_index[SchemeCategory.HEALTH]
        assert isinstance(health_schemes, list)

    def test_empty_engine(self) -> None:
        eng = EligibilityEngine([])
        assert len(eng._schemes) == 0
        assert len(eng._category_index) == 0


# ---------------------------------------------------------------------------
# Individual matching
# ---------------------------------------------------------------------------


class TestMatchIndividual:
    """Tests for match_individual() with real scheme data."""

    def test_farmer_matches_agriculture_schemes(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        results = engine.match_individual(farmer_profile)
        assert len(results) > 0
        scheme_ids = [r.scheme_id for r in results]
        # A BPL farmer should match PM-KISAN
        assert "pm-kisan" in scheme_ids

    def test_farmer_matches_fasal_bima(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        results = engine.match_individual(farmer_profile)
        scheme_ids = [r.scheme_id for r in results]
        assert "pm-fasal-bima-yojana" in scheme_ids

    def test_farmer_matches_health_schemes(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        """A BPL farmer should also match Ayushman Bharat."""
        results = engine.match_individual(farmer_profile)
        scheme_ids = [r.scheme_id for r in results]
        assert "ayushman-bharat-pmjay" in scheme_ids

    def test_elderly_bpl_matches_pension(
        self, engine: EligibilityEngine, elderly_bpl_profile: dict
    ) -> None:
        results = engine.match_individual(elderly_bpl_profile)
        scheme_ids = [r.scheme_id for r in results]
        # IGNOAPS requires age >= 60 and is_bpl
        assert "ignoaps" in scheme_ids

    def test_elderly_bpl_matches_ayushman(
        self, engine: EligibilityEngine, elderly_bpl_profile: dict
    ) -> None:
        results = engine.match_individual(elderly_bpl_profile)
        scheme_ids = [r.scheme_id for r in results]
        assert "ayushman-bharat-pmjay" in scheme_ids

    def test_results_sorted_by_priority(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        results = engine.match_individual(farmer_profile)
        scores = [r.priority_score for r in results]
        # Verify descending order
        assert scores == sorted(scores, reverse=True)

    def test_all_results_are_eligible(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        results = engine.match_individual(farmer_profile)
        assert all(r.eligible for r in results)

    def test_result_has_for_member_field(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        results = engine.match_individual(farmer_profile)
        assert all(r.for_member == "self" for r in results)

    def test_named_member_for_member(
        self, engine: EligibilityEngine, elderly_bpl_profile: dict
    ) -> None:
        results = engine.match_individual(elderly_bpl_profile)
        # The for_member should include the name
        assert all("Kamla" in r.for_member for r in results)


# ---------------------------------------------------------------------------
# Family matching (the key differentiator)
# ---------------------------------------------------------------------------


class TestMatchFamily:
    """Tests for match_family() -- the UNIQUE feature of HaqSetu."""

    def test_family_report_structure(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        assert isinstance(report, FamilyEligibilityReport)
        assert report.profile_id == realistic_family.profile_id

    def test_family_matches_more_than_individual(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        """Family matching should find more schemes than individual matching."""
        # Individual results for the farmer only
        self_profile = realistic_family.to_individual_profile()
        individual_results = engine.match_individual(self_profile)
        individual_count = len(individual_results)

        # Family results for the whole family
        report = engine.match_family(realistic_family)
        family_count = report.total_schemes_matched

        assert family_count > individual_count, (
            f"Family matched {family_count} but individual matched "
            f"{individual_count}; family should find more"
        )

    def test_family_matches_pm_kisan(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        all_ids = set()
        for results in report.member_results.values():
            all_ids.update(r.scheme_id for r in results)
        assert "pm-kisan" in all_ids

    def test_family_matches_ayushman(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        all_ids = set()
        for results in report.member_results.values():
            all_ids.update(r.scheme_id for r in results)
        assert "ayushman-bharat-pmjay" in all_ids

    def test_family_matches_ignoaps(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        """Elderly mother (70, BPL) should trigger IGNOAPS match."""
        report = engine.match_family(realistic_family)
        all_ids = set()
        for results in report.member_results.values():
            all_ids.update(r.scheme_id for r in results)
        assert "ignoaps" in all_ids

    def test_member_results_contain_self(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        assert "self" in report.member_results

    def test_member_results_contain_all_members(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        # Should have "self" + 4 family members = 5 keys
        assert len(report.member_results) == 5

    def test_top_priority_schemes_populated(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        assert len(report.top_priority_schemes) > 0
        assert len(report.top_priority_schemes) <= 10

    def test_total_schemes_matched_positive(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        assert report.total_schemes_matched > 5

    def test_next_steps_generated(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        assert len(report.next_steps) > 0

    def test_generated_at_set(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        assert report.generated_at is not None


# ---------------------------------------------------------------------------
# _check_eligibility (rules engine)
# ---------------------------------------------------------------------------


class TestCheckEligibility:
    """Tests for the internal rules engine."""

    def _make_scheme(self, **elig_kwargs: object) -> SchemeDocument:
        """Helper to create a minimal scheme with given eligibility criteria."""
        return SchemeDocument(
            scheme_id="test-scheme",
            name="Test Scheme",
            description="A test scheme for unit testing.",
            category=SchemeCategory.OTHER,
            ministry="Test Ministry",
            eligibility=EligibilityCriteria(**elig_kwargs),
            benefits="Test benefit",
            application_process="Test process",
            documents_required=[],
            last_updated="2025-01-01T00:00:00Z",
        )

    def test_age_within_range(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(min_age=18, max_age=60)
        profile = {"age": 30}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "age" in result.matched_criteria

    def test_age_below_minimum(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(min_age=18)
        profile = {"age": 15}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_age_above_maximum(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(max_age=50)
        profile = {"age": 55}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_gender_match(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(gender="female")
        profile = {"gender": "female"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "gender" in result.matched_criteria

    def test_gender_mismatch(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(gender="female")
        profile = {"gender": "male"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_gender_all_matches_any(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(gender="all")
        profile = {"gender": "male"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True

    def test_income_within_limit(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(income_limit=200000)
        profile = {"annual_income": 60000}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "income" in result.matched_criteria

    def test_income_exceeds_limit(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(income_limit=100000)
        profile = {"annual_income": 200000}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_bpl_required_and_is_bpl(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(is_bpl=True)
        profile = {"is_bpl": True}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "bpl_status" in result.matched_criteria

    def test_bpl_required_but_not_bpl(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(is_bpl=True)
        profile = {"is_bpl": False}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_category_match(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(category="sc, st, obc")
        profile = {"category": "obc"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True

    def test_category_mismatch(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(category="sc, st")
        profile = {"category": "general"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_occupation_match(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(occupation="farmer")
        profile = {"occupation": "farmer"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "occupation" in result.matched_criteria

    def test_occupation_mismatch(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(occupation="farmer")
        profile = {"occupation": "laborer"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_land_holding_within_limit(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(land_holding_acres=5.0)
        profile = {"land_holding_acres": 2.0}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "land_holding" in result.matched_criteria

    def test_land_holding_exceeds_limit(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(land_holding_acres=2.0)
        profile = {"land_holding_acres": 5.0}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_state_specific_match(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme()
        scheme.state = "Uttar Pradesh"
        profile = {"state": "Uttar Pradesh"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "state" in result.matched_criteria

    def test_state_specific_mismatch(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme()
        scheme.state = "Bihar"
        profile = {"state": "Uttar Pradesh"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_central_scheme_matches_all_states(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme()
        scheme.state = None  # central scheme
        profile = {"state": "Tamil Nadu"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True
        assert "central_scheme" in result.matched_criteria

    def test_multiple_criteria_combined(self, engine: EligibilityEngine) -> None:
        """A scheme requiring age + gender + BPL -- all must pass."""
        scheme = self._make_scheme(min_age=60, gender="female", is_bpl=True)
        profile = {"age": 70, "gender": "female", "is_bpl": True}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True

    def test_multiple_criteria_one_fails(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(min_age=60, gender="female", is_bpl=True)
        profile = {"age": 70, "gender": "male", "is_bpl": True}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is False

    def test_no_criteria_scheme_matches(self, engine: EligibilityEngine) -> None:
        """A scheme with no eligibility criteria should match anyone."""
        scheme = self._make_scheme()
        profile = {"age": 30, "gender": "male"}
        result = engine._check_eligibility(profile, scheme)
        assert result.eligible is True

    def test_confidence_computed(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme(min_age=18, max_age=60, gender="male")
        profile = {"age": 30, "gender": "male"}
        result = engine._check_eligibility(profile, scheme)
        assert 0.0 <= result.confidence <= 1.0

    def test_result_structure(self, engine: EligibilityEngine) -> None:
        scheme = self._make_scheme()
        profile = {"age": 30}
        result = engine._check_eligibility(profile, scheme)
        assert isinstance(result, EligibilityResult)
        assert result.scheme_id == "test-scheme"
        assert result.scheme_name == "Test Scheme"


# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------


class TestPriorityScoring:
    """Tests for _compute_priority_score."""

    def test_priority_score_range(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        results = engine.match_individual(farmer_profile)
        for r in results:
            assert 0.0 <= r.priority_score <= 1.0

    def test_high_benefit_gets_higher_priority(
        self, engine: EligibilityEngine
    ) -> None:
        """PMJAY (Rs 5 lakh) should score higher than Ujjwala (Rs 1,600)
        for someone with the same profile."""
        profile = {
            "age": 40,
            "gender": "female",
            "is_bpl": True,
            "annual_income": 30000.0,
            "has_aadhaar": True,
            "has_bank_account": True,
            "has_ration_card": True,
            "relation": "self",
        }
        results = engine.match_individual(profile)
        result_map = {r.scheme_id: r for r in results}

        if "ayushman-bharat-pmjay" in result_map and "pm-ujjwala-yojana" in result_map:
            pmjay_score = result_map["ayushman-bharat-pmjay"].priority_score
            ujjwala_score = result_map["pm-ujjwala-yojana"].priority_score
            assert pmjay_score > ujjwala_score


# ---------------------------------------------------------------------------
# Missing documents detection
# ---------------------------------------------------------------------------


class TestMissingDocuments:
    """Tests for _check_missing_documents."""

    def test_no_missing_docs_when_all_present(
        self, engine: EligibilityEngine, farmer_profile: dict
    ) -> None:
        """Farmer profile has all documents -- no missing docs."""
        results = engine.match_individual(farmer_profile)
        pm_kisan = next((r for r in results if r.scheme_id == "pm-kisan"), None)
        assert pm_kisan is not None
        # The farmer has all docs, so missing_documents should be empty
        assert pm_kisan.missing_documents == []

    def test_missing_docs_when_not_present(
        self, engine: EligibilityEngine
    ) -> None:
        """Profile missing ration card should list it as missing for
        schemes that require it (e.g. Ayushman Bharat requires 'Ration Card / SECC Letter').
        """
        profile = {
            "age": 45,
            "gender": "male",
            "is_bpl": True,
            "occupation": "farmer",
            "land_holding_acres": 2.0,
            "has_aadhaar": True,
            "has_bank_account": True,
            "has_ration_card": False,
            "has_land_records": True,
            "has_income_certificate": None,
            "has_caste_certificate": None,
            "relation": "self",
        }
        results = engine.match_individual(profile)
        # Ayushman Bharat requires "Ration Card / SECC Letter"
        pmjay = next((r for r in results if r.scheme_id == "ayushman-bharat-pmjay"), None)
        assert pmjay is not None
        # Ration card is False, so it should appear in missing documents
        assert len(pmjay.missing_documents) > 0
        missing_text = " ".join(pmjay.missing_documents).lower()
        assert "ration" in missing_text


# ---------------------------------------------------------------------------
# Next steps generation
# ---------------------------------------------------------------------------


class TestNextSteps:
    """Tests for _generate_next_steps."""

    def test_next_steps_include_document_prep(
        self, engine: EligibilityEngine
    ) -> None:
        """When docs are missing, first step should mention documents."""
        profile = UserProfile(
            age=45,
            occupation="farmer",
            is_bpl=True,
            has_bank_account=False,
            land_holding_acres=2.0,
            consent_given=True,
        )
        report = engine.match_family(profile)
        # Should recommend getting documents ready or opening bank account
        steps_text = " ".join(report.next_steps)
        assert "bank account" in steps_text.lower() or "document" in steps_text.lower()

    def test_next_steps_for_girl_child(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        steps_text = " ".join(report.next_steps)
        assert "sukanya" in steps_text.lower() or "daughter" in steps_text.lower()

    def test_next_steps_for_elderly(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        steps_text = " ".join(report.next_steps)
        assert "elderly" in steps_text.lower() or "pension" in steps_text.lower()

    def test_next_steps_capped_at_eight(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        report = engine.match_family(realistic_family)
        assert len(report.next_steps) <= 8

    def test_next_steps_csc_recommendation(
        self, engine: EligibilityEngine, realistic_family: UserProfile
    ) -> None:
        """When many schemes match, recommend a CSC visit."""
        report = engine.match_family(realistic_family)
        if report.total_schemes_matched >= 5:
            steps_text = " ".join(report.next_steps)
            assert "csc" in steps_text.lower()


# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------


class TestUtilities:
    """Tests for _extract_amount and _parse_deadline."""

    def test_extract_amount_rs_dot(self) -> None:
        assert _extract_amount("Rs. 6,000 per year") == 6000.0

    def test_extract_amount_rs_no_dot(self) -> None:
        assert _extract_amount("Rs 2,00,000") == 200000.0

    def test_extract_amount_inr(self) -> None:
        assert _extract_amount("INR 500000") == 500000.0

    def test_extract_amount_rupee_symbol(self) -> None:
        assert _extract_amount("₹5,00,000 per family") == 500000.0

    def test_extract_amount_per_year(self) -> None:
        assert _extract_amount("6000 per year") == 6000.0

    def test_extract_amount_none_input(self) -> None:
        assert _extract_amount(None) is None

    def test_extract_amount_empty_string(self) -> None:
        assert _extract_amount("") is None

    def test_extract_amount_no_match(self) -> None:
        assert _extract_amount("Free LPG connection") is None

    def test_parse_deadline_iso(self) -> None:
        dt = _parse_deadline("2026-03-31")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 31

    def test_parse_deadline_indian_format(self) -> None:
        dt = _parse_deadline("31/03/2026")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_deadline_english_format(self) -> None:
        dt = _parse_deadline("March 31, 2026")
        assert dt is not None
        assert dt.month == 3

    def test_parse_deadline_invalid(self) -> None:
        assert _parse_deadline("not a date") is None

    def test_parse_deadline_empty(self) -> None:
        assert _parse_deadline("") is None
