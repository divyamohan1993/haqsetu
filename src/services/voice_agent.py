"""Gemini-powered conversational voice agent for HaqSetu.

UNIQUE FEATURE: Natural multi-turn voice conversations where the agent
proactively identifies applicable laws, schemes, and rights based on
the user's narrated problem -- without requiring the user to know any
legal terminology.

Architecture:
    * Uses Gemini's multi-turn chat API for natural conversation flow.
    * Internally classifies the user's situation into applicable BNS
      sections, central/state acts, government schemes, and grievance
      redressal mechanisms.
    * Maintains conversation state per session so the agent can ask
      clarifying questions and build a complete picture.
    * Generates structured case analysis while keeping the conversation
      simple and accessible.

IMPORTANT DISCLAIMER: This is NOT legal advice. Users are always
directed to DLSA (District Legal Services Authority) for actual
legal counsel.
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.services.llm import LLMService
    from src.services.translation import TranslationService

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Disclaimer (must accompany every response)
# ---------------------------------------------------------------------------

LEGAL_DISCLAIMER: Final[str] = (
    "DISCLAIMER: This information is for educational and awareness purposes only. "
    "This is NOT legal advice. For legal counsel, please contact your nearest "
    "District Legal Services Authority (DLSA) or call the Tele-Law helpline "
    "at 1516. Free legal aid is available under the Legal Services Authorities "
    "Act, 1987 for eligible citizens."
)

LEGAL_DISCLAIMER_HI: Final[str] = (
    "अस्वीकरण: यह जानकारी केवल शैक्षिक और जागरूकता उद्देश्यों के लिए है। "
    "यह कानूनी सलाह नहीं है। कानूनी परामर्श के लिए अपने निकटतम जिला "
    "विधिक सेवा प्राधिकरण (DLSA) से संपर्क करें या टेली-लॉ हेल्पलाइन "
    "1516 पर कॉल करें।"
)

# ---------------------------------------------------------------------------
# Voice Agent System Prompt
# ---------------------------------------------------------------------------

VOICE_AGENT_SYSTEM_PROMPT: Final[str] = """\
You are HaqSetu Voice Agent, a compassionate and knowledgeable assistant \
that helps Indian citizens understand their rights, applicable laws, and \
available government schemes through natural conversation.

CRITICAL RULES:
1. You are NOT a lawyer. NEVER give legal advice. Always recommend \
consulting DLSA or a lawyer for legal matters.
2. You MUST include a disclaimer that this is not legal advice in every \
response that discusses laws or legal rights.
3. Be extremely sensitive -- many users are in distress. Be empathetic, \
patient, and non-judgmental.
4. Use simple, everyday language. Avoid legal jargon.
5. When a user describes a problem, internally analyze which laws, \
BNS sections, acts, and schemes might apply -- but present this \
information in an accessible, helpful way.

CONVERSATION APPROACH:
- Start by understanding the user's situation through gentle questions.
- Ask ONE question at a time. Do not overwhelm.
- When you have enough context, provide:
  a) Which laws/rights might be relevant (in simple terms)
  b) Which government schemes could help
  c) What concrete steps they can take (CSC visit, helpline calls, etc.)
  d) Where to get free legal help (DLSA, Tele-Law 1516)
- Always end with a clear next step the user can take.

APPLICABLE LAW IDENTIFICATION:
When the user describes a situation, internally map it to:
- Bharat Nyaya Sanhita (BNS) sections (replaced IPC from 1 July 2024)
- Bharatiya Nagarik Suraksha Sanhita (BNSS) procedures
- Bharatiya Sakshya Adhiniyam (BSA) evidence rules
- Specific central/state acts (e.g., POCSO, DV Act, SC/ST Act)
- Constitutional rights (Articles 14-32 Fundamental Rights)
- Government schemes and welfare programs
- Grievance redressal mechanisms

OUTPUT FORMAT:
Return a JSON object with:
{
  "response_text": "The natural language response to speak to the user",
  "identified_laws": [
    {"law": "BNS Section X", "description": "Simple explanation", "relevance": "How it applies"},
  ],
  "applicable_schemes": [
    {"scheme": "Name", "relevance": "How it helps"},
  ],
  "recommended_actions": [
    "Step 1: ...",
    "Step 2: ...",
  ],
  "helplines": [
    {"name": "...", "number": "..."},
  ],
  "needs_more_info": true/false,
  "follow_up_question": "Optional question to ask the user",
  "severity": "low/medium/high/emergency",
  "disclaimer_required": true
}
"""

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class IdentifiedLaw(BaseModel):
    """A law or legal provision identified as potentially applicable."""

    law: str
    description: str
    relevance: str
    bns_section: str | None = None
    act_name: str | None = None


class ApplicableScheme(BaseModel):
    """A government scheme identified as potentially helpful."""

    scheme: str
    relevance: str
    helpline: str | None = None


class Helpline(BaseModel):
    """A helpline number relevant to the user's situation."""

    name: str
    number: str
    description: str | None = None


class ConversationTurn(BaseModel):
    """A single turn in the voice agent conversation."""

    role: str  # "user" or "agent"
    text: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    language: str = "hi"


class CaseAnalysis(BaseModel):
    """Structured analysis of the user's case built over conversation."""

    case_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    summary: str = ""
    identified_laws: list[IdentifiedLaw] = Field(default_factory=list)
    applicable_schemes: list[ApplicableScheme] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    helplines: list[Helpline] = Field(default_factory=list)
    severity: str = "low"
    category: str = ""  # "domestic_violence", "land_dispute", "labor", etc.
    needs_more_info: bool = True
    disclaimer_included: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VoiceAgentResponse(BaseModel):
    """Response from the voice agent."""

    response_text: str
    case_analysis: CaseAnalysis
    follow_up_question: str | None = None
    disclaimer: str = LEGAL_DISCLAIMER
    audio_response: bytes | None = None
    language: str = "hi"


class ConversationSession(BaseModel):
    """Maintains state for a multi-turn voice conversation."""

    session_id: str
    turns: list[ConversationTurn] = Field(default_factory=list)
    case_analysis: CaseAnalysis | None = None
    user_language: str = "hi"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_active: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Voice Agent Service
# ---------------------------------------------------------------------------


class VoiceAgentService:
    """Gemini-powered multi-turn voice agent for rights awareness.

    Maintains conversation sessions and builds structured case analyses
    while keeping the conversation natural and accessible.

    IMPORTANT: Every response includes a legal disclaimer. This service
    does NOT provide legal advice.
    """

    __slots__ = ("_llm", "_sessions", "_translation")

    def __init__(
        self,
        llm: LLMService,
        translation: TranslationService | None = None,
    ) -> None:
        self._llm = llm
        self._translation = translation
        # In-memory session store; production uses Firestore
        self._sessions: dict[str, ConversationSession] = {}

    async def start_session(
        self,
        session_id: str | None = None,
        language: str = "hi",
    ) -> ConversationSession:
        """Start a new voice conversation session."""
        sid = session_id or uuid4().hex
        session = ConversationSession(
            session_id=sid,
            user_language=language,
            case_analysis=CaseAnalysis(session_id=sid),
        )
        self._sessions[sid] = session
        logger.info("voice_agent.session_started", session_id=sid, language=language)
        return session

    async def process_message(
        self,
        session_id: str,
        user_message: str,
        language: str | None = None,
    ) -> VoiceAgentResponse:
        """Process a user message and return the agent's response.

        Steps:
        1. Retrieve or create session
        2. Add user turn to conversation history
        3. Translate to English if needed
        4. Send full conversation context to Gemini
        5. Parse structured response
        6. Update case analysis
        7. Translate response back to user's language
        8. Return response with disclaimer
        """
        start = time.perf_counter()

        # Step 1: Get or create session
        session = self._sessions.get(session_id)
        if session is None:
            session = await self.start_session(session_id, language or "hi")

        user_lang = language or session.user_language

        # Step 2: Record user turn
        session.turns.append(ConversationTurn(
            role="user",
            text=user_message,
            language=user_lang,
        ))
        session.last_active = datetime.now(UTC)

        # Step 3: Translate to English if needed
        english_message = user_message
        if user_lang != "en" and self._translation is not None:
            try:
                english_message = await self._translation.translate(
                    user_message, source_lang=user_lang, target_lang="en"
                )
            except Exception:
                logger.warning("voice_agent.translation_in_failed", exc_info=True)

        # Step 4: Build conversation history for Gemini
        history = []
        for turn in session.turns[:-1]:  # Exclude current turn
            role = "user" if turn.role == "user" else "model"
            history.append({"role": role, "text": turn.text})

        # Step 5: Generate response via LLM
        try:
            llm_result = await self._llm.generate(
                prompt=english_message,
                context=VOICE_AGENT_SYSTEM_PROMPT,
                conversation_history=history if history else None,
                temperature=0.4,
            )
            raw_response = llm_result.answer
        except Exception:
            logger.error("voice_agent.llm_failed", exc_info=True)
            raw_response = (
                "I apologize, I'm having trouble right now. "
                "For immediate help, please call Tele-Law helpline at 1516 "
                "or visit your nearest District Legal Services Authority."
            )

        # Step 6: Parse structured response or use raw text
        case_update = self._parse_structured_response(raw_response)
        response_text = case_update.get("response_text", raw_response)

        # Step 7: Update case analysis
        if session.case_analysis is None:
            session.case_analysis = CaseAnalysis(session_id=session_id)
        self._update_case_analysis(session.case_analysis, case_update)

        # Step 8: Ensure disclaimer is always present
        disclaimer = LEGAL_DISCLAIMER
        if user_lang == "hi":
            disclaimer = LEGAL_DISCLAIMER_HI
        elif user_lang != "en" and self._translation is not None:
            with contextlib.suppress(Exception):
                disclaimer = await self._translation.translate(
                    LEGAL_DISCLAIMER, source_lang="en", target_lang=user_lang
                )

        # Step 9: Translate response to user's language
        final_text = response_text
        if user_lang != "en" and self._translation is not None:
            try:
                final_text = await self._translation.translate(
                    response_text, source_lang="en", target_lang=user_lang
                )
            except Exception:
                logger.warning("voice_agent.translation_out_failed", exc_info=True)

        # Record agent turn
        session.turns.append(ConversationTurn(
            role="agent",
            text=final_text,
            language=user_lang,
        ))

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "voice_agent.message_processed",
            session_id=session_id,
            turns=len(session.turns),
            elapsed_ms=round(elapsed_ms, 2),
            severity=session.case_analysis.severity,
        )

        return VoiceAgentResponse(
            response_text=final_text,
            case_analysis=session.case_analysis,
            follow_up_question=case_update.get("follow_up_question"),
            disclaimer=disclaimer,
            language=user_lang,
        )

    def get_session(self, session_id: str) -> ConversationSession | None:
        """Retrieve an existing conversation session."""
        return self._sessions.get(session_id)

    def get_case_analysis(self, session_id: str) -> CaseAnalysis | None:
        """Get the current case analysis for a session."""
        session = self._sessions.get(session_id)
        if session:
            return session.case_analysis
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_structured_response(raw: str) -> dict:
        """Attempt to parse structured JSON from LLM response.

        Falls back to treating the entire response as plain text.
        """
        # Try to extract JSON from the response
        try:
            # Look for JSON block in the response
            start_idx = raw.find("{")
            end_idx = raw.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = raw[start_idx:end_idx]
                return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: return as plain response text
        return {"response_text": raw}

    @staticmethod
    def _update_case_analysis(analysis: CaseAnalysis, update: dict) -> None:
        """Update the case analysis with new information from the LLM."""
        analysis.updated_at = datetime.now(UTC)

        if "identified_laws" in update:
            for law_dict in update["identified_laws"]:
                if isinstance(law_dict, dict):
                    law = IdentifiedLaw(
                        law=law_dict.get("law", ""),
                        description=law_dict.get("description", ""),
                        relevance=law_dict.get("relevance", ""),
                        bns_section=law_dict.get("bns_section"),
                        act_name=law_dict.get("act_name"),
                    )
                    # Avoid duplicates
                    if not any(
                        existing.law == law.law
                        for existing in analysis.identified_laws
                    ):
                        analysis.identified_laws.append(law)

        if "applicable_schemes" in update:
            for scheme_dict in update["applicable_schemes"]:
                if isinstance(scheme_dict, dict):
                    scheme = ApplicableScheme(
                        scheme=scheme_dict.get("scheme", ""),
                        relevance=scheme_dict.get("relevance", ""),
                        helpline=scheme_dict.get("helpline"),
                    )
                    if not any(
                        existing.scheme == scheme.scheme
                        for existing in analysis.applicable_schemes
                    ):
                        analysis.applicable_schemes.append(scheme)

        if "recommended_actions" in update:
            for action in update["recommended_actions"]:
                if isinstance(action, str) and action not in analysis.recommended_actions:
                    analysis.recommended_actions.append(action)

        if "helplines" in update:
            for helpline_dict in update["helplines"]:
                if isinstance(helpline_dict, dict):
                    helpline = Helpline(
                        name=helpline_dict.get("name", ""),
                        number=helpline_dict.get("number", ""),
                        description=helpline_dict.get("description"),
                    )
                    if not any(
                        existing.number == helpline.number
                        for existing in analysis.helplines
                    ):
                        analysis.helplines.append(helpline)

        if "severity" in update:
            severity = update["severity"]
            if severity in ("low", "medium", "high", "emergency"):
                analysis.severity = severity

        if "needs_more_info" in update:
            analysis.needs_more_info = bool(update["needs_more_info"])

        analysis.disclaimer_included = True
