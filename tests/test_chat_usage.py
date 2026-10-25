import tempfile
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.chat_usage import (
    ChatUsageStore,
    ConversationNotFound,
    DuplicateChatRequest,
    MAX_MESSAGES_PER_CONVERSATION,
    MAX_SHARED_MESSAGES_PER_INSTALLATION,
    MessageLimitReached,
    OWN_KEY_MODE,
    SHARED_GROQ_MODE,
)


class ChatUsageStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "usage.sqlite3"
        self.store = ChatUsageStore(self.database)
        self.conversation_id = self.store.create_conversation()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_count_persists_across_store_instances_without_storing_content(self):
        self.store.reserve_message(self.conversation_id, "a" * 32)
        reloaded = ChatUsageStore(self.database)
        self.assertEqual(reloaded.usage(self.conversation_id), 1)
        with reloaded._connect() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"conversations", "chat_requests", "app_config", "shared_allowance", "shared_requests", "own_requests"} <= tables)

    def test_credential_mode_and_shared_usage_persist(self):
        self.store.set_credential_mode(SHARED_GROQ_MODE)
        self.store.reserve_shared_message(self.conversation_id, "a" * 32)
        reloaded = ChatUsageStore(self.database)
        self.assertEqual(reloaded.credential_mode(), SHARED_GROQ_MODE)
        self.assertEqual(reloaded.shared_usage(), 1)
        reloaded.set_credential_mode(OWN_KEY_MODE)
        self.assertEqual(self.store.credential_mode(), OWN_KEY_MODE)

    def test_shared_limit_is_installation_wide_across_conversations(self):
        other_conversation = self.store.create_conversation()
        for index in range(MAX_SHARED_MESSAGES_PER_INSTALLATION):
            conversation = self.conversation_id if index % 2 == 0 else other_conversation
            self.store.reserve_shared_message(conversation, f"{index:032x}")
        with self.assertRaises(MessageLimitReached):
            self.store.reserve_shared_message(other_conversation, "f" * 32)
        self.assertEqual(self.store.shared_usage(), MAX_SHARED_MESSAGES_PER_INSTALLATION)

    def test_concurrent_shared_requests_cannot_exceed_installation_limit(self):
        def reserve(index):
            try:
                return self.store.reserve_shared_message(self.conversation_id, f"{index:032x}")
            except MessageLimitReached:
                return None

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(reserve, range(24)))
        self.assertEqual(sum(result is not None for result in results), MAX_SHARED_MESSAGES_PER_INSTALLATION)
        self.assertEqual(self.store.shared_usage(), MAX_SHARED_MESSAGES_PER_INSTALLATION)

    def test_existing_per_conversation_usage_is_migrated_into_shared_allowance(self):
        legacy = Path(self.temp_dir.name) / "legacy.sqlite3"
        db = sqlite3.connect(legacy)
        db.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, messages_used INTEGER NOT NULL, created_at REAL NOT NULL)")
        db.execute("CREATE TABLE chat_requests (conversation_id TEXT, request_id TEXT, created_at REAL)")
        db.execute("INSERT INTO conversations VALUES (?, ?, ?)", ("a" * 32, 4, 1.0))
        db.execute("INSERT INTO chat_requests VALUES (?, ?, ?)", ("a" * 32, "b" * 32, 1.0))
        db.commit()
        db.close()

        migrated = ChatUsageStore(legacy)
        self.assertEqual(migrated.shared_usage(), 4)
        with self.assertRaises(DuplicateChatRequest):
            migrated.reserve_shared_message("a" * 32, "b" * 32)

    def test_deleting_conversation_does_not_restore_shared_allowance(self):
        self.store.reserve_shared_message(self.conversation_id, "a" * 32)
        self.store.delete_conversation(self.conversation_id)
        self.assertEqual(self.store.shared_usage(), 1)
        with self.assertRaises(ConversationNotFound):
            self.store.reserve_shared_message(self.conversation_id, "b" * 32)

    def test_own_key_requests_do_not_consume_shared_allowance(self):
        for index in range(12):
            self.store.reserve_own_request(self.conversation_id, f"{index:032x}")
        self.assertEqual(self.store.shared_usage(), 0)

    def test_tenth_request_is_allowed_and_eleventh_is_rejected(self):
        for index in range(MAX_MESSAGES_PER_CONVERSATION):
            self.assertEqual(self.store.reserve_message(self.conversation_id, f"{index:032x}"), index + 1)
        with self.assertRaises(MessageLimitReached):
            self.store.reserve_message(self.conversation_id, "f" * 32)
        self.assertEqual(self.store.usage(self.conversation_id), MAX_MESSAGES_PER_CONVERSATION)

    def test_duplicate_request_id_does_not_consume_another_message(self):
        self.store.reserve_message(self.conversation_id, "a" * 32)
        with self.assertRaises(DuplicateChatRequest):
            self.store.reserve_message(self.conversation_id, "a" * 32)
        self.assertEqual(self.store.usage(self.conversation_id), 1)

    def test_concurrent_requests_cannot_exceed_conversation_limit(self):
        def reserve(index):
            try:
                return self.store.reserve_message(self.conversation_id, f"{index:032x}")
            except MessageLimitReached:
                return None

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(reserve, range(24)))
        self.assertEqual(sum(result is not None for result in results), MAX_MESSAGES_PER_CONVERSATION)
        self.assertEqual(self.store.usage(self.conversation_id), MAX_MESSAGES_PER_CONVERSATION)


if __name__ == "__main__":
    unittest.main()
