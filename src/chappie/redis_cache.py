"""Redis Cache layer and context synchronizer for Chappie.

Provides sub-millisecond retrieval of hot .chappie context documents, session state caching,
and cache invalidation with automatic fallback to in-memory caching when Redis is offline.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from chappie.config import Settings, get_settings

logger = logging.getLogger("chappie.redis_cache")

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]
    REDIS_AVAILABLE = False


class InMemoryCacheFallback:
    """Thread-safe in-memory cache fallback used when Redis is unavailable or offline."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}  # key -> (value, expire_at)
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            val, expire_at = entry
            if expire_at is not None and time.time() > expire_at:
                del self._store[key]
                return None
            return val

    def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        with self._lock:
            expire_at = (time.time() + ttl) if (ttl is not None and ttl > 0) else None
            self._store[key] = (value, expire_at)
            return True

    def delete(self, *keys: str) -> int:
        with self._lock:
            count = 0
            for k in keys:
                if k in self._store:
                    del self._store[k]
                    count += 1
            return count

    def delete_prefix(self, prefix: str) -> int:
        with self._lock:
            keys_to_del = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_del:
                del self._store[k]
            return len(keys_to_del)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class RedisCacheManager:
    """Enterprise-grade Redis cache manager for project contexts and session states.

    Features:
    - High performance context caching with configurable TTL.
    - mtime-based validation to ensure disk edits outside Chappie immediately bust the cache.
    - Graceful degradation: seamlessly falls back to thread-safe memory storage if Redis is down.
    - Prefix-based eviction upon project updates.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Any = None
        self._is_connected: bool = False
        self._fallback = InMemoryCacheFallback()
        self._lock = threading.RLock()
        self._init_client()

    def _init_client(self) -> None:
        """Attempt to initialize connection to Redis server."""
        if not self.settings.enable_redis_cache or not REDIS_AVAILABLE:
            self._is_connected = False
            return

        try:
            url = self.settings.redis_url or f"redis://{self.settings.redis_host}:{self.settings.redis_port}/0"
            client = redis.Redis.from_url(
                url,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                decode_responses=True,
            )
            # Ping test
            client.ping()
            self._client = client
            self._is_connected = True
            logger.info("Successfully connected to Redis cache at %s", url)
        except Exception as exc:  # pragma: no cover - fallback path
            logger.warning(
                "Redis server not reachable (%s). Falling back to in-memory context cache.",
                exc,
            )
            self._client = None
            self._is_connected = False

    @property
    def is_redis_active(self) -> bool:
        """Check if active connection to Redis is available."""
        if not self._is_connected or self._client is None:
            return False
        try:
            self._client.ping()
            return True
        except Exception:
            self._is_connected = False
            return False

    # --- Generic Key-Value Operations ---

    def get(self, key: str) -> str | None:
        """Get string value by key with fallback."""
        if self.is_redis_active:
            try:
                return self._client.get(key)
            except Exception as exc:
                logger.debug("Redis get error for key %s: %s", key, exc)
        return self._fallback.get(key)

    def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set string value by key with optional TTL in seconds."""
        effective_ttl = ttl or self.settings.redis_context_ttl_seconds
        success = False
        if self.is_redis_active:
            try:
                if effective_ttl and effective_ttl > 0:
                    self._client.setex(key, effective_ttl, value)
                else:
                    self._client.set(key, value)
                success = True
            except Exception as exc:
                logger.debug("Redis set error for key %s: %s", key, exc)
        # Always maintain in fallback for resilience
        self._fallback.set(key, value, effective_ttl)
        return success or True

    def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        if not keys:
            return 0
        deleted = 0
        if self.is_redis_active:
            try:
                deleted = self._client.delete(*keys)
            except Exception as exc:
                logger.debug("Redis delete error: %s", exc)
        self._fallback.delete(*keys)
        return deleted

    def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching pattern (e.g. 'chappie:project:123:*')."""
        deleted = 0
        if self.is_redis_active:
            try:
                keys = self._client.keys(pattern)
                if keys:
                    deleted = self._client.delete(*keys)
            except Exception as exc:
                logger.debug("Redis delete_pattern error for pattern %s: %s", pattern, exc)
        prefix = pattern.replace("*", "")
        self._fallback.delete_prefix(prefix)
        return deleted

    # --- Project Context Specialization (Active Context Policy) ---

    def _project_context_key(self, project_id: str) -> str:
        return f"chappie:project:{project_id}:context"

    def _project_meta_key(self, project_id: str) -> str:
        return f"chappie:project:{project_id}:meta"

    def _active_project_key(self) -> str:
        return "chappie:active:project_id"

    def get_active_project_id(self) -> str | None:
        """Returns the ID of the currently active project stored in Redis."""
        return self.get(self._active_project_key())

    def set_active_project(
        self,
        project_id: str,
        context: str,
        ttl: int | None = None,
        mtime: float | None = None,
        metadata: dict[str, Any] | None = None,
        evict_others: bool = True,
    ) -> bool:
        """Sets the active project context in Redis, evicting any previously cached inactive contexts."""
        if not project_id:
            return False

        if evict_others:
            self.clear_inactive_contexts(keep_project_id=project_id)

        effective_ttl = ttl or self.settings.redis_context_ttl_seconds
        self.set(self._active_project_key(), project_id, ttl=effective_ttl)
        return self.set_project_context(
            project_id=project_id,
            context=context,
            ttl=effective_ttl,
            mtime=mtime,
            metadata=metadata,
        )

    def clear_inactive_contexts(self, keep_project_id: str | None = None) -> int:
        """Evicts all project context caches from Redis except the active one."""
        deleted_count = 0
        if self.is_redis_active:
            try:
                keys = self._client.keys("chappie:project:*")
                keep_ctx_key = self._project_context_key(keep_project_id) if keep_project_id else None
                keep_meta_key = self._project_meta_key(keep_project_id) if keep_project_id else None

                to_delete = [
                    k for k in keys
                    if k not in (keep_ctx_key, keep_meta_key)
                ]
                if to_delete:
                    deleted_count = self._client.delete(*to_delete)
            except Exception as exc:
                logger.debug("Redis clear_inactive_contexts error: %s", exc)

        with self._fallback._lock:
            keep_ctx_key = self._project_context_key(keep_project_id) if keep_project_id else None
            keep_meta_key = self._project_meta_key(keep_project_id) if keep_project_id else None
            keys_to_del = [
                k for k in self._fallback._store
                if k.startswith("chappie:project:") and k not in (keep_ctx_key, keep_meta_key)
            ]
            for k in keys_to_del:
                del self._fallback._store[k]
                deleted_count += 1
        return deleted_count

    def get_project_context(
        self,
        project_id: str,
        root_path: str | Path | None = None,
    ) -> str | None:
        """Retrieve cached .chappie context document for a project.

        If root_path is provided, checks if the physical .chappie file on disk has been
        modified more recently than the cached mtime. If so, invalidates cache and returns None.
        """
        if not project_id:
            return None

        # Check mtime freshness against disk if root_path exists
        if root_path:
            p_root = Path(root_path)
            chappie_file = p_root / ".chappie" if not p_root.name == ".chappie" else p_root
            if chappie_file.is_file():
                disk_mtime = chappie_file.stat().st_mtime
                cached_meta_raw = self.get(self._project_meta_key(project_id))
                if cached_meta_raw:
                    try:
                        cached_meta = json.loads(cached_meta_raw)
                        cached_mtime = float(cached_meta.get("mtime", 0.0))
                        if disk_mtime > cached_mtime:
                            # Disk file is newer -> invalidate cache entry
                            logger.debug(
                                "Disk .chappie at %s is newer than cached mtime (%s > %s). Invalidating.",
                                chappie_file, disk_mtime, cached_mtime,
                            )
                            self.invalidate_project_context(project_id)
                            return None
                    except (ValueError, TypeError, json.JSONDecodeError):
                        self.invalidate_project_context(project_id)
                        return None

        cached_ctx = self.get(self._project_context_key(project_id))
        return cached_ctx

    def set_project_context(
        self,
        project_id: str,
        context: str,
        ttl: int | None = None,
        mtime: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Store project .chappie context document in Redis with metadata and TTL."""
        if not project_id:
            return False

        effective_ttl = ttl or self.settings.redis_context_ttl_seconds
        ctx_key = self._project_context_key(project_id)
        meta_key = self._project_meta_key(project_id)

        meta: dict[str, Any] = metadata.copy() if metadata else {}
        meta["cached_at"] = time.time()
        if mtime is not None:
            meta["mtime"] = mtime

        self.set(ctx_key, context, ttl=effective_ttl)
        self.set(meta_key, json.dumps(meta), ttl=effective_ttl)
        return True

    def invalidate_project_context(self, project_id: str) -> None:
        """Invalidate all cached context and metadata entries for a project."""
        if not project_id:
            return
        pattern = f"chappie:project:{project_id}:*"
        self.delete_pattern(pattern)

    # --- Project thread transcripts (disk remains authoritative) ---

    def get_thread_turns(self, project_id: str, session_id: str, stamp: tuple[int, int]) -> list[dict[str, Any]] | None:
        raw = self.get(f"chappie:thread:{project_id}:{session_id}")
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            if data["stamp"] == list(stamp) and isinstance(data["turns"], list):
                return data["turns"]
        except (ValueError, KeyError, TypeError):
            pass
        return None

    def set_thread_turns(self, project_id: str, session_id: str, stamp: tuple[int, int], turns: list[dict[str, Any]]) -> None:
        self.set(
            f"chappie:thread:{project_id}:{session_id}",
            json.dumps({"stamp": stamp, "turns": turns}, ensure_ascii=False),
            ttl=self.settings.redis_context_ttl_seconds,
        )

    def invalidate_thread(self, project_id: str, session_id: str) -> None:
        self.delete(f"chappie:thread:{project_id}:{session_id}")

    def add_thread_usage(
        self,
        project_id: str,
        session_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        """Accumulate actual model usage per thread, independently of transcript cache eviction."""
        key = f"chappie:usage:{project_id}:{session_id}"
        increments = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        with self._lock:
            if self.is_redis_active:
                try:
                    with self._client.pipeline(transaction=True) as pipeline:
                        for field, amount in increments.items():
                            pipeline.hincrby(key, field, amount)
                        pipeline.execute()
                    return
                except Exception as exc:
                    logger.warning("Could not record token usage in Redis for %s: %s", key, exc)
            previous = json.loads(self._fallback.get(key) or "{}")
            self._fallback.set(
                key,
                json.dumps(
                    {
                        field: int(previous.get(field, 0)) + amount
                        for field, amount in increments.items()
                    }
                ),
            )

    def get_thread_usage(self, project_id: str, session_id: str) -> dict[str, int]:
        key = f"chappie:usage:{project_id}:{session_id}"
        if self.is_redis_active:
            try:
                return {field: int(value) for field, value in self._client.hgetall(key).items()}
            except Exception as exc:
                logger.warning("Could not read token usage from Redis for %s: %s", key, exc)
        return json.loads(self._fallback.get(key) or "{}")

    # --- Session State Caching ---

    def get_session_state(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve cached session state."""
        if not session_id:
            return None
        key = f"chappie:session:{session_id}:state"
        raw = self.get(key)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    def set_session_state(
        self,
        session_id: str,
        state: dict[str, Any],
        ttl: int = 7200,
    ) -> bool:
        """Store session state in cache."""
        if not session_id:
            return False
        key = f"chappie:session:{session_id}:state"
        return self.set(key, json.dumps(state), ttl=ttl)

    def delete_session_state(self, session_id: str) -> int:
        """Delete cached session state."""
        if not session_id:
            return 0
        return self.delete_pattern(f"chappie:session:{session_id}:*")

    # --- Live-Chat Context Persistence ---

    def _live_chat_context_key(self, context_id: str = "live_chat_general") -> str:
        return f"chappie:livechat:{context_id}"

    def get_live_chat_context(self, context_id: str = "live_chat_general") -> str | None:
        """Retrieve persistent Live-Chat general context from Redis."""
        return self.get(self._live_chat_context_key(context_id))

    def set_live_chat_context(
        self,
        context_id: str = "live_chat_general",
        context_json: str = "",
        ttl: int | None = None,
    ) -> bool:
        """Store persistent Live-Chat general context in Redis (AOF appendonly persistence)."""
        key = self._live_chat_context_key(context_id)
        return self.set(key, context_json, ttl=ttl)

    def health(self) -> dict[str, Any]:
        """Return Redis cache health metrics."""
        return {
            "redis_connected": self.is_redis_active,
            "mode": "redis" if self.is_redis_active else "memory_fallback",
            "host": self.settings.redis_host,
            "port": self.settings.redis_port,
            "ttl_seconds": self.settings.redis_context_ttl_seconds,
        }


_GLOBAL_CACHE_MANAGER: RedisCacheManager | None = None
_CACHE_LOCK = threading.RLock()


def get_redis_cache(settings: Settings | None = None) -> RedisCacheManager:
    """Get or create singleton instance of RedisCacheManager."""
    global _GLOBAL_CACHE_MANAGER
    with _CACHE_LOCK:
        if _GLOBAL_CACHE_MANAGER is None:
            _GLOBAL_CACHE_MANAGER = RedisCacheManager(settings=settings)
        return _GLOBAL_CACHE_MANAGER


def reset_redis_cache() -> None:
    """Reset global Redis cache singleton instance (primarily for tests)."""
    global _GLOBAL_CACHE_MANAGER
    with _CACHE_LOCK:
        if _GLOBAL_CACHE_MANAGER is not None:
            _GLOBAL_CACHE_MANAGER._fallback.clear()
        _GLOBAL_CACHE_MANAGER = None
