"""私聊持久化、权限、24小时清理与同步回归；不调用真实微信。"""
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from tests.support import DatabaseTestCase
from tests.test_manual_champions import request
from app.config import settings
from app.database import get_db, engine as application_engine
from app.models.user import User, UserRole
from app.models.team import Team
from app.models.chat import ChatMessage, ChatEvent, ChatMember, ChatConversation, ChatReport, ChatBlock
from app.schemas.chat import OpenConversation, SendMessage, ReadPosition, Preferences, ReportMessage, ResolveReport, SanctionUser
from app.services import chat_service as chat
from app.services.auth_service import create_access_token
from app.routers.chat import router
from app.migrations.chat import upgrade


class ChatTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.ledger_dir = TemporaryDirectory(prefix="cs2-chat-ledger-")
        self.addCleanup(self.ledger_dir.cleanup)
        for key, value in {"CHAT_ENABLED": True, "CHAT_WRITE_ENABLED": True, "CHAT_ALLOW_USERS": "", "CHAT_MODERATION_ENABLED": False, "WX_MOCK_LOGIN": True,
                           "CHAT_DELETION_LEDGER": str(Path(self.ledger_dir.name) / "deleted.jsonl")}.items():
            patcher = patch.object(settings, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.a, self.b, self.c = self.make_user(), self.make_user(), self.make_user()
        self.ids = [self.a.id, self.b.id, self.c.id]
        self.db.commit()
        self.cid = chat.open_conversation(self.db, self.a.id, OpenConversation(target_id=self.b.id))["id"]
        self.n = 0
        self.app = FastAPI()
        self.app.include_router(router)
        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db

    def packet(self, content="你好"):
        self.n += 1
        return SendMessage(client_id="test-message-0000-" + str(self.n), content=content, client_created_at=chat.now())

    def send(self, uid=None, body=None):
        return chat.send_message(self.db, uid or self.ids[0], self.cid, body or self.packet())["message"]

    def test_pair_is_unique_from_both_directions(self):
        result = chat.open_conversation(self.db, self.ids[1], OpenConversation(target_id=self.ids[0]))
        self.assertEqual(result["id"], self.cid)
        self.assertEqual(self.db.query(ChatConversation).count(), 1)

    def test_non_participant_cannot_read_send_or_mark_read(self):
        for fn in [lambda: chat.history(self.db, self.ids[2], self.cid),
                   lambda: chat.mark_read(self.db, self.ids[2], self.cid, 0),
                   lambda: self.send(self.ids[2])]:
            with self.assertRaises(HTTPException) as err:
                fn()
            self.assertEqual(err.exception.status_code, 404)
            self.db.rollback()

    def test_retry_deduplicates_and_content_change_conflicts(self):
        packet = self.packet()
        first = self.send(body=packet)
        second = self.send(body=packet)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.db.query(ChatMessage).count(), 1)
        with self.assertRaises(HTTPException) as err:
            self.send(body=packet.model_copy(update={"content": "其他内容"}))
        self.assertEqual(err.exception.status_code, 409)

    def test_modern_message_does_not_expire_even_when_read(self):
        msg = self.send()
        chat.mark_read(self.db, self.ids[1], self.cid, int(msg["seq"]))
        self.assertEqual(chat.cleanup(self.db, at=chat.now() + timedelta(hours=23)), 0)
        self.assertIsNotNone(self.db.get(ChatMessage, int(msg["id"])).content)

    def test_unread_older_than_day_survives_until_actual_read(self):
        packet = self.packet()
        msg = self.send(body=packet)
        old = chat.now() - timedelta(days=2)
        self.db.get(ChatMessage, int(msg["id"])).created_at = old
        self.db.commit()
        self.assertEqual(chat.cleanup(self.db), 0)
        self.assertEqual(chat.summary(self.db, self.ids[1])["unread"], 1)
        chat.mark_read(self.db, self.ids[1], self.cid, int(msg["seq"]))
        self.assertEqual(chat.cleanup(self.db), 1)
        self.db.expire_all()
        row = self.db.get(ChatMessage, int(msg["id"]))
        self.assertIsNone(row.content)
        self.assertEqual(row.state, "purged")
        self.assertEqual(chat.history(self.db, self.ids[0], self.cid)["items"], [])
        self.assertEqual(chat.list_conversations(self.db, self.ids[0])["items"][0]["latest"], None)
        replay = self.send(body=packet)
        self.assertEqual(replay["state"], "purged")
        self.assertIsNone(replay["content"])
        self.assertEqual(self.db.query(ChatMessage).count(), 1)

    def test_exact_24_hour_boundary(self):
        msg = self.send()
        created = self.db.get(ChatMessage, int(msg["id"])).created_at
        chat.mark_read(self.db, self.ids[1], self.cid, int(msg["seq"]))
        self.assertEqual(chat.cleanup(self.db, at=created + timedelta(days=1) - timedelta(microseconds=1)), 0)
        self.assertEqual(chat.cleanup(self.db, at=created + timedelta(days=1)), 1)

    def test_sending_a_reply_does_not_mark_unseen_messages_read(self):
        first = self.send()
        self.send(self.ids[1])
        self.assertEqual(chat.summary(self.db, self.ids[1])["unread"], 1)
        self.assertEqual(self.db.get(ChatMember, (self.cid, self.ids[1])).read_seq, 0)

    def test_read_position_never_moves_backwards(self):
        a, b = self.send(), self.send()
        chat.mark_read(self.db, self.ids[1], self.cid, int(b["seq"]))
        chat.mark_read(self.db, self.ids[1], self.cid, int(a["seq"]))
        self.assertEqual(self.db.get(ChatMember, (self.cid, self.ids[1])).read_seq, int(b["seq"]))

    def test_recall_and_delete_are_incremental_events_without_body(self):
        msg = self.send()
        chat.recall(self.db, self.ids[0], self.cid, int(msg["id"]))
        result = chat.changes(self.db, self.ids[1], self.cid, int(msg["seq"]))
        self.assertEqual(result["items"][0]["kind"], "recall")
        self.assertIsNone(result["items"][0]["message"]["content"])
        self.assertEqual(self.db.query(ChatEvent).count(), 2)

    def test_old_event_cursor_requires_clean_snapshot(self):
        msg = self.send()
        event = self.db.query(ChatEvent).first()
        event.created_at = chat.now() - timedelta(days=8)
        self.db.commit()
        chat.cleanup(self.db)
        result = chat.changes(self.db, self.ids[1], self.cid, 0)
        self.assertTrue(result["reset"])
        self.assertEqual(len(chat.history(self.db, self.ids[1], self.cid)["items"]), 1)

    def test_block_stops_both_sides_without_removing_history(self):
        self.send()
        chat.set_block(self.db, self.ids[1], self.ids[0], True)
        for uid in self.ids[:2]:
            with self.assertRaises(HTTPException) as err:
                self.send(uid)
            self.assertEqual(err.exception.status_code, 403)
        self.assertEqual(len(chat.history(self.db, self.ids[1], self.cid)["items"]), 1)
        chat.set_block(self.db, self.ids[1], self.ids[0], False)
        self.send()

    def test_muting_does_not_fake_read(self):
        self.send()
        chat.preferences(self.db, self.ids[1], self.cid, Preferences(muted=True))
        self.assertEqual(chat.summary(self.db, self.ids[1])["unread"], 0)
        self.assertEqual(chat.list_conversations(self.db, self.ids[1])["items"][0]["unread"], 1)

    def test_failed_moderation_writes_nothing(self):
        with patch.object(chat, "check_content", side_effect=HTTPException(503, "检测超时")):
            with self.assertRaises(HTTPException):
                self.send()
        self.assertEqual(self.db.query(ChatMessage).count(), 0)
        self.assertEqual(self.db.query(ChatEvent).count(), 0)

    def test_unverified_can_contact_admin_but_not_new_peer(self):
        self.a.is_verified = False
        self.db.commit()
        with self.assertRaises(HTTPException):
            chat.open_conversation(self.db, self.ids[0], OpenConversation(target_id=self.ids[2]))
        self.c.role = UserRole.ADMIN
        self.db.commit()
        chat.open_conversation(self.db, self.ids[0], OpenConversation(target_id=self.ids[2]))

    def test_hidden_user_cannot_read_existing_room(self):
        self.a.is_hidden = True
        self.db.commit()
        with self.assertRaises(HTTPException):
            chat.history(self.db, self.ids[0], self.cid)

    def test_report_does_not_copy_body_and_hide_clears_content(self):
        self.c.role = UserRole.ADMIN
        self.db.commit()
        msg = self.send()
        chat.report(self.db, self.ids[1], ReportMessage(message_id=int(msg["id"]), reason="骚扰"))
        r = self.db.query(ChatReport).one()
        self.assertNotIn("content", {c.name for c in ChatReport.__table__.columns})
        chat.resolve_report(self.db, self.ids[2], r.id, ResolveReport(action="hide", reason="核实违规"))
        self.assertIsNone(self.db.get(ChatMessage, int(msg["id"])).content)

    def test_migration_repeat_preserves_users_and_messages(self):
        self.send()
        upgrade(self.engine)
        upgrade(self.engine)
        self.assertEqual(self.db.query(ChatMessage).count(), 1)
        self.assertIn("chat_events", inspect(self.engine).get_table_names())

    def test_public_data_does_not_include_student_id_or_openid(self):
        result = chat.list_conversations(self.db, self.ids[0])
        self.assertNotIn("student_id", str(result))
        self.assertNotIn("wx_openid", str(result))

    def seed_room(self, user_id, peer_id, created_at):
        low, high = sorted((user_id, peer_id))
        room = ChatConversation(user_low=low, user_high=high, initiator_id=user_id,
                                last_seq=0, event_floor=0, created_at=created_at, updated_at=created_at)
        self.db.add(room)
        self.db.flush()
        self.db.add_all([ChatMember(conversation_id=room.id, user_id=uid, read_seq=0) for uid in (low, high)])
        self.db.flush()
        return room

    def test_hidden_user_cannot_list_contacts_conversations_or_summary(self):
        self.send()
        self.db.get(User, self.ids[0]).is_hidden = True
        self.db.commit()
        for fn in (lambda: chat.contacts(self.db, self.ids[0]),
                   lambda: chat.list_conversations(self.db, self.ids[0]),
                   lambda: chat.summary(self.db, self.ids[0])):
            with self.subTest(fn=fn), self.assertRaises(HTTPException) as error:
                fn()
            self.assertEqual(error.exception.status_code, 403)
            self.db.rollback()

    def test_recent_and_pinned_rooms_are_ordered_across_pages(self):
        stamp = chat.now()
        added = [self.seed_room(self.ids[0], self.make_user().id, stamp - timedelta(minutes=index + 1))
                 for index in range(34)]
        pinned_id = added[-1].id
        self.db.get(ChatMember, (pinned_id, self.ids[0])).pinned = True
        self.db.get(ChatConversation, self.cid).updated_at = stamp + timedelta(hours=1)
        self.db.commit()
        first = chat.list_conversations(self.db, self.ids[0])
        self.assertEqual([row["id"] for row in first["items"][:2]], [pinned_id, self.cid])
        self.assertEqual(len(first["items"]), 30)
        self.assertTrue(first["has_more"])
        second = chat.list_conversations(self.db, self.ids[0], first["next_cursor"])
        self.assertEqual(len(second["items"]), 5)
        self.assertFalse(second["has_more"])
        all_ids = [row["id"] for row in first["items"] + second["items"]]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(set(all_ids), {self.cid, *(room.id for room in added)})

    def test_read_ids_do_not_mark_older_or_other_conversation_messages(self):
        older = self.send()
        visible = self.send()
        other_cid = chat.open_conversation(self.db, self.ids[2], OpenConversation(target_id=self.ids[1]))["id"]
        other = chat.send_message(self.db, self.ids[2], other_cid, self.packet())["message"]
        old_stamp = chat.now() - timedelta(days=2)
        for row in self.db.query(ChatMessage).all():
            row.created_at = old_stamp
        self.db.commit()
        chat.mark_read(self.db, self.ids[1], self.cid, message_ids=[int(visible["id"]), int(other["id"])])
        self.assertIsNone(self.db.get(ChatMessage, int(older["id"])).read_at)
        self.assertIsNone(self.db.get(ChatMessage, int(other["id"])).read_at)
        self.assertIsNotNone(self.db.get(ChatMessage, int(visible["id"])).read_at)
        self.assertEqual(chat.cleanup(self.db), 1)
        self.assertEqual(chat.summary(self.db, self.ids[1])["unread"], 2)

    def test_card_changed_during_moderation_is_rejected(self):
        team = self.make_team(captain=self.a, name="检测过的队名")
        team_id = team.id
        self.db.commit()
        packet = self.packet("").model_copy(update={"kind": "team", "source_id": team_id})
        def change_title(openid, content):
            self.assertIn("检测过的队名", content)
            with Session(self.engine) as writer:
                writer.query(Team).filter_by(id=team_id).update({"name": "未经本次检测的新队名"})
                writer.commit()
        with patch.object(chat, "check_content", side_effect=change_title):
            with self.assertRaises(HTTPException) as error:
                self.send(body=packet)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.db.query(ChatMessage).count(), 0)
        self.assertEqual(self.db.query(ChatEvent).count(), 0)

    def test_blocked_send_does_not_call_content_detection(self):
        chat.set_block(self.db, self.ids[1], self.ids[0], True)
        with patch.object(chat, "check_content") as moderation:
            with self.assertRaises(HTTPException) as error:
                self.send()
        self.assertEqual(error.exception.status_code, 403)
        moderation.assert_not_called()
        self.assertEqual(self.db.query(ChatMessage).count(), 0)

    def test_new_private_chat_limit_resets_at_beijing_midnight(self):
        stamp = datetime(2036, 10, 1, 17)  # 北京时间 10月2日01:00。
        for _ in range(10):
            self.seed_room(self.ids[0], self.make_user().id, stamp - timedelta(hours=2))
        target_id = self.make_user().id
        self.db.commit()
        with patch.object(chat, "now", return_value=stamp):
            result = chat.open_conversation(self.db, self.ids[0], OpenConversation(target_id=target_id))
        self.assertGreater(result["id"], self.cid)

    def test_beijing_day_limit_counts_previous_utc_date(self):
        stamp = datetime(2036, 10, 1, 5)  # 北京时间 10月1日13:00。
        for _ in range(10):
            self.seed_room(self.ids[0], self.make_user().id, stamp - timedelta(hours=12))
        target_id = self.make_user().id
        self.db.commit()
        with patch.object(chat, "now", return_value=stamp):
            with self.assertRaises(HTTPException) as error:
                chat.open_conversation(self.db, self.ids[0], OpenConversation(target_id=target_id))
        self.assertEqual(error.exception.status_code, 429)

    def test_http_routes_authentication_and_private_mode_validation(self):
        self.assertEqual(request(self.app, "GET", "/api/chat/config")[0], 401)
        token = create_access_token(self.ids[0], "user")
        status, config = request(self.app, "GET", "/api/chat/config", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(config["retention_hours"], 24)
        self.assertIsNone(config["unread_retention_days"])
        self.assertEqual(config["self_id"], self.ids[0])
        status, _ = request(self.app, "POST", "/api/chat/conversations",
                            {"target_id": self.ids[2], "source": "group"}, token=token)
        self.assertEqual(status, 422)
        status, _ = request(self.app, "POST", f"/api/chat/conversations/{self.cid}/messages",
                            {"client_id": "valid-client-id-123", "client_created_at": chat.iso(chat.now()),
                             "kind": "image", "content": "invalid"}, token=token)
        self.assertEqual(status, 422)
        status, _ = request(self.app, "POST", "/api/chat/conversations",
                            {"target_id": self.ids[2], "source": "user", "group_members": [self.ids[1]]}, token=token)
        self.assertEqual(status, 422)
        status, _ = request(self.app, "GET", "/api/chat/admin/reports", token=token)
        self.assertEqual(status, 403)

    def test_http_message_send_and_read_update_uses_authenticated_participant(self):
        token_a = create_access_token(self.ids[0], "user")
        token_b = create_access_token(self.ids[1], "user")
        token_c = create_access_token(self.ids[2], "user")
        status, sent = request(self.app, "POST", f"/api/chat/conversations/{self.cid}/messages",
                               self.packet().model_dump(mode="json"), token=token_a)
        self.assertEqual(status, 200)
        self.assertEqual(sent["message"]["sender_id"], self.ids[0])
        status, result = request(self.app, "GET", f"/api/chat/conversations/{self.cid}/messages", token=token_b)
        self.assertEqual(status, 200)
        self.assertEqual(len(result["items"]), 1)
        status, _ = request(self.app, "PUT", f"/api/chat/conversations/{self.cid}/read",
                            {"message_ids": [int(sent["message"]["id"])]}, token=token_c)
        self.assertEqual(status, 404)
        status, _ = request(self.app, "PUT", f"/api/chat/conversations/{self.cid}/read",
                            {"message_ids": [int(sent["message"]["id"])]}, token=token_b)
        self.assertEqual(status, 200)
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(ChatMessage, int(sent["message"]["id"])).read_at)

    def counted_list(self, user_id):
        statements = []
        def count(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", count)
        try:
            with Session(self.engine, autoflush=False) as db:
                result = chat.list_conversations(db, user_id)
        finally:
            event.remove(self.engine, "before_cursor_execute", count)
        return result, len(statements)

    def test_list_query_count_is_bounded_for_one_and_thirty_rooms(self):
        incoming = self.send(self.ids[1])
        chat.preferences(self.db, self.ids[0], self.cid, Preferences(pinned=True, muted=True))
        single, one_count = self.counted_list(self.ids[0])
        self.assertEqual(len(single["items"]), 1)
        self.assertTrue(single["items"][0]["pinned"])
        self.assertTrue(single["items"][0]["muted"])
        self.assertEqual(single["items"][0]["unread"], 1)
        self.assertEqual(single["items"][0]["latest"]["id"], incoming["id"])
        self.assertEqual(chat.summary(self.db, self.ids[0])["unread"], 0)
        stamp = chat.now()
        rooms = [self.seed_room(self.ids[0], self.make_user().id, stamp - timedelta(minutes=index + 1))
                 for index in range(29)]
        blocked_peer = rooms[0].user_high
        blocking_peer = rooms[1].user_high
        self.db.add(ChatBlock(user_id=self.ids[0], target_id=blocked_peer, created_at=stamp))
        self.db.add(ChatBlock(user_id=blocking_peer, target_id=self.ids[0], created_at=stamp))
        self.db.commit()
        page, thirty_count = self.counted_list(self.ids[0])
        self.assertEqual(len(page["items"]), 30)
        self.assertFalse(page["has_more"])
        self.assertEqual(page["items"][0]["id"], self.cid)
        by_peer = {row["peer"]["id"]: row for row in page["items"]}
        self.assertTrue(by_peer[blocked_peer]["blocked"])
        self.assertTrue(by_peer[blocked_peer]["blocked_by_me"])
        self.assertTrue(by_peer[blocking_peer]["blocked"])
        self.assertFalse(by_peer[blocking_peer]["blocked_by_me"])
        self.assertEqual(page["items"][0]["unread"], 1)
        self.assertEqual(page["items"][0]["latest"]["id"], incoming["id"])
        self.assertLessEqual(one_count, 12)
        self.assertLessEqual(thirty_count, 12)
        self.assertLessEqual(thirty_count, one_count + 1)

    def test_empty_conversation_page_has_no_summaries_or_cursor(self):
        page, query_count = self.counted_list(self.ids[2])
        self.assertEqual(page, {"items": [], "has_more": False, "next_cursor": None})
        self.assertLessEqual(query_count, 12)

    def deletion_fault(self, mode):
        if mode == "unconfigured":
            return patch.object(settings, "CHAT_DELETION_LEDGER", "")
        return patch("app.services.chat_deletions.os.fsync", side_effect=OSError("模拟清单同步失败"))

    def assert_retained(self, message_id, content, event_count):
        self.db.expire_all()
        row = self.db.get(ChatMessage, message_id)
        self.assertEqual(row.state, "active")
        self.assertEqual(row.content, content)
        self.assertIsNone(row.purged_at)
        self.assertEqual(self.db.query(ChatEvent).count(), event_count)
        self.assertEqual(self.db.query(ChatEvent).filter(ChatEvent.kind.in_(["delete", "recall", "hide"])).count(), 0)

    def test_cleanup_ledger_failures_keep_body_and_return_503(self):
        sent = self.send()
        message_id = int(sent["id"])
        self.db.get(ChatMessage, message_id).created_at = chat.now() - timedelta(days=2)
        self.db.commit()
        chat.mark_read(self.db, self.ids[1], self.cid, message_ids=[message_id])
        event_count = self.db.query(ChatEvent).count()
        for mode in ("unconfigured", "fsync_failure"):
            with self.subTest(mode=mode), self.deletion_fault(mode):
                with self.assertRaises(HTTPException) as error:
                    chat.cleanup(self.db)
            self.assertEqual(error.exception.status_code, 503)
            self.assert_retained(message_id, sent["content"], event_count)

    def test_recall_ledger_failures_keep_body_and_return_503(self):
        sent = self.send()
        message_id = int(sent["id"])
        event_count = self.db.query(ChatEvent).count()
        for mode in ("unconfigured", "fsync_failure"):
            with self.subTest(mode=mode), self.deletion_fault(mode):
                with self.assertRaises(HTTPException) as error:
                    chat.recall(self.db, self.ids[0], self.cid, message_id)
            self.assertEqual(error.exception.status_code, 503)
            self.assert_retained(message_id, sent["content"], event_count)

    def test_admin_hide_ledger_failures_keep_body_and_report_pending(self):
        self.c.role = UserRole.ADMIN
        self.db.commit()
        sent = self.send()
        message_id = int(sent["id"])
        chat.report(self.db, self.ids[1], ReportMessage(message_id=message_id, reason="待审核内容"))
        report_id = self.db.query(ChatReport).one().id
        event_count = self.db.query(ChatEvent).count()
        for mode in ("unconfigured", "fsync_failure"):
            with self.subTest(mode=mode), self.deletion_fault(mode):
                with self.assertRaises(HTTPException) as error:
                    chat.resolve_report(self.db, self.ids[2], report_id, ResolveReport(action="hide", reason="已核实"))
            self.assertEqual(error.exception.status_code, 503)
            self.assert_retained(message_id, sent["content"], event_count)
            self.assertEqual(self.db.get(ChatReport, report_id).status, "pending")
            self.assertIsNone(self.db.get(ChatReport, report_id).resolved_at)

    def test_non_admin_cannot_use_report_or_sanction_services_directly(self):
        sent = self.send()
        message_id = int(sent["id"])
        chat.report(self.db, self.ids[1], ReportMessage(message_id=message_id, reason="权限回归"))
        report_id = self.db.query(ChatReport).one().id
        operations = (
            lambda: chat.admin_reports(self.db, self.ids[2]),
            lambda: chat.resolve_report(self.db, self.ids[2], report_id, ResolveReport(action="hide", reason="非法处理")),
            lambda: chat.sanction(self.db, self.ids[2], SanctionUser(user_id=self.ids[0], hours=1, reason="非法禁言")),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(HTTPException) as error:
                operation()
            self.assertEqual(error.exception.status_code, 403)
            self.db.rollback()
        self.assertEqual(self.db.get(ChatMessage, message_id).state, "active")
        self.assertEqual(self.db.get(ChatMessage, message_id).content, sent["content"])
        self.assertEqual(self.db.get(ChatReport, report_id).status, "pending")

    def test_database_errors_never_include_private_message_parameters(self):
        # 使用隔离配置加载的真实应用引擎，故意触发驱动错误而非模拟异常文本。
        private_body = "私聊正文不应进入数据库异常日志-隐私回归"
        with application_engine.connect() as connection:
            with self.assertRaises(SQLAlchemyError) as error:
                connection.execute(text("SELECT :body / FROM"), {"body": private_body})
        self.assertNotIn(private_body, str(error.exception))
        self.assertIn("parameters hidden", str(error.exception))
