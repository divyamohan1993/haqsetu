"""Tests for family-based user profile models.

Covers FamilyMember, UserProfile, computed properties, and profile
conversion methods that power HaqSetu's family-level scheme matching.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.models.user_profile import FamilyMember, UserProfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def farmer_member() -> FamilyMember:
    """A typical farmer (head of family) as a FamilyMember."""
    return FamilyMember(
        name="Ramesh",
        relation="self",
        age=45,
        gender="male",
        occupation="farmer",
        education="primary",
        disability=None,
        is_student=False,
        is_pregnant=False,
        has_chronic_illness=False,
    )


@pytest.fixture
def wife_member() -> FamilyMember:
    return FamilyMember(
        name="Sita",
        relation="spouse",
        age=40,
        gender="female",
        occupation="homemaker",
    )


@pytest.fixture
def daughter_member() -> FamilyMember:
    return FamilyMember(
        name="Priya",
        relation="child",
        age=18,
        gender="female",
        is_student=True,
        education="higher_secondary",
    )


@pytest.fixture
def son_member() -> FamilyMember:
    return FamilyMember(
        name="Ravi",
        relation="child",
        age=8,
        gender="male",
        is_student=True,
        education="primary",
    )


@pytest.fixture
def elderly_mother() -> FamilyMember:
    return FamilyMember(
        name="Kamla",
        relation="parent",
        age=70,
        gender="female",
        has_chronic_illness=True,
    )


@pytest.fixture
def full_family(
    wife_member: FamilyMember,
    daughter_member: FamilyMember,
    son_member: FamilyMember,
    elderly_mother: FamilyMember,
) -> UserProfile:
    """A realistic five-person family profile."""
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
        family_members=[wife_member, daughter_member, son_member, elderly_mother],
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
# FamilyMember tests
# ---------------------------------------------------------------------------


class TestFamilyMember:
    """Tests for FamilyMember model."""

    def test_creation_with_all_fields(self, farmer_member: FamilyMember) -> None:
        assert farmer_member.name == "Ramesh"
        assert farmer_member.relation == "self"
        assert farmer_member.age == 45
        assert farmer_member.gender == "male"
        assert farmer_member.occupation == "farmer"
        assert farmer_member.education == "primary"
        assert farmer_member.disability is None
        assert farmer_member.is_student is False
        assert farmer_member.is_pregnant is False
        assert farmer_member.has_chronic_illness is False

    def test_creation_minimal(self) -> None:
        """Only relation is required; all other fields are optional."""
        member = FamilyMember(relation="child")
        assert member.relation == "child"
        assert member.name is None
        assert member.age is None
        assert member.gender is None
        assert member.occupation is None
        assert member.education is None
        assert member.disability is None
        assert member.is_student is False
        assert member.is_pregnant is False
        assert member.has_chronic_illness is False

    def test_member_key_with_name(self, farmer_member: FamilyMember) -> None:
        assert farmer_member.member_key == "self:Ramesh"

    def test_member_key_without_name(self) -> None:
        member = FamilyMember(relation="child")
        assert member.member_key == "child:unnamed"

    def test_is_minor_true(self, son_member: FamilyMember) -> None:
        assert son_member.is_minor is True

    def test_is_minor_false_adult(self, farmer_member: FamilyMember) -> None:
        assert farmer_member.is_minor is False

    def test_is_minor_none_age(self) -> None:
        member = FamilyMember(relation="child")
        assert member.is_minor is False

    def test_is_senior_true(self, elderly_mother: FamilyMember) -> None:
        assert elderly_mother.is_senior is True

    def test_is_senior_false(self, farmer_member: FamilyMember) -> None:
        assert farmer_member.is_senior is False

    def test_is_senior_none_age(self) -> None:
        member = FamilyMember(relation="parent")
        assert member.is_senior is False

    def test_is_disabled_true(self) -> None:
        member = FamilyMember(relation="self", disability="locomotor")
        assert member.is_disabled is True

    def test_is_disabled_false_none(self) -> None:
        member = FamilyMember(relation="self", disability=None)
        assert member.is_disabled is False

    def test_is_disabled_false_none_string(self) -> None:
        member = FamilyMember(relation="self", disability="none")
        assert member.is_disabled is False

    def test_boundary_age_18_not_minor(self) -> None:
        member = FamilyMember(relation="child", age=18)
        assert member.is_minor is False

    def test_boundary_age_17_is_minor(self) -> None:
        member = FamilyMember(relation="child", age=17)
        assert member.is_minor is True

    def test_boundary_age_60_is_senior(self) -> None:
        member = FamilyMember(relation="parent", age=60)
        assert member.is_senior is True

    def test_boundary_age_59_not_senior(self) -> None:
        member = FamilyMember(relation="parent", age=59)
        assert member.is_senior is False


# ---------------------------------------------------------------------------
# UserProfile tests
# ---------------------------------------------------------------------------


class TestUserProfile:
    """Tests for UserProfile model and its computed properties."""

    def test_creation_with_family(self, full_family: UserProfile) -> None:
        assert full_family.age == 45
        assert full_family.gender == "male"
        assert full_family.state == "Uttar Pradesh"
        assert full_family.occupation == "farmer"
        assert full_family.annual_income == 60000.0
        assert full_family.is_bpl is True
        assert full_family.category == "obc"
        assert full_family.land_holding_acres == 2.0
        assert len(full_family.family_members) == 4
        assert full_family.consent_given is True
        assert full_family.preferred_language == "hi"
        assert full_family.preferred_channel == "whatsapp"

    def test_default_values(self) -> None:
        profile = UserProfile()
        assert profile.age is None
        assert profile.gender is None
        assert profile.state is None
        assert profile.annual_income is None
        assert profile.is_bpl is None
        assert profile.category is None
        assert profile.occupation is None
        assert profile.land_holding_acres is None
        assert profile.family_members == []
        assert profile.has_aadhaar is True
        assert profile.has_bank_account is None
        assert profile.preferred_language == "hi"
        assert profile.preferred_channel == "web"
        assert profile.consent_given is False
        assert profile.consent_timestamp is None
        assert profile.profile_id is not None
        assert len(profile.profile_id) > 0

    def test_unique_profile_ids(self) -> None:
        p1 = UserProfile()
        p2 = UserProfile()
        assert p1.profile_id != p2.profile_id

    # -- Computed properties -----------------------------------------------

    def test_family_size_with_members(self, full_family: UserProfile) -> None:
        """family_size = number of family members + 1 (primary user)."""
        assert full_family.family_size == 5

    def test_family_size_no_members(self) -> None:
        profile = UserProfile()
        assert profile.family_size == 1

    def test_has_children_true(self, full_family: UserProfile) -> None:
        assert full_family.has_children is True

    def test_has_children_false(self) -> None:
        profile = UserProfile(
            family_members=[FamilyMember(relation="spouse", age=40)]
        )
        assert profile.has_children is False

    def test_has_elderly_true(self, full_family: UserProfile) -> None:
        assert full_family.has_elderly is True

    def test_has_elderly_false(self) -> None:
        profile = UserProfile(
            family_members=[FamilyMember(relation="spouse", age=40)]
        )
        assert profile.has_elderly is False

    def test_has_girl_child_true(self, full_family: UserProfile) -> None:
        assert full_family.has_girl_child is True

    def test_has_girl_child_false_no_female_child(self) -> None:
        profile = UserProfile(
            family_members=[
                FamilyMember(relation="child", gender="male", age=10),
            ]
        )
        assert profile.has_girl_child is False

    def test_has_girl_child_false_female_but_not_child(self) -> None:
        """A female spouse is not a 'girl child'."""
        profile = UserProfile(
            family_members=[
                FamilyMember(relation="spouse", gender="female", age=35),
            ]
        )
        assert profile.has_girl_child is False

    def test_has_disabled_member_true(self) -> None:
        profile = UserProfile(
            family_members=[
                FamilyMember(relation="child", disability="visual"),
            ]
        )
        assert profile.has_disabled_member is True

    def test_has_disabled_member_false(self, full_family: UserProfile) -> None:
        assert full_family.has_disabled_member is False

    def test_has_student_true(self, full_family: UserProfile) -> None:
        assert full_family.has_student is True

    def test_has_student_false(self) -> None:
        profile = UserProfile(
            family_members=[FamilyMember(relation="spouse")]
        )
        assert profile.has_student is False

    def test_has_pregnant_member_true(self) -> None:
        profile = UserProfile(
            family_members=[
                FamilyMember(relation="spouse", is_pregnant=True),
            ]
        )
        assert profile.has_pregnant_member is True

    def test_has_pregnant_member_false(self, full_family: UserProfile) -> None:
        assert full_family.has_pregnant_member is False

    def test_empty_family_computed_properties(self) -> None:
        profile = UserProfile()
        assert profile.family_size == 1
        assert profile.has_children is False
        assert profile.has_elderly is False
        assert profile.has_girl_child is False
        assert profile.has_disabled_member is False
        assert profile.has_student is False
        assert profile.has_pregnant_member is False

    # -- to_individual_profile() -------------------------------------------

    def test_to_individual_profile(self, full_family: UserProfile) -> None:
        result = full_family.to_individual_profile()
        assert isinstance(result, dict)
        assert result["age"] == 45
        assert result["gender"] == "male"
        assert result["state"] == "Uttar Pradesh"
        assert result["district"] == "Lucknow"
        assert result["annual_income"] == 60000.0
        assert result["is_bpl"] is True
        assert result["category"] == "obc"
        assert result["occupation"] == "farmer"
        assert result["land_holding_acres"] == 2.0
        assert result["family_size"] == 5
        assert result["has_aadhaar"] is True
        assert result["has_bank_account"] is True
        assert result["has_ration_card"] is True
        assert result["has_land_records"] is True
        assert result["has_income_certificate"] is True
        assert result["has_caste_certificate"] is True
        assert result["relation"] == "self"
        assert result["name"] is None

    def test_to_individual_profile_minimal(self) -> None:
        profile = UserProfile()
        result = profile.to_individual_profile()
        assert result["age"] is None
        assert result["gender"] is None
        assert result["state"] is None
        assert result["occupation"] is None
        assert result["family_size"] == 1
        assert result["relation"] == "self"

    # -- member_to_profile() -----------------------------------------------

    def test_member_to_profile_inherits_household_attributes(
        self, full_family: UserProfile, daughter_member: FamilyMember
    ) -> None:
        result = full_family.member_to_profile(daughter_member)
        assert isinstance(result, dict)
        # Member-specific fields
        assert result["age"] == 18
        assert result["gender"] == "female"
        assert result["is_student"] is True
        assert result["education"] == "higher_secondary"
        assert result["occupation"] is None  # daughter has no occupation set
        assert result["relation"] == "child"
        assert result["name"] == "Priya"
        # Inherited household fields
        assert result["state"] == "Uttar Pradesh"
        assert result["district"] == "Lucknow"
        assert result["annual_income"] == 60000.0
        assert result["is_bpl"] is True
        assert result["category"] == "obc"
        assert result["land_holding_acres"] == 2.0
        assert result["family_size"] == 5
        assert result["has_aadhaar"] is True
        assert result["has_bank_account"] is True
        assert result["has_ration_card"] is True

    def test_member_to_profile_elderly_mother(
        self, full_family: UserProfile, elderly_mother: FamilyMember
    ) -> None:
        result = full_family.member_to_profile(elderly_mother)
        assert result["age"] == 70
        assert result["gender"] == "female"
        assert result["has_chronic_illness"] is True
        assert result["is_bpl"] is True  # inherited
        assert result["relation"] == "parent"
        assert result["name"] == "Kamla"

    def test_member_to_profile_includes_disability(self) -> None:
        profile = UserProfile(state="Bihar")
        member = FamilyMember(relation="child", disability="visual", age=12)
        result = profile.member_to_profile(member)
        assert result["disability"] == "visual"
        assert result["state"] == "Bihar"

    def test_member_to_profile_includes_pregnancy(self) -> None:
        profile = UserProfile()
        member = FamilyMember(relation="spouse", is_pregnant=True, age=28)
        result = profile.member_to_profile(member)
        assert result["is_pregnant"] is True
