"""Web Search Engine for real-time external knowledge retrieval and enrichment."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import googlesearch
import httpx
from bs4 import BeautifulSoup

from chappie.config import Settings, get_settings
from chappie.models import WebSearchAuditRecord, WebSearchResult

logger = logging.getLogger(__name__)

# Patterns matching sensitive data: file paths, tokens, API keys, env assignments
_SENSITIVE_PATTERNS = [
    # Windows paths (e.g. C:\workspace\file.py, D:/data/sec.txt)
    re.compile(r"[A-Za-z]:[\\/][^\s\"']+", re.IGNORECASE),
    # Common absolute POSIX directory paths
    re.compile(
        r"/(?:workspace|home|etc|var|tmp|usr|opt|root|data|app|private|dev|Users|bin|sbin)[^\s\"']*",
        re.IGNORECASE,
    ),
    # Relative path notations (e.g. ../secret.env, ./configs/token.json)
    re.compile(r"(?:\.{1,2}[\\/][^\s\"']+)", re.IGNORECASE),
    # Secrets / keys / password assignments
    re.compile(
        r"(?:[A-Za-z0-9_]*(?:SECRET|KEY|TOKEN|PASSWORD|PASS|AUTH|CREDENTIAL|PWD)[A-Za-z0-9_]*\s*=\s*[^\s]+)",
        re.IGNORECASE,
    ),
    # Shell / env variable patterns ($VAR, ${VAR}, %VAR%)
    re.compile(
        r"(?:\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\}|%[A-Za-z_][A-Za-z0-9_]*%)"
    ),
    # Known API key / auth bearer formats
    re.compile(
        r"\b(?:sk-[a-zA-Z0-9_-]{12,}|ghp_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{20,}|xox[baprs]-[a-zA-Z0-9_-]{10,}|Bearer\s+[a-zA-Z0-9_\-\.]{15,})\b",
        re.IGNORECASE,
    ),
]


class WebSearchEngine:
    """Executes sanitized real-time web searches and extracts bounded content excerpts."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def sanitize_query(self, query: str) -> str:
        """Strip file system paths, credentials, and env tokens from query."""
        if not query:
            return ""
        cleaned = query
        for pattern in _SENSITIVE_PATTERNS:
            cleaned = pattern.sub(" ", cleaned)
        return " ".join(cleaned.split()).strip()

    def _sync_google_search(self, term: str, max_results: int, timeout: float) -> list[Any]:
        """Execute synchronous Google search generator."""
        results: list[Any] = []
        for item in googlesearch.search(
            term,
            num_results=max_results,
            advanced=True,
            timeout=timeout,
        ):
            results.append(item)
            if len(results) >= max_results:
                break
        return results

    async def _fetch_excerpt(
        self, client: httpx.AsyncClient, url: str, timeout: float
    ) -> str:
        """Fetch page HTML and extract clean text excerpt."""
        try:
            resp = await client.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
                follow_redirects=True,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                return text[:2000]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed fetching excerpt for %s: %s", url, exc)
        return ""

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        timeout: float | None = None,
    ) -> tuple[list[WebSearchResult], WebSearchAuditRecord]:
        """Execute query, fetch excerpts, and return structured results with audit record."""
        now_iso = datetime.now(UTC).isoformat()
        sanitized = self.sanitize_query(query)
        effective_max = max_results or self.settings.web_search_max_results
        effective_timeout = timeout or self.settings.web_search_timeout_seconds

        if not sanitized:
            return [], WebSearchAuditRecord(
                query=query,
                timestamp=now_iso,
                status="empty",
                results_count=0,
                urls_contacted=[],
                error_message="Query was empty after sanitization",
            )

        if not self.settings.enable_web_search:
            return [], WebSearchAuditRecord(
                query=sanitized,
                timestamp=now_iso,
                status="disabled",
                results_count=0,
                urls_contacted=[],
                error_message="Web search is disabled in settings",
            )

        urls_contacted: list[str] = []
        try:
            raw_items = await asyncio.to_thread(
                self._sync_google_search,
                sanitized,
                effective_max,
                effective_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Web search engine connection failed: %s", exc)
            return [], WebSearchAuditRecord(
                query=sanitized,
                timestamp=now_iso,
                status="network_unreachable",
                results_count=0,
                urls_contacted=[],
                error_message=str(exc),
            )

        if not raw_items:
            return [], WebSearchAuditRecord(
                query=sanitized,
                timestamp=now_iso,
                status="empty",
                results_count=0,
                urls_contacted=[],
                error_message=None,
            )

        results: list[WebSearchResult] = []
        fetch_timeout = min(effective_timeout, 4.0)

        try:
            async with httpx.AsyncClient(verify=False) as client:
                fetch_tasks = []
                for item in raw_items:
                    url = getattr(item, "url", str(item))
                    urls_contacted.append(url)
                    fetch_tasks.append(self._fetch_excerpt(client, url, fetch_timeout))

                excerpts = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                for item, excerpt_res in zip(raw_items, excerpts, strict=False):
                    url = getattr(item, "url", str(item))
                    title = getattr(item, "title", "")
                    snippet = getattr(item, "description", "")
                    excerpt = excerpt_res if isinstance(excerpt_res, str) else ""
                    results.append(
                        WebSearchResult(
                            title=title or url,
                            url=url,
                            snippet=snippet or excerpt[:300],
                            content_excerpt=excerpt or snippet,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed during excerpt fetching: %s", exc)

        audit = WebSearchAuditRecord(
            query=sanitized,
            timestamp=now_iso,
            status="success" if results else "empty",
            results_count=len(results),
            urls_contacted=urls_contacted,
        )
        return results, audit
