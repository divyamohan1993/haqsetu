"""Complete language configuration for all 22 Scheduled Languages of India + English.

Each ``LanguageConfig`` carries ISO codes, native names, script metadata,
GCP service codes (Translation / TTS / STT), and approximate speaker counts
so that downstream services can decide capabilities at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "LanguageConfig",
    "LANGUAGES",
    "LANGUAGE_CODE_MAP",
    "get_language",
    "get_supported_languages",
    "get_high_priority_languages",
    "get_gcp_tts_languages",
]


@dataclass(frozen=True, slots=True)
class LanguageConfig:
    """Immutable descriptor for a single language supported by HaqSetu."""

    code: str
    """ISO 639-1 (2-letter) or ISO 639-3 (3-letter) code."""

    name_english: str
    """Language name in English."""

    name_native: str
    """Language name in its own script."""

    script: str
    """Primary script used for the language."""

    gcp_translation_code: str
    """BCP-47 / ISO code accepted by Google Cloud Translation API."""

    gcp_tts_code: str | None
    """Google Cloud Text-to-Speech language code (e.g. ``hi-IN``), or ``None`` if unsupported."""

    gcp_stt_code: str | None
    """Google Cloud Speech-to-Text language code (e.g. ``hi-IN``), or ``None`` if unsupported."""

    population_millions: float
    """Approximate number of speakers in millions."""

    is_high_priority: bool
    """Whether this language is among the top-10 by speaker count."""


# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

LANGUAGES: Final[dict[str, LanguageConfig]] = {
    # ── High-priority languages (top 10 by speaker count) ──────────────
    "hi": LanguageConfig(
        code="hi",
        name_english="Hindi",
        name_native="हिन्दी",
        script="Devanagari",
        gcp_translation_code="hi",
        gcp_tts_code="hi-IN",
        gcp_stt_code="hi-IN",
        population_millions=600.0,
        is_high_priority=True,
    ),
    "bn": LanguageConfig(
        code="bn",
        name_english="Bengali",
        name_native="বাংলা",
        script="Bengali",
        gcp_translation_code="bn",
        gcp_tts_code="bn-IN",
        gcp_stt_code="bn-IN",
        population_millions=270.0,
        is_high_priority=True,
    ),
    "en": LanguageConfig(
        code="en",
        name_english="English",
        name_native="English",
        script="Latin",
        gcp_translation_code="en",
        gcp_tts_code="en-IN",
        gcp_stt_code="en-IN",
        population_millions=130.0,
        is_high_priority=True,
    ),
    "te": LanguageConfig(
        code="te",
        name_english="Telugu",
        name_native="తెలుగు",
        script="Telugu",
        gcp_translation_code="te",
        gcp_tts_code="te-IN",
        gcp_stt_code="te-IN",
        population_millions=96.0,
        is_high_priority=True,
    ),
    "mr": LanguageConfig(
        code="mr",
        name_english="Marathi",
        name_native="मराठी",
        script="Devanagari",
        gcp_translation_code="mr",
        gcp_tts_code="mr-IN",
        gcp_stt_code="mr-IN",
        population_millions=95.0,
        is_high_priority=True,
    ),
    "ta": LanguageConfig(
        code="ta",
        name_english="Tamil",
        name_native="தமிழ்",
        script="Tamil",
        gcp_translation_code="ta",
        gcp_tts_code="ta-IN",
        gcp_stt_code="ta-IN",
        population_millions=85.0,
        is_high_priority=True,
    ),
    "ur": LanguageConfig(
        code="ur",
        name_english="Urdu",
        name_native="اردو",
        script="Perso-Arabic",
        gcp_translation_code="ur",
        gcp_tts_code="ur-IN",
        gcp_stt_code="ur-IN",
        population_millions=70.0,
        is_high_priority=True,
    ),
    "gu": LanguageConfig(
        code="gu",
        name_english="Gujarati",
        name_native="ગુજરાતી",
        script="Gujarati",
        gcp_translation_code="gu",
        gcp_tts_code="gu-IN",
        gcp_stt_code="gu-IN",
        population_millions=60.0,
        is_high_priority=True,
    ),
    "kn": LanguageConfig(
        code="kn",
        name_english="Kannada",
        name_native="ಕನ್ನಡ",
        script="Kannada",
        gcp_translation_code="kn",
        gcp_tts_code="kn-IN",
        gcp_stt_code="kn-IN",
        population_millions=50.0,
        is_high_priority=True,
    ),
    "or": LanguageConfig(
        code="or",
        name_english="Odia",
        name_native="ଓଡ଼ିଆ",
        script="Odia",
        gcp_translation_code="or",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=40.0,
        is_high_priority=True,
    ),
    # ── Remaining Scheduled Languages ──────────────────────────────────
    "ml": LanguageConfig(
        code="ml",
        name_english="Malayalam",
        name_native="മലയാളം",
        script="Malayalam",
        gcp_translation_code="ml",
        gcp_tts_code="ml-IN",
        gcp_stt_code="ml-IN",
        population_millions=38.0,
        is_high_priority=False,
    ),
    "pa": LanguageConfig(
        code="pa",
        name_english="Punjabi",
        name_native="ਪੰਜਾਬੀ",
        script="Gurmukhi",
        gcp_translation_code="pa",
        gcp_tts_code="pa-IN",
        gcp_stt_code="pa-IN",
        population_millions=35.0,
        is_high_priority=False,
    ),
    "mai": LanguageConfig(
        code="mai",
        name_english="Maithili",
        name_native="मैथिली",
        script="Devanagari",
        gcp_translation_code="mai",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=35.0,
        is_high_priority=False,
    ),
    "as": LanguageConfig(
        code="as",
        name_english="Assamese",
        name_native="অসমীয়া",
        script="Bengali",
        gcp_translation_code="as",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=25.0,
        is_high_priority=False,
    ),
    "sat": LanguageConfig(
        code="sat",
        name_english="Santali",
        name_native="ᱥᱟᱱᱛᱟᱲᱤ",
        script="Ol Chiki",
        gcp_translation_code="sat",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=7.6,
        is_high_priority=False,
    ),
    "ks": LanguageConfig(
        code="ks",
        name_english="Kashmiri",
        name_native="कॉशुर",
        script="Perso-Arabic/Devanagari",
        gcp_translation_code="ks",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=7.0,
        is_high_priority=False,
    ),
    "ne": LanguageConfig(
        code="ne",
        name_english="Nepali",
        name_native="नेपाली",
        script="Devanagari",
        gcp_translation_code="ne",
        gcp_tts_code="ne-NP",
        gcp_stt_code="ne-NP",
        population_millions=3.0,
        is_high_priority=False,
    ),
    "sd": LanguageConfig(
        code="sd",
        name_english="Sindhi",
        name_native="سنڌي",
        script="Perso-Arabic/Devanagari",
        gcp_translation_code="sd",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=3.0,
        is_high_priority=False,
    ),
    "doi": LanguageConfig(
        code="doi",
        name_english="Dogri",
        name_native="डोगरी",
        script="Devanagari",
        gcp_translation_code="doi",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=3.0,
        is_high_priority=False,
    ),
    "kok": LanguageConfig(
        code="kok",
        name_english="Konkani",
        name_native="कोंकणी",
        script="Devanagari",
        gcp_translation_code="gom",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=2.5,
        is_high_priority=False,
    ),
    "mni": LanguageConfig(
        code="mni",
        name_english="Manipuri/Meitei",
        name_native="মৈতৈলোন্",
        script="Bengali/Meitei",
        gcp_translation_code="mni-Mtei",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=2.0,
        is_high_priority=False,
    ),
    "brx": LanguageConfig(
        code="brx",
        name_english="Bodo",
        name_native="बड़ो",
        script="Devanagari",
        gcp_translation_code="brx",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=1.5,
        is_high_priority=False,
    ),
    "sa": LanguageConfig(
        code="sa",
        name_english="Sanskrit",
        name_native="संस्कृतम्",
        script="Devanagari",
        gcp_translation_code="sa",
        gcp_tts_code=None,
        gcp_stt_code=None,
        population_millions=0.025,
        is_high_priority=False,
    ),
}


# ---------------------------------------------------------------------------
# Alternate code lookup table
# ---------------------------------------------------------------------------

LANGUAGE_CODE_MAP: Final[dict[str, str]] = {
    # ISO 639-2/T and 639-2/B alternates
    "hin": "hi",
    "ben": "bn",
    "eng": "en",
    "tel": "te",
    "mar": "mr",
    "tam": "ta",
    "urd": "ur",
    "guj": "gu",
    "kan": "kn",
    "ori": "or",
    "odi": "or",
    "mal": "ml",
    "pan": "pa",
    "asm": "as",
    "kas": "ks",
    "nep": "ne",
    "snd": "sd",
    "san": "sa",
    "bod": "brx",
    # Common alternate spellings / BCP-47 variants
    "hnd": "hi",
    "bng": "bn",
    "gom": "kok",
    "mni-Mtei": "mni",
    # Region-tagged variants map back to base code
    "hi-IN": "hi",
    "bn-IN": "bn",
    "en-IN": "en",
    "te-IN": "te",
    "mr-IN": "mr",
    "ta-IN": "ta",
    "ur-IN": "ur",
    "gu-IN": "gu",
    "kn-IN": "kn",
    "ml-IN": "ml",
    "pa-IN": "pa",
    "ne-NP": "ne",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_language(code: str) -> LanguageConfig | None:
    """Return the ``LanguageConfig`` for *code*, checking aliases.

    Returns ``None`` if the language is not found.
    """
    canonical = LANGUAGE_CODE_MAP.get(code, code)
    return LANGUAGES.get(canonical)


def get_supported_languages() -> list[LanguageConfig]:
    """Return all supported languages sorted by speaker count (descending)."""
    return sorted(LANGUAGES.values(), key=lambda lang: lang.population_millions, reverse=True)


def get_high_priority_languages() -> list[LanguageConfig]:
    """Return the top-10 high-priority languages sorted by speaker count (descending)."""
    return sorted(
        (lang for lang in LANGUAGES.values() if lang.is_high_priority),
        key=lambda lang: lang.population_millions,
        reverse=True,
    )


def get_gcp_tts_languages() -> list[LanguageConfig]:
    """Return languages that have Google Cloud TTS support."""
    return sorted(
        (lang for lang in LANGUAGES.values() if lang.gcp_tts_code is not None),
        key=lambda lang: lang.population_millions,
        reverse=True,
    )
