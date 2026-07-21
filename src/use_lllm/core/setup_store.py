"""Versioned first-run setup state stored beside the local application data."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SETUP_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SetupStore:
    def __init__(self, base_directory: Path) -> None:
        self.path = base_directory / "setup.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"completed_version": 0}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"completed_version": 0}
        return value if isinstance(value, dict) else {"completed_version": 0}

    def is_complete(self) -> bool:
        return int(self.load().get("completed_version", 0)) >= SETUP_VERSION

    def mark_complete(self, *, endpoint: str, model: str) -> dict[str, Any]:
        payload = {
            "completed_version": SETUP_VERSION,
            "endpoint": endpoint,
            "model": model,
            "completed_at": _now(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return payload
