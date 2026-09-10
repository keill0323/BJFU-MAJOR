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

    def test_resubmission_clears_old_rejection_even_when_ai_fails(self):
        user = self.applicant(
            verify_image="/uploads/old.png", ai_review_status="manual_reject",
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
