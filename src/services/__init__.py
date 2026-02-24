"""HaqSetu service layer -- cache, speech, LLM, RAG, and supporting integrations.

Exports are defined eagerly for services with no external dependencies
(cache, hinglish, rag, scheme_search) and lazily for GCP-dependent
services (llm, speech, translation) so that ``import src.services``
succeeds even when GCP client libraries are not yet installed or
have runtime issues (e.g. missing native extensions).
"""

from __future__ import annotations

from src.services.cache import CacheManager, InMemoryCacheBackend, RedisCacheBackend
from src.services.hinglish import HinglishProcessor
from src.services.rag import RAGService, SearchResult
from src.services.scheme_search import SchemeSearchService

# GCP-dependent services: import lazily so that ``import src.services``
# does not fail in environments where google-cloud-* or vertexai
# packages are not installed or have broken native extensions.
# We catch BaseException because some GCP native extension failures
# raise pyo3_runtime.PanicException which inherits from BaseException.
try:
    from src.services.llm import HAQSETU_SYSTEM_PROMPT, LLMResult, LLMService
except BaseException:  # pragma: no cover  # noqa: BLE001
    HAQSETU_SYSTEM_PROMPT = None  # type: ignore[assignment]
    LLMResult = None  # type: ignore[assignment,misc]
    LLMService = None  # type: ignore[assignment,misc]

try:
    from src.services.speech import (
        VOICE_MAP,
        ASRResult,
        SpeechToTextService,
        TextToSpeechService,
    )
except BaseException:  # pragma: no cover  # noqa: BLE001
    VOICE_MAP = None  # type: ignore[assignment]
    ASRResult = None  # type: ignore[assignment,misc]
    SpeechToTextService = None  # type: ignore[assignment,misc]
    TextToSpeechService = None  # type: ignore[assignment,misc]

try:
    from src.services.translation import TranslationService
except BaseException:  # pragma: no cover  # noqa: BLE001
    TranslationService = None  # type: ignore[assignment,misc]

__all__ = [
    "ASRResult",
    "CacheManager",
    "HAQSETU_SYSTEM_PROMPT",
    "HinglishProcessor",
    "InMemoryCacheBackend",
    "LLMResult",
    "LLMService",
    "RAGService",
    "RedisCacheBackend",
    "SchemeSearchService",
    "SearchResult",
    "SpeechToTextService",
    "TextToSpeechService",
    "TranslationService",
    "VOICE_MAP",
]
