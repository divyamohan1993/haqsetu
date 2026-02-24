from __future__ import annotations

from enum import StrEnum


class ChannelType(StrEnum):
    __slots__ = ()

    IVR = "ivr"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    USSD = "ussd"
    MISSED_CALL_BACK = "missed_call_back"
    CSC_KIOSK = "csc_kiosk"
    WEB = "web"


class ContentType(StrEnum):
    __slots__ = ()

    AUDIO = "audio"
    TEXT = "text"
    DTMF = "dtmf"
    USSD_SELECTION = "ussd_selection"
    IMAGE = "image"
    LOCATION = "location"


class NetworkQuality(StrEnum):
    __slots__ = ()

    OFFLINE = "offline"
    TWO_G = "2g"
    THREE_G = "3g"
    FOUR_G = "4g"
    WIFI = "wifi"


class DeviceType(StrEnum):
    __slots__ = ()

    FEATURE_PHONE = "feature_phone"
    SMARTPHONE = "smartphone"
    CSC_KIOSK = "csc_kiosk"


class QueryIntent(StrEnum):
    __slots__ = ()

    SCHEME_SEARCH = "scheme_search"
    ELIGIBILITY_CHECK = "eligibility_check"
    APPLICATION_GUIDANCE = "application_guidance"
    STATUS_INQUIRY = "status_inquiry"
    MANDI_PRICE = "mandi_price"
    WEATHER_QUERY = "weather_query"
    SOIL_HEALTH = "soil_health"
    DOCUMENT_HELP = "document_help"
    PAYMENT_STATUS = "payment_status"
    GENERAL_INFO = "general_info"
    GREETING = "greeting"
    COMPLAINT = "complaint"
    HUMAN_ESCALATION = "human_escalation"


class LanguageCode(StrEnum):
    """ISO 639-1 codes for 22 scheduled languages of India + English."""

    __slots__ = ()

    hi = "hi"       # Hindi
    bn = "bn"       # Bengali
    te = "te"       # Telugu
    mr = "mr"       # Marathi
    ta = "ta"       # Tamil
    ur = "ur"       # Urdu
    gu = "gu"       # Gujarati
    kn = "kn"       # Kannada
    or_lang = "or"  # Odia — 'or' is a Python keyword
    ml = "ml"       # Malayalam
    pa = "pa"       # Punjabi
    as_lang = "as"  # Assamese — 'as' is a Python keyword
    mai = "mai"     # Maithili
    sat = "sat"     # Santali
    ks = "ks"       # Kashmiri
    ne = "ne"       # Nepali
    sd = "sd"       # Sindhi
    kok = "kok"     # Konkani
    doi = "doi"     # Dogri
    mni = "mni"     # Manipuri
    brx = "brx"     # Bodo
    sa = "sa"       # Sanskrit
    en = "en"       # English


class ServiceProvider(StrEnum):
    __slots__ = ()

    BHASHINI = "bhashini"
    SARVAM = "sarvam"
    GOOGLE = "google"
    AI4BHARAT = "ai4bharat"
    VERTEX_AI = "vertex_ai"
    GEMINI = "gemini"
