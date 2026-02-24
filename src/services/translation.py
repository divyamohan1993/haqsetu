"""Google Cloud Translation v3 service with caching.

Wraps the GCP ``TranslationServiceAsyncClient`` and adds a transparent
cache layer so repeated translations are served from cache (Redis or
in-memory) rather than incurring an API call.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import structlog
from google.cloud.translate_v3 import (
    DetectLanguageRequest,
    TranslateTextRequest,
    TranslationServiceAsyncClient,
)

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# TTL constants
# ---------------------------------------------------------------------------

_TRANSLATION_TTL: int = 30 * 24 * 60 * 60  # 30 days in seconds
_DETECTION_TTL: int = 7 * 24 * 60 * 60     # 7 days in seconds

# ---------------------------------------------------------------------------
# Language code mapping  --  internal ISO codes -> GCP Translation codes
# ---------------------------------------------------------------------------

# Most ISO 639-1 / 639-3 codes are identical to what GCP accepts.  This map
# captures only the codes that require special handling or that we want to
# be explicit about.  Codes not listed here are passed through unchanged.
LANG_TO_GCP_CODE: dict[str, str] = {
    # Standard ISO 639-1 codes accepted as-is by GCP
    "hi": "hi",
    "bn": "bn",
    "te": "te",
    "mr": "mr",
    "ta": "ta",
    "ur": "ur",
    "gu": "gu",
    "kn": "kn",
    "or": "or",       # Odia
    "ml": "ml",
    "pa": "pa",
    "as": "as",       # Assamese
    "ne": "ne",
    "sd": "sd",
    "sa": "sa",       # Sanskrit
    "ks": "ks",       # Kashmiri
    "en": "en",
    # ISO 639-3 / extended codes -- some may have limited GCP support
    "mai": "mai",     # Maithili
    "kok": "kok",     # Konkani
    "doi": "doi",     # Dogri
    "mni": "mni-Mtei",  # Manipuri -- GCP uses script-qualified BCP-47
    "brx": "brx",     # Bodo -- limited support; may need Bhashini fallback
    "sat": "sat-Olck",  # Santali -- GCP uses script-qualified BCP-47
}


def _gcp_code(lang: str) -> str:
    """Map an internal language code to its GCP Translation equivalent."""
    return LANG_TO_GCP_CODE.get(lang, lang)


def _text_hash(text: str) -> str:
    """Deterministic, compact hash for use in cache keys."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# TranslationService
# ---------------------------------------------------------------------------


class TranslationService:
    """Async Google Cloud Translation v3 wrapper with transparent caching.

    Parameters
    ----------
    project_id:
        GCP project identifier.
    region:
        GCP region for the Translation API (e.g. ``"us-central1"``).
    cache:
        A :class:`CacheManager` instance (typically namespaced with
        ``"translation:"``).
    """

    __slots__ = ("_cache", "_client", "_parent")

    def __init__(
        self,
        project_id: str,
        region: str,
        cache: CacheManager,
    ) -> None:
        self._cache = cache
        self._client = TranslationServiceAsyncClient()
        # The "parent" resource name expected by every v3 RPC call.
        self._parent = f"projects/{project_id}/locations/{region}"

    # ------------------------------------------------------------------
    # Single-text translation
    # ------------------------------------------------------------------

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        """Translate *text* from *source_lang* to *target_lang*.

        Results are cached for 30 days so identical requests do not hit
        the GCP API again.
        """
        if source_lang == target_lang:
            return text

        cache_key = f"tr:{source_lang}:{target_lang}:{_text_hash(text)}"

        cached: str | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug("translation.cache_hit", key=cache_key)
            return cached

        src_gcp = _gcp_code(source_lang)
        tgt_gcp = _gcp_code(target_lang)

        request = TranslateTextRequest(
            parent=self._parent,
            contents=[text],
            source_language_code=src_gcp,
            target_language_code=tgt_gcp,
            mime_type="text/plain",
        )

        response = await self._client.translate_text(request=request)
        translated = response.translations[0].translated_text

        await self._cache.set(cache_key, translated, ttl_seconds=_TRANSLATION_TTL)

        logger.info(
            "translation.completed",
            source_lang=source_lang,
            target_lang=target_lang,
            chars=len(text),
        )
        return translated

    # ------------------------------------------------------------------
    # Batch translation
    # ------------------------------------------------------------------

    async def translate_batch(
        self,
        texts: list[str],
        source_lang: str,
        target_lang: str,
    ) -> list[str]:
        """Translate a list of texts, using the cache for already-translated items.

        Only texts that are *not* already cached are sent to GCP in a
        single batch call, keeping API usage to a minimum.
        """
        if source_lang == target_lang:
            return list(texts)

        results: list[str | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # 1. Check cache for each text
        for idx, text in enumerate(texts):
            cache_key = f"tr:{source_lang}:{target_lang}:{_text_hash(text)}"
            cached: str | None = await self._cache.get(cache_key)
            if cached is not None:
                results[idx] = cached
            else:
                uncached_indices.append(idx)
                uncached_texts.append(text)

        # 2. Batch-translate uncached texts (if any)
        if uncached_texts:
            src_gcp = _gcp_code(source_lang)
            tgt_gcp = _gcp_code(target_lang)

            request = TranslateTextRequest(
                parent=self._parent,
                contents=uncached_texts,
                source_language_code=src_gcp,
                target_language_code=tgt_gcp,
                mime_type="text/plain",
            )

            response = await self._client.translate_text(request=request)

            for pos, translation in enumerate(response.translations):
                original_idx = uncached_indices[pos]
                translated = translation.translated_text
                results[original_idx] = translated

                # Populate cache for next time
                cache_key = f"tr:{source_lang}:{target_lang}:{_text_hash(uncached_texts[pos])}"
                await self._cache.set(cache_key, translated, ttl_seconds=_TRANSLATION_TTL)

            logger.info(
                "translation.batch_completed",
                total=len(texts),
                from_cache=len(texts) - len(uncached_texts),
                translated=len(uncached_texts),
            )

        # All slots should be populated; type-narrow for mypy.
        return [r if r is not None else "" for r in results]

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    async def detect_language(self, text: str) -> tuple[str, float]:
        """Detect the language of *text*.

        Returns
        -------
        tuple[str, float]
            ``(language_code, confidence)`` where *language_code* is an
            ISO 639-1 code and *confidence* is in ``[0.0, 1.0]``.
        """
        cache_key = f"detect:{_text_hash(text)}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return (cached[0], cached[1])

        request = DetectLanguageRequest(
            parent=self._parent,
            content=text,
            mime_type="text/plain",
        )

        response = await self._client.detect_language(request=request)

        if not response.languages:
            logger.warning("translation.detect_no_result", text_len=len(text))
            return ("und", 0.0)

        top = response.languages[0]
        lang_code: str = top.language_code
        confidence: float = top.confidence

        await self._cache.set(cache_key, [lang_code, confidence], ttl_seconds=_DETECTION_TTL)

        logger.info(
            "translation.detect_completed",
            lang=lang_code,
            confidence=round(confidence, 3),
        )
        return (lang_code, confidence)
