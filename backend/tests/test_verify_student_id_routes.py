"""School ID recognition uses local synthetic evidence and mocked AI only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.support import DatabaseTestCase, settings
from tests.test_manual_champions import request
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.user import UserRole
from app.routers.auth import router
from app.services import ai_review_service, auth_service


class VerifyStudentIdRouteTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.addCleanup(patch.stopall)
        self.files = TemporaryDirectory(prefix="verify-student-id-")
        self.addCleanup(self.files.cleanup)
        self.evidence_path = Path(self.files.name) / "evidence.png"
        self.evidence_path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-test-only")
        patch.object(settings, "UPLOAD_DIR", self.files.name).start()
        patch.object(settings, "AI_REVIEW_ENABLED", True).start()
        self.vision = patch.object(ai_review_service, "review_image", return_value={
            "student_id": "260123456", "confidence": 0.71,
            "reason": "识别到学号，需核对原图", "is_valid": True,
        }).start()

        self.app = FastAPI()
        self.app.include_router(router)

        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db

        self.app.dependency_overrides[get_db] = isolated_db
        self.admin = self.make_user(role=UserRole.ADMIN)
        self.user = self.make_user(student_id=None, is_verified=False,
                                   verify_image="/uploads/evidence.png", ai_review_status="pending")
        self.db.commit()
        self.token = auth_service.create_access_token(self.admin.id, "admin")
        self.path = f"/api/auth/admin/users/{self.user.id}/recognize-student-id"

    def recognize(self, **changes):
        payload = {"expected_verify_image": "/uploads/evidence.png", **changes}
        return request(self.app, "POST", self.path, payload, self.token)

    def refresh_user(self):
        self.db.expire_all()
        return auth_service.get_user_by_id(self.db, self.user.id)

    def test_recognition_requires_current_manager_role(self):
        self.assertEqual(request(self.app, "POST", self.path,
                                 {"expected_verify_image": "/uploads/evidence.png"})[0], 401)
        for role in UserRole:
            with self.subTest(role=role):
                actor = self.make_user(role=role)
                self.db.commit()
                token = auth_service.create_access_token(actor.id, "admin")
                status, _ = request(self.app, "POST", self.path,
                                    {"expected_verify_image": "/uploads/evidence.png"}, token)
                self.assertEqual(status, 200 if role in (UserRole.ADMIN, UserRole.REVIEWER) else 403)

    def test_recognition_returns_candidate_without_approving_or_changing_real_student_id(self):
        self.user.student_id = "260222222"
        self.db.commit()
        status, result = self.recognize()
        self.assertEqual(status, 200)
        self.assertEqual(result, {"student_id": "260123456", "confidence": 0.71,
                                  "reason": "识别到学号，需核对原图", "verify_image": "/uploads/evidence.png"})
        user = self.refresh_user()
        self.assertEqual(user.ai_student_id, "260123456")
        self.assertEqual(user.student_id, "260222222")
        self.assertFalse(user.is_verified)
        self.assertEqual(user.ai_review_status, "pending")
        self.assertEqual(Path(self.vision.call_args.args[0]), self.evidence_path.resolve())

    def test_already_approved_missing_student_id_can_recognize_and_save_without_reupload(self):
        self.user.is_verified = True
        self.user.ai_review_status = "manual_pass"
        self.db.commit()
        status, _ = self.recognize()
        self.assertEqual(status, 200)
        user = self.refresh_user()
        self.assertTrue(user.is_verified)
        self.assertIsNone(user.student_id)
        self.assertEqual(user.ai_review_status, "manual_pass")
        self.assertEqual(user.ai_student_id, "260123456")
        status, result = request(self.app, "PUT", f"/api/auth/admin/users/{user.id}", {
            "student_id": "260123456", "is_verified": True,
            "expected_verify_image": "/uploads/evidence.png",
        }, self.token)
        self.assertEqual(status, 200)
        self.assertTrue(result["is_verified"])
        self.assertEqual(result["student_id"], "260123456")

    def test_ai_disabled_and_no_image_fail_before_vision_io(self):
        with patch.object(settings, "AI_REVIEW_ENABLED", False):
            self.assertEqual(self.recognize()[0], 400)
        self.user.verify_image = None
        self.db.commit()
        self.assertEqual(self.recognize()[0], 400)
        self.vision.assert_not_called()

    def test_missing_user_and_missing_local_image_return_404(self):
        self.assertEqual(request(self.app, "POST", "/api/auth/admin/users/99999/recognize-student-id",
                                 {"expected_verify_image": "/uploads/evidence.png"}, self.token)[0], 404)
        self.evidence_path.unlink()
        self.assertEqual(self.recognize()[0], 404)
        self.user.verify_image = "/uploads/sub/evidence.png"
        self.db.commit()
        self.assertEqual(self.recognize(expected_verify_image=self.user.verify_image)[0], 404)
        self.vision.assert_not_called()

    def test_existing_image_in_safe_upload_subdirectory_can_be_recognized(self):
        nested_image = Path(self.files.name) / "sub" / "evidence.png"
        nested_image.parent.mkdir()
        nested_image.write_bytes(self.evidence_path.read_bytes())
        self.user.verify_image = "/uploads/sub/evidence.png"
        self.db.commit()
        status, result = self.recognize(expected_verify_image=self.user.verify_image)
        self.assertEqual(status, 200)
        self.assertEqual(result["student_id"], "260123456")
        self.assertEqual(Path(self.vision.call_args.args[0]), nested_image.resolve())

    def test_remote_and_traversal_image_paths_are_rejected_without_io(self):
        unsafe = ("https://example.test/evidence.png", "/uploads/../secret.png", "/uploads/..\\secret.png",
                  "/uploads/C:\\secret.png", "/other/evidence.png")
        for url in unsafe:
            with self.subTest(url=url):
                self.user.verify_image = url
                self.db.commit()
                self.assertEqual(self.recognize(expected_verify_image=url)[0], 400)
        self.vision.assert_not_called()

    def test_stale_image_rejected_before_ai_call(self):
        self.assertEqual(self.recognize(expected_verify_image="/uploads/old.png")[0], 409)
        self.vision.assert_not_called()

    def test_model_failure_preserves_previous_candidate_and_verification(self):
        self.user.ai_student_id = "260999999"
        self.db.commit()
        for outcome in (TimeoutError("simulated AI failure"), {"confidence": "bad"}):
            with self.subTest(outcome=type(outcome).__name__):
                self.vision.side_effect = outcome if isinstance(outcome, Exception) else None
                self.vision.return_value = outcome
                self.assertEqual(self.recognize()[0], 502)
                user = self.refresh_user()
                self.assertEqual(user.ai_student_id, "260999999")
                self.assertIsNone(user.student_id)
                self.assertFalse(user.is_verified)

    def test_no_detected_student_id_returns_empty_candidate_without_auto_approval(self):
        self.vision.return_value = {"student_id": None, "confidence": 0.5,
                                   "reason": "学号不可辨认", "is_valid": False}
        status, result = self.recognize()
        self.assertEqual(status, 200)
        self.assertIsNone(result["student_id"])
        user = self.refresh_user()
        self.assertIsNone(user.ai_student_id)
        self.assertIsNone(user.student_id)
        self.assertFalse(user.is_verified)

    def test_new_upload_while_recognizing_returns_conflict_and_never_saves_old_candidate(self):
        def new_upload(*_args):
            with Session(self.engine) as concurrent_db:
                auth_service.update_verify_image(concurrent_db, self.user.id, "/uploads/newer.png")
            return {"student_id": "260123456", "confidence": 0.9, "reason": "旧截图", "is_valid": True}

        self.vision.side_effect = new_upload
        self.assertEqual(self.recognize()[0], 409)
        user = self.refresh_user()
        self.assertEqual(user.verify_image, "/uploads/newer.png")
        self.assertIsNone(user.ai_student_id)
        self.assertIsNone(user.student_id)

    def test_manual_student_id_change_while_recognizing_returns_conflict(self):
        def confirm_student_id(*_args):
            with Session(self.engine) as concurrent_db:
                auth_service.admin_update_user(concurrent_db, self.user.id,
                                               student_id="260222222", is_verified=True)
            return {"student_id": "260123456", "confidence": 0.9, "reason": "旧结果", "is_valid": True}

        self.vision.side_effect = confirm_student_id
        self.assertEqual(self.recognize()[0], 409)
        user = self.refresh_user()
        self.assertTrue(user.is_verified)
        self.assertEqual(user.student_id, "260222222")
        self.assertEqual(user.ai_review_status, "manual_pass")
        self.assertIsNone(user.ai_student_id)

    def test_manual_rejection_while_recognizing_preserves_review_decision(self):
        def reject_evidence(*_args):
            with Session(self.engine) as concurrent_db:
                auth_service.admin_update_user(concurrent_db, self.user.id, is_verified=False,
                                               verify_reject_reason="人工驳回")
            return {"student_id": "260123456", "confidence": 0.9, "reason": "旧结果", "is_valid": True}

        self.vision.side_effect = reject_evidence
        self.assertEqual(self.recognize()[0], 409)
        user = self.refresh_user()
        self.assertFalse(user.is_verified)
        self.assertEqual(user.ai_review_status, "manual_reject")
        self.assertEqual(user.verify_reject_reason, "人工驳回")
        self.assertIsNone(user.ai_student_id)

    def test_admin_api_requires_confirmation_and_exposes_candidate_on_review_list(self):
        self.user.ai_student_id = "260123456"
        self.db.commit()
        status, items = request(self.app, "GET", "/api/auth/admin/verify-list", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(items[0]["ai_student_id"], "260123456")
        status, _ = request(self.app, "PUT", f"/api/auth/admin/users/{self.user.id}",
                            {"is_verified": True}, self.token)
        self.assertEqual(status, 400)
        self.assertFalse(self.refresh_user().is_verified)
        status, result = request(self.app, "PUT", f"/api/auth/admin/users/{self.user.id}", {
            "is_verified": True, "student_id": " 260123456 ",
            "expected_verify_image": "/uploads/evidence.png",
        }, self.token)
        self.assertEqual(status, 200)
        self.assertEqual(result["student_id"], "260123456")
        self.assertTrue(result["is_verified"])

    def test_admin_api_propagates_expected_image_and_keeps_newer_evidence_untouched(self):
        status, _ = request(self.app, "PUT", f"/api/auth/admin/users/{self.user.id}", {
            "is_verified": True, "student_id": "260123456", "expected_verify_image": "/uploads/old.png",
        }, self.token)
        self.assertEqual(status, 409)
        user = self.refresh_user()
        self.assertEqual(user.verify_image, "/uploads/evidence.png")
        self.assertIsNone(user.student_id)
        self.assertFalse(user.is_verified)
