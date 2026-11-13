"""General Chat & Knowledge/RAG Service.

Manages session-scoped standalone conversational interactions, ChromaDB vector RAG retrieval,
knowledge profiles, PyPI lookups, and web search, decoupled from project directories and live video streams.
"""

from __future__ import annotations

import logging
from typing import Any

from chappie.knowledge import KnowledgeBase
from chappie.models import (
    BookProfile,
    BookProfileCreate,
    BookProfileUpdate,
    DocumentSummary,
    RetrievedChunk,
)
from chappie.profiles import ProfileStorage
from chappie.pypi import PypiEngine
from chappie.redis_cache import RedisCacheManager, get_redis_cache
from chappie.web_search import WebSearchEngine

logger = logging.getLogger("chappie.services.general_chat")


class GeneralChatService:
    """Domain service managing general standalone chat, session-scoped memory, and knowledge retrieval."""

    def __init__(
        self,
        knowledge_manager: KnowledgeBase,
        profile_storage: ProfileStorage,
        pypi_engine: PypiEngine | None = None,
        web_search_engine: WebSearchEngine | None = None,
        redis_cache: RedisCacheManager | None = None,
    ) -> None:
        self.knowledge = knowledge_manager
        self.profiles = profile_storage
        self.pypi = pypi_engine
        self.web_search = web_search_engine
        self.cache = redis_cache or get_redis_cache()

    # --- Session State Caching ---

    def get_session_state(self, session_id: str) -> dict[str, Any] | None:
        """Retrieves cached session state from Redis or memory."""
        return self.cache.get_session_state(session_id)

    def set_session_state(self, session_id: str, state: dict[str, Any], ttl: int = 7200) -> bool:
        """Saves session state to Redis with TTL."""
        return self.cache.set_session_state(session_id, state, ttl=ttl)

    def clear_session_state(self, session_id: str) -> int:
        """Clears session state upon session reset."""
        return self.cache.delete_session_state(session_id)

    # --- Knowledge Base & RAG Operations ---

    async def search_knowledge(
        self,
        query: str,
        document_ids: list[str] | None = None,
        limit: int = 5,
    ) -> list[RetrievedChunk]:
        """Searches the vector knowledge base (ChromaDB + Embeddings)."""
        return await self.knowledge.search(
            query=query,
            document_ids=document_ids or [],
            limit=limit,
        )

    async def list_documents(self) -> list[DocumentSummary]:
        """Lists all indexed documents in the vector database."""
        return await self.knowledge.list_documents()

    async def delete_document(self, document_id: str) -> bool:
        """Deletes a document from the vector database and cleans profiles."""
        await self.knowledge.delete_document(document_id)
        self.profiles.remove_document_from_all_profiles(document_id)
        return True

    # --- Knowledge Profiles ---

    def list_profiles(self) -> list[BookProfile]:
        """Lists available knowledge profiles."""
        return self.profiles.list_profiles()

    def get_profile(self, profile_id: str) -> BookProfile | None:
        """Retrieves a specific profile by ID."""
        return self.profiles.get_profile(profile_id)

    def create_profile(self, payload: BookProfileCreate) -> BookProfile:
        """Creates a new knowledge profile."""
        return self.profiles.create_profile(payload)

    def update_profile(self, profile_id: str, payload: BookProfileUpdate) -> BookProfile | None:
        """Updates an existing knowledge profile."""
        return self.profiles.update_profile(profile_id, payload)

    def delete_profile(self, profile_id: str) -> bool:
        """Deletes a knowledge profile."""
        return self.profiles.delete_profile(profile_id)

    # --- External Lookups (PyPI & Web Search) ---

    async def lookup_pypi_package(self, package_name: str) -> Any:
        """Fetches metadata for a package from PyPI if enabled."""
        if not self.pypi:
            raise RuntimeError("PyPI engine is not enabled or initialized.")
        return await self.pypi.fetch_package_info(package_name)

    async def search_web(self, query: str, max_results: int = 3) -> Any:
        """Executes a web search query if enabled."""
        if not self.web_search:
            raise RuntimeError("Web search engine is not enabled or initialized.")
        results, _ = await self.web_search.search(query, max_results=max_results)
        return results
