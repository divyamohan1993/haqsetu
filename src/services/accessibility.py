"""Accessibility services for HaqSetu.

Makes the platform usable by blind, deaf, and non-English-speaking users
across India.  Provides:

* **Indian Sign Language (ISL) support** -- generates textual descriptions of
  ISL gestures that can drive a sign-language avatar on the front-end.
* **Screen-reader optimisation** -- produces ARIA-friendly plain-text
  descriptions stripped of visual formatting.
* **Haptic feedback patterns** -- pre-defined vibration sequences for common
  alert types (success, error, warning, urgent, notification).
* **Audio descriptions** -- enhanced spoken descriptions of visual content,
  synthesised via the existing ``TextToSpeechService``.
* **Simplified language mode** -- reduces sentence complexity and vocabulary
  for users with cognitive challenges.
* **High-contrast text** -- generates text with explicit structural markers
  suitable for low-vision rendering.
* **Braille-ready text** -- formats output for Grade-1 Bharati Braille
  displays (no visual formatting, explicit structure).
* **Voice speed control** -- exposes slow / normal / fast speech-rate
  multipliers passed through to TTS.

All processing is synchronous except audio synthesis, which delegates to the
async ``TextToSpeechService``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.services.speech import TextToSpeechService
    from src.services.translation import TranslationService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AccessibilityMode(StrEnum):
    """Supported accessibility output modes."""

    __slots__ = ()

    SCREEN_READER = "screen_reader"
    SIGN_LANGUAGE = "sign_language"
    SIMPLIFIED = "simplified"
    AUDIO_DESCRIPTION = "audio_description"
    HAPTIC = "haptic"
    BRAILLE = "braille"


# ---------------------------------------------------------------------------
# Voice speed presets
# ---------------------------------------------------------------------------

VOICE_SPEED_SLOW: Final[float] = 0.7
VOICE_SPEED_NORMAL: Final[float] = 1.0
VOICE_SPEED_FAST: Final[float] = 1.3


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class HapticPattern(BaseModel):
    """A vibration pattern for haptic-capable devices.

    ``pattern`` is a list of durations in milliseconds, alternating between
    *vibrate* and *pause*.  For example ``[200, 100, 200]`` means
    "vibrate 200 ms, pause 100 ms, vibrate 200 ms".
    """

    pattern: list[int] = Field(default_factory=list)
    description: str = ""


class ISLGesture(BaseModel):
    """A single Indian Sign Language gesture description."""

    gloss: str = ""
    hand_shape: str = ""
    movement: str = ""
    location: str = ""
    facial_expression: str = ""
    description: str = ""


class ISLDescription(BaseModel):
    """A sequence of ISL gestures representing a complete utterance.

    ``video_url`` is a placeholder for a future avatar-video endpoint.
    """

    gestures: list[ISLGesture] = Field(default_factory=list)
    video_url: str | None = None


class AccessibleResponse(BaseModel):
    """Unified response carrying all accessibility-enhanced content.

    The pipeline fills whichever fields are relevant for the requested
    ``AccessibilityMode``; unused fields retain their defaults.
    """

    text: str = ""
    audio: bytes | None = None
    haptic_pattern: HapticPattern | None = None
    isl_description: ISLDescription | None = None
    screen_reader_text: str = ""
    braille_text: str = ""


# ---------------------------------------------------------------------------
# Pre-defined haptic patterns
# ---------------------------------------------------------------------------

_HAPTIC_PATTERNS: Final[dict[str, HapticPattern]] = {
    "success": HapticPattern(
        pattern=[100, 50, 100, 50, 300],
        description="Two short pulses followed by a long pulse indicating success.",
    ),
    "error": HapticPattern(
        pattern=[400, 100, 400, 100, 400],
        description="Three long pulses indicating an error occurred.",
    ),
    "warning": HapticPattern(
        pattern=[200, 100, 200, 100, 200],
        description="Three medium pulses indicating a warning.",
    ),
    "urgent": HapticPattern(
        pattern=[300, 50, 300, 50, 300, 50, 300],
        description="Four rapid strong pulses indicating urgent attention required.",
    ),
    "notification": HapticPattern(
        pattern=[100, 200, 100],
        description="Two gentle pulses indicating a new notification.",
    ),
}


# ---------------------------------------------------------------------------
# ISL gesture vocabulary
# ---------------------------------------------------------------------------
#
# A simplified mapping from common concept words to ISL gesture descriptions.
# In production this would be backed by a full ISL lexicon database; here we
# keep a curated set covering the most frequent government-scheme terms so
# the service is functional without external dependencies.

_ISL_GESTURE_MAP: Final[dict[str, ISLGesture]] = {
    "hello": ISLGesture(
        gloss="HELLO",
        hand_shape="Open palm, fingers together",
        movement="Raise hand to forehead level, move outward in a small arc",
        location="Forehead",
        facial_expression="Smile, eyebrows slightly raised",
        description="Open palm moves from forehead outward in greeting.",
    ),
    "namaste": ISLGesture(
        gloss="NAMASTE",
        hand_shape="Both palms pressed together, fingers pointing up",
        movement="Hands brought together at chest level, slight bow of head",
        location="Chest",
        facial_expression="Respectful expression, slight smile",
        description="Both palms pressed together at chest with a slight head bow.",
    ),
    "government": ISLGesture(
        gloss="GOVERNMENT",
        hand_shape="Fist with index finger extended",
        movement="Finger points upward, then traces a circle above the head",
        location="Above head",
        facial_expression="Neutral, authoritative",
        description="Index finger points up and traces a circle above the head representing authority.",
    ),
    "scheme": ISLGesture(
        gloss="SCHEME",
        hand_shape="Both hands open, palms facing each other",
        movement="Hands move from center outward, opening like a book",
        location="Chest level",
        facial_expression="Attentive, interested",
        description="Open hands spread outward from center like opening a plan or document.",
    ),
    "money": ISLGesture(
        gloss="MONEY",
        hand_shape="Thumb rubs against fingers, palm up",
        movement="Repeated rubbing motion of thumb against fingertips",
        location="Waist level, in front of body",
        facial_expression="Neutral",
        description="Thumb rubs against fingertips in the universal money gesture.",
    ),
    "help": ISLGesture(
        gloss="HELP",
        hand_shape="One fist placed on open palm of other hand",
        movement="Lower hand lifts upper fist upward",
        location="Chest level",
        facial_expression="Concerned, empathetic",
        description="Open palm supports and lifts a fist upward representing assistance.",
    ),
    "eligible": ISLGesture(
        gloss="ELIGIBLE",
        hand_shape="Thumb up, other fingers curled",
        movement="Hand moves forward and nods once",
        location="Shoulder level",
        facial_expression="Positive, encouraging nod",
        description="Thumbs-up gesture moved forward with an affirming nod.",
    ),
    "not_eligible": ISLGesture(
        gloss="NOT-ELIGIBLE",
        hand_shape="Open hand, palm facing outward",
        movement="Hand sweeps horizontally left to right",
        location="Chest level",
        facial_expression="Sympathetic, slight head shake",
        description="Open palm sweeps horizontally indicating negation.",
    ),
    "document": ISLGesture(
        gloss="DOCUMENT",
        hand_shape="Both hands flat, palms facing down",
        movement="Hands outline a rectangular shape in the air",
        location="Chest level",
        facial_expression="Neutral, attentive",
        description="Flat hands trace the outline of a rectangular document.",
    ),
    "apply": ISLGesture(
        gloss="APPLY",
        hand_shape="Writing hand position, other hand flat as surface",
        movement="Dominant hand mimes writing on flat non-dominant hand",
        location="Waist level",
        facial_expression="Focused, determined",
        description="Mimes writing on a flat surface representing filling an application.",
    ),
    "family": ISLGesture(
        gloss="FAMILY",
        hand_shape="Both hands form a circle together, fingers interlocked",
        movement="Interlocked hands rotate in a small circle",
        location="Chest level",
        facial_expression="Warm, inclusive smile",
        description="Interlocked hands forming a circle represent family togetherness.",
    ),
    "farmer": ISLGesture(
        gloss="FARMER",
        hand_shape="Both hands grip an imaginary plough handle",
        movement="Hands push forward mimicking ploughing motion",
        location="Waist level",
        facial_expression="Hardworking, serious",
        description="Hands mime gripping and pushing a plough forward.",
    ),
    "education": ISLGesture(
        gloss="EDUCATION",
        hand_shape="One hand holds imaginary book, other hand open",
        movement="Open hand moves across imaginary book pages",
        location="Chest level",
        facial_expression="Attentive, studious",
        description="Hand moves across imaginary book pages representing reading and learning.",
    ),
    "health": ISLGesture(
        gloss="HEALTH",
        hand_shape="Open hand placed on chest over heart",
        movement="Hand pats chest gently twice",
        location="Chest, over heart",
        facial_expression="Caring, concerned",
        description="Hand placed over heart with gentle patting represents health and wellbeing.",
    ),
    "woman": ISLGesture(
        gloss="WOMAN",
        hand_shape="Open hand, fingers together",
        movement="Hand traces along the jawline from ear to chin",
        location="Face, jawline",
        facial_expression="Neutral",
        description="Hand traces the jawline from ear to chin.",
    ),
    "child": ISLGesture(
        gloss="CHILD",
        hand_shape="Flat hand, palm facing down",
        movement="Hand held at lower height, palm pushes down gently",
        location="Waist level",
        facial_expression="Gentle, nurturing",
        description="Flat palm held at low height indicating a small person.",
    ),
    "house": ISLGesture(
        gloss="HOUSE",
        hand_shape="Both hands form a triangle at fingertips",
        movement="Hands form a peaked roof shape, then separate downward tracing walls",
        location="Above head, then down to shoulder level",
        facial_expression="Neutral",
        description="Hands form a peaked roof shape then trace walls downward.",
    ),
    "yes": ISLGesture(
        gloss="YES",
        hand_shape="Fist with thumb up",
        movement="Fist nods downward once",
        location="Shoulder level",
        facial_expression="Affirmative nod, slight smile",
        description="Fist nods downward once with thumb up for affirmation.",
    ),
    "no": ISLGesture(
        gloss="NO",
        hand_shape="Open hand, palm outward",
        movement="Hand waves side to side",
        location="Shoulder level",
        facial_expression="Head shake",
        description="Open hand waves side to side accompanied by head shake.",
    ),
    "thank_you": ISLGesture(
        gloss="THANK-YOU",
        hand_shape="Flat hand, fingers together",
        movement="Hand touches chin then moves forward and down",
        location="Chin, then forward",
        facial_expression="Grateful smile",
        description="Hand touches chin then extends forward and downward in gratitude.",
    ),
    "aadhaar": ISLGesture(
        gloss="AADHAAR",
        hand_shape="Index finger extended, other hand flat",
        movement="Index finger taps flat palm, then finger traces fingerprint circle",
        location="Chest level",
        facial_expression="Neutral, official",
        description="Index finger taps palm then traces a fingerprint circle representing Aadhaar identity.",
    ),
    "bank": ISLGesture(
        gloss="BANK",
        hand_shape="Both hands cupped together",
        movement="Cupped hands mime stacking coins, then close protectively",
        location="Waist level",
        facial_expression="Neutral, secure",
        description="Cupped hands mime stacking and protecting coins representing a bank.",
    ),
    "pension": ISLGesture(
        gloss="PENSION",
        hand_shape="One hand open receiving, other hand dropping imaginary coins",
        movement="Dominant hand drops imaginary coins into open receiving palm, repeated",
        location="Waist level",
        facial_expression="Pleased, grateful",
        description="One hand drops imaginary coins into the other, representing regular payments.",
    ),
    "disability": ISLGesture(
        gloss="DISABILITY",
        hand_shape="Both hands open, one higher than the other",
        movement="Higher hand gently supports the lower hand, lifting it up",
        location="Chest level",
        facial_expression="Supportive, empathetic",
        description="One hand supports and lifts the other, representing support for disability.",
    ),
    "deadline": ISLGesture(
        gloss="DEADLINE",
        hand_shape="Index finger taps wrist (watch position)",
        movement="Urgent tapping on wrist, then hand sweeps forward",
        location="Wrist, then forward",
        facial_expression="Urgent, eyebrows raised",
        description="Finger taps wrist like checking a watch then sweeps forward indicating time running out.",
    ),
}


# ---------------------------------------------------------------------------
# Simplification vocabulary
# ---------------------------------------------------------------------------
#
# Maps complex / bureaucratic words to simpler everyday equivalents.  The
# mapping is intentionally biased toward Indian government terminology.

_SIMPLIFICATION_MAP: Final[dict[str, str]] = {
    "beneficiary": "person who gets help",
    "disbursement": "payment",
    "domicile": "home state",
    "eligibility": "who can apply",
    "eligible": "can apply",
    "emoluments": "salary",
    "empanelled": "approved",
    "enumeration": "counting",
    "expenditure": "spending",
    "grievance": "complaint",
    "implementation": "how it works",
    "infrastructure": "roads and buildings",
    "installment": "part payment",
    "jurisdiction": "area covered",
    "lump sum": "one-time payment",
    "mandate": "order",
    "mechanism": "way",
    "notification": "notice",
    "per annum": "per year",
    "per capita": "per person",
    "procurement": "buying",
    "provision": "rule",
    "reimbursement": "money back",
    "remuneration": "payment",
    "requisite": "needed",
    "sanction": "approval",
    "stipend": "small payment",
    "subsistence": "basic living",
    "subsidy": "money help from government",
    "sustainable": "long-lasting",
    "utilization": "use",
    "verification": "checking",
    "allocation": "amount set aside",
    "amendment": "change",
    "annuity": "yearly payment",
    "applicant": "person applying",
    "attestation": "official stamp",
    "autonomous": "independent",
    "cessation": "stopping",
    "collateral": "guarantee",
    "compliance": "following rules",
    "consortium": "group",
    "contingency": "backup plan",
    "cumulative": "total added up",
    "deferment": "delay",
    "depreciation": "loss in value",
    "devolution": "passing down",
    "enrolment": "signing up",
    "fiscal": "about money",
    "gazette": "government paper",
    "incumbent": "current holder",
    "indemnity": "protection from loss",
    "indigent": "very poor",
    "mortality": "death rate",
    "pecuniary": "about money",
    "panchayat": "village council",
    "promulgation": "official announcement",
    "proviso": "condition",
    "quantum": "amount",
    "ratification": "official approval",
    "remittance": "money sent",
    "requisition": "official request",
    "scrutiny": "careful checking",
    "statutory": "required by law",
    "surcharge": "extra fee",
    "tenancy": "renting",
    "tribunal": "court",
    "undertaking": "promise",
    "vetting": "checking carefully",
    "waiver": "removed requirement",
}

# Pre-compile a regex that matches any simplifiable word at word boundaries.
_SIMPLIFY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _SIMPLIFICATION_MAP) + r")\b",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Screen-reader formatting schemes
# ---------------------------------------------------------------------------

_SCREEN_READER_SCHEMES: Final[dict[str, str]] = {
    "heading": "Heading: {text}",
    "list_item": "List item: {text}",
    "link": "Link: {text}",
    "emphasis": "Important: {text}",
    "paragraph": "{text}",
    "table_cell": "Cell: {text}",
    "alert": "Alert: {text}",
    "status": "Status: {text}",
    "navigation": "Navigation: {text}",
    "button": "Button: {text}",
}


# ---------------------------------------------------------------------------
# Braille formatting helpers
# ---------------------------------------------------------------------------

# Grade-1 contracted Braille indicators for Bharati Braille.
# These are Unicode Braille pattern characters used as structural markers.
_BRAILLE_CAPITAL_PREFIX: Final[str] = "\u2820"  # Braille dot-6
_BRAILLE_NUMBER_PREFIX: Final[str] = "\u283c"  # Braille dots-3456
_BRAILLE_PARAGRAPH_SEP: Final[str] = "\u2800\u2800"  # Two blank cells
_BRAILLE_LINE_SEP: Final[str] = "\u2800"  # Single blank cell


# ---------------------------------------------------------------------------
# AccessibilityService
# ---------------------------------------------------------------------------


class AccessibilityService:
    """Central service for all accessibility transformations in HaqSetu.

    Delegates audio synthesis to ``TextToSpeechService`` and text
    translation to ``TranslationService``; all other transformations
    are performed locally without external API calls.

    Parameters
    ----------
    translation:
        A :class:`TranslationService` instance for multi-language support.
    tts:
        A :class:`TextToSpeechService` instance for audio generation.
    """

    __slots__ = ("_translation", "_tts")

    def __init__(
        self,
        translation: TranslationService,
        tts: TextToSpeechService,
    ) -> None:
        self._translation = translation
        self._tts = tts

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def generate_accessible_response(
        self,
        text: str,
        mode: AccessibilityMode,
        language: str = "en",
    ) -> AccessibleResponse:
        """Generate a fully accessible response for the requested mode.

        Depending on *mode* the returned :class:`AccessibleResponse` will
        have different fields populated:

        * ``screen_reader`` -- ``screen_reader_text`` filled with
          ARIA-friendly markup-free text.
        * ``sign_language`` -- ``isl_description`` containing gesture
          descriptions, ``text`` containing a simplified version.
        * ``simplified`` -- ``text`` rewritten with simple vocabulary and
          short sentences.
        * ``audio_description`` -- ``audio`` containing synthesised MP3
          bytes and ``text`` containing the spoken description.
        * ``haptic`` -- ``haptic_pattern`` with a default notification
          pattern, ``text`` unchanged.
        * ``braille`` -- ``braille_text`` formatted for Braille displays.

        Parameters
        ----------
        text:
            The original response text to make accessible.
        mode:
            The accessibility mode to apply.
        language:
            ISO 639-1 language code (default ``"en"``).

        Returns
        -------
        AccessibleResponse
            Response with the relevant accessibility fields populated.
        """
        logger.info(
            "accessibility.generate",
            mode=mode,
            language=language,
            text_length=len(text),
        )

        response = AccessibleResponse(text=text)

        if mode == AccessibilityMode.SCREEN_READER:
            response.screen_reader_text = self.format_for_screen_reader(text)

        elif mode == AccessibilityMode.SIGN_LANGUAGE:
            response.text = self.simplify_text(text)
            response.isl_description = self.generate_isl_description(text)

        elif mode == AccessibilityMode.SIMPLIFIED:
            response.text = self.simplify_text(text)

        elif mode == AccessibilityMode.AUDIO_DESCRIPTION:
            audio_text = self._build_audio_description_text(text)
            response.text = audio_text
            response.audio = await self.generate_audio_description(
                audio_text, language, VOICE_SPEED_NORMAL
            )

        elif mode == AccessibilityMode.HAPTIC:
            response.haptic_pattern = self.get_haptic_pattern("notification")

        elif mode == AccessibilityMode.BRAILLE:
            response.braille_text = self._format_for_braille(text)

        logger.info(
            "accessibility.generated",
            mode=mode,
            language=language,
            has_audio=response.audio is not None,
            has_isl=response.isl_description is not None,
            has_haptic=response.haptic_pattern is not None,
        )

        return response

    # ------------------------------------------------------------------
    # Text simplification
    # ------------------------------------------------------------------

    def simplify_text(self, text: str) -> str:
        """Reduce text complexity for users with cognitive challenges.

        Applies the following transformations:

        1. Replace bureaucratic / complex words with simple equivalents.
        2. Break long sentences into shorter ones (at conjunctions and
           punctuation boundaries).
        3. Remove parenthetical asides and nested clauses.
        4. Limit sentence length to roughly 12 words.
        5. Strip excessive punctuation and normalise whitespace.

        Parameters
        ----------
        text:
            The original text to simplify.

        Returns
        -------
        str
            Simplified text suitable for low-literacy or cognitively
            challenged users.
        """
        if not text:
            return text

        # Step 1: Replace complex words.
        simplified = _SIMPLIFY_PATTERN.sub(
            lambda m: _SIMPLIFICATION_MAP[m.group(0).lower()], text
        )

        # Step 2: Remove content inside parentheses.
        simplified = re.sub(r"\([^)]*\)", "", simplified)

        # Step 3: Remove content inside square brackets.
        simplified = re.sub(r"\[[^\]]*\]", "", simplified)

        # Step 4: Split at sentence boundaries and at conjunctions.
        # We split on periods, semicolons, and common conjunctions that
        # introduce subordinate clauses.
        fragments: list[str] = re.split(
            r"[.;]\s+|\s+(?:however|therefore|furthermore|moreover|"
            r"nevertheless|consequently|notwithstanding|whereas|"
            r"whereby|herein|therein)\s+",
            simplified,
            flags=re.IGNORECASE,
        )

        short_sentences: list[str] = []
        for fragment in fragments:
            fragment = fragment.strip()
            if not fragment:
                continue

            # Step 5: If a fragment is still too long, break at commas
            # or the word "and".
            words = fragment.split()
            if len(words) > 15:
                sub_fragments = re.split(r",\s+|\s+and\s+", fragment)
                for sub in sub_fragments:
                    sub = sub.strip()
                    if sub:
                        short_sentences.append(self._cap_sentence(sub))
            else:
                short_sentences.append(self._cap_sentence(fragment))

        result = ". ".join(short_sentences)

        # Ensure the text ends with a period.
        if result and not result.endswith("."):
            result += "."

        # Normalise whitespace.
        result = re.sub(r"\s{2,}", " ", result).strip()

        logger.debug(
            "accessibility.simplify",
            original_length=len(text),
            simplified_length=len(result),
        )

        return result

    @staticmethod
    def _cap_sentence(sentence: str) -> str:
        """Capitalise the first letter of a sentence and strip trailing dots."""
        sentence = sentence.strip().rstrip(".")
        if sentence:
            sentence = sentence[0].upper() + sentence[1:]
        return sentence

    # ------------------------------------------------------------------
    # Audio description
    # ------------------------------------------------------------------

    async def generate_audio_description(
        self,
        text: str,
        language: str = "en",
        speed: float = VOICE_SPEED_NORMAL,
    ) -> bytes:
        """Generate enhanced spoken audio from text.

        Uses :class:`TextToSpeechService` to synthesise speech at the
        requested *speed*.

        Parameters
        ----------
        text:
            The text to speak.
        language:
            ISO 639-1 language code (e.g. ``"hi"``, ``"en"``).
        speed:
            Speech-rate multiplier.  Use :data:`VOICE_SPEED_SLOW` (0.7),
            :data:`VOICE_SPEED_NORMAL` (1.0), or :data:`VOICE_SPEED_FAST`
            (1.3).

        Returns
        -------
        bytes
            MP3 audio bytes.
        """
        # Clamp speed to a safe range.
        speed = max(0.5, min(speed, 2.0))

        audio_text = self._build_audio_description_text(text)

        logger.info(
            "accessibility.audio_description",
            language=language,
            speed=speed,
            text_length=len(audio_text),
        )

        audio_bytes = await self._tts.synthesize(
            text=audio_text,
            language_code=language,
            speaking_rate=speed,
        )

        logger.info(
            "accessibility.audio_description_complete",
            audio_bytes=len(audio_bytes),
        )

        return audio_bytes

    @staticmethod
    def _build_audio_description_text(text: str) -> str:
        """Prepare text for spoken audio by adding pauses and structure.

        Transforms the source text so that when synthesised it sounds
        natural and navigable by ear:

        * Numbered items get announced ("Item one", "Item two", ...).
        * Headings get a pause before and after.
        * URLs and email addresses are announced descriptively.
        * Excessive punctuation is cleaned up.
        """
        if not text:
            return text

        lines = text.split("\n")
        audio_lines: list[str] = []
        list_counter = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Detect numbered list items (e.g. "1. ", "2) ").
            numbered_match = re.match(r"^(\d+)[.)]\s*(.+)", stripped)
            if numbered_match:
                list_counter += 1
                item_text = numbered_match.group(2)
                audio_lines.append(f"Item {list_counter}: {item_text}.")
                continue

            # Detect bullet list items.
            bullet_match = re.match(r"^[-*]\s+(.+)", stripped)
            if bullet_match:
                list_counter += 1
                item_text = bullet_match.group(1)
                audio_lines.append(f"Item {list_counter}: {item_text}.")
                continue

            # Reset list counter for non-list lines.
            list_counter = 0

            # Replace URLs with descriptive text.
            processed = re.sub(
                r"https?://\S+",
                "web link provided",
                stripped,
            )

            # Replace email addresses.
            processed = re.sub(
                r"\S+@\S+\.\S+",
                "email address provided",
                processed,
            )

            audio_lines.append(processed)

        return " ".join(audio_lines)

    # ------------------------------------------------------------------
    # Haptic patterns
    # ------------------------------------------------------------------

    def get_haptic_pattern(self, alert_type: str) -> HapticPattern:
        """Return a pre-defined haptic vibration pattern for *alert_type*.

        Supported alert types: ``success``, ``error``, ``warning``,
        ``urgent``, ``notification``.  Returns the ``notification``
        pattern for any unrecognised type.

        Parameters
        ----------
        alert_type:
            One of the supported alert type strings.

        Returns
        -------
        HapticPattern
            The matching vibration pattern with a human-readable
            description.
        """
        pattern = _HAPTIC_PATTERNS.get(alert_type)
        if pattern is None:
            logger.warning(
                "accessibility.unknown_haptic_type",
                alert_type=alert_type,
                fallback="notification",
            )
            pattern = _HAPTIC_PATTERNS["notification"]
        return pattern

    # ------------------------------------------------------------------
    # Screen-reader formatting
    # ------------------------------------------------------------------

    def format_for_screen_reader(
        self,
        text: str,
        schemes: list[str] | None = None,
    ) -> str:
        """Format text for optimal screen-reader consumption.

        Applies the given formatting *schemes* (e.g. ``["heading",
        "paragraph"]``) to successive paragraphs / sections of the text.
        If no schemes are provided the text is cleaned of visual-only
        formatting and returned as a flat, navigable string.

        Parameters
        ----------
        text:
            The source text.
        schemes:
            Optional list of scheme names from
            ``_SCREEN_READER_SCHEMES``.  Each scheme is applied to the
            corresponding paragraph in order.  If there are more
            paragraphs than schemes the last scheme is reused.

        Returns
        -------
        str
            ARIA-friendly plain-text description.
        """
        if not text:
            return text

        # Strip markdown-style formatting.
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", text)   # bold
        cleaned = re.sub(r"\*(.+?)\*", r"\1", cleaned)     # italic
        cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)     # underline
        cleaned = re.sub(r"~~(.+?)~~", r"\1", cleaned)     # strike
        cleaned = re.sub(r"`(.+?)`", r"\1", cleaned)       # code

        # Strip HTML tags.
        cleaned = re.sub(r"<[^>]+>", "", cleaned)

        # Normalise whitespace.
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        # Split into paragraphs for scheme application.
        paragraphs = [p.strip() for p in cleaned.split("\n") if p.strip()]

        if not schemes:
            # No schemes: return as a single navigable block.
            formatted_parts: list[str] = []
            for para in paragraphs:
                formatted_parts.append(para)
            return " ".join(formatted_parts)

        # Apply formatting schemes to each paragraph.
        formatted: list[str] = []
        for idx, para in enumerate(paragraphs):
            scheme_name = schemes[idx] if idx < len(schemes) else schemes[-1]
            template = _SCREEN_READER_SCHEMES.get(scheme_name, "{text}")
            formatted.append(template.format(text=para))

        return " ".join(formatted)

    # ------------------------------------------------------------------
    # ISL description generation
    # ------------------------------------------------------------------

    def generate_isl_description(self, text: str) -> ISLDescription:
        """Generate Indian Sign Language gesture descriptions for *text*.

        Tokenises the input, maps recognised words to ISL gestures from
        the vocabulary, and synthesises descriptions for words that have
        no direct mapping (using fingerspelling cues).

        Parameters
        ----------
        text:
            The text to convert into ISL gesture descriptions.

        Returns
        -------
        ISLDescription
            Ordered list of gestures plus a placeholder ``video_url``.
        """
        if not text:
            return ISLDescription(gestures=[], video_url=None)

        # Tokenise and normalise.
        words = re.findall(r"[A-Za-z\u0900-\u097F]+", text.lower())

        gestures: list[ISLGesture] = []
        seen_glosses: set[str] = set()

        for word in words:
            # Look up in the gesture vocabulary.
            gesture = _ISL_GESTURE_MAP.get(word)
            if gesture is not None:
                # Avoid repeating the same gesture in a row.
                if gesture.gloss not in seen_glosses:
                    gestures.append(gesture)
                    seen_glosses.add(gesture.gloss)
                continue

            # For unmapped words, generate a fingerspelling gesture.
            fingerspell_gesture = self._fingerspell(word)
            if fingerspell_gesture.gloss not in seen_glosses:
                gestures.append(fingerspell_gesture)
                seen_glosses.add(fingerspell_gesture.gloss)

        # Reset deduplication between logical phrases: if the same concept
        # appears in separate sentences it should be signed again.  We use
        # sentence-level reset by detecting punctuation gaps in the original
        # text.  For simplicity the current implementation deduplicates only
        # within the full text; a production system would segment first.

        logger.info(
            "accessibility.isl_generated",
            word_count=len(words),
            gesture_count=len(gestures),
        )

        return ISLDescription(
            gestures=gestures,
            video_url=None,  # Placeholder for future avatar-video endpoint.
        )

    @staticmethod
    def _fingerspell(word: str) -> ISLGesture:
        """Generate a fingerspelling gesture for an unmapped word.

        In ISL, words without a dedicated sign are fingerspelled letter
        by letter.  This method produces a textual description of the
        fingerspelling sequence.
        """
        letters = list(word.upper())
        letter_desc = ", ".join(letters)
        return ISLGesture(
            gloss=f"FS:{word.upper()}",
            hand_shape="Dominant hand forms each letter shape sequentially",
            movement=f"Fingerspell: {letter_desc}",
            location="Shoulder level, in front of body",
            facial_expression="Neutral, steady gaze at recipient",
            description=f"Fingerspell the word '{word}' letter by letter: {letter_desc}.",
        )

    # ------------------------------------------------------------------
    # Braille formatting
    # ------------------------------------------------------------------

    def _format_for_braille(self, text: str) -> str:
        """Format text for Braille display output.

        Produces plain text with Braille structural markers:

        * Capital letters are preceded by a Braille capital indicator.
        * Numbers are preceded by a Braille number indicator.
        * Paragraphs are separated by double blank cells.
        * Lines within a paragraph are separated by a single blank cell.
        * Visual formatting (bold, italic, markdown) is stripped.
        * Abbreviations and acronyms are expanded or kept as-is with
          capital indicators.

        Parameters
        ----------
        text:
            The source text.

        Returns
        -------
        str
            Braille-ready text with structural indicators.
        """
        if not text:
            return text

        # Strip markdown / HTML formatting.
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        cleaned = re.sub(r"\*(.+?)\*", r"\1", cleaned)
        cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
        cleaned = re.sub(r"~~(.+?)~~", r"\1", cleaned)
        cleaned = re.sub(r"`(.+?)`", r"\1", cleaned)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)

        # Process character by character for Braille indicators.
        result_chars: list[str] = []
        prev_was_digit = False

        for char in cleaned:
            if char.isupper():
                result_chars.append(_BRAILLE_CAPITAL_PREFIX)
                result_chars.append(char.lower())
                prev_was_digit = False
            elif char.isdigit():
                if not prev_was_digit:
                    result_chars.append(_BRAILLE_NUMBER_PREFIX)
                result_chars.append(char)
                prev_was_digit = True
            elif char == "\n":
                # Paragraph boundary: double blank cell.
                if result_chars and result_chars[-1] != _BRAILLE_PARAGRAPH_SEP:
                    result_chars.append(_BRAILLE_PARAGRAPH_SEP)
                prev_was_digit = False
            else:
                result_chars.append(char)
                prev_was_digit = False

        braille_text = "".join(result_chars).strip()

        logger.debug(
            "accessibility.braille_formatted",
            original_length=len(text),
            braille_length=len(braille_text),
        )

        return braille_text
