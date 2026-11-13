from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from chappie.electronics_standard import (
    ELECTRONICS_PROFILE_DESCRIPTION,
    ELECTRONICS_PROFILE_ID,
    ELECTRONICS_PROFILE_NAME,
)
from chappie.models import BookProfile, BookProfileCreate, BookProfileUpdate

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_ID = "default"
DEFAULT_PROFILE_NAME = "default"
DEFAULT_PROFILE_DESCRIPTION = (
    "Domyślny profil wiedzy zawierający wszystkie książki i dokumenty w bazie."
)


class ProfileStorage:
    """Persistent storage for knowledge profiles using JSON file."""

    def __init__(self, file_path: str = "data/profiles.json") -> None:
        self.file_path = Path(file_path)
        self._lock = threading.RLock()
        self._ensure_storage_dir()

    def _ensure_storage_dir(self) -> None:
        try:
            if self.file_path.parent and not self.file_path.parent.exists():
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not create storage directory '%s': %s", self.file_path.parent, exc
            )

    def ensure_default_profiles(self) -> None:
        with self._lock:
            profiles = self._load_data()
            changed = False

            if DEFAULT_PROFILE_ID not in profiles:
                has_default = any(
                    p.name.strip().lower() == DEFAULT_PROFILE_NAME.lower()
                    for p in profiles.values()
                )
                if not has_default:
                    now_iso = "2026-10-25T00:00:00Z"
                    default_profile = BookProfile(
                        id=DEFAULT_PROFILE_ID,
                        name=DEFAULT_PROFILE_NAME,
                        description=DEFAULT_PROFILE_DESCRIPTION,
                        document_ids=[],
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                    profiles[DEFAULT_PROFILE_ID] = default_profile
                    changed = True

            if ELECTRONICS_PROFILE_ID not in profiles:
                has_electronics = any(
                    p.name.strip().lower() == ELECTRONICS_PROFILE_NAME.lower()
                    for p in profiles.values()
                )
                if not has_electronics:
                    now_iso = "2026-10-25T00:00:00Z"
                    electronics_profile = BookProfile(
                        id=ELECTRONICS_PROFILE_ID,
                        name=ELECTRONICS_PROFILE_NAME,
                        description=ELECTRONICS_PROFILE_DESCRIPTION,
                        document_ids=[],
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                    profiles[ELECTRONICS_PROFILE_ID] = electronics_profile
                    changed = True

            if changed:
                try:
                    self._save_data(profiles)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed saving initial default profiles: %s", exc)

    def _load_data(self) -> dict[str, BookProfile]:
        try:
            if not self.file_path.exists():
                return {}
        except OSError as exc:
            logger.warning("Failed to access profiles path %s: %s", self.file_path, exc)
            return {}
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                logger.warning(
                    "Profiles file %s does not contain a list, resetting", self.file_path
                )
                return {}
            profiles: dict[str, BookProfile] = {}
            for item in raw:
                try:
                    profile = BookProfile.model_validate(item)
                    profiles[profile.id] = profile
                except Exception as err:  # noqa: BLE001
                    logger.warning("Skipping invalid profile entry in %s: %s", self.file_path, err)
            return profiles
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to read profiles from %s: %s", self.file_path, exc)
            return {}

    def _save_data(self, profiles: dict[str, BookProfile]) -> None:
        self._ensure_storage_dir()
        data = [profile.model_dump() for profile in profiles.values()]
        # Write to temporary file first then replace atomically
        dir_path = self.file_path.parent if str(self.file_path.parent) != "" else Path(".")
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=dir_path, prefix="profiles_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.file_path)
        except Exception as exc:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            logger.error("Failed to save profiles to %s: %s", self.file_path, exc)
            raise

    def list_profiles(self) -> list[BookProfile]:
        with self._lock:
            profiles = self._load_data()
            return list(profiles.values())

    def get_profile(self, profile_id: str) -> BookProfile | None:
        with self._lock:
            profiles = self._load_data()
            prof = profiles.get(profile_id)
            if prof is not None:
                return prof
            if profile_id == DEFAULT_PROFILE_ID:
                return BookProfile(
                    id=DEFAULT_PROFILE_ID,
                    name=DEFAULT_PROFILE_NAME,
                    description=DEFAULT_PROFILE_DESCRIPTION,
                    document_ids=[],
                    created_at="2026-10-25T00:00:00Z",
                    updated_at="2026-10-25T00:00:00Z",
                )
            if profile_id == ELECTRONICS_PROFILE_ID:
                return BookProfile(
                    id=ELECTRONICS_PROFILE_ID,
                    name=ELECTRONICS_PROFILE_NAME,
                    description=ELECTRONICS_PROFILE_DESCRIPTION,
                    document_ids=[],
                    created_at="2026-10-25T00:00:00Z",
                    updated_at="2026-10-25T00:00:00Z",
                )
            return None

    def create_profile(self, payload: BookProfileCreate) -> BookProfile:
        with self._lock:
            profiles = self._load_data()
            name_lower = payload.name.strip().lower()
            for existing in profiles.values():
                if existing.name.strip().lower() == name_lower:
                    raise ValueError(f"Profile with name '{payload.name}' already exists")

            now_iso = datetime.now(UTC).isoformat()
            profile_id = uuid4().hex[:12]
            # Deduplicate document_ids preserving order
            seen: set[str] = set()
            clean_doc_ids: list[str] = []
            for doc_id in payload.document_ids:
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    clean_doc_ids.append(doc_id)

            profile = BookProfile(
                id=profile_id,
                name=payload.name.strip(),
                description=payload.description.strip(),
                document_ids=clean_doc_ids,
                created_at=now_iso,
                updated_at=now_iso,
            )
            profiles[profile.id] = profile
            self._save_data(profiles)
            return profile

    def update_profile(self, profile_id: str, payload: BookProfileUpdate) -> BookProfile | None:
        with self._lock:
            profiles = self._load_data()
            existing = profiles.get(profile_id)
            if existing is None:
                return None

            new_name = existing.name
            if payload.name is not None:
                candidate_name = payload.name.strip()
                name_lower = candidate_name.lower()
                for other_id, other in profiles.items():
                    if other_id != profile_id and other.name.strip().lower() == name_lower:
                        raise ValueError(f"Profile with name '{candidate_name}' already exists")
                new_name = candidate_name

            new_description = (
                payload.description.strip()
                if payload.description is not None
                else existing.description
            )

            new_doc_ids = existing.document_ids
            if payload.document_ids is not None:
                seen: set[str] = set()
                clean_doc_ids: list[str] = []
                for doc_id in payload.document_ids:
                    if doc_id and doc_id not in seen:
                        seen.add(doc_id)
                        clean_doc_ids.append(doc_id)
                new_doc_ids = clean_doc_ids

            now_iso = datetime.now(UTC).isoformat()
            updated = BookProfile(
                id=existing.id,
                name=new_name,
                description=new_description,
                document_ids=new_doc_ids,
                created_at=existing.created_at,
                updated_at=now_iso,
            )
            profiles[profile_id] = updated
            self._save_data(profiles)
            return updated

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            profiles = self._load_data()
            if profile_id not in profiles:
                return False
            del profiles[profile_id]
            self._save_data(profiles)
            return True

    def remove_document_from_all_profiles(self, document_id: str) -> int:
        with self._lock:
            profiles = self._load_data()
            modified_count = 0
            now_iso = datetime.now(UTC).isoformat()
            for p_id, profile in list(profiles.items()):
                if document_id in profile.document_ids:
                    updated_docs = [d for d in profile.document_ids if d != document_id]
                    profiles[p_id] = BookProfile(
                        id=profile.id,
                        name=profile.name,
                        description=profile.description,
                        document_ids=updated_docs,
                        created_at=profile.created_at,
                        updated_at=now_iso,
                    )
                    modified_count += 1
            if modified_count > 0:
                self._save_data(profiles)
            return modified_count
