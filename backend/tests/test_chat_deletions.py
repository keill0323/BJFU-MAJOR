"""删除清单仅使用临时文件和内存数据库；不访问线上或项目上传目录。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.support import DatabaseTestCase
from app.config import settings
from app.models.chat import ChatConversation, ChatEvent, ChatMessage
from app.services import chat_deletions as ledger


class ChatDeletionTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        directory = TemporaryDirectory(prefix="chat-deletion-ledger-test-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "private" / "chat-deletions.jsonl"
        setting = patch.object(settings, "CHAT_DELETION_LEDGER", str(self.path))
        setting.start()
        self.addCleanup(setting.stop)
        self.created = datetime(2026, 10, 1, 1, 2, 3, 123456)
        self.deleted = self.created + timedelta(days=1)
        self.room = ChatConversation(user_low=1, user_high=2, initiator_id=1, last_seq=1,
                                     event_floor=0, created_at=self.created, updated_at=self.created)
        self.db.add(self.room)
        self.db.flush()
        self.message = ChatMessage(conversation_id=self.room.id, seq=1, sender_id=1,
                                   client_id="ledger-test-message-001", payload_hash="a" * 64,
                                   kind="text", content="绝不可写入清单的私人消息", state="active", created_at=self.created)
        self.db.add(self.message)
        self.db.commit()
        self.message_id, self.room_id = self.message.id, self.room.id

    def record(self, **overrides):
        return {"cid": self.room_id, "id": self.message_id, "sender": 1,
                "client_id": "ledger-test-message-001", "hash": "a" * 64,
                "created_at": "2026-10-01T01:02:03.123456Z", "deleted_at": "2026-10-02T01:02:03.123456Z",
                "state": "purged", **overrides}

    def write(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"".join((json.dumps(record) + "\n").encode("ascii") for record in records))

    def test_append_fsyncs_only_identity_fields_before_allowing_body_deletion(self):
        original_fsync = os.fsync
        with patch.object(ledger.os, "fsync", wraps=original_fsync) as sync:
            result = ledger.append(self.message, self.deleted, "purged")
        self.assertGreaterEqual(sync.call_count, 1)
        self.assertEqual(result, self.record())
        self.assertEqual(json.loads(self.path.read_text()), self.record())
        self.assertNotIn("content", result)
        self.assertNotIn(self.message.content, self.path.read_text())
        self.assertEqual(self.db.get(ChatMessage, self.message_id).state, "active")
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_unconfigured_or_unwritable_ledger_fails_closed(self):
        with patch.object(settings, "CHAT_DELETION_LEDGER", ""):
            with self.assertRaisesRegex(ledger.LedgerError, "未配置"):
                ledger.append(self.message, self.deleted, "purged")
        with patch.object(ledger.os, "write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(ledger.LedgerError, "无法写入或同步"):
                ledger.append(self.message, self.deleted, "purged")
        self.assertEqual(self.db.get(ChatMessage, self.message_id).state, "active")
        self.assertIsNotNone(self.db.get(ChatMessage, self.message_id).content)

    def test_fsync_failure_is_not_reported_as_a_successful_deletion(self):
        self.path.parent.mkdir(parents=True)
        with patch.object(ledger.os, "fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(ledger.LedgerError):
                ledger.append(self.message, self.deleted, "purged")
        self.assertEqual(self.message.state, "active")
        self.assertIsNotNone(self.message.content)

    def test_incomplete_existing_tail_blocks_new_appends(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'{"id":')
        with self.assertRaisesRegex(ledger.LedgerError, "尾部不完整"):
            ledger.append(self.message, self.deleted, "purged")
        self.assertEqual(self.path.read_bytes(), b'{"id":')

    def test_threaded_append_keeps_complete_records_and_no_bodies(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            records = list(pool.map(lambda _: ledger.append(self.message, self.deleted, "purged"), range(30)))
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 30)
        self.assertTrue(all(json.loads(line) == self.record() for line in lines))
        self.assertEqual(len(records), 30)

    def test_restore_purges_exact_message_and_publishes_one_incremental_delete_event(self):
        self.write([self.record()])
        result = ledger.restore(self.db, self.path)
        self.assertEqual(result["applied"], 1)
        self.db.expire_all()
        message = self.db.get(ChatMessage, self.message_id)
        self.assertIsNone(message.content)
        self.assertEqual(message.state, "purged")
        self.assertEqual(message.purged_at, self.deleted)
        event = self.db.query(ChatEvent).one()
        self.assertEqual((event.kind, event.message_id, event.seq, event.notified), ("delete", self.message_id, 2, False))
        self.assertEqual(self.db.get(ChatConversation, self.room_id).last_seq, 2)
        replay = ledger.restore(self.db, self.path)
        self.assertEqual(replay["applied"], 0)
        self.assertEqual(replay["already_removed"], 1)
        self.assertEqual(self.db.query(ChatEvent).count(), 1)

    def test_restore_matches_all_six_identity_fields_including_full_timestamp(self):
        for field, value in [("cid", self.room_id + 1), ("id", self.message_id + 1), ("sender", 2),
                             ("client_id", "ledger-other-message-001"), ("hash", "b" * 64),
                             ("created_at", "2026-10-01T01:02:03.123457Z")]:
            with self.subTest(field=field):
                self.write([self.record(**{field: value})])
                result = ledger.restore(self.db, self.path)
                self.assertEqual(result["applied"], 0)
                self.assertEqual(result["missing"] + result["mismatched"], 1)
                self.assertEqual(self.db.get(ChatMessage, self.message_id).state, "active")
                self.assertIsNotNone(self.db.get(ChatMessage, self.message_id).content)
        self.assertEqual(self.db.query(ChatEvent).count(), 0)

    def test_restore_allocates_after_existing_event_and_message_watermarks(self):
        self.db.add(ChatEvent(conversation_id=self.room_id, seq=12, kind="read", created_at=self.created, notified=True))
        self.room.last_seq = 2
        self.db.commit()
        self.write([self.record()])
        ledger.restore(self.db, self.path)
        self.assertEqual(self.db.get(ChatConversation, self.room_id).last_seq, 13)
        self.assertEqual(self.db.query(ChatEvent).filter_by(kind="delete").one().seq, 13)

    def test_restore_never_overwrites_already_recalled_or_hidden_rows(self):
        for state in ("recalled", "hidden", "purged"):
            with self.subTest(state=state):
                self.message.state, self.message.content = state, None
                self.db.commit()
                self.write([self.record()])
                result = ledger.restore(self.db, self.path)
                self.assertEqual(result["already_removed"], 1)
                self.assertEqual(self.db.get(ChatMessage, self.message_id).state, state)
        self.assertEqual(self.db.query(ChatEvent).count(), 0)

    def test_restore_is_strict_about_unknown_duplicate_or_malformed_fields(self):
        invalid = [self.record(content="must not be present"), self.record(id=True), self.record(hash="invalid"),
                   self.record(state="active"), self.record(created_at="2026-10-01T01:02:03Z"),
                   self.record(deleted_at="2026-09-30T01:02:03.123456Z"), self.record(client_id="short")]
        for record in invalid:
            with self.subTest(record=record):
                self.write([record])
                with self.assertRaisesRegex(ledger.LedgerError, "第 1 行"):
                    ledger.restore(self.db, self.path)
                self.assertEqual(self.message.state, "active")
        self.path.write_bytes(b'{"id":1,"id":2}\n')
        with self.assertRaisesRegex(ledger.LedgerError, "第 1 行"):
            ledger.restore(self.db, self.path)
        self.assertEqual(self.db.query(ChatEvent).count(), 0)

    def test_restore_limits_line_memory_and_rejects_truncated_or_blank_records(self):
        self.path.parent.mkdir(parents=True)
        for raw in [b"x" * 10000 + b"\n", json.dumps(self.record()).encode("ascii"), b"\n"]:
            self.path.write_bytes(raw)
            with self.assertRaises(ledger.LedgerError):
                ledger.restore(self.db, self.path)
        self.assertEqual(self.message.state, "active")

    def test_corrupt_later_line_stops_restore_but_successfully_deleted_body_stays_deleted(self):
        self.write([self.record()])
        with self.path.open("ab") as stream:
            stream.write(b'{broken json}\n')
        with self.assertRaisesRegex(ledger.LedgerError, "第 2 行"):
            ledger.restore(self.db, self.path)
        self.db.expire_all()
        self.assertIsNone(self.db.get(ChatMessage, self.message_id).content)
        self.assertEqual(self.db.query(ChatEvent).count(), 1)

    def test_database_commit_failure_rolls_back_both_body_and_event(self):
        self.write([self.record()])
        with patch.object(self.db, "commit", side_effect=RuntimeError("commit interrupted")):
            with self.assertRaisesRegex(RuntimeError, "commit interrupted"):
                ledger.restore(self.db, self.path)
        self.assertEqual(self.db.get(ChatMessage, self.message_id).state, "active")
        self.assertIsNotNone(self.db.get(ChatMessage, self.message_id).content)
        self.assertEqual(self.db.query(ChatEvent).count(), 0)
        self.assertEqual(ledger.restore(self.db, self.path)["applied"], 1)

    def test_timezone_inputs_are_canonical_and_invalid_deletion_intents_never_write(self):
        offset = timezone(timedelta(hours=8))
        ledger.append(self.message, self.deleted.replace(tzinfo=timezone.utc).astimezone(offset), "recalled")
        self.assertEqual(json.loads(self.path.read_text())["deleted_at"], self.record()["deleted_at"])
        original = self.path.read_bytes()
        for at, state in [(self.created - timedelta(seconds=1), "purged"), (self.deleted, "active")]:
            with self.assertRaises(ledger.LedgerError):
                ledger.append(self.message, at, state)
        self.assertEqual(self.path.read_bytes(), original)

    def test_restore_rejects_missing_file_and_pending_unrelated_changes(self):
        with self.assertRaisesRegex(ledger.LedgerError, "无法读取"):
            ledger.restore(self.db, self.path)
        self.write([self.record()])
        self.room.updated_at = self.deleted
        with self.assertRaisesRegex(ledger.LedgerError, "待提交业务写入"):
            ledger.restore(self.db, self.path)
        self.db.rollback()
        self.assertEqual(self.message.state, "active")

    def test_cli_uses_isolated_session_and_returns_nonzero_for_corrupt_ledger(self):
        self.write([self.record()])
        class SessionContext:
            def __enter__(context): return self.db
            def __exit__(context, *args): return False
        with patch("app.database.SessionLocal", return_value=SessionContext()), patch("sys.stdout", new_callable=StringIO) as output:
            self.assertEqual(ledger.main(["apply", "--ledger", str(self.path)]), 0)
            self.assertIn("删除正文 1 条", output.getvalue())
        self.path.write_bytes(b"invalid\n")
        with patch("app.database.SessionLocal", return_value=SessionContext()), patch("sys.stderr", new_callable=StringIO) as output:
            self.assertEqual(ledger.main(["apply", "--ledger", str(self.path)]), 1)
            self.assertIn("停止恢复", output.getvalue())
