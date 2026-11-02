"""Real create_agent contracts against isolated SQLite and scripted providers.

The graph runs; no model/API, project .env, production database or deployment is
used. These tests exercise the framework boundary, with business rules remaining
covered by the existing assistant service suites.
"""
from copy import deepcopy
from contextlib import ExitStack
import json
import os
import threading
from unittest.mock import patch

from tests.support import DatabaseTestCase
from tests.test_assistant_agent import AgentClock, ScriptedProvider, answer, tool_call
from app.config import settings
from app.models.assistant_action import AssistantAction
from app.models.assistant_session import AssistantConversation, AssistantToolTrace, AssistantTurn
from app.models.match import Registration
from app.services import assistant_langchain_runtime as runtime
from app.services import assistant_service as service
from app.services.assistant_provider import ProviderError


def tool_batch(*calls):
    """Keep original call order and raw JSON arguments, including invalid cases."""
    return {"role": "assistant", "content": "", "tool_calls": [
        deepcopy(call["tool_calls"][0]) for call in calls
    ]}


class AssistantLangChainContracts(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        for key, value in {
            "ASSISTANT_ENABLED": True,
            "ASSISTANT_ENGINE": "langchain",
            "ASSISTANT_MAX_STEPS": 6,
            "ASSISTANT_MAX_TOOL_CALLS": 8,
            "ASSISTANT_TIMEOUT_SECONDS": 20,
            "ASSISTANT_SUMMARY_ENABLED": False,
        }.items():
            mocked = patch.object(settings, key, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.actor = self.make_user(nickname="框架契约用户")
        self.match = self.make_match(name="框架契约公开杯")
        self.db.commit()
        self.conversation_id = service.create_conversation(self.db, self.actor)["conversation_id"]

    def send(self, provider, *, message="查询赛事", client_id="framework_contract", clock=None):
        options = {"provider": provider}
        if clock is not None:
            options["clock"] = clock
        return service.send_message(self.db, self.actor, self.conversation_id,
                                    message, client_id, **options)

    def traces(self):
        return self.db.query(AssistantToolTrace).order_by(AssistantToolTrace.created_at).all()

    def test_actual_create_agent_constructs_graph_and_returns_tool_message_to_provider(self):
        model = ScriptedProvider(self.db, [tool_call("list_matches"), answer("查到公开赛事。")])
        with patch.object(runtime, "create_agent", wraps=runtime.create_agent) as create:
            result = self.send(model)
        create.assert_called_once()
        self.assertEqual(result["code"], "completed")
        self.assertEqual(len(model.calls), 2)
        tools = [m for m in model.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tools], ["call_1"])
        self.assertEqual(json.loads(tools[0]["content"])["data"]["items"][0]["id"], self.match.id)
        self.assertEqual(result["cards"][0]["items"][0]["title"], self.match.name)
        self.assertEqual([(t.tool, t.result_code) for t in self.traces()], [("list_matches", "ok")])
        definitions = model.calls[0]["tools"]
        self.assertNotIn("confirm_registration", [d["function"]["name"] for d in definitions])
        for definition in definitions:
            schema = definition["function"]["parameters"]
            self.assertFalse(schema["additionalProperties"])
            self.assertTrue({"role", "db", "session"}.isdisjoint(schema["properties"]))
            # recommend_players accepts an existing public-player filter;
            # private tools must always derive their actor from the JWT.
            if definition["function"]["name"] != "recommend_players":
                self.assertNotIn("user_id", schema["properties"])

    def test_environment_tracing_flags_cannot_export_messages_or_reasoning(self):
        from langchain_core.tracers.langchain import wait_for_all_tracers
        from langsmith import Client
        from langsmith.async_client import AsyncClient
        from langsmith.run_helpers import get_tracing_context
        import httpx
        import requests

        first = tool_call("list_matches")
        first["reasoning_content"] = "仅允许同轮保留的合成思考"
        observed_tracing = []

        class TracingProvider(ScriptedProvider):
            def __call__(inner, messages, tools, timeout):
                enabled = get_tracing_context().get("enabled")
                observed_tracing.append(enabled)
                self.assertIs(enabled, False, "real graph provider callback must explicitly disable tracing")
                return super().__call__(messages, tools, timeout)

        model = TracingProvider(self.db, [first, answer()])
        synthetic_environment = {
            "LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true",
            "LANGSMITH_API_KEY": "lsv2_synthetic_contract_key_no_network",
            "LANGCHAIN_API_KEY": "lsv2_synthetic_contract_key_no_network",
            "LANGSMITH_ENDPOINT": "https://trace-contract.invalid",
            "LANGCHAIN_ENDPOINT": "https://trace-contract.invalid",
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, synthetic_environment))
            tracing = stack.enter_context(patch.object(runtime, "tracing_context", wraps=runtime.tracing_context))
            # Block HTTP transports rather than socket.connect: Windows'
            # asyncio event loop needs an internal loopback socket pair.
            sync_network = stack.enter_context(patch.object(requests.Session, "send",
                side_effect=AssertionError("contract test must never issue an HTTP request")))
            async_network = stack.enter_context(patch.object(httpx.AsyncClient, "send",
                side_effect=AssertionError("contract test must never issue an HTTP request")))
            exports = [stack.enter_context(patch.object(Client, method)) for method in (
                "create_run", "update_run", "batch_ingest_runs", "multipart_ingest", "request_with_retries"
            )]
            exports.extend(stack.enter_context(patch.object(AsyncClient, method)) for method in (
                "create_run", "update_run"
            ))
            result = self.send(model)
            wait_for_all_tracers()
            self.assertEqual(result["code"], "completed")
            self.assertEqual(len(model.calls), 2)
            self.assertEqual(observed_tracing, [False, False])
            tracing.assert_called_once_with(enabled=False)
            for export in exports:
                export.assert_not_called()
            sync_network.assert_not_called()
            async_network.assert_not_called()

    def test_explicit_legacy_skips_graph_and_framework_error_never_falls_back(self):
        from app.services import assistant_execution

        legacy_model = ScriptedProvider(self.db, [tool_call("list_matches"), answer()])
        with patch.object(settings, "ASSISTANT_ENGINE", "legacy"):
            with patch.object(runtime, "create_agent", wraps=runtime.create_agent) as create:
                result = self.send(legacy_model)
        create.assert_not_called()
        self.assertEqual(result["code"], "completed")
        self.assertEqual(len(legacy_model.calls), 2)

        unused_model = ScriptedProvider(self.db, [answer("自动回退不应发出此请求")])
        with patch.object(runtime, "create_agent", side_effect=ProviderError("assistant_runtime_unavailable")) as create:
            with patch.object(assistant_execution, "run_legacy", wraps=assistant_execution.run_legacy) as legacy:
                result = self.send(unused_model, client_id="framework_unavailable")
        create.assert_called_once()
        legacy.assert_not_called()
        self.assertEqual(result["code"], "assistant_runtime_unavailable")
        self.assertEqual(unused_model.calls, [])

    def test_three_tools_stay_ordered_on_request_thread_and_model_waits_have_no_transaction(self):
        request_thread = threading.get_ident()
        events = []
        batch = tool_batch(tool_call("list_matches", identifier="first"),
                           tool_call("get_public_admin_contacts", identifier="second"),
                           tool_call("get_my_verification", identifier="third"))

        class ThreadProvider(ScriptedProvider):
            def __call__(inner, messages, tools, timeout):
                events.append(("model", threading.get_ident()))
                return super().__call__(messages, tools, timeout)

        original = service.execute_tool

        def execute(db, actor, name, arguments):
            events.append((name, threading.get_ident()))
            return original(db, actor, name, arguments)

        model = ThreadProvider(self.db, [batch, answer()])
        with patch.object(service, "execute_tool", side_effect=execute):
            result = self.send(model)
        self.assertEqual(result["code"], "completed")
        self.assertEqual([name for name, _ in events], [
            "model", "list_matches", "get_public_admin_contacts", "get_my_verification", "model"
        ])
        self.assertEqual({thread for _, thread in events}, {request_thread})
        messages = model.calls[1]["messages"]
        assistant = next(m for m in messages if m.get("tool_calls"))
        tool_messages = [m for m in messages if m["role"] == "tool"]
        self.assertEqual([call["id"] for call in assistant["tool_calls"]], ["first", "second", "third"])
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["first", "second", "third"])
        for message in tool_messages:
            self.assertIsInstance(json.loads(message["content"]), dict)
        self.assertEqual([trace.tool for trace in self.traces()], [
            "list_matches", "get_public_admin_contacts", "get_my_verification"
        ])

    def test_permission_is_rechecked_between_tools_in_one_graph_batch(self):
        executed = []
        original = service.execute_tool

        def execute(db, actor, name, arguments):
            executed.append(name)
            result = original(db, actor, name, arguments)
            self.db.get(type(self.actor), self.actor.id).is_hidden = True
            self.db.commit()
            return result

        model = ScriptedProvider(self.db, [tool_batch(
            tool_call("list_matches", identifier="before_revoke"),
            tool_call("get_public_admin_contacts", identifier="after_revoke")), answer()])
        with patch.object(service, "execute_tool", side_effect=execute):
            self.send(model)
        self.assertEqual(executed, ["list_matches"])
        self.assertEqual([t.result_code for t in self.traces()], ["ok", "permission_or_business_error"])
        tools = [m for m in model.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tools), 2)
        self.assertEqual(json.loads(tools[1]["content"])["status"], "blocked")

    def test_unknown_confirmation_forged_identity_boolean_and_bad_json_are_not_executed(self):
        unknown = tool_call("confirm_registration", {"action_id": "private-confirm-token"}, "unknown")
        forged = tool_call("list_matches", {"user_id": 999, "token": "private-forged-token"}, "forged")
        boolean = tool_call("list_matches", {"limit": True}, "boolean")
        malformed = tool_call("list_matches", identifier="bad_json")
        malformed["tool_calls"][0]["function"]["arguments"] = '{"token":"private-json-token",'
        model = ScriptedProvider(self.db, [tool_batch(unknown, forged, boolean), malformed, answer()])
        with patch.object(service, "execute_tool") as execute:
            result = self.send(model)
        execute.assert_not_called()
        self.assertEqual(result["cards"], [])
        self.assertEqual([trace.result_code for trace in self.traces()], ["invalid_tool_arguments"] * 4)
        forwarded = json.dumps([call["messages"] for call in model.calls[1:]], ensure_ascii=False)
        for private in ("private-confirm-token", "private-forged-token", "private-json-token"):
            self.assertNotIn(private, forwarded)
        tools = [m for m in model.calls[-1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tools], ["unknown", "forged", "boolean", "bad_json"])
        for message in tools:
            self.assertEqual(json.loads(message["content"])["status"], "error")

    def test_duplicate_call_id_is_rejected_before_a_second_execution(self):
        model = ScriptedProvider(self.db, [tool_call("list_matches", identifier="same"),
                                           tool_call("list_matches", identifier="same")])
        with patch.object(service, "execute_tool", return_value={"status": "ok", "cards": []}) as execute:
            result = self.send(model)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["code"], "provider_response_invalid")
        self.assertEqual(len(self.traces()), 1)

    def test_four_call_response_is_rejected_before_any_tool_runs(self):
        model = ScriptedProvider(self.db, [tool_batch(*[
            tool_call("list_matches", identifier=f"call_{i}") for i in range(4)
        ])])
        with patch.object(service, "execute_tool") as execute:
            result = self.send(model)
        execute.assert_not_called()
        self.assertEqual(result["code"], "provider_response_invalid")

    def test_reasoning_is_exact_in_current_protocol_and_absent_from_all_durable_outputs(self):
        thought = "  同轮供应商思考：先查赛事\n保留空格、+与中文。  "
        first = tool_call("list_matches")
        first["reasoning_content"] = thought
        final = answer("已查询。")
        final["reasoning_content"] = "最终供应商思考也不能存储"
        model = ScriptedProvider(self.db, [first, final])
        result = self.send(model)
        assistant = next(m for m in model.calls[1]["messages"] if m.get("tool_calls"))
        self.assertEqual(assistant["reasoning_content"], thought)
        conversation = self.db.query(AssistantConversation).one()
        stored = json.dumps([result, service.conversation_history(self.db, self.actor, self.conversation_id),
                             conversation.context_json, conversation.memory_json,
                             self.db.query(AssistantTurn).one().response_json,
                             [(t.tool, t.result_code) for t in self.traces()]], ensure_ascii=False)
        for private in ("reasoning_content", "同轮供应商思考", "最终供应商思考"):
            self.assertNotIn(private, stored)
        following = ScriptedProvider(self.db, [answer()])
        self.send(following, client_id="following_turn")
        self.assertNotIn("reasoning_content", json.dumps(following.calls[0]["messages"], ensure_ascii=False))
        self.assertEqual(set(AssistantToolTrace.__table__.columns.keys()),
                         {"id", "turn_id", "tool", "result_code", "duration_ms", "created_at"})

    def test_invalid_and_cumulative_reasoning_budgets_stop_before_extra_tools(self):
        bad = tool_call("list_matches")
        bad["reasoning_content"] = {"not": "a string"}
        with patch.object(service, "execute_tool") as execute:
            result = self.send(ScriptedProvider(self.db, [bad]))
        execute.assert_not_called()
        self.assertEqual(result["code"], "provider_response_invalid")
        replies = [tool_call("list_matches", identifier=f"reason_{index}") for index in range(3)]
        for response in replies:
            response["reasoning_content"] = "中" * 8000
        with patch.object(service, "execute_tool", return_value={"status": "ok", "cards": []}) as execute:
            result = self.send(ScriptedProvider(self.db, replies), client_id="reasoning_budget")
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(result["code"], "provider_reasoning_limit")
        self.assertNotIn("中中中", json.dumps(result, ensure_ascii=False))

    def test_model_step_and_tool_call_limits_are_business_budgets_not_graph_recursion(self):
        with patch.object(settings, "ASSISTANT_MAX_STEPS", 2):
            model = ScriptedProvider(self.db, [tool_call("list_matches", identifier=f"step_{i}") for i in range(2)])
            result = self.send(model)
        self.assertEqual(result["code"], "step_limit")
        self.assertEqual(len(model.calls), 2)
        with patch.object(settings, "ASSISTANT_MAX_TOOL_CALLS", 1):
            model = ScriptedProvider(self.db, [tool_batch(
                tool_call("list_matches", identifier="budget_one"),
                tool_call("list_matches", identifier="budget_two"))])
            with patch.object(service, "execute_tool", return_value={"status": "ok", "cards": []}) as execute:
                result = self.send(model, client_id="tool_budget")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["code"], "tool_budget")

    def test_deadline_during_argument_sanitization_is_tool_budget_before_any_execution(self):
        original = service.public_payload
        for engine in ("langchain", "legacy"):
            with self.subTest(engine=engine):
                clock = AgentClock()

                def sanitize_after_deadline(value):
                    # The provider returned within its allowance, but preparing
                    # its tool arguments used the remaining turn budget.
                    clock.now = settings.ASSISTANT_TIMEOUT_SECONDS + 1
                    return original(value)

                model = ScriptedProvider(self.db, [tool_call("list_matches")])
                with patch.object(settings, "ASSISTANT_ENGINE", engine):
                    with patch.object(service, "public_payload", side_effect=sanitize_after_deadline):
                        with patch.object(service, "execute_tool") as execute:
                            result = self.send(model, clock=clock, client_id=f"before_tool_{engine}")
                self.assertEqual(result["code"], "tool_budget")
                self.assertEqual(len(model.calls), 1)
                execute.assert_not_called()
                self.assertEqual(result["cards"], [])

    def test_last_allowed_tool_finishing_after_deadline_preserves_legacy_step_limit(self):
        for engine in ("langchain", "legacy"):
            with self.subTest(engine=engine):
                clock = AgentClock()

                def tool_finishes_late(*_):
                    # Synchronous local business work cannot be interrupted;
                    # exhausted model steps must not start another paid call.
                    clock.now = settings.ASSISTANT_TIMEOUT_SECONDS + 1
                    return {"status": "ok", "data": {}, "cards": []}

                model = ScriptedProvider(self.db, [tool_call("list_matches")])
                with patch.object(settings, "ASSISTANT_ENGINE", engine):
                    with patch.object(settings, "ASSISTANT_MAX_STEPS", 1):
                        with patch.object(service, "execute_tool", side_effect=tool_finishes_late) as execute:
                            result = self.send(model, clock=clock, client_id=f"last_tool_{engine}")
                self.assertEqual(result["code"], "step_limit")
                self.assertEqual(len(model.calls), 1)
                execute.assert_called_once()

    def test_late_model_result_is_not_accepted_or_automatically_retried(self):
        clock = AgentClock()

        def late(*_):
            clock.now = settings.ASSISTANT_TIMEOUT_SECONDS + 1
            return answer("这个迟到回答不能成为成功结果")

        model = ScriptedProvider(self.db, [late])
        result = self.send(model, clock=clock)
        self.assertEqual(result["code"], "time_budget")
        self.assertNotIn("这个迟到回答", result["reply"])
        self.assertEqual(self.send(model, clock=clock), result)
        self.assertEqual(len(model.calls), 1)
        self.assertIsNone(self.db.query(AssistantConversation).one().lease_token)
        self.assertEqual(self.db.query(AssistantTurn).one().state, "completed")

    def test_oversized_tool_result_keeps_valid_json_pair_and_server_cards(self):
        server_card = {"type": "matches", "title": "受控卡片", "items": []}
        result = {"status": "ok", "data": {"items": ["x" * 3000] * 20}, "cards": [server_card]}
        model = ScriptedProvider(self.db, [tool_call("list_matches"), answer()])
        with patch.object(service, "execute_tool", return_value=result):
            response = self.send(model)
        tool = next(m for m in model.calls[1]["messages"] if m["role"] == "tool")
        self.assertEqual(tool["tool_call_id"], "call_1")
        self.assertEqual(json.loads(tool["content"])["status"], "result_too_large")
        self.assertLessEqual(len(tool["content"].encode("utf-8")), 18000)
        self.assertEqual(response["cards"], [server_card])

    def test_real_preview_stays_pending_and_server_message_overrides_false_success(self):
        model = ScriptedProvider(self.db, [tool_call("prepare_registration", {
            "match_id": self.match.id, "mode": "personal", "team_id": None,
        }), answer("我已代你提交报名并审核通过。")])
        with patch("app.services.assistant_registration_service.confirm_registration") as confirm:
            result = self.send(model, message="帮我报名")
        confirm.assert_not_called()
        self.assertEqual(self.db.query(Registration).count(), 0)
        action = self.db.query(AssistantAction).one()
        self.assertEqual((action.user_id, action.match_id, action.state),
                         (self.actor.id, self.match.id, "pending"))
        self.assertIn("尚未提交", result["reply"])
        self.assertIn("点击确认报名", result["reply"])
        self.assertNotIn("审核通过", result["reply"])
        self.assertEqual(result["cards"][0]["type"], "registration_preview")

    def test_critical_partial_keeps_preview_and_replay_does_not_create_second_action(self):
        model = ScriptedProvider(self.db, [tool_call("prepare_registration", {
            "match_id": self.match.id, "mode": "personal", "team_id": None,
        }), ProviderError("provider_request_failed")])
        result = self.send(model, message="帮我报名")
        self.assertEqual(result["code"], "partial")
        self.assertIn("尚未提交", result["reply"])
        self.assertIn("其余整理未完成", result["reply"])
        self.assertEqual(result["cards"][0]["type"], "registration_preview")
        self.assertEqual(self.send(model, message="帮我报名"), result)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(self.db.query(AssistantAction).count(), 1)
        self.assertEqual(self.db.query(Registration).count(), 0)
        self.assertEqual(service.conversation_history(self.db, self.actor, self.conversation_id)["messages"][-1]["cards"],
                         result["cards"])

    def test_model_transport_and_unexpected_tool_failures_are_not_retried(self):
        model = ScriptedProvider(self.db, [ProviderError("provider_request_failed")])
        result = self.send(model)
        self.assertEqual(result["code"], "provider_request_failed")
        self.assertEqual(len(model.calls), 1)
        model = ScriptedProvider(self.db, [tool_call("list_matches")])
        with patch.object(service, "execute_tool", side_effect=RuntimeError("private-tool-error")) as execute:
            result = self.send(model, client_id="tool_failure")
        execute.assert_called_once()
        self.assertEqual(result["code"], "tool_execution_failed")
        self.assertNotIn("private-tool-error", json.dumps(result, ensure_ascii=False))
