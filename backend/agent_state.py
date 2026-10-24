"""Private, bounded SQLite storage for local agent sessions and edit checkpoints."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


MAX_SESSIONS = 500
MAX_TURNS_PER_SESSION = 200
MAX_TOTAL_TURNS = 10_000
MAX_CHECKPOINTS_PER_SESSION = 100
MAX_TOTAL_CHECKPOINTS = 1_000
MAX_TURN_CHARS = 24_000
SESSION_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def default_database_path() -> Path:
    configured = os.environ.get("BRAINCELLGPT_DATA_DIR", "").strip()
    if configured:
        data_dir = Path(configured).expanduser()
    elif os.name == "nt":
        data_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "BrainCellGPT"
    else:
        data_dir = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "BrainCellGPT"
    return data_dir / "agent.sqlite3"


class AgentStore:
    def __init__(self, database_path: Path | str | None = None):
        self.path = Path(database_path) if database_path else default_database_path()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA secure_delete = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    base_url TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS turns_session_id_idx ON turns(session_id, id);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    group_id TEXT,
                    relative_path TEXT NOT NULL,
                    previous_content BLOB,
                    existed INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS checkpoints_session_idx ON checkpoints(session_id, created_at DESC);
            """)
            checkpoint_columns = {row[1] for row in db.execute("PRAGMA table_info(checkpoints)")}
            if "group_id" not in checkpoint_columns:
                db.execute("ALTER TABLE checkpoints ADD COLUMN group_id TEXT")
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    @staticmethod
    def validate_session_id(session_id: str) -> str:
        if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("Invalid agent session ID.")
        return session_id

    def create_session(self, workspace: str, provider: str, base_url: str, model: str, mode: str, title: str) -> str:
        now = time.time()
        session_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,workspace,provider,base_url,model,mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (session_id, title[:120] or "New task", workspace, provider[:80], base_url[:2000], model[:200], mode[:40], now, now),
            )
            db.execute("DELETE FROM sessions WHERE id IN (SELECT id FROM sessions ORDER BY updated_at DESC LIMIT -1 OFFSET ?)", (MAX_SESSIONS,))
        return session_id

    def get_session(self, session_id: str):
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def update_session(self, session_id: str, *, provider: str, base_url: str, model: str, mode: str) -> None:
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE sessions SET provider=?, base_url=?, model=?, mode=?, updated_at=? WHERE id=?",
                (provider[:80], base_url[:2000], model[:200], mode[:40], time.time(), session_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Agent session was not found.")

    def add_turn(self, session_id: str, role: str, content: str, metadata: dict | None = None) -> None:
        session_id = self.validate_session_id(session_id)
        if role not in ("user", "assistant") or not isinstance(content, str):
            raise ValueError("Invalid agent turn.")
        content = content[:MAX_TURN_CHARS]
        with self._connect() as db:
            db.execute(
                "INSERT INTO turns(session_id,role,content,metadata,created_at) VALUES(?,?,?,?,?)",
                (session_id, role, content, json.dumps(metadata or {})[:4000], time.time()),
            )
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
            db.execute(
                "DELETE FROM turns WHERE session_id=? AND id NOT IN (SELECT id FROM turns WHERE session_id=? ORDER BY id DESC LIMIT ?)",
                (session_id, session_id, MAX_TURNS_PER_SESSION),
            )
            total_turns = db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            excess_turns = total_turns - MAX_TOTAL_TURNS
            if excess_turns > 0:
                db.execute("DELETE FROM turns WHERE id IN (SELECT id FROM turns ORDER BY id LIMIT ?)", (excess_turns,))

    def get_turns(self, session_id: str) -> list[dict]:
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT role,content,metadata,created_at FROM turns WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        turns = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            turns.append({"role": row["role"], "content": row["content"], "metadata": metadata, "created_at": row["created_at"]})
        return turns

    def history(self, session_id: str) -> list[dict]:
        return [{"role": turn["role"], "content": turn["content"]} for turn in self.get_turns(session_id)]

    def list_sessions(self, workspace: str, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,title,provider,base_url,model,mode,created_at,updated_at FROM sessions WHERE workspace=? ORDER BY updated_at DESC LIMIT ?",
                (workspace, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str, workspace: str) -> bool:
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            cursor = db.execute("DELETE FROM sessions WHERE id=? AND workspace=?", (session_id, workspace))
        return cursor.rowcount == 1

    def add_checkpoint(
        self,
        session_id: str,
        relative_path: str,
        previous_content: bytes | None,
        existed: bool,
        group_id: str | None = None,
    ) -> str:
        session_id = self.validate_session_id(session_id)
        if group_id is not None and (not isinstance(group_id, str) or not SESSION_ID_RE.fullmatch(group_id)):
            raise ValueError("Invalid checkpoint group ID.")
        checkpoint_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO checkpoints(id,session_id,group_id,relative_path,previous_content,existed,created_at) VALUES(?,?,?,?,?,?,?)",
                (checkpoint_id, session_id, group_id, relative_path, previous_content, int(existed), time.time()),
            )
            db.execute("DELETE FROM checkpoints WHERE session_id=? AND id NOT IN (SELECT id FROM checkpoints WHERE session_id=? ORDER BY created_at DESC LIMIT ?)", (session_id, session_id, MAX_CHECKPOINTS_PER_SESSION))
            total_checkpoints = db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
            excess_checkpoints = total_checkpoints - MAX_TOTAL_CHECKPOINTS
            if excess_checkpoints > 0:
                db.execute("DELETE FROM checkpoints WHERE id IN (SELECT id FROM checkpoints ORDER BY created_at LIMIT ?)", (excess_checkpoints,))
        return checkpoint_id

    def list_checkpoints(self, session_id: str) -> list[dict]:
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,relative_path,existed,created_at FROM checkpoints WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, MAX_CHECKPOINTS_PER_SESSION),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_checkpoint_groups(self, session_id: str) -> list[dict]:
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,group_id,relative_path,existed,created_at FROM checkpoints "
                "WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, MAX_CHECKPOINTS_PER_SESSION),
            ).fetchall()
        groups: dict[str, dict] = {}
        for row in rows:
            checkpoint = dict(row)
            group_id = checkpoint["group_id"] or checkpoint["id"]
            group = groups.setdefault(group_id, {
                "id": group_id,
                "created_at": checkpoint["created_at"],
                "files": [],
                "checkpoints": [],
            })
            if checkpoint["relative_path"] not in group["files"]:
                group["files"].append(checkpoint["relative_path"])
            group["checkpoints"].append({
                "id": checkpoint["id"],
                "path": checkpoint["relative_path"],
                "existed": bool(checkpoint["existed"]),
            })
            group["created_at"] = max(group["created_at"], checkpoint["created_at"])
        return sorted(groups.values(), key=lambda item: item["created_at"], reverse=True)

    def get_checkpoint_group(self, group_id: str, session_id: str) -> list[dict]:
        if not isinstance(group_id, str) or not SESSION_ID_RE.fullmatch(group_id):
            raise ValueError("Invalid checkpoint group ID.")
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM checkpoints WHERE session_id=? AND group_id=? ORDER BY created_at ASC,id ASC",
                (session_id, group_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_checkpoint(self, checkpoint_id: str, session_id: str):
        if not isinstance(checkpoint_id, str) or not SESSION_ID_RE.fullmatch(checkpoint_id):
            raise ValueError("Invalid checkpoint ID.")
        session_id = self.validate_session_id(session_id)
        with self._connect() as db:
            row = db.execute("SELECT * FROM checkpoints WHERE id=? AND session_id=?", (checkpoint_id, session_id)).fetchone()
        return dict(row) if row else None


_default_store: AgentStore | None = None


def get_agent_store() -> AgentStore:
    global _default_store
    if _default_store is None:
        _default_store = AgentStore()
    return _default_store
