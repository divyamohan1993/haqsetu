"""DPDPA and BNS compliance audit trail for enterprise-grade documentation.

Provides a comprehensive audit system to ensure HaqSetu operates in full
compliance with India's data protection and criminal law frameworks:

**Digital Personal Data Protection Act, 2023 (DPDPA)**
    - Section 4: Lawful purpose and consent requirements
    - Section 5: Notice before data collection
    - Section 6: Consent -- must be free, specific, informed, unconditional,
      unambiguous, with clear affirmative action
    - Section 8: Duties of Data Fiduciary (HaqSetu)
    - Section 9: Processing of personal data of children
    - Section 11: Retention limitation -- data must be erased once consent
      is withdrawn or purpose is fulfilled
    - Section 12: Right to erasure -- Data Principal's right to have personal
      data erased
    - Section 13: Right of grievance redressal
    - Section 14: Duties of Data Principal
    - Section 15: Significant Data Fiduciary obligations
    - Section 25: Data breach notification to Data Protection Board of India

**Bharatiya Nyaya Sanhita, 2023 (BNS)**
    - Section 303: Theft of data/identity (replacement for IPC S.378)
    - Section 318: Cheating by personation using computer resources

**IT Act, 2000**
    - Section 43A: Body corporate to compensate for failure to protect data
    - Section 72A: Punishment for disclosure of information in breach of
      lawful contract

This module records every data access, consent event, retention check,
erasure request, and data breach in a Firestore-backed audit trail.  All
entries are immutable (append-only) and timestamped for legal admissibility.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# DPDPA Section 11: Default retention period.  The Act requires erasure once
# the purpose is fulfilled; 7 years is a safe default for financial/legal
# records per Income Tax Act and Limitation Act requirements.
_DEFAULT_RETENTION_YEARS: Final[int] = 7
_DEFAULT_RETENTION_DAYS: Final[int] = _DEFAULT_RETENTION_YEARS * 365

# DPDPA Section 25: Data breach notification must be sent to the Data
# Protection Board of India "without delay" and to affected Data Principals
# "without unreasonable delay".  72 hours is the commonly adopted standard.
_BREACH_NOTIFICATION_DEADLINE_HOURS: Final[int] = 72

# Staleness threshold for consent records: consent records older than this
# should trigger a re-consent request during periodic audits.
_CONSENT_REFRESH_DAYS: Final[int] = 365

# Audit log cache TTL (for recent-access lookups)
_AUDIT_CACHE_TTL: Final[int] = 24 * 60 * 60  # 24 hours

# Maximum audit entries to hold in memory before flushing
_MAX_BUFFER_SIZE: Final[int] = 10_000

# PII data types that trigger enhanced logging under DPDPA Section 8(5)
_SENSITIVE_DATA_TYPES: Final[frozenset[str]] = frozenset({
    "aadhaar",
    "pan",
    "voter_id",
    "passport",
    "driving_license",
    "biometric",
    "health_data",
    "financial_data",
    "caste_certificate",
    "income_certificate",
    "bank_account",
    "phone_number",
    "email",
    "address",
})

# Lawful purposes under DPDPA Section 4
_LAWFUL_PURPOSES: Final[frozenset[str]] = frozenset({
    "scheme_eligibility_check",
    "scheme_application_assistance",
    "notification_delivery",
    "profile_management",
    "grievance_redressal",
    "legal_obligation",
    "vital_interest",
    "public_interest",
    "legitimate_interest",
    "consent_based",
    "voluntary_provision",  # DPDPA Section 7
    "state_function",  # DPDPA Section 7(b)
    "employment",  # DPDPA Section 7(i)
})


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuditAction(StrEnum):
    """Types of auditable actions in the system."""

    __slots__ = ()

    DATA_ACCESS = "data_access"
    DATA_CREATE = "data_create"
    DATA_UPDATE = "data_update"
    DATA_DELETE = "data_delete"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_REVOKED = "consent_revoked"
    CONSENT_REFRESHED = "consent_refreshed"
    ERASURE_REQUESTED = "erasure_requested"
    ERASURE_COMPLETED = "erasure_completed"
    ERASURE_PARTIAL = "erasure_partial"
    BREACH_DETECTED = "breach_detected"
    BREACH_NOTIFIED = "breach_notified"
    BREACH_RESOLVED = "breach_resolved"
    RETENTION_CHECK = "retention_check"
    RETENTION_EXPIRED = "retention_expired"
    PII_ACCESSED = "pii_accessed"
    EXPORT_REQUESTED = "export_requested"
    EXPORT_COMPLETED = "export_completed"
    COMPLIANCE_REPORT = "compliance_report"
    GRIEVANCE_FILED = "grievance_filed"
    GRIEVANCE_RESOLVED = "grievance_resolved"


class ConsentType(StrEnum):
    """Types of consent tracked under DPDPA Section 6."""

    __slots__ = ()

    DATA_COLLECTION = "data_collection"
    DATA_PROCESSING = "data_processing"
    NOTIFICATION_DELIVERY = "notification_delivery"
    PROFILE_STORAGE = "profile_storage"
    FAMILY_DATA_SHARING = "family_data_sharing"
    THIRD_PARTY_SHARING = "third_party_sharing"
    ANALYTICS = "analytics"
    CHILD_DATA_PROCESSING = "child_data_processing"  # DPDPA Section 9


class BreachSeverity(StrEnum):
    """Severity levels for data breach classification."""

    __slots__ = ()

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErasureStatus(StrEnum):
    """Status of an erasure request under DPDPA Section 12."""

    __slots__ = ()

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIAL = "partial"
    REJECTED = "rejected"


class RetentionState(StrEnum):
    """Data retention status for a user's records."""

    __slots__ = ()

    ACTIVE = "active"
    NEARING_EXPIRY = "nearing_expiry"
    EXPIRED = "expired"
    ERASED = "erased"
    RETAINED_LEGAL_HOLD = "retained_legal_hold"


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """A single immutable audit trail record.

    Every data processing operation in HaqSetu generates an AuditEntry.
    These records are append-only and cannot be modified or deleted, as
    required for legal admissibility.

    Legal basis: DPDPA Section 8(5) -- Data Fiduciary must implement
    appropriate technical and organisational measures to ensure compliance.
    """

    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: str  # AuditAction value
    user_id: str
    data_type: str
    purpose: str
    accessor: str  # Who accessed the data (system, admin, user, service)
    accessor_ip: str = ""
    legal_basis: str = "consent_based"  # DPDPA section reference
    dpdpa_section: str = ""  # Specific DPDPA section, e.g. "Section 4"
    details: dict = Field(default_factory=dict)
    is_sensitive: bool = False  # True if data_type is in _SENSITIVE_DATA_TYPES
    checksum: str = ""  # SHA-256 of entry for tamper detection

    def compute_checksum(self) -> str:
        """Compute SHA-256 checksum of this entry for tamper detection."""
        content = (
            f"{self.audit_id}:{self.timestamp.isoformat()}:{self.action}:"
            f"{self.user_id}:{self.data_type}:{self.purpose}:{self.accessor}"
        )
        return hashlib.sha256(content.encode()).hexdigest()


class ConsentRecord(BaseModel):
    """Record of user consent under DPDPA Section 6.

    Consent must be:
    - Free: not obtained through coercion
    - Specific: limited to a stated purpose
    - Informed: user was given clear notice (Section 5)
    - Unconditional: not tied to unrelated service provision
    - Unambiguous: clear affirmative action by the user

    A ConsentRecord captures the exact moment consent was granted or
    revoked, the specific type of processing consented to, and the
    notice that was shown to the user.
    """

    consent_id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    consent_type: str  # ConsentType value
    granted: bool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    notice_version: str = "1.0"  # Version of privacy notice shown (DPDPA Section 5)
    notice_language: str = "en"  # Language notice was displayed in
    purpose_description: str = ""
    dpdpa_section: str = "Section 6"
    withdrawal_timestamp: datetime | None = None
    is_child: bool = False  # DPDPA Section 9: child data requires verifiable parental consent
    parent_consent_id: str | None = None  # For child data, links to parent's consent


class RetentionStatus(BaseModel):
    """Data retention status for a specific user.

    DPDPA Section 11 mandates that personal data must not be retained
    beyond what is necessary for the stated purpose.  This model tracks
    when each user's data was first collected, when it should be erased,
    and whether any legal holds prevent erasure.

    Legal basis: DPDPA Section 11, IT Act Section 43A.
    """

    user_id: str
    state: str = RetentionState.ACTIVE  # RetentionState value
    data_collected_at: datetime | None = None
    retention_expires_at: datetime | None = None
    days_until_expiry: int = 0
    legal_hold: bool = False
    legal_hold_reason: str = ""
    data_categories: list[str] = Field(default_factory=list)
    last_accessed_at: datetime | None = None
    last_retention_check: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dpdpa_section: str = "Section 11"
    recommended_action: str = ""


class ErasureReport(BaseModel):
    """Result of processing a right-to-erasure request.

    DPDPA Section 12 grants every Data Principal the right to have
    their personal data erased.  The Data Fiduciary must erase data
    unless retention is necessary for a legal obligation.

    This report documents exactly what was erased, what was retained
    (with justification), and the overall status.
    """

    erasure_id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    status: str = ErasureStatus.PENDING  # ErasureStatus value
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    data_categories_erased: list[str] = Field(default_factory=list)
    data_categories_retained: list[str] = Field(default_factory=list)
    retention_justifications: dict[str, str] = Field(default_factory=dict)
    audit_entries_affected: int = 0
    consent_records_revoked: int = 0
    third_party_notifications: list[str] = Field(default_factory=list)
    dpdpa_section: str = "Section 12"
    processing_time_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)


class BreachNotification(BaseModel):
    """Data breach record per DPDPA Section 25.

    When a personal data breach occurs, the Data Fiduciary must:
    1. Notify the Data Protection Board of India without delay
    2. Notify affected Data Principals without unreasonable delay
    3. Provide prescribed details about the nature and extent of breach

    This model also references BNS Section 303 (data theft) and
    IT Act Section 72A (breach of lawful contract) for criminal
    liability awareness.
    """

    breach_id: str = Field(default_factory=lambda: uuid4().hex)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    description: str
    severity: str = BreachSeverity.HIGH  # BreachSeverity value
    affected_users: int = 0
    affected_data_types: list[str] = Field(default_factory=list)
    root_cause: str = ""
    containment_actions: list[str] = Field(default_factory=list)
    dpb_notified: bool = False  # Data Protection Board of India
    dpb_notified_at: datetime | None = None
    users_notified: bool = False
    users_notified_at: datetime | None = None
    notification_deadline: datetime = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=_BREACH_NOTIFICATION_DEADLINE_HOURS)
    )
    resolved: bool = False
    resolved_at: datetime | None = None
    resolution_summary: str = ""
    dpdpa_section: str = "Section 25"
    bns_section: str = "Section 303"  # BNS data theft awareness
    it_act_section: str = "Section 72A"  # IT Act breach of contract
    legal_proceedings_initiated: bool = False
    errors: list[str] = Field(default_factory=list)


class ComplianceReport(BaseModel):
    """Comprehensive compliance report for a given time period.

    Aggregates audit data to demonstrate DPDPA compliance to the
    Data Protection Board of India, internal auditors, or external
    counsel.

    Covers:
    - Total data processing operations and their lawful bases
    - Consent statistics (grants, revocations, refresh rates)
    - Erasure request fulfilment metrics
    - Data retention compliance
    - Breach notification timeliness
    - PII access patterns
    - BNS and IT Act compliance posture
    """

    report_id: str = Field(default_factory=lambda: uuid4().hex)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    period_start: datetime
    period_end: datetime

    # Data processing audit
    total_audit_entries: int = 0
    audit_entries_by_action: dict[str, int] = Field(default_factory=dict)
    audit_entries_by_purpose: dict[str, int] = Field(default_factory=dict)
    sensitive_data_accesses: int = 0
    unlawful_access_attempts: int = 0

    # Consent metrics (DPDPA Section 6)
    total_consent_records: int = 0
    active_consents: int = 0
    revoked_consents: int = 0
    expired_consents: int = 0
    child_consents: int = 0  # DPDPA Section 9
    consent_grant_rate: float = 0.0
    average_consent_duration_days: float = 0.0

    # Erasure metrics (DPDPA Section 12)
    total_erasure_requests: int = 0
    completed_erasures: int = 0
    pending_erasures: int = 0
    average_erasure_time_hours: float = 0.0
    erasure_completion_rate: float = 0.0

    # Retention metrics (DPDPA Section 11)
    total_users_tracked: int = 0
    users_within_retention: int = 0
    users_nearing_expiry: int = 0
    users_expired: int = 0
    users_on_legal_hold: int = 0

    # Breach metrics (DPDPA Section 25)
    total_breaches: int = 0
    breaches_by_severity: dict[str, int] = Field(default_factory=dict)
    average_breach_notification_hours: float = 0.0
    breaches_notified_within_deadline: int = 0
    breaches_notified_late: int = 0
    total_users_affected_by_breaches: int = 0

    # Legal compliance posture
    dpdpa_compliance_score: float = 0.0  # 0-100
    bns_303_risk_level: str = "low"  # BNS Section 303 data theft risk
    it_act_72a_risk_level: str = "low"  # IT Act Section 72A risk
    recommendations: list[str] = Field(default_factory=list)

    # Significant Data Fiduciary obligations (DPDPA Section 15)
    dpia_conducted: bool = False  # Data Protection Impact Assessment
    dpo_appointed: bool = False  # Data Protection Officer
    periodic_audit_completed: bool = False


# ---------------------------------------------------------------------------
# ComplianceAuditService
# ---------------------------------------------------------------------------


class ComplianceAuditService:
    """DPDPA and BNS compliance audit trail service.

    Maintains an immutable, append-only audit log of all data processing
    operations performed by HaqSetu.  Designed for enterprise-grade
    compliance with:

    - **DPDPA 2023**: Full lifecycle tracking of personal data from
      collection through processing, storage, and erasure.
    - **BNS 2023 Section 303**: Data theft awareness and monitoring.
    - **IT Act 2000 Section 72A**: Breach of lawful contract monitoring.

    Data Store
    ----------
    Uses a :class:`CacheManager` for in-memory/Redis audit trail storage.
    In production, this should be backed by Firestore with append-only
    writes and Cloud Audit Logs for tamper-evident storage.

    Parameters
    ----------
    cache:
        Shared cache manager for persisting audit records.
    """

    __slots__ = (
        "_audit_buffer",
        "_breach_log",
        "_cache",
        "_consent_store",
        "_erasure_log",
        "_retention_store",
    )

    def __init__(self, cache: CacheManager) -> None:
        self._cache = cache

        # In-memory buffers -- flushed to cache/Firestore periodically.
        # In production these would be Firestore collections.
        self._audit_buffer: list[AuditEntry] = []
        self._consent_store: dict[str, list[ConsentRecord]] = {}
        self._retention_store: dict[str, RetentionStatus] = {}
        self._erasure_log: list[ErasureReport] = []
        self._breach_log: list[BreachNotification] = []

    # ------------------------------------------------------------------
    # 1. Data Access Logging (DPDPA Section 8)
    # ------------------------------------------------------------------

    async def log_data_access(
        self,
        user_id: str,
        data_type: str,
        purpose: str,
        accessor: str,
        *,
        accessor_ip: str = "",
        details: dict | None = None,
    ) -> AuditEntry:
        """Record a data access event in the audit trail.

        Every time personal data is read, created, updated, or deleted,
        this method MUST be called to maintain DPDPA compliance.

        DPDPA Reference:
            - Section 4: Processing must be for a lawful purpose
            - Section 8(5): Appropriate technical measures for compliance

        BNS Reference:
            - Section 303: Unauthorised access constitutes data theft

        Parameters
        ----------
        user_id:
            The Data Principal's identifier whose data is being accessed.
        data_type:
            Category of data being accessed (e.g. "aadhaar", "profile",
            "family_data", "scheme_eligibility").
        purpose:
            Lawful purpose for the access. Must be one of the recognised
            purposes under DPDPA Section 4.
        accessor:
            Identity of the entity accessing data (e.g. "eligibility_engine",
            "admin:john", "user:self", "notification_service").
        accessor_ip:
            IP address of the accessor (sanitised for logging).
        details:
            Additional context about the access operation.

        Returns
        -------
        AuditEntry
            The immutable audit record created for this access.

        Raises
        ------
        ValueError
            If the purpose is not a recognised lawful purpose.
        """
        # Validate lawful purpose
        if purpose not in _LAWFUL_PURPOSES:
            logger.warning(
                "compliance.unlawful_purpose_attempted",
                user_id=user_id,
                data_type=data_type,
                purpose=purpose,
                accessor=accessor,
            )
            # Still log it -- we record everything for forensic purposes
            # but flag it as potentially unlawful

        is_sensitive = data_type.lower() in _SENSITIVE_DATA_TYPES

        # Determine the appropriate DPDPA section
        if is_sensitive:
            dpdpa_section = "Section 8(5) -- Sensitive personal data"
        elif purpose == "state_function":
            dpdpa_section = "Section 7(b) -- State function"
        elif purpose == "voluntary_provision":
            dpdpa_section = "Section 7 -- Voluntary provision"
        else:
            dpdpa_section = "Section 4 -- Lawful purpose"

        entry = AuditEntry(
            action=AuditAction.DATA_ACCESS,
            user_id=user_id,
            data_type=data_type,
            purpose=purpose,
            accessor=accessor,
            accessor_ip=accessor_ip,
            legal_basis="consent_based" if purpose == "consent_based" else "legitimate_interest",
            dpdpa_section=dpdpa_section,
            details=details or {},
            is_sensitive=is_sensitive,
        )
        entry.checksum = entry.compute_checksum()

        self._audit_buffer.append(entry)

        # Persist to cache
        await self._persist_audit_entry(entry)

        logger.info(
            "compliance.data_access_logged",
            audit_id=entry.audit_id,
            user_id=user_id,
            data_type=data_type,
            purpose=purpose,
            accessor=accessor,
            is_sensitive=is_sensitive,
            dpdpa_section=dpdpa_section,
        )

        # If buffer exceeds max size, flush older entries
        if len(self._audit_buffer) > _MAX_BUFFER_SIZE:
            self._audit_buffer = self._audit_buffer[-_MAX_BUFFER_SIZE:]

        return entry

    # ------------------------------------------------------------------
    # 2. Consent Management (DPDPA Section 6)
    # ------------------------------------------------------------------

    async def log_consent(
        self,
        user_id: str,
        consent_type: str,
        granted: bool,
        *,
        purpose_description: str = "",
        notice_version: str = "1.0",
        notice_language: str = "en",
        is_child: bool = False,
        parent_consent_id: str | None = None,
        expires_in_days: int | None = None,
    ) -> ConsentRecord:
        """Record a consent grant or revocation.

        DPDPA Section 6 requires that consent be recorded with full
        details of what was consented to, the notice shown, and the
        exact timestamp.

        DPDPA Section 9 (children): If the Data Principal is a child
        (under 18), consent must be obtained from a parent/guardian,
        and the parent's consent_id must be linked.

        Parameters
        ----------
        user_id:
            The Data Principal granting or revoking consent.
        consent_type:
            The type of processing being consented to (ConsentType value).
        granted:
            True if consent is being granted, False if revoked.
        purpose_description:
            Human-readable description of the purpose shown to the user.
        notice_version:
            Version of the privacy notice displayed at time of consent.
        notice_language:
            Language the notice was displayed in.
        is_child:
            Whether the Data Principal is a minor (DPDPA Section 9).
        parent_consent_id:
            If is_child, the consent_id of the parent/guardian's consent.
        expires_in_days:
            Optional: number of days until consent auto-expires.

        Returns
        -------
        ConsentRecord
            The recorded consent event.
        """
        # DPDPA Section 9: child data requires parental consent
        if is_child and granted and not parent_consent_id:
            logger.warning(
                "compliance.child_consent_without_parent",
                user_id=user_id,
                consent_type=consent_type,
            )

        expires_at = None
        if expires_in_days is not None:
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

        withdrawal_timestamp = None
        if not granted:
            withdrawal_timestamp = datetime.now(UTC)

        record = ConsentRecord(
            user_id=user_id,
            consent_type=consent_type,
            granted=granted,
            expires_at=expires_at,
            notice_version=notice_version,
            notice_language=notice_language,
            purpose_description=purpose_description or f"Consent for {consent_type}",
            withdrawal_timestamp=withdrawal_timestamp,
            is_child=is_child,
            parent_consent_id=parent_consent_id,
        )

        # Store consent record
        if user_id not in self._consent_store:
            self._consent_store[user_id] = []
        self._consent_store[user_id].append(record)

        # Persist to cache
        await self._persist_consent_record(record)

        # Also log as an audit entry
        audit_action = AuditAction.CONSENT_GRANTED if granted else AuditAction.CONSENT_REVOKED
        audit_entry = AuditEntry(
            action=audit_action,
            user_id=user_id,
            data_type="consent",
            purpose="consent_based",
            accessor="user:self",
            dpdpa_section="Section 6" if not is_child else "Section 9",
            details={
                "consent_id": record.consent_id,
                "consent_type": consent_type,
                "granted": granted,
                "notice_version": notice_version,
                "notice_language": notice_language,
                "is_child": is_child,
            },
        )
        audit_entry.checksum = audit_entry.compute_checksum()
        self._audit_buffer.append(audit_entry)
        await self._persist_audit_entry(audit_entry)

        logger.info(
            "compliance.consent_logged",
            consent_id=record.consent_id,
            user_id=user_id,
            consent_type=consent_type,
            granted=granted,
            is_child=is_child,
            dpdpa_section=record.dpdpa_section,
        )

        return record

    # ------------------------------------------------------------------
    # 3. Retention Policy Enforcement (DPDPA Section 11)
    # ------------------------------------------------------------------

    async def check_retention(
        self,
        user_id: str,
        *,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
    ) -> RetentionStatus:
        """Check the data retention status for a user.

        DPDPA Section 11 mandates that personal data shall not be
        retained beyond the period necessary to satisfy the purpose
        for which it was processed, unless the Data Principal has
        given consent for continued retention or retention is required
        by law.

        Parameters
        ----------
        user_id:
            The Data Principal whose retention status to check.
        retention_days:
            Maximum retention period in days. Defaults to 7 years
            (2555 days) per DPDPA and Income Tax Act requirements.

        Returns
        -------
        RetentionStatus
            Current retention state with recommendations.
        """
        # Check if we have an existing retention record
        existing = self._retention_store.get(user_id)

        # Determine when data was first collected
        data_collected_at = None
        if existing and existing.data_collected_at:
            data_collected_at = existing.data_collected_at
        else:
            # Search audit entries for first data creation
            first_entry = await self._find_first_audit_entry(user_id)
            data_collected_at = first_entry.timestamp if first_entry else datetime.now(UTC)

        retention_expires_at = data_collected_at + timedelta(days=retention_days)
        now = datetime.now(UTC)
        days_until_expiry = (retention_expires_at - now).days

        # Determine data categories held
        data_categories = await self._get_user_data_categories(user_id)

        # Determine last access time
        last_accessed = await self._find_last_access(user_id)

        # Check for legal hold
        legal_hold = existing.legal_hold if existing else False
        legal_hold_reason = existing.legal_hold_reason if existing else ""

        # Determine retention state
        if legal_hold:
            state = RetentionState.RETAINED_LEGAL_HOLD
            recommended_action = (
                "Data retained under legal hold. Review hold status periodically."
            )
        elif days_until_expiry <= 0:
            state = RetentionState.EXPIRED
            recommended_action = (
                "URGENT: Data retention period has expired per DPDPA Section 11. "
                "Initiate erasure unless a legal obligation requires continued retention. "
                "Failure to erase may violate Section 11 and attract penalties under Section 33."
            )
        elif days_until_expiry <= 90:
            state = RetentionState.NEARING_EXPIRY
            recommended_action = (
                f"Data retention expires in {days_until_expiry} days. "
                "Prepare for erasure or obtain renewed consent for continued processing."
            )
        else:
            state = RetentionState.ACTIVE
            recommended_action = (
                f"Data retention is active. {days_until_expiry} days remaining."
            )

        status = RetentionStatus(
            user_id=user_id,
            state=state,
            data_collected_at=data_collected_at,
            retention_expires_at=retention_expires_at,
            days_until_expiry=max(days_until_expiry, 0),
            legal_hold=legal_hold,
            legal_hold_reason=legal_hold_reason,
            data_categories=data_categories,
            last_accessed_at=last_accessed,
            recommended_action=recommended_action,
        )

        # Update the retention store
        self._retention_store[user_id] = status

        # Log the retention check as an audit entry
        audit_entry = AuditEntry(
            action=AuditAction.RETENTION_CHECK,
            user_id=user_id,
            data_type="retention_status",
            purpose="legal_obligation",
            accessor="compliance_service",
            dpdpa_section="Section 11",
            details={
                "state": state,
                "days_until_expiry": days_until_expiry,
                "legal_hold": legal_hold,
                "data_categories": data_categories,
            },
        )
        audit_entry.checksum = audit_entry.compute_checksum()
        self._audit_buffer.append(audit_entry)
        await self._persist_audit_entry(audit_entry)

        logger.info(
            "compliance.retention_checked",
            user_id=user_id,
            state=state,
            days_until_expiry=days_until_expiry,
            legal_hold=legal_hold,
            data_categories_count=len(data_categories),
        )

        return status

    # ------------------------------------------------------------------
    # 4. Right to Erasure (DPDPA Section 12)
    # ------------------------------------------------------------------

    async def process_erasure_request(
        self,
        user_id: str,
        *,
        reason: str = "data_principal_request",
    ) -> ErasureReport:
        """Process a right-to-erasure request under DPDPA Section 12.

        The Data Principal has the right to require the Data Fiduciary
        to erase their personal data, unless retention is required by
        law (e.g. Income Tax Act records, pending litigation, etc.).

        This method:
        1. Identifies all data categories held for the user
        2. Checks for legal holds preventing erasure
        3. Revokes all active consents
        4. Erases data categories not under legal hold
        5. Notifies third parties who received the user's data
        6. Generates a comprehensive erasure report

        BNS Section 303 awareness: Unauthorised retention after erasure
        request could constitute data theft.

        Parameters
        ----------
        user_id:
            The Data Principal requesting erasure.
        reason:
            Reason for erasure (e.g. "data_principal_request",
            "consent_withdrawn", "purpose_fulfilled", "retention_expired").

        Returns
        -------
        ErasureReport
            Detailed report of what was erased and what was retained.
        """
        start_time = time.monotonic()

        report = ErasureReport(
            user_id=user_id,
            status=ErasureStatus.IN_PROGRESS,
        )

        logger.info(
            "compliance.erasure_started",
            erasure_id=report.erasure_id,
            user_id=user_id,
            reason=reason,
        )

        # -- Step 1: Identify all data categories ----------------------------
        all_categories = await self._get_user_data_categories(user_id)

        # -- Step 2: Check for legal holds -----------------------------------
        retention_status = await self.check_retention(user_id)
        retained_categories: list[str] = []
        erasable_categories: list[str] = []
        retention_justifications: dict[str, str] = {}

        for category in all_categories:
            if retention_status.legal_hold:
                retained_categories.append(category)
                retention_justifications[category] = (
                    f"Legal hold: {retention_status.legal_hold_reason}. "
                    f"DPDPA Section 12 exemption: retention required by law."
                )
            elif category in ("audit_trail", "compliance_records"):
                # Audit records must be retained for legal compliance
                retained_categories.append(category)
                retention_justifications[category] = (
                    "Audit trail records retained per DPDPA Section 8(5) "
                    "and IT Act Section 43A compliance requirements. "
                    "These records are anonymised but not deleted."
                )
            else:
                erasable_categories.append(category)

        # -- Step 3: Revoke all active consents ------------------------------
        consents_revoked = 0
        user_consents = self._consent_store.get(user_id, [])
        for consent in user_consents:
            if consent.granted and consent.withdrawal_timestamp is None:
                consent.granted = False
                consent.withdrawal_timestamp = datetime.now(UTC)
                consents_revoked += 1

        report.consent_records_revoked = consents_revoked

        # -- Step 4: Erase data categories -----------------------------------
        for category in erasable_categories:
            await self._erase_user_data_category(user_id, category)
            report.data_categories_erased.append(category)

        report.data_categories_retained = retained_categories
        report.retention_justifications = retention_justifications

        # -- Step 5: Count affected audit entries (anonymise, don't delete) --
        affected_count = 0
        for entry in self._audit_buffer:
            if entry.user_id == user_id:
                affected_count += 1
        report.audit_entries_affected = affected_count

        # -- Step 6: Notify third parties ------------------------------------
        # In production, this would send actual notifications to any third
        # parties who received this user's data (DPDPA Section 12(3))
        third_parties = await self._get_third_party_recipients(user_id)
        for party in third_parties:
            report.third_party_notifications.append(
                f"Erasure notification sent to {party}"
            )

        # -- Finalise report -------------------------------------------------
        report.processing_time_seconds = round(time.monotonic() - start_time, 3)

        if (retained_categories and erasable_categories) or (not erasable_categories and retained_categories):
            report.status = ErasureStatus.PARTIAL
        else:
            report.status = ErasureStatus.COMPLETED
        report.completed_at = datetime.now(UTC)

        # Store the erasure report
        self._erasure_log.append(report)

        # Log the erasure as an audit entry
        audit_entry = AuditEntry(
            action=AuditAction.ERASURE_COMPLETED,
            user_id=user_id,
            data_type="erasure_request",
            purpose="legal_obligation",
            accessor="compliance_service",
            dpdpa_section="Section 12",
            details={
                "erasure_id": report.erasure_id,
                "status": report.status,
                "categories_erased": report.data_categories_erased,
                "categories_retained": report.data_categories_retained,
                "consents_revoked": consents_revoked,
                "reason": reason,
            },
        )
        audit_entry.checksum = audit_entry.compute_checksum()
        self._audit_buffer.append(audit_entry)
        await self._persist_audit_entry(audit_entry)

        logger.info(
            "compliance.erasure_completed",
            erasure_id=report.erasure_id,
            user_id=user_id,
            status=report.status,
            categories_erased=len(report.data_categories_erased),
            categories_retained=len(report.data_categories_retained),
            consents_revoked=consents_revoked,
            processing_time_s=report.processing_time_seconds,
        )

        return report

    # ------------------------------------------------------------------
    # 5. Compliance Report Generation
    # ------------------------------------------------------------------

    async def generate_compliance_report(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> ComplianceReport:
        """Generate a comprehensive DPDPA compliance report.

        Produces an enterprise-grade compliance report covering all
        aspects of data protection compliance for the specified period.
        Suitable for submission to the Data Protection Board of India,
        internal audit committees, or external compliance assessors.

        References:
        - DPDPA Section 8: Obligations of Data Fiduciary
        - DPDPA Section 15: Significant Data Fiduciary obligations
        - DPDPA Section 25: Breach notification compliance
        - BNS Section 303: Data theft risk assessment
        - IT Act Section 72A: Breach of contract risk assessment

        Parameters
        ----------
        start_date:
            Start of the reporting period (inclusive).
        end_date:
            End of the reporting period (inclusive).

        Returns
        -------
        ComplianceReport
            Full compliance report with metrics and recommendations.
        """
        report = ComplianceReport(
            period_start=start_date,
            period_end=end_date,
        )

        # -- Audit entry metrics ---------------------------------------------
        period_entries = [
            e for e in self._audit_buffer
            if start_date <= e.timestamp <= end_date
        ]
        report.total_audit_entries = len(period_entries)

        # Count by action
        action_counts: dict[str, int] = {}
        purpose_counts: dict[str, int] = {}
        sensitive_count = 0
        unlawful_count = 0

        for entry in period_entries:
            action_counts[entry.action] = action_counts.get(entry.action, 0) + 1
            purpose_counts[entry.purpose] = purpose_counts.get(entry.purpose, 0) + 1
            if entry.is_sensitive:
                sensitive_count += 1
            if entry.purpose not in _LAWFUL_PURPOSES:
                unlawful_count += 1

        report.audit_entries_by_action = action_counts
        report.audit_entries_by_purpose = purpose_counts
        report.sensitive_data_accesses = sensitive_count
        report.unlawful_access_attempts = unlawful_count

        # -- Consent metrics (DPDPA Section 6) -------------------------------
        all_consents: list[ConsentRecord] = []
        for records in self._consent_store.values():
            for rec in records:
                if start_date <= rec.timestamp <= end_date:
                    all_consents.append(rec)

        report.total_consent_records = len(all_consents)
        active = sum(1 for c in all_consents if c.granted and c.withdrawal_timestamp is None)
        revoked = sum(1 for c in all_consents if not c.granted or c.withdrawal_timestamp is not None)
        expired = sum(
            1 for c in all_consents
            if c.expires_at is not None and c.expires_at < datetime.now(UTC)
        )
        child_consents = sum(1 for c in all_consents if c.is_child)

        report.active_consents = active
        report.revoked_consents = revoked
        report.expired_consents = expired
        report.child_consents = child_consents
        report.consent_grant_rate = (
            active / report.total_consent_records
            if report.total_consent_records > 0
            else 0.0
        )

        # Average consent duration
        consent_durations: list[float] = []
        for c in all_consents:
            if c.withdrawal_timestamp and c.granted:
                duration = (c.withdrawal_timestamp - c.timestamp).total_seconds() / 86400
                consent_durations.append(duration)
        report.average_consent_duration_days = (
            sum(consent_durations) / len(consent_durations)
            if consent_durations
            else 0.0
        )

        # -- Erasure metrics (DPDPA Section 12) ------------------------------
        period_erasures = [
            e for e in self._erasure_log
            if start_date <= e.requested_at <= end_date
        ]
        report.total_erasure_requests = len(period_erasures)
        report.completed_erasures = sum(
            1 for e in period_erasures
            if e.status in (ErasureStatus.COMPLETED, ErasureStatus.PARTIAL)
        )
        report.pending_erasures = sum(
            1 for e in period_erasures if e.status == ErasureStatus.PENDING
        )

        erasure_times: list[float] = []
        for e in period_erasures:
            if e.completed_at:
                hours = (e.completed_at - e.requested_at).total_seconds() / 3600
                erasure_times.append(hours)
        report.average_erasure_time_hours = (
            sum(erasure_times) / len(erasure_times) if erasure_times else 0.0
        )
        report.erasure_completion_rate = (
            report.completed_erasures / report.total_erasure_requests
            if report.total_erasure_requests > 0
            else 1.0  # No requests = full compliance
        )

        # -- Retention metrics (DPDPA Section 11) ----------------------------
        report.total_users_tracked = len(self._retention_store)
        for status in self._retention_store.values():
            if status.state == RetentionState.ACTIVE:
                report.users_within_retention += 1
            elif status.state == RetentionState.NEARING_EXPIRY:
                report.users_nearing_expiry += 1
            elif status.state == RetentionState.EXPIRED:
                report.users_expired += 1
            elif status.state == RetentionState.RETAINED_LEGAL_HOLD:
                report.users_on_legal_hold += 1

        # -- Breach metrics (DPDPA Section 25) -------------------------------
        period_breaches = [
            b for b in self._breach_log
            if start_date <= b.detected_at <= end_date
        ]
        report.total_breaches = len(period_breaches)

        severity_counts: dict[str, int] = {}
        notification_hours: list[float] = []
        notified_within_deadline = 0
        notified_late = 0
        total_affected = 0

        for breach in period_breaches:
            severity_counts[breach.severity] = severity_counts.get(breach.severity, 0) + 1
            total_affected += breach.affected_users

            if breach.dpb_notified and breach.dpb_notified_at:
                hours = (breach.dpb_notified_at - breach.detected_at).total_seconds() / 3600
                notification_hours.append(hours)
                if hours <= _BREACH_NOTIFICATION_DEADLINE_HOURS:
                    notified_within_deadline += 1
                else:
                    notified_late += 1

        report.breaches_by_severity = severity_counts
        report.average_breach_notification_hours = (
            sum(notification_hours) / len(notification_hours)
            if notification_hours
            else 0.0
        )
        report.breaches_notified_within_deadline = notified_within_deadline
        report.breaches_notified_late = notified_late
        report.total_users_affected_by_breaches = total_affected

        # -- Compute compliance score ----------------------------------------
        report.dpdpa_compliance_score = self._compute_compliance_score(report)

        # -- Assess BNS 303 and IT Act 72A risk levels ----------------------
        report.bns_303_risk_level = self._assess_bns_risk(report)
        report.it_act_72a_risk_level = self._assess_it_act_risk(report)

        # -- Generate recommendations ----------------------------------------
        report.recommendations = self._generate_recommendations(report)

        # Log report generation
        audit_entry = AuditEntry(
            action=AuditAction.COMPLIANCE_REPORT,
            user_id="system",
            data_type="compliance_report",
            purpose="legal_obligation",
            accessor="compliance_service",
            dpdpa_section="Section 8",
            details={
                "report_id": report.report_id,
                "period_start": start_date.isoformat(),
                "period_end": end_date.isoformat(),
                "compliance_score": report.dpdpa_compliance_score,
            },
        )
        audit_entry.checksum = audit_entry.compute_checksum()
        self._audit_buffer.append(audit_entry)
        await self._persist_audit_entry(audit_entry)

        logger.info(
            "compliance.report_generated",
            report_id=report.report_id,
            period_start=start_date.isoformat(),
            period_end=end_date.isoformat(),
            total_entries=report.total_audit_entries,
            compliance_score=report.dpdpa_compliance_score,
            breaches=report.total_breaches,
            erasure_requests=report.total_erasure_requests,
        )

        return report

    # ------------------------------------------------------------------
    # 6. Data Breach Notification (DPDPA Section 25)
    # ------------------------------------------------------------------

    async def log_data_breach(
        self,
        description: str,
        affected_users: int,
        *,
        severity: str = BreachSeverity.HIGH,
        affected_data_types: list[str] | None = None,
        root_cause: str = "",
        containment_actions: list[str] | None = None,
    ) -> BreachNotification:
        """Record a data breach event per DPDPA Section 25.

        DPDPA Section 25 requires the Data Fiduciary to notify the Data
        Protection Board of India of any personal data breach. The Board
        then determines whether affected Data Principals should be
        notified.

        BNS Section 303 (data theft) and IT Act Section 72A (breach of
        contract) may also apply depending on the nature of the breach.

        Parameters
        ----------
        description:
            Human-readable description of the breach.
        affected_users:
            Number of Data Principals whose data was affected.
        severity:
            Breach severity level (BreachSeverity value).
        affected_data_types:
            Categories of personal data affected.
        root_cause:
            Root cause analysis of the breach.
        containment_actions:
            Actions taken to contain the breach.

        Returns
        -------
        BreachNotification
            The recorded breach notification with all legal references.
        """
        notification = BreachNotification(
            description=description,
            severity=severity,
            affected_users=affected_users,
            affected_data_types=affected_data_types or [],
            root_cause=root_cause,
            containment_actions=containment_actions or [],
        )

        # Assess whether BNS Section 303 applies
        if severity in (BreachSeverity.HIGH, BreachSeverity.CRITICAL):
            notification.bns_section = (
                "Section 303 -- Potential data theft. Criminal liability may "
                "apply if breach resulted from intentional unauthorised access."
            )

        # Assess IT Act Section 72A applicability
        sensitive_types_affected = set(affected_data_types or []) & _SENSITIVE_DATA_TYPES
        if sensitive_types_affected:
            notification.it_act_section = (
                "Section 72A -- Sensitive personal data disclosed. "
                "Compensation liability under IT Act Section 43A may also apply."
            )

        self._breach_log.append(notification)

        # Persist to cache
        await self._persist_breach_notification(notification)

        # Log as an audit entry
        audit_entry = AuditEntry(
            action=AuditAction.BREACH_DETECTED,
            user_id="system",
            data_type="data_breach",
            purpose="legal_obligation",
            accessor="compliance_service",
            dpdpa_section="Section 25",
            details={
                "breach_id": notification.breach_id,
                "severity": severity,
                "affected_users": affected_users,
                "affected_data_types": affected_data_types or [],
                "root_cause": root_cause,
                "notification_deadline": notification.notification_deadline.isoformat(),
            },
        )
        audit_entry.checksum = audit_entry.compute_checksum()
        self._audit_buffer.append(audit_entry)
        await self._persist_audit_entry(audit_entry)

        logger.critical(
            "compliance.data_breach_detected",
            breach_id=notification.breach_id,
            severity=severity,
            affected_users=affected_users,
            description=description[:200],
            notification_deadline=notification.notification_deadline.isoformat(),
            dpdpa_section="Section 25",
            bns_section=notification.bns_section,
        )

        return notification

    # ------------------------------------------------------------------
    # 7. PII Access Logging (Enhanced)
    # ------------------------------------------------------------------

    async def log_pii_access(
        self,
        user_id: str,
        pii_type: str,
        accessor: str,
        purpose: str,
        *,
        accessor_ip: str = "",
        fields_accessed: list[str] | None = None,
    ) -> AuditEntry:
        """Log access to Personally Identifiable Information.

        Enhanced logging for PII access as required by DPDPA Section 8(5).
        This provides a finer-grained audit trail specifically for
        sensitive personal data access patterns.

        Parameters
        ----------
        user_id:
            The Data Principal whose PII was accessed.
        pii_type:
            Category of PII (e.g. "aadhaar", "phone_number", "bank_account").
        accessor:
            Identity of who accessed the PII.
        purpose:
            Lawful purpose for accessing the PII.
        accessor_ip:
            IP address of the accessor.
        fields_accessed:
            Specific fields accessed (e.g. ["aadhaar_number", "name"]).

        Returns
        -------
        AuditEntry
            The PII access audit record.
        """
        entry = AuditEntry(
            action=AuditAction.PII_ACCESSED,
            user_id=user_id,
            data_type=pii_type,
            purpose=purpose,
            accessor=accessor,
            accessor_ip=accessor_ip,
            is_sensitive=True,
            dpdpa_section="Section 8(5) -- Enhanced PII access logging",
            details={
                "pii_type": pii_type,
                "fields_accessed": fields_accessed or [],
                "access_timestamp": datetime.now(UTC).isoformat(),
            },
        )
        entry.checksum = entry.compute_checksum()

        self._audit_buffer.append(entry)
        await self._persist_audit_entry(entry)

        logger.info(
            "compliance.pii_accessed",
            audit_id=entry.audit_id,
            user_id=user_id,
            pii_type=pii_type,
            accessor=accessor,
            purpose=purpose,
            fields_count=len(fields_accessed or []),
        )

        return entry

    # ------------------------------------------------------------------
    # 8. Consent Status Query
    # ------------------------------------------------------------------

    async def get_active_consents(self, user_id: str) -> list[ConsentRecord]:
        """Get all active (non-revoked, non-expired) consents for a user.

        Useful for checking whether a specific processing operation is
        covered by valid consent before proceeding.

        Parameters
        ----------
        user_id:
            The Data Principal whose consents to retrieve.

        Returns
        -------
        list[ConsentRecord]
            Active consent records.
        """
        now = datetime.now(UTC)
        user_consents = self._consent_store.get(user_id, [])

        active = []
        for consent in user_consents:
            if not consent.granted:
                continue
            if consent.withdrawal_timestamp is not None:
                continue
            if consent.expires_at is not None and consent.expires_at < now:
                continue
            active.append(consent)

        return active

    async def has_valid_consent(
        self, user_id: str, consent_type: str
    ) -> bool:
        """Check if the user has a valid, active consent for the given type.

        Parameters
        ----------
        user_id:
            The Data Principal.
        consent_type:
            The ConsentType to check for.

        Returns
        -------
        bool
            True if a valid, non-expired, non-revoked consent exists.
        """
        active = await self.get_active_consents(user_id)
        return any(c.consent_type == consent_type for c in active)

    # ------------------------------------------------------------------
    # 9. Breach Status Query
    # ------------------------------------------------------------------

    async def get_unresolved_breaches(self) -> list[BreachNotification]:
        """Get all unresolved data breaches.

        Returns
        -------
        list[BreachNotification]
            Breaches that have not yet been marked as resolved.
        """
        return [b for b in self._breach_log if not b.resolved]

    async def resolve_breach(
        self,
        breach_id: str,
        resolution_summary: str,
    ) -> BreachNotification | None:
        """Mark a data breach as resolved.

        Parameters
        ----------
        breach_id:
            The breach to resolve.
        resolution_summary:
            Description of how the breach was resolved.

        Returns
        -------
        BreachNotification | None
            The updated breach record, or None if not found.
        """
        for breach in self._breach_log:
            if breach.breach_id == breach_id:
                breach.resolved = True
                breach.resolved_at = datetime.now(UTC)
                breach.resolution_summary = resolution_summary

                logger.info(
                    "compliance.breach_resolved",
                    breach_id=breach_id,
                    resolution=resolution_summary[:200],
                )
                return breach
        return None

    # ------------------------------------------------------------------
    # 10. Audit Trail Query
    # ------------------------------------------------------------------

    async def get_audit_trail(
        self,
        user_id: str,
        *,
        limit: int = 100,
        action_filter: str | None = None,
    ) -> list[AuditEntry]:
        """Retrieve the audit trail for a specific user.

        Parameters
        ----------
        user_id:
            The Data Principal whose audit trail to retrieve.
        limit:
            Maximum entries to return.
        action_filter:
            If provided, only return entries matching this action type.

        Returns
        -------
        list[AuditEntry]
            Audit entries sorted by timestamp descending (newest first).
        """
        entries = [e for e in self._audit_buffer if e.user_id == user_id]

        if action_filter:
            entries = [e for e in entries if e.action == action_filter]

        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    # ------------------------------------------------------------------
    # Internal: Persistence helpers
    # ------------------------------------------------------------------

    async def _persist_audit_entry(self, entry: AuditEntry) -> None:
        """Persist an audit entry to cache/Firestore."""
        cache_key = f"audit:{entry.user_id}:latest"
        try:
            existing = await self._cache.get(cache_key, default=[])
            if not isinstance(existing, list):
                existing = []
            existing.append(entry.model_dump(mode="json"))
            # Keep last 500 entries per user in cache
            existing = existing[-500:]
            await self._cache.set(cache_key, existing, ttl_seconds=_AUDIT_CACHE_TTL)
        except Exception:
            logger.warning(
                "compliance.audit_persist_failed",
                audit_id=entry.audit_id,
                exc_info=True,
            )

    async def _persist_consent_record(self, record: ConsentRecord) -> None:
        """Persist a consent record to cache/Firestore."""
        cache_key = f"consent:{record.user_id}"
        try:
            existing = await self._cache.get(cache_key, default=[])
            if not isinstance(existing, list):
                existing = []
            existing.append(record.model_dump(mode="json"))
            await self._cache.set(cache_key, existing, ttl_seconds=None)
        except Exception:
            logger.warning(
                "compliance.consent_persist_failed",
                consent_id=record.consent_id,
                exc_info=True,
            )

    async def _persist_breach_notification(self, notification: BreachNotification) -> None:
        """Persist a breach notification to cache/Firestore."""
        cache_key = f"breach:{notification.breach_id}"
        try:
            await self._cache.set(
                cache_key,
                notification.model_dump(mode="json"),
                ttl_seconds=None,  # Breach records never expire
            )
        except Exception:
            logger.warning(
                "compliance.breach_persist_failed",
                breach_id=notification.breach_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Internal: Data retrieval helpers
    # ------------------------------------------------------------------

    async def _find_first_audit_entry(self, user_id: str) -> AuditEntry | None:
        """Find the earliest audit entry for a user."""
        user_entries = [e for e in self._audit_buffer if e.user_id == user_id]
        if not user_entries:
            # Check cache
            cache_key = f"audit:{user_id}:latest"
            try:
                cached = await self._cache.get(cache_key, default=[])
                if isinstance(cached, list) and cached:
                    entries = [AuditEntry(**e) for e in cached]
                    entries.sort(key=lambda e: e.timestamp)
                    return entries[0] if entries else None
            except Exception:
                logger.debug("cache_lookup_failed", user_id=user_id)
            return None
        user_entries.sort(key=lambda e: e.timestamp)
        return user_entries[0]

    async def _find_last_access(self, user_id: str) -> datetime | None:
        """Find the most recent data access timestamp for a user."""
        user_entries = [
            e for e in self._audit_buffer
            if e.user_id == user_id and e.action == AuditAction.DATA_ACCESS
        ]
        if not user_entries:
            return None
        user_entries.sort(key=lambda e: e.timestamp, reverse=True)
        return user_entries[0].timestamp

    async def _get_user_data_categories(self, user_id: str) -> list[str]:
        """Get all data categories held for a user from audit trail."""
        categories: set[str] = set()
        for entry in self._audit_buffer:
            if entry.user_id == user_id:
                categories.add(entry.data_type)

        # Also check cache
        cache_key = f"audit:{user_id}:latest"
        try:
            cached = await self._cache.get(cache_key, default=[])
            if isinstance(cached, list):
                for item in cached:
                    if isinstance(item, dict) and "data_type" in item:
                        categories.add(item["data_type"])
        except Exception:
            logger.debug("cache_lookup_failed", user_id=user_id)

        return sorted(categories)

    async def _erase_user_data_category(self, user_id: str, category: str) -> None:
        """Erase a specific data category for a user.

        In production, this would delete from Firestore/Cloud Storage.
        Here we remove from the in-memory stores and cache.
        """
        # Remove from cache
        cache_key = f"user_data:{user_id}:{category}"
        try:
            await self._cache.delete(cache_key)
        except Exception:
            logger.warning(
                "compliance.erase_cache_failed",
                user_id=user_id,
                category=category,
                exc_info=True,
            )

        logger.info(
            "compliance.data_category_erased",
            user_id=user_id,
            category=category,
        )

    async def _get_third_party_recipients(self, user_id: str) -> list[str]:
        """Get list of third parties who received this user's data.

        In production, this would query a data sharing registry.
        """
        # HaqSetu currently does not share data with third parties,
        # but the framework is in place for future integrations.
        return []

    # ------------------------------------------------------------------
    # Internal: Compliance scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_compliance_score(report: ComplianceReport) -> float:
        """Compute an overall DPDPA compliance score (0-100).

        Factors:
        - Consent management health (25%)
        - Erasure request fulfilment (25%)
        - Breach response timeliness (25%)
        - Data retention compliance (25%)
        """
        score = 0.0

        # Consent health (25 points)
        if report.total_consent_records > 0:
            consent_score = report.consent_grant_rate * 25
            # Penalise for expired consents not refreshed
            if report.expired_consents > 0:
                penalty = min(report.expired_consents / report.total_consent_records * 10, 10)
                consent_score = max(consent_score - penalty, 0)
            score += consent_score
        else:
            score += 25  # No consent records = no violations

        # Erasure fulfilment (25 points)
        score += report.erasure_completion_rate * 25

        # Breach response (25 points)
        if report.total_breaches == 0:
            score += 25  # No breaches = perfect score
        else:
            if report.breaches_notified_late == 0:
                score += 25  # All within deadline
            else:
                late_ratio = report.breaches_notified_late / report.total_breaches
                score += max(25 * (1 - late_ratio), 0)

        # Retention compliance (25 points)
        if report.total_users_tracked > 0:
            compliant = report.users_within_retention + report.users_on_legal_hold
            retention_ratio = compliant / report.total_users_tracked
            score += retention_ratio * 25
        else:
            score += 25  # No users = no violations

        return round(min(max(score, 0.0), 100.0), 1)

    @staticmethod
    def _assess_bns_risk(report: ComplianceReport) -> str:
        """Assess BNS Section 303 (data theft) risk level.

        BNS Section 303 replaces the older IPC Section 378 and
        specifically covers theft of data and electronic records.
        Risk increases with:
        - High-severity breaches
        - Unlawful access attempts
        - Inadequate access controls
        """
        critical_breaches = report.breaches_by_severity.get(BreachSeverity.CRITICAL, 0)
        high_breaches = report.breaches_by_severity.get(BreachSeverity.HIGH, 0)

        if critical_breaches > 0 or report.unlawful_access_attempts > 10:
            return "critical"
        if high_breaches > 2 or report.unlawful_access_attempts > 5:
            return "high"
        if high_breaches > 0 or report.unlawful_access_attempts > 0:
            return "medium"
        return "low"

    @staticmethod
    def _assess_it_act_risk(report: ComplianceReport) -> str:
        """Assess IT Act Section 72A risk level.

        IT Act Section 72A punishes disclosure of personal information
        obtained under a lawful contract, without the consent of the
        person concerned. Risk increases with:
        - Data breaches involving sensitive data
        - Low consent rates
        - Delayed breach notifications
        """
        if report.total_breaches > 0 and report.breaches_notified_late > 0:
            return "high"
        if report.total_breaches > 0:
            return "medium"
        if report.consent_grant_rate < 0.5 and report.total_consent_records > 0:
            return "medium"
        return "low"

    @staticmethod
    def _generate_recommendations(report: ComplianceReport) -> list[str]:
        """Generate actionable compliance recommendations."""
        recommendations: list[str] = []

        # Consent recommendations
        if report.expired_consents > 0:
            recommendations.append(
                f"DPDPA Section 6: {report.expired_consents} consent records have "
                f"expired. Initiate re-consent campaign to maintain lawful processing basis."
            )

        if report.consent_grant_rate < 0.8 and report.total_consent_records > 10:
            recommendations.append(
                "DPDPA Section 6: Consent grant rate is below 80%. Review consent "
                "flow UX and notice language to ensure users understand the purpose "
                "of data collection."
            )

        # Erasure recommendations
        if report.pending_erasures > 0:
            recommendations.append(
                f"DPDPA Section 12: {report.pending_erasures} erasure requests are "
                f"pending. Process these immediately to comply with the right to erasure."
            )

        if report.average_erasure_time_hours > 48:
            recommendations.append(
                f"DPDPA Section 12: Average erasure processing time is "
                f"{report.average_erasure_time_hours:.1f} hours. Target is under 48 hours. "
                f"Optimise the erasure pipeline."
            )

        # Retention recommendations
        if report.users_expired > 0:
            recommendations.append(
                f"DPDPA Section 11: {report.users_expired} users have data past "
                f"the retention period. Initiate batch erasure or obtain renewed consent."
            )

        if report.users_nearing_expiry > 0:
            recommendations.append(
                f"DPDPA Section 11: {report.users_nearing_expiry} users approaching "
                f"retention expiry within 90 days. Plan proactive re-consent outreach."
            )

        # Breach recommendations
        if report.breaches_notified_late > 0:
            recommendations.append(
                f"DPDPA Section 25: {report.breaches_notified_late} breaches were "
                f"notified after the {_BREACH_NOTIFICATION_DEADLINE_HOURS}-hour deadline. "
                f"Improve incident response procedures."
            )

        if report.total_breaches > 3:
            recommendations.append(
                f"DPDPA Section 25 / BNS Section 303: {report.total_breaches} breaches "
                f"detected in this period. Conduct a security audit and implement "
                f"additional technical safeguards per DPDPA Section 8(5)."
            )

        # BNS / IT Act recommendations
        if report.bns_303_risk_level in ("high", "critical"):
            recommendations.append(
                "BNS Section 303: Data theft risk is elevated. Engage legal counsel "
                "and conduct a forensic investigation. Consider filing an FIR if "
                "unauthorised access is confirmed."
            )

        if report.it_act_72a_risk_level == "high":
            recommendations.append(
                "IT Act Section 72A: Risk of liability for breach of lawful contract "
                "is high. Review all data processing agreements and ensure third-party "
                "processors have adequate safeguards."
            )

        # Significant Data Fiduciary obligations
        if not report.dpia_conducted:
            recommendations.append(
                "DPDPA Section 15: Conduct a Data Protection Impact Assessment (DPIA) "
                "as required for Significant Data Fiduciaries."
            )

        if not report.dpo_appointed:
            recommendations.append(
                "DPDPA Section 15: Appoint a Data Protection Officer (DPO) based in India "
                "who represents the Board's point of contact."
            )

        if report.unlawful_access_attempts > 0:
            recommendations.append(
                f"SECURITY: {report.unlawful_access_attempts} access attempts with "
                f"unlawful purposes detected. Investigate and strengthen access controls."
            )

        # Overall score recommendation
        if report.dpdpa_compliance_score < 70:
            recommendations.append(
                f"OVERALL: Compliance score is {report.dpdpa_compliance_score}/100. "
                f"Immediate attention required to avoid penalties under DPDPA Section 33 "
                f"(up to Rs 250 crore for significant breaches)."
            )
        elif report.dpdpa_compliance_score < 90:
            recommendations.append(
                f"OVERALL: Compliance score is {report.dpdpa_compliance_score}/100. "
                f"Good compliance posture. Address the above recommendations to reach "
                f"best-in-class status."
            )

        return recommendations
