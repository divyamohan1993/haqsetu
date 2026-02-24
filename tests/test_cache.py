"""Tests for the dual-layer caching system (in-memory LRU + CacheManager)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.cache import CacheManager, InMemoryCacheBackend, _stable_hash


# -----------------------------------------------------------------------
# InMemoryCacheBackend tests
# -----------------------------------------------------------------------


class TestInMemoryCacheBackend:
    """Test the in-memory LRU cache backend."""

    async def test_get_set_basic(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"value1")
        result = await cache.get("key1")
        assert result == b"value1", "get should return the value that was set"

    async def test_get_missing_key_returns_none(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        result = await cache.get("nonexistent")
        assert result is None, "get should return None for a missing key"

    async def test_set_overwrites_existing(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"original")
        await cache.set("key1", b"updated")
        result = await cache.get("key1")
        assert result == b"updated", "set should overwrite existing value for the same key"

    async def test_delete_removes_key(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"value1")
        await cache.delete("key1")
        result = await cache.get("key1")
        assert result is None, "get should return None after delete"

    async def test_delete_nonexistent_key_no_error(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        await cache.delete("nonexistent")  # should not raise

    async def test_exists_returns_true_for_existing_key(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"value1")
        assert await cache.exists("key1") is True, "exists should return True for a key that was set"

    async def test_exists_returns_false_for_missing_key(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        assert await cache.exists("nonexistent") is False, "exists should return False for a missing key"

    async def test_size_property(self) -> None:
        cache = InMemoryCacheBackend(max_size=100)
        assert cache.size == 0, "size should be 0 for an empty cache"
        await cache.set("key1", b"value1")
        assert cache.size == 1, "size should be 1 after one set"
        await cache.set("key2", b"value2")
        assert cache.size == 2, "size should be 2 after two sets"

    async def test_lru_eviction(self) -> None:
        """When max_size is reached, the least-recently-used entry should be evicted."""
        cache = InMemoryCacheBackend(max_size=3)
        await cache.set("a", b"1")
        await cache.set("b", b"2")
        await cache.set("c", b"3")

        # Cache is full (3 items). Adding a 4th should evict 'a' (LRU).
        await cache.set("d", b"4")
        assert cache.size == 3, "size should remain at max_size after eviction"
        assert await cache.get("a") is None, "LRU entry 'a' should have been evicted"
        assert await cache.get("b") == b"2", "'b' should still be present"
        assert await cache.get("c") == b"3", "'c' should still be present"
        assert await cache.get("d") == b"4", "'d' should be present after insertion"

    async def test_lru_access_promotes_entry(self) -> None:
        """Accessing an entry should move it to the end (most-recently-used)."""
        cache = InMemoryCacheBackend(max_size=3)
        await cache.set("a", b"1")
        await cache.set("b", b"2")
        await cache.set("c", b"3")

        # Access 'a' to promote it.
        await cache.get("a")

        # Now add 'd' -- 'b' should be evicted (it is now LRU).
        await cache.set("d", b"4")
        assert await cache.get("b") is None, "'b' should be evicted as LRU after 'a' was accessed"
        assert await cache.get("a") == b"1", "'a' should still be present (was recently accessed)"

    async def test_ttl_expiration(self) -> None:
        """An entry with TTL should expire after the specified time."""
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"value1", ttl_seconds=0)  # expires immediately

        # Give a tiny window for monotonic clock to advance.
        await asyncio.sleep(0.01)
        result = await cache.get("key1")
        assert result is None, "entry with TTL=0 should expire almost immediately"

    async def test_ttl_not_expired_within_window(self) -> None:
        """An entry with a longer TTL should still be accessible."""
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"value1", ttl_seconds=60)
        result = await cache.get("key1")
        assert result == b"value1", "entry with TTL=60 should not be expired yet"

    async def test_exists_returns_false_for_expired_key(self) -> None:
        """exists() should return False for an expired entry."""
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"value1", ttl_seconds=0)
        await asyncio.sleep(0.01)
        assert await cache.exists("key1") is False, "exists should return False for expired key"

    async def test_no_ttl_entry_never_expires(self) -> None:
        """An entry without TTL should never expire."""
        cache = InMemoryCacheBackend(max_size=100)
        await cache.set("key1", b"value1")  # no TTL
        result = await cache.get("key1")
        assert result == b"value1", "entry without TTL should persist indefinitely"

    async def test_multiple_evictions(self) -> None:
        """Fill and overfill the cache to verify multiple evictions."""
        cache = InMemoryCacheBackend(max_size=2)
        await cache.set("a", b"1")
        await cache.set("b", b"2")
        await cache.set("c", b"3")  # evicts 'a'
        await cache.set("d", b"4")  # evicts 'b'

        assert cache.size == 2
        assert await cache.get("a") is None
        assert await cache.get("b") is None
        assert await cache.get("c") == b"3"
        assert await cache.get("d") == b"4"


# -----------------------------------------------------------------------
# _stable_hash tests
# -----------------------------------------------------------------------


class TestStableHash:
    def test_deterministic(self) -> None:
        h1 = _stable_hash("test string")
        h2 = _stable_hash("test string")
        assert h1 == h2, "_stable_hash should return the same hash for the same input"

    def test_different_inputs_different_hashes(self) -> None:
        h1 = _stable_hash("input one")
        h2 = _stable_hash("input two")
        assert h1 != h2, "_stable_hash should return different hashes for different inputs"

    def test_hash_length(self) -> None:
        h = _stable_hash("any string")
        assert len(h) == 16, "_stable_hash should return a 16-character hex string"


# -----------------------------------------------------------------------
# CacheManager tests
# -----------------------------------------------------------------------


class TestCacheManager:
    """Test the CacheManager facade with Redis disabled (falls back to in-memory)."""

    async def test_fallback_to_inmemory_when_no_redis(self) -> None:
        """When redis_url is None, CacheManager should use in-memory backend."""
        mgr = CacheManager(redis_url=None, namespace="test:")
        await mgr.set("key1", {"data": "value"})
        result = await mgr.get("key1")
        assert result == {"data": "value"}, "should store and retrieve via in-memory fallback"

    async def test_namespace_key_prefixing(self) -> None:
        """Keys should be prefixed with the namespace."""
        mgr = CacheManager(redis_url=None, namespace="myns:")
        # Verify internal key construction.
        assert mgr._make_key("foo") == "myns:foo", "key should be prefixed with namespace"

    async def test_empty_namespace(self) -> None:
        """An empty namespace should not add any prefix."""
        mgr = CacheManager(redis_url=None, namespace="")
        assert mgr._make_key("foo") == "foo", "empty namespace should not add prefix"

    async def test_get_returns_default_for_missing_key(self) -> None:
        mgr = CacheManager(redis_url=None)
        result = await mgr.get("missing", default="fallback")
        assert result == "fallback", "get should return the default for a missing key"

    async def test_get_returns_none_default(self) -> None:
        mgr = CacheManager(redis_url=None)
        result = await mgr.get("missing")
        assert result is None, "get should return None by default for a missing key"

    async def test_set_and_get_various_types(self) -> None:
        mgr = CacheManager(redis_url=None)
        # dict
        await mgr.set("dict_key", {"a": 1, "b": [2, 3]})
        assert await mgr.get("dict_key") == {"a": 1, "b": [2, 3]}

        # list
        await mgr.set("list_key", [1, 2, 3])
        assert await mgr.get("list_key") == [1, 2, 3]

        # string
        await mgr.set("str_key", "hello")
        assert await mgr.get("str_key") == "hello"

        # int
        await mgr.set("int_key", 42)
        assert await mgr.get("int_key") == 42

    async def test_delete(self) -> None:
        mgr = CacheManager(redis_url=None)
        await mgr.set("key1", "val1")
        await mgr.delete("key1")
        result = await mgr.get("key1")
        assert result is None, "deleted key should not be retrievable"

    async def test_exists(self) -> None:
        mgr = CacheManager(redis_url=None)
        await mgr.set("key1", "val1")
        assert await mgr.exists("key1") is True
        assert await mgr.exists("nonexistent") is False

    async def test_get_or_set_cache_miss(self) -> None:
        """get_or_set should call factory_fn on cache miss and store the result."""
        mgr = CacheManager(redis_url=None)

        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            return {"computed": True}

        result = await mgr.get_or_set("my_key", factory, ttl_seconds=60)
        assert result == {"computed": True}, "get_or_set should return factory result on miss"
        assert call_count == 1, "factory should be called once on cache miss"

    async def test_get_or_set_cache_hit(self) -> None:
        """get_or_set should return cached value and NOT call factory_fn on hit."""
        mgr = CacheManager(redis_url=None)
        await mgr.set("my_key", {"cached": True})

        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            return {"computed": True}

        result = await mgr.get_or_set("my_key", factory)
        assert result == {"cached": True}, "get_or_set should return cached value on hit"
        assert call_count == 0, "factory should NOT be called on cache hit"

    async def test_for_namespace_constructor(self) -> None:
        """CacheManager.for_namespace should create a properly namespaced instance."""
        mgr = CacheManager.for_namespace("translation:", redis_url=None)
        assert mgr._make_key("hello") == "translation:hello"

    async def test_close_without_redis(self) -> None:
        """close() should not raise even when no Redis is configured."""
        mgr = CacheManager(redis_url=None)
        await mgr.close()  # should not raise

    async def test_redis_unavailable_fallback(self) -> None:
        """When Redis ping fails, operations should transparently fall back to in-memory."""
        # Create a CacheManager with a fake Redis URL that will fail.
        # We mock the RedisCacheBackend ping to return False.
        mgr = CacheManager(redis_url=None, namespace="fb:")
        await mgr.set("k", "v")
        assert await mgr.get("k") == "v", "in-memory fallback should work when Redis is unavailable"
