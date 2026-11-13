"""PyPI package inspection engine with permission checks and sanitization."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

from chappie.config import Settings, get_settings
from chappie.models import PypiAuditRecord, PypiPackageInfo

logger = logging.getLogger(__name__)

_SAFE_PACKAGE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

# Regex to detect candidate python packages in user queries
_PACKAGE_CANDIDATE_PATTERNS = [
    re.compile(r"\bpypi:\s*([a-zA-Z0-9_\-\.]+)\b", re.IGNORECASE),
    re.compile(r"\bpip\s+install\s+([a-zA-Z0-9_\-\.]+)\b", re.IGNORECASE),
    re.compile(r"\b(?:package|library|dependency)\s+([a-zA-Z0-9_\-\.]+)\b", re.IGNORECASE),
    re.compile(r"\bpypi\s+package\s+([a-zA-Z0-9_\-\.]+)\b", re.IGNORECASE),
]


class PypiEngine:
    """Queries official PyPI JSON APIs to inspect package metadata with strict security constraints."""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = http_client

    def sanitize_package_name(self, name: str) -> str:
        """Sanitizes and validates a package name."""
        clean = name.strip()
        if not clean or len(clean) > 120:
            return ""
        if not _SAFE_PACKAGE_NAME_PATTERN.match(clean):
            return ""
        if clean in (".", ".."):
            return ""
        return clean

    def extract_package_candidates(self, text: str) -> list[str]:
        """Extracts likely PyPI package names from query or prompt text."""
        candidates: list[str] = []
        seen: set[str] = set()

        for pattern in _PACKAGE_CANDIDATE_PATTERNS:
            for match in pattern.finditer(text):
                pkg = self.sanitize_package_name(match.group(1))
                if pkg and pkg.lower() not in seen:
                    seen.add(pkg.lower())
                    candidates.append(pkg)

        return candidates

    async def fetch_package_info(
        self,
        package_name: str,
        timeout: float | None = None,
    ) -> PypiPackageInfo | None:
        """Fetches metadata for a single package from PyPI JSON API."""
        clean_name = self.sanitize_package_name(package_name)
        if not clean_name:
            return None

        url = f"{self.settings.pypi_base_url.rstrip('/')}/{clean_name}/json"
        req_timeout = timeout or self.settings.pypi_timeout_seconds

        client = self._client or httpx.AsyncClient(timeout=req_timeout)
        should_close = self._client is None

        try:
            resp = await client.get(
                url,
                headers={"User-Agent": "Chappie-Agent/1.0 (https://github.com/chappie)"},
                follow_redirects=True,
            )
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                logger.warning("PyPI returned status %d for %s", resp.status_code, clean_name)
                return None

            data = resp.json()
            info = data.get("info", {})
            releases_raw = data.get("releases", {})
            release_versions = (
                list(releases_raw.keys())[-10:] if isinstance(releases_raw, dict) else []
            )

            requires_dist = info.get("requires_dist") or []
            if not isinstance(requires_dist, list):
                requires_dist = []

            return PypiPackageInfo(
                name=info.get("name") or clean_name,
                version=info.get("version") or "",
                summary=info.get("summary") or "",
                home_page=info.get("home_page") or info.get("project_url") or "",
                author=info.get("author") or "",
                license=info.get("license") or "",
                requires_python=info.get("requires_python") or "",
                requires_dist=requires_dist[:30],
                releases=release_versions,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed fetching PyPI info for '%s': %s", clean_name, exc)
            return None
        finally:
            if should_close:
                await client.aclose()

    async def inspect_packages(
        self,
        package_names: list[str],
        timeout: float | None = None,
    ) -> tuple[list[PypiPackageInfo], PypiAuditRecord]:
        """Inspects multiple packages and produces a structured audit record."""
        now_iso = datetime.now(UTC).isoformat()
        results: list[PypiPackageInfo] = []
        cleaned_targets: list[str] = []

        for raw_name in package_names:
            clean = self.sanitize_package_name(raw_name)
            if clean and clean not in cleaned_targets:
                cleaned_targets.append(clean)

        if not cleaned_targets:
            audit = PypiAuditRecord(
                query=",".join(package_names),
                timestamp=now_iso,
                status="empty",
                packages_found=[],
                error_message="No valid package names provided",
            )
            return results, audit

        for pkg in cleaned_targets[:5]:  # Bounded to max 5 packages per lookup
            info = await self.fetch_package_info(pkg, timeout=timeout)
            if info is not None:
                results.append(info)

        found_names = [r.name for r in results]
        status = "success" if results else "not_found"
        audit = PypiAuditRecord(
            query=",".join(cleaned_targets),
            timestamp=now_iso,
            status=status,
            packages_found=found_names,
            error_message=None if results else "Packages not found on PyPI",
        )
        return results, audit
