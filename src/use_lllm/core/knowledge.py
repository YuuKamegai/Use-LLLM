"""Local document library and lightweight full-text retrieval."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".xml",
    ".log",
    ".ini",
    ".toml",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeStore:
    def __init__(self, base_directory: Path) -> None:
        self.root = base_directory / "knowledge"
        self.database_path = self.root / "knowledge.sqlite3"
        self.files = self.root / "files"
        self.root.mkdir(parents=True, exist_ok=True)
        self.files.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, mime_type TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL,
                    text_content TEXT NOT NULL, origin TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
                    source_id UNINDEXED, name, text_content
                );
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _text(name: str, data: bytes, mime_type: str) -> str:
        if Path(name).suffix.lower() in TEXT_EXTENSIONS or mime_type.startswith("text/"):
            return data.decode("utf-8", errors="replace")
        return ""

    def add_bytes(
        self,
        name: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
        *,
        origin: str = "upload",
    ) -> dict[str, Any]:
        clean_name = Path(name).name.strip()
        if not clean_name:
            raise ValueError("ファイル名が空です。")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("ファイルは5MB以下にしてください。")
        source_id = uuid.uuid4().hex
        digest = hashlib.sha256(data).hexdigest()
        target = self.files / f"{source_id}{Path(clean_name).suffix.lower()}"
        target.write_bytes(data)
        text = self._text(clean_name, data, mime_type)
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    clean_name,
                    mime_type,
                    len(data),
                    digest,
                    str(target),
                    text,
                    origin,
                    _now(),
                ),
            )
            connection.execute(
                "INSERT INTO source_fts(source_id, name, text_content) VALUES (?, ?, ?)",
                (source_id, clean_name, text),
            )
        return self.get(source_id)

    def list(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, name, mime_type, size, sha256, origin, created_at, "
                "length(text_content) AS text_length FROM sources ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, source_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        return dict(row)

    def delete(self, source_id: str) -> None:
        item = self.get(source_id)
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM source_fts WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM sources WHERE id=?", (source_id,))
        Path(item["path"]).unlink(missing_ok=True)

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        text = query.strip()
        if not text:
            return self.list()[:limit]
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT s.id, s.name, s.mime_type, s.size, s.origin, "
                "snippet(source_fts, 2, '[', ']', ' … ', 24) AS excerpt "
                "FROM source_fts JOIN sources s ON s.id=source_fts.source_id "
                "WHERE source_fts MATCH ? ORDER BY rank LIMIT ?",
                (f'"{text.replace(chr(34), chr(34) * 2)}"', max(1, min(limit, 50))),
            ).fetchall()
        return [dict(row) for row in rows]

    def attachment_context(self, source_ids: list[str], *, max_chars: int = 16000) -> str:
        parts: list[str] = []
        remaining = max_chars
        for source_id in source_ids:
            item = self.get(source_id)
            text = str(item["text_content"])
            excerpt = text[:remaining]
            parts.append(f"### {item['name']}\n{excerpt or '[テキスト抽出対象外のファイル]'}")
            remaining -= len(excerpt)
            if remaining <= 0:
                break
        return "\n\n".join(parts)
