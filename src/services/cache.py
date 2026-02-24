"""Dual-layer caching system with Redis primary and in-memory LRU fallback.

Provides transparent failover: if Redis is unavailable, all operations
silently degrade to a process-local LRU cache so the application never
blocks on a missing cache backend.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

import orjson
import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Cache backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CacheBackend(Protocol):
    """Async cache backend interface."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------


class RedisCacheBackend:
    """Redis-backed cache using ``redis.asyncio`` with connection pooling."""

    __slots__ = ("_pool", "_redis")

    def __init__(self, url: str = "redis://localhost:6379/0", *, max_connections: int = 20) -> None:
        import redis.asyncio as aioredis

        self._pool = aioredis.ConnectionPool.from_url(
            url,
            max_connections=max_connections,
            decode_responses=False,
        )
        self._redis = aioredis.Redis(connection_pool=self._pool)

    # -- CacheBackend interface ------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        return await self._redis.get(key)

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is not None:
            await self._redis.set(key, value, ex=ttl_seconds)
        else:
            await self._redis.set(key, value)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    # -- Lifecycle -------------------------------------------------------------

    async def close(self) -> None:
        await self._redis.aclose()
        await self._pool.aclose()

    async def ping(self) -> bool:
        """Return *True* if the Redis server is reachable."""
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# In-memory LRU backend
# ---------------------------------------------------------------------------


class _CacheEntry:
    """Single cache entry with optional TTL."""

    __slots__ = ("expires_at", "value")

    def __init__(self, value: bytes, ttl_seconds: int | None) -> None:
        self.value = value
        self.expires_at: float | None = (time.monotonic() + ttl_seconds) if ttl_seconds is not None else None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.monotonic() > self.expires_at


class InMemoryCacheBackend:
    """OrderedDict-based LRU cache with O(1) get/set/delete.

    Thread-safe via :class:`asyncio.Lock` (sufficient for single-process
    async workloads). Expired entries are lazily evicted on access *and*
    eagerly evicted when the cache is full.
    """

    __slots__ = ("_data", "_lock", "_max_size")

    def __init__(self, *, max_size: int = 10_000) -> None:
        self._max_size = max_size
        self._data: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()

    # -- CacheBackend interface ------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expired:
                del self._data[key]
                return None
            # Move to end (most-recently-used)
            self._data.move_to_end(key)
            return entry.value

    async def set(self, key: str, value: bytes, ttl_seconds: int | None = None) -> None:
        async with self._lock:
            # Remove existing entry first so move_to_end works correctly
            if key in self._data:
                del self._data[key]
            # Evict least-recently-used entries if at capacity
            while len(self._data) >= self._max_size:
                self._data.popitem(last=False)
            self._data[key] = _CacheEntry(value, ttl_seconds)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def exists(self, key: str) -> bool:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            if entry.expired:
                del self._data[key]
                return False
            return True

    @property
    def size(self) -> int:
        """Return the current number of (possibly expired) entries."""
        return len(self._data)


# ---------------------------------------------------------------------------
# CacheManager  --  public API
# ---------------------------------------------------------------------------


def _stable_hash(text: str) -> str:
    """Deterministic, URL-safe hash for cache keys."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class CacheManager:
    """Unified caching facade with automatic Redis -> in-memory fallback.

    Parameters
    ----------
    redis_url:
        Redis connection string.  Pass *None* to skip Redis entirely.
    namespace:
        Optional prefix prepended to every key (e.g. ``"translation:"``).
    inmemory_max_size:
        Maximum entries for the in-memory fallback cache.
    """

    __slots__ = (
        "_fallback",
        "_namespace",
        "_redis",
        "_redis_available",
        "_redis_checked",
    )

    def __init__(
        self,
        *,
        redis_url: str | None = "redis://localhost:6379/0",
        namespace: str = "",
        inmemory_max_size: int = 10_000,
    ) -> None:
        self._namespace = namespace
        self._fallback = InMemoryCacheBackend(max_size=inmemory_max_size)
        self._redis: RedisCacheBackend | None = None
        self._redis_available: bool = False
        self._redis_checked: bool = False

        if redis_url is not None:
            try:
                self._redis = RedisCacheBackend(url=redis_url)
            except Exception:
                logger.warning("cache.redis_init_failed", redis_url=redis_url)
                self._redis = None

    # -- Internal helpers ------------------------------------------------------

    def _make_key(self, key: str) -> str:
        if self._namespace:
            return f"{self._namespace}{key}"
        return key

    async def _backend(self) -> CacheBackend:
        """Return the best available backend, checking Redis once lazily."""
        if self._redis is not None and not self._redis_checked:
            self._redis_checked = True
            self._redis_available = await self._redis.ping()
            if self._redis_available:
                logger.info("cache.redis_connected")
            else:
                logger.warning("cache.redis_unavailable_using_inmemory")

        if self._redis_available and self._redis is not None:
            return self._redis
        return self._fallback

    async def _safe_redis_op(self, method: str, key: str, *args: Any, **kwargs: Any) -> Any:
        """Try Redis; on failure, flip to in-memory and retry transparently."""
        if self._redis_available and self._redis is not None:
            try:
                return await getattr(self._redis, method)(key, *args, **kwargs)
            except Exception:
                logger.warning("cache.redis_op_failed", method=method, key=key)
                self._redis_available = False

        return await getattr(self._fallback, method)(key, *args, **kwargs)

    # -- Public API ------------------------------------------------------------

    async def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a cached value, deserialised from bytes via *orjson*."""
        full_key = self._make_key(key)
        # Ensure the backend has been checked at least once
        await self._backend()
        raw: bytes | None = await self._safe_redis_op("get", full_key)
        if raw is None:
            return default
        try:
            return orjson.loads(raw)
        except (orjson.JSONDecodeError, ValueError):
            return default

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Serialise *value* via *orjson* and store it."""
        full_key = self._make_key(key)
        raw = orjson.dumps(value)
        await self._backend()
        await self._safe_redis_op("set", full_key, raw, ttl_seconds=ttl_seconds)

    async def get_or_set(
        self,
        key: str,
        factory_fn: Any,
        ttl_seconds: int | None = None,
    ) -> Any:
        """Cache-aside pattern: return cached value or call *factory_fn* to populate.

        *factory_fn* must be an async callable that returns the value to cache.
        """
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await factory_fn()
        await self.set(key, value, ttl_seconds=ttl_seconds)
        return value

    async def delete(self, key: str) -> None:
        full_key = self._make_key(key)
        await self._backend()
        await self._safe_redis_op("delete", full_key)

    async def exists(self, key: str) -> bool:
        full_key = self._make_key(key)
        await self._backend()
        result = await self._safe_redis_op("exists", full_key)
        return bool(result)

    # -- Lifecycle -------------------------------------------------------------

    async def close(self) -> None:
        """Cleanly shut down the Redis connection pool (if any)."""
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.close()

    # -- Convenience constructors ----------------------------------------------

    @staticmethod
    def for_namespace(
        namespace: str,
        *,
        redis_url: str | None = "redis://localhost:6379/0",
        inmemory_max_size: int = 10_000,
    ) -> CacheManager:
        """Create a :class:`CacheManager` scoped to *namespace*.

        Example::

            translation_cache = CacheManager.for_namespace("translation:")
            scheme_cache = CacheManager.for_namespace("scheme:")
        """
        return CacheManager(
            redis_url=redis_url,
            namespace=namespace,
            inmemory_max_size=inmemory_max_size,
        )
