"""Retired QQ reminders never send; normal invitations and applications continue."""
import json
from unittest.mock import Mock, patch

from sqlalchemy.orm import Session

from tests.support import DatabaseTestCase
from app.config import settings
from app.models.recruitment import WechatOutbox, WechatSubscription
from app.services import team_service as teams, wechat_service as wx


KIND = "captain_qq_reminder"
TEMPLATE = "shared-team-notice-unit-test"
OTHER_TEMPLATE = "invitation-only-unit-test"
SPEC = {"template_id": TEMPLATE, "fields": {"phrase6": "event", "thing7": "team_name"}}
CONFIG = {kind: SPEC for kind in ("team_invitation", "team_application", KIND)}


class CaptainQQReminderTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        for key, value in dict(
            WX_SUBSCRIBE_ENABLED=True, WX_MOCK_LOGIN=False,
            WX_APPID="test-app", WX_SECRET="test-secret",
            WX_SUBSCRIBE_TEMPLATES=json.dumps(CONFIG), WX_SUBSCRIBE_ENV="formal",
        ).items():
            setting = patch.object(settings, key, value)
            setting.start()
            self.addCleanup(setting.stop)
        transport = patch.object(wx, "_post", side_effect=AssertionError("No WeChat network calls in tests"))
        self.network = transport.start()
        self.addCleanup(transport.stop)
        self.addCleanup(self.network.assert_not_called)
        self.factory = lambda: Session(self.engine, autoflush=False)

    def captain_team(self, **kwargs):
        captain = self.make_user(wx_openid=f"real-captain-{self._next()}")
        team = self.make_team(captain=captain, **kwargs)
        self.db.commit()
        return captain, team

    def accept(self, user, db=None, template=TEMPLATE):
        return wx.record_choice(db or self.db, user.id, {template: "accept"})

    def test_legacy_template_config_exposes_only_active_invitation_and_application_kinds(self):
        config = wx.configuration()
        self.assertEqual({r["kind"] for r in config["templates"]}, {"team_invitation", "team_application"})
        self.assertNotIn("补充QQ", json.dumps(config, ensure_ascii=False))

    def test_accepting_shared_template_does_not_create_qq_reminders(self):
        captain, _ = self.captain_team()
        self.accept(captain)
        self.accept(captain)
        self.assertTrue(self.db.get(WechatSubscription, (captain.id, TEMPLATE)).enabled)
        self.assertEqual(self.db.query(WechatOutbox).count(), 0)

    def test_direct_enqueue_of_retired_kind_is_ignored(self):
        captain, team = self.captain_team()
        self.accept(captain)
        wx.enqueue(self.db, KIND, team.id, captain.id, team, captain)
        self.db.commit()
        self.assertEqual(self.db.query(WechatOutbox).count(), 0)

    def test_existing_pending_reminder_is_skipped_without_wechat_delivery(self):
        captain, team = self.captain_team()
        self.accept(captain)
        row = WechatOutbox(user_id=captain.id, kind=KIND, source_id=team.id, template_id=TEMPLATE,
                           payload={"data": {"phrase6": {"value": "补充QQ"}}, "page": "pages/team/team"})
        self.db.add(row)
        self.db.commit()
        sender = Mock(return_value=0)
        wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        sender.assert_not_called()
        self.db.refresh(row)
        self.assertEqual(row.status, "skipped")

    def test_retired_only_configuration_is_disabled(self):
        with patch.object(settings, "WX_SUBSCRIBE_TEMPLATES", json.dumps({KIND: SPEC})):
            self.assertEqual(wx.configuration(), {"enabled": False, "templates": []})
            sender = Mock()
            wx.deliver_pending(self.factory, sender)
            sender.assert_not_called()

    def test_invitation_and_application_preserve_routes_and_do_not_send_qq_reminder(self):
        captain, team = self.captain_team()
        player = self.make_user(wx_openid="real-player")
        self.db.commit()
        self.accept(captain)
        self.accept(player)
        teams.invite_player(self.db, team.id, captain.id, player.id)
        teams.apply_join_team(self.db, team.id, player.id, "private introduction")
        sender = Mock(return_value=0)
        wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 2)
        sent = {call.args[0]["data"]["phrase6"]["value"]: call.args[0] for call in sender.call_args_list}
        self.assertEqual(set(sent), {"入队邀请", "入队申请"})
        self.assertEqual(sent["入队邀请"]["page"], "pages/messages/messages")
        self.assertEqual(sent["入队申请"]["page"], "pages/team/team")
        self.assertNotIn("private introduction", json.dumps(sent))
