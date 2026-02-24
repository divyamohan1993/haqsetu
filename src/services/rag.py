"""Retrieval-Augmented Generation (RAG) service using numpy for vector search.

Uses brute-force cosine similarity over a dense numpy matrix.  For the
prototype scale (~2,300 government schemes) this is efficient: a single
matrix-vector multiply on float32 takes <1 ms on any modern CPU.

Production upgrade path:
  - Qdrant (managed vector DB, supports filtering natively)
  - FAISS (Facebook's ANN library, IVF-PQ for million-scale)
  - Vertex AI Vector Search (fully managed, auto-scaling)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DIM: Final[int] = 768
_RRF_K: Final[int] = 60  # Reciprocal Rank Fusion constant (standard value)

# English stopwords — small, static set for lightweight keyword matching.
_STOPWORDS: Final[frozenset[str]] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "am", "not", "no",
    "nor", "so", "and", "but", "or", "if", "for", "of", "to", "in", "on",
    "at", "by", "up", "as", "it", "its", "he", "she", "we", "they", "i",
    "me", "my", "you", "your", "this", "that", "these", "those", "with",
    "from", "what", "which", "who", "whom", "how", "all", "each", "every",
    "any", "few", "more", "most", "other", "some", "such", "than", "too",
    "very", "just", "also", "about", "over", "after", "before",
})


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SearchResult:
    """A single search result from the RAG index."""

    doc_id: str
    score: float
    metadata: dict


# ---------------------------------------------------------------------------
# RAGService
# ---------------------------------------------------------------------------


class RAGService:
    """In-process vector search engine backed by NumPy.

    Parameters
    ----------
    embedding_dim:
        Dimensionality of the embedding vectors (default 768, matching
        ``textembedding-gecko@003``).

    Design notes
    ------------
    * All vectors are stored as **float32** to halve memory vs float64.
    * ``search()`` uses a single matrix-vector multiply (``_embeddings @ query``)
      followed by ``np.argpartition`` for O(n) top-k selection — no full sort.
    * ``hybrid_search()`` combines semantic similarity with BM25-style keyword
      matching via Reciprocal Rank Fusion (RRF).
    * IDF values and document lengths are precomputed at index time to keep
      query-time BM25 scoring fast.
    """

    __slots__ = (
        "_dim",
        "_documents",
        "_doc_lengths",
        "_doc_token_freqs",
        "_embeddings",
        "_idf",
        "_index_map",
        "_avg_doc_length",
        "_corpus_size",
    )

    def __init__(self, embedding_dim: int = _DEFAULT_DIM) -> None:
        self._dim: int = embedding_dim

        # Vector storage — starts empty, built up via index_document / index_batch
        self._embeddings: np.ndarray = np.empty((0, embedding_dim), dtype=np.float32)

        # Document storage — parallel to the rows of _embeddings
        self._documents: list[dict] = []

        # doc_id -> row index for O(1) lookup
        self._index_map: dict[str, int] = {}

        # BM25 precomputed data
        self._doc_token_freqs: list[dict[str, int]] = []  # per-document term freqs
        self._doc_lengths: list[int] = []  # token count per document
        self._idf: dict[str, float] = {}  # inverse document frequency per term
        self._avg_doc_length: float = 0.0
        self._corpus_size: int = 0

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index_document(
        self,
        doc_id: str,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        """Add or update a single document in the index.

        Parameters
        ----------
        doc_id:
            Unique identifier for the document (e.g. ``"pm-kisan"``).
        embedding:
            Dense vector of length ``embedding_dim``.
        metadata:
            Arbitrary metadata dict stored alongside the vector.
        """
        vec = np.asarray(embedding, dtype=np.float32).reshape(1, -1)

        if doc_id in self._index_map:
            # Update existing document in-place.
            idx = self._index_map[doc_id]
            self._embeddings[idx] = vec[0]
            self._documents[idx] = metadata
            self._doc_token_freqs[idx] = self._build_token_freqs(metadata)
            self._doc_lengths[idx] = sum(self._doc_token_freqs[idx].values())
        else:
            # Append new row — grows the numpy array dynamically.
            idx = len(self._documents)
            self._index_map[doc_id] = idx
            self._documents.append(metadata)

            if self._embeddings.shape[0] == 0:
                self._embeddings = vec
            else:
                self._embeddings = np.vstack([self._embeddings, vec])

            token_freqs = self._build_token_freqs(metadata)
            self._doc_token_freqs.append(token_freqs)
            self._doc_lengths.append(sum(token_freqs.values()))

        self._corpus_size = len(self._documents)
        self._recompute_idf()

        logger.debug("rag.indexed_document", doc_id=doc_id, index=idx)

    async def index_batch(
        self,
        documents: list[tuple[str, list[float], dict]],
    ) -> None:
        """Index multiple documents in one shot — much faster than repeated single inserts.

        Parameters
        ----------
        documents:
            List of ``(doc_id, embedding, metadata)`` tuples.
        """
        if not documents:
            return

        n = len(documents)
        embeddings = np.empty((n, self._dim), dtype=np.float32)

        for i, (doc_id, embedding, metadata) in enumerate(documents):
            embeddings[i] = np.asarray(embedding, dtype=np.float32)
            self._index_map[doc_id] = i + len(self._documents)

        # Build BM25 data for the new batch
        new_token_freqs: list[dict[str, int]] = []
        new_doc_lengths: list[int] = []
        for _, _, metadata in documents:
            tf = self._build_token_freqs(metadata)
            new_token_freqs.append(tf)
            new_doc_lengths.append(sum(tf.values()))

        # Re-index: overwrite if there were existing documents, otherwise set fresh.
        # For initial seeding we expect an empty index, so optimize for that path.
        if self._embeddings.shape[0] == 0:
            self._embeddings = embeddings
            self._documents = [meta for _, _, meta in documents]
            self._doc_token_freqs = new_token_freqs
            self._doc_lengths = new_doc_lengths
        else:
            self._embeddings = np.vstack([self._embeddings, embeddings])
            self._documents.extend(meta for _, _, meta in documents)
            self._doc_token_freqs.extend(new_token_freqs)
            self._doc_lengths.extend(new_doc_lengths)

        self._corpus_size = len(self._documents)
        self._recompute_idf()

        logger.info("rag.batch_indexed", count=n, total=self._corpus_size)

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """Retrieve the top-k most similar documents via cosine similarity.

        Complexity: O(n) where n = number of indexed documents, dominated by
        a single matrix-vector multiply followed by ``np.argpartition``.

        Parameters
        ----------
        query_embedding:
            Dense query vector (same dimensionality as indexed vectors).
        top_k:
            Number of results to return.
        filters:
            Optional dict of field-value pairs to filter results (e.g.
            ``{"category": "agriculture", "state": None}``).  Filters are
            applied **post-scoring** so that we always consider the full
            index during the similarity computation.
        """
        if self._corpus_size == 0:
            return []

        # Normalize query vector once.
        query = np.asarray(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        if query_norm < 1e-10:
            logger.warning("rag.zero_norm_query")
            return []
        query = query / query_norm

        # Normalize all document vectors (row-wise).
        norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)  # avoid division by zero
        normed = self._embeddings / norms

        # Cosine similarity = dot product of unit vectors.
        # Shape: (n,) — one score per document.
        similarities = normed @ query

        # Apply filters post-scoring if provided.
        if filters:
            mask = self._build_filter_mask(filters)
            similarities = similarities * mask  # zero out non-matching docs

        # O(n) top-k via argpartition (faster than full argsort for large n).
        k = min(top_k, self._corpus_size)
        if k >= self._corpus_size:
            top_indices = np.argsort(similarities)[::-1][:k]
        else:
            # argpartition gives the k smallest; negate for k largest.
            partitioned = np.argpartition(-similarities, k)[:k]
            # Sort just the top-k for proper ordering.
            top_indices = partitioned[np.argsort(-similarities[partitioned])]

        results: list[SearchResult] = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score <= 0.0:
                continue  # skip filtered-out or negatively-scored docs
            doc_id = self._reverse_lookup(int(idx))
            results.append(SearchResult(
                doc_id=doc_id,
                score=score,
                metadata=self._documents[int(idx)],
            ))

        return results

    # ------------------------------------------------------------------
    # Hybrid search (semantic + keyword via RRF)
    # ------------------------------------------------------------------

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Combine vector similarity with BM25 keyword matching using RRF.

        Reciprocal Rank Fusion (RRF) merges two ranked lists without
        requiring score calibration::

            RRF_score(d) = 1/(k + rank_semantic(d)) + 1/(k + rank_keyword(d))

        Parameters
        ----------
        query_text:
            Raw query string for keyword matching.
        query_embedding:
            Dense query vector for semantic matching.
        top_k:
            Number of results to return.
        """
        if self._corpus_size == 0:
            return []

        # 1. Semantic scores
        query = np.asarray(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        if query_norm < 1e-10:
            return []
        query = query / query_norm

        norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        normed = self._embeddings / norms
        semantic_scores = normed @ query

        # 2. BM25 keyword scores
        query_tokens = self._keyword_tokenize(query_text)
        bm25_scores = self._compute_bm25_scores(query_tokens)

        # 3. Compute ranks for RRF
        semantic_ranks = np.empty(self._corpus_size, dtype=np.float64)
        semantic_ranks[np.argsort(-semantic_scores)] = np.arange(1, self._corpus_size + 1)

        bm25_ranks = np.empty(self._corpus_size, dtype=np.float64)
        bm25_ranks[np.argsort(-bm25_scores)] = np.arange(1, self._corpus_size + 1)

        # 4. RRF fusion
        rrf_scores = (1.0 / (float(_RRF_K) + semantic_ranks)) + (1.0 / (float(_RRF_K) + bm25_ranks))

        # 5. Top-k selection via argpartition
        k = min(top_k, self._corpus_size)
        if k >= self._corpus_size:
            top_indices = np.argsort(-rrf_scores)[:k]
        else:
            partitioned = np.argpartition(-rrf_scores, k)[:k]
            top_indices = partitioned[np.argsort(-rrf_scores[partitioned])]

        results: list[SearchResult] = []
        for idx in top_indices:
            doc_id = self._reverse_lookup(int(idx))
            results.append(SearchResult(
                doc_id=doc_id,
                score=float(rrf_scores[idx]),
                metadata=self._documents[int(idx)],
            ))

        return results

    # ------------------------------------------------------------------
    # BM25 scoring
    # ------------------------------------------------------------------

    def _compute_bm25_scores(
        self,
        query_tokens: list[str],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> np.ndarray:
        """Compute BM25 scores for all documents given the query tokens.

        Uses precomputed IDF values and per-document term frequency maps.

        Parameters
        ----------
        query_tokens:
            Tokenized query (lowercase, stopwords removed).
        k1:
            Term frequency saturation parameter.
        b:
            Length normalization parameter.

        Returns
        -------
        np.ndarray:
            BM25 score for each document, shape ``(corpus_size,)``.
        """
        scores = np.zeros(self._corpus_size, dtype=np.float64)

        if not query_tokens or self._corpus_size == 0:
            return scores

        avg_dl = self._avg_doc_length if self._avg_doc_length > 0 else 1.0

        for token in query_tokens:
            idf = self._idf.get(token, 0.0)
            if idf <= 0.0:
                continue

            for i in range(self._corpus_size):
                tf = self._doc_token_freqs[i].get(token, 0)
                if tf == 0:
                    continue
                dl = self._doc_lengths[i]
                # BM25 formula
                numerator = tf * (k1 + 1.0)
                denominator = tf + k1 * (1.0 - b + b * (dl / avg_dl))
                scores[i] += idf * (numerator / denominator)

        return scores

    # ------------------------------------------------------------------
    # Tokenization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _keyword_tokenize(text: str) -> list[str]:
        """Tokenize text for keyword matching.

        Simple whitespace split + lowercasing + stopword removal.
        O(n) in the length of the text.
        """
        tokens: list[str] = []
        for word in text.lower().split():
            # Strip punctuation from edges.
            cleaned = word.strip(".,;:!?\"'()[]{}/-")
            if cleaned and cleaned not in _STOPWORDS and len(cleaned) > 1:
                tokens.append(cleaned)
        return tokens

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_token_freqs(self, metadata: dict) -> dict[str, int]:
        """Build a term-frequency map from a document's searchable text fields."""
        text_parts: list[str] = []

        # Concatenate all searchable text fields.
        for key in ("name", "description", "benefits", "category", "ministry"):
            val = metadata.get(key)
            if isinstance(val, str):
                text_parts.append(val)

        # Also index custom eligibility criteria if present.
        eligibility = metadata.get("eligibility")
        if isinstance(eligibility, dict):
            for criterion in eligibility.get("custom_criteria", []):
                if isinstance(criterion, str):
                    text_parts.append(criterion)

        full_text = " ".join(text_parts)
        tokens = self._keyword_tokenize(full_text)

        freq: dict[str, int] = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        return freq

    def _recompute_idf(self) -> None:
        """Recompute IDF values and average document length after indexing changes."""
        if self._corpus_size == 0:
            self._avg_doc_length = 0.0
            self._idf = {}
            return

        self._avg_doc_length = sum(self._doc_lengths) / self._corpus_size

        # Count document frequency for each term.
        df: dict[str, int] = {}
        for token_freqs in self._doc_token_freqs:
            for token in token_freqs:
                df[token] = df.get(token, 0) + 1

        # IDF with smoothing: log((N - df + 0.5) / (df + 0.5) + 1)
        n = self._corpus_size
        self._idf = {
            token: math.log((n - freq + 0.5) / (freq + 0.5) + 1.0)
            for token, freq in df.items()
        }

    def _build_filter_mask(self, filters: dict) -> np.ndarray:
        """Return a binary mask (0/1) for documents matching all filter criteria.

        Supports filtering on any top-level metadata key.  A filter value
        of ``None`` matches documents where the field is absent or ``None``.
        """
        mask = np.ones(self._corpus_size, dtype=np.float32)

        for key, value in filters.items():
            for i, doc in enumerate(self._documents):
                doc_value = doc.get(key)
                if value is None:
                    # Filter for "field is None or absent"
                    if doc_value is not None:
                        mask[i] = 0.0
                else:
                    if doc_value != value:
                        mask[i] = 0.0

        return mask

    def _reverse_lookup(self, index: int) -> str:
        """Return the doc_id for a given row index.

        Linear scan of the index map — acceptable because this is only
        called for top-k results (k << n).  For production, maintain a
        reverse list.
        """
        for doc_id, idx in self._index_map.items():
            if idx == index:
                return doc_id
        return f"unknown-{index}"

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def corpus_size(self) -> int:
        """Number of indexed documents."""
        return self._corpus_size

    @property
    def embedding_dim(self) -> int:
        """Dimensionality of the embedding vectors."""
        return self._dim
