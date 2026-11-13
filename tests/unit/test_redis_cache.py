"""Unit tests for RedisCacheManager and in-memory cache fallback."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from chappie.config import Settings
from chappie.redis_cache import InMemoryCacheFallback, RedisCacheManager, reset_redis_cache


class TestInMemoryCacheFallback:
    """Tests verifying behavior of thread-safe in-memory cache fallback."""

    def test_set_get_and_delete(self) -> None:
        cache = InMemoryCacheFallback()
        cache.set("k1", "v1")
        assert cache.get("k1") == "v1"
        assert cache.exists("k1") is True

        cache.delete("k1")
        assert cache.get("k1") is None
        assert cache.exists("k1") is False

    def test_ttl_expiration(self) -> None:
        cache = InMemoryCacheFallback()
        cache.set("short_lived", "data", ttl=1)
        assert cache.get("short_lived") == "data"
        time.sleep(1.1)
        assert cache.get("short_lived") is None

    def test_delete_prefix(self) -> None:
        cache = InMemoryCacheFallback()
        cache.set("project:1:ctx", "ctx1")
        cache.set("project:1:meta", "meta1")
        cache.set("project:2:ctx", "ctx2")

        deleted = cache.delete_prefix("project:1:")
        assert deleted == 2
        assert cache.get("project:1:ctx") is None
        assert cache.get("project:1:meta") is None
        assert cache.get("project:2:ctx") == "ctx2"


class TestRedisCacheManager:
    """Tests verifying RedisCacheManager operations, mtime disk validation, and degradation."""

    @pytest.fixture(autouse=True)
    def clean_cache(self) -> None:
        reset_redis_cache()
        yield
        reset_redis_cache()

    def test_project_context_caching_and_invalidation(self) -> None:
        settings = Settings(enable_redis_cache=False)
        cache = RedisCacheManager(settings=settings)

        project_id = "test_proj_1"
        context_doc = "# Context for Test Project\n## 1. Overview\nSample project content."

        cache.set_project_context(project_id, context_doc)
        retrieved = cache.get_project_context(project_id)
        assert retrieved == context_doc

        cache.invalidate_project_context(project_id)
        assert cache.get_project_context(project_id) is None

    def test_thread_usage_is_atomically_incremented_in_redis(self) -> None:
        cache = RedisCacheManager(settings=Settings(enable_redis_cache=False))
        client = MagicMock()
        cache._client = client
        cache._is_connected = True
        pipeline = client.pipeline.return_value.__enter__.return_value
        client.hgetall.return_value = {
            "prompt_tokens": "12", "completion_tokens": "3", "total_tokens": "15"
        }

        cache.add_thread_usage("project", "thread", 12, 3, 15)

        client.pipeline.assert_called_once_with(transaction=True)
        pipeline.hincrby.assert_has_calls([
            call("chappie:usage:project:thread", "prompt_tokens", 12),
            call("chappie:usage:project:thread", "completion_tokens", 3),
            call("chappie:usage:project:thread", "total_tokens", 15),
        ])
        pipeline.execute.assert_called_once_with()
        assert cache.get_thread_usage("project", "thread") == {
            "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15
        }

    def test_mtime_disk_freshness_invalidation(self, tmp_path: Path) -> None:
        settings = Settings(enable_redis_cache=False)
        cache = RedisCacheManager(settings=settings)

        chappie_file = tmp_path / ".chappie"
        chappie_file.write_text("# Initial Context Content", encoding="utf-8")

        initial_mtime = chappie_file.stat().st_mtime
        cache.set_project_context(
            project_id="proj_disk",
            context="# Initial Context Content",
            mtime=initial_mtime,
        )

        # Immediate retrieval matches
        assert cache.get_project_context("proj_disk", root_path=tmp_path) == "# Initial Context Content"

        # Simulate external modification of .chappie on disk
        time.sleep(0.05)
        chappie_file.write_text("# Externally Modified Context", encoding="utf-8")
        # Ensure timestamp is strictly higher
        new_time = initial_mtime + 2.0
        import os
        os.utime(chappie_file, (new_time, new_time))

        # get_project_context detects stale cache via mtime check and invalidates
        stale_check = cache.get_project_context("proj_disk", root_path=tmp_path)
        assert stale_check is None

    def test_session_state_caching(self) -> None:
        settings = Settings(enable_redis_cache=False)
        cache = RedisCacheManager(settings=settings)

        session_id = "sess_abc123"
        state = {"user": "tester", "turn_count": 3, "active": True}

        cache.set_session_state(session_id, state)
        fetched = cache.get_session_state(session_id)
        assert fetched == state

        cache.delete_session_state(session_id)
        assert cache.get_session_state(session_id) is None

    def test_active_project_and_inactive_eviction(self) -> None:
        settings = Settings(enable_redis_cache=False)
        cache = RedisCacheManager(settings=settings)

        # Set project 1 as active
        cache.set_active_project("proj_1", "# Context 1")
        assert cache.get_active_project_id() == "proj_1"
        assert cache.get_project_context("proj_1") == "# Context 1"

        # Switch active project to project 2
        cache.set_active_project("proj_2", "# Context 2", evict_others=True)
        assert cache.get_active_project_id() == "proj_2"
        assert cache.get_project_context("proj_2") == "# Context 2"
        # Project 1 was evicted to ensure only currently active context resides in Redis
        assert cache.get_project_context("proj_1") is None

    def test_live_chat_context_persistence(self) -> None:
        settings = Settings(enable_redis_cache=False)
        cache = RedisCacheManager(settings=settings)

        context_data = '{"id": "live_chat_general", "title": "Hardware Notes"}'
        cache.set_live_chat_context("live_chat_general", context_data)

        retrieved = cache.get_live_chat_context("live_chat_general")
        assert retrieved == context_data

    def test_health_report(self) -> None:
        settings = Settings(enable_redis_cache=False)
        cache = RedisCacheManager(settings=settings)
        health = cache.health()

        assert health["mode"] in {"redis", "memory_fallback"}
        assert "ttl_seconds" in health
