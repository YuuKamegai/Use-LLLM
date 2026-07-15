"""SQLite session/event store and content-addressed local artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_tool_result(text: str, limit: int = 2000) -> str:
    """Keep a deterministic prompt-safe preview while the raw result stays in messages."""

    value = text.strip()
    if len(value) <= limit:
        return value
    head = max(1, limit - 320)
    return (
        value[:head] + f"\n\n...[tool result compacted: {len(value)} chars]...\n\n" + value[-240:]
    )


def default_data_directory() -> Path:
    configured = os.environ.get("USE_LLLM_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Use-LLLM"


class SessionNotFound(KeyError):
    pass


class SessionStore:
    def __init__(self, base_directory: Path | None = None) -> None:
        self.base_directory = (base_directory or default_data_directory()).resolve()
        self.artifact_root = self.base_directory / "sessions"
        self.database_path = self.base_directory / "use-lllm.sqlite3"
        self._lock = threading.RLock()
        self.base_directory.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            objective TEXT NOT NULL DEFAULT '',
            dataset_path TEXT,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            surface TEXT NOT NULL DEFAULT 'lipidomics'
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            parent_event_id INTEGER REFERENCES events(id),
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            first_message_id INTEGER NOT NULL,
            last_message_id INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            estimated_tokens INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, last_message_id)
        );
        CREATE TABLE IF NOT EXISTS tool_invocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            server_name TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            status TEXT NOT NULL,
            is_error INTEGER,
            result_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
            result_digest TEXT,
            result_summary TEXT NOT NULL DEFAULT '',
            connection_generation INTEGER NOT NULL DEFAULT 0,
            replay_of_id INTEGER REFERENCES tool_invocations(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS file_bindings (
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            polarity TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            fingerprint TEXT NOT NULL,
            fingerprint_mode TEXT NOT NULL,
            PRIMARY KEY(session_id, polarity, kind)
        );
        CREATE TABLE IF NOT EXISTS ontology_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            original_token TEXT NOT NULL,
            factor TEXT NOT NULL,
            canonical_value TEXT NOT NULL,
            display_ja TEXT NOT NULL,
            scope TEXT NOT NULL,
            confirmed INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, position, original_token)
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            approved INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_summaries_session
            ON conversation_summaries(session_id, last_message_id);
        CREATE INDEX IF NOT EXISTS idx_tool_invocations_session
            ON tool_invocations(session_id, id);
        """
        with self._lock, self._connection() as connection:
            connection.executescript(schema)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "surface" not in columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN surface TEXT NOT NULL DEFAULT 'lipidomics'"
                )
            interrupted = connection.execute(
                "SELECT DISTINCT session_id FROM events WHERE status='running'"
            ).fetchall()
            if interrupted:
                now = utc_now()
                connection.execute(
                    "UPDATE events SET status='interrupted', completed_at=? WHERE status='running'",
                    (now,),
                )
                connection.executemany(
                    "UPDATE sessions SET status='error', updated_at=? WHERE id=?",
                    ((now, row["session_id"]) for row in interrupted),
                )
            connection.execute(
                "UPDATE tool_invocations SET status='interrupted', completed_at=? "
                "WHERE status='running'",
                (utc_now(),),
            )

    @staticmethod
    def _decode_session(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def create_session(
        self,
        title: str = "新しい解析",
        objective: str = "",
        *,
        surface: str = "lipidomics",
    ) -> dict[str, Any]:
        if surface not in {"lipidomics", "general"}:
            raise ValueError(f"未対応のsession surfaceです: {surface}")
        session_id = str(uuid.uuid4())
        now = utc_now()
        if surface == "general":
            state = {
                "phase": "chat",
                "network_mode": "offline",
                "pending_approval": None,
            }
        else:
            state = {
                "phase": "data_selection",
                "polarities": {
                    "NEG": {"status": "waiting"},
                    "POS": {"status": "waiting"},
                },
                "mapping_confirmed": False,
                "contrast": None,
                "current_result_id": None,
            }
        with self._lock, self._connection() as connection:
            connection.execute(
                """INSERT INTO sessions
                (id, title, status, objective, dataset_path, state_json,
                 created_at, updated_at, surface)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    session_id,
                    title,
                    "new",
                    objective,
                    json.dumps(state),
                    now,
                    now,
                    surface,
                ),
            )
        (self.artifact_root / session_id).mkdir(parents=True, exist_ok=True)
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        result = self._decode_session(row)
        result["events"] = self.list_events(session_id)
        result["messages"] = self.list_messages(session_id)
        result["tool_invocations"] = self.list_tool_invocations(session_id)
        result["memory_summary"] = self.latest_conversation_summary(session_id)
        return result

    def list_sessions(self, surface: str | None = "lipidomics") -> list[dict[str, Any]]:
        with self._connection() as connection:
            if surface is None:
                rows = connection.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sessions WHERE surface=? ORDER BY updated_at DESC",
                    (surface,),
                ).fetchall()
        return [self._decode_session(row) for row in rows]

    def delete_session(self, session_id: str) -> dict[str, Any]:
        """Delete one persisted session and its session-scoped artifacts."""

        self.get_session(session_id)
        artifact_root = self.artifact_root.resolve()
        artifact_directory = (artifact_root / session_id).resolve()
        try:
            artifact_directory.relative_to(artifact_root)
        except ValueError as exc:
            raise ValueError("不正なセッション成果物パスです。") from exc
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            if cursor.rowcount != 1:
                raise SessionNotFound(session_id)
        artifacts_deleted = artifact_directory.is_dir()
        if artifacts_deleted:
            shutil.rmtree(artifact_directory)
        return {"deleted": True, "session_id": session_id, "artifacts_deleted": artifacts_deleted}

    def update_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        dataset_path: str | None = None,
        state_patch: dict[str, Any] | None = None,
        title: str | None = None,
        objective: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_session(session_id)
        state = current["state"]
        if state_patch:
            state = _deep_merge(state, state_patch)
        values = {
            "status": status if status is not None else current["status"],
            "dataset_path": dataset_path if dataset_path is not None else current["dataset_path"],
            "state_json": json.dumps(state, ensure_ascii=False),
            "title": title if title is not None else current["title"],
            "objective": objective if objective is not None else current["objective"],
            "updated_at": utc_now(),
            "id": session_id,
        }
        with self._lock, self._connection() as connection:
            connection.execute(
                """UPDATE sessions SET status=:status, dataset_path=:dataset_path,
                state_json=:state_json, title=:title, objective=:objective,
                updated_at=:updated_at WHERE id=:id""",
                values,
            )
        return self.get_session(session_id)

    def append_event(
        self,
        session_id: str,
        kind: str,
        payload: dict[str, Any],
        status: str = "complete",
        parent_event_id: int | None = None,
    ) -> int:
        now = utc_now()
        completed = now if status in {"complete", "failed", "cancelled", "skipped"} else None
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO events(session_id, kind, status, payload_json,
                parent_event_id, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    kind,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    parent_event_id,
                    now,
                    completed,
                ),
            )
            event_id = int(cursor.lastrowid)
            connection.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
        return event_id

    def complete_event(self, event_id: int, status: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE events SET status=?, payload_json=?, completed_at=? WHERE id=?",
                (status, json.dumps(payload, ensure_ascii=False), utc_now(), event_id),
            )

    def list_events(self, session_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id",
                (session_id, after_id),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events

    def get_event(self, session_id: str, event_id: int) -> dict[str, Any]:
        events = [item for item in self.list_events(session_id) if item["id"] == event_id]
        if not events:
            raise KeyError(f"event:{event_id}")
        return events[0]

    def add_message(
        self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> int:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO messages(session_id, role, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
        messages = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            messages.append(item)
        return messages

    def add_conversation_summary(
        self,
        session_id: str,
        first_message_id: int,
        last_message_id: int,
        summary: dict[str, Any],
        *,
        source_hash: str,
        model: str,
        estimated_tokens: int,
    ) -> int:
        if first_message_id <= 0 or last_message_id < first_message_id:
            raise ValueError("要約対象のmessage ID範囲が不正です。")
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT OR REPLACE INTO conversation_summaries
                (session_id, first_message_id, last_message_id, summary_json,
                 source_hash, model, estimated_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    first_message_id,
                    last_message_id,
                    json.dumps(summary, ensure_ascii=False),
                    source_hash,
                    model,
                    max(0, int(estimated_tokens)),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_conversation_summaries(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_summaries WHERE session_id=? ORDER BY last_message_id",
                (session_id,),
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["summary"] = json.loads(item.pop("summary_json"))
            summaries.append(item)
        return summaries

    def latest_conversation_summary(self, session_id: str) -> dict[str, Any] | None:
        summaries = self.list_conversation_summaries(session_id)
        return summaries[-1] if summaries else None

    def start_tool_invocation(
        self,
        session_id: str,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        connection_generation: int = 0,
        replay_of_id: int | None = None,
    ) -> int:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO tool_invocations
                (session_id, server_name, tool_name, arguments_json, status,
                 connection_generation, replay_of_id, created_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?, ?)""",
                (
                    session_id,
                    server_name,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    max(0, int(connection_generation)),
                    replay_of_id,
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def complete_tool_invocation(
        self,
        invocation_id: int,
        *,
        result_message_id: int | None,
        is_error: bool,
        result_text: str,
    ) -> None:
        digest = hashlib.sha256(result_text.encode("utf-8")).hexdigest()
        summary = compact_tool_result(result_text)
        with self._lock, self._connection() as connection:
            connection.execute(
                """UPDATE tool_invocations
                SET status=?, is_error=?, result_message_id=?, result_digest=?,
                    result_summary=?, completed_at=?
                WHERE id=?""",
                (
                    "failed" if is_error else "complete",
                    int(is_error),
                    result_message_id,
                    digest,
                    summary,
                    utc_now(),
                    invocation_id,
                ),
            )

    def fail_tool_invocation(self, invocation_id: int, error: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """UPDATE tool_invocations
                SET status='failed', is_error=1, result_digest=?, result_summary=?,
                    completed_at=? WHERE id=?""",
                (
                    hashlib.sha256(error.encode("utf-8")).hexdigest(),
                    compact_tool_result(error),
                    utc_now(),
                    invocation_id,
                ),
            )

    def list_tool_invocations(
        self, session_id: str, *, include_replays: bool = True
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM tool_invocations WHERE session_id=?"
        values: tuple[Any, ...] = (session_id,)
        if not include_replays:
            query += " AND replay_of_id IS NULL"
        query += " ORDER BY id"
        with self._connection() as connection:
            rows = connection.execute(query, values).fetchall()
        invocations: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["arguments"] = json.loads(item.pop("arguments_json"))
            if item["is_error"] is not None:
                item["is_error"] = bool(item["is_error"])
            invocations.append(item)
        return invocations

    def save_artifact(self, session_id: str, name: str, data: bytes) -> dict[str, Any]:
        safe_name = Path(name).name
        digest = hashlib.sha256(data).hexdigest()
        relative = Path(session_id) / f"{digest[:12]}-{safe_name}"
        destination = self.artifact_root / relative
        destination.write_bytes(data)
        return {"path": str(relative).replace("\\", "/"), "sha256": digest, "size": len(data)}

    def artifact_path(self, session_id: str, name: str) -> Path:
        candidate = (self.artifact_root / session_id / Path(name).name).resolve()
        candidate.relative_to((self.artifact_root / session_id).resolve())
        return candidate

    def bind_file(
        self, session_id: str, polarity: str, kind: str, path_text: str
    ) -> dict[str, Any]:
        path = Path(path_text).resolve(strict=True)
        stat = path.stat()
        fingerprint, mode = fingerprint_file(path)
        row = {
            "session_id": session_id,
            "polarity": polarity,
            "kind": kind,
            "path": str(path),
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
            "fingerprint": fingerprint,
            "fingerprint_mode": mode,
        }
        with self._lock, self._connection() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO file_bindings
                (session_id, polarity, kind, path, size, modified_ns, fingerprint, fingerprint_mode)
                VALUES (:session_id, :polarity, :kind, :path, :size, :modified_ns, :fingerprint, :fingerprint_mode)""",
                row,
            )
        return row

    def verify_bindings(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM file_bindings WHERE session_id=?", (session_id,)
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            path = Path(item["path"])
            if not path.is_file():
                item["state"] = "missing"
            else:
                stat = path.stat()
                if stat.st_size == item["size"] and stat.st_mtime_ns == item["modified_ns"]:
                    item["state"] = "valid"
                else:
                    fingerprint, _ = fingerprint_file(path)
                    item["state"] = "valid" if fingerprint == item["fingerprint"] else "changed"
            results.append(item)
        return results

    def save_mapping(self, session_id: str, mapping: dict[str, Any]) -> None:
        scope = mapping.get("scope", "session_only")
        if scope not in {"session_only", "ontology_candidate"}:
            raise ValueError("scopeが不正です。")
        with self._lock, self._connection() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO ontology_mappings
                (session_id, position, original_token, factor, canonical_value,
                 display_ja, scope, confirmed, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    session_id,
                    int(mapping["position"]),
                    str(mapping["original_token"]),
                    str(mapping["factor"]),
                    str(mapping["canonical_value"]),
                    str(mapping["display_ja"]),
                    scope,
                    utc_now(),
                ),
            )

    def list_mappings(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM ontology_mappings WHERE session_id=? ORDER BY position, original_token",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_ontology_candidates(self, session_id: str) -> list[dict[str, Any]]:
        return [
            item for item in self.list_mappings(session_id) if item["scope"] == "ontology_candidate"
        ]

    def record_approval(
        self, session_id: str, action: str, arguments: dict[str, Any], approved: bool
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO approvals(session_id, action, arguments_json, approved, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    action,
                    json.dumps(arguments, ensure_ascii=False),
                    int(approved),
                    utc_now(),
                ),
            )


def fingerprint_file(path: Path, chunk_size: int = 1024 * 1024) -> tuple[str, str]:
    size = path.stat().st_size
    digest = hashlib.sha256()
    if size <= 64 * chunk_size:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
        return digest.hexdigest(), "sha256-full"
    with path.open("rb") as stream:
        digest.update(stream.read(chunk_size))
        stream.seek(max(0, size // 2 - chunk_size // 2))
        digest.update(stream.read(chunk_size))
        stream.seek(max(0, size - chunk_size))
        digest.update(stream.read(chunk_size))
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest(), "sha256-sampled-3x1MiB"


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
