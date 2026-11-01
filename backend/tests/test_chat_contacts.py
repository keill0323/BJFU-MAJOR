"""Contact search regressions using isolated SQLite; never call external services."""
import asyncio
import json
from urllib.parse import urlencode
from unittest.mock import patch

from tests.support import DatabaseTestCase
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.models.user import UserRole
from app.routers.chat import router
from app.services import chat_service as chat
from app.services.auth_service import create_access_token


class ChatContactSearchTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        for key, value in {"CHAT_ENABLED": True, "CHAT_ALLOW_USERS": ""}.items():
            setting = patch.object(settings, key, value)
            setting.start()
            self.addCleanup(setting.stop)
        self.requester = self.make_user(id=1, nickname="搜索发起者", game_id="requester")
        self.db.commit()
        self.app = FastAPI()
        self.app.include_router(router)

        def isolated_db():
            with Session(self.engine, autoflush=False) as db:
                yield db

        self.app.dependency_overrides[get_db] = isolated_db

    def ids(self, keyword="", **kwargs):
        self.db.commit()
        return [item["id"] for item in chat.contacts(self.db, self.requester.id, keyword, **kwargs)["items"]]

    def request_contacts(self, params, authenticated=True):
        self.db.commit()
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        headers = []
        if authenticated:
            token = create_access_token(self.requester.id, "user")
            headers.append((b"authorization", f"Bearer {token}".encode()))
        asyncio.run(self.app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": "/api/chat/contacts",
            "raw_path": b"/api/chat/contacts", "query_string": urlencode(params).encode(),
            "root_path": "", "headers": headers, "client": ("127.0.0.1", 1), "server": ("test", 80),
        }, receive, send))
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return status, json.loads(body)

    def test_system_id_matches_exactly_without_matching_other_ids(self):
        self.make_user(id=114, nickname="目标选手", game_id="target")
        self.make_user(id=1114, nickname="另一位选手", game_id="another")
        self.make_user(id=1140, nickname="无关选手", game_id="unrelated")
        self.assertEqual(self.ids("114"), [114])

    def test_numeric_keyword_preserves_nickname_and_game_id_matches(self):
        self.make_user(id=114, nickname="目标选手", game_id="target")
        nickname_match = self.make_user(nickname="选手114号", game_id="nickname-match")
        game_match = self.make_user(nickname="游戏玩家", game_id="match114suffix")
        self.assertEqual(set(self.ids("114")), {114, nickname_match.id, game_match.id})

    def test_system_id_trims_whitespace_and_accepts_leading_zeroes(self):
        self.make_user(id=114, nickname="目标选手", game_id="target")
        text_match = self.make_user(nickname="玩家00114", game_id="literal-zeroes")
        self.assertEqual(set(self.ids(" \t114\n ")), {114, text_match.id})
        self.assertEqual(set(self.ids(" 00114 ")), {114, text_match.id})

    def test_maximum_integer_id_and_long_zero_prefix_are_safe(self):
        self.make_user(id=2147483647, nickname="最大编号选手", game_id="maximum")
        self.assertEqual(self.ids("2147483647"), [2147483647])
        self.assertEqual(self.ids("0" * 40 + "2147483647"), [2147483647])

    def test_oversized_numbers_remain_literal_text_searches(self):
        for keyword in ("2147483648", "9" * 64, "0"):
            with self.subTest(keyword=keyword):
                match = self.make_user(nickname=keyword, game_id="text-match")
                self.assertEqual(self.ids(keyword), [match.id])

    def test_non_ascii_and_signed_digits_do_not_become_system_ids(self):
        self.make_user(id=114, nickname="目标选手", game_id="target")
        for keyword in ("１１４", "١١٤", "+114", "-114"):
            with self.subTest(keyword=keyword):
                match = self.make_user(nickname="玩家" + keyword, game_id="text-match")
                self.assertEqual(self.ids(keyword), [match.id])

    def test_hidden_users_and_requester_are_excluded_from_id_and_text_matches(self):
        self.requester.nickname = "玩家114"
        self.make_user(id=114, nickname="目标选手", is_hidden=True)
        visible = self.make_user(nickname="正常选手", game_id="visible")
        self.assertEqual(self.ids("114"), [])
        self.assertEqual(self.ids("1"), [])
        self.assertEqual(self.ids(), [visible.id])

    def test_student_id_and_openid_are_not_searchable_or_serialized(self):
        self.make_user(id=114, nickname="目标选手", game_id="target", student_id="private-student-id", wx_openid="private-openid")
        self.db.commit()
        self.assertEqual(self.ids("private-student-id"), [])
        self.assertEqual(self.ids("private-openid"), [])
        result = chat.contacts(self.db, self.requester.id, "114")
        self.assertEqual(result["items"][0]["id"], 114)
        self.assertNotIn("private-student-id", json.dumps(result))
        self.assertNotIn("private-openid", json.dumps(result))
        self.assertNotIn("student_id", result["items"][0])
        self.assertNotIn("wx_openid", result["items"][0])

    def test_existing_text_search_escapes_like_wildcards(self):
        nickname_match = self.make_user(nickname="玩家%_标记", game_id="nickname-match")
        game_match = self.make_user(nickname="游戏玩家", game_id="game%_suffix")
        self.make_user(nickname="玩家XY标记", game_id="gameABsuffix")
        self.assertEqual(set(self.ids("%_")), {nickname_match.id, game_match.id})
        slash_match = self.make_user(nickname="玩家/_标记", game_id="slash-match")
        self.assertEqual(self.ids("/_"), [slash_match.id])

    def test_cursor_and_admin_contact_mode_keep_existing_behavior(self):
        self.make_user(id=114, nickname="目标选手", game_id="target")
        numeric_name = self.make_user(id=115, nickname="玩家114", game_id="numeric-name")
        admin = self.make_user(id=116, nickname="管理员", game_id="admin", role=UserRole.ADMIN)
        self.assertEqual(self.ids("114", before_id=numeric_name.id), [114])
        self.assertEqual(self.ids("114", admins=True), [admin.id])

    def test_http_search_accepts_numeric_keyword_and_requires_authentication(self):
        self.make_user(id=114, nickname="目标选手", game_id="target")
        self.make_user(id=1114, nickname="无关选手", game_id="unrelated")
        status, result = self.request_contacts({"keyword": " 00114 "})
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in result["items"]], [114])
        self.assertFalse(result["has_more"])
        self.assertIsNone(result["next_cursor"])
        self.assertEqual(self.request_contacts({"keyword": "114"}, authenticated=False)[0], 401)

    def test_http_oversized_keyword_is_rejected_by_existing_limit(self):
        self.assertEqual(self.request_contacts({"keyword": "9" * 65})[0], 422)
