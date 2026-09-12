"""Authentication regressions using synthetic users and mocked image/AI I/O."""
from unittest.mock import patch

from tests.support import DatabaseTestCase, settings

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.user import RankApplication
from app.routers import auth
from app.services import ai_review_service, auth_service, match_service


class AuthenticationRegressions(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(patch.stopall)
        patch.object(settings, "AI_REVIEW_ENABLED", True).start()
        self.save_image = patch.object(
            auth.upload_service, "save_upload_image",
            return_value=("/uploads/new.png", "/mock-not-read.png"),
        ).start()
        self.vision = patch.object(ai_review_service, "_call_vision").start()

    def applicant(self, **kwargs):
        values = dict(is_verified=False, student_id=None)
        values.update(kwargs)
        user = self.make_user(**values)
        self.db.commit()
        return user

    def verify(self, user, **kwargs):
        self.vision.return_value = {
            "is_valid": True, "confidence": 0.99, "reason": "测试凭证",
            "student_id": "260123456", **kwargs,
        }
        return auth.upload_verify(file=None, db=self.db, current_user=user)

    def test_duplicate_student_id_stays_unverified_and_cannot_register(self):
        self.make_user(student_id="260123456")
        match = self.make_match()
        user = self.applicant()
        result = self.verify(user)
        self.assertFalse(result.is_verified)
        self.assertIsNone(result.student_id)
        self.assertEqual(result.ai_student_id, "260123456")
        self.assertEqual(result.ai_review_status, "pending")
        with self.assertRaises(HTTPException) as error:
            match_service.register_user(self.db, match.id, user.id)
        self.assertEqual(error.exception.status_code, 400)

    def test_empty_or_invalid_student_id_requires_manual_review(self):
        for sid in (None, "", "  ", "not-a-student", "12", "2" * 65, "２６０１２３４５６"):
            with self.subTest(student_id=sid):
                user = self.applicant()
                result = self.verify(user, student_id=sid)
                self.assertFalse(result.is_verified)
                self.assertEqual(result.ai_review_status, "pending")

    def test_valid_student_id_and_same_account_student_id_can_auto_pass(self):
        user = self.applicant(student_id="260123456")
        result = self.verify(user, student_id=" 260123456 ")
        self.assertTrue(result.is_verified)
        self.assertEqual(result.student_id, "260123456")
        self.assertEqual(result.ai_review_status, "auto_pass")

    def test_identity_checks_nine_digits_before_enrollment_year(self):
        from datetime import datetime
        with patch.object(auth_service, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 12)
            for sid, expected in (("260123456", "new_student"), ("250123456", "new_student"),
                                  ("240123456", "senior"), ("220123456", "senior"),
                                  ("26012345", None), ("2601234567", None), ("22012345", None),
                                  ("26ABC1234", None), ("２６０１２３４５６", None), (None, None)):
                with self.subTest(student_id=sid):
                    self.assertEqual(auth_service._auto_identity(sid), expected)

    def test_non_nine_digit_school_id_stays_pending_with_admin_contact(self):
        for sid in ("26012345", "22012345", "20260123456"):
            with self.subTest(student_id=sid):
                user = self.applicant()
                result = self.verify(user, student_id=sid)
                self.assertFalse(result.is_verified)
                self.assertIsNone(result.student_id)
                self.assertEqual(result.ai_student_id, sid)
                self.assertEqual(result.ai_review_status, "pending")
                self.assertIn("联系管理员", result.ai_review_reason)
                self.assertIn("3761215994", result.ai_review_reason)
                match = self.make_match()
                with self.assertRaises(HTTPException) as error:
                    match_service.register_user(self.db, match.id, user.id)
                self.assertIn("联系管理员", error.exception.detail)

    def test_non_nine_digit_id_is_not_rejected_only_for_format(self):
        user = self.applicant()
        result = self.verify(user, student_id="20260123456", is_valid=False)
        self.assertFalse(result.is_verified)
        self.assertEqual(result.ai_review_status, "pending")

    def test_manual_identity_remains_available_for_non_nine_digit_id(self):
        user = self.applicant()
        result = auth_service.admin_update_user(self.db, user.id, student_id="20260123456", is_verified=True)
        self.assertTrue(result.is_verified)
        self.assertIsNone(auth_service.effective_identity(result))
        for identity in ("new_student", "senior"):
            result = auth_service.admin_update_user(self.db, user.id, identity=identity)
            self.assertEqual(auth_service.effective_identity(result), identity)

    def test_low_confidence_student_id_is_retained_only_as_unconfirmed_candidate(self):
        for validity in (True, False, None):
            with self.subTest(is_valid=validity):
                user = self.applicant()
                result = self.verify(user, confidence=0.6, is_valid=validity,
                                     student_id=" 260123456 ")
                self.assertFalse(result.is_verified)
                self.assertIsNone(result.student_id)
                self.assertEqual(result.ai_student_id, "260123456")
                self.assertEqual(result.ai_review_status, "pending")

    def test_ai_candidate_never_replaces_previously_saved_student_id_without_approval(self):
        user = self.applicant(student_id="260222222")
        result = self.verify(user, confidence=0.7, student_id="260333333")
        self.assertEqual(result.student_id, "260222222")
        self.assertEqual(result.ai_student_id, "260333333")
        self.assertFalse(result.is_verified)

    def test_resubmission_clears_old_rejection_even_when_ai_fails(self):
        user = self.applicant(
            verify_image="/uploads/old.png", ai_review_status="manual_reject",
            ai_student_id="260999999",
            ai_review_reason="旧图片理由", ai_review_confidence=0.98,
            verify_reject_reason="旧图片驳回",
        )
        self.vision.side_effect = TimeoutError("simulated AI timeout")
        result = auth.upload_verify(file=None, db=self.db, current_user=user)
        self.assertEqual(result.verify_image, "/uploads/new.png")
        self.assertEqual(result.ai_review_status, "pending")
        self.assertIsNone(result.verify_reject_reason)
        self.assertIsNone(result.ai_review_reason)
        self.assertEqual(result.ai_review_confidence, 0)
        self.assertIsNone(result.ai_student_id)

    def test_malformed_ai_output_is_saved_for_manual_verification(self):
        invalid = [[], "unexpected"]
        invalid += [{"is_valid": True, "confidence": value, "student_id": "260123456"}
                    for value in (None, "0.95", True, -0.1, 1.1, float("nan"), float("inf"))]
        invalid += [
            {"is_valid": "true", "confidence": 0.99},
            {"is_valid": True, "confidence": 0.99, "student_id": 260123456},
            {"is_valid": True, "confidence": 0.99, "reason": {}},
            {"is_valid": True, "confidence": 0.99, "reason": "x" * 501},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                user = self.applicant()
                self.vision.return_value = payload
                result = auth.upload_verify(file=None, db=self.db, current_user=user)
                self.assertFalse(result.is_verified)
                self.assertEqual(result.ai_review_status, "pending")
                self.assertEqual(result.verify_image, "/uploads/new.png")

    def test_high_confidence_invalid_evidence_is_rejected(self):
        user = self.applicant()
        result = self.verify(user, is_valid=False, student_id=None)
        self.assertFalse(result.is_verified)
        self.assertEqual(result.ai_review_status, "auto_reject")

    def test_unique_constraint_race_falls_back_to_manual_review(self):
        user = self.applicant()
        original_commit = self.db.commit
        raised = False

        def race_on_auto_pass():
            nonlocal raised
            if user.is_verified and not raised:
                raised = True
                raise IntegrityError("UPDATE users", {}, Exception("duplicate student_id"))
            original_commit()

        with patch.object(self.db, "commit", side_effect=race_on_auto_pass):
            result = self.verify(user)
        self.assertTrue(raised)
        self.assertFalse(result.is_verified)
        self.assertIsNone(result.student_id)
        self.assertEqual(result.ai_student_id, "260123456")
        self.assertEqual(result.ai_review_status, "pending")
        self.assertIn("其他用户", result.ai_review_reason)

    def test_late_ai_does_not_overwrite_newer_upload(self):
        user = self.applicant()

        def newer_upload(*_args):
            auth_service.update_verify_image(self.db, user.id, "/uploads/newer.png")
            return {"is_valid": True, "confidence": 0.99, "student_id": "260123456"}

        self.vision.side_effect = newer_upload
        result = auth.upload_verify(file=None, db=self.db, current_user=user)
        self.assertEqual(result.verify_image, "/uploads/newer.png")
        self.assertFalse(result.is_verified)
        self.assertEqual(result.ai_review_status, "pending")
        self.assertIsNone(result.ai_student_id)

    def test_late_ai_does_not_override_manual_rejection(self):
        user = self.applicant()

        def manual_rejection(*_args):
            auth_service.admin_update_user(self.db, user.id, is_verified=False,
                                           verify_reject_reason="人工核验未通过")
            return {"is_valid": True, "confidence": 0.99, "student_id": "260123456"}

        self.vision.side_effect = manual_rejection
        result = auth.upload_verify(file=None, db=self.db, current_user=user)
        self.assertFalse(result.is_verified)
        self.assertEqual(result.ai_review_status, "manual_reject")
        self.assertEqual(result.verify_reject_reason, "人工核验未通过")
        self.assertIsNone(result.ai_student_id)

    def test_late_ai_does_not_override_manual_student_id_confirmation(self):
        user = self.applicant()

        def manual_approval(*_args):
            auth_service.admin_update_user(self.db, user.id, student_id="260222222",
                                           is_verified=True, expected_verify_image="/uploads/new.png")
            return {"is_valid": True, "confidence": 0.99, "student_id": "260123456"}

        self.vision.side_effect = manual_approval
        result = auth.upload_verify(file=None, db=self.db, current_user=user)
        self.assertTrue(result.is_verified)
        self.assertEqual(result.student_id, "260222222")
        self.assertEqual(result.ai_review_status, "manual_pass")
        self.assertIsNone(result.ai_student_id)

    def test_manual_approval_requires_confirmed_student_id_and_never_uses_candidate_implicitly(self):
        user = self.applicant(ai_student_id="260123456", ai_review_status="pending")
        with self.assertRaises(HTTPException) as error:
            auth_service.admin_update_user(self.db, user.id, is_verified=True)
        self.assertEqual(error.exception.status_code, 400)
        self.db.rollback()
        self.assertFalse(user.is_verified)
        self.assertIsNone(user.student_id)
        self.assertEqual(user.ai_student_id, "260123456")

    def test_manual_approval_saves_trimmed_student_id_and_verification_together(self):
        user = self.applicant(ai_student_id="260999999", ai_review_status="pending")
        commits = []
        original_commit = self.db.commit

        def capture_commit():
            commits.append((user.student_id, user.is_verified, user.ai_review_status))
            original_commit()

        with patch.object(self.db, "commit", side_effect=capture_commit):
            result = auth_service.admin_update_user(self.db, user.id,
                                                   student_id=" 260123456 ", is_verified=True)
        self.assertEqual(result.student_id, "260123456")
        self.assertTrue(result.is_verified)
        self.assertEqual(result.ai_review_status, "manual_pass")
        self.assertTrue(commits)
        self.assertTrue(all(row == ("260123456", True, "manual_pass") for row in commits))

    def test_manual_approval_accepts_existing_valid_student_id_for_legacy_clients(self):
        user = self.applicant(student_id="260123456")
        result = auth_service.admin_update_user(self.db, user.id, is_verified=True)
        self.assertTrue(result.is_verified)
        self.assertEqual(result.student_id, "260123456")

    def test_manual_student_id_rejects_empty_internal_spaces_and_invalid_digits(self):
        for sid in ("", " ", "260 123456", "１２３４５６", "12345", "1" * 21, "260ABC123"):
            with self.subTest(student_id=sid):
                user = self.applicant()
                with self.assertRaises(HTTPException) as error:
                    auth_service.admin_update_user(self.db, user.id, student_id=sid, is_verified=True)
                self.assertEqual(error.exception.status_code, 400)
                self.db.rollback()
                self.assertIsNone(user.student_id)
                self.assertFalse(user.is_verified)

    def test_manual_student_id_conflict_does_not_partially_approve(self):
        owner = self.applicant(student_id="260123456")
        user = self.applicant(ai_student_id="260123456", ai_review_status="pending")
        with self.assertRaises(HTTPException) as error:
            auth_service.admin_update_user(self.db, user.id, student_id="260123456", is_verified=True)
        self.assertEqual(error.exception.status_code, 400)
        self.db.rollback()
        self.assertFalse(user.is_verified)
        self.assertIsNone(user.student_id)
        self.assertEqual(user.ai_review_status, "pending")
        self.assertEqual(owner.student_id, "260123456")

    def test_manual_review_rejects_stale_image_without_writing_student_id(self):
        user = self.applicant(verify_image="/uploads/current.png", ai_review_status="pending")
        with self.assertRaises(HTTPException) as error:
            auth_service.admin_update_user(self.db, user.id, student_id="260123456", is_verified=True,
                                           expected_verify_image="/uploads/old.png")
        self.assertEqual(error.exception.status_code, 409)
        self.db.rollback()
        self.assertFalse(user.is_verified)
        self.assertIsNone(user.student_id)
        self.assertEqual(user.verify_image, "/uploads/current.png")

    def test_first_rank_failures_enter_existing_manual_review_queue(self):
        cases = [False, TimeoutError("simulated"),
                 {"rank": "A", "confidence": 0.5},
                 {"rank": None, "confidence": 0.99},
                 {"rank": "A", "confidence": "0.99"},
                 {"rank": "invalid", "confidence": 0.99}]
        for payload in cases:
            with self.subTest(payload=payload):
                user = self.applicant(rank=None, individual_rating=0)
                self.vision.side_effect = payload if isinstance(payload, Exception) else None
                self.vision.return_value = payload
                with patch.object(settings, "AI_REVIEW_ENABLED", payload is not False):
                    result = auth.upload_rank(file=None, db=self.db, current_user=user)
                self.assertTrue(result["need_review"])
                self.assertFalse(result["auto_applied"])
                self.assertIsNone(user.rank)
                self.assertEqual(user.rank_image, "/uploads/new.png")
                pending = auth_service.get_pending_rank_application(self.db, user.id)
                self.assertIsNotNone(pending)
                self.assertEqual(pending.rank_image, user.rank_image)
                self.assertIn(pending, auth_service.get_pending_rank_applications(self.db))

    def test_first_high_confidence_rank_applies_and_recalculates_team(self):
        user = self.applicant(rank=None, individual_rating=0)
        team = self.make_team(captain=user)
        self.db.commit()
        self.vision.return_value = {"rank": "s20", "confidence": 0.99}
        result = auth.upload_rank(file=None, db=self.db, current_user=user)
        self.assertTrue(result["auto_applied"])
        self.assertEqual(user.rank, "S20")
        self.assertEqual(user.individual_rating, auth_service._rank_rating("S20"))
        self.assertEqual(team.rating, user.individual_rating)
        self.assertIsNone(auth_service.get_pending_rank_application(self.db, user.id))

    def test_repeated_upload_is_rejected_before_image_or_ai_io(self):
        user = self.applicant(rank=None)
        auth_service.create_rank_application(self.db, user.id, rank_image="/uploads/pending.png")
        with self.assertRaises(HTTPException) as error:
            auth.upload_rank(file=None, db=self.db, current_user=user)
        self.assertEqual(error.exception.status_code, 400)
        self.save_image.assert_not_called()
        self.vision.assert_not_called()
        self.assertEqual(self.db.query(RankApplication).count(), 1)

    def test_pending_created_while_ai_runs_prevents_duplicate_application(self):
        user = self.applicant(rank=None)

        def competing_upload(*_args):
            auth_service.create_rank_application(self.db, user.id, rank_image="/uploads/other.png")
            return {"rank": "A", "confidence": 0.99}

        self.vision.side_effect = competing_upload
        with self.assertRaises(HTTPException):
            auth.upload_rank(file=None, db=self.db, current_user=user)
        self.assertEqual(self.db.query(RankApplication).count(), 1)
        self.assertIsNone(user.rank)

    def test_first_rank_cannot_be_approved_without_a_valid_rank(self):
        user = self.applicant(rank=None, individual_rating=0)
        application = auth_service.create_rank_application(self.db, user.id,
                                                          rank_image="/uploads/pending.png")
        with self.assertRaises(HTTPException) as error:
            auth_service.approve_rank_application(self.db, application.id)
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(application.status, "pending")
        self.assertIsNone(user.rank)
        auth_service.approve_rank_application(self.db, application.id, "B++")
        self.assertEqual(application.status, "approved")
        self.assertEqual(user.rank, "B++")
        self.assertEqual(user.individual_rating, auth_service._rank_rating("B++"))

    def test_existing_rank_and_image_change_only_after_manual_approval(self):
        user = self.applicant(rank="A", rank_image="/uploads/current.png")
        self.vision.return_value = {"rank": "S10", "confidence": 0.99}
        result = auth.upload_rank(file=None, db=self.db, current_user=user)
        self.assertTrue(result["need_review"])
        self.assertEqual(user.rank, "A")
        self.assertEqual(user.rank_image, "/uploads/current.png")
        application = auth_service.get_pending_rank_application(self.db, user.id)
        auth_service.approve_rank_application(self.db, application.id)
        self.assertEqual(user.rank, "S10")
        self.assertEqual(user.rank_image, "/uploads/new.png")
