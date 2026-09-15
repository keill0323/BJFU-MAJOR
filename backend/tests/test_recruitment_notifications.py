"""Recruitment lifecycle and real team transactions; WeChat transport is always mocked."""
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from fastapi import HTTPException
from tests.support import DatabaseTestCase
from app.config import settings
from app.models.recruitment import RecruitmentPost, WechatSubscription, WechatOutbox
from app.models.team import TeamApplication, TeamInvitation, TeamStatus, TeamMember
from app.models.match import Registration, TeamProgress, StageStatus, MatchStatus
from app.services import recruitment_service as hall, wechat_service as wx, team_service as teams
from app.migrations.recruitment_notifications import upgrade
from app.services import notification_service as notices
from app.models.notification import ScheduleNotification

TEMPLATE = "template-for-unit-tests-only"
CONFIG = {kind: {"template_id": TEMPLATE, "fields": {"thing1": "team_name", "name2": "actor_name", "phrase3": "event", "time4": "time"}} for kind in ("team_invitation", "team_application")}


class RecruitmentTests(DatabaseTestCase):
    def test_finished_seeded_match_does_not_block_application_or_invitation_or_rewrite_history(self):
        for kind in ("application", "invitation", "direct"):
            with self.subTest(kind=kind):
                team = self.make_team()
                match = self.make_match(status=MatchStatus.FINISHED, team_size=1)
                reg = Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id)
                progress = TeamProgress(match_id=match.id, team_id=team.id, seed=3, group_name="上区", stage=StageStatus.PLAYOFF)
                self.db.add_all([reg, progress])
                player = self.make_user(is_verified=False, rank=None)
                self.db.commit()
                if kind == "application":
                    application = teams.apply_join_team(self.db, team.id, player.id)
                    teams.approve_application(self.db, application.id, team.captain_id)
                elif kind == "invitation":
                    invitation = teams.invite_player(self.db, team.id, team.captain_id, player.id)
                    teams.accept_invitation(self.db, invitation.id, player.id)
                else:
                    teams.join_team(self.db, team.id, player.id)
                self.assertEqual(self.db.query(TeamMember).filter_by(user_id=player.id).one().team_id, team.id)
                self.assertEqual([r.user_id for r in self.db.query(Registration).filter_by(match_id=match.id)], [team.captain_id])
                self.assertEqual(progress.seed, 3)
                self.assertEqual(progress.group_name, "上区")

    def test_current_competition_still_checks_capacity_and_certification_despite_finished_history(self):
        team = self.make_team()
        old = self.make_match(status=MatchStatus.FINISHED, team_size=1)
        active = self.make_match(team_size=2)
        for match in (old, active):
            self.db.add(Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id))
        pending = self.make_user(is_verified=False)
        player, overflow = self.make_user(), self.make_user()
        self.db.commit()
        with self.assertRaises(HTTPException):
            teams.apply_join_team(self.db, team.id, pending.id)
        application = teams.apply_join_team(self.db, team.id, player.id)
        teams.approve_application(self.db, application.id, team.captain_id)
        self.assertEqual(self.db.query(Registration).filter_by(match_id=old.id).count(), 1)
        self.assertEqual(self.db.query(Registration).filter_by(match_id=active.id).count(), 2)
        with self.assertRaises(HTTPException):
            teams.invite_player(self.db, team.id, team.captain_id, overflow.id)

    def test_existing_seeded_team_retains_original_member_join_policy(self):
        team = self.make_team()
        match = self.make_match(status=MatchStatus.IN_PROGRESS, team_size=5)
        self.db.add(Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id))
        self.db.add(TeamProgress(match_id=match.id, team_id=team.id, seed=2, stage=StageStatus.LEGEND))
        player = self.make_user()
        self.db.commit()
        invitation = teams.invite_player(self.db, team.id, team.captain_id, player.id)
        teams.accept_invitation(self.db, invitation.id, player.id)
        self.assertEqual(self.db.query(Registration).filter_by(match_id=match.id, user_id=player.id).one().team_id, team.id)

    def test_joined_member_keeps_identity_visible_without_student_id(self):
        from app.schemas.team import TeamInfo
        captain = self.make_user(identity="senior")
        newcomer = self.make_user(identity="new_student")
        team = self.make_team(captain=captain)
        self.db.commit()
        application = teams.apply_join_team(self.db, team.id, newcomer.id)
        self.assertEqual(teams.get_team_applications(self.db, team.id, captain.id)[0]["identity"], "new_student")
        self.assertEqual(notices.team_applications(self.db, captain.id)[0]["identity"], "new_student")
        teams.approve_application(self.db, application.id, captain.id)
        self.db.expire_all()
        payload = TeamInfo.model_validate(team).model_dump()
        identities = {m["user_id"]: m["identity"] for m in payload["members"]}
        self.assertEqual(identities, {captain.id: "senior", newcomer.id: "new_student"})
        self.assertNotIn("student_id", json.dumps(payload, default=str))

    def test_player_directory_includes_unregistered_teamed_and_unverified_users(self):
        first = self.make_user(identity="new_student", nickname="新同学")
        senior = self.make_user(identity="senior", nickname="老同学")
        pending = self.make_user(is_verified=False, identity="new_student")
        hidden = self.make_user(is_hidden=True)
        self.make_team(captain=senior)
        self.db.commit()
        entries = {p["id"]: p for p in hall.public_players(self.db)["items"]}
        self.assertEqual(set(entries), {first.id, senior.id, pending.id})
        self.assertEqual(entries[first.id]["identity"], "new_student")
        self.assertEqual(entries[senior.id]["identity"], "senior")
        self.assertTrue(entries[senior.id]["has_team"])
        self.assertIsNone(entries[pending.id]["identity"])
        self.assertEqual(self.db.query(Registration).count(), 0)
        for item in entries.values():
            self.assertFalse({"student_id", "ai_student_id", "wx_openid", "verify_image", "rank_image"} & item.keys())

    def test_directory_identity_uses_verified_effective_identity_and_manual_override(self):
        from app.services.auth_service import datetime as auth_datetime
        year = auth_datetime.now().year % 100
        new = self.make_user(student_id=f"{year:02d}1234567", identity=None)
        old = self.make_user(student_id="221234567", identity=None)
        master = self.make_user(student_id="graduate-custom", identity="new_student")
        unknown = self.make_user(student_id="other-format", identity=None)
        self.db.commit()
        entries = {p["id"]: p for p in hall.public_players(self.db)["items"]}
        self.assertEqual(entries[new.id]["identity"], "new_student")
        self.assertEqual(entries[old.id]["identity"], "senior")
        self.assertEqual(entries[master.id]["identity"], "new_student")
        self.assertIsNone(entries[unknown.id]["identity"])

    def test_directory_search_does_not_search_student_id_and_cursor_is_stable(self):
        first = self.make_user(nickname="林间", student_id="261111111")
        second = self.make_user(nickname="白桦", game_id="ForestAWP")
        literal = self.make_user(nickname="100%")
        self.db.commit()
        self.assertEqual(hall.public_players(self.db, keyword="261111111")["items"], [])
        self.assertEqual(hall.public_players(self.db, keyword="Forest")["items"][0]["id"], second.id)
        self.assertEqual(len(hall.public_players(self.db, keyword="%")["items"]), 1)
        page = hall.public_players(self.db, limit=1)
        self.make_user()
        self.db.commit()
        older = hall.public_players(self.db, before_id=page["next_cursor"], limit=1)
        self.assertEqual(older["items"][0]["id"], second.id)

    def test_captain_gets_applicant_details_but_no_student_id_or_credentials(self):
        team = self.make_team()
        player = self.make_user(student_id="260123456", ai_student_id="260123456", identity="new_student",
            game_id="ForestPlayer", rank="A+", individual_rating=35, user_description="晚间训练", verify_image="private-verification.png")
        outsider = self.make_user()
        self.db.commit()
        teams.apply_join_team(self.db, team.id, player.id, "我可以打辅助")
        from app.schemas.team import TeamApplicationInfo, ApplyJoinRequest, TeamInfo, TalentMarketItem
        from pydantic import ValidationError
        for result in (teams.get_team_applications(self.db, team.id, team.captain_id)[0], notices.team_applications(self.db, team.captain_id)[0]):
            payload = TeamApplicationInfo.model_validate(result).model_dump()
            self.assertEqual(payload["rank"], "A+")
            self.assertEqual(payload["individual_rating"], 35)
            self.assertEqual(payload["identity"], "new_student")
            self.assertEqual(payload["message"], "我可以打辅助")
            self.assertEqual(payload["user_description"], "晚间训练")
            serialized = json.dumps(result, default=str)
            for secret in ("student_id", "wx_openid", "verify_image", "rank_image", "260123456", "private-verification.png"):
                self.assertNotIn(secret, serialized)
        self.assertNotIn("student_id", TeamInfo.model_validate(team).model_dump_json())
        self.assertNotIn("student_id", TalentMarketItem.model_validate(player).model_dump_json())
        with self.assertRaises(HTTPException) as error:
            teams.get_team_applications(self.db, team.id, outsider.id)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(ValidationError):
            ApplyJoinRequest(team_id=team.id, message="字" * 201)
        player.is_verified = False
        self.db.commit()
        self.assertIsNone(teams.get_team_applications(self.db, team.id, team.captain_id)[0]["identity"])

    def test_home_summary_is_private_and_processed_requests_disappear(self):
        team, player, other = self.make_team(), self.make_user(), self.make_user()
        self.db.commit()
        invitation = teams.invite_player(self.db, team.id, team.captain_id, player.id)
        application = teams.apply_join_team(self.db, team.id, player.id, "想加入")
        self.db.add(ScheduleNotification(user_id=player.id, match_id=1, round_id=2, kind="proposed",
            match_name="赛事", team1_name="一队", team2_name="二队", scheduled_time=datetime.now(), created_at=datetime.now()))
        self.db.commit()
        self.assertEqual(notices.personal_summary(self.db, other.id)["total"], 0)
        self.assertEqual(notices.personal_summary(self.db, player.id)["total"], 2)
        self.assertEqual(notices.personal_summary(self.db, team.captain_id)["application_count"], 1)
        self.assertEqual(notices.team_applications(self.db, other.id), [])
        self.assertEqual(notices.team_applications(self.db, team.captain_id)[0]["message"], "想加入")
        teams.reject_application(self.db, application.id, team.captain_id)
        teams.reject_invitation(self.db, invitation.id, player.id)
        self.assertEqual(notices.personal_summary(self.db, team.captain_id)["total"], 0)
        self.assertEqual(notices.personal_summary(self.db, player.id)["invitation_count"], 0)

    def test_captain_can_publish_before_any_event_registration_and_edit_close_reopen(self):
        team = self.make_team()
        self.db.commit()
        hall.save_post(self.db, team.id, team.captain_id, "  缺步枪手，晚间训练  ")
        self.assertEqual(hall.public_posts(self.db)["items"][0]["content"], "缺步枪手，晚间训练")
        self.assertEqual(self.db.query(Registration).count(), 0)
        hall.save_post(self.db, team.id, team.captain_id, "已更新")
        self.assertEqual(self.db.query(RecruitmentPost).count(), 1)
        hall.close_post(self.db, team.id, team.captain_id)
        self.assertEqual(hall.public_posts(self.db)["items"], [])
        self.assertEqual(hall.my_post(self.db, team.captain_id)["content"], "已更新")
        hall.save_post(self.db, team.id, team.captain_id, "重新招募")
        self.assertEqual(len(hall.public_posts(self.db)["items"]), 1)

    def test_permissions_review_hidden_and_blank_validation(self):
        team, other = self.make_team(), self.make_user()
        self.db.commit()
        for action in (lambda: hall.save_post(self.db, team.id, other.id, "招募"), lambda: hall.close_post(self.db, team.id, other.id)):
            with self.assertRaises(HTTPException) as error:
                action()
            self.assertEqual(error.exception.status_code, 403)
        for content in ("  ", "a" * 501):
            with self.assertRaises(HTTPException):
                hall.save_post(self.db, team.id, team.captain_id, content)
        for attr, value in (("status", TeamStatus.PENDING), ("is_hidden", True)):
            setattr(team, attr, value)
            self.db.commit()
            with self.assertRaises(HTTPException):
                hall.save_post(self.db, team.id, team.captain_id, "招募")

    def test_feed_filters_hidden_and_rejected_teams_and_captains_no_private_fields(self):
        team = self.make_team()
        self.db.commit()
        hall.save_post(self.db, team.id, team.captain_id, "招募")
        item = hall.public_posts(self.db)["items"][0]
        self.assertEqual(item["member_count"], 1)
        self.assertFalse({"student_id", "wx_openid", "captain_id"} & item.keys())
        for obj, attr, value in ((team, "is_hidden", True), (team, "status", TeamStatus.REJECTED), (team.captain, "is_hidden", True)):
            old = getattr(obj, attr)
            setattr(obj, attr, value)
            self.db.commit()
            self.assertEqual(hall.public_posts(self.db)["items"], [])
            setattr(obj, attr, old)
            self.db.commit()

    def test_feed_cursor_survives_new_posts_and_deleting_team_cascades_post(self):
        first, second, third = self.make_team(), self.make_team(), self.make_team()
        self.db.commit()
        for team in (first, second):
            hall.save_post(self.db, team.id, team.captain_id, "招募")
        page = hall.public_posts(self.db, limit=1)
        hall.save_post(self.db, third.id, third.captain_id, "新招募")
        next_page = hall.public_posts(self.db, page["next_cursor"], 1)
        self.assertEqual(next_page["items"][0]["team_id"], first.id)
        self.assertFalse(next_page["has_more"])
        teams.disband_team(self.db, third.id, third.captain_id)
        self.assertEqual(self.db.query(RecruitmentPost).count(), 2)

    def test_registration_free_application_and_new_requests_blocked_when_full_or_seeded(self):
        captain, applicant = self.make_user(), self.make_user()
        team = self.make_team(captain=captain)
        self.db.commit()
        request = teams.apply_join_team(self.db, team.id, applicant.id, "想打辅助")
        self.assertEqual(request.message, "想打辅助")
        match = self.make_match(team_size=1)
        self.db.add(Registration(match_id=match.id, team_id=team.id, user_id=captain.id))
        another = self.make_user()
        self.db.commit()
        for action in (lambda: teams.apply_join_team(self.db, team.id, another.id), lambda: teams.invite_player(self.db, team.id, captain.id, another.id)):
            with self.assertRaises(HTTPException):
                action()
        match.team_size = 5
        other_team = self.make_team()
        self.db.add(TeamProgress(match_id=match.id, team_id=other_team.id, seed=1, stage=StageStatus.LEGEND))
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            teams.apply_join_team(self.db, team.id, another.id)
        self.assertIn("锁定", error.exception.detail)

    def test_migration_idempotent(self):
        for model in (WechatOutbox, WechatSubscription, RecruitmentPost):
            model.__table__.drop(self.engine)
        upgrade(self.engine)
        team = self.make_team()
        self.db.commit()
        hall.save_post(self.db, team.id, team.captain_id, "长期保存")
        upgrade(self.engine)
        self.assertEqual(hall.public_posts(self.db)["items"][0]["content"], "长期保存")


class WechatTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        for key, value in dict(WX_SUBSCRIBE_ENABLED=True, WX_MOCK_LOGIN=False, WX_APPID="test-app", WX_SECRET="test-secret", WX_SUBSCRIBE_TEMPLATES=json.dumps(CONFIG), WX_SUBSCRIBE_ENV="formal").items():
            setting = patch.object(settings, key, value)
            setting.start()
            self.addCleanup(setting.stop)
        self.captain = self.make_user(wx_openid="openid-captain")
        self.player = self.make_user(wx_openid="openid-player")
        self.team = self.make_team(captain=self.captain)
        self.db.commit()
        self.factory = lambda: Session(self.engine, autoflush=False)

    def consent(self, user):
        wx.record_choice(self.db, user.id, {TEMPLATE: "accept"})

    def invitation(self):
        return teams.invite_player(self.db, self.team.id, self.captain.id, self.player.id)

    def test_config_off_invalid_mapping_and_no_secrets_exposed(self):
        config = wx.configuration()
        self.assertTrue(config["enabled"])
        self.assertNotIn("test-secret", json.dumps(config))
        for value in ("not json", "[]", '{"team_invitation":{"template_id":"abcdefghijk","fields":{"number1":"actor_name"}}}', '{"team_invitation":{"template_id":"abcdefghijk","fields":{"time1":"team_name"}}}'):
            with patch.object(settings, "WX_SUBSCRIBE_TEMPLATES", value):
                self.assertFalse(wx.configuration()["enabled"])
        with patch.object(settings, "WX_SUBSCRIBE_ENABLED", False):
            self.invitation()
            self.assertEqual(self.db.query(WechatOutbox).count(), 0)

    def test_consent_is_per_recipient_and_duplicate_callbacks_do_not_create_credits(self):
        self.consent(self.captain)
        self.consent(self.captain)
        self.assertEqual(self.db.query(WechatSubscription).count(), 1)
        self.invitation()
        self.assertEqual(self.db.query(WechatOutbox).count(), 0)
        with self.assertRaises(HTTPException):
            wx.record_choice(self.db, self.player.id, {"unconfigured": "accept"})

    def test_invitation_and_application_notify_correct_people_once(self):
        self.consent(self.player)
        self.consent(self.captain)
        invite = self.invitation()
        teams.apply_join_team(self.db, self.team.id, self.player.id, "申请留言")
        with self.assertRaises(HTTPException):
            self.invitation()
        sender = Mock(return_value=0)
        wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 2)
        calls = [call.args[0] for call in sender.call_args_list]
        self.assertEqual([p["touser"] for p in calls], ["openid-player", "openid-captain"])
        self.assertEqual(calls[0]["page"], "pages/messages/messages")
        self.assertEqual(calls[1]["page"], "pages/team/team")
        self.assertEqual(calls[0]["data"]["phrase3"]["value"], "入队邀请")
        self.assertNotIn("申请留言", json.dumps(calls))
        self.assertEqual(self.db.get(TeamInvitation, invite.id).status.value, "pending")

    def test_enqueue_failure_rolls_back_business_and_no_http_during_transaction(self):
        self.consent(self.player)
        with patch.object(wx, "enqueue", side_effect=RuntimeError("storage failure")), patch.object(wx, "_post") as network:
            with self.assertRaises(RuntimeError):
                self.invitation()
            network.assert_not_called()
        self.assertEqual(self.db.query(TeamInvitation).count(), 0)
        self.assertEqual(self.db.query(WechatOutbox).count(), 0)

    def test_network_ambiguity_is_not_retried_and_business_survives(self):
        self.consent(self.player)
        invite = self.invitation()
        sender = Mock(side_effect=TimeoutError("secret-containing URL must not be stored"))
        wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        self.db.expire_all()
        self.assertEqual(self.db.query(WechatOutbox).one().status, "unknown")
        self.assertEqual(self.db.get(TeamInvitation, invite.id).status.value, "pending")

    def test_no_permission_does_not_disable_a_new_consent_or_retry_old_event(self):
        self.consent(self.player)
        self.invitation()
        sender = Mock(return_value=43101)
        wx.deliver_pending(self.factory, sender)
        wx.deliver_pending(self.factory, sender)
        self.assertEqual(sender.call_count, 1)
        self.db.expire_all()
        self.assertEqual(self.db.query(WechatOutbox).one().error_code, "43101")
        self.assertTrue(self.db.get(WechatSubscription, (self.player.id, TEMPLATE)).enabled)

    def test_resolved_invite_revoked_consent_hidden_user_and_expired_event_not_sent(self):
        for scenario in ("resolved", "revoked", "hidden", "expired", "interrupted"):
            with self.subTest(scenario=scenario):
                player = self.make_user(wx_openid="real-openid-" + scenario)
                self.db.commit()
                self.consent(player)
                inv = teams.invite_player(self.db, self.team.id, self.captain.id, player.id)
                row = self.db.query(WechatOutbox).filter_by(source_id=inv.id).one()
                if scenario == "resolved":
                    teams.reject_invitation(self.db, inv.id, player.id)
                elif scenario == "revoked":
                    wx.record_choice(self.db, player.id, {TEMPLATE: "reject"})
                elif scenario == "hidden":
                    player.is_hidden = True
                elif scenario == "expired":
                    row.created_at = datetime.now() - timedelta(days=2)
                else:
                    row.status, row.updated_at = "sending", datetime.now() - timedelta(minutes=6)
                self.db.commit()
                sender = Mock(return_value=0)
                wx.deliver_pending(self.factory, sender)
                sender.assert_not_called()

    def test_token_retry_only_after_explicit_token_rejection(self):
        with patch.object(wx, "access_token", return_value="token") as token, patch.object(wx, "_post", side_effect=[{"errcode": 42001}, {"errcode": 0}]) as post:
            self.assertEqual(wx.send({}), 0)
            self.assertEqual(post.call_count, 2)
            self.assertEqual(token.call_args.kwargs, {"refresh": True})
        with patch.object(wx, "access_token", return_value="token"), patch.object(wx, "_post", side_effect=TimeoutError) as post:
            with self.assertRaises(TimeoutError):
                wx.send({})
            self.assertEqual(post.call_count, 1)
