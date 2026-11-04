"""Assistant public-query boundaries, isolated DB only; no model/network calls."""
from datetime import datetime, timedelta
import json
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event

from tests.support import DatabaseTestCase
from app.models.chat import ChatConversation, ChatMessage
from app.models.match import (
    MatchRound, MatchStatus, Registration, RegistrationStatus, RoundStatus,
    StageStatus, TeamProgress,
)
from app.models.recruitment import RecruitmentPost, WechatOutbox
from app.models.team import TeamStatus, TeamApplication, TeamInvitation
from app.services import assistant_query_service as service
from app.services.player_stats_service import import_perfect_maps


class AssistantQueryTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.make_user(nickname="当前用户")

    def call(self, name, **args):
        return service.dispatch_read_tool(self.db, self.actor, name, args)

    def add_round(self, match, left, right, **kwargs):
        values = dict(match_id=match.id, team1_id=left.id if left else None,
                      team2_id=right.id if right else None, round_number=1,
                      group_name="A", status=RoundStatus.PENDING)
        values.update(kwargs)
        row = MatchRound(**values)
        self.db.add(row)
        self.db.flush()
        return row

    def stats_fixture(self, match=None, hidden=False):
        match = match or self.make_match()
        left, right = self.make_team(is_hidden=hidden), self.make_team()
        for team in (left, right):
            self.db.add(TeamProgress(match_id=match.id, team_id=team.id, stage=StageStatus.LEGEND))
        self.db.commit()
        payload = {"source": "perfect_match", "external_match_id": str(10000 + match.id),
            "source_url": "https://match.wmpvp.com/csgo/detail/" + str(10000 + match.id),
            "cup_name": "测试杯", "map_name": "de_mirage", "team1_id": left.id, "team2_id": right.id,
            "source_team1_name": left.name, "source_team2_name": right.name,
            "team1_score": 13, "team2_score": 5,
            "players": [{"steam_id": "76561198000000001", "nickname": "同名选手", "team_slot": 1,
                "source_team_id": "1", "kills": 20, "deaths": 10, "assists": 3,
                "adr": 100, "rating": 1.5, "first_kills": 2},
                {"steam_id": "76561198000000002", "nickname": "同名选手", "team_slot": 2,
                "source_team_id": "2", "kills": 10, "deaths": 20, "assists": 2,
                "adr": None, "rating": None, "first_kills": None}]}
        import_perfect_maps(self.db, match.id, [payload])
        return match, left, right

    def test_tools_are_closed_schemas_and_reject_forged_identity_types_or_unknown_tools(self):
        for tool in service.READ_TOOL_DEFINITIONS:
            schema = tool["function"]["parameters"]
            self.assertNotIn("strict", tool["function"])
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["required"], [])
        for name, args in (("register_user", {}), ("get_my_registration", {"user_id": 2}),
                           ("list_matches", {"limit": True}), ("recommend_players", {"rank": "arbitrary"}),
                           ("find_teams", {"team_id": -1})):
            with self.subTest(name=name, args=args), self.assertRaises(HTTPException):
                self.call(name, **args)
        with self.assertRaises(HTTPException):
            service.dispatch_read_tool(self.db, None, "list_matches", {})

    def test_public_matches_hide_drafts_and_distinguish_pending_from_approved_count(self):
        self.make_match(name="草稿", status=MatchStatus.DRAFT)
        match = self.make_match(name="公开杯", register_start=datetime(2026, 11, 1, 13))
        team = self.make_team()
        self.db.add(Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id,
                                 status=RegistrationStatus.PENDING))
        self.db.flush()
        result = self.call("list_matches")
        self.assertEqual(len(result["data"]["items"]), 1)
        row = result["data"]["items"][0]
        self.assertEqual((row["registered_team_count"], row["approved_team_count"]), (1, 0))
        self.assertEqual(row["register_start"], "2026-11-01T13:00:00+08:00")

    def test_multiple_event_matches_require_selection_and_explicit_draft_is_unavailable(self):
        self.make_match(name="起源杯甲")
        self.make_match(name="起源杯乙")
        draft = self.make_match(status=MatchStatus.DRAFT)
        result = self.call("get_match_rounds", match_keyword="起源杯")
        self.assertEqual(result["status"], "needs_selection")
        self.assertEqual(len(result["cards"][0]["items"]), 2)
        self.assertEqual(self.call("get_my_registration", match_id=draft.id)["status"], "not_found")

    def test_registration_query_is_self_only_and_does_not_turn_pending_into_approval(self):
        match = self.make_match()
        other = self.make_user()
        self.db.add_all([Registration(match_id=match.id, user_id=self.actor.id, status=RegistrationStatus.PENDING),
                         Registration(match_id=match.id, user_id=other.id, status=RegistrationStatus.APPROVED)])
        self.db.flush()
        result = self.call("get_my_registration", match_id=match.id)
        self.assertEqual(result["data"]["registration_status"], "pending")
        self.assertFalse(result["data"]["review_approved"])
        self.assertIn("尚未审核通过", result["message"])

    def test_team_lookup_is_approved_visible_and_scoped_to_event(self):
        match = self.make_match()
        visible = self.make_team(name="可见队")
        other = self.make_team(name="其他赛队")
        self.make_team(name="隐藏队", is_hidden=True)
        self.make_team(name="待审队", status=TeamStatus.PENDING)
        self.db.add(TeamProgress(match_id=match.id, team_id=visible.id, stage=StageStatus.LEGEND, group_name="上区"))
        self.db.flush()
        result = self.call("find_teams", match_id=match.id)
        self.assertEqual([r["id"] for r in result["data"]["items"]], [visible.id])
        self.assertEqual(result["data"]["items"][0]["group_name"], "上区")
        self.assertEqual(self.call("find_teams", match_id=match.id, team_id=other.id)["status"], "not_found")

    def test_bo3_series_maps_and_pending_zero_placeholders_are_separate(self):
        match = self.make_match()
        left, right = self.make_team(), self.make_team()
        played = self.add_round(match, left, right, group_name="淘汰赛", status=RoundStatus.FINISHED,
            team1_score=2, team2_score=1, winner_id=left.id,
            bo3_scores=json.dumps([{"t1": 13, "t2": 5}, {"t1": 9, "t2": 13}, {"t1": 13, "t2": 11}]))
        pending = self.add_round(match, left, right, round_number=2)
        result = self.call("get_match_rounds", match_id=match.id)
        by_id = {r["id"]: r for r in result["data"]["items"]}
        self.assertEqual(by_id[played.id]["score"], {"team1": 2, "team2": 1})
        self.assertEqual(by_id[played.id]["score_unit"], "maps")
        self.assertEqual(by_id[played.id]["map_scores"][0], {"map_number": 1, "team1_rounds": 13, "team2_rounds": 5})
        self.assertIsNone(by_id[pending.id]["score"])
        self.assertIsNone(by_id[pending.id]["winner_id"])

    def test_next_round_uses_own_team_current_status_and_does_not_cross_events(self):
        match, other_event = self.make_match(), self.make_match()
        own = self.make_team(captain=self.actor)
        other, third = self.make_team(), self.make_team()
        self.add_round(match, other, third, scheduled_time=datetime(2026, 11, 1, 12))
        target = self.add_round(match, own, other, scheduled_time=datetime(2026, 11, 1, 13),
            team1_confirmed=True, team2_confirmed=True)
        self.add_round(other_event, own, other, scheduled_time=datetime(2026, 11, 1, 11))
        result = self.call("get_match_rounds", match_id=match.id, my_team=True, next_only=True)
        self.assertEqual([r["id"] for r in result["data"]["items"]], [target.id])
        self.assertEqual(result["data"]["items"][0]["schedule_status"], "confirmed")

    def test_hidden_opponents_do_not_leak_through_old_rounds_or_my_team(self):
        match = self.make_match()
        left, hidden = self.make_team(), self.make_team(is_hidden=True)
        self.add_round(match, left, hidden, status=RoundStatus.FINISHED, team1_score=13, team2_score=0)
        result = self.call("get_match_rounds", match_id=match.id)
        self.assertEqual(result["data"]["items"], [])
        self.assertNotIn(hidden.name, json.dumps(result))

    def test_missing_or_corrupt_bo3_map_scores_do_not_fabricate_or_crash(self):
        match = self.make_match()
        left, right = self.make_team(), self.make_team()
        self.add_round(match, left, right, group_name="淘汰赛", status=RoundStatus.FINISHED,
                       team1_score=2, team2_score=0, bo3_scores="bad-json")
        row = self.call("get_match_rounds", match_id=match.id)["data"]["items"][0]
        self.assertEqual(row["score"], {"team1": 2, "team2": 0})
        self.assertTrue(row["map_scores_missing"])
        self.assertIsNone(row["map_scores"])

    def test_imported_stats_preserve_missing_coverage_and_disambiguate_without_account_binding(self):
        match, _, _ = self.stats_fixture()
        self.make_user(nickname="同名选手", student_id="251101111")
        result = self.call("get_player_stats", match_id=match.id, keyword="同名选手")
        self.assertEqual(result["status"], "needs_selection")
        rows = result["data"]["items"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["player_key"], rows[1]["player_key"])
        self.assertIsNone(rows[1]["adr"])
        self.assertEqual(rows[1]["adr_maps"], 0)
        self.assertEqual(result["data"]["map_count"], 1)
        self.assertEqual(result["data"]["account_binding"], "none")
        selection = result["data"]["selection"]
        self.assertEqual(selection, {"kind": "stats_player", "match_id": match.id, "requires_selection": True,
            "candidates": [{"player_key": row["player_key"], "name": row["nickname"]} for row in rows]})
        self.assertEqual(result["cards"][0]["items"][1]["action"], {
            "kind": "select_stats_player", "player_key": rows[1]["player_key"], "match_id": match.id})
        self.assertNotIn("user_id", json.dumps(selection))
        for metric in ("kills", "adr", "rating", "maps_played"):
            self.assertNotIn(metric, json.dumps(selection))
        exact = self.call("get_player_stats", match_id=match.id, player_key=rows[1]["player_key"])
        self.assertEqual(len(exact["data"]["items"]), 1)
        serialized = json.dumps(result)
        for secret in ("steam_id", "76561198000000001", "251101111", "wx_openid", "student_id", "verify_image", "user_id"):
            self.assertNotIn(secret, serialized)

    def test_hidden_source_teams_hide_stats_and_keys_do_not_transfer_between_events(self):
        first, _, _ = self.stats_fixture()
        key = self.call("get_player_stats", match_id=first.id)["data"]["items"][0]["player_key"]
        second, _, _ = self.stats_fixture()
        self.assertEqual(self.call("get_player_stats", match_id=second.id, player_key=key)["status"], "not_found")
        hidden, _, _ = self.stats_fixture(hidden=True)
        self.assertEqual(self.call("get_player_stats", match_id=hidden.id)["data"]["items"], [])

    def test_recruitment_filters_free_players_and_direct_id_lookup_can_show_already_teamed_user(self):
        free = self.make_user(nickname="自由新生", identity="new_student", rank="A+")
        member = self.make_user(nickname="已入队", identity="new_student", rank="A+")
        team = self.make_team(captain=member)
        self.make_user(nickname="隐藏自由人", is_hidden=True, identity="new_student", rank="A+")
        self.db.flush()
        result = self.call("recommend_players", rank="A+", identity="new_student")
        self.assertEqual([r["id"] for r in result["data"]["items"]], [free.id])
        direct = self.call("recommend_players", user_id=member.id)
        self.assertTrue(direct["data"]["items"][0]["has_team"])
        self.assertEqual(direct["data"]["items"][0]["team_name"], team.name)
        self.assertEqual(self.call("recommend_players", user_id=999999)["status"], "not_found")

    def test_recommendation_sort_is_explicit_stable_and_unknowns_are_not_invented(self):
        low = self.make_user(nickname="低分", individual_rating=1, rank="D", user_description="我擅长站位")
        high = self.make_user(nickname="高分", individual_rating=50, rank="S50")
        stable = self.call("recommend_players")["data"]
        self.assertEqual([r["id"] for r in stable["items"]], [low.id, high.id])
        ranked = self.call("recommend_players", order_by="rank")["data"]
        self.assertEqual([r["id"] for r in ranked["items"]], [high.id, low.id])
        self.assertEqual(ranked["items"][0]["unknown_fields"], ["位置偏好", "空闲时间"])
        self.assertNotIn("percent", json.dumps(ranked))

    def test_public_description_redaction_and_all_tools_remain_read_only(self):
        match = self.make_match()
        self.make_user(nickname="可见", user_description="学号251101111 bearer abcdefghijklmnop 我是狙击手")
        self.db.commit()
        writes = []
        def watch(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE"):
                writes.append(statement)
        event.listen(self.engine, "before_cursor_execute", watch)
        result = self.call("recommend_players")
        self.call("list_matches")
        self.call("get_my_registration", match_id=match.id)
        self.call("find_teams")
        self.call("get_match_rounds", match_id=match.id)
        self.call("get_player_stats", match_id=match.id)
        self.call("recommend_teams")
        self.assertEqual(writes, [])
        text = json.dumps(result)
        self.assertNotIn("251101111", text)
        self.assertNotIn("abcdefghijklmnop", text)
        for model in (ChatConversation, ChatMessage, TeamApplication, TeamInvitation, WechatOutbox):
            self.assertEqual(self.db.query(model).count(), 0)

    def test_team_recommendations_require_active_public_post_and_real_application_precheck(self):
        self.actor.is_verified = False
        active = self.make_match()
        enrolled = self.make_team(name="已报名队")
        open_team = self.make_team(name="可联系队")
        hidden = self.make_team(is_hidden=True)
        closed = self.make_team()
        for team, enabled in ((enrolled, True), (open_team, True), (hidden, True), (closed, False)):
            self.db.add(RecruitmentPost(team_id=team.id, content="请先站内联系", is_active=enabled))
        self.db.add(Registration(match_id=active.id, team_id=enrolled.id, user_id=enrolled.captain_id))
        self.db.flush()
        result = self.call("recommend_teams")
        self.assertEqual([row["id"] for row in result["data"]["items"]], [open_team.id])
        self.assertEqual(result["cards"][0]["items"][0]["action"], {"kind": "contact_team", "id": open_team.id})
        all_rows = self.call("recommend_teams", eligible_only=False)["data"]["items"]
        self.assertEqual([r["id"] for r in all_rows], [open_team.id, enrolled.id])
        self.assertEqual(all_rows[1]["join_precheck"]["status"], "blocked")

    def test_recruitment_into_full_registered_team_is_not_recommended(self):
        match = self.make_match(team_size=1)
        team = self.make_team()
        self.db.add_all([RecruitmentPost(team_id=team.id, content="招人"),
            Registration(match_id=match.id, team_id=team.id, user_id=team.captain_id)])
        candidate = self.make_user()
        self.db.flush()
        result = self.call("recommend_players", team_id=team.id)
        self.assertEqual(result["data"]["items"], [])
        self.assertEqual(self.call("recommend_teams")["data"]["items"], [])

    def test_filtered_player_scan_has_a_hard_page_budget(self):
        candidate = {"id": self.actor.id, "has_team": False, "is_verified": True}
        with patch.object(service.recruitment_service, "public_players", return_value={
                "items": [candidate], "has_more": True, "next_cursor": 1}) as call:
            result = self.call("recommend_players")
        self.assertEqual(call.call_count, 10)
        self.assertFalse(result["data"]["scan_complete"])


if __name__ == "__main__":
    import unittest
    unittest.main()
