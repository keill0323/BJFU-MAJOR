"""队伍成员、邀请和报名联动的隔离回归测试。"""

from tests.support import DatabaseTestCase, Base

import multiprocessing
from pathlib import Path
import tempfile

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.team import TeamApplication, TeamInvitation, Team, TeamMember, ApplicationStatus
from app.models.match import MatchStatus, Registration, RegistrationStatus, TeamProgress
from app.services import team_service


def _join_in_process(database_url, team_id, user_id, started, finished, results,
                     counted=None, release=None):
    """独立进程使用真实服务；SQL 事件仅控制交错，不替代任何业务判断。"""
    engine = create_engine(database_url, connect_args={"timeout": 15})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    if counted is not None:
        @event.listens_for(engine, "after_cursor_execute")
        def pause_after_member_count(_connection, _cursor, statement, _parameters, _context, _many):
            if (not counted.is_set() and statement.lstrip().upper().startswith("SELECT")
                    and "FROM team_members" in statement and "team_members.team_id =" in statement):
                counted.set()
                if not release.wait(15):
                    raise RuntimeError("Timed out waiting to release the first join")

    with Session(engine, autoflush=False) as db:
        try:
            started.set()
            team_service.join_team(db, team_id, user_id)
            results.put("joined")
        except HTTPException as error:
            results.put(error.status_code)
        except Exception as error:
            results.put(type(error).__name__)
        finally:
            finished.set()
    engine.dispose()


class TeamRegressionTests(DatabaseTestCase):
    def registered_team(self, identities=None, *, match_type="major", status=MatchStatus.REGISTERING):
        users = [self.make_user(identity=identity) for identity in (identities or ["senior"] * 4)]
        team = self.make_team(captain=users[0], members=users[1:])
        match = self.make_match(match_type=match_type, status=status)
        for user in users:
            self.db.add(Registration(
                match_id=match.id, team_id=team.id, user_id=user.id,
                status=RegistrationStatus.APPROVED,
            ))
        self.db.add(TeamProgress(match_id=match.id, team_id=team.id))
        self.db.commit()
        team_service.recalc_team_rating(self.db, team.id)
        return team, match, users

    def assert_not_member(self, user_id):
        self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=user_id).first())

    def test_used_invitation_cannot_rejoin_after_kick_but_new_invitation_can(self):
        team, match, users = self.registered_team()
        user = self.make_user()
        self.db.commit()
        invite = team_service.invite_player(self.db, team.id, users[0].id, user.id)
        team_service.accept_invitation(self.db, invite.id, user.id)
        team_service.kick_member(self.db, team.id, users[0].id, user.id)

        with self.assertRaises(HTTPException) as error:
            team_service.accept_invitation(self.db, invite.id, user.id)
        self.assertEqual(error.exception.status_code, 400)
        self.assert_not_member(user.id)
        registration = self.db.query(Registration).filter_by(match_id=match.id, user_id=user.id).one()
        self.assertIsNone(registration.team_id)

        fresh_invite = team_service.invite_player(self.db, team.id, users[0].id, user.id)
        team_service.accept_invitation(self.db, fresh_invite.id, user.id)
        self.assertEqual(self.db.query(TeamMember).filter_by(user_id=user.id).one().team_id, team.id)

    def test_rejected_invitation_cannot_be_accepted(self):
        team = self.make_team()
        user = self.make_user()
        self.db.commit()
        invite = team_service.invite_player(self.db, team.id, team.captain_id, user.id)
        team_service.reject_invitation(self.db, invite.id, user.id)
        with self.assertRaises(HTTPException):
            team_service.accept_invitation(self.db, invite.id, user.id)
        self.assert_not_member(user.id)
        self.assertEqual(invite.status, ApplicationStatus.REJECTED)

    def test_used_application_cannot_rejoin_after_leave(self):
        team = self.make_team()
        user = self.make_user()
        self.db.commit()
        application = team_service.apply_join_team(self.db, team.id, user.id)
        team_service.approve_application(self.db, application.id, team.captain_id)
        team_service.leave_team(self.db, team.id, user.id)
        with self.assertRaises(HTTPException):
            team_service.approve_application(self.db, application.id, team.captain_id)
        self.assert_not_member(user.id)

    def test_all_join_entries_enforce_full_team_and_keep_requests_pending(self):
        for action in ("join", "application", "invitation"):
            with self.subTest(action=action):
                team, match, users = self.registered_team(["senior"] * 5)
                user = self.make_user()
                self.db.commit()
                request = None
                if action == "application":
                    # 已有申请可能在队伍满员前创建；模拟历史待处理申请。
                    request = TeamApplication(team_id=team.id, user_id=user.id, status=ApplicationStatus.PENDING)
                    self.db.add(request)
                    self.db.commit()
                    attempt = lambda: team_service.approve_application(self.db, request.id, users[0].id)
                elif action == "invitation":
                    request = TeamInvitation(team_id=team.id, user_id=user.id, status=ApplicationStatus.PENDING)
                    self.db.add(request)
                    self.db.commit()
                    attempt = lambda: team_service.accept_invitation(self.db, request.id, user.id)
                else:
                    attempt = lambda: team_service.join_team(self.db, team.id, user.id)
                with self.assertRaises(HTTPException):
                    attempt()
                self.assert_not_member(user.id)
                self.assertIsNone(self.db.query(Registration).filter_by(match_id=match.id, user_id=user.id).first())
                if request is not None:
                    self.assertEqual(request.status, ApplicationStatus.PENDING)

    def test_personal_registration_inherits_team_approval_not_its_own_status(self):
        for progress, mixed, expected in (
            (False, False, RegistrationStatus.PENDING),
            (True, True, RegistrationStatus.PENDING),
            (True, False, RegistrationStatus.APPROVED),
        ):
            with self.subTest(progress=progress, mixed=mixed):
                team, match, users = self.registered_team()
                if not progress:
                    self.db.query(TeamProgress).filter_by(match_id=match.id, team_id=team.id).delete()
                if mixed:
                    self.db.query(Registration).filter_by(match_id=match.id, user_id=users[-1].id).update(
                        {Registration.status: RegistrationStatus.PENDING})
                user = self.make_user()
                personal = Registration(match_id=match.id, user_id=user.id, status=RegistrationStatus.APPROVED)
                self.db.add(personal)
                self.db.commit()
                team_service.join_team(self.db, team.id, user.id)
                self.db.refresh(personal)
                self.assertEqual(personal.team_id, team.id)
                self.assertEqual(personal.status, expected)
                self.assertEqual(team.rating, sum(member.individual_rating for member in users) + user.individual_rating)

    def test_failed_registration_sync_rolls_back_membership_invitation_and_rating(self):
        team, match, users = self.registered_team()
        other_team = self.make_team()
        user = self.make_user()
        self.db.add(Registration(
            match_id=match.id, user_id=user.id, team_id=other_team.id,
            status=RegistrationStatus.APPROVED,
        ))
        self.db.commit()
        previous_rating = team.rating
        invite = team_service.invite_player(self.db, team.id, users[0].id, user.id)
        with self.assertRaises(HTTPException):
            team_service.accept_invitation(self.db, invite.id, user.id)
        self.assert_not_member(user.id)
        self.assertEqual(invite.status, ApplicationStatus.PENDING)
        self.assertEqual(team.rating, previous_rating)
        self.assertEqual(self.db.query(Registration).filter_by(match_id=match.id, user_id=user.id).one().team_id, other_team.id)

    def test_freshman_leave_and_kick_cannot_reduce_roster_below_three(self):
        team, match, users = self.registered_team(
            ["new_student"] * 3 + ["senior"] * 2, match_type="freshman")
        for action in ("leave", "kick"):
            with self.subTest(action=action):
                with self.assertRaises(HTTPException):
                    if action == "leave":
                        team_service.leave_team(self.db, team.id, users[1].id)
                    else:
                        team_service.kick_member(self.db, team.id, users[0].id, users[1].id)
                self.assertEqual(self.db.query(TeamMember).filter_by(team_id=team.id).count(), 5)
                self.assertEqual(self.db.query(Registration).filter_by(match_id=match.id, user_id=users[1].id).one().team_id, team.id)
        team_service.leave_team(self.db, team.id, users[-1].id)
        self.assert_not_member(users[-1].id)

    def test_freshman_can_leave_when_three_remain_or_match_finished(self):
        for identities, status in (
            (["new_student"] * 4, MatchStatus.REGISTERING),
            (["new_student"] * 3, MatchStatus.FINISHED),
        ):
            with self.subTest(status=status, count=len(identities)):
                team, match, users = self.registered_team(identities, match_type="freshman", status=status)
                team_service.leave_team(self.db, team.id, users[1].id)
                self.assert_not_member(users[1].id)
                self.assertIsNone(self.db.query(Registration).filter_by(match_id=match.id, user_id=users[1].id).one().team_id)

    def test_unverified_leave_preserves_each_registration_status(self):
        team, first_match, users = self.registered_team()
        second_match = self.make_match()
        target = users[-1]
        target.rank = None
        self.db.add(Registration(
            match_id=second_match.id, user_id=target.id, team_id=team.id,
            status=RegistrationStatus.PENDING,
        ))
        self.db.commit()
        team_service.leave_team(self.db, team.id, target.id)
        records = self.db.query(Registration).filter_by(user_id=target.id).all()
        self.assertEqual({record.match_id: record.status for record in records}, {
            first_match.id: RegistrationStatus.APPROVED,
            second_match.id: RegistrationStatus.PENDING,
        })
        self.assertTrue(all(record.team_id is None for record in records))

    def test_database_lock_prevents_two_processes_taking_last_slot(self):
        # 文件仅位于临时目录；不同进程不能共享 Python 锁或内存数据库。
        self.db.close()
        self.engine.dispose()
        with tempfile.TemporaryDirectory(prefix="cs2-team-concurrency-") as directory:
            database_url = "sqlite:///" + (Path(directory) / "isolated.sqlite").as_posix()
            self.engine = create_engine(database_url)

            @event.listens_for(self.engine, "connect")
            def enable_foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")

            Base.metadata.create_all(self.engine)
            self.db = Session(self.engine, autoflush=False)
            team, match, _users = self.registered_team()
            candidates = [self.make_user(), self.make_user()]
            self.db.commit()
            team_id, match_id = team.id, match.id
            candidate_ids = [user.id for user in candidates]
            self.db.close()

            context = multiprocessing.get_context("spawn")
            counted, release = context.Event(), context.Event()
            started = [context.Event(), context.Event()]
            finished = [context.Event(), context.Event()]
            results = context.Queue()
            workers = [context.Process(target=_join_in_process, args=(
                database_url, team_id, candidate_ids[index], started[index], finished[index], results,
                counted if index == 0 else None, release if index == 0 else None,
            )) for index in range(2)]
            try:
                workers[0].start()
                self.assertTrue(counted.wait(15), "first process never reached the capacity check")
                workers[1].start()
                self.assertTrue(started[1].wait(15), "second process never started")
                self.assertFalse(finished[1].wait(0.3), "second process bypassed the database lock")
                release.set()
                for worker in workers:
                    worker.join(15)
                    self.assertFalse(worker.is_alive(), "join worker did not finish")
                outcomes = [results.get(timeout=5), results.get(timeout=5)]
                self.assertCountEqual(outcomes, ["joined", 400])
                self.db = Session(self.engine, autoflush=False)
                self.assertEqual(self.db.query(TeamMember).filter_by(team_id=team_id).count(), 5)
                self.assertEqual(self.db.query(Registration).filter_by(team_id=team_id, match_id=match_id).count(), 5)
                expected_rating = self.db.query(Team).filter_by(id=team_id).one().rating
                self.assertEqual(expected_rating, 125)
            finally:
                release.set()
                for worker in workers:
                    if worker.pid is not None:
                        worker.join(2)
                        if worker.is_alive():
                            worker.terminate()
                            worker.join(2)
                results.close()
                self.db.close()
                self.engine.dispose()
