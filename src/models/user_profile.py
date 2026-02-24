"""Family-based user profile models for HaqSetu.

HaqSetu's KEY DIFFERENTIATOR: Instead of matching schemes for a single
individual (which every other platform does), we model the entire family
and discover schemes that ANY family member qualifies for.

A family of five can easily qualify for 20-30 distinct schemes across
agriculture, education, health, housing, pensions, and social security
-- but no one discovers them because existing platforms only do
individual lookups.

Privacy & Consent: All fields are optional except ``relation`` on family
members.  Profile data is stored only after explicit DPDPA consent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class FamilyMember(BaseModel):
    """A single family member's profile for scheme matching.

    All fields except ``relation`` are optional so users can provide
    as much or as little information as they are comfortable sharing.
    More data leads to better scheme matches, but even minimal data
    (e.g. relation + age + gender) is useful.
    """

    name: str | None = None  # Optional for privacy
    relation: str  # "self", "spouse", "child", "parent", "sibling"
    age: int | None = None
    gender: str | None = None  # "male", "female", "other"
    occupation: str | None = None
    education: str | None = None  # "none", "primary", "secondary", "higher_secondary", "graduate", "post_graduate"
    disability: str | None = None  # "none", "visual", "hearing", "locomotor", "mental", "multiple"
    is_student: bool = False
    is_pregnant: bool = False
    has_chronic_illness: bool = False

    @property
    def member_key(self) -> str:
        """Unique key for this member within the family.

        Used as the dict key in ``FamilyEligibilityReport.member_results``.
        Format: ``"relation:name"`` or ``"relation:index"`` if no name.
        """
        label = self.name or "unnamed"
        return f"{self.relation}:{label}"

    @property
    def is_minor(self) -> bool:
        return self.age is not None and self.age < 18

    @property
    def is_senior(self) -> bool:
        return self.age is not None and self.age >= 60

    @property
    def is_disabled(self) -> bool:
        return self.disability is not None and self.disability != "none"


class UserProfile(BaseModel):
    """Complete user/family profile for comprehensive scheme matching.

    This is HaqSetu's key differentiator: instead of matching schemes for
    an individual, we match for the ENTIRE FAMILY -- discovering schemes
    each family member qualifies for.

    Example: A farmer (age 45) with wife (40, homemaker), daughter (18,
    student), son (8), and elderly mother (70, BPL) could qualify for:

    - PM-KISAN (farmer)
    - PM Fasal Bima Yojana (farmer)
    - Sukanya Samriddhi Yojana (daughter)
    - PM Matru Vandana Yojana (if wife is pregnant)
    - Samagra Shiksha Abhiyan (son's education)
    - IGNOAPS (elderly mother's pension)
    - Ayushman Bharat PMJAY (family health)
    - PM Awas Yojana Gramin (housing)
    - NFSA / Ration (food security)
    - PM Ujjwala Yojana (cooking gas)
    - And 10+ more state-specific schemes

    That is 20-30 schemes from ONE family profile -- something no other
    platform in India provides.
    """

    model_config = {"frozen": False}

    profile_id: str = Field(default_factory=lambda: uuid4().hex)

    # ----------------------------------------------------------------
    # Primary user (head of family)
    # ----------------------------------------------------------------
    age: int | None = None
    gender: str | None = None  # "male", "female", "other"
    state: str | None = None
    district: str | None = None
    pin_code: str | None = None

    # ----------------------------------------------------------------
    # Economic
    # ----------------------------------------------------------------
    annual_income: float | None = None  # In INR
    is_bpl: bool | None = None
    category: str | None = None  # "general", "sc", "st", "obc", "ews"

    # ----------------------------------------------------------------
    # Occupation
    # ----------------------------------------------------------------
    occupation: str | None = None  # "farmer", "laborer", "artisan", "vendor", "student", "homemaker", "unemployed", "salaried", "self_employed"
    land_holding_acres: float | None = None

    # ----------------------------------------------------------------
    # Family members (the DIFFERENTIATOR)
    # ----------------------------------------------------------------
    family_members: list[FamilyMember] = Field(default_factory=list)

    # ----------------------------------------------------------------
    # Documents owned (helps assess application readiness)
    # ----------------------------------------------------------------
    has_aadhaar: bool = True
    has_bank_account: bool | None = None
    has_ration_card: bool | None = None
    has_land_records: bool | None = None
    has_income_certificate: bool | None = None
    has_caste_certificate: bool | None = None
    has_domicile_certificate: bool | None = None

    # ----------------------------------------------------------------
    # Preferences
    # ----------------------------------------------------------------
    preferred_language: str = "hi"
    preferred_channel: str = "web"  # "web", "sms", "whatsapp", "ivr_callback"

    # ----------------------------------------------------------------
    # DPDPA Consent
    # ----------------------------------------------------------------
    consent_given: bool = False
    consent_timestamp: datetime | None = None

    # ----------------------------------------------------------------
    # Timestamps
    # ----------------------------------------------------------------
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ----------------------------------------------------------------
    # Computed properties for quick eligibility pre-filtering
    # ----------------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def family_size(self) -> int:
        """Total family members including the primary user."""
        return len(self.family_members) + 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_children(self) -> bool:
        return any(m.relation == "child" for m in self.family_members)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_elderly(self) -> bool:
        return any(m.age is not None and m.age >= 60 for m in self.family_members)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_girl_child(self) -> bool:
        return any(
            m.relation == "child" and m.gender == "female"
            for m in self.family_members
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_disabled_member(self) -> bool:
        return any(m.is_disabled for m in self.family_members)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_student(self) -> bool:
        return any(m.is_student for m in self.family_members)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_pregnant_member(self) -> bool:
        return any(m.is_pregnant for m in self.family_members)

    def to_individual_profile(self) -> dict:
        """Convert primary user data into a flat dict for eligibility matching."""
        return {
            "age": self.age,
            "gender": self.gender,
            "state": self.state,
            "district": self.district,
            "annual_income": self.annual_income,
            "is_bpl": self.is_bpl,
            "category": self.category,
            "occupation": self.occupation,
            "land_holding_acres": self.land_holding_acres,
            "family_size": self.family_size,
            "has_aadhaar": self.has_aadhaar,
            "has_bank_account": self.has_bank_account,
            "has_ration_card": self.has_ration_card,
            "has_land_records": self.has_land_records,
            "has_income_certificate": self.has_income_certificate,
            "has_caste_certificate": self.has_caste_certificate,
            "relation": "self",
            "name": None,
        }

    def member_to_profile(self, member: FamilyMember) -> dict:
        """Convert a family member into a flat dict for eligibility matching.

        Family-level attributes (income, BPL, category, state) are
        inherited from the primary user since they are household-level.
        """
        return {
            "age": member.age,
            "gender": member.gender,
            "state": self.state,
            "district": self.district,
            "annual_income": self.annual_income,  # Household income
            "is_bpl": self.is_bpl,  # Household BPL
            "category": self.category,  # Household category
            "occupation": member.occupation,
            "education": member.education,
            "disability": member.disability,
            "is_student": member.is_student,
            "is_pregnant": member.is_pregnant,
            "has_chronic_illness": member.has_chronic_illness,
            "land_holding_acres": self.land_holding_acres,  # Household land
            "family_size": self.family_size,
            "has_aadhaar": self.has_aadhaar,  # Assume family-level
            "has_bank_account": self.has_bank_account,
            "has_ration_card": self.has_ration_card,
            "relation": member.relation,
            "name": member.name,
        }
