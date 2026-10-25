"""Persistent, atomic per-conversation chat request limits."""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


MAX_MESSAGES_PER_CONVERSATION = 10
CONVERSATION_ID_RE = re.compile(r"^[a-f0-9]{32}$")
REQUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")


class ConversationNotFound(Exception):
    """The client referenced a conversation that does not exist."""


class MessageLimitReached(Exception):
    """The conversation has used its allowed provider requests."""


class DuplicateChatRequest(Exception):
    """This request ID has already been reserved for a provider call."""


def default_database_path() -> Path:
    configured = os.environ.get("BRAINCELLGPT_DATA_DIR", "").strip()
    if configured:
        data_dir = Path(configured).expanduser()
    elif os.name == "nt":
        data_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "BrainCellGPT"
    else:
        data_dir = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "BrainCellGPT"
    return data_dir / "chat-usage.sqlite3"


class ChatUsageStore:
    """Store conversation identities and request counts, never message text or keys."""

    def __init__(self, database_path: Path | str | None = None):
        self.path = Path(database_path) if database_path else default_database_path()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    messages_used INTEGER NOT NULL DEFAULT 0 CHECK(messages_used BETWEEN 0 AND 10),
                    created_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS chat_requests (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (conversation_id, request_id)
                )
            """)
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        db.execute("PRAGMA secure_delete = ON")
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _validate_id(value: str, name: str) -> str:
        pattern = CONVERSATION_ID_RE if name == "conversation" else REQUEST_ID_RE
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError("Invalid " + name + " ID.")
        return value

    def create_conversation(self) -> str:
        conversation_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO conversations(id, created_at) VALUES(?, ?)", (conversation_id, time.time()))
        return conversation_id

    def usage(self, conversation_id: str) -> int:
        self._validate_id(conversation_id, "conversation")
        with self._connect() as db:
            row = db.execute("SELECT messages_used FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            raise ConversationNotFound
        return int(row[0])

    def reserve_message(self, conversation_id: str, request_id: str) -> int:
        """Atomically reserve one provider request, enforcing count and idempotency."""
        self._validate_id(conversation_id, "conversation")
        self._validate_id(request_id, "request")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT messages_used FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                raise ConversationNotFound
            if db.execute(
                "SELECT 1 FROM chat_requests WHERE conversation_id = ? AND request_id = ?",
                (conversation_id, request_id),
            ).fetchone():
                raise DuplicateChatRequest
            used = int(row[0])
            if used >= MAX_MESSAGES_PER_CONVERSATION:
                raise MessageLimitReached
            used += 1
            db.execute("UPDATE conversations SET messages_used = ? WHERE id = ?", (used, conversation_id))
            db.execute(
                "INSERT INTO chat_requests(conversation_id, request_id, created_at) VALUES(?, ?, ?)",
                (conversation_id, request_id, time.time()),
            )
            db.execute("COMMIT")
            return used
