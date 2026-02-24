"""Tests for the proactive scheme notification system.

Covers Notification model creation, NotificationService initialization,
new-scheme notifications, deadline notifications, priority levels,
and channel-specific message length limits.

All tests run WITHOUT network access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from src.data.seed import load_schemes
from src.models.scheme import EligibilityCriteria, SchemeCategory, SchemeDocument
from src.models.user_profile import FamilyMember, UserProfile
from src.services.eligibility import EligibilityEngine
from src.services.notifications import (
    Notification,
    NotificationService,
    _CHANNEL_LIMITS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_schemes() -> list[SchemeDocument]:
    return load_schemes()


@pytest.fixture(scope="module")
def engine(real_schemes: list[SchemeDocument]) -> EligibilityEngine:
    return EligibilityEngine(real_schemes)


@pytest.fixture
def notification_service(engine: EligibilityEngine) -> NotificationService:
    return NotificationService(eligibility=engine, translation=None)


@pytest.fixture
def consented_farmer() -> UserProfile:
    """A consented farmer profile for notification testing."""
    return UserProfile(
        age=45,
        gender="male",
        state="Uttar Pradesh",
        annual_income=60000.0,
        is_bpl=True,
        category="obc",
        occupation="farmer",
        land_holding_acres=2.0,
        family_members=[
            FamilyMember(
                name="Kamla",
                relation="parent",
                age=70,
                gender="female",
            ),
        ],
        has_aadhaar=True,
        has_bank_account=True,
        has_ration_card=True,
        has_land_records=True,
        preferred_language="hi",
        preferred_channel="whatsapp",
        consent_given=True,
        consent_timestamp=datetime.now(UTC),
    )


@pytest.fixture
def non_consented_profile() -> UserProfile:
    """A profile without DPDPA consent -- should NOT receive notifications."""
    return UserProfile(
        age=30,
        gender="male",
        is_bpl=True,
        occupation="farmer",
        consent_given=False,
    )


def _make_scheme(
    scheme_id: str = "test-scheme",
    name: str = "Test Scheme",
    deadline: str | None = None,
    **elig_kwargs: object,
) -> SchemeDocument:
    """Helper to create a SchemeDocument with given attributes."""
    return SchemeDocument(
        scheme_id=scheme_id,
        name=name,
        description="A test scheme for notification testing.",
        category=SchemeCategory.SOCIAL_SECURITY,
        ministry="Test Ministry",
        eligibility=EligibilityCriteria(**elig_kwargs),
        benefits="Rs 10,000 per year",
        application_process="Apply online",
        documents_required=["Aadhaar Card"],
        helpline="14555",
        website="https://example.gov.in",
        last_updated="2025-12-01T00:00:00Z",
        deadline=deadline,
        popularity_score=0.8,
    )


# ---------------------------------------------------------------------------
# Notification model
# ---------------------------------------------------------------------------


class TestNotificationModel:
    """Tests for the Notification Pydantic model."""

    def test_notification_creation(self) -> None:
        n = Notification(
            profile_id="profile-123",
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            notification_type="new_scheme",
            message="You are eligible for PM-KISAN!",
            language="hi",
            channel="sms",
            priority="high",
            for_member="self",
        )
        assert n.profile_id == "profile-123"
        assert n.scheme_id == "pm-kisan"
        assert n.notification_type == "new_scheme"
        assert n.priority == "high"
        assert n.channel == "sms"
        assert n.sent is False
        assert n.sent_at is None
        assert n.notification_id is not None
        assert len(n.notification_id) > 0

    def test_notification_default_values(self) -> None:
        n = Notification(
            profile_id="p1",
            scheme_id="s1",
            scheme_name="Scheme 1",
            notification_type="deadline",
            message="Deadline approaching",
            language="en",
            channel="whatsapp",
        )
        assert n.priority == "medium"
        assert n.for_member == "self"
        assert n.sent is False
        assert n.created_at is not None

    def test_unique_notification_ids(self) -> None:
        n1 = Notification(
            profile_id="p1",
            scheme_id="s1",
            scheme_name="S1",
            notification_type="new_scheme",
            message="msg",
            language="en",
            channel="sms",
        )
        n2 = Notification(
            profile_id="p1",
            scheme_id="s1",
            scheme_name="S1",
            notification_type="new_scheme",
            message="msg",
            language="en",
            channel="sms",
        )
        assert n1.notification_id != n2.notification_id


# ---------------------------------------------------------------------------
# NotificationService initialization
# ---------------------------------------------------------------------------


class TestNotificationServiceInit:
    """Tests for NotificationService construction."""

    def test_initialization(self, notification_service: NotificationService) -> None:
        assert notification_service._eligibility is not None
        assert notification_service._translation is None
        assert notification_service._notification_queue == []

    def test_queue_size_starts_at_zero(
        self, notification_service: NotificationService
    ) -> None:
        assert notification_service.queue_size == 0


# ---------------------------------------------------------------------------
# New scheme notifications
# ---------------------------------------------------------------------------


class TestNewSchemeNotifications:
    """Tests for check_new_scheme_notifications."""

    async def test_generates_notification_for_matching_profile(
        self,
        notification_service: NotificationService,
        consented_farmer: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        # Use the real PM-KISAN scheme as a "new" scheme
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        notifications = await notification_service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[consented_farmer],
        )
        assert len(notifications) > 0
        assert notifications[0].scheme_id == "pm-kisan"
        assert notifications[0].profile_id == consented_farmer.profile_id
        assert notifications[0].notification_type == "new_scheme"

    async def test_no_notification_without_consent(
        self,
        notification_service: NotificationService,
        non_consented_profile: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        """DPDPA: Non-consented profiles must NOT receive notifications."""
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        notifications = await notification_service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[non_consented_profile],
        )
        assert len(notifications) == 0

    async def test_notification_added_to_queue(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        """Notifications should be added to the in-memory queue."""
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[consented_farmer],
        )
        assert service.queue_size > 0

    async def test_notification_message_contains_scheme_name(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        notifications = await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[consented_farmer],
        )
        assert len(notifications) > 0
        assert "PM-KISAN" in notifications[0].message

    async def test_notification_language_from_profile(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        notifications = await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[consented_farmer],
        )
        assert notifications[0].language == "hi"

    async def test_notification_channel_from_profile(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        notifications = await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[consented_farmer],
        )
        assert notifications[0].channel == "whatsapp"


# ---------------------------------------------------------------------------
# Deadline notifications
# ---------------------------------------------------------------------------


class TestDeadlineNotifications:
    """Tests for check_deadline_notifications."""

    async def test_approaching_deadline_generates_notification(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
    ) -> None:
        """A scheme with deadline 10 days from now should trigger notification."""
        future_date = (datetime.now(UTC) + timedelta(days=10)).strftime("%Y-%m-%d")
        scheme = _make_scheme(
            scheme_id="deadline-test",
            name="Deadline Test Scheme",
            deadline=future_date,
            is_bpl=True,
        )
        # Build engine with this one scheme so the farmer matches
        deadline_engine = EligibilityEngine([scheme])
        service = NotificationService(eligibility=deadline_engine)

        notifications = await service.check_deadline_notifications(
            schemes=[scheme],
            profiles=[consented_farmer],
            days_ahead=30,
        )
        assert len(notifications) > 0
        assert notifications[0].notification_type == "deadline"
        assert notifications[0].scheme_id == "deadline-test"

    async def test_far_deadline_no_notification(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
    ) -> None:
        """A scheme with deadline 60 days out should NOT trigger with days_ahead=30."""
        future_date = (datetime.now(UTC) + timedelta(days=60)).strftime("%Y-%m-%d")
        scheme = _make_scheme(
            scheme_id="far-deadline",
            name="Far Deadline Scheme",
            deadline=future_date,
            is_bpl=True,
        )
        deadline_engine = EligibilityEngine([scheme])
        service = NotificationService(eligibility=deadline_engine)

        notifications = await service.check_deadline_notifications(
            schemes=[scheme],
            profiles=[consented_farmer],
            days_ahead=30,
        )
        assert len(notifications) == 0

    async def test_past_deadline_no_notification(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
    ) -> None:
        """A scheme with past deadline should NOT generate notification."""
        past_date = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
        scheme = _make_scheme(
            scheme_id="past-deadline",
            name="Past Deadline Scheme",
            deadline=past_date,
            is_bpl=True,
        )
        deadline_engine = EligibilityEngine([scheme])
        service = NotificationService(eligibility=deadline_engine)

        notifications = await service.check_deadline_notifications(
            schemes=[scheme],
            profiles=[consented_farmer],
            days_ahead=30,
        )
        assert len(notifications) == 0


# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------


class TestNotificationPriority:
    """Tests for notification priority assessment."""

    def test_high_priority_for_high_score(
        self, notification_service: NotificationService
    ) -> None:
        from src.services.eligibility import EligibilityResult

        result = EligibilityResult(
            scheme_id="test",
            scheme_name="Test",
            eligible=True,
            priority_score=0.8,
        )
        priority = notification_service._assess_priority(result)
        assert priority == "high"

    def test_medium_priority_for_medium_score(
        self, notification_service: NotificationService
    ) -> None:
        from src.services.eligibility import EligibilityResult

        result = EligibilityResult(
            scheme_id="test",
            scheme_name="Test",
            eligible=True,
            priority_score=0.5,
        )
        priority = notification_service._assess_priority(result)
        assert priority == "medium"

    def test_low_priority_for_low_score(
        self, notification_service: NotificationService
    ) -> None:
        from src.services.eligibility import EligibilityResult

        result = EligibilityResult(
            scheme_id="test",
            scheme_name="Test",
            eligible=True,
            priority_score=0.2,
        )
        priority = notification_service._assess_priority(result)
        assert priority == "low"

    def test_default_priority_for_non_result(
        self, notification_service: NotificationService
    ) -> None:
        """Non-EligibilityResult objects should get medium priority."""
        priority = notification_service._assess_priority("not a result")
        assert priority == "medium"

    async def test_urgent_deadline_gets_high_priority(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
    ) -> None:
        """A deadline within 7 days should produce a 'high' priority notification."""
        future_date = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
        scheme = _make_scheme(
            scheme_id="urgent",
            name="Urgent Scheme",
            deadline=future_date,
            is_bpl=True,
        )
        deadline_engine = EligibilityEngine([scheme])
        service = NotificationService(eligibility=deadline_engine)

        notifications = await service.check_deadline_notifications(
            schemes=[scheme],
            profiles=[consented_farmer],
            days_ahead=30,
        )
        if notifications:
            assert notifications[0].priority == "high"


# ---------------------------------------------------------------------------
# Channel-specific message length limits
# ---------------------------------------------------------------------------


class TestChannelLimits:
    """Tests for channel-specific message truncation."""

    def test_sms_limit_defined(self) -> None:
        assert _CHANNEL_LIMITS["sms"] == 160

    def test_whatsapp_limit_defined(self) -> None:
        assert _CHANNEL_LIMITS["whatsapp"] == 4096

    def test_ivr_limit_defined(self) -> None:
        assert _CHANNEL_LIMITS["ivr_callback"] == 300

    async def test_sms_message_truncated(
        self,
        engine: EligibilityEngine,
    ) -> None:
        """SMS messages should be truncated to 160 chars."""
        profile = UserProfile(
            age=45,
            gender="male",
            is_bpl=True,
            occupation="farmer",
            land_holding_acres=2.0,
            has_aadhaar=True,
            has_bank_account=True,
            preferred_channel="sms",
            preferred_language="en",
            consent_given=True,
            consent_timestamp=datetime.now(UTC),
        )
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in engine._schemes if s.scheme_id == "pm-kisan")
        notifications = await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[profile],
        )
        if notifications:
            assert len(notifications[0].message) <= 160

    async def test_whatsapp_message_not_truncated(
        self,
        engine: EligibilityEngine,
    ) -> None:
        """WhatsApp messages have a large limit and should not be truncated."""
        profile = UserProfile(
            age=45,
            gender="male",
            is_bpl=True,
            occupation="farmer",
            land_holding_acres=2.0,
            has_aadhaar=True,
            has_bank_account=True,
            preferred_channel="whatsapp",
            preferred_language="en",
            consent_given=True,
            consent_timestamp=datetime.now(UTC),
        )
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in engine._schemes if s.scheme_id == "pm-kisan")
        notifications = await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[profile],
        )
        if notifications:
            assert len(notifications[0].message) <= 4096


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------


class TestQueueManagement:
    """Tests for notification queue operations."""

    async def test_mark_sent(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        notifications = await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[consented_farmer],
        )
        assert len(notifications) > 0
        nid = notifications[0].notification_id

        assert service.mark_sent(nid) is True
        # Verify sent status
        for_profile = service.get_notifications_for_profile(consented_farmer.profile_id)
        sent_n = next(n for n in for_profile if n.notification_id == nid)
        assert sent_n.sent is True
        assert sent_n.sent_at is not None

    async def test_get_pending_notifications(
        self,
        engine: EligibilityEngine,
        consented_farmer: UserProfile,
        real_schemes: list[SchemeDocument],
    ) -> None:
        service = NotificationService(eligibility=engine)
        pm_kisan = next(s for s in real_schemes if s.scheme_id == "pm-kisan")
        await service.check_new_scheme_notifications(
            new_schemes=[pm_kisan],
            profiles=[consented_farmer],
        )
        pending = service.get_pending_notifications(consented_farmer.profile_id)
        assert all(not n.sent for n in pending)
