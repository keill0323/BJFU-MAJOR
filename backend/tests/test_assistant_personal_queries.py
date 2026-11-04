"""JWT-scoped team advice and contextual next-match queries; isolated DB only."""
from datetime import datetime
from contextlib import ExitStack
import json
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import event

from tests.support import DatabaseTestCase
from app.config import settings
from app.models.match import MatchRound, MatchStatus, RoundStatus
from app.models.team import Team, TeamMember, TeamStatus, MemberRole
from app.services import assistant_query_service as service
from app.services import assistant_service as agent


class AssistantPersonalQueryTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.make_user(nickname="本人", student_id="261234567")

    def call(self, name="get_my_team", **arguments):
        return service.dispatch_read_tool(self.db, self.actor, name, arguments)

    def add_round(self, match, left, right, **kwargs):
        values = dict(match_id=match.id, team1_id=left.id if left else None,
                      team2_id=right.id if right else None, round_number=1,
                      group_name="A", status=RoundStatus.PENDING)
        values.update(kwargs)
        row = MatchRound(**values)
        self.db.add(row)
        self.db.flush()
        return row

    def next(self, **arguments):
        return self.call("get_match_rounds", next_only=True, **arguments)

    def test_captain_advice_uses_real_team_authority_and_no_account_details(self):
        own = self.make_team(captain=self.actor, description="私密介绍不应进入本人队伍诊断")
        result = self.call()
        self.assertEqual(result["data"]["team_id"], own.id)
        self.assertEqual(result["data"]["team_name"], own.name)
        self.assertTrue(result["data"]["is_captain"])
        self.assertEqual(result["data"]["recommended_registration_mode"], "team")
        serialized = json.dumps(result, ensure_ascii=False)
        for private in (self.actor.student_id, self.actor.wx_openid, own.description,
                        "captain_id", "student_id", "wx_openid", "members"):
            self.assertNotIn(private, serialized)

    def test_member_cannot_become_captain_from_the_membership_role_label(self):
        own = self.make_team(members=[self.actor])
        member = self.db.query(TeamMember).filter_by(user_id=self.actor.id).one()
        member.role = MemberRole.CAPTAIN
        self.db.flush()
        result = self.call()
        self.assertEqual(result["data"]["team_id"], own.id)
        self.assertFalse(result["data"]["is_captain"])
        self.assertFalse(result["data"]["can_request_team_registration"])
        self.assertIsNone(result["data"]["recommended_registration_mode"])
        self.assertIn("队长", result["message"])

    def test_captain_relationship_without_a_member_row_is_not_misreported_as_no_team(self):
        own = self.make_team(captain=self.actor)
        self.db.query(TeamMember).filter_by(user_id=self.actor.id).delete()
        self.db.flush()
        result = self.call()
        self.assertEqual(result["data"]["team_id"], own.id)
        self.assertTrue(result["data"]["is_captain"])

    def test_no_team_advice_is_personal_but_not_registration_approval(self):
        other = self.make_team()
        result = self.call()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"]["has_team"])
        self.assertEqual(result["data"]["recommended_registration_mode"], "personal")
        self.assertIsNone(result["data"]["team_id"])
        self.assertIn("核验", result["message"])
        self.assertNotIn(other.name, json.dumps(result))

    def test_pending_team_is_reported_without_inventing_eligibility(self):
        own = self.make_team(captain=self.actor, status=TeamStatus.PENDING)
        result = self.call()
        self.assertEqual(result["data"]["team_id"], own.id)
        self.assertEqual(result["data"]["team_status"], "pending")
        self.assertTrue(result["data"]["is_captain"])
        self.assertIn("待审核", result["message"])

    def test_hidden_team_never_turns_into_personal_registration_or_leaks_name(self):
        own = self.make_team(captain=self.actor, is_hidden=True)
        result = self.call()
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["data"]["recommended_registration_mode"])
        self.assertIsNone(result["data"]["team_id"])
        self.assertNotIn(own.name, json.dumps(result))

    def test_hidden_captain_makes_a_members_team_unavailable_without_name_leak(self):
        captain = self.make_user(is_hidden=True)
        own = self.make_team(captain=captain, members=[self.actor])
        result = self.call()
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn(own.name, json.dumps(result))

    def test_conflicting_captain_records_do_not_arbitrarily_pick_a_team(self):
        own = self.make_team(captain=self.actor)
        conflicting = Team(name="冲突归属", captain_id=self.actor.id, status=TeamStatus.APPROVED)
        self.db.add(conflicting)
        self.db.flush()
        result = self.call()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["data"]["code"], "team_relationship_conflict")
        self.assertIsNone(result["data"]["team_id"])
        self.assertNotIn(own.name, json.dumps(result))
        self.assertNotIn(conflicting.name, json.dumps(result))

    def test_zero_argument_tool_rejects_forged_identity(self):
        with self.assertRaises(HTTPException) as caught:
            self.call(user_id=999)
        self.assertEqual(caught.exception.status_code, 422)

    def test_team_advice_and_next_query_are_read_only(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        match = self.make_match()
        self.add_round(match, own, opponent)
        self.db.commit()
        writes = []

        def watch(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                writes.append(statement)

        event.listen(self.engine, "before_cursor_execute", watch)
        try:
            self.call()
            self.next(my_team=True)
        finally:
            event.remove(self.engine, "before_cursor_execute", watch)
        self.assertEqual(writes, [])

    def test_my_next_round_ignores_unrelated_public_events_without_needing_an_event_id(self):
        own, opponent, other = self.make_team(captain=self.actor), self.make_team(), self.make_team()
        target_event, unrelated = self.make_match(), self.make_match()
        target = self.add_round(target_event, own, opponent, scheduled_time=datetime(2026, 11, 7, 13))
        self.add_round(unrelated, opponent, other, scheduled_time=datetime(2026, 11, 7, 12))
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([row["id"] for row in result["data"]["items"]], [target.id])
        self.assertEqual(result["data"]["implicit_scope"], "my_team")
        self.assertIn(own.name, result["message"])
        self.assertIn(opponent.name, result["message"])
        self.assertIn(target_event.name, result["message"])

    def test_bare_next_without_an_explicit_subject_can_resolve_the_current_members_team(self):
        own, opponent, other = self.make_team(members=[self.actor]), self.make_team(), self.make_team()
        match = self.make_match()
        target = self.add_round(match, own, opponent)
        self.add_round(match, opponent, other, scheduled_time=datetime(2026, 11, 7, 12))
        result = self.next()
        self.assertEqual([row["id"] for row in result["data"]["items"]], [target.id])
        self.assertEqual(result["data"]["team_id"], own.id)

    def test_explicit_other_team_takes_precedence_over_implicit_own_team(self):
        own, opponent, other = self.make_team(captain=self.actor), self.make_team(), self.make_team()
        match = self.make_match()
        target = self.add_round(match, opponent, other, scheduled_time=datetime(2026, 11, 7, 13))
        self.add_round(match, own, other, scheduled_time=datetime(2026, 11, 7, 12))
        result = self.next(team_id=opponent.id)
        self.assertEqual(result["data"]["items"][0]["id"], target.id)
        self.assertEqual(result["data"]["implicit_scope"], "selected_team")

    def test_explicit_event_is_not_silently_replaced_with_the_callers_own_event(self):
        own, opponent, other = self.make_team(captain=self.actor), self.make_team(), self.make_team()
        own_event, chosen = self.make_match(), self.make_match()
        self.add_round(own_event, own, other)
        target = self.add_round(chosen, opponent, other)
        result = self.next(match_id=chosen.id)
        self.assertEqual(result["data"]["items"][0]["id"], target.id)
        self.assertIsNone(result["data"]["team_id"])
        self.assertEqual(result["data"]["match_id"], chosen.id)

    def test_explicit_event_and_my_team_do_not_cross_into_another_event(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        chosen, other = self.make_match(), self.make_match()
        target = self.add_round(chosen, own, opponent, scheduled_time=datetime(2026, 11, 7, 15))
        self.add_round(other, own, opponent, scheduled_time=datetime(2026, 11, 7, 13))
        result = self.next(match_id=chosen.id, my_team=True)
        self.assertEqual(result["data"]["items"][0]["id"], target.id)

    def test_unique_earliest_scheduled_round_can_resolve_across_related_events(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        early, late = self.make_match(), self.make_match()
        target = self.add_round(early, own, opponent, scheduled_time=datetime(2026, 11, 7, 13))
        self.add_round(late, own, opponent, scheduled_time=datetime(2026, 11, 7, 15))
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["items"][0]["id"], target.id)
        self.assertEqual(result["data"]["match_id"], early.id)
        self.assertEqual(result["data"]["resolution"], "earliest_scheduled")

    def test_same_time_candidates_are_not_resolved_by_database_id(self):
        own, left, right = self.make_team(captain=self.actor), self.make_team(), self.make_team()
        first, second = self.make_match(), self.make_match()
        a = self.add_round(first, own, left, scheduled_time=datetime(2026, 11, 7, 13))
        b = self.add_round(second, own, right, scheduled_time=datetime(2026, 11, 7, 13))
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "needs_selection")
        self.assertEqual({row["id"] for row in result["data"]["items"]}, {a.id, b.id})
        self.assertIsNone(result["data"]["match_id"])

    def test_unknown_schedule_does_not_get_arbitrarily_sorted_after_known_times(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        match = self.make_match()
        a = self.add_round(match, own, opponent, scheduled_time=datetime(2026, 11, 7, 13))
        b = self.add_round(match, own, opponent, round_number=2)
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "needs_selection")
        self.assertEqual({row["id"] for row in result["data"]["items"]}, {a.id, b.id})
        self.assertEqual(result["data"]["resolution"], "schedule_unknown")

    def test_only_remaining_unscheduled_round_can_be_shown_without_inventing_a_time(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        match = self.make_match()
        target = self.add_round(match, own, opponent, group_name="淘汰赛")
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "ok")
        row = result["data"]["items"][0]
        self.assertEqual(row["id"], target.id)
        self.assertEqual(row["format"], "BO3")
        self.assertIsNone(row["scheduled_time"])
        self.assertIsNone(row["score"])
        self.assertIn("尚未安排", result["message"])

    def test_an_ongoing_round_takes_precedence_over_other_pending_rounds(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        match = self.make_match()
        self.add_round(match, own, opponent, round_number=1)
        target = self.add_round(match, own, opponent, round_number=2, status=RoundStatus.IN_PROGRESS)
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["items"][0]["id"], target.id)
        self.assertIn("正在进行", result["message"])

    def test_multiple_ongoing_rounds_are_a_real_conflict_not_a_first_id_choice(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        match = self.make_match()
        self.add_round(match, own, opponent, status=RoundStatus.IN_PROGRESS)
        self.add_round(match, own, opponent, round_number=2, status=RoundStatus.IN_PROGRESS)
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "needs_selection")
        self.assertEqual(len(result["data"]["items"]), 2)

    def test_explicit_my_team_without_membership_never_falls_back_to_other_teams(self):
        match = self.make_match(status=MatchStatus.IN_PROGRESS)
        self.add_round(match, self.make_team(), self.make_team())
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["data"]["code"], "no_team")
        self.assertEqual(result["data"]["items"], [])

    def test_bare_next_for_an_unteamed_operator_uses_the_unique_ongoing_event(self):
        match = self.make_match(status=MatchStatus.IN_PROGRESS)
        self.make_match(status=MatchStatus.FINISHED)
        self.make_match(status=MatchStatus.REGISTERING)
        target = self.add_round(match, self.make_team(), self.make_team())
        result = self.next()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["items"][0]["id"], target.id)
        self.assertEqual(result["data"]["implicit_scope"], "ongoing_match")
        self.assertIn(match.name, result["message"])

    def test_unteamed_operator_only_gets_ongoing_events_as_real_candidates(self):
        active = [self.make_match(status=MatchStatus.IN_PROGRESS) for _ in range(2)]
        hidden = [self.make_match(status=value) for value in
                  (MatchStatus.FINISHED, MatchStatus.REGISTERING, MatchStatus.DRAFT)]
        result = self.next()
        self.assertEqual(result["status"], "needs_selection")
        self.assertEqual({item["id"] for item in result["cards"][0]["items"]}, {m.id for m in active})
        for match in hidden:
            self.assertNotIn(match.name, json.dumps(result))

    def test_my_implicit_next_excludes_finished_events_and_unpublished_drafts(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        for status in (MatchStatus.FINISHED, MatchStatus.DRAFT):
            self.add_round(self.make_match(status=status), own, opponent)
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["data"]["items"], [])
        self.assertIn("尚未生成", result["message"])

    def test_hidden_opponents_are_filtered_before_resolving_next_or_exposing_candidates(self):
        own, opponent, hidden = self.make_team(captain=self.actor), self.make_team(), self.make_team(is_hidden=True)
        match = self.make_match()
        target = self.add_round(match, own, opponent)
        self.add_round(match, own, hidden)
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["items"][0]["id"], target.id)
        self.assertNotIn(hidden.name, json.dumps(result))

    def test_finished_rounds_do_not_become_next_or_imply_an_elimination_result(self):
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        match = self.make_match()
        self.add_round(match, own, opponent, status=RoundStatus.FINISHED)
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["data"]["items"], [])
        self.assertNotIn("淘汰", result["message"])

    def test_conflicting_my_team_and_explicit_other_team_remain_a_business_error(self):
        self.make_team(captain=self.actor)
        other = self.make_team()
        with self.assertRaises(HTTPException) as caught:
            self.next(my_team=True, team_id=other.id)
        self.assertEqual(caught.exception.status_code, 422)

    def test_unknown_opponent_is_not_replaced_with_an_unrelated_real_team(self):
        own = self.make_team(captain=self.actor)
        match = self.make_match()
        self.add_round(match, own, None)
        result = self.next(my_team=True)
        self.assertEqual(result["data"]["items"][0]["team2_name"], "对手待定")
        self.assertIn("对手待定", result["message"])

    def test_membership_is_requeried_and_no_longer_teamed_user_gets_no_own_next(self):
        own, opponent = self.make_team(members=[self.actor]), self.make_team()
        match = self.make_match()
        self.add_round(match, own, opponent)
        self.assertEqual(self.next(my_team=True)["status"], "ok")
        self.db.query(TeamMember).filter_by(user_id=self.actor.id).delete()
        self.db.flush()
        result = self.next(my_team=True)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["data"]["items"], [])

    def test_real_http_adapter_executes_minimal_next_arguments_without_null_filling(self):
        """Exercise actual provider serialization and agent validation, not tool_call()."""
        own, opponent = self.make_team(captain=self.actor), self.make_team()
        match = self.make_match(status=MatchStatus.IN_PROGRESS)
        target = self.add_round(match, own, opponent, scheduled_time=datetime(2026, 11, 7, 13))
        self.db.commit()
        original_arguments = '{"my_team":true,"next_only":true}'
        wire_responses = [
            {"choices": [{"finish_reason": "tool_calls", "message": {
                "content": None, "tool_calls": [{"id": "minimal_next_1", "type": "function", "function": {
                    "name": "get_match_rounds", "arguments": original_arguments}}]}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "已查询本队下一场对阵。"}}]},
        ]
        payloads = []

        def open_mock(request, **kwargs):
            self.assertFalse(self.db.in_transaction(), "Model wait cannot hold a DB transaction")
            payloads.append(json.loads(request.data))
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps(wire_responses.pop(0), ensure_ascii=False).encode()
            return response

        values = {"ASSISTANT_ENABLED": True, "ASSISTANT_EXTERNAL_ENABLED": True,
                  "ASSISTANT_TOOL_MODEL_VERIFIED": True, "ASSISTANT_MODEL": "glm-5.3-flash",
                  "ASSISTANT_API_KEY": "synthetic-test-key",
                  "ASSISTANT_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
                  "ASSISTANT_SUMMARY_ENABLED": False, "ASSISTANT_MAX_STEPS": 3,
                  "ASSISTANT_MAX_TOOL_CALLS": 4, "ASSISTANT_TIMEOUT_SECONDS": 20}
        with ExitStack() as stack:
            if hasattr(settings, "ASSISTANT_ENGINE"):
                stack.enter_context(patch.object(settings, "ASSISTANT_ENGINE", "legacy"))
            for key, value in values.items():
                stack.enter_context(patch.object(settings, key, value))
            opener = Mock()
            opener.open.side_effect = open_mock
            stack.enter_context(patch("urllib.request.build_opener", return_value=opener))
            conversation = agent.create_conversation(self.db, self.actor)
            result = agent.send_message(self.db, self.actor, conversation["conversation_id"],
                                        "我的下一场对阵是谁？", "minimal_next_client")
        self.assertEqual(len(payloads), 2)
        next_schema = next(row["function"] for row in payloads[0]["tools"]
                           if row["function"]["name"] == "get_match_rounds")
        self.assertNotIn("strict", next_schema)
        self.assertEqual(next_schema["parameters"]["required"], [])
        received = next(message for message in payloads[1]["messages"] if message["role"] == "tool")
        tool_result = json.loads(received["content"])
        self.assertEqual(tool_result["status"], "ok")
        self.assertEqual(tool_result["data"]["items"][0]["id"], target.id)
        model_call = next(message for message in payloads[1]["messages"]
                          if message["role"] == "assistant" and message.get("tool_calls"))
        # The runtime may reserialize JSON whitespace; it must not add filters
        # or turn omissions into a fabricated selected event/team.
        self.assertEqual(json.loads(model_call["tool_calls"][0]["function"]["arguments"]),
                         json.loads(original_arguments))
        self.assertEqual(result["code"], "completed")
        self.assertIn(opponent.name, json.dumps(result, ensure_ascii=False))

    def test_optional_read_filters_do_not_weaken_registration_required_fields(self):
        definitions = {row["function"]["name"]: row["function"]["parameters"]
                       for row in agent.definitions()}
        for name in ("check_registration", "prepare_registration"):
            self.assertEqual(set(definitions[name]["required"]), {"match_id", "mode"})
            with self.assertRaises(ValueError):
                agent.validate_arguments({"match_id": 1}, definitions[name])
        agent.validate_arguments({"my_team": True, "next_only": True}, definitions["get_match_rounds"])
        for forged in ({"my_team": True, "next_only": True, "actor_id": 99},
                       {"my_team": "true", "next_only": True}, {"limit": 100}):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                agent.validate_arguments(forged, definitions["get_match_rounds"])

    def test_matching_event_selection_text_and_cards_use_the_same_ordinal_order(self):
        first_created = self.make_match(name="测试赛事甲")
        last_created = self.make_match(name="测试赛事乙")
        result = self.call("list_matches", keyword="测试赛事")
        self.assertEqual(result["status"], "needs_selection")
        items = result["cards"][0]["items"]
        self.assertEqual([item["id"] for item in items], [last_created.id, first_created.id])
        self.assertEqual(result["message"].splitlines()[1:3], [
            "1. 测试赛事乙（ID %s）" % last_created.id,
            "2. 测试赛事甲（ID %s）" % first_created.id])
        self.assertIn("第二个", result["message"])

    def test_resolving_an_ambiguous_event_uses_the_same_canonical_list_as_name_search(self):
        self.make_match(name="测试赛事甲")
        self.make_match(name="测试赛事乙")
        listed = self.call("list_matches", keyword="测试赛事")
        resolved = self.call("get_match_rounds", match_keyword="测试赛事")
        self.assertEqual(resolved["status"], "needs_selection")
        self.assertEqual(resolved["message"], listed["message"])
        self.assertEqual(resolved["cards"][0]["items"], listed["cards"][0]["items"])

    def test_listing_all_events_does_not_force_a_selection_without_a_name_query(self):
        self.make_match(name="测试赛事甲")
        self.make_match(name="测试赛事乙")
        for keyword in (None, "", "  "):
            with self.subTest(keyword=keyword):
                result = self.call("list_matches", keyword=keyword)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(result["data"]["items"]), 2)

    def test_partial_event_selection_only_numbers_shown_cards_and_reports_the_limit(self):
        self.make_match(name="测试赛事甲")
        last_created = self.make_match(name="测试赛事乙")
        result = self.call("list_matches", keyword="测试赛事", limit=1)
        self.assertEqual(result["status"], "needs_selection")
        self.assertTrue(result["data"]["has_more"])
        self.assertEqual(result["message"].splitlines()[1], "1. 测试赛事乙（ID %s）" % last_created.id)
        self.assertNotIn("测试赛事甲", result["message"])
        self.assertIn("仅展示部分", result["message"])
        exact = self.call("list_matches", keyword="测试赛事乙")
        self.assertEqual(exact["status"], "ok")
        self.assertEqual([row["id"] for row in exact["data"]["items"]], [last_created.id])


if __name__ == "__main__":
    import unittest
    unittest.main()
