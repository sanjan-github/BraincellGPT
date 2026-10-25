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
MAX_SHARED_MESSAGES_PER_INSTALLATION = 10
SHARED_GROQ_MODE = "shared_groq"
OWN_KEY_MODE = "own_key"
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
            db.execute("""
                CREATE TABLE IF NOT EXISTS app_config (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    credential_mode TEXT CHECK(credential_mode IN ('shared_groq', 'own_key'))
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS shared_allowance (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    messages_used INTEGER NOT NULL DEFAULT 0
                        CHECK(messages_used BETWEEN 0 AND 10)
                )
            """)
            allowance_exists = db.execute(
                "SELECT 1 FROM shared_allowance WHERE singleton = 1"
            ).fetchone()
            if allowance_exists is None:
                db.execute(
                    "INSERT INTO shared_allowance(singleton, messages_used) "
                    "SELECT 1, min(?, coalesce(sum(messages_used), 0)) FROM conversations",
                    (MAX_SHARED_MESSAGES_PER_INSTALLATION,),
                )
            db.execute("""
                CREATE TABLE IF NOT EXISTS shared_requests (
                    request_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS own_requests (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (conversation_id, request_id)
                )
            """)
            db.execute(
                "INSERT OR IGNORE INTO shared_requests(request_id, conversation_id, created_at) "
                "SELECT request_id, conversation_id, created_at FROM chat_requests"
            )
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

    def delete_conversation(self, conversation_id: str) -> None:
        self._validate_id(conversation_id, "conversation")
        with self._connect() as db:
            db.execute("DELETE FROM chat_requests WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM own_requests WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def credential_mode(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT credential_mode FROM app_config WHERE singleton = 1").fetchone()
        return str(row[0]) if row and row[0] else None

    def set_credential_mode(self, mode: str | None) -> None:
        if mode not in (None, SHARED_GROQ_MODE, OWN_KEY_MODE):
            raise ValueError("Choose shared Groq access or your own provider key.")
        with self._connect() as db:
            if mode is None:
                db.execute("DELETE FROM app_config WHERE singleton = 1")
            else:
                db.execute(
                    "INSERT INTO app_config(singleton, credential_mode) VALUES(1, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET credential_mode = excluded.credential_mode",
                    (mode,),
                )

    def shared_usage(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT messages_used FROM shared_allowance WHERE singleton = 1").fetchone()
        return int(row[0]) if row else 0

    def reserve_shared_message(self, conversation_id: str, request_id: str) -> int:
        """Atomically consume the installation-wide shared-key allowance."""
        self._validate_id(conversation_id, "conversation")
        self._validate_id(request_id, "request")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if exists is None:
                raise ConversationNotFound
            if db.execute("SELECT 1 FROM shared_requests WHERE request_id = ?", (request_id,)).fetchone():
                raise DuplicateChatRequest
            row = db.execute("SELECT messages_used FROM shared_allowance WHERE singleton = 1").fetchone()
            used = int(row[0]) if row else 0
            if used >= MAX_SHARED_MESSAGES_PER_INSTALLATION:
                raise MessageLimitReached
            used += 1
            db.execute(
                "INSERT INTO shared_allowance(singleton, messages_used) VALUES(1, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET messages_used = excluded.messages_used",
                (used,),
            )
            db.execute(
                "INSERT INTO shared_requests(request_id, conversation_id, created_at) VALUES(?, ?, ?)",
                (request_id, conversation_id, time.time()),
            )
            db.execute("COMMIT")
            return used

    def reserve_own_request(self, conversation_id: str, request_id: str) -> None:
        """Deduplicate own-key requests without applying the shared allowance."""
        self._validate_id(conversation_id, "conversation")
        self._validate_id(request_id, "request")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise ConversationNotFound
            try:
                db.execute(
                    "INSERT INTO own_requests(conversation_id, request_id, created_at) VALUES(?, ?, ?)",
                    (conversation_id, request_id, time.time()),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateChatRequest from exc
            db.execute("COMMIT")

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
