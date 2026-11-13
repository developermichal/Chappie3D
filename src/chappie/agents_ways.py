from __future__ import annotations

import json
import os
from typing import Any


class ContextStorage:
    def __init__(self, filename: str = "context.json") -> None:
        self.filename = filename

    def save_context(self, context: Any, session_id: str | None = None) -> bool:
        """Save context (optionally per session_id) to a file."""
        try:
            if session_id is not None:
                data = self.load_all() or {}
                if not isinstance(data, dict):
                    data = {}
                data[session_id] = context
                to_save = data
            else:
                to_save = context
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump(to_save, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"Error saving context: {e}")
            return False

    def load_context(self, session_id: str | None = None) -> Any:
        """Load context (optionally for a specific session_id) from a file."""
        try:
            if not os.path.exists(self.filename):
                return None
            with open(self.filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            if session_id is not None:
                return data.get(session_id) if isinstance(data, dict) else None
            return data
        except Exception as e:
            print(f"Error loading context: {e}")
            return None

    def load_all(self) -> Any:
        """Load the entire raw JSON data from storage file."""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, "r", encoding="utf-8") as f:
                    return json.load(f)
            return None
        except Exception as e:
            print(f"Error loading context: {e}")
            return None

    def clear(self, session_id: str | None = None) -> bool:
        """Clear context for a session or entire storage."""
        try:
            if not os.path.exists(self.filename):
                return True
            if session_id is None:
                os.remove(self.filename)
                return True
            data = self.load_all()
            if isinstance(data, dict) and session_id in data:
                del data[session_id]
                with open(self.filename, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"Error clearing context: {e}")
            return False



