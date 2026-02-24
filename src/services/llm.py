"""Vertex AI Gemini LLM service for HaqSetu.

Wraps the ``vertexai`` SDK to provide scheme-advisory responses, intent
classification, and eligibility checking via Gemini Flash.  All prompts
are tuned for voice-first delivery to rural Indian users.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

import structlog
import vertexai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from vertexai.generative_models import (
    Content,
    GenerationConfig,
    GenerativeModel,
    Part,
)

from src.models.enums import QueryIntent

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

HAQSETU_SYSTEM_PROMPT: Final[str] = """\
You are HaqSetu, a friendly and knowledgeable government scheme advisor \
built to help Indian citizens -- especially those living in rural areas \
-- discover, understand, and apply for government welfare schemes at \
the central and state levels.

ROLE AND PERSONA
- You are speaking with people who may have limited formal education. \
Use simple, everyday language. Avoid jargon, bureaucratic terms, and \
English words unless they are commonly understood.
- Be warm, respectful, and patient. Many callers are nervous about \
dealing with the government. Reassure them that you are here to help.
- Always address the user respectfully. Use polite forms of address \
appropriate to the conversation language.

CORE CAPABILITIES
1. Scheme Discovery: When a user describes their situation (farmer \
facing drought, pregnant woman needing healthcare, student seeking \
scholarship), identify ALL relevant central and state government schemes \
they may be eligible for.
2. Eligibility Assessment: Ask targeted questions about age, income, \
caste category, occupation, location, land holding, and family \
composition to determine eligibility. Be explicit about which criteria \
are met and which need verification.
3. Application Guidance: Provide clear, step-by-step instructions on \
how to apply -- including required documents, where to go (block \
office, CSC centre, gram panchayat), and expected timelines.
4. Status Tracking: Help users understand how to check the status of \
their existing applications and what to do if there are delays.
5. Grievance Support: If a user has been denied benefits unfairly or \
faces corruption, guide them to the appropriate grievance redressal \
mechanism, toll-free helpline, or RTI process.

RESPONSE GUIDELINES
- Always cite specific scheme names, e.g. "Pradhan Mantri Kisan Samman \
Nidhi (PM-KISAN)" rather than vague references.
- When listing eligibility criteria, use numbered points.
- Provide relevant helpline numbers when available, for example: \
PM-KISAN helpline 155261, Ayushman Bharat helpline 14555, UMANG app \
helpline 1800-111-8657.
- If the user seems eligible, proactively tell them the next concrete \
step they should take.
- If you are not sure about eligibility, say so honestly and recommend \
they visit their nearest Common Service Centre (CSC) or gram panchayat \
office for confirmation.

FORMAT FOR VOICE
- Keep sentences short -- no more than 15 to 20 words each.
- Do NOT use markdown, bullet points, asterisks, or special characters. \
These will be read aloud by a text-to-speech engine.
- Use numbered lists only when giving step-by-step instructions.
- Spell out abbreviations on first use.
- Pause-friendly: structure answers so natural pauses occur between \
key pieces of information.

SAFETY AND ETHICS
- Never give legal advice. If the user has a legal dispute, recommend \
they consult a lawyer or contact the District Legal Services Authority \
(DLSA) for free legal aid.
- Never ask for Aadhaar numbers, bank account numbers, or passwords. \
If the user volunteers such information, remind them to keep it \
private and never share it over the phone.
- Be sensitive to the user's economic context. Do not make assumptions \
about literacy, digital access, or financial capacity.
- If the user expresses distress or mentions self-harm, provide the \
iCALL helpline number (9152987821) and recommend they speak to a \
counselor immediately.

KNOWLEDGE BOUNDARIES
- If you do not know the answer or the scheme details have changed \
recently, say so clearly. Do not fabricate scheme names or eligibility \
criteria.
- When in doubt, recommend the user call the relevant ministry \
helpline or visit https://www.myscheme.gov.in for the latest \
information.\
"""

# Approximate cost per million tokens for Gemini 2.0 Flash (USD).
_COST_PER_M_INPUT_TOKENS: Final[float] = 0.10
_COST_PER_M_OUTPUT_TOKENS: Final[float] = 0.40

# Intent classification prompt -- returns structured JSON.
_INTENT_PROMPT: Final[str] = """\
Classify the following user query into exactly one intent category.
Return ONLY a JSON object with the key "intent" and optionally "confidence" (0-1).

Valid intents: scheme_search, eligibility_check, application_guidance, \
status_inquiry, mandi_price, weather_query, soil_health, document_help, \
payment_status, general_info, greeting, complaint, human_escalation.

User query: {text}

JSON response:\
"""

# Eligibility checking prompt.
_ELIGIBILITY_PROMPT: Final[str] = """\
You are an eligibility assessment engine. Given the user profile and \
scheme criteria below, determine whether the user is likely eligible.

User Profile:
{user_profile}

Scheme Eligibility Criteria:
{scheme_criteria}

Return a JSON object with these keys:
- "eligible": boolean (true if likely eligible, false otherwise)
- "matched_criteria": list of criteria the user meets
- "missing_info": list of criteria that cannot be verified from the \
profile (information the user still needs to provide)
- "confidence": float 0-1 indicating how confident you are

JSON response:\
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LLMResult:
    """Result returned by :meth:`LLMService.generate`."""

    answer: str
    intent: str
    confidence: float
    tokens_used: dict[str, int]
    cost_usd: float
    processing_time_ms: float
    provider: str = field(default="gemini")


# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------


class LLMService:
    """Async interface to Vertex AI Gemini for HaqSetu.

    Provides three high-level operations:

    * **generate** -- free-form advisory responses
    * **classify_intent** -- fast structured intent extraction
    * **check_eligibility** -- profile-vs-scheme matching
    """

    def __init__(
        self,
        project_id: str,
        region: str = "asia-south1",
        model_name: str = "gemini-2.0-flash",
    ) -> None:
        self._project_id = project_id
        self._region = region
        self._model_name = model_name
        self._model: GenerativeModel | None = None
        self._initialized = False

    # -- lifecycle ----------------------------------------------------------

    def _initialize(self) -> None:
        """Lazily initialize the Vertex AI SDK and model handle."""
        if self._initialized:
            return
        vertexai.init(project=self._project_id, location=self._region)
        self._model = GenerativeModel(
            model_name=self._model_name,
            system_instruction=[Part.from_text(HAQSETU_SYSTEM_PROMPT)],
        )
        self._initialized = True
        logger.info(
            "llm_initialized",
            project=self._project_id,
            region=self._region,
            model=self._model_name,
        )

    def _get_model(self) -> GenerativeModel:
        self._initialize()
        assert self._model is not None  # noqa: S101
        return self._model

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _build_contents(
        prompt: str,
        context: str,
        conversation_history: list[dict] | None,
    ) -> list[Content]:
        """Assemble a ``contents`` list suitable for ``generate_content_async``."""
        contents: list[Content] = []

        # Replay conversation history, if any.
        if conversation_history:
            for turn in conversation_history:
                role = turn.get("role", "user")
                text = turn.get("text", "")
                if not text:
                    continue
                contents.append(
                    Content(
                        role=role,
                        parts=[Part.from_text(text)],
                    )
                )

        # Build the current user turn, optionally prepending RAG context.
        user_text = prompt
        if context:
            user_text = (
                f"Relevant scheme information for reference:\n{context}\n\n"
                f"User query: {prompt}"
            )

        contents.append(
            Content(role="user", parts=[Part.from_text(user_text)])
        )
        return contents

    @staticmethod
    def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens / 1_000_000) * _COST_PER_M_INPUT_TOKENS
            + (output_tokens / 1_000_000) * _COST_PER_M_OUTPUT_TOKENS,
            8,
        )

    # -- public API ---------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def generate(
        self,
        prompt: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> LLMResult:
        """Generate a free-form advisory response.

        Parameters
        ----------
        prompt:
            The user's current question / utterance.
        context:
            Optional RAG context (scheme descriptions, etc.).
        conversation_history:
            Prior turns as ``[{"role": "user"|"model", "text": "..."}]``.
        temperature:
            Sampling temperature.  Lower is more deterministic.
        """
        start = time.perf_counter()
        model = self._get_model()

        contents = self._build_contents(prompt, context, conversation_history)

        generation_config = GenerationConfig(
            temperature=temperature,
            top_p=0.95,
            top_k=40,
            max_output_tokens=1024,
        )

        response = await model.generate_content_async(
            contents=contents,
            generation_config=generation_config,
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        answer_text = response.text if response.text else ""

        # Extract token usage from response metadata.
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count if usage else 0
        output_tokens = usage.candidates_token_count if usage else 0
        cost = self._estimate_cost(input_tokens, output_tokens)

        result = LLMResult(
            answer=answer_text,
            intent="",
            confidence=0.0,
            tokens_used={"input": input_tokens, "output": output_tokens},
            cost_usd=cost,
            processing_time_ms=round(elapsed_ms, 2),
        )

        logger.info(
            "llm_generate",
            prompt_length=len(prompt),
            answer_length=len(answer_text),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            processing_time_ms=result.processing_time_ms,
        )
        return result

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    async def classify_intent(self, text: str) -> QueryIntent:
        """Classify user text into a :class:`QueryIntent` category.

        Uses a low-temperature structured-output prompt so the model
        returns a clean JSON blob that can be parsed deterministically.
        """
        start = time.perf_counter()
        model = self._get_model()

        formatted_prompt = _INTENT_PROMPT.format(text=text)

        generation_config = GenerationConfig(
            temperature=0.1,
            top_p=0.8,
            max_output_tokens=128,
            response_mime_type="application/json",
        )

        response = await model.generate_content_async(
            contents=[Content(role="user", parts=[Part.from_text(formatted_prompt)])],
            generation_config=generation_config,
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Parse structured output.
        raw_text = (response.text or "").strip()
        try:
            parsed = json.loads(raw_text)
            intent_str = parsed.get("intent", "general_info")
        except (json.JSONDecodeError, AttributeError):
            logger.warning("intent_parse_failed", raw=raw_text)
            intent_str = "general_info"

        # Map to enum, falling back to GENERAL_INFO for unrecognised values.
        try:
            intent = QueryIntent(intent_str)
        except ValueError:
            logger.warning("intent_unknown", raw_intent=intent_str)
            intent = QueryIntent.GENERAL_INFO

        logger.info(
            "llm_classify_intent",
            text_length=len(text),
            intent=intent.value,
            processing_time_ms=round(elapsed_ms, 2),
        )
        return intent

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    async def check_eligibility(
        self,
        user_profile: dict,
        scheme: dict,
    ) -> dict:
        """Determine whether a user matches a scheme's eligibility criteria.

        Parameters
        ----------
        user_profile:
            Dictionary describing the user (age, income, caste, state, etc.).
        scheme:
            Dictionary describing the scheme's eligibility criteria.

        Returns
        -------
        dict
            ``{"eligible": bool, "matched_criteria": [...],
              "missing_info": [...], "confidence": float}``
        """
        start = time.perf_counter()
        model = self._get_model()

        formatted_prompt = _ELIGIBILITY_PROMPT.format(
            user_profile=json.dumps(user_profile, ensure_ascii=False, indent=2),
            scheme_criteria=json.dumps(scheme, ensure_ascii=False, indent=2),
        )

        generation_config = GenerationConfig(
            temperature=0.1,
            top_p=0.8,
            max_output_tokens=512,
            response_mime_type="application/json",
        )

        response = await model.generate_content_async(
            contents=[Content(role="user", parts=[Part.from_text(formatted_prompt)])],
            generation_config=generation_config,
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        raw_text = (response.text or "").strip()
        try:
            result = json.loads(raw_text)
        except (json.JSONDecodeError, AttributeError):
            logger.warning("eligibility_parse_failed", raw=raw_text)
            result = {
                "eligible": False,
                "matched_criteria": [],
                "missing_info": ["Unable to parse LLM response -- manual review needed"],
                "confidence": 0.0,
            }

        # Ensure expected keys are present.
        result.setdefault("eligible", False)
        result.setdefault("matched_criteria", [])
        result.setdefault("missing_info", [])
        result.setdefault("confidence", 0.0)

        logger.info(
            "llm_check_eligibility",
            eligible=result["eligible"],
            matched=len(result["matched_criteria"]),
            missing=len(result["missing_info"]),
            confidence=result["confidence"],
            processing_time_ms=round(elapsed_ms, 2),
        )
        return result
