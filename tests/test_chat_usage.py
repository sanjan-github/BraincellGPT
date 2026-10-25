import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.chat_usage import (
    ChatUsageStore,
    DuplicateChatRequest,
    MAX_MESSAGES_PER_CONVERSATION,
    MessageLimitReached,
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
            self.assertEqual(tables, {"conversations", "chat_requests"})

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
