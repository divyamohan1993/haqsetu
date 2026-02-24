"""Verification data models for HaqSetu.

Defines the data structures for the multi-source government document
verification system. Only official government documents serve as valid
proof of a scheme's existence and details.

Verification Sources (in trust order):
1. Gazette of India (egazette.gov.in) - Legal notification of record
2. India Code (indiacode.nic.in) - Full text of enabling Acts
3. Parliament documents (sansad.in) - Bills/Acts passed
4. MyScheme (myscheme.gov.in) via API Setu - Official catalogue
5. data.gov.in - Supplementary government data

All other sources (news sites, blogs, third-party aggregators) are
treated as UNVERIFIED and carry zero trust weight.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

import orjson
from pydantic import BaseModel, Field


def _orjson_dumps(v: object, *, default: object = None) -> str:
    return orjson.dumps(v, default=default).decode()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class VerificationStatus(StrEnum):
    """Current verification state of a scheme."""

    __slots__ = ()

    UNVERIFIED = "unverified"
    PENDING = "pending"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    REVOKED = "revoked"


class VerificationSource(StrEnum):
    """Official government sources accepted for verification."""

    __slots__ = ()

    GAZETTE_OF_INDIA = "gazette_of_india"
    INDIA_CODE = "india_code"
    SANSAD_PARLIAMENT = "sansad_parliament"
    MYSCHEME_GOV = "myscheme_gov"
    DATA_GOV_IN = "data_gov_in"
    API_SETU = "api_setu"
    STATE_GAZETTE = "state_gazette"
    COURT_ORDER = "court_order"


class TrustLevel(StrEnum):
    """Trust tier with associated numerical weight.

    Weights encode how much confidence a single piece of evidence from
    this tier contributes to the overall trust score.

    +---------------------+--------+
    | Tier                | Weight |
    +---------------------+--------+
    | official_gazette    |   1.0  |
    | legislation         |   0.9  |
    | parliamentary       |  0.85  |
    | government_portal   |   0.7  |
    | government_data     |   0.5  |
    | unverified          |   0.0  |
    +---------------------+--------+
    """

    __slots__ = ()

    OFFICIAL_GAZETTE = "official_gazette"
    LEGISLATION = "legislation"
    PARLIAMENTARY = "parliamentary"
    GOVERNMENT_PORTAL = "government_portal"
    GOVERNMENT_DATA = "government_data"
    UNVERIFIED = "unverified"

    @property
    def weight(self) -> float:
        """Numerical trust weight for this tier."""
        return _TRUST_WEIGHTS[self]


_TRUST_WEIGHTS: dict[TrustLevel, float] = {
    TrustLevel.OFFICIAL_GAZETTE: 1.0,
    TrustLevel.LEGISLATION: 0.9,
    TrustLevel.PARLIAMENTARY: 0.85,
    TrustLevel.GOVERNMENT_PORTAL: 0.7,
    TrustLevel.GOVERNMENT_DATA: 0.5,
    TrustLevel.UNVERIFIED: 0.0,
}


class ChangeType(StrEnum):
    """Type of change detected in a scheme."""

    __slots__ = ()

    CREATED = "created"
    UPDATED = "updated"
    BENEFITS_CHANGED = "benefits_changed"
    ELIGIBILITY_CHANGED = "eligibility_changed"
    REVOKED = "revoked"
    EXTENDED = "extended"
    AMOUNT_CHANGED = "amount_changed"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class VerificationEvidence(BaseModel):
    """A single piece of evidence from a government source.

    Each evidence record captures exactly one document (or API response)
    that either confirms or contradicts the existence / details of a
    scheme.  The ``excerpt`` field stores the first 500 characters of
    the source document for quick human review.
    """

    source: VerificationSource
    source_url: str
    document_type: str
    document_id: str | None = None
    document_date: datetime | None = None
    title: str
    excerpt: str = Field(
        default="",
        max_length=500,
        description="First 500 characters of the source document.",
    )
    trust_weight: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Numerical trust weight assigned to this evidence.",
    )
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    verified_by: str = Field(
        default="auto_pipeline",
        description='Either "auto_pipeline" or "manual_review".',
    )
    raw_metadata: dict[str, object] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    """Aggregated verification result for a single scheme.

    Combines evidence from multiple government sources into an overall
    trust score and status.  The ``reverification_interval_hours``
    default of 168 hours (one week) ensures schemes are re-checked
    regularly even when no gazette update is detected.
    """

    scheme_id: str
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    trust_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Aggregate trust score (0 = no evidence, 1 = gazette-confirmed).",
    )
    evidences: list[VerificationEvidence] = Field(default_factory=list)
    sources_checked: list[VerificationSource] = Field(default_factory=list)
    sources_confirmed: list[VerificationSource] = Field(default_factory=list)
    verification_started_at: datetime | None = None
    verification_completed_at: datetime | None = None
    last_reverification_at: datetime | None = None
    reverification_interval_hours: int = Field(
        default=168,
        description="Hours between automatic re-verification checks (default: weekly).",
    )
    notes: list[str] = Field(default_factory=list)

    # Optional fields populated when specific sources confirm the scheme
    gazette_notification_number: str | None = None
    enabling_act: str | None = None
    parliamentary_session: str | None = None


class VerificationSummary(BaseModel):
    """Lightweight summary of a scheme's verification state.

    Intended for list views and dashboard cards where the full
    ``VerificationResult`` payload would be excessive.
    """

    scheme_id: str
    scheme_name: str
    status: VerificationStatus
    trust_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_count: int = 0
    last_verified: datetime | None = None
    gazette_confirmed: bool = False
    act_confirmed: bool = False
    parliament_confirmed: bool = False


class SchemeChangelog(BaseModel):
    """Record of a detected change in scheme details.

    The verification pipeline emits a changelog entry every time it
    detects a difference between the previously stored scheme data and
    the latest evidence from a government source.
    """

    scheme_id: str
    change_type: ChangeType
    field_changed: str
    old_value: str | None = None
    new_value: str | None = None
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: VerificationSource | None = None
    verified: bool = False


class VerificationDashboardStats(BaseModel):
    """Aggregate statistics for the verification dashboard.

    Provides a snapshot of the overall health of the scheme database
    and the operational status of each verification source.
    """

    total_schemes: int = 0
    verified_count: int = 0
    partially_verified_count: int = 0
    unverified_count: int = 0
    disputed_count: int = 0
    average_trust_score: float = Field(default=0.0, ge=0.0, le=1.0)
    last_pipeline_run: datetime | None = None
    sources_status: dict[str, bool] = Field(
        default_factory=dict,
        description="Mapping of source name to online/offline status.",
    )
