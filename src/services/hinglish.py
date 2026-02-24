"""Hinglish (Hindi-English code-mixing) processor.

Detects, normalises, and extracts intent keywords from Hinglish text --
the mixed Hindi-English register commonly used across rural and
semi-urban India, especially when discussing government schemes.

All processing is **O(n)** where *n* = ``len(text)`` -- no nested loops.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Unicode ranges
# ---------------------------------------------------------------------------

_DEVANAGARI_RE: Final[re.Pattern[str]] = re.compile(r"[\u0900-\u097F]")
_LATIN_ALPHA_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]")
_WORD_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[^\w]+", flags=re.UNICODE)

# ---------------------------------------------------------------------------
# Romanised Hindi word -> intent category mapping  (50+ entries)
#
# Categories align with ``QueryIntent`` in src/models/enums.py.
# ---------------------------------------------------------------------------

COMMON_HINDI_ROMAN_WORDS: Final[dict[str, str]] = {
    # -- scheme_search --
    "yojana": "scheme_search",
    "yojna": "scheme_search",
    "scheme": "scheme_search",
    "sarkari": "scheme_search",
    "sarkar": "scheme_search",
    "government": "scheme_search",
    "labh": "scheme_search",
    "laabh": "scheme_search",
    "suvidha": "scheme_search",
    "sahayata": "scheme_search",
    "subsidy": "scheme_search",
    "anudan": "scheme_search",
    "rahat": "scheme_search",
    "mudra": "scheme_search",
    # -- payment_status --
    "paisa": "payment_status",
    "paise": "payment_status",
    "money": "payment_status",
    "rupaya": "payment_status",
    "rupaye": "payment_status",
    "bhugtan": "payment_status",
    "payment": "payment_status",
    "kist": "payment_status",
    "installment": "payment_status",
    "rashi": "payment_status",
    "amount": "payment_status",
    "balance": "payment_status",
    "status": "payment_status",
    # -- agriculture --
    "kisan": "agriculture",
    "kisaan": "agriculture",
    "farmer": "agriculture",
    "khet": "agriculture",
    "kheti": "agriculture",
    "fasal": "agriculture",
    "crop": "agriculture",
    "beej": "agriculture",
    "seed": "agriculture",
    "mandi": "agriculture",
    "krishi": "agriculture",
    "sinchai": "agriculture",
    "irrigation": "agriculture",
    "urvarak": "agriculture",
    "fertilizer": "agriculture",
    "tractor": "agriculture",
    "zameen": "agriculture",
    "jamin": "agriculture",
    # -- health --
    "dawai": "health",
    "dawa": "health",
    "medicine": "health",
    "hospital": "health",
    "aspatal": "health",
    "doctor": "health",
    "ilaj": "health",
    "treatment": "health",
    "bimari": "health",
    "sehat": "health",
    "swasthya": "health",
    "ayushman": "health",
    "bima": "health",
    "insurance": "health",
    "janani": "health",
    # -- education --
    "school": "education",
    "padhai": "education",
    "padai": "education",
    "vidyalaya": "education",
    "college": "education",
    "scholarship": "education",
    "chhatravriti": "education",
    "shiksha": "education",
    "taleem": "education",
    "exam": "education",
    "pariksha": "education",
    "kitab": "education",
    # -- housing --
    "ghar": "housing",
    "makaan": "housing",
    "makan": "housing",
    "house": "housing",
    "awas": "housing",
    "aawas": "housing",
    "flat": "housing",
    "plot": "housing",
    "indira": "housing",
    "pradhan": "housing",
    "pucca": "housing",
    # -- employment --
    "naukri": "employment",
    "naukari": "employment",
    "kaam": "employment",
    "rojgar": "employment",
    "rozgar": "employment",
    "job": "employment",
    "employment": "employment",
    "manrega": "employment",
    "mnrega": "employment",
    "nrega": "employment",
    "mazdoor": "employment",
    "majdoor": "employment",
    "labour": "employment",
    "berozgar": "employment",
    "berozgari": "employment",
    # -- senior_citizen --
    "pension": "senior_citizen",
    "buddha": "senior_citizen",
    "budhapa": "senior_citizen",
    "vridha": "senior_citizen",
    "vriddha": "senior_citizen",
    "vriddhawastha": "senior_citizen",
    "old": "senior_citizen",
    "retire": "senior_citizen",
    # -- women --
    "mahila": "women",
    "aurat": "women",
    "stri": "women",
    "nari": "women",
    "beti": "women",
    "ladki": "women",
    "matritva": "women",
    "maternity": "women",
    "mangalsutra": "women",
    "ujjwala": "women",
    # -- child_welfare --
    "baccha": "child_welfare",
    "bachcha": "child_welfare",
    "bachche": "child_welfare",
    "balak": "child_welfare",
    "balika": "child_welfare",
    "child": "child_welfare",
    "poshan": "child_welfare",
    "nutrition": "child_welfare",
    "anganwadi": "child_welfare",
    "midday": "child_welfare",
    "doodh": "child_welfare",
    # -- document_help --
    "aadhaar": "document_help",
    "aadhar": "document_help",
    "aadhar_card": "document_help",
    "ration": "document_help",
    "card": "document_help",
    "certificate": "document_help",
    "praman": "document_help",
    "pramaan": "document_help",
    "patta": "document_help",
    "domicile": "document_help",
    "jaati": "document_help",
    "caste": "document_help",
    "voter": "document_help",
    "pan": "document_help",
    "licence": "document_help",
    "license": "document_help",
    "passport": "document_help",
    # -- eligibility_check --
    "patrata": "eligibility_check",
    "eligible": "eligibility_check",
    "eligibility": "eligibility_check",
    "yogya": "eligibility_check",
    "patra": "eligibility_check",
    # -- complaint --
    "shikayat": "complaint",
    "complaint": "complaint",
    "samasyaa": "complaint",
    "samasya": "complaint",
    "problem": "complaint",
    "pareshani": "complaint",
    "pareshan": "complaint",
    "nahi_mila": "complaint",
    # -- general identity / reservation categories (used as modifiers) --
    "garib": "eligibility_check",
    "gareeb": "eligibility_check",
    "bpl": "eligibility_check",
    "sc": "eligibility_check",
    "st": "eligibility_check",
    "obc": "eligibility_check",
    "ews": "eligibility_check",
    "viklang": "eligibility_check",
    "divyang": "eligibility_check",
    "handicap": "eligibility_check",
    "disability": "eligibility_check",
    # -- utilities --
    "bijli": "general_info",
    "electricity": "general_info",
    "paani": "general_info",
    "pani": "general_info",
    "water": "general_info",
    "sadak": "general_info",
    "road": "general_info",
    "shauchalay": "general_info",
    "toilet": "general_info",
    "gas": "general_info",
    "cylinder": "general_info",
}

# Pre-compute a frozenset for O(1) membership tests
_HINDI_ROMAN_WORDS_SET: Final[frozenset[str]] = frozenset(COMMON_HINDI_ROMAN_WORDS)

# ---------------------------------------------------------------------------
# Government-specific Romanised Hindi -> Devanagari normalisation
# ---------------------------------------------------------------------------

_ROMAN_TO_DEVANAGARI: Final[dict[str, str]] = {
    "sarkari": "\u0938\u0930\u0915\u093e\u0930\u0940",
    "sarkar": "\u0938\u0930\u0915\u093e\u0930",
    "yojana": "\u092f\u094b\u091c\u0928\u093e",
    "yojna": "\u092f\u094b\u091c\u0928\u093e",
    "aadhaar": "\u0906\u0927\u093e\u0930",
    "aadhar": "\u0906\u0927\u093e\u0930",
    "kisan": "\u0915\u093f\u0938\u093e\u0928",
    "kisaan": "\u0915\u093f\u0938\u093e\u0928",
    "ration": "\u0930\u093e\u0936\u0928",
    "pension": "\u092a\u0947\u0902\u0936\u0928",
    "paisa": "\u092a\u0948\u0938\u093e",
    "paise": "\u092a\u0948\u0938\u0947",
    "rupaya": "\u0930\u0941\u092a\u092f\u093e",
    "bijli": "\u092c\u093f\u091c\u0932\u0940",
    "paani": "\u092a\u093e\u0928\u0940",
    "pani": "\u092a\u093e\u0928\u0940",
    "garib": "\u0917\u0930\u0940\u092c",
    "gareeb": "\u0917\u0930\u0940\u092c",
    "mahila": "\u092e\u0939\u093f\u0932\u093e",
    "shiksha": "\u0936\u093f\u0915\u094d\u0937\u093e",
    "swasthya": "\u0938\u094d\u0935\u093e\u0938\u094d\u0925\u094d\u092f",
    "rojgar": "\u0930\u094b\u091c\u0917\u093e\u0930",
    "rozgar": "\u0930\u094b\u091c\u0917\u093e\u0930",
    "awas": "\u0906\u0935\u093e\u0938",
    "naukri": "\u0928\u094c\u0915\u0930\u0940",
    "dawai": "\u0926\u0935\u093e\u0908",
    "shikayat": "\u0936\u093f\u0915\u093e\u092f\u0924",
    "patrata": "\u092a\u093e\u0924\u094d\u0930\u0924\u093e",
    "krishi": "\u0915\u0943\u0937\u093f",
    "vidyalaya": "\u0935\u093f\u0926\u094d\u092f\u093e\u0932\u092f",
    "pradhan": "\u092a\u094d\u0930\u0927\u093e\u0928",
    "mantri": "\u092e\u0902\u0924\u094d\u0930\u0940",
    "sahayata": "\u0938\u0939\u093e\u092f\u0924\u093e",
    "suvidha": "\u0938\u0941\u0935\u093f\u0927\u093e",
    "baccha": "\u092c\u091a\u094d\u091a\u093e",
    "bachcha": "\u092c\u091a\u094d\u091a\u093e",
    "vridha": "\u0935\u0943\u0926\u094d\u0927\u093e",
}

# Pre-build set for O(1) lookups
_ROMAN_DEVANAGARI_KEYS: Final[frozenset[str]] = frozenset(_ROMAN_TO_DEVANAGARI)


# ---------------------------------------------------------------------------
# HinglishProcessor
# ---------------------------------------------------------------------------


class HinglishProcessor:
    """Detects, normalises, and extracts intent keywords from Hinglish text.

    All public methods run in **O(n)** time where *n* = ``len(text)``.
    """

    __slots__ = ()

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def is_hinglish(text: str) -> bool:
        """Return ``True`` if *text* appears to be Hinglish (code-mixed).

        Detection heuristics (all O(n)):

        1. If the text contains **both** Latin and Devanagari characters
           it is definitively code-mixed.
        2. If the text is entirely Latin-script but contains known
           Romanised Hindi words, it is likely Hinglish.
        """
        has_devanagari = False
        has_latin = False

        for ch in text:
            if "\u0900" <= ch <= "\u097F":
                has_devanagari = True
            elif ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
                has_latin = True
            # Early exit once both scripts are detected
            if has_devanagari and has_latin:
                return True

        # If both scripts are present we already returned True above.
        # If only Latin script, check for Romanised Hindi vocabulary.
        if has_latin and not has_devanagari:
            return _has_roman_hindi_words(text)

        return False

    # ------------------------------------------------------------------ #
    # Normalisation
    # ------------------------------------------------------------------ #

    @staticmethod
    def normalize(text: str) -> str:
        """Normalise common Romanised Hindi government terms to Devanagari.

        Words not in the normalisation dictionary are left unchanged so
        English fragments remain readable.  The algorithm is a single
        pass over the token list -- O(n).
        """
        tokens = _WORD_SPLIT_RE.split(text)
        # We also need the delimiters to reconstruct the string faithfully.
        delimiters = _WORD_SPLIT_RE.findall(text)

        out: list[str] = []
        for i, token in enumerate(tokens):
            lower = token.lower()
            replacement = _ROMAN_TO_DEVANAGARI.get(lower)
            if replacement is not None:
                out.append(replacement)
            else:
                out.append(token)
            # Re-insert the delimiter that followed this token
            if i < len(delimiters):
                out.append(delimiters[i])

        return "".join(out)

    # ------------------------------------------------------------------ #
    # Keyword extraction
    # ------------------------------------------------------------------ #

    @staticmethod
    def extract_intent_keywords(text: str) -> list[str]:
        """Extract scheme/government-related intent keywords from *text*.

        Returns a deduplicated list of intent category strings (e.g.
        ``["agriculture", "payment_status"]``) preserving first-seen
        order.  Runs in O(n).
        """
        seen: set[str] = set()
        result: list[str] = []

        for token in _WORD_SPLIT_RE.split(text):
            lower = token.lower()
            category = COMMON_HINDI_ROMAN_WORDS.get(lower)
            if category is not None and category not in seen:
                seen.add(category)
                result.append(category)

        return result


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _has_roman_hindi_words(text: str) -> bool:
    """Check whether *text* (assumed all-Latin) contains Romanised Hindi words.

    Returns ``True`` if at least **two** known Hindi words are found, or
    if a single very distinctive Hindi word is present.  This avoids
    false positives on pure English text that happens to contain a word
    like "old" or "card".
    """
    # High-signal words that alone indicate Hindi origin
    _high_signal: frozenset[str] = frozenset({
        "yojana", "yojna", "sarkari", "sarkar",
        "aadhaar", "aadhar", "kisan", "kisaan",
        "paisa", "paise", "rupaya", "rupaye",
        "bijli", "paani", "pani", "garib", "gareeb",
        "dawai", "mahila", "naukri", "rojgar", "rozgar",
        "shikayat", "baccha", "bachcha", "bachche",
        "manrega", "mnrega", "nrega", "patrata",
        "vridha", "vriddha", "budhapa", "mazdoor",
        "majdoor", "anganwadi", "ujjwala", "ayushman",
        "pradhan", "krishi", "sinchai", "berozgar",
        "shauchalay", "chhatravriti",
    })

    count = 0
    for token in _WORD_SPLIT_RE.split(text):
        lower = token.lower()
        if lower in _HINDI_ROMAN_WORDS_SET:
            if lower in _high_signal:
                return True
            count += 1
            if count >= 2:
                return True
    return False
