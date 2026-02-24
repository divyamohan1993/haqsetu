"""Proactive scheme notification system for HaqSetu.

UNIQUE FEATURE: No other platform in India proactively notifies users
about new or relevant government schemes.  When a new scheme launches
or a deadline approaches, HaqSetu:

1. Checks all saved user profiles against the scheme's eligibility criteria.
2. Generates personalized notification text in the user's preferred language.
3. Queues delivery via the user's preferred channel (SMS, WhatsApp, IVR callback).

This transforms HaqSetu from a passive search tool into an active
rights-awareness companion -- ensuring no eligible citizen misses a
scheme because they didn't know about it.

Notification types:
    * ``new_scheme``      -- A new scheme was launched that the user qualifies for.
    * ``deadline``        -- An existing scheme's application deadline is approaching.
    * ``status_update``   -- Application status changed (payment credited, etc.).
    * ``payment_due``     -- Periodic benefit installment is upcoming.
    * ``document_reminder`` -- User is missing a document needed for a high-priority scheme.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from src.models.scheme import SchemeDocument
from src.models.user_profile import UserProfile
from src.services.eligibility import EligibilityEngine

if TYPE_CHECKING:
    from src.services.translation import TranslationService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Notification model
# ---------------------------------------------------------------------------


class Notification(BaseModel):
    """A single notification to be delivered to a user."""

    notification_id: str = Field(default_factory=lambda: uuid4().hex)
    profile_id: str
    scheme_id: str
    scheme_name: str
    notification_type: str  # "new_scheme", "deadline", "status_update", "payment_due", "document_reminder"
    message: str
    language: str
    channel: str  # "sms", "whatsapp", "ivr_callback"
    priority: str = "medium"  # "high", "medium", "low"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sent: bool = False
    sent_at: datetime | None = None
    for_member: str = "self"  # Which family member this applies to


# ---------------------------------------------------------------------------
# Notification templates (Hindi examples; English used as translation source)
# ---------------------------------------------------------------------------

_TEMPLATES: Final[dict[str, str]] = {
    "new_scheme": (
        "Good news! A new scheme '{scheme_name}' has been launched by the government. "
        "Based on your profile, {member_text}may be eligible. "
        "Benefits: {benefits}. "
        "Visit HaqSetu or call {helpline} for application help."
    ),
    "deadline": (
        "Important: The deadline for '{scheme_name}' is approaching on {deadline}. "
        "{member_text}is eligible for this scheme. "
        "Please apply soon to avoid missing out. "
        "Visit your nearest CSC or call {helpline} for assistance."
    ),
    "status_update": (
        "Update on '{scheme_name}': {status_message}. "
        "For more details, visit HaqSetu or call {helpline}."
    ),
    "payment_due": (
        "Good news! The next installment of '{scheme_name}' worth {amount} "
        "is expected soon. Ensure your Aadhaar is linked to your bank account "
        "for seamless Direct Benefit Transfer."
    ),
    "document_reminder": (
        "You are eligible for '{scheme_name}' but missing: {missing_docs}. "
        "Get these documents from your nearest CSC or tehsil office to complete "
        "your application. Call {helpline} for guidance."
    ),
}

# Hindi greeting templates for personalized touch
_HINDI_GREETINGS: Final[dict[str, str]] = {
    "new_scheme": "नमस्ते {name} जी! ",
    "deadline": "नमस्ते {name} जी! ",
    "payment_due": "नमस्ते {name} जी! ",
}

# Channel-specific message length limits
_CHANNEL_LIMITS: Final[dict[str, int]] = {
    "sms": 160,  # Standard SMS
    "whatsapp": 4096,  # WhatsApp message limit
    "ivr_callback": 300,  # Reasonable voice message length
}


# ---------------------------------------------------------------------------
# Notification Service
# ---------------------------------------------------------------------------


class NotificationService:
    """Proactive scheme notification system.

    UNIQUE: No other platform proactively notifies users about new/relevant schemes.

    When a new scheme launches or deadline approaches:
    1. Check all saved user profiles against the scheme
    2. Generate personalized notification in user's preferred language
    3. Queue for delivery via preferred channel (SMS, WhatsApp, IVR callback)

    This service does NOT handle actual delivery (SMS gateway, WhatsApp
    Business API, IVR provider integration).  It generates and queues
    ``Notification`` objects that a delivery layer can process.
    """

    __slots__ = ("_eligibility", "_notification_queue", "_translation")

    def __init__(
        self,
        eligibility: EligibilityEngine,
        translation: TranslationService | None = None,
    ) -> None:
        self._eligibility = eligibility
        self._translation = translation
        # In-memory queue; production would use Redis/SQS/Pub-Sub
        self._notification_queue: list[Notification] = []

    # ------------------------------------------------------------------
    # New scheme notifications
    # ------------------------------------------------------------------

    async def check_new_scheme_notifications(
        self,
        new_schemes: list[SchemeDocument],
        profiles: list[UserProfile],
    ) -> list[Notification]:
        """Check which profiles match new/updated schemes and generate notifications.

        For each new scheme, runs the eligibility engine against every
        profile.  For matches, generates a personalized notification in
        the user's preferred language.

        Parameters
        ----------
        new_schemes:
            Newly launched or recently updated schemes.
        profiles:
            All saved user profiles to check against.

        Returns
        -------
        list[Notification]
            Generated notifications ready for delivery.
        """
        notifications: list[Notification] = []

        for scheme in new_schemes:
            for profile in profiles:
                if not profile.consent_given:
                    continue  # DPDPA: Only notify consented users

                # Run family eligibility check
                report = self._eligibility.match_family(profile)

                if report.total_schemes_matched == 0:
                    continue

                # Check if this specific new scheme was matched
                for member_key, results in report.member_results.items():
                    for result in results:
                        if result.scheme_id == scheme.scheme_id and result.eligible:
                            notification = await self._create_notification(
                                profile=profile,
                                scheme=scheme,
                                notification_type="new_scheme",
                                for_member=result.for_member,
                                priority=self._assess_priority(result),
                                result=result,
                            )
                            notifications.append(notification)
                            self._notification_queue.append(notification)
                            break  # One notification per scheme per profile
                    else:
                        continue
                    break  # Found a match for this profile, move to next

        logger.info(
            "notifications.new_scheme_check",
            new_schemes=len(new_schemes),
            profiles_checked=len(profiles),
            notifications_generated=len(notifications),
        )

        return notifications

    # ------------------------------------------------------------------
    # Deadline notifications
    # ------------------------------------------------------------------

    async def check_deadline_notifications(
        self,
        schemes: list[SchemeDocument],
        profiles: list[UserProfile],
        days_ahead: int = 30,
    ) -> list[Notification]:
        """Generate notifications for approaching scheme deadlines.

        Checks all schemes with deadlines within ``days_ahead`` days,
        then cross-references with user profiles to find eligible users.

        Parameters
        ----------
        schemes:
            All available schemes.
        profiles:
            All saved user profiles.
        days_ahead:
            Number of days to look ahead for deadlines.
        """
        notifications: list[Notification] = []
        now = datetime.now(UTC)

        # Find schemes with approaching deadlines
        approaching_schemes: list[tuple[SchemeDocument, int]] = []
        for scheme in schemes:
            if not scheme.deadline:
                continue
            try:
                deadline_dt = self._parse_deadline(scheme.deadline)
                if deadline_dt is None:
                    continue
                days_remaining = (deadline_dt - now).days
                if 0 < days_remaining <= days_ahead:
                    approaching_schemes.append((scheme, days_remaining))
            except (ValueError, TypeError):
                continue

        if not approaching_schemes:
            return notifications

        logger.info(
            "notifications.deadline_check",
            approaching_schemes=len(approaching_schemes),
            days_ahead=days_ahead,
        )

        # For each approaching scheme, check all profiles
        for scheme, days_remaining in approaching_schemes:
            for profile in profiles:
                if not profile.consent_given:
                    continue

                report = self._eligibility.match_family(profile)

                for member_key, results in report.member_results.items():
                    for result in results:
                        if result.scheme_id == scheme.scheme_id and result.eligible:
                            # Set priority based on urgency
                            if days_remaining <= 7:
                                priority = "high"
                            elif days_remaining <= 15:
                                priority = "medium"
                            else:
                                priority = "low"

                            notification = await self._create_notification(
                                profile=profile,
                                scheme=scheme,
                                notification_type="deadline",
                                for_member=result.for_member,
                                priority=priority,
                                result=result,
                                extra_context={"deadline": scheme.deadline, "days_remaining": days_remaining},
                            )
                            notifications.append(notification)
                            self._notification_queue.append(notification)
                            break
                    else:
                        continue
                    break

        logger.info(
            "notifications.deadline_notifications",
            total_notifications=len(notifications),
        )

        return notifications

    # ------------------------------------------------------------------
    # Document reminder notifications
    # ------------------------------------------------------------------

    async def check_document_reminders(
        self,
        profiles: list[UserProfile],
    ) -> list[Notification]:
        """Generate reminders for users missing documents for high-priority schemes.

        Checks each profile's top-priority eligible schemes and reminds
        users about missing documents that would unlock applications.
        """
        notifications: list[Notification] = []

        for profile in profiles:
            if not profile.consent_given:
                continue

            report = self._eligibility.match_family(profile)

            for result in report.top_priority_schemes[:5]:
                if result.missing_documents:
                    scheme = self._find_scheme_by_id(result.scheme_id)
                    if scheme is None:
                        continue

                    notification = await self._create_notification(
                        profile=profile,
                        scheme=scheme,
                        notification_type="document_reminder",
                        for_member=result.for_member,
                        priority="medium",
                        result=result,
                        extra_context={"missing_docs": ", ".join(result.missing_documents[:3])},
                    )
                    notifications.append(notification)
                    self._notification_queue.append(notification)

        return notifications

    # ------------------------------------------------------------------
    # Notification text generation
    # ------------------------------------------------------------------

    async def generate_notification_text(
        self, notification: Notification
    ) -> str:
        """Generate personalized notification text in user's language.

        If a translation service is available, translates the English
        template to the user's preferred language.  Otherwise returns
        the English text.

        Example (Hindi):
        "नमस्ते राम जी! PM-KISAN की अगली किस्त ₹2,000 आने वाली है।
        आपका आवेदन स्वीकृत है। और जानकारी के लिए हक़सेतु पर कॉल करें।"
        """
        return notification.message  # Already generated during creation

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def get_pending_notifications(
        self, profile_id: str | None = None
    ) -> list[Notification]:
        """Get all pending (unsent) notifications, optionally filtered by profile."""
        if profile_id:
            return [
                n for n in self._notification_queue
                if n.profile_id == profile_id and not n.sent
            ]
        return [n for n in self._notification_queue if not n.sent]

    def get_notifications_for_profile(self, profile_id: str) -> list[Notification]:
        """Get all notifications (sent and unsent) for a specific profile."""
        return [n for n in self._notification_queue if n.profile_id == profile_id]

    def mark_sent(self, notification_id: str) -> bool:
        """Mark a notification as sent."""
        for n in self._notification_queue:
            if n.notification_id == notification_id:
                n.sent = True
                n.sent_at = datetime.now(UTC)
                return True
        return False

    @property
    def queue_size(self) -> int:
        """Number of pending notifications in the queue."""
        return sum(1 for n in self._notification_queue if not n.sent)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_notification(
        self,
        profile: UserProfile,
        scheme: SchemeDocument,
        notification_type: str,
        for_member: str,
        priority: str,
        result: object | None = None,
        extra_context: dict | None = None,
    ) -> Notification:
        """Create a notification with personalized text."""
        context = extra_context or {}

        # Build member text
        if for_member == "self":
            member_text = "you "
        else:
            parts = for_member.split(":", 1)
            if len(parts) == 2 and parts[1] != "unnamed":
                member_text = f"your {parts[0]} ({parts[1]}) "
            else:
                member_text = f"your {parts[0]} "

        # Build template context
        template_ctx = {
            "scheme_name": scheme.name,
            "member_text": member_text,
            "benefits": scheme.benefits[:100] if scheme.benefits else "See details on HaqSetu",
            "helpline": scheme.helpline or "14555 (Government helpline)",
            "deadline": context.get("deadline", ""),
            "status_message": context.get("status_message", ""),
            "amount": context.get("amount", ""),
            "missing_docs": context.get("missing_docs", ""),
        }

        # Generate message from template
        template = _TEMPLATES.get(notification_type, _TEMPLATES["new_scheme"])
        message = template.format(**template_ctx)

        # Truncate for SMS if needed
        channel = profile.preferred_channel
        if channel not in _CHANNEL_LIMITS:
            channel = "whatsapp"  # Default

        limit = _CHANNEL_LIMITS.get(channel, 4096)
        if len(message) > limit:
            message = message[: limit - 3] + "..."

        # Translate if not English and translation service available
        language = profile.preferred_language
        if language != "en" and self._translation is not None:
            try:
                message = await self._translation.translate(
                    message, source_lang="en", target_lang=language
                )
            except Exception:
                logger.warning(
                    "notifications.translation_failed",
                    language=language,
                    exc_info=True,
                )
                # Keep English message as fallback

        return Notification(
            profile_id=profile.profile_id,
            scheme_id=scheme.scheme_id,
            scheme_name=scheme.name,
            notification_type=notification_type,
            message=message,
            language=language,
            channel=channel,
            priority=priority,
            for_member=for_member,
        )

    @staticmethod
    def _assess_priority(result: object) -> str:
        """Assess notification priority based on eligibility result."""
        from src.services.eligibility import EligibilityResult

        if not isinstance(result, EligibilityResult):
            return "medium"

        if result.priority_score >= 0.7:
            return "high"
        elif result.priority_score >= 0.4:
            return "medium"
        return "low"

    def _find_scheme_by_id(self, scheme_id: str) -> SchemeDocument | None:
        """Look up a scheme by ID from the eligibility engine's scheme list."""
        return self._eligibility._find_scheme(scheme_id)

    @staticmethod
    def _parse_deadline(deadline_str: str) -> datetime | None:
        """Parse a deadline string into a datetime."""
        formats = ["%Y-%m-%d", "%d/%m/%Y", "%B %d, %Y", "%d %B %Y", "%d-%m-%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(deadline_str.strip(), fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
        return None
