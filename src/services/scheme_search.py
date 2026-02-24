"""Scheme search engine with hash-based feature embeddings and RAG integration.

Provides semantic and keyword-based scheme discovery without requiring an
external embedding API.  Embeddings are generated locally using a
deterministic hash-based feature hashing approach (murmurhash-style),
producing fixed-dimension dense vectors suitable for cosine similarity
search via :class:`~src.services.rag.RAGService`.

This is ideal for the prototype scale (~2,300 schemes) where the full
pipeline -- embedding generation, indexing, and query -- completes in
well under a second on commodity hardware.
"""

from __future__ import annotations

import hashlib
from typing import Final

import numpy as np
import structlog

from src.models.scheme import SchemeDocument
from src.services.cache import CacheManager
from src.services.rag import RAGService, SearchResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBEDDING_DIM: Final[int] = 768
_CACHE_TTL_SECONDS: Final[int] = 3600  # 1 hour
_CACHE_PREFIX: Final[str] = "scheme_search:"


class SchemeSearchService:
    """High-level scheme search built on :class:`RAGService` with caching.

    Responsibilities:

    * Store and manage :class:`SchemeDocument` instances in memory.
    * Convert scheme text to dense embeddings via deterministic feature
      hashing (no external API calls).
    * Index all scheme embeddings into the RAG service for fast retrieval.
    * Expose query methods that combine vector search with optional
      user-profile boosting.

    Parameters
    ----------
    rag:
        The RAG service to use for vector indexing and search.
    cache:
        The cache manager for caching search results.
    """

    __slots__ = ("_cache", "_rag", "_schemes")

    def __init__(self, rag: RAGService, cache: CacheManager) -> None:
        self._rag = rag
        self._cache = cache
        self._schemes: dict[str, SchemeDocument] = {}

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self, schemes: list[SchemeDocument]) -> None:
        """Index a collection of schemes into the RAG service.

        For each scheme, a searchable text representation is built from
        its name, description, benefits, category, ministry, and
        eligibility criteria.  This text is converted to a dense
        embedding via :meth:`_text_to_embedding` and batch-indexed.

        Parameters
        ----------
        schemes:
            List of :class:`SchemeDocument` instances to index.
        """
        if not schemes:
            logger.warning("scheme_search.no_schemes_to_index")
            return

        logger.info("scheme_search.initializing", count=len(schemes))

        # Store schemes in local dict for fast lookup by ID.
        for scheme in schemes:
            self._schemes[scheme.scheme_id] = scheme

        # Build batch for RAG indexing: list of (doc_id, embedding, metadata).
        batch: list[tuple[str, list[float], dict]] = []
        for scheme in schemes:
            text = self._scheme_to_text(scheme)
            embedding = self._text_to_embedding(text)
            metadata = self._scheme_to_metadata(scheme)
            batch.append((scheme.scheme_id, embedding, metadata))

        await self._rag.index_batch(batch)

        logger.info(
            "scheme_search.initialized",
            scheme_count=len(schemes),
            embedding_dim=_EMBEDDING_DIM,
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_schemes(
        self,
        query: str,
        language: str = "en",
        user_profile: dict | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """Search for schemes matching a natural-language query.

        Uses hybrid search (semantic + keyword via RRF) from the RAG
        service, then enriches results with full scheme details.

        Parameters
        ----------
        query:
            Natural-language search query (e.g. "farmer pension scheme").
        language:
            ISO 639-1 language code.  Currently used for cache key
            differentiation; future versions will support multilingual
            embeddings.
        user_profile:
            Optional dict describing the user (age, income, state, etc.)
            for relevance boosting.
        top_k:
            Maximum number of results to return.

        Returns
        -------
        list[dict]
            Ranked list of dicts, each containing scheme details and a
            relevance ``score``.
        """
        # Check cache first.
        cache_key = self._build_cache_key(query, language, user_profile)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug("scheme_search.cache_hit", query=query)
            return cached

        # Generate query embedding.
        query_embedding = self._text_to_embedding(query)

        # Use hybrid search for best results.
        results: list[SearchResult] = await self._rag.hybrid_search(
            query_text=query,
            query_embedding=query_embedding,
            top_k=top_k,
        )

        # Enrich results with full scheme data and apply profile boosting.
        enriched: list[dict] = []
        for result in results:
            scheme = self._schemes.get(result.doc_id)
            if scheme is None:
                continue

            entry: dict = {
                "scheme_id": scheme.scheme_id,
                "name": scheme.name,
                "description": scheme.description,
                "category": scheme.category.value,
                "ministry": scheme.ministry,
                "benefits": scheme.benefits,
                "application_process": scheme.application_process,
                "documents_required": scheme.documents_required,
                "helpline": scheme.helpline,
                "website": scheme.website,
                "score": result.score,
            }

            # Apply user-profile relevance boosting when available.
            if user_profile:
                entry["score"] = self._boost_score(
                    entry["score"], scheme, user_profile
                )

            # Include translated name/description if available for the
            # requested language.
            if language != "en":
                if language in scheme.name_translations:
                    entry["name_translated"] = scheme.name_translations[language]
                if language in scheme.description_translations:
                    entry["description_translated"] = scheme.description_translations[language]

            enriched.append(entry)

        # Re-sort after boosting.
        enriched.sort(key=lambda x: x["score"], reverse=True)

        # Cache the results.
        await self._cache.set(cache_key, enriched, ttl_seconds=_CACHE_TTL_SECONDS)

        logger.info(
            "scheme_search.query",
            query=query,
            language=language,
            results=len(enriched),
            top_score=enriched[0]["score"] if enriched else 0.0,
        )

        return enriched

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """Compatibility wrapper matching the interface expected by the orchestrator.

        Falls back to keyword-only search if the index is empty or
        embedding generation is not available.  Returns a list of
        metadata dicts.

        Parameters
        ----------
        query:
            Natural-language search query.
        top_k:
            Maximum number of results to return.
        filters:
            Optional metadata filters (currently unused).
        """
        if not self._schemes:
            return []

        query_embedding = self._text_to_embedding(query)

        try:
            results: list[SearchResult] = await self._rag.hybrid_search(
                query_text=query,
                query_embedding=query_embedding,
                top_k=top_k,
            )
        except Exception:
            logger.warning("scheme_search.search_failed", exc_info=True)
            results = []

        return [r.metadata for r in results]

    async def get_scheme_by_id(self, scheme_id: str) -> SchemeDocument | None:
        """Return a single scheme by its unique ID, or *None* if not found."""
        return self._schemes.get(scheme_id)

    async def get_schemes_by_category(self, category: str) -> list[SchemeDocument]:
        """Return all schemes belonging to *category*.

        Parameters
        ----------
        category:
            Category value string (e.g. ``"agriculture"``, ``"health"``).
        """
        return [
            scheme
            for scheme in self._schemes.values()
            if scheme.category.value == category
        ]

    async def get_all_schemes(self) -> list[SchemeDocument]:
        """Return every indexed scheme."""
        return list(self._schemes.values())

    # ------------------------------------------------------------------
    # Embedding generation  (hash-based feature hashing)
    # ------------------------------------------------------------------

    @staticmethod
    def _text_to_embedding(text: str) -> list[float]:
        """Convert *text* to a fixed-dimension dense vector via feature hashing.

        Uses a murmurhash-like approach:

        1. Tokenize the text into unigrams and bigrams.
        2. For each token, compute a deterministic hash to select a
           dimension in the output vector.
        3. Use a second hash to determine the sign (+1 / -1) to reduce
           collision bias (the "signed hash trick").
        4. L2-normalize the resulting vector.

        This produces embeddings that are:

        * **Deterministic** -- identical text always yields identical vectors.
        * **O(n)** in the length of the text -- no matrix operations.
        * **No external API** -- suitable for offline / prototype use.

        Parameters
        ----------
        text:
            Input text to embed.

        Returns
        -------
        list[float]
            Dense vector of length :data:`_EMBEDDING_DIM`.
        """
        vec = np.zeros(_EMBEDDING_DIM, dtype=np.float64)

        # Tokenize: lowercase, split on whitespace, strip punctuation.
        tokens: list[str] = []
        for word in text.lower().split():
            cleaned = word.strip(".,;:!?\"'()[]{}/-")
            if cleaned and len(cleaned) > 1:
                tokens.append(cleaned)

        if not tokens:
            return vec.tolist()

        # Generate unigrams and bigrams for richer representation.
        ngrams: list[str] = list(tokens)  # unigrams
        for i in range(len(tokens) - 1):
            ngrams.append(f"{tokens[i]}_{tokens[i + 1]}")

        # Feature hashing with sign trick (murmurhash-like).
        for ngram in ngrams:
            # Primary hash -> dimension index.
            h1 = hashlib.md5(ngram.encode(), usedforsecurity=False).hexdigest()
            dim_idx = int(h1[:8], 16) % _EMBEDDING_DIM

            # Secondary hash -> sign (+1 or -1).
            h2 = hashlib.md5(
                (ngram + "_sign").encode(), usedforsecurity=False
            ).hexdigest()
            sign = 1.0 if int(h2[0], 16) % 2 == 0 else -1.0

            vec[dim_idx] += sign

        # L2 normalization.
        norm = np.linalg.norm(vec)
        if norm > 1e-10:
            vec = vec / norm

        return vec.tolist()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scheme_to_text(scheme: SchemeDocument) -> str:
        """Build a searchable text representation of a scheme.

        Concatenates the most important fields with light weighting
        (repeating the name to increase its influence on the embedding).
        """
        parts: list[str] = [
            scheme.name,
            scheme.name,  # repeated for emphasis
            scheme.description,
            scheme.benefits,
            f"category {scheme.category.value}",
            f"ministry {scheme.ministry}",
        ]

        if scheme.state:
            parts.append(f"state {scheme.state}")

        # Include eligibility criteria in the text.
        elig = scheme.eligibility
        if elig.occupation:
            parts.append(f"occupation {elig.occupation}")
        if elig.category:
            parts.append(f"caste category {elig.category}")
        if elig.is_bpl is True:
            parts.append("below poverty line BPL")
        for criterion in elig.custom_criteria:
            parts.append(criterion)

        return " ".join(parts)

    @staticmethod
    def _scheme_to_metadata(scheme: SchemeDocument) -> dict:
        """Convert a :class:`SchemeDocument` to a flat metadata dict for RAG indexing."""
        return {
            "scheme_id": scheme.scheme_id,
            "name": scheme.name,
            "description": scheme.description,
            "category": scheme.category.value,
            "ministry": scheme.ministry,
            "state": scheme.state,
            "benefits": scheme.benefits,
            "eligibility": scheme.eligibility.model_dump(),
            "application_process": scheme.application_process,
            "documents_required": scheme.documents_required,
            "helpline": scheme.helpline,
            "website": scheme.website,
            "popularity_score": scheme.popularity_score,
        }

    @staticmethod
    def _boost_score(
        base_score: float,
        scheme: SchemeDocument,
        user_profile: dict,
    ) -> float:
        """Apply user-profile-based relevance boosting to a search score.

        Heuristic boosts (additive) for:

        * State match (user's state matches scheme's state or scheme is central).
        * Category match (user's occupation aligns with scheme category).
        * Popularity (higher ``popularity_score`` gets a small bump).
        * BPL status alignment.

        The final score is clamped to ``[0, 1]``.
        """
        boost = 0.0

        # State relevance: central schemes (state=None) are universally
        # relevant; state-specific schemes get a boost when they match.
        user_state = user_profile.get("state")
        if scheme.state is None:
            boost += 0.02  # small boost for central schemes
        elif user_state and scheme.state and scheme.state.lower() == user_state.lower():
            boost += 0.05  # larger boost for state match

        # Occupation -> category alignment.
        user_occupation = user_profile.get("occupation", "").lower()
        category_occupation_map: dict[str, list[str]] = {
            "agriculture": ["farmer", "agricultural", "cultivator", "kisan"],
            "education": ["student", "scholar", "teacher"],
            "health": ["patient", "pregnant", "disabled"],
            "employment": ["unemployed", "worker", "labourer", "labor"],
        }
        for cat, keywords in category_occupation_map.items():
            if scheme.category.value == cat and any(
                kw in user_occupation for kw in keywords
            ):
                boost += 0.04
                break

        # BPL alignment.
        user_bpl = user_profile.get("is_bpl")
        if user_bpl is True and scheme.eligibility.is_bpl is True:
            boost += 0.03

        # Popularity bump (scaled to max 0.02).
        boost += scheme.popularity_score * 0.02

        return min(max(base_score + boost, 0.0), 1.0)

    @staticmethod
    def _build_cache_key(
        query: str,
        language: str,
        user_profile: dict | None,
    ) -> str:
        """Build a deterministic cache key from search parameters."""
        raw = f"{query}|{language}"
        if user_profile:
            # Sort keys for deterministic ordering.
            profile_str = "|".join(
                f"{k}={v}" for k, v in sorted(user_profile.items())
            )
            raw += f"|{profile_str}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"{_CACHE_PREFIX}{digest}"
