"""Main query orchestrator for HaqSetu.

Coordinates the full pipeline: language detection, translation, intent
classification, scheme search (RAG), LLM response generation, and
output translation.  Each step records its latency so the caller can
profile the end-to-end request.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Final

import structlog

from src.models.enums import ContentType, LanguageCode, QueryIntent
from src.models.response import (
    HaqSetuResponse,
    LatencyBreakdown,
    ResponseMetadata,
    SchemeReference,
)

if TYPE_CHECKING:
    from src.models.request import HaqSetuRequest
    from src.models.scheme import SchemeDocument
    from src.services.cache import CacheManager
    from src.services.hinglish import HinglishProcessor
    from src.services.llm import LLMService
    from src.services.rag import RAGService, SearchResult
    from src.services.scheme_search import SchemeSearchService
    from src.services.speech import SpeechToTextService, TextToSpeechService
    from src.services.translation import TranslationService

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Greetings in all 22 Scheduled Languages + English
# ---------------------------------------------------------------------------

GREETINGS: Final[dict[str, str]] = {
    "hi": "नमस्ते! मैं हक़सेतु हूँ, आपकी सरकारी योजनाओं का साथी। मैं आपकी कैसे मदद कर सकता हूँ?",
    "bn": "নমস্কার! আমি হকসেতু, সরকারি প্রকল্পের সহায়ক। আমি কীভাবে সাহায্য করতে পারি?",
    "te": "నమస్కారం! నేను హక్‌సేతు, ప్రభుత్వ పథకాల సహాయకుడిని. నేను మీకు ఎలా సహాయం చేయగలను?",
    "mr": "नमस्कार! मी हक़सेतु, सरकारी योजनांचा सहाय्यक. मी तुम्हाला कशी मदत करू शकतो?",
    "ta": "வணக்கம்! நான் ஹக்சேது, அரசுத் திட்டங்களுக்கான உதவியாளர். நான் உங்களுக்கு எப்படி உதவ முடியும்?",
    "ur": "السلام علیکم! میں حق‌سیتو ہوں، سرکاری اسکیموں کا معاون۔ میں آپ کی کیسے مدد کر سکتا ہوں؟",
    "gu": "નમસ્તે! હું હકસેતુ છું, સરકારી યોજનાઓનો સહાયક. હું તમને કેવી રીતે મદદ કરી શકું?",
    "kn": "ನಮಸ್ಕಾರ! ನಾನು ಹಕ್‌ಸೇತು, ಸರ್ಕಾರಿ ಯೋಜನೆಗಳ ಸಹಾಯಕ. ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು?",
    "or": "ନମସ୍କାର! ମୁଁ ହକସେତୁ, ସରକାରୀ ଯୋଜନାର ସହାୟକ। ମୁଁ ଆପଣଙ୍କୁ କିପରି ସାହାଯ୍ୟ କରିପାରିବି?",
    "ml": "നമസ്കാരം! ഞാൻ ഹഖ്സേതു, സർക്കാർ പദ്ധതികളുടെ സഹായി. എനിക്ക് നിങ്ങളെ എങ്ങനെ സഹായിക്കാനാകും?",
    "pa": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਹਕਸੇਤੂ ਹਾਂ, ਸਰਕਾਰੀ ਯੋਜਨਾਵਾਂ ਦਾ ਸਹਾਇਕ। ਮੈਂ ਤੁਹਾਡੀ ਕਿਵੇਂ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ?",
    "as": "নমস্কাৰ! মই হকসেতু, চৰকাৰী আঁচনিৰ সহায়ক। মই আপোনাক কেনেকৈ সহায় কৰিব পাৰোঁ?",
    "mai": "प्रणाम! हम हक़सेतु छी, सरकारी योजनाक सहायक। हम अहाँक केना मदद कऽ सकैत छी?",
    "sat": "ᱡᱚᱦᱟᱨ! ᱤᱧ ᱦᱚᱠᱥᱮᱛᱩ ᱠᱟᱱᱟ, ᱥᱚᱨᱠᱟᱨᱤ ᱡᱚᱡᱚᱱᱟ ᱨᱮᱱᱟᱜ ᱜᱚᱲᱚ। ᱤᱧ ᱟᱢᱮᱫ ᱚᱠᱛᱚ ᱡᱚᱛᱚᱱ ᱫᱟᱲᱮᱭᱟᱜ ᱠᱟᱱᱟ?",
    "ks": "آداب! بٕہ ہَکسیتُو چُھس، سَرکٲری سکیمَن ہُنٛد مَدَدگار۔ بٕہ تۄہہِ کَتھ مَدَد کَرَے ہیکِو؟",
    "ne": "नमस्ते! म हकसेतु हुँ, सरकारी योजनाहरूको सहायक। म तपाईंलाई कसरी मद्दत गर्न सक्छु?",
    "sd": "سلام! مان حقسيتو آهيان، سرڪاري اسڪيمن جو مددگار. مان توهان جي ڪيئن مدد ڪري سگهان ٿو؟",
    "kok": "नमस्कार! हांव हक़सेतु, सरकारी येवजणांचो सहाय्यक. हांव तुमकां कशें मदत करूं येता?",
    "doi": "नमस्कार! मैं हक़सेतु हां, सरकारी योजनाएं दा सहायक। मैं तुहाडी किस तरह मदद करी सकना हां?",
    "mni": "খুরুমজরি! ঐহাক্ হকসেতু, সরকারগী স্কিমশীংগী মতেংনচিংবনি। ঐহাক্ নখোয়দা কমদৌনা মতেং পাংবা ঙমগনি?",
    "brx": "नमस्कार! आं हक़सेतु, सरकारनि जथुम्मानि गोहो। आं नोंथांनो माबोरै मदद होनांगौ?",
    "sa": "नमस्ते! अहं हक़सेतुः अस्मि, शासकीय-योजनानां सहायकः। अहं भवन्तं कथं साहाय्यं कर्तुं शक्नोमि?",
    "en": "Hello! I am HaqSetu, your government schemes assistant. How can I help you today?",
}


# ---------------------------------------------------------------------------
# QueryOrchestrator
# ---------------------------------------------------------------------------


class QueryOrchestrator:
    """Central query processing pipeline for HaqSetu.

    Coordinates language detection, translation, intent classification,
    scheme retrieval (RAG), LLM-based response generation, and output
    translation.  Tracks per-step latency for observability.
    """

    __slots__ = (
        "_cache",
        "_hinglish",
        "_llm",
        "_scheme_search",
        "_speech_to_text",
        "_text_to_speech",
        "_translation",
    )

    def __init__(
        self,
        translation: TranslationService,
        llm: LLMService,
        scheme_search: SchemeSearchService,
        hinglish: HinglishProcessor,
        cache: CacheManager,
        speech_to_text: SpeechToTextService | None = None,
        text_to_speech: TextToSpeechService | None = None,
    ) -> None:
        self._translation = translation
        self._llm = llm
        self._scheme_search = scheme_search
        self._hinglish = hinglish
        self._cache = cache
        self._speech_to_text = speech_to_text
        self._text_to_speech = text_to_speech

    # ------------------------------------------------------------------
    # Text query pipeline
    # ------------------------------------------------------------------

    async def process_text_query(self, request: HaqSetuRequest) -> HaqSetuResponse:
        """Execute the full text-query pipeline and return a response.

        Steps:
        1. Start timing
        2. Detect language (if not provided)
        3. Check if Hinglish -- if so, extract keywords and intent
        4. Translate to English if not English (cache result)
        5. Classify intent via LLM
        6. Search relevant schemes via RAG
        7. Build context from top schemes
        8. Generate response via LLM with scheme context
        9. Translate response back to user's language
        10. Build HaqSetuResponse with full latency breakdown
        """
        pipeline_start = time.perf_counter()
        latency = LatencyBreakdown()
        content = request.content if isinstance(request.content, str) else request.content.decode("utf-8")

        log = logger.bind(request_id=request.request_id, session_id=request.session_id)
        log.info("pipeline.text_query_start", content_length=len(content))

        # -- Step 1-2: Detect language -----------------------------------------
        step_start = time.perf_counter()
        if request.language is not None:
            user_lang = request.language.value
            lang_confidence = 1.0
        else:
            user_lang, lang_confidence = await self._translation.detect_language(content)
            # Normalise GCP language codes to our internal codes
            if user_lang == "und":
                user_lang = "hi"  # default to Hindi for rural India users
                lang_confidence = 0.5
        latency.language_detection_ms = _elapsed_ms(step_start)
        log.info("pipeline.language_detected", language=user_lang, confidence=lang_confidence)

        # -- Step 3: Check for Hinglish ----------------------------------------
        is_hinglish = False
        hinglish_keywords: list[str] = []
        try:
            is_hinglish = self._hinglish.is_hinglish(content)
            if is_hinglish:
                hinglish_keywords = self._hinglish.extract_intent_keywords(content)
                log.info("pipeline.hinglish_detected", keywords=hinglish_keywords)
        except Exception:
            log.warning("pipeline.hinglish_check_failed", exc_info=True)

        # -- Step 4: Translate to English if needed ----------------------------
        step_start = time.perf_counter()
        if user_lang == "en":
            english_query = content
        else:
            try:
                english_query = await self._translation.translate(
                    content, source_lang=user_lang, target_lang="en"
                )
            except Exception:
                log.warning("pipeline.translation_in_failed", exc_info=True)
                english_query = content  # fallback: use original text
        latency.translation_in_ms = _elapsed_ms(step_start)

        # -- Step 5: Classify intent -------------------------------------------
        step_start = time.perf_counter()
        intent_confidence = 0.8  # default confidence for LLM-classified intents
        try:
            intent = await self._llm.classify_intent(english_query)
        except Exception:
            log.warning("pipeline.intent_classification_failed", exc_info=True)
            intent = QueryIntent.GENERAL_INFO
            intent_confidence = 0.3
        log.info("pipeline.intent_classified", intent=intent.value, confidence=intent_confidence)

        # Handle greetings early
        if intent == QueryIntent.GREETING:
            greeting = await self._handle_greeting(user_lang)
            latency.llm_reasoning_ms = _elapsed_ms(step_start)
            return self._build_response(
                request=request,
                content=greeting,
                language=user_lang,
                latency=latency,
                confidence=intent_confidence,
                schemes=[],
            )
        latency_intent_ms = _elapsed_ms(step_start)

        # -- Step 6: Search relevant schemes via RAG ---------------------------
        step_start = time.perf_counter()
        search_query = english_query
        if is_hinglish and hinglish_keywords:
            search_query = f"{english_query} {' '.join(hinglish_keywords)}"

        filters: dict | None = None
        if request.metadata and request.metadata.approximate_state:
            filters = {"state": request.metadata.approximate_state}

        try:
            schemes = await self._scheme_search.search(
                query=search_query, top_k=5, filters=filters
            )
        except Exception:
            log.warning("pipeline.scheme_search_failed", exc_info=True)
            schemes = []
        latency.rag_retrieval_ms = _elapsed_ms(step_start)
        log.info("pipeline.schemes_found", count=len(schemes))

        # -- Step 7-8: Build context and generate response ---------------------
        step_start = time.perf_counter()
        scheme_context = await self._build_scheme_context(schemes, english_query)

        try:
            llm_result = await self._llm.generate(
                prompt=english_query,
                context=scheme_context,
            )
            english_response = llm_result.answer
        except Exception:
            log.error("pipeline.llm_generation_failed", exc_info=True)
            english_response = (
                "I apologize, but I'm having trouble processing your request "
                "right now. Please try again shortly."
            )
        latency.llm_reasoning_ms = latency_intent_ms + _elapsed_ms(step_start)

        # -- Step 9: Translate response back to user's language ----------------
        step_start = time.perf_counter()
        if user_lang == "en":
            final_response = english_response
        else:
            try:
                final_response = await self._translation.translate(
                    english_response, source_lang="en", target_lang=user_lang
                )
            except Exception:
                log.warning("pipeline.translation_out_failed", exc_info=True)
                final_response = english_response  # fallback: return English
        latency.translation_out_ms = _elapsed_ms(step_start)

        # -- Step 10: Build final response -------------------------------------
        scheme_refs = self._extract_scheme_references(schemes)

        total_ms = _elapsed_ms(pipeline_start)
        log.info(
            "pipeline.text_query_complete",
            total_ms=round(total_ms, 2),
            intent=intent.value,
            language=user_lang,
            schemes_count=len(scheme_refs),
        )

        return self._build_response(
            request=request,
            content=final_response,
            language=user_lang,
            latency=latency,
            confidence=intent_confidence,
            schemes=scheme_refs,
        )

    # ------------------------------------------------------------------
    # Voice query pipeline
    # ------------------------------------------------------------------

    async def process_voice_query(self, request: HaqSetuRequest) -> HaqSetuResponse:
        """Execute the full voice-query pipeline.

        Steps:
        1. Transcribe audio via Speech-to-Text
        2. Run the text query pipeline on the transcript
        3. Optionally generate audio response via TTS
        """
        latency = LatencyBreakdown()
        log = logger.bind(request_id=request.request_id, session_id=request.session_id)

        if self._speech_to_text is None:
            log.error("pipeline.stt_not_configured")
            raise RuntimeError("Speech-to-Text service is not configured")

        audio_data = request.content if isinstance(request.content, bytes) else request.content.encode("utf-8")

        # -- Step 1: Transcribe audio ------------------------------------------
        step_start = time.perf_counter()
        lang_hint = request.language.value if request.language else "hi"

        try:
            asr_result = await self._speech_to_text.transcribe(
                audio_data=audio_data,
                language_code=lang_hint,
            )
        except Exception:
            log.error("pipeline.stt_failed", exc_info=True)
            raise
        latency.asr_ms = _elapsed_ms(step_start)
        log.info(
            "pipeline.stt_complete",
            text=asr_result.text[:100],
            confidence=asr_result.confidence,
            asr_ms=latency.asr_ms,
        )

        if not asr_result.text:
            return self._build_response(
                request=request,
                content=await self._handle_greeting(lang_hint),
                language=lang_hint,
                latency=latency,
                confidence=0.0,
                schemes=[],
            )

        # -- Step 2: Create a text request from the transcript and run pipeline
        from src.models.request import HaqSetuRequest as HaqSetuRequestModel

        text_request = HaqSetuRequestModel(
            request_id=request.request_id,
            session_id=request.session_id,
            channel_type=request.channel_type,
            content=asr_result.text,
            content_type=ContentType.TEXT,
            language=request.language,
            metadata=request.metadata,
        )

        text_response = await self.process_text_query(text_request)

        # Merge ASR latency into the response
        text_response.metadata.latency.asr_ms = latency.asr_ms

        # -- Step 3: Optionally generate audio response via TTS ----------------
        if self._text_to_speech is not None:
            step_start = time.perf_counter()
            try:
                audio_bytes = await self._text_to_speech.synthesize(
                    text=text_response.content,
                    language_code=text_response.language.value,
                )
                # Encode audio as base64 and append to content
                audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                text_response.metadata.latency.tts_ms = _elapsed_ms(step_start)
                # Store audio in a way the API layer can extract it
                text_response = HaqSetuResponse(
                    request_id=text_response.request_id,
                    session_id=text_response.session_id,
                    content=text_response.content,
                    content_type=ContentType.AUDIO,
                    language=text_response.language,
                    metadata=ResponseMetadata(
                        confidence=text_response.metadata.confidence,
                        latency=text_response.metadata.latency,
                        schemes_referenced=text_response.metadata.schemes_referenced,
                        requires_followup=text_response.metadata.requires_followup,
                        suggested_actions=text_response.metadata.suggested_actions,
                    ),
                )
                # Stash audio_b64 as an extra attribute for the API layer
                object.__setattr__(text_response, "_audio_b64", audio_b64)
                log.info("pipeline.tts_complete", audio_bytes=len(audio_bytes))
            except Exception:
                log.warning("pipeline.tts_failed", exc_info=True)
                text_response.metadata.latency.tts_ms = _elapsed_ms(step_start)

        return text_response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _build_scheme_context(self, schemes: list, query: str) -> str:
        """Build a concise context string from scheme search results for the LLM.

        Includes scheme name, benefits, eligibility, and application process.
        Optimised for token efficiency.
        """
        if not schemes:
            return "No relevant government schemes found for this query."

        parts: list[str] = [
            f"The user asked: \"{query}\"\n",
            "Here are the relevant government schemes:\n",
        ]

        for idx, scheme in enumerate(schemes[:5], start=1):
            # Handle both SchemeDocument objects and dict-like results
            if hasattr(scheme, "name"):
                name = scheme.name
                benefits = scheme.benefits
                description = scheme.description
                eligibility = ""
                if hasattr(scheme, "eligibility") and scheme.eligibility:
                    elig = scheme.eligibility
                    elig_parts: list[str] = []
                    if hasattr(elig, "income_limit") and elig.income_limit:
                        elig_parts.append(f"Income limit: Rs.{elig.income_limit:,.0f}")
                    if hasattr(elig, "category") and elig.category:
                        elig_parts.append(f"Category: {elig.category}")
                    if hasattr(elig, "min_age") and elig.min_age:
                        elig_parts.append(f"Min age: {elig.min_age}")
                    if hasattr(elig, "max_age") and elig.max_age:
                        elig_parts.append(f"Max age: {elig.max_age}")
                    if hasattr(elig, "custom_criteria") and elig.custom_criteria:
                        elig_parts.extend(elig.custom_criteria)
                    eligibility = "; ".join(elig_parts)
                application = getattr(scheme, "application_process", "")
                documents = getattr(scheme, "documents_required", [])
                helpline = getattr(scheme, "helpline", "")
            elif isinstance(scheme, dict):
                name = scheme.get("name", "Unknown Scheme")
                benefits = scheme.get("benefits", "")
                description = scheme.get("description", "")
                eligibility = scheme.get("eligibility", "")
                if isinstance(eligibility, dict):
                    eligibility = "; ".join(f"{k}: {v}" for k, v in eligibility.items() if v)
                application = scheme.get("application_process", "")
                documents = scheme.get("documents_required", [])
                helpline = scheme.get("helpline", "")
            else:
                continue

            block = f"Scheme {idx}: {name}\n"
            if description:
                block += f"  Description: {description[:200]}\n"
            if benefits:
                block += f"  Benefits: {benefits[:200]}\n"
            if eligibility:
                block += f"  Eligibility: {eligibility[:200]}\n"
            if application:
                block += f"  How to apply: {application[:200]}\n"
            if documents:
                docs_str = ", ".join(documents[:5])
                block += f"  Documents needed: {docs_str}\n"
            if helpline:
                block += f"  Helpline: {helpline}\n"
            parts.append(block)

        parts.append(
            "\nInstructions: Based on the above schemes, provide a helpful, "
            "clear, and concise answer to the user's question. Mention specific "
            "scheme names, eligibility criteria, benefits, and how to apply. "
            "Be empathetic and use simple language suitable for rural users."
        )

        return "\n".join(parts)

    async def _handle_greeting(self, language: str) -> str:
        """Return a warm greeting in the user's language.

        Falls back to Hindi if the language is not found in pre-cached
        greetings, then to the English greeting.
        """
        return GREETINGS.get(language, GREETINGS.get("hi", GREETINGS["en"]))

    @staticmethod
    def _extract_scheme_references(schemes: list) -> list[SchemeReference]:
        """Convert raw scheme search results to SchemeReference objects."""
        refs: list[SchemeReference] = []
        for scheme in schemes[:5]:
            try:
                if hasattr(scheme, "scheme_id"):
                    refs.append(
                        SchemeReference(
                            scheme_id=scheme.scheme_id,
                            scheme_name=scheme.name,
                            relevance_score=getattr(scheme, "relevance_score", 0.0)
                            or getattr(scheme, "popularity_score", 0.0),
                            matched_criteria=getattr(scheme, "matched_criteria", []),
                        )
                    )
                elif isinstance(scheme, dict):
                    refs.append(
                        SchemeReference(
                            scheme_id=scheme.get("scheme_id", ""),
                            scheme_name=scheme.get("name", "Unknown"),
                            relevance_score=scheme.get("relevance_score", 0.0),
                            matched_criteria=scheme.get("matched_criteria", []),
                        )
                    )
            except Exception:
                logger.warning("pipeline.scheme_ref_extraction_failed", exc_info=True)
        return refs

    @staticmethod
    def _build_response(
        request: HaqSetuRequest,
        content: str,
        language: str,
        latency: LatencyBreakdown,
        confidence: float,
        schemes: list[SchemeReference],
    ) -> HaqSetuResponse:
        """Construct a complete HaqSetuResponse."""
        # Resolve LanguageCode enum value
        try:
            lang_enum = LanguageCode(language)
        except ValueError:
            lang_enum = LanguageCode.hi  # default fallback

        return HaqSetuResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            content=content,
            content_type=ContentType.TEXT,
            language=lang_enum,
            metadata=ResponseMetadata(
                confidence=confidence,
                latency=latency,
                schemes_referenced=schemes,
            ),
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _elapsed_ms(start: float) -> float:
    """Return milliseconds elapsed since *start* (a ``perf_counter`` value)."""
    return round((time.perf_counter() - start) * 1000, 2)
