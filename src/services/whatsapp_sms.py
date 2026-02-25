"""WhatsApp Business API and SMS integration for HaqSetu.

Provides multi-channel messaging for reaching citizens on any device:

1. **WhatsApp** -- Rich messaging via Meta Cloud API with templates,
   interactive buttons, list messages, and media support.  Ideal for
   smartphone users on low-bandwidth connections.

2. **SMS** -- Configurable provider (MSG91, Kaleyra, Textlocal) for
   reaching feature phone users.  Messages are automatically truncated
   to 160-character SMS segments with smart content compression.

3. **USSD** -- Menu generation for feature phones without data.  Users
   navigate numbered menus (e.g. "Press 1 for PM-KISAN status") on any
   phone.  USSD sessions are typically limited to 182 characters per page.

4. **Delivery tracking** -- Unified delivery status tracking across all
   channels with webhook support for real-time status updates.

Architecture:
    * ``MessagingService`` is the unified entry point for all channels.
    * Channel-specific adapters handle API differences internally.
    * Message templates are pre-approved for WhatsApp Business API and
      optimised for 160-character SMS segments.
    * USSD menus are auto-generated from scheme data with language support.
    * All messages include the HaqSetu sender ID for trust and recognition.

IMPORTANT: WhatsApp Business API requires pre-approved message templates
for proactive (non-session) messages.  Session messages (replies within
24 hours) can use free-form text.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WHATSAPP_API_BASE: Final[str] = "https://graph.facebook.com/v18.0"

_INDIAN_MOBILE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:\+?91)?([6-9]\d{9})$",
)

# Retry configuration
_MAX_RETRIES: Final[int] = 3
_RETRY_BACKOFF_SECONDS: Final[tuple[float, ...]] = (0.5, 1.0, 2.0)

# Channel character limits
_SMS_GSM7_MAX: Final[int] = 160
_SMS_UNICODE_MAX: Final[int] = 70
_WHATSAPP_MAX: Final[int] = 4096
_USSD_MAX: Final[int] = 182


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MessageChannel(StrEnum):
    """Supported messaging channels."""

    __slots__ = ()

    WHATSAPP = "whatsapp"
    SMS = "sms"
    USSD = "ussd"


class DeliveryState(StrEnum):
    """Unified message delivery states across all channels."""

    __slots__ = ()

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    EXPIRED = "expired"
    MOCK = "mock"


class SMSProviderType(StrEnum):
    """Supported SMS gateway providers."""

    __slots__ = ()

    MSG91 = "msg91"
    KALEYRA = "kaleyra"
    TEXTLOCAL = "textlocal"
    MOCK = "mock"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DeliveryStatus(BaseModel):
    """Delivery status for a sent message.

    Tracks the full lifecycle of a message from queued through to
    delivered/read/failed.  Used as the return type for all send
    operations, providing a consistent interface regardless of channel.
    """

    message_id: str = Field(default_factory=lambda: uuid4().hex)
    channel: str
    to: str
    status: DeliveryState = DeliveryState.QUEUED
    provider: str = ""
    provider_message_id: str | None = None
    error_message: str | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    cost_inr: float | None = None
    segments: int = 1  # SMS segments consumed
    raw_response: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MessageTemplate(BaseModel):
    """Pre-approved message template for WhatsApp Business API.

    WhatsApp requires templates to be pre-approved by Meta before they
    can be used for proactive (non-session) messages.  Each template
    has bilingual bodies (Hindi and English) with ``{placeholder}``
    variables, optional buttons, and a registered WA template name.
    """

    template_name: str
    category: str = "UTILITY"  # UTILITY, MARKETING, AUTHENTICATION
    whatsapp_template_name: str | None = None  # Registered WA template name
    body_hi: str  # Hindi body with {placeholders}
    body_en: str  # English body with {placeholders}
    footer: str | None = None
    buttons: list[dict[str, str]] = Field(default_factory=list)
    required_params: list[str] = Field(default_factory=list)


class USSDMenu(BaseModel):
    """A USSD menu page for feature phone navigation.

    USSD sessions are limited to ~182 characters per page and support
    only numbered option selection.  This model represents a single
    page in the USSD session flow.
    """

    page_id: str
    title: str
    options: list[dict[str, str]] = Field(default_factory=list)
    # Each option: {"key": "1", "label": "PM-KISAN status", "action": "pm_kisan"}
    footer: str = "0: Wapas"
    language: str = "hi"
    raw_text: str = ""  # The final USSD-formatted text

    @property
    def char_count(self) -> int:
        """Number of characters in the rendered menu text."""
        return len(self.raw_text)

    @property
    def within_limit(self) -> bool:
        """Whether the menu fits within the USSD character limit."""
        return len(self.raw_text) <= _USSD_MAX


class IncomingMessage(BaseModel):
    """An incoming message received from a WhatsApp or SMS webhook."""

    message_id: str = Field(default_factory=lambda: uuid4().hex)
    channel: str
    from_number: str
    text: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    media_url: str | None = None
    location: dict[str, float] | None = None
    is_button_reply: bool = False
    button_payload: str | None = None


# ---------------------------------------------------------------------------
# Pre-approved WhatsApp templates
# ---------------------------------------------------------------------------

TEMPLATES: Final[dict[str, MessageTemplate]] = {
    "scheme_notification": MessageTemplate(
        template_name="scheme_notification",
        category="UTILITY",
        whatsapp_template_name="scheme_notification_v1",
        body_hi=(
            "namaste {user_name} ji,\n\n"
            "aapke liye ek nai sarkari yojana hai: *{scheme_name}*\n\n"
            "faayda: {benefit}\n"
            "aavedan ki aakhri tarikh: {deadline}\n\n"
            "zyada jaankari ke liye HaqSetu par sampark karein.\n"
            "CSC Helpline: 1800-121-3468"
        ),
        body_en=(
            "Hello {user_name},\n\n"
            "A new government scheme is available for you: *{scheme_name}*\n\n"
            "Benefit: {benefit}\n"
            "Application deadline: {deadline}\n\n"
            "Contact HaqSetu for more information.\n"
            "CSC Helpline: 1800-121-3468"
        ),
        footer="HaqSetu - Aapka Haq, Aapki Seva",
        required_params=["user_name", "scheme_name", "benefit", "deadline"],
        buttons=[
            {"type": "QUICK_REPLY", "text": "Aur jaanein"},
            {"type": "QUICK_REPLY", "text": "Aavedan karein"},
        ],
    ),
    "deadline_alert": MessageTemplate(
        template_name="deadline_alert",
        category="UTILITY",
        whatsapp_template_name="deadline_alert_v1",
        body_hi=(
            "zaroori soochna!\n\n"
            "{user_name} ji, *{scheme_name}* ki aakhri tarikh "
            "*{deadline}* hai.\n\n"
            "sirf {days_remaining} din baaki hain. "
            "abhi aavedan karein!\n\n"
            "CSC: 1800-121-3468 | Tele-Law: 1516"
        ),
        body_en=(
            "Important Alert!\n\n"
            "{user_name}, the deadline for *{scheme_name}* is "
            "*{deadline}*.\n\n"
            "Only {days_remaining} days remaining. "
            "Apply now!\n\n"
            "CSC: 1800-121-3468 | Tele-Law: 1516"
        ),
        footer="HaqSetu",
        required_params=["user_name", "scheme_name", "deadline", "days_remaining"],
    ),
    "status_update": MessageTemplate(
        template_name="status_update",
        category="UTILITY",
        whatsapp_template_name="status_update_v1",
        body_hi=(
            "{user_name} ji,\n\n"
            "aapke aavedan ka status badal gaya hai:\n"
            "yojana: *{scheme_name}*\n"
            "naya status: *{status}*\n\n"
            "{details}\n\n"
            "madad: {helpline}"
        ),
        body_en=(
            "{user_name},\n\n"
            "Your application status has been updated:\n"
            "Scheme: *{scheme_name}*\n"
            "New status: *{status}*\n\n"
            "{details}\n\n"
            "Helpline: {helpline}"
        ),
        footer="HaqSetu",
        required_params=["user_name", "scheme_name", "status", "details", "helpline"],
    ),
    "payment_reminder": MessageTemplate(
        template_name="payment_reminder",
        category="UTILITY",
        whatsapp_template_name="payment_reminder_v1",
        body_hi=(
            "{user_name} ji,\n\n"
            "*{scheme_name}* ke tahat aapko *Rs. {amount}* ka "
            "bhugtaan milne waala hai.\n\n"
            "anumaan: {expected_date}\n"
            "kripya apna bank account details verify karein.\n"
            "Aadhaar-bank link zaroori hai."
        ),
        body_en=(
            "{user_name},\n\n"
            "You are due to receive a payment of *Rs. {amount}* "
            "under *{scheme_name}*.\n\n"
            "Expected date: {expected_date}\n"
            "Please verify your bank account details.\n"
            "Aadhaar-bank linking is required."
        ),
        footer="HaqSetu",
        required_params=["user_name", "scheme_name", "amount", "expected_date"],
    ),
    "document_reminder": MessageTemplate(
        template_name="document_reminder",
        category="UTILITY",
        whatsapp_template_name="document_reminder_v1",
        body_hi=(
            "{user_name} ji,\n\n"
            "*{scheme_name}* ke liye aapko ye dastavez chahiye:\n"
            "{documents}\n\n"
            "kripya jaldi se jaldi ye taiyaar karein.\n"
            "CSC: 1800-121-3468 | Tehsil office mein bhi mil sakte hain."
        ),
        body_en=(
            "{user_name},\n\n"
            "You need the following documents for *{scheme_name}*:\n"
            "{documents}\n\n"
            "Please prepare these at the earliest.\n"
            "CSC: 1800-121-3468 | Also available at tehsil office."
        ),
        footer="HaqSetu",
        required_params=["user_name", "scheme_name", "documents"],
    ),
    "welcome": MessageTemplate(
        template_name="welcome",
        category="UTILITY",
        whatsapp_template_name="welcome_v1",
        body_hi=(
            "namaste! HaqSetu mein aapka swagat hai.\n\n"
            "Hum aapko sarkari yojanaon, kanuni adhikaron, aur "
            "sarkaari sewaon ke baare mein madad karte hain.\n\n"
            "Kya karna chahte hain?\n"
            "1. Yojana khojein\n"
            "2. Paatrata jaanchein\n"
            "3. Najdiki CSC dhundhein\n"
            "4. Kanuni sahayata\n\n"
            "Koi bhi number bhejein ya apna sawaal likhein."
        ),
        body_en=(
            "Hello! Welcome to HaqSetu.\n\n"
            "We help you discover government schemes, legal rights, "
            "and government services.\n\n"
            "What would you like to do?\n"
            "1. Find schemes\n"
            "2. Check eligibility\n"
            "3. Find nearest CSC\n"
            "4. Legal help\n\n"
            "Send any number or type your question."
        ),
        footer="HaqSetu - Aapka Haq, Aapki Seva",
        required_params=[],
    ),
    "eligibility_report": MessageTemplate(
        template_name="eligibility_report",
        category="UTILITY",
        whatsapp_template_name="eligibility_report_v1",
        body_hi=(
            "namaste {user_name} ji!\n\n"
            "Aapki parivar ki paatrata report taiyaar hai:\n\n"
            "Kul yojanayen: *{total_schemes}*\n"
            "Anumit varshik labh: *{total_benefit}*\n\n"
            "Top yojana: {top_scheme}\n\n"
            "Poori report ke liye HaqSetu par jaayen.\n"
            "CSC: 1800-121-3468"
        ),
        body_en=(
            "Hello {user_name}!\n\n"
            "Your family eligibility report is ready:\n\n"
            "Total schemes: *{total_schemes}*\n"
            "Estimated annual benefit: *{total_benefit}*\n\n"
            "Top scheme: {top_scheme}\n\n"
            "Visit HaqSetu for the full report.\n"
            "CSC: 1800-121-3468"
        ),
        footer="HaqSetu - Aapka Haq, Aapki Seva",
        required_params=["user_name", "total_schemes", "total_benefit", "top_scheme"],
    ),
}


# ---------------------------------------------------------------------------
# SMS templates (optimised for 160-character limit)
# ---------------------------------------------------------------------------

_SMS_TEMPLATES: Final[dict[str, str]] = {
    # Each template is carefully crafted to stay within or near 160 chars
    "scheme_notification": (
        "HaqSetu: {user_name} ji, nai yojana {scheme_name} ke liye paatra hain. "
        "Labh:{benefit}. CSC:1800-121-3468"
    ),  # ~140 chars with short names
    "deadline_alert": (
        "HaqSetu: {user_name} ji, {scheme_name} last date {deadline}. "
        "Sirf {days_remaining} din baaki. Jaldi aavedan karein! CSC:1800-121-3468"
    ),  # ~150 chars
    "status_update": (
        "HaqSetu: {scheme_name} status: {status}. {details} "
        "Help:{helpline}"
    ),  # ~100 chars
    "payment_update": (
        "HaqSetu: {scheme_name} Rs.{amount} aapke account mein. "
        "Problem? Bank/CSC sampark karein. 1800-121-3468"
    ),  # ~130 chars
    "document_reminder": (
        "HaqSetu: {scheme_name} ke liye chahiye: {documents}. "
        "CSC/tehsil jaayen. Help:1800-121-3468"
    ),  # ~120 chars
    "eligibility_summary": (
        "HaqSetu: {user_name} ji, parivar {total_schemes} yojanaon ke paatra. "
        "Labh Rs.{total_benefit}/yr. HaqSetu ya CSC par jaayen"
    ),  # ~140 chars
    "welcome": (
        "HaqSetu: Swagat hai! Sarkari yojana/kanuni madad ke liye "
        "HAQSETU reply karein. Helpline:1800-121-3468"
    ),  # ~110 chars
    "otp": (
        "HaqSetu OTP: {otp}. 10 min mein expire hoga. Kisi se share na karein."
    ),  # ~73 chars
}


# ---------------------------------------------------------------------------
# USSD menu configurations
# ---------------------------------------------------------------------------

_USSD_MAIN_MENUS: Final[dict[str, dict[str, str]]] = {
    "hi": {
        "title": "HaqSetu",
        "1": "Yojana khojein",
        "2": "Paatrata jaanchein",
        "3": "Najdiki CSC",
        "4": "Kanuni madad",
        "5": "Shikayat darj",
        "0": "Bhasha badlein",
    },
    "en": {
        "title": "HaqSetu",
        "1": "Find schemes",
        "2": "Check eligibility",
        "3": "Nearest CSC",
        "4": "Legal help",
        "5": "File complaint",
        "0": "Change language",
    },
    "bn": {
        "title": "HaqSetu",
        "1": "Yojana khunjun",
        "2": "Joggyota janun",
        "3": "Kachher CSC",
        "4": "Aainer sahayota",
        "5": "Obhijog",
        "0": "Bhasha badlun",
    },
    "ta": {
        "title": "HaqSetu",
        "1": "Thittam thedu",
        "2": "Thaguthi paaru",
        "3": "Arugil CSC",
        "4": "Satta uthavi",
        "5": "Pugar pathivu",
        "0": "Mozhi maatru",
    },
    "te": {
        "title": "HaqSetu",
        "1": "Padakalu vetuku",
        "2": "Arhata choodu",
        "3": "Daggarilo CSC",
        "4": "Nyaya sahayam",
        "5": "Phiryadu cheyu",
        "0": "Bhasha marchu",
    },
    "mr": {
        "title": "HaqSetu",
        "1": "Yojana shodha",
        "2": "Patrata tapasa",
        "3": "Jawalcha CSC",
        "4": "Kanuni madad",
        "5": "Takrar nondva",
        "0": "Bhasha badla",
    },
    "gu": {
        "title": "HaqSetu",
        "1": "Yojana shodho",
        "2": "Patrata tapaso",
        "3": "Najiknu CSC",
        "4": "Kanuni madad",
        "5": "Fariyad karo",
        "0": "Bhasha badlo",
    },
}


# ---------------------------------------------------------------------------
# Phone number utilities
# ---------------------------------------------------------------------------


def sanitize_phone(number: str) -> str:
    """Normalise an Indian mobile number to E.164 format (``+91XXXXXXXXXX``).

    Accepts ``+91XXXXXXXXXX``, ``91XXXXXXXXXX``, or plain 10-digit
    formats.  Strips spaces, dashes, and parentheses before matching.

    Raises
    ------
    ValueError
        If the number cannot be parsed as a valid Indian mobile.
    """
    cleaned = re.sub(r"[\s\-\(\)]+", "", number.strip())
    match = _INDIAN_MOBILE_RE.match(cleaned)
    if not match:
        raise ValueError(
            f"Invalid Indian mobile number: {number!r}. "
            "Expected +91XXXXXXXXXX, 91XXXXXXXXXX, or 10-digit format."
        )
    return f"+91{match.group(1)}"


# ---------------------------------------------------------------------------
# SMS provider implementations
# ---------------------------------------------------------------------------


class _SMSProviderBase:
    """Abstract base for SMS gateway providers."""

    async def send(
        self,
        to: str,
        message: str,
        *,
        api_key: str,
    ) -> dict[str, Any]:
        raise NotImplementedError


class _MSG91Provider(_SMSProviderBase):
    """MSG91 SMS gateway integration.

    MSG91 is one of the most popular SMS gateways in India with
    excellent delivery rates for transactional SMS.
    """

    _BASE_URL: Final[str] = "https://api.msg91.com/api/v5/flow/"

    async def send(
        self,
        to: str,
        message: str,
        *,
        api_key: str,
    ) -> dict[str, Any]:
        import httpx

        headers = {"authkey": api_key, "Content-Type": "application/json"}
        payload = {
            "flow_id": "haqsetu_sms",
            "sender": "HAQSET",
            "recipients": [
                {
                    "mobiles": to.lstrip("+"),
                    "message": message,
                },
            ],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._BASE_URL, json=payload, headers=headers,
            )
            response.raise_for_status()
            return response.json()


class _KaleyraProvider(_SMSProviderBase):
    """Kaleyra SMS gateway integration.

    Kaleyra provides enterprise SMS delivery with strong coverage
    across all Indian telecom operators.
    """

    _BASE_URL: Final[str] = "https://api.kaleyra.io/v1"

    async def send(
        self,
        to: str,
        message: str,
        *,
        api_key: str,
    ) -> dict[str, Any]:
        import httpx

        # Kaleyra expects api_key in "SID:api_key" format
        parts = api_key.split(":", 1)
        if len(parts) != 2:
            raise ValueError(
                "Kaleyra api_key must be in 'SID:api_key' format."
            )
        sid, token = parts

        headers = {"api-key": token, "Content-Type": "application/json"}
        payload = {
            "to": to.lstrip("+"),
            "type": "TXN",
            "sender": "HAQSET",
            "body": message,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._BASE_URL}/{sid}/messages",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()


class _TextlocalProvider(_SMSProviderBase):
    """Textlocal SMS gateway integration."""

    _BASE_URL: Final[str] = "https://api.textlocal.in/send/"

    async def send(
        self,
        to: str,
        message: str,
        *,
        api_key: str,
    ) -> dict[str, Any]:
        import httpx

        payload = {
            "apikey": api_key,
            "numbers": to.lstrip("+"),
            "message": message,
            "sender": "HAQSET",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self._BASE_URL, data=payload)
            response.raise_for_status()
            return response.json()


class _MockProvider(_SMSProviderBase):
    """Mock SMS provider for local development and testing."""

    async def send(
        self,
        to: str,
        message: str,
        *,
        api_key: str,
    ) -> dict[str, Any]:
        logger.info(
            "mock_sms.sent",
            to=to,
            message_preview=message[:80],
            length=len(message),
        )
        return {
            "status": "mock",
            "message_id": f"mock_{uuid4().hex[:12]}",
            "to": to,
        }


_SMS_PROVIDERS: Final[dict[str, _SMSProviderBase]] = {
    "msg91": _MSG91Provider(),
    "kaleyra": _KaleyraProvider(),
    "textlocal": _TextlocalProvider(),
    "mock": _MockProvider(),
}


# ---------------------------------------------------------------------------
# Messaging Service
# ---------------------------------------------------------------------------


class MessagingService:
    """Unified WhatsApp, SMS, and USSD messaging service.

    Provides a single interface for reaching citizens across all device
    types -- smartphones via WhatsApp, feature phones via SMS, and the
    most basic phones via USSD menus.

    Configuration:
        * WhatsApp: Requires Meta Business API token and phone number ID.
        * SMS: Requires provider API key (MSG91, Kaleyra, Textlocal).
        * USSD: Generates menu text; actual USSD gateway integration is
          provider-specific.

    Usage::

        service = MessagingService(
            whatsapp_phone_id="your_phone_number_id",
            whatsapp_token="your_meta_token",
            sms_provider="msg91",
            sms_api_key="your_msg91_key",
        )

        # Send WhatsApp message
        status = await service.send_whatsapp("+919876543210", "Hello!")

        # Send with template
        status = await service.send_whatsapp(
            "+919876543210", "Hello!", template="welcome"
        )

        # Send SMS
        status = await service.send_sms("+919876543210", "Your OTP is 1234")

        # Generate USSD menu
        ussd_text = service.generate_ussd_menu(schemes, language="hi")

        # Format for SMS
        sms_text = service.format_for_sms(long_response, max_length=160)

        # Format for WhatsApp
        wa_text = service.format_for_whatsapp(long_response, schemes)
    """

    __slots__ = (
        "_delivery_log",
        "_sms_api_key",
        "_sms_provider",
        "_sms_provider_name",
        "_whatsapp_phone_id",
        "_whatsapp_token",
    )

    def __init__(
        self,
        whatsapp_phone_id: str = "",
        whatsapp_token: str = "",
        sms_provider: str = "mock",
        sms_api_key: str = "",
    ) -> None:
        self._whatsapp_phone_id = whatsapp_phone_id
        self._whatsapp_token = whatsapp_token
        self._sms_provider_name = sms_provider
        self._sms_api_key = sms_api_key

        if sms_provider not in _SMS_PROVIDERS:
            raise ValueError(
                f"Unknown SMS provider {sms_provider!r}. "
                f"Supported: {', '.join(sorted(_SMS_PROVIDERS))}."
            )
        self._sms_provider: _SMSProviderBase = _SMS_PROVIDERS[sms_provider]

        # In-memory delivery log; production would use Redis or Firestore
        self._delivery_log: dict[str, DeliveryStatus] = {}

        logger.info(
            "messaging_service.initialised",
            whatsapp_phone_id=(
                whatsapp_phone_id[:6] + "..."
                if whatsapp_phone_id
                else "<empty>"
            ),
            sms_provider=sms_provider,
        )

    # ------------------------------------------------------------------
    # WhatsApp messaging
    # ------------------------------------------------------------------

    async def send_whatsapp(
        self,
        to: str,
        message: str,
        template: str | None = None,
        template_params: dict[str, str] | None = None,
        language: str = "hi",
    ) -> DeliveryStatus:
        """Send a WhatsApp message via Meta Cloud API.

        Can send either a free-form text message (within a 24-hour session
        window) or a pre-approved template message (for proactive outreach).

        Parameters
        ----------
        to:
            Recipient phone number in any Indian format.
        message:
            Message text (used for session messages or as fallback).
        template:
            Template name for proactive messages.  If provided, the message
            is first sent as a registered WhatsApp template; if that fails,
            it falls back to a plain text session message.
        template_params:
            Variable values to fill into template placeholders.
        language:
            Language code (default ``"hi"``).

        Returns
        -------
        DeliveryStatus
            Delivery tracking status with message_id for follow-up queries.
        """
        start = time.perf_counter()

        try:
            phone = sanitize_phone(to)
        except ValueError as exc:
            return DeliveryStatus(
                channel="whatsapp",
                to=to,
                status=DeliveryState.FAILED,
                error_message=str(exc),
            )

        log = logger.bind(channel="whatsapp", to=phone, template=template)

        # If a template is specified, render it
        rendered_message = message
        if template and template in TEMPLATES:
            tmpl = TEMPLATES[template]
            body = tmpl.body_hi if language == "hi" else tmpl.body_en
            params = template_params or {}
            try:
                rendered_message = body.format(**params)
            except KeyError:
                log.warning(
                    "whatsapp.template_render_fallback",
                    template=template,
                )

        # Mock mode: when credentials are not configured
        if (
            not self._whatsapp_phone_id
            or self._whatsapp_phone_id.startswith("mock")
            or self._sms_provider_name == "mock"
        ):
            status = DeliveryStatus(
                channel="whatsapp",
                to=phone,
                status=DeliveryState.MOCK,
                provider="mock",
                sent_at=datetime.now(UTC),
            )
            self._delivery_log[status.message_id] = status
            log.info("mock_whatsapp.sent", message_preview=rendered_message[:80])
            return status

        # Production: send via WhatsApp Cloud API
        try:
            # Attempt template delivery first (works outside 24-hour window)
            if template and template in TEMPLATES:
                tmpl = TEMPLATES[template]
                if tmpl.whatsapp_template_name:
                    wa_status = await self._send_wa_template(
                        phone, tmpl, template_params or {}, language, log,
                    )
                    if wa_status.status != DeliveryState.FAILED:
                        self._delivery_log[wa_status.message_id] = wa_status
                        return wa_status
                    log.warning(
                        "whatsapp.template_fallback_to_text",
                        error=wa_status.error_message,
                    )

            # Send as plain text (session message)
            wa_status = await self._send_wa_text(phone, rendered_message, log)
            self._delivery_log[wa_status.message_id] = wa_status

            elapsed_ms = (time.perf_counter() - start) * 1000
            log.info(
                "whatsapp.completed",
                status=wa_status.status,
                elapsed_ms=round(elapsed_ms, 2),
            )
            return wa_status

        except Exception as exc:
            status = DeliveryStatus(
                channel="whatsapp",
                to=phone,
                status=DeliveryState.FAILED,
                provider="whatsapp_cloud_api",
                error_message=str(exc),
            )
            self._delivery_log[status.message_id] = status
            log.error("whatsapp.send_failed", error=str(exc), exc_info=True)
            return status

    async def _send_wa_text(
        self,
        phone: str,
        message: str,
        log: Any,
    ) -> DeliveryStatus:
        """Send a plain text WhatsApp message via Cloud API."""
        import httpx

        url = f"{WHATSAPP_API_BASE}/{self._whatsapp_phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._whatsapp_token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone.lstrip("+"),
            "type": "text",
            "text": {"preview_url": False, "body": message[:_WHATSAPP_MAX]},
        }

        response = await self._request_with_retry(
            "POST", url, headers=headers, json_body=payload,
        )
        data = response.json()

        if response.status_code == 200 and "messages" in data:
            wa_msg_id = data["messages"][0].get("id", "")
            log.info("whatsapp.text_sent", wa_message_id=wa_msg_id)
            return DeliveryStatus(
                channel="whatsapp",
                to=phone,
                status=DeliveryState.SENT,
                provider="whatsapp_cloud_api",
                provider_message_id=wa_msg_id,
                sent_at=datetime.now(UTC),
                raw_response=data,
            )

        error_msg = data.get("error", {}).get("message", str(data))
        log.error("whatsapp.api_error", status=response.status_code, body=data)
        return DeliveryStatus(
            channel="whatsapp",
            to=phone,
            status=DeliveryState.FAILED,
            provider="whatsapp_cloud_api",
            error_message=error_msg,
            raw_response=data,
        )

    async def _send_wa_template(
        self,
        phone: str,
        tmpl: MessageTemplate,
        params: dict[str, str],
        language: str,
        log: Any,
    ) -> DeliveryStatus:
        """Send a registered WhatsApp Business template message."""
        import httpx

        url = f"{WHATSAPP_API_BASE}/{self._whatsapp_phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._whatsapp_token}",
            "Content-Type": "application/json",
        }

        # Build parameter components
        components: list[dict[str, Any]] = []
        body_params = [
            {"type": "text", "text": str(params.get(k, ""))}
            for k in tmpl.required_params
            if k in params
        ]
        if body_params:
            components.append({"type": "body", "parameters": body_params})

        # Map language codes to WhatsApp locale codes
        wa_lang_map: dict[str, str] = {
            "hi": "hi", "en": "en_US", "bn": "bn", "ta": "ta",
            "te": "te", "mr": "mr", "gu": "gu", "kn": "kn",
            "ml": "ml", "pa": "pa", "or": "or",
        }
        wa_lang = wa_lang_map.get(language, "hi")

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone.lstrip("+"),
            "type": "template",
            "template": {
                "name": tmpl.whatsapp_template_name,
                "language": {"code": wa_lang},
                "components": components,
            },
        }

        try:
            response = await self._request_with_retry(
                "POST", url, headers=headers, json_body=payload,
            )
            data = response.json()

            if response.status_code == 200 and "messages" in data:
                wa_msg_id = data["messages"][0].get("id", "")
                log.info(
                    "wa_template.sent",
                    wa_message_id=wa_msg_id,
                    template=tmpl.whatsapp_template_name,
                )
                return DeliveryStatus(
                    channel="whatsapp",
                    to=phone,
                    status=DeliveryState.SENT,
                    provider="whatsapp_cloud_api",
                    provider_message_id=wa_msg_id,
                    sent_at=datetime.now(UTC),
                    raw_response=data,
                )

            error_msg = data.get("error", {}).get("message", str(data))
            return DeliveryStatus(
                channel="whatsapp",
                to=phone,
                status=DeliveryState.FAILED,
                provider="whatsapp_cloud_api",
                error_message=error_msg,
                raw_response=data,
            )

        except Exception as exc:
            return DeliveryStatus(
                channel="whatsapp",
                to=phone,
                status=DeliveryState.FAILED,
                provider="whatsapp_cloud_api",
                error_message=str(exc),
            )

    # ------------------------------------------------------------------
    # SMS messaging
    # ------------------------------------------------------------------

    async def send_sms(
        self,
        to: str,
        message: str,
        template_name: str | None = None,
        template_vars: dict[str, str] | None = None,
    ) -> DeliveryStatus:
        """Send an SMS message via the configured provider.

        Automatically truncates messages to fit SMS segment limits and
        calculates the number of segments required.  Cost is estimated
        at approximately Rs 0.20 per segment.

        Parameters
        ----------
        to:
            Recipient phone number (any Indian format).
        message:
            Message text.  Will be compressed to fit SMS limits.
        template_name:
            Optional SMS template name from pre-built templates.
        template_vars:
            Variables to fill into the template.

        Returns
        -------
        DeliveryStatus
            Delivery tracking status.
        """
        start = time.perf_counter()

        try:
            phone = sanitize_phone(to)
        except ValueError as exc:
            return DeliveryStatus(
                channel="sms",
                to=to,
                status=DeliveryState.FAILED,
                error_message=str(exc),
            )

        log = logger.bind(channel="sms", to=phone, provider=self._sms_provider_name)

        # Use template if specified
        final_message = message
        if template_name and template_name in _SMS_TEMPLATES:
            template_text = _SMS_TEMPLATES[template_name]
            if template_vars:
                try:
                    final_message = template_text.format(**template_vars)
                except KeyError:
                    log.warning("sms.template_render_fallback", template=template_name)

        # Compress for SMS limits
        final_message = self.format_for_sms(final_message, max_length=_SMS_GSM7_MAX)
        segments = _calculate_sms_segments(message)

        try:
            result = await self._sms_provider.send(
                phone, final_message, api_key=self._sms_api_key,
            )

            provider_msg_id = result.get("message_id", result.get("id", ""))
            provider_status = result.get("status", "sent")

            if provider_status == "mock":
                mapped_status = DeliveryState.MOCK
            elif provider_status in ("sent", "submitted", "success"):
                mapped_status = DeliveryState.SENT
            else:
                mapped_status = DeliveryState.QUEUED

            status = DeliveryStatus(
                channel="sms",
                to=phone,
                status=mapped_status,
                provider=self._sms_provider_name,
                provider_message_id=str(provider_msg_id),
                sent_at=datetime.now(UTC),
                segments=segments,
                cost_inr=round(segments * 0.20, 2),
                raw_response=result,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000
            log.info(
                "sms.sent",
                segments=segments,
                provider_id=provider_msg_id,
                elapsed_ms=round(elapsed_ms, 2),
            )

        except Exception as exc:
            status = DeliveryStatus(
                channel="sms",
                to=phone,
                status=DeliveryState.FAILED,
                provider=self._sms_provider_name,
                error_message=str(exc),
                segments=segments,
            )
            log.error("sms.send_failed", error=str(exc), exc_info=True)

        self._delivery_log[status.message_id] = status
        return status

    # ------------------------------------------------------------------
    # USSD menu generation
    # ------------------------------------------------------------------

    def generate_ussd_menu(
        self,
        schemes: list | None = None,
        language: str = "hi",
        page: str = "main",
    ) -> str:
        """Generate a USSD menu page for feature phone users.

        USSD sessions are limited to ~182 characters per page and support
        only numbered option selection.  Generates appropriately formatted
        menu text for different pages of the USSD navigation flow.

        Parameters
        ----------
        schemes:
            List of scheme objects or dicts with ``name``/``scheme_name``.
            When provided with ``page="schemes"``, generates a scheme
            selection menu.
        language:
            Language code (default ``"hi"`` for Hindi).
        page:
            Menu page identifier: ``"main"``, ``"schemes"``,
            ``"eligibility"``, ``"language"``, ``"legal"``, ``"csc"``.

        Returns
        -------
        str
            Formatted USSD menu text, guaranteed within 182-char limit.
        """
        if page == "schemes" and schemes:
            return self._ussd_scheme_menu(schemes, language)
        if page == "eligibility":
            return self._ussd_eligibility_menu(language)
        if page == "language":
            return self._ussd_language_menu()
        if page == "legal":
            return self._ussd_legal_menu(language)
        if page == "csc":
            return self._ussd_csc_menu(language)
        return self._ussd_main_menu(language)

    def _ussd_main_menu(self, language: str) -> str:
        """Generate the main USSD menu."""
        menu_data = _USSD_MAIN_MENUS.get(language, _USSD_MAIN_MENUS["hi"])
        lines = [menu_data["title"]]
        for key in sorted(k for k in menu_data if k != "title"):
            lines.append(f"{key}.{menu_data[key]}")
        text = "\n".join(lines)
        text = _enforce_ussd_limit(text)
        logger.info("messaging.ussd_menu", page="main", language=language, chars=len(text))
        return text

    def _ussd_scheme_menu(self, schemes: list, language: str) -> str:
        """Generate USSD menu listing available schemes."""
        title = "Yojanayen:" if language == "hi" else "Schemes:"
        back = "0.Wapas" if language == "hi" else "0.Back"
        lines = [title]

        for idx, scheme in enumerate(schemes[:7], 1):
            name = _extract_name(scheme)
            max_name = 22
            display = name[:max_name]
            if len(name) > max_name:
                display = display[:max_name - 2] + ".."
            lines.append(f"{idx}.{display}")

        lines.append(back)
        text = "\n".join(lines)

        while len(text) > _USSD_MAX and len(lines) > 3:
            lines.pop(-2)
            text = "\n".join(lines)

        return text

    @staticmethod
    def _ussd_eligibility_menu(language: str) -> str:
        """Generate USSD eligibility check flow menu."""
        if language == "hi":
            return _enforce_ussd_limit(
                "Paatrata Jaanch\n"
                "1.Umar batayein\n"
                "2.Aay batayein\n"
                "3.Kshetra(Gramin/Shahri)\n"
                "4.Vyavsay\n"
                "5.Jaanch shuru\n"
                "0.Wapas"
            )
        return _enforce_ussd_limit(
            "Eligibility Check\n"
            "1.Enter age\n"
            "2.Enter income\n"
            "3.Area(Rural/Urban)\n"
            "4.Occupation\n"
            "5.Start check\n"
            "0.Back"
        )

    @staticmethod
    def _ussd_legal_menu(language: str) -> str:
        """Generate USSD legal help menu."""
        if language == "hi":
            return _enforce_ussd_limit(
                "Kanuni Sahayata\n"
                "1.DLSA(muft kanuni aid)\n"
                "2.Tele-Law:1516\n"
                "3.Mahila helpline:181\n"
                "4.Bal helpline:1098\n"
                "5.Police:100\n"
                "0.Wapas"
            )
        return _enforce_ussd_limit(
            "Legal Help\n"
            "1.DLSA(free legal aid)\n"
            "2.Tele-Law:1516\n"
            "3.Women helpline:181\n"
            "4.Child helpline:1098\n"
            "5.Police:100\n"
            "0.Back"
        )

    @staticmethod
    def _ussd_csc_menu(language: str) -> str:
        """Generate USSD CSC finder menu."""
        if language == "hi":
            return _enforce_ussd_limit(
                "CSC Dhundhein\n"
                "1.PIN code daalen\n"
                "2.District chunein\n"
                "3.CSC helpline:1800-121-3468\n"
                "0.Wapas"
            )
        return _enforce_ussd_limit(
            "Find CSC\n"
            "1.Enter PIN code\n"
            "2.Select district\n"
            "3.CSC helpline:1800-121-3468\n"
            "0.Back"
        )

    @staticmethod
    def _ussd_language_menu() -> str:
        """Generate USSD language selection menu."""
        return (
            "Bhasha/Language\n"
            "1.Hindi\n"
            "2.English\n"
            "3.Bengali\n"
            "4.Tamil\n"
            "5.Telugu\n"
            "6.Marathi\n"
            "7.Gujarati\n"
            "8.Kannada\n"
            "9.Malayalam\n"
            "0.Wapas/Back"
        )

    # ------------------------------------------------------------------
    # Message formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_for_sms(response_text: str, max_length: int = 160) -> str:
        """Format a response text to fit within SMS character limits.

        Applies intelligent compression:
        1. Strip WhatsApp markdown (``*bold*``, ``_italic_``, ``~strike~``)
        2. Abbreviate common words (government -> govt, etc.)
        3. Collapse whitespace
        4. Truncate at sentence boundary if possible
        5. Append "..." if truncated

        Parameters
        ----------
        response_text:
            Full response text to compress.
        max_length:
            Maximum character length (default 160 for standard SMS).

        Returns
        -------
        str
            Compressed text fitting within ``max_length``.
        """
        text = response_text.strip()

        # Step 1: Strip markdown
        text = re.sub(r"[*_~`]", "", text)
        text = re.sub(r"#{1,6}\s+", "", text)
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)

        # Step 2: Abbreviate common words
        _abbr: dict[str, str] = {
            "Government": "Govt", "government": "govt",
            "Department": "Dept", "department": "dept",
            "Application": "App", "application": "app",
            "Information": "Info", "information": "info",
            "Certificate": "Cert", "certificate": "cert",
            "District": "Dist", "district": "dist",
            "Number": "No.", "number": "no.",
            "Telephone": "Tel", "telephone": "tel",
            "Common Service Centre": "CSC",
            "District Legal Services Authority": "DLSA",
            "Pradhan Mantri": "PM",
            "Rupees": "Rs", "rupees": "Rs",
            "per month": "/mo", "per year": "/yr", "per annum": "/yr",
        }
        for long_form, short_form in _abbr.items():
            text = text.replace(long_form, short_form)

        # Step 3: Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        text = text.replace(" ,", ",").replace(" .", ".")

        # Step 4: Return if within limit
        if len(text) <= max_length:
            return text

        # Step 5: Truncate at natural break point
        truncated = text[:max_length - 3]
        cutoff = max_length * 0.5
        last_period = truncated.rfind(".")
        last_comma = truncated.rfind(",")
        last_space = truncated.rfind(" ")

        if last_period > cutoff:
            truncated = truncated[:last_period + 1]
        elif last_comma > cutoff:
            truncated = truncated[:last_comma + 1] + ".."
        elif last_space > cutoff:
            truncated = truncated[:last_space] + "..."
        else:
            truncated = truncated + "..."

        return truncated[:max_length]

    @staticmethod
    def format_for_whatsapp(
        response_text: str,
        schemes: list | None = None,
    ) -> str:
        """Format a response for WhatsApp with rich formatting.

        Enhances plain text with WhatsApp-supported markdown:
        - Bold (``*text*``) for scheme names and key terms
        - Normalised bullet points
        - Helpline numbers highlighted for visibility
        - Optional scheme list appended at the bottom
        - HaqSetu footer

        Parameters
        ----------
        response_text:
            Full response text to format.
        schemes:
            Optional list of scheme objects or dicts to append as a
            formatted reference list at the bottom.

        Returns
        -------
        str
            WhatsApp-formatted message text (max 4096 chars).
        """
        text = response_text.strip()

        # Format lines: bold headers, normalise bullets
        formatted_lines: list[str] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                formatted_lines.append("")
                continue
            if stripped.startswith("*") and stripped.endswith("*"):
                formatted_lines.append(stripped)
                continue
            if stripped.isupper() or (stripped.endswith(":") and len(stripped) < 60):
                formatted_lines.append(f"*{stripped}*")
                continue
            if stripped.startswith("- ") or stripped.startswith("* "):
                formatted_lines.append(f"  - {stripped[2:]}")
                continue
            formatted_lines.append(stripped)

        text = "\n".join(formatted_lines)

        # Bold scheme names in the text
        if schemes:
            for scheme in schemes:
                name = _extract_name(scheme)
                if name and name in text and f"*{name}*" not in text:
                    text = text.replace(name, f"*{name}*")

        # Highlight helpline numbers
        text = re.sub(
            r"(?<!\*)\b(1800[-\s]?\d{3}[-\s]?\d{4})\b(?!\*)",
            r"*\1*",
            text,
        )

        # Append scheme list
        if schemes:
            scheme_lines = ["\n\n*Relevant Schemes:*"]
            for idx, scheme in enumerate(schemes[:5], 1):
                name = _extract_name(scheme)
                benefit = _extract_benefit(scheme)
                entry = f"{idx}. *{name}*"
                if benefit:
                    entry += f" - {str(benefit)[:60]}"
                scheme_lines.append(entry)

            scheme_section = "\n".join(scheme_lines)
            if len(text) + len(scheme_section) <= _WHATSAPP_MAX:
                text += scheme_section

        # HaqSetu footer
        footer = "\n\n_HaqSetu - Aapka Haq, Aapki Seva_"
        if len(text) + len(footer) <= _WHATSAPP_MAX:
            text += footer

        if len(text) > _WHATSAPP_MAX:
            text = text[:_WHATSAPP_MAX - 3] + "..."

        return text

    # ------------------------------------------------------------------
    # Delivery tracking
    # ------------------------------------------------------------------

    def get_delivery_status(self, message_id: str) -> DeliveryStatus | None:
        """Get the delivery status of a sent message.

        Parameters
        ----------
        message_id:
            The HaqSetu message ID returned from ``send_whatsapp``
            or ``send_sms``.

        Returns
        -------
        DeliveryStatus | None
            Current delivery status, or None if not found.
        """
        return self._delivery_log.get(message_id)

    def update_delivery_status(
        self,
        message_id: str,
        new_status: str,
        timestamp: datetime | None = None,
    ) -> bool:
        """Update delivery status from a webhook callback.

        Called when WhatsApp or SMS provider sends a delivery notification.

        Parameters
        ----------
        message_id:
            The HaqSetu message ID or provider message ID.
        new_status:
            New status string (maps to ``DeliveryState``).
        timestamp:
            When the status change occurred (default: now).

        Returns
        -------
        bool
            True if status was updated, False if message not found.
        """
        status = self._delivery_log.get(message_id)
        if status is None:
            for entry in self._delivery_log.values():
                if entry.provider_message_id == message_id:
                    status = entry
                    break

        if status is None:
            return False

        ts = timestamp or datetime.now(UTC)

        _status_map: dict[str, DeliveryState] = {
            "queued": DeliveryState.QUEUED,
            "sent": DeliveryState.SENT,
            "delivered": DeliveryState.DELIVERED,
            "read": DeliveryState.READ,
            "failed": DeliveryState.FAILED,
            "expired": DeliveryState.EXPIRED,
            "accepted": DeliveryState.SENT,
            "server": DeliveryState.SENT,
            "submitted": DeliveryState.SENT,
            "rejected": DeliveryState.FAILED,
            "ndnc": DeliveryState.FAILED,
        }

        mapped = _status_map.get(new_status.lower(), DeliveryState.SENT)
        status.status = mapped

        if mapped == DeliveryState.DELIVERED:
            status.delivered_at = ts
        elif mapped == DeliveryState.READ:
            status.read_at = ts

        logger.info(
            "messaging.status_updated",
            message_id=message_id,
            new_status=mapped,
        )
        return True

    def get_delivery_stats(self) -> dict[str, int]:
        """Get aggregate delivery statistics across all messages.

        Returns
        -------
        dict[str, int]
            Message counts grouped by delivery state.
        """
        stats: dict[str, int] = {
            "total": 0, "queued": 0, "sent": 0, "delivered": 0,
            "read": 0, "failed": 0, "mock": 0,
        }
        for entry in self._delivery_log.values():
            stats["total"] += 1
            key = entry.status.value
            if key in stats:
                stats[key] += 1
        return stats

    # ------------------------------------------------------------------
    # Webhook handlers
    # ------------------------------------------------------------------

    async def handle_whatsapp_webhook(
        self, payload: dict[str, Any],
    ) -> IncomingMessage | None:
        """Process an incoming WhatsApp Cloud API webhook payload.

        Handles both incoming user messages and delivery status updates
        from the Meta webhook callback.

        Parameters
        ----------
        payload:
            The webhook payload from Meta.

        Returns
        -------
        IncomingMessage | None
            Parsed incoming message, or None if this was a status update
            or the payload could not be parsed.
        """
        try:
            entry = payload.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})

            # Process delivery status updates
            for status_update in value.get("statuses", []):
                provider_id = status_update.get("id", "")
                wa_status = status_update.get("status", "")
                self.update_delivery_status(provider_id, wa_status)

            # Process incoming messages
            messages = value.get("messages", [])
            if not messages:
                return None

            msg = messages[0]
            msg_type = msg.get("type", "text")
            text = ""
            location = None
            is_button = False
            button_payload = None

            if msg_type == "text":
                text = msg.get("text", {}).get("body", "")
            elif msg_type == "interactive":
                interactive = msg.get("interactive", {})
                itype = interactive.get("type", "")
                if itype == "button_reply":
                    reply = interactive.get("button_reply", {})
                    text = reply.get("title", "")
                    button_payload = reply.get("id", "")
                    is_button = True
                elif itype == "list_reply":
                    reply = interactive.get("list_reply", {})
                    text = reply.get("title", "")
                    button_payload = reply.get("id", "")
                    is_button = True
            elif msg_type == "location":
                loc = msg.get("location", {})
                location = {
                    "latitude": loc.get("latitude", 0.0),
                    "longitude": loc.get("longitude", 0.0),
                }
                text = f"Location: {location['latitude']}, {location['longitude']}"

            from_number = msg.get("from", "")
            incoming = IncomingMessage(
                message_id=msg.get("id", uuid4().hex),
                channel="whatsapp",
                from_number=from_number,
                text=text,
                location=location,
                is_button_reply=is_button,
                button_payload=button_payload,
            )

            logger.info(
                "messaging.whatsapp_received",
                from_number=from_number,
                msg_type=msg_type,
                text_length=len(text),
            )
            return incoming

        except Exception:
            logger.error("messaging.whatsapp_webhook_error", exc_info=True)
            return None

    async def handle_sms_webhook(
        self, payload: dict[str, Any],
    ) -> IncomingMessage | None:
        """Process an incoming SMS webhook payload.

        Supports payloads from MSG91, Kaleyra, Textlocal, and Twilio
        by trying multiple common field names.

        Parameters
        ----------
        payload:
            The webhook payload from the SMS provider.

        Returns
        -------
        IncomingMessage | None
            Parsed incoming message, or None on parse error.
        """
        try:
            from_number = (
                payload.get("from")
                or payload.get("sender")
                or payload.get("mobile")
                or payload.get("From")
                or ""
            )
            text = (
                payload.get("text")
                or payload.get("message")
                or payload.get("content")
                or payload.get("Body")
                or ""
            )
            msg_id = str(
                payload.get("id")
                or payload.get("message_id")
                or payload.get("MessageSid")
                or uuid4().hex
            )

            if not from_number or not text:
                return None

            incoming = IncomingMessage(
                message_id=msg_id,
                channel="sms",
                from_number=sanitize_phone(from_number),
                text=text.strip(),
            )

            logger.info(
                "messaging.sms_received",
                from_number=incoming.from_number,
                text_length=len(incoming.text),
            )
            return incoming

        except Exception:
            logger.error("messaging.sms_webhook_error", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Template management
    # ------------------------------------------------------------------

    @staticmethod
    def get_template(template_name: str) -> MessageTemplate | None:
        """Get a message template by name."""
        return TEMPLATES.get(template_name)

    @staticmethod
    def get_sms_template(template_name: str) -> str | None:
        """Get an SMS template string by name."""
        return _SMS_TEMPLATES.get(template_name)

    @staticmethod
    def list_templates() -> dict[str, list[str]]:
        """List all available template names grouped by channel."""
        return {
            "whatsapp": sorted(TEMPLATES.keys()),
            "sms": sorted(_SMS_TEMPLATES.keys()),
        }

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an HTTP request with exponential-backoff retry.

        Retries on 5xx, 429, and transient connection errors up to
        ``_MAX_RETRIES`` times.
        """
        import asyncio

        import httpx

        last_exc: BaseException | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.request(
                        method, url, headers=headers, json=json_body,
                    )
                if response.status_code >= 500 or response.status_code == 429:
                    logger.warning(
                        "messaging.retryable_status",
                        status=response.status_code,
                        attempt=attempt + 1,
                        url=url,
                    )
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt])
                        continue
                return response

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "messaging.request_error",
                    error=str(exc),
                    attempt=attempt + 1,
                    url=url,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt])

        raise ConnectionError(
            f"All {_MAX_RETRIES} attempts failed for {url}"
        ) from last_exc


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _enforce_ussd_limit(text: str) -> str:
    """Truncate text to fit the USSD character limit (182 chars)."""
    if len(text) <= _USSD_MAX:
        return text
    return text[:_USSD_MAX - 3] + "..."


def _extract_name(obj: object) -> str:
    """Extract a display name from a scheme-like object or dict."""
    if isinstance(obj, dict):
        return str(
            obj.get("name")
            or obj.get("scheme_name")
            or obj.get("title")
            or "Unknown"
        )
    for attr in ("name", "scheme_name", "title"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    return str(obj)


def _extract_benefit(obj: object) -> str:
    """Extract a benefit description from a scheme-like object or dict."""
    if isinstance(obj, dict):
        return str(
            obj.get("benefits")
            or obj.get("estimated_benefit")
            or obj.get("benefit")
            or ""
        )
    for attr in ("benefits", "estimated_benefit", "benefit"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    return ""


def _calculate_sms_segments(message: str) -> int:
    """Calculate the number of SMS segments needed.

    Standard SMS: 160 chars per segment (7-bit GSM encoding).
    If message contains Unicode (Hindi etc.): 70 chars per segment.
    Multi-part: 153 chars per segment (7-bit) or 67 chars (Unicode).
    """
    gsm_chars = frozenset(
        "@$!\"#%&'()*+,-./0123456789:;<=>?"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        " \n\r"
    )
    is_unicode = any(char not in gsm_chars for char in message)
    length = len(message)

    if is_unicode:
        if length <= 70:
            return 1
        return (length + 66) // 67
    else:
        if length <= 160:
            return 1
        return (length + 152) // 153
