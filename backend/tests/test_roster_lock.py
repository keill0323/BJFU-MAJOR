"""报名名单锁定、原子编排与跨进程赛事行锁的隔离回归。"""
from tests.support import Base, DatabaseTestCase

import multiprocessing
from pathlib import Path
import tempfile
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.match import MatchRound, Registration, RegistrationStatus, StageStatus, TeamProgress
from app.models.team import ApplicationStatus, TeamMember
from app.services import match_service as service, team_service


def _roster_operation(database_url, action, match_id, team_id, started, finished,
                      results, locked=None, release=None):
    """真实服务加锁后暂停首个事务，第二个独立进程必须等待它提交。"""
    engine = create_engine(database_url, connect_args={"timeout": 15})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    if locked is not None:
        @event.listens_for(engine, "after_cursor_execute")
        def pause_locked_match(_connection, _cursor, statement, _parameters, _context, _many):
            if not locked.is_set() and statement.lstrip().upper().startswith("UPDATE MATCHES SET"):
                locked.set()
                if not release.wait(15):
                    raise RuntimeError("Timed out waiting to release roster transaction")

    try:
        with Session(engine, autoflush=False) as db:
            try:
                started.set()
                if action == "group":
                    service.seed_and_group_teams(db, match_id, ["A", "B", "C"])
                else:
                    service.approve_team_registration(db, match_id, team_id)
                results.put((action, "ok"))
            except HTTPException as error:
                results.put((action, error.status_code))
            except Exception as error:
                results.put((action, type(error).__name__))
            finally:
                finished.set()
    finally:
        engine.dispose()


class RosterLockTests(DatabaseTestCase):
    def pending_team(self, match, *, rating=100):
        team = self.make_team(rating=rating)
        reg = Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id,
                           status=RegistrationStatus.PENDING)
        self.db.add(reg)
        self.db.commit()
        return team, reg

    def enrolled(self, count=10):
        match = self.make_match(max_teams=32)
        for i in range(count):
            team = self.make_team(rating=200 - i)
            self.db.add_all([
                Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id,
                             status=RegistrationStatus.APPROVED),
                TeamProgress(match_id=match.id, team_id=team.id),
            ])
        self.db.commit()
        return match

    def snapshot(self, match_id):
        return [(p.team_id, p.seed, p.stage, p.group_name) for p in self.db.query(TeamProgress)
                .filter_by(match_id=match_id).order_by(TeamProgress.team_id)]

    def test_approved_teams_are_unseeded_until_grouping_and_roster_stays_open(self):
        match = self.make_match()
        for _ in range(2):
            team, reg = self.pending_team(match)
            result = service.approve_team_registration(self.db, match.id, team.id)
            progress = self.db.query(TeamProgress).filter_by(match_id=match.id, team_id=team.id).one()
            self.assertEqual(result["approved"], 1)
            self.assertEqual(reg.status, RegistrationStatus.APPROVED)
            self.assertEqual(progress.seed, 0)
            self.assertIsNone(progress.group_name)
            self.assertFalse(service.is_roster_locked(self.db, match.id))

    def test_seed_group_or_round_locks_new_registration_and_both_approval_routes(self):
        for lock_type in ("seed", "group", "round"):
            with self.subTest(lock_type=lock_type):
                match = self.enrolled()
                candidate, reg = self.pending_team(match)
                new_team = self.make_team()
                self.db.commit()
                if lock_type == "seed":
                    service.auto_assign_seeds(self.db, match.id)
                elif lock_type == "group":
                    progress = self.db.query(TeamProgress).filter_by(match_id=match.id).first()
                    progress.group_name = "A"
                    self.db.commit()
                else:
                    teams = self.db.query(TeamProgress).filter_by(match_id=match.id).limit(2).all()
                    service.add_round(self.db, match.id, 1, teams[0].team_id, teams[1].team_id)
                before = self.snapshot(match.id)
                registered_before = self.db.query(Registration).filter_by(match_id=match.id).count()
                self.assertTrue(service.is_roster_locked(self.db, match.id))
                for operation in (
                    lambda: service.register_team(self.db, match.id, new_team.id),
                    lambda: service.approve_team_registration(self.db, match.id, candidate.id),
                    lambda: service.approve_registration(self.db, reg.id),
                    lambda: service.ensure_team_progress(self.db, match.id, candidate.id),
                ):
                    with self.assertRaises(HTTPException) as error:
                        operation()
                    self.assertEqual(error.exception.status_code, 400)
                    self.assertIn("名单已锁定", error.exception.detail)
                    self.db.rollback()
                    self.assertEqual(before, self.snapshot(match.id))
                    self.assertEqual(reg.status, RegistrationStatus.PENDING)
                    self.assertEqual(registered_before, self.db.query(Registration)
                                     .filter_by(match_id=match.id).count())

    def test_existing_approved_team_can_replay_without_resetting_seeds_groups_or_stage(self):
        match = self.enrolled()
        service.seed_and_group_teams(self.db, match.id, ["A", "B", "C"])
        before = self.snapshot(match.id)
        regs = self.db.query(Registration).filter_by(match_id=match.id).all()
        for reg in regs:
            self.assertEqual(service.approve_team_registration(self.db, match.id, reg.team_id)["approved"], 0)
            service.approve_registration(self.db, reg.id)
            service.ensure_team_progress(self.db, match.id, reg.team_id)
        self.assertEqual(before, self.snapshot(match.id))

    def test_legacy_approved_without_progress_and_mixed_approvals_cannot_bypass_lock(self):
        match = self.enrolled()
        candidate, reg = self.pending_team(match)
        reg.status = RegistrationStatus.APPROVED
        self.db.commit()
        service.seed_and_group_teams(self.db, match.id, ["A", "B", "C"])
        with self.assertRaises(HTTPException):
            service.approve_team_registration(self.db, match.id, candidate.id)
        self.db.rollback()
        existing = self.db.query(TeamProgress).filter_by(match_id=match.id).first()
        approved_reg = self.db.query(Registration).filter_by(match_id=match.id, team_id=existing.team_id).one()
        approved_reg.status = RegistrationStatus.PENDING
        self.db.commit()
        with self.assertRaises(HTTPException):
            service.approve_team_registration(self.db, match.id, existing.team_id)
        self.db.rollback()
        self.assertEqual(approved_reg.status, RegistrationStatus.PENDING)
        self.assertIsNone(self.db.query(TeamProgress).filter_by(match_id=match.id, team_id=candidate.id).first())

    def test_late_member_sync_cannot_make_an_unapproved_team_participate(self):
        match = self.enrolled()
        candidate, _reg = self.pending_team(match)
        player = self.make_user()
        self.db.commit()
        invite = team_service.invite_player(self.db, candidate.id, candidate.captain_id, player.id)
        before_rating = candidate.rating
        service.seed_and_group_teams(self.db, match.id, ["A", "B", "C"])
        with self.assertRaises(HTTPException) as error:
            team_service.accept_invitation(self.db, invite.id, player.id)
        self.assertIn("名单已锁定", error.exception.detail)
        self.assertIsNone(self.db.query(TeamMember).filter_by(user_id=player.id).first())
        self.assertIsNone(self.db.query(Registration).filter_by(match_id=match.id, user_id=player.id).first())
        self.assertEqual(invite.status, ApplicationStatus.PENDING)
        self.assertEqual(candidate.rating, before_rating)

    def test_free_agent_registration_and_joining_existing_team_remain_available(self):
        match = self.enrolled()
        player = self.make_user()
        self.db.commit()
        service.seed_and_group_teams(self.db, match.id, ["A", "B", "C"])
        reg = service.register_user(self.db, match.id, player.id)
        self.assertIsNone(reg.team_id)
        team_id = self.db.query(TeamProgress).filter_by(match_id=match.id).first().team_id
        team_service.join_team(self.db, team_id, player.id)
        self.db.refresh(reg)
        self.assertEqual(reg.team_id, team_id)
        self.assertEqual(reg.status, RegistrationStatus.APPROVED)

    def test_atomic_grouping_validates_capacity_before_locking_roster(self):
        for count, groups in ((9, ["A", "B", "C"]), (11, ["A", "B", "C", "D"])):
            with self.subTest(count=count):
                match = self.enrolled(count)
                candidate, _reg = self.pending_team(match)
                before = self.snapshot(match.id)
                with self.assertRaises(HTTPException):
                    service.seed_and_group_teams(self.db, match.id, groups)
                # 不依赖请求结束时关闭 Session 或调用方另行 rollback。
                self.assertEqual(before, self.snapshot(match.id))
                self.assertFalse(service.is_roster_locked(self.db, match.id))
                service.approve_team_registration(self.db, match.id, candidate.id)
                progresses = service.seed_and_group_teams(self.db, match.id, groups)
                self.assertEqual(len(progresses), count + 1)
                self.assertTrue(service.is_roster_locked(self.db, match.id))

    def test_atomic_grouping_rejects_invalid_groups_without_partial_seeding(self):
        match = self.enrolled(12)
        for groups in ([], ["A", "B"], ["A", "A", "C"], ["A", "B", "附加赛"]):
            with self.subTest(groups=groups), self.assertRaises(HTTPException):
                service.seed_and_group_teams(self.db, match.id, groups)
            self.assertFalse(service.is_roster_locked(self.db, match.id))

    def test_atomic_grouping_rolls_back_even_after_mutations_were_flushed(self):
        match = self.enrolled()
        before = self.snapshot(match.id)

        def fail_commit():
            self.db.flush()
            raise RuntimeError("simulated commit failure")

        with patch.object(self.db, "commit", side_effect=fail_commit):
            with self.assertRaises(RuntimeError):
                service.seed_and_group_teams(self.db, match.id, ["A", "B", "C"])
        self.assertEqual(before, self.snapshot(match.id))
        self.assertFalse(service.is_roster_locked(self.db, match.id))

    def test_atomic_grouping_assigns_four_seeds_and_snake_groups_by_rating(self):
        for groups in (["A", "B", "C"], ["甲", "乙", "丙", "丁"]):
            with self.subTest(groups=groups):
                match = self.enrolled(4 + len(groups) * 2)
                progresses = service.seed_and_group_teams(self.db, match.id, groups)
                self.assertEqual([p.seed for p in progresses], list(range(1, len(progresses) + 1)))
                self.assertTrue(all(p.stage == StageStatus.LEGEND and p.group_name is None for p in progresses[:4]))
                self.assertTrue(all(p.stage == StageStatus.CHALLENGER for p in progresses[4:]))
                self.assertEqual([p.group_name for p in progresses[4:]], groups + list(reversed(groups)))

    def test_atomic_regrouping_cannot_destroy_existing_rounds(self):
        match = self.enrolled()
        service.seed_and_group_teams(self.db, match.id, ["A", "B", "C"])
        service.auto_generate_group_matches(self.db, match.id, "A")
        before = self.snapshot(match.id)
        with self.assertRaises(HTTPException):
            service.seed_and_group_teams(self.db, match.id, ["甲", "乙", "丙"])
        self.assertEqual(before, self.snapshot(match.id))
        self.assertEqual(self.db.query(MatchRound).filter_by(match_id=match.id).count(), 2)

    def test_grouping_and_approval_serialize_in_either_order_across_processes(self):
        for first_action in ("group", "approve"):
            with self.subTest(first_action=first_action), tempfile.TemporaryDirectory(prefix="roster-race-") as directory:
                self.db.close()
                self.engine.dispose()
                database_url = "sqlite:///" + (Path(directory) / "test.sqlite").as_posix()
                self.engine = create_engine(database_url, connect_args={"timeout": 15})
                Base.metadata.create_all(self.engine)
                self.db = Session(self.engine, autoflush=False)
                match = self.enrolled()
                candidate, reg = self.pending_team(match)
                match_id, team_id, reg_id = match.id, candidate.id, reg.id
                self.db.close()
                context = multiprocessing.get_context("spawn")
                locked, release = context.Event(), context.Event()
                started = [context.Event(), context.Event()]
                finished = [context.Event(), context.Event()]
                results = context.Queue()
                actions = [first_action, "approve" if first_action == "group" else "group"]
                workers = [context.Process(target=_roster_operation, args=(
                    database_url, action, match_id, team_id, started[index], finished[index], results,
                    locked if index == 0 else None, release if index == 0 else None,
                )) for index, action in enumerate(actions)]
                try:
                    workers[0].start()
                    self.assertTrue(locked.wait(15), "first transaction never acquired the match lock")
                    workers[1].start()
                    self.assertTrue(started[1].wait(15), "second transaction never started")
                    self.assertFalse(finished[1].wait(0.3), "second transaction bypassed the match lock")
                    release.set()
                    for worker in workers:
                        worker.join(15)
                        self.assertFalse(worker.is_alive(), "roster worker did not finish")
                    outcomes = dict(results.get(timeout=5) for _ in range(2))
                    self.assertEqual(outcomes, {"group": "ok", "approve": 400 if first_action == "group" else "ok"})
                    self.db = Session(self.engine, autoflush=False)
                    approved_first = first_action == "approve"
                    self.assertEqual(self.db.get(Registration, reg_id).status,
                                     RegistrationStatus.APPROVED if approved_first else RegistrationStatus.PENDING)
                    progress = self.db.query(TeamProgress).filter_by(match_id=match_id, team_id=team_id).first()
                    self.assertEqual(progress is not None, approved_first)
                    if progress:
                        self.assertGreater(progress.seed, 0)
                        self.assertIsNotNone(progress.group_name)
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
