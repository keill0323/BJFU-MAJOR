"""入队后旧申请永久失效；所有待办入口仅暴露仍可处理的请求。"""
from datetime import datetime
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from tests.support import DatabaseTestCase
from app.models.team import Team, TeamApplication, TeamInvitation, TeamMember, ApplicationStatus
from app.models.recruitment import WechatOutbox
from app.services import team_service as teams, notification_service as notices, wechat_service as wx


class TeamRequestLifecycleTests(DatabaseTestCase):
    def request_set(self):
        player = self.make_user()
        first, second, third = self.make_team(), self.make_team(), self.make_team()
        self.db.commit()
        applications = [teams.apply_join_team(self.db, team.id, player.id, "希望加入")
                        for team in (first, second)]
        invitations = [teams.invite_player(self.db, team.id, team.captain_id, player.id)
                       for team in (first, third)]
        return player, (first, second, third), applications, invitations

    def assert_invalidated(self, rows):
        self.db.expire_all()
        for row in rows:
            with self.subTest(model=type(row).__name__, request_id=row.id):
                self.assertEqual(row.status, ApplicationStatus.PENDING)
                self.assertIsNotNone(row.invalidated_at)

    def assert_open(self, rows):
        self.db.expire_all()
        for row in rows:
            self.assertEqual(row.status, ApplicationStatus.PENDING)
            self.assertIsNone(row.invalidated_at)

    def assert_no_requests(self, player, target_teams):
        self.assertEqual(teams.get_my_invitations(self.db, player.id), [])
        self.assertEqual(notices.personal_summary(self.db, player.id)["invitation_count"], 0)
        for team in target_teams:
            self.assertEqual(teams.get_team_applications(self.db, team.id, team.captain_id), [])
            self.assertEqual(notices.team_applications(self.db, team.captain_id), [])
            self.assertEqual(notices.personal_summary(self.db, team.captain_id)["application_count"], 0)

    def test_approving_application_invalidates_other_applications_and_invitations(self):
        player, target_teams, applications, invitations = self.request_set()
        first = target_teams[0]
        teams.approve_application(self.db, applications[0].id, first.captain_id)
        self.assert_invalidated(applications[1:] + invitations)
        self.assertEqual(applications[0].status, ApplicationStatus.APPROVED)
        self.assertIsNone(applications[0].invalidated_at)
        self.assertEqual(self.db.query(TeamMember).filter_by(user_id=player.id).one().team_id, first.id)
        self.assert_no_requests(player, target_teams)

    def test_accepting_invitation_invalidates_all_other_requests(self):
        player, target_teams, applications, invitations = self.request_set()
        teams.accept_invitation(self.db, invitations[1].id, player.id)
        self.assert_invalidated(applications + invitations[:1])
        self.assertEqual(invitations[1].status, ApplicationStatus.APPROVED)
        self.assertIsNone(invitations[1].invalidated_at)
        self.assertEqual(self.db.query(TeamMember).filter_by(user_id=player.id).one().team_id, target_teams[2].id)
        self.assert_no_requests(player, target_teams)

    def test_direct_join_invalidates_requests_for_same_and_other_teams(self):
        player, target_teams, applications, invitations = self.request_set()
        teams.join_team(self.db, target_teams[0].id, player.id)
        self.assert_invalidated(applications + invitations)
        self.assert_no_requests(player, target_teams)

    def test_creating_team_invalidates_former_requests_without_deleting_history(self):
        player, target_teams, applications, invitations = self.request_set()
        new_team = teams.create_team(self.db, "新建自己的队伍", player.id)
        self.assertEqual(new_team.captain_id, player.id)
        self.assert_invalidated(applications + invitations)
        self.assertEqual(self.db.query(TeamApplication).filter_by(user_id=player.id).count(), 2)
        self.assertEqual(self.db.query(TeamInvitation).filter_by(user_id=player.id).count(), 2)
        self.assert_no_requests(player, target_teams)

    def test_leave_does_not_revive_old_requests_and_fresh_request_is_allowed(self):
        player, target_teams, applications, invitations = self.request_set()
        first, second, third = target_teams
        teams.approve_application(self.db, applications[0].id, first.captain_id)
        teams.leave_team(self.db, first.id, player.id)
        self.assert_no_requests(player, target_teams)
        self.assert_invalidated(applications[1:] + invitations)
        with self.assertRaises(HTTPException):
            teams.approve_application(self.db, applications[1].id, second.captain_id)
        with self.assertRaises(HTTPException):
            teams.accept_invitation(self.db, invitations[1].id, player.id)
        self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=player.id).first())
        fresh = teams.apply_join_team(self.db, second.id, player.id, "退队后重新申请")
        invited = teams.invite_player(self.db, third.id, third.captain_id, player.id)
        self.assertNotEqual(fresh.id, applications[1].id)
        self.assertNotEqual(invited.id, invitations[1].id)
        self.assert_open([fresh, invited])
        self.assertEqual([a["id"] for a in teams.get_team_applications(self.db, second.id, second.captain_id)], [fresh.id])
        self.assertEqual([a["id"] for a in teams.get_my_invitations(self.db, player.id)], [invited.id])

    def remove_player(self, action, team, player):
        if action == "leave":
            teams.leave_team(self.db, team.id, player.id)
        elif action == "kick":
            teams.kick_member(self.db, team.id, team.captain_id, player.id)
        elif action == "disband":
            teams.disband_team(self.db, team.id, team.captain_id)
        else:
            teams.delete_team(self.db, team.id)

    def test_historical_pending_does_not_revive_after_any_membership_removal(self):
        for action in ("leave", "kick", "disband", "delete"):
            with self.subTest(action=action):
                player, target_teams, applications, invitations = self.request_set()
                # 模拟历史入队遗漏失效标记，删除成员时应补齐保护。
                actual_team = self.make_team(members=[player])
                self.db.commit()
                self.assert_open(applications + invitations)
                self.remove_player(action, actual_team, player)
                self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=player.id).first())
                self.assert_invalidated(applications + invitations)
                self.assert_no_requests(player, target_teams)
                with self.assertRaises(HTTPException):
                    teams.approve_application(self.db, applications[0].id, target_teams[0].captain_id)
                with self.assertRaises(HTTPException):
                    teams.accept_invitation(self.db, invitations[0].id, player.id)
                fresh = teams.apply_join_team(self.db, target_teams[0].id, player.id)
                self.assert_open([fresh])
                self.assertEqual([a["id"] for a in notices.team_applications(self.db, target_teams[0].captain_id)],
                                 [fresh.id])

    def test_failed_membership_removal_rolls_back_invalidations_and_keeps_team(self):
        for action in ("leave", "kick", "disband", "delete"):
            with self.subTest(action=action):
                player, target_teams, applications, invitations = self.request_set()
                actual_team = self.make_team(members=[player])
                self.db.commit()
                actual_team_id = actual_team.id
                with patch.object(self.db, "commit", side_effect=RuntimeError("移除提交失败")):
                    with self.assertRaises(RuntimeError):
                        self.remove_player(action, actual_team, player)
                self.assertIsNotNone(self.db.get(Team, actual_team_id))
                self.assertEqual(self.db.query(TeamMember).filter_by(user_id=player.id).one().team_id, actual_team_id)
                self.assertEqual(self.db.query(TeamMember).filter_by(team_id=actual_team_id).count(), 2)
                self.assert_open(applications + invitations)
                self.assert_no_requests(player, target_teams)

    def test_deleting_team_invalidates_captain_and_all_members_but_not_other_players(self):
        for action in ("disband", "delete"):
            with self.subTest(action=action):
                target_team = self.make_team()
                captain, first_member, second_member, outsider = [self.make_user() for _ in range(4)]
                self.db.commit()
                records = {}
                for user in (captain, first_member, second_member, outsider):
                    records[user.id] = [
                        teams.apply_join_team(self.db, target_team.id, user.id),
                        teams.invite_player(self.db, target_team.id, target_team.captain_id, user.id),
                    ]
                actual_team = self.make_team(captain=captain, members=[first_member, second_member])
                self.db.commit()
                actual_team_id = actual_team.id
                self.remove_player(action, actual_team, first_member)
                self.assertIsNone(self.db.get(Team, actual_team_id))
                self.assertEqual(self.db.query(TeamMember).filter_by(team_id=actual_team_id).count(), 0)
                for user in (captain, first_member, second_member):
                    self.assert_invalidated(records[user.id])
                    self.assertEqual(teams.get_my_invitations(self.db, user.id), [])
                self.assert_open(records[outsider.id])
                self.assertEqual([a["user_id"] for a in teams.get_team_applications(
                    self.db, target_team.id, target_team.captain_id)], [outsider.id])
                self.assertEqual(notices.personal_summary(self.db, target_team.captain_id)["application_count"], 1)

    def test_failed_membership_transaction_preserves_every_open_request(self):
        player, target_teams, applications, invitations = self.request_set()
        first = target_teams[0]
        original_rating = first.rating
        # 让整个入队变更执行完后在提交处失败，验证失效标记也随成员一起回滚。
        with patch.object(self.db, "commit", side_effect=RuntimeError("提交失败")):
            with self.assertRaises(RuntimeError):
                teams.approve_application(self.db, applications[0].id, first.captain_id)
        self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=player.id).first())
        self.assert_open(applications + invitations)
        self.assertEqual(first.rating, original_rating)
        self.assertEqual(notices.personal_summary(self.db, first.captain_id)["application_count"], 1)
        self.assertEqual(notices.personal_summary(self.db, player.id)["invitation_count"], 2)

    def test_failed_team_creation_preserves_open_requests_and_creates_no_membership(self):
        player, target_teams, applications, invitations = self.request_set()
        original_count = self.db.query(Team).count()
        with patch.object(teams, "recalc_team_rating", side_effect=RuntimeError("评分写入失败")):
            with self.assertRaises(RuntimeError):
                teams.create_team(self.db, "本次创建应回滚", player.id)
        self.assertEqual(self.db.query(Team).count(), original_count)
        self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=player.id).first())
        self.assert_open(applications + invitations)

    def test_other_users_and_already_processed_history_are_unchanged(self):
        player, target_teams, applications, invitations = self.request_set()
        first = target_teams[0]
        other = self.make_user()
        rejected = TeamApplication(team_id=first.id, user_id=player.id, status=ApplicationStatus.REJECTED)
        approved = TeamInvitation(team_id=first.id, user_id=player.id, status=ApplicationStatus.APPROVED)
        self.db.add_all([rejected, approved])
        self.db.commit()
        other_application = teams.apply_join_team(self.db, first.id, other.id)
        other_invitation = teams.invite_player(self.db, first.id, first.captain_id, other.id)
        teams.join_team(self.db, first.id, player.id)
        self.assert_invalidated(applications + invitations)
        self.assert_open([other_application, other_invitation])
        self.assertEqual(rejected.status, ApplicationStatus.REJECTED)
        self.assertEqual(approved.status, ApplicationStatus.APPROVED)
        self.assertIsNone(rejected.invalidated_at)
        self.assertIsNone(approved.invalidated_at)
        for rows in (teams.get_team_applications(self.db, first.id, first.captain_id),
                     notices.team_applications(self.db, first.captain_id)):
            self.assertEqual([row["user_id"] for row in rows], [other.id])
        self.assertEqual(notices.personal_summary(self.db, first.captain_id)["application_count"], 1)
        self.assertEqual(notices.personal_summary(self.db, other.id)["invitation_count"], 1)

    def test_historical_pending_rows_for_teamed_player_are_filtered_everywhere(self):
        player, target_teams, applications, invitations = self.request_set()
        # 模拟升级前的数据：旧服务允许入队后留下 pending，不经过当前入队服务。
        self.make_team(members=[player])
        other = self.make_user()
        self.db.commit()
        self.assert_open(applications + invitations)
        first = target_teams[0]
        active = teams.apply_join_team(self.db, first.id, other.id)
        for rows in (teams.get_team_applications(self.db, first.id, first.captain_id),
                     notices.team_applications(self.db, first.captain_id)):
            self.assertEqual([row["id"] for row in rows], [active.id])
        self.assertEqual(notices.personal_summary(self.db, first.captain_id)["application_count"], 1)
        self.assertEqual(notices.personal_summary(self.db, target_teams[1].captain_id)["application_count"], 0)
        self.assertEqual(teams.get_my_invitations(self.db, player.id), [])
        self.assertEqual(notices.personal_summary(self.db, player.id)["invitation_count"], 0)

    def test_explicitly_invalidated_requests_are_hidden_even_when_player_is_free(self):
        player, target_teams, applications, invitations = self.request_set()
        for row in applications + invitations:
            row.invalidated_at = datetime(2026, 9, 12, 14)
        self.db.commit()
        self.assert_no_requests(player, target_teams)
        with self.assertRaises(HTTPException):
            teams.approve_application(self.db, applications[0].id, target_teams[0].captain_id)
        with self.assertRaises(HTTPException):
            teams.accept_invitation(self.db, invitations[0].id, player.id)
        self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=player.id).first())

    def test_wechat_does_not_send_invalidated_or_already_teamed_request(self):
        template_id = "unit-team-lifecycle-template"
        specs = {kind: {"template_id": template_id, "fields": {"thing1": "team_name"}}
                 for kind in ("team_application", "team_invitation")}
        with patch.object(wx, "templates", return_value=specs):
            for kind in specs:
                for reason in ("invalidated", "already_teamed"):
                    with self.subTest(kind=kind, reason=reason):
                        player = self.make_user(wx_openid=f"openid-{kind}-{reason}")
                        captain = self.make_user(wx_openid=f"captain-{kind}-{reason}")
                        team = self.make_team(captain=captain)
                        self.db.commit()
                        recipient = captain if kind == "team_application" else player
                        wx.record_choice(self.db, recipient.id, {template_id: "accept"})
                        if kind == "team_application":
                            request = teams.apply_join_team(self.db, team.id, player.id)
                        else:
                            request = teams.invite_player(self.db, team.id, captain.id, player.id)
                        outbox = self.db.query(WechatOutbox).filter_by(kind=kind, source_id=request.id).one()
                        if reason == "invalidated":
                            request.invalidated_at = datetime.now()
                        else:
                            self.make_team(members=[player])
                        self.db.commit()
                        sender = Mock(return_value=0)
                        wx.deliver_pending(lambda: Session(self.engine, autoflush=False), sender)
                        sender.assert_not_called()
                        self.db.expire_all()
                        self.assertEqual(outbox.status, "skipped")

    def test_migration_adds_nullable_columns_and_preserves_history_on_repeat(self):
        from app.migrations.team_request_invalidation import upgrade

        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE team_members (id INTEGER PRIMARY KEY, user_id INTEGER)")
                connection.exec_driver_sql("INSERT INTO team_members VALUES (1, 115)")
                for table in ("team_applications", "team_invitations"):
                    connection.exec_driver_sql(
                        f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, user_id INTEGER, "
                        "status VARCHAR(16), message VARCHAR(200))")
                    connection.exec_driver_sql(f"CREATE INDEX ix_{table}_status ON {table} (status)")
                    connection.exec_driver_sql(f"INSERT INTO {table} VALUES (3, 114, 'PENDING', '历史留言')")
                    connection.exec_driver_sql(f"INSERT INTO {table} VALUES (4, 115, 'PENDING', '已加入别队')")
                    connection.exec_driver_sql(f"INSERT INTO {table} VALUES (5, 115, 'REJECTED', '队长已拒绝')")
            upgrade(bind=engine)
            for table in ("team_applications", "team_invitations"):
                column = next(c for c in inspect(engine).get_columns(table) if c["name"] == "invalidated_at")
                self.assertTrue(column["nullable"])
                self.assertIn("DATETIME", str(column["type"]).upper())
            with engine.begin() as connection:
                for table in ("team_applications", "team_invitations"):
                    self.assertEqual(connection.exec_driver_sql(
                        f"SELECT id, user_id, status, message, invalidated_at FROM {table} WHERE id=3").one(),
                        (3, 114, "PENDING", "历史留言", None))
                    invalidated = connection.exec_driver_sql(
                        f"SELECT status, message, invalidated_at FROM {table} WHERE id=4").one()
                    self.assertEqual(invalidated[:2], ("PENDING", "已加入别队"))
                    self.assertIsNotNone(invalidated[2])
                    self.assertEqual(connection.exec_driver_sql(
                        f"SELECT status, invalidated_at FROM {table} WHERE id=5").one(), ("REJECTED", None))
                    connection.exec_driver_sql(f"UPDATE {table} SET invalidated_at = '2026-09-12 14:00:00' WHERE id = 3")
                    connection.exec_driver_sql(f"UPDATE {table} SET invalidated_at = '2026-09-11 16:00:00' WHERE id = 4")
            upgrade(bind=engine)
            with engine.connect() as connection:
                for table in ("team_applications", "team_invitations"):
                    self.assertEqual(connection.exec_driver_sql(f"SELECT invalidated_at FROM {table} WHERE id=3").scalar_one(),
                                     "2026-09-12 14:00:00")
                    self.assertEqual(connection.exec_driver_sql(f"SELECT invalidated_at FROM {table} WHERE id=4").scalar_one(),
                                     "2026-09-11 16:00:00")
                    self.assertEqual(len(inspect(engine).get_indexes(table)), 1)
        finally:
            engine.dispose()
