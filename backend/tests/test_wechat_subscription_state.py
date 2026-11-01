"""订阅回调、持久状态与发送竞态。网络均为mock，不使用真实账号/密钥。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.support import DatabaseTestCase
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models.user import User
from app.models.team import TeamInvitation
from app.models.recruitment import (WechatSubscription, WechatOutbox, WechatSubscriptionAttempt,
                                    WechatSubscriptionState, WechatSubscriptionDelivery)
from app.routers.notifications import router
from app.services.auth_service import get_current_user
from app.services import wechat_service as wx, team_service as teams, notification_service
from app.migrations.recruitment_notifications import upgrade


INVITE = "invitation-template-test-2026"
APPLY = "application-template-test-2026"
CONFIG = {kind: {"template_id": tid, "fields": {"thing1": "team_name", "phrase2": "event"}}
          for kind, tid in (("team_invitation", INVITE), ("team_application", APPLY))}


def request(app, path, body=None, method="GET"):
    messages = []
    async def receive():
        return {"type": "http.request", "body": json.dumps(body or {}).encode(), "more_body": False}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(payload)


class WechatSubscriptionStateTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        for key, value in {"WX_SUBSCRIBE_ENABLED": True, "WX_MOCK_LOGIN": False,
                           "WX_APPID": "isolated-app", "WX_SECRET": "isolated-secret",
                           "WX_SUBSCRIBE_TEMPLATES": json.dumps(CONFIG), "WX_SUBSCRIBE_ENV": "formal"}.items():
            value_patch = patch.object(settings, key, value)
            value_patch.start()
            self.addCleanup(value_patch.stop)
        network = patch.object(wx, "_post", side_effect=AssertionError("No real WeChat calls"))
        transport = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(transport.assert_not_called)
        self.captain = self.make_user(wx_openid="actual-like-captain")
        self.player = self.make_user(wx_openid="actual-like-player")
        self.team = self.make_team(captain=self.captain)
        self.db.commit()
        self.factory = lambda: Session(self.engine, autoflush=False)

    def accept(self, user=None, tid=INVITE, attempt="gesture-0000000001", db=None):
        return wx.record_choice(db or self.db, (user or self.player).id, {tid: "accept"}, attempt_id=attempt)

    def item(self, user=None, tid=INVITE):
        self.db.expire_all()
        return next(x for x in wx.subscription_state(self.db, (user or self.player).id)["templates"] if x["template_id"] == tid)

    def invitation(self):
        return teams.invite_player(self.db, self.team.id, self.captain.id, self.player.id)

    def test_initial_state_is_readonly_unknown_and_does_not_expose_private_fields(self):
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            result = wx.subscription_state(self.db, self.player.id)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))
        for item in result["templates"]:
            self.assertEqual(item["actual_remaining"], "unknown")
            self.assertEqual(item["successful_send_count"], 0)
            self.assertTrue(item["needs_resubscribe"])
            self.assertFalse(item["enabled"])
        serialized = json.dumps(result)
        for value in ("isolated-secret", self.player.student_id, self.player.wx_openid):
            self.assertNotIn(value, serialized)
        self.assertEqual(self.db.query(WechatSubscriptionState).count(), 0)

    def test_duplicate_attempt_is_one_version_and_does_not_restore_an_older_choice(self):
        first = self.accept()
        duplicate = self.accept()
        self.assertFalse(first["idempotent"])
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(self.item()["grant_version"], 1)
        self.assertEqual(self.db.query(WechatSubscriptionAttempt).count(), 1)
        wx.record_choice(self.db, self.player.id, {INVITE: "reject"}, attempt_id="gesture-reject-002")
        self.accept()
        self.assertFalse(self.item()["enabled"])
        self.assertEqual(self.item()["grant_version"], 1)
        self.assertTrue(self.item()["has_accepted"])

    def test_same_attempt_with_different_choices_is_a_conflict_without_changes(self):
        self.accept()
        with self.assertRaises(HTTPException) as caught:
            wx.record_choice(self.db, self.player.id, {INVITE: "reject"}, attempt_id="gesture-0000000001")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(self.item()["enabled"])

    def test_multiple_templates_and_accounts_have_independent_authorization(self):
        self.accept()
        self.accept(self.captain)
        wx.record_choice(self.db, self.player.id, {INVITE: "accept", APPLY: "reject"}, attempt_id="gesture-multi-002")
        self.assertEqual(self.item()["grant_version"], 2)
        self.assertEqual(self.item(self.captain)["grant_version"], 1)
        self.assertFalse(self.item(tid=APPLY)["enabled"])
        self.assertEqual(self.item(tid=APPLY)["grant_version"], 0)
        self.assertEqual(self.db.query(WechatSubscriptionAttempt).count(), 3)
        self.invitation()
        self.assertEqual(self.db.query(WechatOutbox).one().user_id, self.player.id)

    def test_legacy_callbacks_are_compatible_without_inventing_counted_grants(self):
        for _ in range(2):
            result = self.accept(attempt=None)
            self.assertFalse(result["attempt_recorded"])
        item = self.item()
        self.assertTrue(item["enabled"])
        self.assertFalse(item["needs_resubscribe"])
        self.assertEqual(item["grant_version"], 0)
        self.assertEqual(self.db.query(WechatSubscriptionAttempt).count(), 0)
        self.invitation()
        wx.deliver_pending(self.factory, Mock(return_value=0))
        self.accept(attempt=None)
        self.assertTrue(self.item()["needs_resubscribe"])
        self.assertEqual(self.item()["grant_version"], 0)

    def test_success_persists_one_use_and_keeps_local_enabled_with_unknown_remaining(self):
        self.accept()
        self.invitation()
        sender = Mock(return_value=0)
        wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        item = self.item()
        self.assertEqual(item["last_delivery_status"], "sent")
        self.assertTrue(item["enabled"])
        self.assertTrue(item["needs_resubscribe"])
        self.assertEqual(item["successful_send_count"], 1)
        self.assertEqual(item["actual_remaining"], "unknown")
        self.assertIn("不代表全部次数", item["message"])
        receipt = self.db.query(WechatSubscriptionDelivery).one()
        self.assertEqual((receipt.status, receipt.grant_version), ("sent", 1))

    def test_new_grant_while_old_send_is_in_flight_is_not_overwritten_by_success(self):
        self.accept()
        self.invitation()
        def sender(_payload):
            with self.factory() as other:
                self.accept(attempt="gesture-fresh-0002", db=other)
            return 0
        wx.deliver_pending(self.factory, sender)
        item = self.item()
        self.assertEqual(item["grant_version"], 2)
        self.assertFalse(item["needs_resubscribe"])
        self.assertEqual(item["successful_send_count"], 1)
        self.assertEqual(self.db.query(WechatSubscriptionDelivery).one().grant_version, 1)

    def test_new_grant_while_old_send_is_rejected_is_not_disabled(self):
        self.accept()
        self.invitation()
        def sender(_payload):
            with self.factory() as other:
                self.accept(attempt="gesture-fresh-0002", db=other)
            return 43101
        wx.deliver_pending(self.factory, sender)
        item = self.item()
        self.assertTrue(item["enabled"])
        self.assertFalse(item["needs_resubscribe"])
        self.assertEqual(item["grant_version"], 2)
        self.assertEqual(item["successful_send_count"], 0)

    def test_revoke_while_old_send_is_in_flight_stays_revoked(self):
        self.accept()
        self.invitation()
        def sender(_payload):
            with self.factory() as other:
                wx.record_choice(other, self.player.id, {INVITE: "ban"}, attempt_id="gesture-banned-0002")
            return 0
        wx.deliver_pending(self.factory, sender)
        self.assertFalse(self.item()["enabled"])
        self.assertTrue(self.item()["needs_resubscribe"])

    def test_failed_43101_requests_regrant_without_rolling_back_station_messages(self):
        self.accept()
        invitation = self.invitation()
        sender = Mock(return_value=43101)
        wx.deliver_pending(self.factory, sender)
        item = self.item()
        self.assertEqual(item["last_delivery_status"], "failed")
        self.assertTrue(item["needs_resubscribe"])
        self.assertEqual(item["successful_send_count"], 0)
        self.assertEqual(notification_service.personal_summary(self.db, self.player.id)["invitation_count"], 1)
        self.assertIsNotNone(self.db.get(TeamInvitation, invitation.id))
        self.accept(attempt="gesture-fresh-0002")
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertFalse(self.item()["needs_resubscribe"])

    def test_timeout_is_unknown_not_consumption_and_never_retried(self):
        self.accept()
        self.invitation()
        sender = Mock(side_effect=TimeoutError("access_token=must-not-be-stored"))
        wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        item = self.item()
        self.assertEqual(item["last_delivery_status"], "unknown")
        self.assertEqual(item["successful_send_count"], 0)
        self.assertFalse(item["needs_resubscribe"])
        self.assertEqual(self.db.query(WechatSubscriptionDelivery).one().status, "unknown")
        self.assertNotIn("must-not-be-stored", json.dumps(item))

    def test_malformed_response_is_not_success_or_an_exposed_raw_error(self):
        for code in (None, False, {}, "0"):
            with self.subTest(code=code):
                player = self.make_user(wx_openid="valid-like-" + str(self._next()))
                self.db.commit()
                self.accept(player)
                teams.invite_player(self.db, self.team.id, self.captain.id, player.id)
                sender = Mock(return_value=code)
                wx.deliver_pending(self.factory, sender)
                wx.deliver_pending(self.factory, sender)
                self.assertEqual(sender.call_count, 1)
                item = self.item(player)
                self.assertEqual(item["successful_send_count"], 0)
                self.assertEqual(item["last_delivery_status"], "unknown")
                self.assertEqual(item["last_error_code"], "provider_response_invalid")
                row = self.db.query(WechatOutbox).filter_by(user_id=player.id).one()
                row.error_code = "secret-containing-legacy-message"
                self.db.commit()
                self.assertEqual(self.item(player)["last_error_code"], "delivery_error")

    def test_interrupted_receipt_is_unknown_and_late_success_cannot_reopen_it(self):
        self.accept()
        self.invitation()
        nested_sender = Mock(return_value=0)
        def sender(_payload):
            with self.factory() as other:
                row = other.query(WechatOutbox).one()
                row.updated_at = datetime.now() - timedelta(minutes=6)
                other.commit()
            wx.deliver_pending(self.factory, nested_sender)
            return 0
        wx.deliver_pending(self.factory, sender)
        nested_sender.assert_not_called()
        self.assertEqual(self.item()["last_delivery_status"], "unknown")
        self.assertEqual(self.item()["successful_send_count"], 0)

    def test_manual_reset_of_unknown_outbox_does_not_trigger_automatic_resend(self):
        self.accept()
        self.invitation()
        wx.deliver_pending(self.factory, Mock(side_effect=TimeoutError))
        row = self.db.query(WechatOutbox).one()
        row.status = "pending"
        self.db.commit()
        sender = Mock(return_value=0)
        wx.deliver_pending(self.factory, sender)
        sender.assert_not_called()
        self.assertEqual(self.item()["last_delivery_status"], "unknown")

    def test_receipt_storage_failure_after_send_never_requeues_the_successful_network_call(self):
        self.accept()
        self.invitation()
        sender = Mock(return_value=0)
        with patch.object(wx, "_finish_delivery", side_effect=OperationalError("COMMIT", {}, RuntimeError("simulated failure"))):
            with self.assertRaises(OperationalError):
                wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(self.item()["last_delivery_status"], "sending")
        self.assertEqual(self.item()["successful_send_count"], 0)

    def test_actual_terminal_commit_failure_rolls_back_receipt_but_not_durable_claim(self):
        self.accept()
        invitation = self.invitation()
        finish = wx._finish_delivery
        def fail_commit(db, *args):
            with patch.object(db, "commit", side_effect=OperationalError("COMMIT", {}, RuntimeError("storage failure"))):
                return finish(db, *args)
        sender = Mock(return_value=0)
        with patch.object(wx, "_finish_delivery", side_effect=fail_commit), self.assertRaises(OperationalError):
            wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(self.item()["last_delivery_status"], "sending")
        self.assertEqual(self.db.query(WechatSubscriptionDelivery).one().status, "sending")
        self.assertIsNotNone(self.db.get(TeamInvitation, invitation.id))

    def test_lost_terminal_commit_response_leaves_one_success_not_another_send(self):
        self.accept()
        self.invitation()
        finish = wx._finish_delivery
        def lost_response(db, *args):
            commit = db.commit
            def committed_then_error():
                commit()
                raise OperationalError("COMMIT", {}, RuntimeError("commit response lost"))
            with patch.object(db, "commit", side_effect=committed_then_error):
                return finish(db, *args)
        sender = Mock(return_value=0)
        with patch.object(wx, "_finish_delivery", side_effect=lost_response), self.assertRaises(OperationalError):
            wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(self.item()["successful_send_count"], 1)
        self.assertEqual(self.db.query(WechatSubscriptionDelivery).one().status, "sent")

    def test_new_template_does_not_inherit_old_template_grants_or_usage(self):
        self.accept()
        self.invitation()
        wx.deliver_pending(self.factory, Mock(return_value=0))
        new_id = "replacement-template-test-2026"
        changed = dict(CONFIG, team_invitation=dict(CONFIG["team_invitation"], template_id=new_id))
        with patch.object(settings, "WX_SUBSCRIBE_TEMPLATES", json.dumps(changed)):
            fresh = self.item(tid=new_id)
            self.assertFalse(fresh["enabled"])
            self.assertEqual(fresh["successful_send_count"], 0)
            self.assertTrue(fresh["needs_resubscribe"])
            # Replaying a known callback remains idempotent, but cannot grant the replacement template.
            self.assertTrue(self.accept()["idempotent"])
            self.assertFalse(self.item(tid=new_id)["enabled"])
            with self.assertRaises(HTTPException):
                self.accept(attempt="new-gesture-old-template")

    def test_old_sent_outbox_remains_visible_without_backfill_or_migrating_private_rows(self):
        old = datetime.now() - timedelta(minutes=2)
        self.db.add(WechatSubscription(user_id=self.player.id, template_id=INVITE, enabled=True, updated_at=old))
        self.db.add(WechatOutbox(user_id=self.player.id, kind="team_invitation", source_id=909,
                                 template_id=INVITE, payload={}, status="sent", updated_at=old + timedelta(seconds=1)))
        self.db.commit()
        item = self.item()
        self.assertEqual(item["successful_send_count"], 1)
        self.assertTrue(item["needs_resubscribe"])
        self.assertEqual(self.db.query(WechatSubscriptionDelivery).count(), 0)
        self.assertEqual(self.db.query(WechatSubscriptionState).count(), 0)

    def test_migration_repeats_without_changing_legacy_choice_or_recorded_attempt(self):
        self.accept()
        self.invitation()
        wx.deliver_pending(self.factory, Mock(return_value=0))
        for _ in range(2):
            upgrade(self.engine)
        self.assertEqual(self.db.query(WechatSubscriptionAttempt).count(), 1)
        self.assertEqual(self.db.query(WechatSubscriptionDelivery).one().status, "sent")
        self.assertTrue(self.item()["enabled"])

    def test_api_requires_login_returns_only_own_state_and_accepts_optional_attempt(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        status, _ = request(app, "/api/notifications/wechat/state")
        self.assertEqual(status, 401)
        self.accept(self.captain)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=self.player.id)
        status, body = request(app, "/api/notifications/wechat/state")
        self.assertEqual(status, 200)
        self.assertTrue(all(not x["enabled"] for x in body["templates"]))
        status, body = request(app, "/api/notifications/wechat/subscriptions",
                               {"choices": {INVITE: "accept"}, "attempt_id": "api-gesture-0000001"}, "POST")
        self.assertEqual(status, 200)
        self.assertTrue(body["attempt_recorded"])
        status, _ = request(app, "/api/notifications/wechat/subscriptions",
                             {"choices": {INVITE: "accept"}, "attempt_id": "x"}, "POST")
        self.assertEqual(status, 422)
        status, body = request(app, "/api/notifications/wechat/subscriptions", {"choices": {APPLY: "accept"}}, "POST")
        self.assertEqual(status, 200)
        self.assertFalse(body["attempt_recorded"])

    def test_two_independent_connections_deduplicate_the_same_callback(self):
        local = Path(__file__).resolve().parents[2] / ".local"
        local.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local, prefix="wechat-subscribe-test-") as directory:
            engine = create_engine("sqlite:///" + (Path(directory) / "test.sqlite").as_posix(),
                                   connect_args={"check_same_thread": False, "timeout": 30})
            try:
                Base.metadata.create_all(engine)
                with Session(engine) as db:
                    user = User(nickname="Concurrent subscriber")
                    db.add(user)
                    db.commit()
                    user_id = user.id
                barrier = threading.Barrier(2)
                def worker(_n):
                    with Session(engine, autoflush=False) as db:
                        barrier.wait(timeout=10)
                        return wx.record_choice(db, user_id, {INVITE: "accept"}, attempt_id="same-concurrent-gesture")
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(worker, range(2)))
                self.assertEqual(sorted(r["idempotent"] for r in results), [False, True])
                with Session(engine) as db:
                    self.assertEqual(db.query(WechatSubscriptionAttempt).count(), 1)
                    self.assertEqual(db.get(WechatSubscriptionState, (user_id, INVITE)).grant_version, 1)
            finally:
                engine.dispose()
