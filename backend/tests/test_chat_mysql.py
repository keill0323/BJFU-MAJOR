"""可选真实 MySQL RR 并发回归；默认跳过，绝不使用项目业务数据库。

CHAT_TEST_MYSQL_URL 必须指向全新空库 cs2_chat_test_<十六进制后缀>。
测试结束只删除本套件创建的表，不 DROP DATABASE。内容检测全程 mock。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from tests import support  # noqa: F401 — 提前启用不读取项目 .env 的隔离配置。
from fastapi import HTTPException
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.models.user import User, UserRole
from app.models.chat import ChatConversation, ChatEvent, ChatMessage, ChatMember, ChatBlock, ChatReport, ChatSanction
from app.schemas.chat import OpenConversation, SendMessage, ReportMessage, SanctionUser
from app.services import chat_service as chat


@unittest.skipUnless(os.environ.get("CHAT_TEST_MYSQL_URL"), "未设置独立 MySQL 测试库，跳过真实数据库并发测试")
class MySQLConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            url = make_url(os.environ["CHAT_TEST_MYSQL_URL"])
        except Exception:
            raise RuntimeError("CHAT_TEST_MYSQL_URL 格式不合法") from None
        if url.get_backend_name() != "mysql" or not re.fullmatch(r"cs2_chat_test_[a-f0-9]+", url.database or ""):
            raise RuntimeError("仅允许独立 MySQL 测试库 cs2_chat_test_<十六进制后缀>，已拒绝连接")
        cls.engine = create_engine(url, isolation_level="REPEATABLE READ", pool_pre_ping=True)
        cls.addClassCleanup(cls.engine.dispose)
        inspection = inspect(cls.engine)
        if inspection.get_table_names() or inspection.get_view_names():
            raise RuntimeError("MySQL 测试数据库必须全新且为空；未修改任何已有表")
        cls.owned_tables = tuple(Base.metadata.sorted_tables)
        Base.metadata.create_all(cls.engine)
        cls.addClassCleanup(cls.drop_owned_tables)
        cls.ledger_dir = TemporaryDirectory(prefix="cs2-chat-mysql-ledger-")
        cls.addClassCleanup(cls.ledger_dir.cleanup)
        for key, value in {
            "CHAT_ENABLED": True, "CHAT_WRITE_ENABLED": True, "CHAT_ALLOW_USERS": "",
            "CHAT_MODERATION_ENABLED": False, "WX_MOCK_LOGIN": True,
            "CHAT_DELETION_LEDGER": str(Path(cls.ledger_dir.name) / "deleted.jsonl"),
        }.items():
            patcher = patch.object(settings, key, value)
            patcher.start()
            cls.addClassCleanup(patcher.stop)

    @classmethod
    def drop_owned_tables(cls):
        Base.metadata.drop_all(cls.engine, tables=list(cls.owned_tables), checkfirst=True)

    def setUp(self):
        with self.engine.begin() as connection:
            for table in reversed(self.owned_tables):
                connection.execute(table.delete())
        with Session(self.engine, autoflush=False) as db:
            self.assertEqual(db.connection().get_isolation_level(), "REPEATABLE READ")
            users = [User(wx_openid="test-mysql-chat-" + str(index), nickname="并发测试" + str(index),
                          role=UserRole.USER, is_hidden=False, is_verified=True) for index in range(3)]
            db.add_all(users)
            db.flush()
            self.ids = [user.id for user in users]
            db.commit()
            self.cid = chat.open_conversation(db, self.ids[0], OpenConversation(target_id=self.ids[1]))["id"]

    def packet(self, suffix, content="并发测试消息"):
        return SendMessage(client_id="mysql-test-message-" + suffix, client_created_at=chat.now(), content=content)

    def send_in_thread(self, user_id, packet):
        with Session(self.engine, autoflush=False) as db:
            # 模拟真实 HTTP 认证先 SELECT 用户并建立 RR 快照。
            db.query(User).filter_by(id=user_id).first()
            return chat.send_message(db, user_id, self.cid, packet)

    def extra_users(self, count, admins=False):
        with Session(self.engine, autoflush=False) as db:
            users = [User(wx_openid="test-mysql-extra-" + str(index), nickname="独立并发" + str(index),
                          role=UserRole.ADMIN if admins and index % 2 == 0 else UserRole.USER,
                          is_hidden=False, is_verified=True) for index in range(count)]
            db.add_all(users)
            db.flush()
            ids = [user.id for user in users]
            db.commit()
            return ids

    def concurrently(self, items, operation, insert_table=None):
        start = threading.Barrier(len(items))
        inserts = threading.Barrier(len(items)) if insert_table else None
        def before_insert(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("INSERT INTO " + insert_table.upper()):
                # 无关事务均已完成缺失行检查，再同时插入，稳定复现 RR 间隙锁冲突。
                inserts.wait(timeout=10)
        def execute(item):
            with Session(self.engine, autoflush=False) as db:
                db.query(User).filter_by(id=item[0]).first()  # 模拟鉴权已建立旧快照。
                start.wait(timeout=10)
                return operation(db, item)
        if insert_table:
            event.listen(self.engine, "before_cursor_execute", before_insert)
        try:
            with ThreadPoolExecutor(max_workers=len(items)) as pool:
                futures = [pool.submit(execute, item) for item in items]
                return [future.result(timeout=30) for future in futures]
        finally:
            if insert_table:
                event.remove(self.engine, "before_cursor_execute", before_insert)

    def test_distinct_pairs_can_create_simultaneously_without_gap_deadlock(self):
        ids = self.extra_users(16)
        pairs = list(zip(ids[::2], ids[1::2]))
        rooms = self.concurrently(pairs, lambda db, pair: chat.open_conversation(db, pair[0], OpenConversation(target_id=pair[1])),
                                  insert_table="chat_conversations")
        self.assertEqual(len({room["id"] for room in rooms}), 8)
        with Session(self.engine) as db:
            self.assertEqual(db.query(ChatConversation).count(), 9)
            self.assertEqual(db.query(ChatMember).count(), 18)

    def test_simultaneous_same_pair_creates_one_conversation(self):
        a, b = self.extra_users(2)
        pairs = [(a, b), (b, a)] * 4
        rooms = self.concurrently(pairs, lambda db, pair: chat.open_conversation(db, pair[0], OpenConversation(target_id=pair[1])))
        self.assertEqual(len({room["id"] for room in rooms}), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.query(ChatConversation).filter_by(user_low=a, user_high=b).count(), 1)
            self.assertEqual(db.query(ChatMember).filter_by(conversation_id=rooms[0]["id"]).count(), 2)

    def test_simultaneous_last_daily_slot_admits_only_one_new_room(self):
        actor, *targets = self.extra_users(12)
        with Session(self.engine, autoflush=False) as db:
            for target in targets[:9]:
                chat.open_conversation(db, actor, OpenConversation(target_id=target))
        def open_last(db, pair):
            try:
                return chat.open_conversation(db, pair[0], OpenConversation(target_id=pair[1]))
            except HTTPException as error:
                return error.status_code
        results = self.concurrently([(actor, target) for target in targets[9:]], open_last)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(results.count(429), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.query(ChatConversation).filter_by(initiator_id=actor).count(), 10)

    def test_distinct_pairs_can_block_without_existing_rooms_or_gap_deadlock(self):
        ids = self.extra_users(16)
        pairs = list(zip(ids[::2], ids[1::2]))
        self.concurrently(pairs, lambda db, pair: chat.set_block(db, pair[0], pair[1], True), insert_table="chat_blocks")
        with Session(self.engine) as db:
            self.assertEqual(db.query(ChatBlock).count(), 8)
            for actor, target in pairs:
                self.assertTrue(chat.blocked(db, actor, target))

    def test_distinct_conversations_can_report_without_gap_deadlock(self):
        ids = self.extra_users(16)
        reports = []
        with Session(self.engine, autoflush=False) as db, patch.object(chat, "check_content", return_value=None):
            for index, (a, b) in enumerate(zip(ids[::2], ids[1::2])):
                cid = chat.open_conversation(db, a, OpenConversation(target_id=b))["id"]
                message = chat.send_message(db, a, cid, self.packet("report-" + str(index)))["message"]
                reports.append((b, int(message["id"])))
        self.concurrently(reports, lambda db, item: chat.report(db, item[0], ReportMessage(message_id=item[1], reason="并发举报测试")),
                          insert_table="chat_reports")
        with Session(self.engine) as db:
            self.assertEqual(db.query(ChatReport).count(), 8)

    def test_distinct_admin_target_pairs_can_sanction_without_gap_deadlock(self):
        ids = self.extra_users(16, admins=True)
        pairs = list(zip(ids[::2], ids[1::2]))
        self.concurrently(pairs, lambda db, pair: chat.sanction(db, pair[0], SanctionUser(user_id=pair[1], hours=1, reason="并发禁言测试")),
                          insert_table="chat_sanctions")
        with Session(self.engine) as db:
            self.assertEqual(db.query(ChatSanction).count(), 8)

    def test_simultaneous_bidirectional_messages_have_unique_order(self):
        barrier = threading.Barrier(2)
        def detection(openid, content):
            barrier.wait(timeout=10)
        with patch.object(chat, "check_content", side_effect=detection), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.send_in_thread, self.ids[0], self.packet("direction-a")),
                       pool.submit(self.send_in_thread, self.ids[1], self.packet("direction-b"))]
            responses = [future.result(timeout=20) for future in futures]
        self.assertEqual(len({response["message"]["id"] for response in responses}), 2)
        self.assertEqual(sorted(int(response["message"]["seq"]) for response in responses), [1, 2])
        with Session(self.engine, autoflush=False) as db:
            self.assertEqual(db.query(ChatMessage).count(), 2)
            self.assertEqual(db.query(ChatEvent).count(), 2)
            self.assertEqual(db.get(ChatConversation, self.cid).last_seq, 2)
            for user_id in self.ids[:2]:
                self.assertEqual(chat.summary(db, user_id)["unread"], 1)

    def test_same_client_id_concurrently_creates_one_message(self):
        barrier = threading.Barrier(2)
        packet = self.packet("duplicate")
        def detection(openid, content):
            barrier.wait(timeout=10)
        with patch.object(chat, "check_content", side_effect=detection), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.send_in_thread, self.ids[0], packet) for _ in range(2)]
            responses = [future.result(timeout=20) for future in futures]
        self.assertEqual(responses[0]["message"]["id"], responses[1]["message"]["id"])
        self.assertEqual(sorted(response["replayed"] for response in responses), [False, True])
        with Session(self.engine, autoflush=False) as db:
            self.assertEqual(db.query(ChatMessage).count(), 1)
            self.assertEqual(db.query(ChatEvent).count(), 1)
            self.assertEqual(db.get(ChatConversation, self.cid).last_seq, 1)

    def test_block_committed_during_detection_stops_send(self):
        entered, release = threading.Event(), threading.Event()
        def detection(openid, content):
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError("外部检测测试暂停超时")
        with patch.object(chat, "check_content", side_effect=detection), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.send_in_thread, self.ids[0], self.packet("blocked"))
            try:
                self.assertTrue(entered.wait(timeout=10))
                with Session(self.engine, autoflush=False) as db:
                    db.query(User).filter_by(id=self.ids[1]).first()
                    chat.set_block(db, self.ids[1], self.ids[0], True)
            finally:
                release.set()
            with self.assertRaises(HTTPException) as error:
                future.result(timeout=20)
        self.assertEqual(error.exception.status_code, 403)
        with Session(self.engine, autoflush=False) as db:
            self.assertEqual(db.query(ChatMessage).count(), 0)
            self.assertEqual(db.query(ChatEvent).filter_by(kind="message").count(), 0)

    def test_actual_read_and_cleanup_race_does_not_delete_unread_or_duplicate_events(self):
        with patch.object(chat, "check_content", return_value=None):
            sent = self.send_in_thread(self.ids[0], self.packet("cleanup"))["message"]
        message_id = int(sent["id"])
        with Session(self.engine, autoflush=False) as db:
            message = db.get(ChatMessage, message_id)
            message.created_at = chat.now() - timedelta(days=2)
            db.commit()
        def read():
            with Session(self.engine, autoflush=False) as db:
                db.query(User).filter_by(id=self.ids[1]).first()
                return chat.mark_read(db, self.ids[1], self.cid, message_ids=[message_id])
        def clean():
            with Session(self.engine, autoflush=False) as db:
                return chat.cleanup(db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(read), pool.submit(clean)]
            results = [future.result(timeout=20) for future in futures]
        self.assertIn(results[1], (0, 1))
        with Session(self.engine, autoflush=False) as db:
            chat.cleanup(db)
            message = db.get(ChatMessage, message_id)
            self.assertIsNotNone(message.read_at)
            self.assertIsNone(message.content)
            self.assertEqual(message.state, "purged")
            self.assertEqual(db.query(ChatEvent).filter_by(kind="read").count(), 1)
            self.assertEqual(db.query(ChatEvent).filter_by(kind="delete").count(), 1)
            events = db.query(ChatEvent).filter_by(conversation_id=self.cid).order_by(ChatEvent.seq).all()
            self.assertEqual([event.seq for event in events], [1, 2, 3])
            self.assertEqual(chat.summary(db, self.ids[1])["unread"], 0)


if __name__ == "__main__":
    unittest.main()
