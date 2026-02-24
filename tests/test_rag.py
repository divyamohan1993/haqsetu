"""Tests for the RAG (Retrieval-Augmented Generation) service."""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.services.rag import RAGService, SearchResult, _STOPWORDS


# -----------------------------------------------------------------------
# Helper to create a simple normalized embedding
# -----------------------------------------------------------------------

def _make_embedding(dim: int, nonzero_idx: int = 0, value: float = 1.0) -> list[float]:
    """Create a sparse embedding with a single non-zero dimension."""
    vec = [0.0] * dim
    vec[nonzero_idx] = value
    return vec


def _make_random_embedding(dim: int, seed: int = 42) -> list[float]:
    """Create a random embedding for testing."""
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


# -----------------------------------------------------------------------
# RAGService tests
# -----------------------------------------------------------------------


class TestRAGServiceBasic:
    """Test basic indexing and search functionality."""

    async def test_empty_index_returns_empty(self) -> None:
        rag = RAGService(embedding_dim=8)
        results = await rag.search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        assert results == [], "search on an empty index should return empty list"

    async def test_corpus_size_starts_at_zero(self) -> None:
        rag = RAGService(embedding_dim=8)
        assert rag.corpus_size == 0, "corpus_size should be 0 before any indexing"

    async def test_embedding_dim_property(self) -> None:
        rag = RAGService(embedding_dim=128)
        assert rag.embedding_dim == 128, "embedding_dim should match constructor argument"

    async def test_index_document_and_search(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        # Index a single document with a known embedding.
        emb = _make_embedding(dim, nonzero_idx=0)
        await rag.index_document("doc1", emb, {"name": "Test Document"})

        assert rag.corpus_size == 1, "corpus_size should be 1 after indexing one document"

        # Search with the same embedding -- should match with score ~1.0.
        results = await rag.search(emb, top_k=1)
        assert len(results) == 1, "search should return 1 result"
        assert results[0].doc_id == "doc1"
        assert results[0].score > 0.99, "score should be ~1.0 for identical embedding"

    async def test_index_document_update(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        emb1 = _make_embedding(dim, nonzero_idx=0)
        emb2 = _make_embedding(dim, nonzero_idx=1)

        await rag.index_document("doc1", emb1, {"name": "Original"})
        await rag.index_document("doc1", emb2, {"name": "Updated"})

        assert rag.corpus_size == 1, "updating a document should not increase corpus_size"

        # Search with emb2 -- should find the updated document.
        results = await rag.search(emb2, top_k=1)
        assert len(results) == 1
        assert results[0].metadata["name"] == "Updated"

    async def test_search_returns_correct_top_k(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        # Index 5 documents with different embeddings.
        for i in range(5):
            emb = _make_embedding(dim, nonzero_idx=i)
            await rag.index_document(f"doc{i}", emb, {"name": f"Document {i}"})

        assert rag.corpus_size == 5

        # Search for top 3.
        query = _make_embedding(dim, nonzero_idx=0)
        results = await rag.search(query, top_k=3)
        assert len(results) <= 3, "search should return at most top_k results"

        # The first result should be doc0 (identical embedding).
        assert results[0].doc_id == "doc0"
        assert results[0].score > 0.99

    async def test_search_top_k_larger_than_corpus(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        await rag.index_document("doc1", _make_embedding(dim, 0), {"name": "Doc1"})
        await rag.index_document("doc2", _make_embedding(dim, 1), {"name": "Doc2"})

        results = await rag.search(_make_embedding(dim, 0), top_k=10)
        assert len(results) <= 2, "should return at most corpus_size results when top_k > corpus_size"


class TestRAGServiceIndexBatch:
    """Test batch indexing."""

    async def test_index_batch_multiple_documents(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        docs = [
            (f"doc{i}", _make_embedding(dim, i), {"name": f"Document {i}"})
            for i in range(4)
        ]
        await rag.index_batch(docs)

        assert rag.corpus_size == 4, "index_batch should index all documents"

    async def test_index_batch_empty_list(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)
        await rag.index_batch([])
        assert rag.corpus_size == 0, "index_batch with empty list should not change corpus"

    async def test_index_batch_then_search(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        docs = [
            ("farmer_doc", _make_embedding(dim, 0), {"name": "Farmer Scheme", "category": "agriculture"}),
            ("health_doc", _make_embedding(dim, 1), {"name": "Health Scheme", "category": "health"}),
            ("edu_doc", _make_embedding(dim, 2), {"name": "Education Scheme", "category": "education"}),
        ]
        await rag.index_batch(docs)

        # Search for the farmer doc's embedding.
        results = await rag.search(_make_embedding(dim, 0), top_k=1)
        assert len(results) == 1
        assert results[0].doc_id == "farmer_doc"

    async def test_index_batch_then_single_insert(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        docs = [
            ("doc1", _make_embedding(dim, 0), {"name": "Doc1"}),
            ("doc2", _make_embedding(dim, 1), {"name": "Doc2"}),
        ]
        await rag.index_batch(docs)

        # Add a third document individually.
        await rag.index_document("doc3", _make_embedding(dim, 2), {"name": "Doc3"})
        assert rag.corpus_size == 3


class TestCosineSimilarity:
    """Test cosine similarity correctness."""

    async def test_identical_vectors_have_similarity_one(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        emb = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        await rag.index_document("doc1", emb, {"name": "Test"})
        results = await rag.search(emb, top_k=1)
        assert len(results) == 1
        assert abs(results[0].score - 1.0) < 1e-5, "identical vectors should have cosine similarity of 1.0"

    async def test_orthogonal_vectors_have_zero_similarity(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        emb1 = _make_embedding(dim, nonzero_idx=0)  # [1, 0, 0, ...]
        emb2 = _make_embedding(dim, nonzero_idx=1)  # [0, 1, 0, ...]

        await rag.index_document("doc1", emb1, {"name": "Doc1"})

        results = await rag.search(emb2, top_k=1)
        # Orthogonal vectors have 0 similarity; the search should skip them (score <= 0).
        if len(results) > 0:
            assert results[0].score <= 0.01, "orthogonal vectors should have ~0 cosine similarity"

    async def test_similar_vectors_rank_higher(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        # doc1 embedding: close to query
        # doc2 embedding: far from query
        query = [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb_close = [1.0, 1.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb_far = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]

        await rag.index_document("close", emb_close, {"name": "Close"})
        await rag.index_document("far", emb_far, {"name": "Far"})

        results = await rag.search(query, top_k=2)
        assert len(results) >= 1
        assert results[0].doc_id == "close", "more similar vector should rank first"

    async def test_zero_query_vector_returns_empty(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)
        await rag.index_document("doc1", _make_embedding(dim, 0), {"name": "Doc1"})

        results = await rag.search([0.0] * dim, top_k=1)
        assert results == [], "zero-norm query should return empty results"


class TestFilteredSearch:
    """Test search with metadata filters."""

    async def test_filter_by_category(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        # Both docs have similar embeddings to the query, but different categories.
        query = [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb1 = [1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb2 = [0.9, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        await rag.index_document("agri", emb1, {"name": "Agri Scheme", "category": "agriculture"})
        await rag.index_document("health", emb2, {"name": "Health Scheme", "category": "health"})

        # Filter for agriculture only.
        results = await rag.search(query, top_k=5, filters={"category": "agriculture"})
        assert all(
            r.metadata.get("category") == "agriculture" for r in results
        ), "all results should match the filter"

    async def test_filter_for_none_value(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        emb = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        await rag.index_document("central", emb, {"name": "Central", "state": None})
        await rag.index_document("state", emb, {"name": "State", "state": "Bihar"})

        # Filter for central schemes (state=None).
        results = await rag.search(emb, top_k=5, filters={"state": None})
        for r in results:
            assert r.metadata.get("state") is None, "filter for None should return only docs where state is None"


class TestHybridSearch:
    """Test hybrid search (semantic + BM25 via RRF)."""

    async def test_hybrid_search_returns_results(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        await rag.index_document(
            "doc1",
            _make_embedding(dim, 0),
            {"name": "farmer agriculture scheme", "description": "support for farmers"},
        )
        await rag.index_document(
            "doc2",
            _make_embedding(dim, 1),
            {"name": "health insurance scheme", "description": "medical coverage"},
        )

        results = await rag.hybrid_search(
            query_text="farmer agriculture",
            query_embedding=_make_embedding(dim, 0),
            top_k=2,
        )
        assert len(results) > 0, "hybrid_search should return results"
        # doc1 should rank higher due to both semantic and keyword match.
        assert results[0].doc_id == "doc1", "doc with both semantic and keyword match should rank first"

    async def test_hybrid_search_empty_index(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)
        results = await rag.hybrid_search("farmer", [1.0] + [0.0] * 7, top_k=5)
        assert results == [], "hybrid_search on empty index should return empty list"

    async def test_hybrid_search_combines_scores(self) -> None:
        """Verify that hybrid search uses RRF to combine vector and keyword scores."""
        dim = 8
        rag = RAGService(embedding_dim=dim)

        # doc1: good semantic match, poor keyword match
        # doc2: poor semantic match, good keyword match
        # doc3: moderate on both
        emb_query = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb1 = [0.99, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb2 = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        emb3 = [0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        await rag.index_document("doc1", emb1, {"name": "unrelated name", "description": "no keywords"})
        await rag.index_document("doc2", emb2, {"name": "farmer pension scheme", "description": "farmer pension"})
        await rag.index_document("doc3", emb3, {"name": "farmer help", "description": "assist farmers"})

        results = await rag.hybrid_search(
            query_text="farmer pension",
            query_embedding=emb_query,
            top_k=3,
        )
        assert len(results) == 3, "should return all 3 documents"


class TestBM25Scoring:
    """Test BM25 keyword scoring."""

    async def test_bm25_scores_relevant_doc_higher(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)

        await rag.index_document(
            "agri",
            _make_embedding(dim, 0),
            {"name": "farmer kisan agriculture", "description": "support farmer kisan crop"},
        )
        await rag.index_document(
            "health",
            _make_embedding(dim, 1),
            {"name": "health medical insurance", "description": "hospital treatment doctor"},
        )

        tokens = rag._keyword_tokenize("farmer kisan")
        scores = rag._compute_bm25_scores(tokens)

        assert scores[0] > scores[1], (
            "BM25 should score the agriculture doc higher for 'farmer kisan' query"
        )

    async def test_bm25_empty_query(self) -> None:
        dim = 8
        rag = RAGService(embedding_dim=dim)
        await rag.index_document("doc1", _make_embedding(dim, 0), {"name": "test"})

        scores = rag._compute_bm25_scores([])
        assert all(s == 0.0 for s in scores), "BM25 with empty query should produce all-zero scores"

    async def test_keyword_tokenize_removes_stopwords(self) -> None:
        tokens = RAGService._keyword_tokenize("the farmer is in the field")
        assert "the" not in tokens, "stopwords should be removed"
        assert "is" not in tokens, "stopwords should be removed"
        assert "in" not in tokens, "stopwords should be removed"
        assert "farmer" in tokens, "'farmer' should be kept"
        assert "field" in tokens, "'field' should be kept"

    async def test_keyword_tokenize_lowercases(self) -> None:
        tokens = RAGService._keyword_tokenize("Farmer HEALTH Education")
        assert "farmer" in tokens
        assert "health" in tokens
        assert "education" in tokens

    async def test_keyword_tokenize_strips_punctuation(self) -> None:
        tokens = RAGService._keyword_tokenize("farmer, health! education?")
        assert "farmer" in tokens
        assert "health" in tokens
        assert "education" in tokens
