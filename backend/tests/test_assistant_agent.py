"""Real bounded agent/tool orchestration against isolated DB and scripted models.

No main import, .env read, production connection, external model or user message.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch, Mock

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.support import DatabaseTestCase
from app.config import settings
from app.database import get_db
from app.models.assistant_session import AssistantConversation, AssistantTurn, AssistantToolTrace
from app.models.match import Registration, RegistrationStatus
from app.models.user import UserRole
from app.routers.assistant import router
from app.services.auth_service import get_current_user
from app.services import assistant_service as service
from app.services.assistant_provider import ProviderError


def tool_call(name, arguments=None, identifier="call_1"):
    arguments = dict(arguments or {})
    definition = next((row["function"] for row in service.definitions() if row["function"]["name"] == name), None)
    if definition:
        for key in definition["parameters"].get("required", []):
            arguments.setdefault(key, None)
    return {"role": "assistant", "content": "", "tool_calls": [{"id": identifier, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def answer(text="已按当前记录查询。"):
    return {"role": "assistant", "content": text, "tool_calls": []}


class ScriptedProvider:
    def __init__(self, db, responses):
        self.db, self.responses, self.calls = db, list(responses), []

    def __call__(self, messages, tools, timeout):
        if self.db.in_transaction():
            raise AssertionError("LLM wait must not hold a DB transaction")
        self.calls.append({"messages": deepcopy(messages), "tools": deepcopy(tools), "timeout": timeout})
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value(messages, tools, timeout) if callable(value) else value


class AgentClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def asgi_request(app, path, *, method="GET", body=None, headers=()):
    sent = []
    payload = json.dumps(body).encode() if body is not None else b""
    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}
    async def send(message):
        sent.append(message)
    request_headers = [(b"content-type", b"application/json"), *headers]
    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": request_headers, "client": ("127.0.0.1", 1), "server": ("test", 80)}, receive, send))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    data = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, json.loads(data)


class AssistantAgentTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        for key, value in {"ASSISTANT_ENABLED": True, "ASSISTANT_MAX_STEPS": 3,
                           "ASSISTANT_MAX_TOOL_CALLS": 4, "ASSISTANT_TIMEOUT_SECONDS": 20}.items():
            mocked = patch.object(settings, key, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.actor = self.make_user(nickname="测试助手用户")
        self.db.commit()
        self.conversation_id = service.create_conversation(self.db, self.actor)["conversation_id"]

    def send(self, provider, message="查询赛事", client_id="client_0001", context=None, clock=None):
        kwargs = {"provider": provider}
        if clock is not None:
            kwargs["clock"] = clock
        return service.send_message(self.db, self.actor, self.conversation_id, message, client_id, context, **kwargs)

    def test_actual_tool_selection_execution_tool_reply_then_answer_has_no_network_wait_transaction(self):
        match = self.make_match(name="助手公开杯")
        self.db.commit()
        model = ScriptedProvider(self.db, [tool_call("list_matches"), answer("查询到了公开赛事。")])
        result = self.send(model)
        self.assertEqual(len(model.calls), 2)
        tools = [m for m in model.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(json.loads(tools[0]["content"])["data"]["items"][0]["id"], match.id)
        self.assertEqual(result["cards"][0]["items"][0]["title"], "助手公开杯")
        trace = self.db.query(AssistantToolTrace).one()
        self.assertEqual((trace.tool, trace.result_code), ("list_matches", "ok"))
        self.assertGreaterEqual(trace.duration_ms, 0)

    def test_interleaved_reasoning_is_exact_only_inside_current_tool_request(self):
        thought_1 = '  内存思考甲：先查赛事\n保留空格和 + 符号。  '
        thought_2 = '内存思考乙：\n再查支持入口。'
        first, second, final = tool_call('list_matches'), tool_call('get_public_admin_contacts', identifier='call_2'), answer()
        first['reasoning_content'], second['reasoning_content'] = thought_1, thought_2
        final['reasoning_content'] = '最终思考也不应持久化'
        model = ScriptedProvider(self.db, [first, second, final])
        result = self.send(model)
        self.assertEqual(result['code'], 'completed')
        self.assertEqual([m['reasoning_content'] for m in model.calls[1]['messages'] if 'reasoning_content' in m], [thought_1])
        self.assertEqual([m['reasoning_content'] for m in model.calls[2]['messages'] if 'reasoning_content' in m], [thought_1, thought_2])
        conversation = self.db.query(AssistantConversation).one()
        stored = json.dumps([result, service.conversation_history(self.db, self.actor, self.conversation_id),
            conversation.context_json, conversation.memory_json, self.db.query(AssistantTurn).one().response_json,
            [(t.tool, t.result_code) for t in self.db.query(AssistantToolTrace).all()]], ensure_ascii=False)
        for value in ('reasoning_content', '内存思考甲', '内存思考乙', '最终思考也不应持久化'):
            self.assertNotIn(value, stored)
        next_model = ScriptedProvider(self.db, [answer('下一个问题。')])
        self.send(next_model, client_id='next_client')
        self.assertFalse(any('reasoning_content' in m for m in next_model.calls[0]['messages']))

    def test_real_adapter_and_tool_loop_keep_thinking_on_mocked_wire_only(self):
        thought = '  先查真实赛事\n再给出简洁回复。  '
        first, final = tool_call('list_matches'), answer('已查询。')
        first['reasoning_content'] = thought
        wire_responses = [json.dumps({'choices': [{'finish_reason': finish, 'message': message}]},
                                    ensure_ascii=False).encode('utf-8')
                          for finish, message in [('tool_calls', first), ('stop', final)]]
        incoming = Mock()
        incoming.__enter__ = Mock(return_value=incoming)
        incoming.__exit__ = Mock(return_value=False)
        incoming.read.side_effect = wire_responses
        payloads = []
        def open_mock(request, **kwargs):
            self.assertFalse(self.db.in_transaction())
            payloads.append(json.loads(request.data))
            return incoming
        opener = Mock()
        opener.open.side_effect = open_mock
        with (patch.object(settings, 'ASSISTANT_EXTERNAL_ENABLED', True),
              patch.object(settings, 'ASSISTANT_TOOL_MODEL_VERIFIED', True),
              patch.object(settings, 'ASSISTANT_MODEL', 'glm-5.3-flash'),
              patch.object(settings, 'ASSISTANT_API_KEY', 'mock-assistant-key'),
              patch.object(settings, 'ASSISTANT_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4'),
              patch('urllib.request.build_opener', return_value=opener)):
            result = service.send_message(self.db, self.actor, self.conversation_id, '查询赛事', 'wire_client')
        self.assertEqual(result['code'], 'completed')
        self.assertEqual(len(payloads), 2)
        intermediate = [m for m in payloads[1]['messages'] if m.get('tool_calls')]
        self.assertEqual(intermediate[0]['reasoning_content'], thought)
        self.assertTrue(any(m['role'] == 'tool' and m['tool_call_id'] == 'call_1'
                            for m in payloads[1]['messages']))
        self.assertNotIn('reasoning_content', self.db.query(AssistantTurn).one().response_json)

    def test_invalid_reasoning_stops_before_any_tool_and_is_never_returned(self):
        for index, reasoning in enumerate((None, 2, {}, [], '中' * 8001)):
            with self.subTest(index=index):
                response = tool_call('list_matches')
                response['reasoning_content'] = reasoning
                model = ScriptedProvider(self.db, [response])
                with patch.object(service, 'execute_tool') as execute:
                    result = self.send(model, client_id=f'invalid_{index}')
                execute.assert_not_called()
                self.assertEqual(result['code'], 'provider_reasoning_limit' if index == 4 else 'provider_response_invalid')
                self.assertNotIn('reasoning_content', result)

    def test_cumulative_reasoning_budget_stops_before_third_tool(self):
        replies = [tool_call('list_matches', identifier=f'call_{index}') for index in range(3)]
        for response in replies:
            response['reasoning_content'] = '中' * 8000  # 每次恰好24000字节。
        model = ScriptedProvider(self.db, replies)
        with patch.object(service, 'execute_tool', return_value={'status': 'ok', 'data': {}, 'cards': []}) as execute:
            result = self.send(model)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(result['code'], 'provider_reasoning_limit')
        self.assertEqual(self.db.query(AssistantToolTrace).count(), 2)
        self.assertNotIn('中中中', self.db.query(AssistantTurn).one().response_json)

    def test_same_named_event_queries_return_selection_not_an_arbitrary_first_event(self):
        self.make_match(name="起源杯测试甲")
        self.make_match(name="起源杯测试乙")
        self.db.commit()
        model = ScriptedProvider(self.db, [tool_call("get_match_rounds", {"match_keyword": "起源杯测试"}), answer("请先选择赛事。")])
        result = self.send(model)
        payload = json.loads([m for m in model.calls[1]["messages"] if m["role"] == "tool"][0]["content"])
        self.assertEqual(payload["status"], "needs_selection")
        self.assertEqual(len(result["cards"][0]["items"]), 2)

    def test_multiturn_remembers_selection_but_changing_event_clears_stale_team_context(self):
        first = ScriptedProvider(self.db, [answer("已选赛事。")])
        result = self.send(first, context={"match_id": 7, "team_id": 3})
        self.assertEqual(result["context"], {"match_id": 7, "team_id": 3})
        second = ScriptedProvider(self.db, [answer("继续查询。")])
        remembered = self.send(second, message="它下一场呢", client_id="client_0002")
        self.assertEqual(remembered["context"], {"match_id": 7, "team_id": 3})
        messages = second.calls[0]["messages"]
        self.assertTrue(any(m["content"] == "已选赛事。" for m in messages))
        third = ScriptedProvider(self.db, [answer("切换赛事。")])
        changed = self.send(third, client_id="client_0003", context={"match_id": 8})
        self.assertEqual(changed["context"], {"match_id": 8})
        self.assertEqual(service.conversation_history(self.db, self.actor, self.conversation_id)["context"], {"match_id": 8})

    def test_explicit_empty_context_clears_scope_and_later_omission_cannot_revive_it(self):
        first = self.send(ScriptedProvider(self.db, [answer("已记录选择")]), context={"match_id": 7, "team_id": 3, "player_id": 8})
        self.assertEqual(first["context"], {"match_id": 7, "team_id": 3, "player_id": 8})
        clear = self.send(ScriptedProvider(self.db, [answer("已清除选择")]), client_id="client_0002", context={})
        self.assertEqual(clear["context"], {})
        later_model = ScriptedProvider(self.db, [answer("当前未选择赛事")])
        later = self.send(later_model, client_id="client_0003", context=None)
        self.assertEqual(later["context"], {})
        self.assertIn("{}", later_model.calls[0]["messages"][-1]["content"])
        self.assertEqual(service.conversation_history(self.db, self.actor, self.conversation_id)["context"], {})

    def test_stats_selection_memory_keeps_only_ordered_identity_and_requeries_second_player(self):
        keys = ["a" * 32, "abcdef123456789012abcdefabcdefab"]
        selection = {"kind": "stats_player", "match_id": 7, "requires_selection": True,
            "candidates": [{"player_key": key, "name": "同名选手"} for key in keys]}
        result = {"status": "needs_selection", "message": "请先选择统计身份", "data": {"selection": selection,
            "items": [{"player_key": key, "kills": 9876, "adr": 143.21} for key in keys]},
            "cards": [{"type": "player_stats", "title": "选手选择", "items": [
                {"id": key, "title": "同名选手", "action": {"kind": "select_stats_player", "player_key": key, "match_id": 7}}
                for key in keys]}]}
        first_model = ScriptedProvider(self.db, [tool_call("get_player_stats", {"match_id": 7, "keyword": "同名选手"}), answer("有两个同名选手，请选择。")])
        with patch.object(service, "execute_tool", return_value=result):
            self.send(first_model, context={"match_id": 7})
        memory = json.loads(self.db.get(AssistantConversation, self.conversation_id).memory_json)
        self.assertEqual(memory, {"selection": selection})
        for metric in ("kills", "adr", "9876", "143.21"):
            self.assertNotIn(metric, json.dumps(memory))
        second_model = ScriptedProvider(self.db, [tool_call("get_player_stats", {"match_id": 7, "player_key": keys[1]}, "second"), answer("已重新查询第二名选手。")])
        with patch.object(service, "execute_tool", return_value={"status": "ok", "data": {}, "cards": []}) as execute:
            self.send(second_model, message="第二个", client_id="client_0002")
        prompt = second_model.calls[0]["messages"][-1]["content"]
        self.assertLess(prompt.index(keys[0]), prompt.index(keys[1]))
        self.assertNotIn("9876", prompt)
        self.assertNotIn("143.21", prompt)
        self.assertEqual(execute.call_args.args[3]["player_key"], keys[1])

    def test_changing_event_or_explicit_clear_discards_controlled_stats_candidates(self):
        key = "abcdef123456789012abcdefabcdefab"
        for context in ({"match_id": 8}, {}):
            with self.subTest(context=context):
                conversation = self.db.get(AssistantConversation, self.conversation_id)
                conversation.context_json = json.dumps({"match_id": 7, "stats_player_key": key})
                conversation.memory_json = json.dumps({"selection": {"kind": "stats_player", "match_id": 7,
                    "candidates": [{"player_key": key, "name": "上一场候选"}]}})
                self.db.commit()
                model = ScriptedProvider(self.db, [answer("选择已更新。")])
                self.send(model, client_id="scope_" + ("change" if context else "clear"), context=context)
                prompt = model.calls[0]["messages"][-1]["content"]
                self.assertNotIn(key, prompt)
                self.assertNotIn("上一场候选", prompt)
                self.assertEqual(json.loads(self.db.get(AssistantConversation, self.conversation_id).memory_json), {})

    def test_completed_client_id_is_replayed_without_rerunning_the_provider(self):
        model = ScriptedProvider(self.db, [answer("仅执行一次")])
        first = self.send(model)
        second = self.send(model)
        self.assertEqual(first, second)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.db.query(AssistantTurn).count(), 1)
        with self.assertRaises(HTTPException) as caught:
            self.send(model, message="不同问题")
        self.assertEqual(caught.exception.status_code, 409)

    def test_same_client_id_with_changed_redacted_raw_input_is_still_a_conflict(self):
        model = ScriptedProvider(self.db, [answer()])
        self.send(model, message="我的学号251101111")
        with self.assertRaises(HTTPException) as caught:
            self.send(model, message="我的学号251101112")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(len(model.calls), 1)

    def test_processing_request_is_not_blindly_reexecuted_after_a_lost_response(self):
        digest = hashlib.sha256(json.dumps(["查询赛事", None], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.db.add(AssistantTurn(id="processing-turn", conversation_id=self.conversation_id,
            client_message_id="client_0001", input_digest=digest, user_text="查询赛事", state="processing"))
        self.db.commit()
        model = ScriptedProvider(self.db, [answer()])
        with self.assertRaises(HTTPException) as caught:
            self.send(model)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "assistant_turn_processing")
        self.assertEqual(model.calls, [])

    def test_an_active_conversation_lease_rejects_a_different_parallel_turn(self):
        conversation = self.db.get(AssistantConversation, self.conversation_id)
        conversation.lease_token = "another-request"
        conversation.lease_until = datetime.utcnow() + timedelta(minutes=1)
        self.db.commit()
        model = ScriptedProvider(self.db, [answer()])
        with self.assertRaises(HTTPException) as caught:
            self.send(model)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(model.calls, [])
        self.assertEqual(self.db.query(AssistantTurn).count(), 0)

    def test_cross_account_history_and_send_are_inaccessible(self):
        other = self.make_user()
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            service.conversation_history(self.db, other, self.conversation_id)
        self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            service.send_message(self.db, other, self.conversation_id, "越权", "client_0001", provider=lambda *a: answer())

    def test_expired_history_and_hidden_actors_are_rejected(self):
        row = self.db.get(AssistantConversation, self.conversation_id)
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            service.conversation_history(self.db, self.actor, self.conversation_id)
        self.assertEqual(caught.exception.status_code, 410)
        self.actor.is_hidden = True
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            service.create_conversation(self.db, self.actor)
        self.assertEqual(caught.exception.status_code, 403)

    def test_permissions_are_rechecked_after_model_wait_and_before_tool_execution(self):
        def revoke(*_):
            self.db.get(type(self.actor), self.actor.id).is_hidden = True
            self.db.commit()
            return tool_call("list_matches")
        model = ScriptedProvider(self.db, [revoke, answer("账号暂不可用")])
        with patch.object(service, "execute_tool") as execute:
            self.send(model)
        execute.assert_not_called()
        trace = self.db.query(AssistantToolTrace).one()
        self.assertEqual(trace.result_code, "permission_or_business_error")

    def test_unknown_tool_and_invalid_schema_are_tool_errors_not_executed_actions(self):
        unknown = tool_call("confirm_registration", {"action_id": "fake"}, "unknown1")
        invalid = tool_call("list_matches", {"limit": True}, "invalid1")
        invalid["tool_calls"][0]["function"]["arguments"] = json.dumps({"limit": True, "keyword": None})
        model = ScriptedProvider(self.db, [unknown, invalid, answer("请更正参数")])
        with patch.object(service, "execute_tool") as execute:
            result = self.send(model)
        execute.assert_not_called()
        self.assertEqual(result["cards"], [])
        self.assertEqual([r.result_code for r in self.db.query(AssistantToolTrace).all()],
                         ["invalid_tool_arguments", "invalid_tool_arguments"])

    def test_schema_pattern_is_enforced_before_tool_execution(self):
        with self.assertRaises(ValueError):
            service.validate_arguments("not-a-player-key", {"type": "string", "pattern": "^[0-9a-f]{32}$"})

    def test_total_step_and_tool_call_budgets_are_enforced(self):
        model = ScriptedProvider(self.db, [tool_call("list_matches", identifier="c1"),
            tool_call("list_matches", identifier="c2"), tool_call("list_matches", identifier="c3")])
        result = self.send(model)
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(result["code"], "step_limit")
        with patch.object(settings, "ASSISTANT_MAX_TOOL_CALLS", 1):
            more = ScriptedProvider(self.db, [tool_call("list_matches", identifier="x1"), tool_call("list_matches", identifier="x2")])
            result = self.send(more, client_id="client_0002")
        self.assertEqual(result["code"], "tool_budget")

    def test_duplicate_tool_call_ids_are_not_executed_twice(self):
        self.make_match()
        self.db.commit()
        model = ScriptedProvider(self.db, [tool_call("list_matches", identifier="same"), tool_call("list_matches", identifier="same")])
        result = self.send(model)
        self.assertEqual(result["code"], "provider_response_invalid")
        self.assertEqual(self.db.query(AssistantToolTrace).count(), 1)

    def test_model_timeout_is_saved_and_replayed_without_a_second_charge_attempt(self):
        model = ScriptedProvider(self.db, [ProviderError("provider_request_failed")])
        first = self.send(model)
        self.assertEqual(first["code"], "provider_request_failed")
        self.assertEqual(self.send(model), first)
        self.assertEqual(len(model.calls), 1)

    def test_a_provider_returning_after_total_deadline_cannot_claim_completed(self):
        clock = AgentClock()
        def late(*_):
            clock.now = settings.ASSISTANT_TIMEOUT_SECONDS + 1
            return answer("迟到结果")
        result = self.send(ScriptedProvider(self.db, [late]), clock=clock)
        self.assertNotEqual(result["code"], "completed")
        self.assertNotIn("迟到结果", result["reply"])

    def test_cleaned_user_history_tool_result_and_trace_exclude_sensitive_data(self):
        result = {"status": "ok", "message": "公开结果", "data": {"nickname": "公开昵称", "student_id": "251101111",
            "nested": {"token": "secret-token", "wx_openid": "openid-secret", "note": "号码251101112"}}, "cards": []}
        model = ScriptedProvider(self.db, [tool_call("list_matches"), answer("收到251101113")])
        with patch.object(service, "execute_tool", return_value=result):
            response = self.send(model, message="学号251101114 Bearer abcdefghijklmnop sk-secret1234567890")
        serialized = json.dumps([call["messages"] for call in model.calls], ensure_ascii=False)
        history = json.dumps(service.conversation_history(self.db, self.actor, self.conversation_id), ensure_ascii=False)
        for value in ("251101111", "251101112", "251101113", "251101114", "abcdefghijklmnop", "sk-secret1234567890", "secret-token", "openid-secret"):
            self.assertNotIn(value, serialized)
            self.assertNotIn(value, history)
        self.assertNotIn("student_id", serialized)
        trace = self.db.query(AssistantToolTrace).one()
        self.assertEqual(set(trace.__table__.columns.keys()), {"id", "turn_id", "tool", "result_code", "duration_ms", "created_at"})

    def test_semantic_short_student_number_and_email_are_not_forwarded_or_stored(self):
        model = ScriptedProvider(self.db, [answer("请通过原认证页面处理")])
        self.send(model, message="我的学号114514，邮箱private-person@example.test，帮我看报名")
        forwarded = json.dumps(model.calls[0]["messages"], ensure_ascii=False)
        stored = json.dumps(service.conversation_history(self.db, self.actor, self.conversation_id), ensure_ascii=False)
        for private in ("114514", "private-person@example.test"):
            self.assertNotIn(private, forwarded)
            self.assertNotIn(private, stored)

    def test_validated_opaque_stats_keys_survive_redaction_but_plain_numbers_do_not(self):
        opaque = "abcdef123456789012abcdefabcdefab"
        self.assertEqual(len(opaque), 32)
        public = service.public_payload({"player_key": opaque, "stats_player_key": opaque,
                                         "note": "学号251101111"})
        self.assertEqual(public["player_key"], opaque)
        self.assertEqual(public["stats_player_key"], opaque)
        self.assertNotIn("251101111", public["note"])

    def test_rejected_tool_arguments_are_not_retransmitted_as_raw_sensitive_model_context(self):
        invalid = tool_call("list_matches", {"token": "private-secret-example", "keyword": "251101111"})
        model = ScriptedProvider(self.db, [invalid, answer("请更正参数")])
        with patch.object(service, "execute_tool") as execute:
            self.send(model)
        execute.assert_not_called()
        forwarded = json.dumps(model.calls[1]["messages"], ensure_ascii=False)
        self.assertNotIn("private-secret-example", forwarded)
        self.assertNotIn("251101111", forwarded)

    def test_critical_business_summary_is_cleaned_before_storage_and_return(self):
        result = {"status": "blocked", "message": "成员251101111尚未完成认证", "data": {}, "cards": []}
        model = ScriptedProvider(self.db, [tool_call("check_registration", {"match_id": 1, "mode": "team", "team_id": 1}), answer("已确认")])
        with patch.object(service, "execute_tool", return_value=result):
            response = self.send(model)
        self.assertNotIn("251101111", response["reply"])
        self.assertNotIn("251101111", self.db.query(AssistantTurn).one().response_json)

    def test_registration_preview_never_implicitly_confirms_and_overrides_model_approved_claim(self):
        preview = {"status": "ok", "message": "报名预览已准备，尚未提交", "data": {"state": "pending_confirmation"},
            "cards": [{"type": "registration_preview", "title": "报名预览", "action_id": "preview_only", "items": []}]}
        model = ScriptedProvider(self.db, [tool_call("prepare_registration", {"match_id": 1, "mode": "personal", "team_id": None}),
                                           answer("我已替你报名并审核通过")])
        with (patch("app.services.assistant_registration_service.preview_registration", return_value=preview) as prepare,
              patch("app.services.assistant_registration_service.confirm_registration") as confirm):
            result = self.send(model, message="帮我报名")
        prepare.assert_called_once()
        confirm.assert_not_called()
        self.assertIn("尚未提交", result["reply"])
        self.assertIn("点击确认报名", result["reply"])
        self.assertNotIn("审核通过", result["reply"])
        self.assertEqual(self.db.query(Registration).count(), 0)
        self.assertNotIn("confirm_registration", [r["function"]["name"] for r in service.definitions()])

    def test_actual_pending_registration_remains_backend_fact_despite_model_approval_claim(self):
        match = self.make_match()
        self.db.add(Registration(match_id=match.id, user_id=self.actor.id, status=RegistrationStatus.PENDING))
        self.db.commit()
        model = ScriptedProvider(self.db, [tool_call("get_my_registration", {"match_id": match.id}), answer("已经成功获批")])
        result = self.send(model)
        self.assertIn("尚未审核通过", result["reply"])
        self.assertNotIn("已经成功获批", result["reply"])
        self.assertEqual(result["cards"][0]["items"][0]["badge"], "pending")

    def test_model_cannot_claim_it_performed_registration_without_a_tool_result(self):
        result = self.send(ScriptedProvider(self.db, [answer("我已替你提交报名并审核通过，报名成功。")]))
        self.assertNotIn("我已替你提交报名并审核通过", result["reply"])
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_model_error_preserves_already_obtained_cards_for_history_and_retry(self):
        self.make_match(name="已查询到的赛事")
        self.db.commit()
        model = ScriptedProvider(self.db, [tool_call("list_matches"), ProviderError("provider_request_failed")])
        result = self.send(model)
        self.assertEqual(result["code"], "provider_request_failed")
        self.assertEqual(result["cards"][0]["items"][0]["title"], "已查询到的赛事")
        self.assertEqual(self.send(model), result)
        history = service.conversation_history(self.db, self.actor, self.conversation_id)
        self.assertEqual(history["messages"][-1]["cards"], result["cards"])

    def test_disabled_feature_does_not_create_or_send_but_reports_unavailable(self):
        with patch.object(settings, "ASSISTANT_ENABLED", False):
            self.assertFalse(service.status()["enabled"])
            self.assertFalse(service.status()["available"])
            with self.assertRaises(HTTPException) as caught:
                service.create_conversation(self.db, self.actor)
            self.assertEqual(caught.exception.status_code, 503)
            with self.assertRaises(HTTPException):
                self.send(ScriptedProvider(self.db, [answer()]))


class AssistantRouteTests(DatabaseTestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with patch("tests.support.create_engine", return_value=engine):
            super().setUp()
        self.actor = self.make_user()
        self.db.commit()
        self.actor_id = self.actor.id
        self.app = FastAPI()
        self.app.include_router(router)
        def isolated_db():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db] = isolated_db

    def login(self, role=UserRole.USER):
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=self.actor_id, role=role)

    def test_status_is_public_and_writing_endpoints_require_jwt(self):
        with patch.object(settings, "ASSISTANT_ENABLED", False):
            code, data = asgi_request(self.app, "/api/assistant/status")
        self.assertEqual(code, 200)
        self.assertFalse(data["enabled"])
        self.assertEqual(asgi_request(self.app, "/api/assistant/conversations", method="POST")[0], 401)

    def test_request_model_rejects_impersonation_and_context_role(self):
        self.login()
        body = {"message": "查询", "client_message_id": "client_0001", "user_id": 999}
        self.assertEqual(asgi_request(self.app, "/api/assistant/conversations/x/messages", method="POST", body=body)[0], 422)
        body.pop("user_id")
        body["context"] = {"role": "admin"}
        self.assertEqual(asgi_request(self.app, "/api/assistant/conversations/x/messages", method="POST", body=body)[0], 422)

    def test_only_real_admin_role_can_maintain_knowledge_not_reviewer_or_user(self):
        for role in (UserRole.USER, UserRole.REVIEWER):
            self.login(role)
            self.assertEqual(asgi_request(self.app, "/api/assistant/knowledge")[0], 403)
        self.login(UserRole.ADMIN)
        with patch("app.routers.assistant.knowledge.current_knowledge", return_value={"version": "mock-v1"}):
            code, data = asgi_request(self.app, "/api/assistant/knowledge")
        self.assertEqual((code, data["version"]), (200, "mock-v1"))


if __name__ == "__main__":
    import unittest
    unittest.main()
