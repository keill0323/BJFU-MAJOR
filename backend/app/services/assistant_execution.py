"""Per-turn business boundaries shared by the framework and rollback runtimes.

This object owns no authentication authority or durable graph state. Its
callbacks are supplied by the authenticated service, never by model arguments.
"""
import json
import math
import re
import threading

from fastapi import HTTPException

from app.services import assistant_provider as provider_api


class TurnExecution:
    def __init__(self, *, db, messages, tools, model_call, authorize, execute_tool,
                 clean_text, public_payload, validate_arguments, settings,
                 clock, started, context, memory, cards, critical, traces):
        self.db, self.messages, self.tools = db, messages, tools
        self.model_call, self.authorize, self.execute_tool = model_call, authorize, execute_tool
        self.clean_text, self.public_payload = clean_text, public_payload
        self.validate_arguments = validate_arguments
        self.clock, self.started = clock, started
        self.timeout = settings.ASSISTANT_TIMEOUT_SECONDS
        self.max_steps, self.max_calls = settings.ASSISTANT_MAX_STEPS, settings.ASSISTANT_MAX_TOOL_CALLS
        self.schemas = {item['function']['name']: item['function']['parameters'] for item in tools}
        self.context, self.memory = context, memory
        self.cards, self.critical, self.traces = cards, critical, traces
        self._summary_slots, self._preview_cards = {}, {}
        self._selection_slot = None
        self.steps, self.calls, self.reasoning_bytes = 0, 0, 0
        self.seen_ids, self.invalid_calls, self.consumed = set(), set(), set()
        self.pending = {}
        self.tool_order = []
        self.reply = '处理达到本次上限，请缩小查询范围后重试。'
        self.code = 'step_limit'
        self.owner_thread = threading.get_ident()

    def _check_thread(self):
        if threading.get_ident() != self.owner_thread:
            raise provider_api.ProviderError('tool_execution_thread_changed')

    def _remaining(self, error_code='time_budget'):
        remaining = self.timeout - (self.clock() - self.started)
        if not math.isfinite(remaining) or remaining <= 0:
            raise provider_api.ProviderError(error_code)
        return remaining

    def model_response(self, messages):
        self._check_thread()
        if self.steps >= self.max_steps:
            # A terminal graph response, with no extra paid request or retry.
            return {'role': 'assistant', 'content': self.reply, 'tool_calls': []}
        remaining = self._remaining()
        self.db.rollback()
        self.steps += 1
        response = self.model_call(messages, self.tools, min(20, remaining))
        self._remaining()
        if not isinstance(response, dict):
            raise provider_api.ProviderError('provider_response_invalid')
        content, calls = response.get('content') or '', response.get('tool_calls') or []
        if not isinstance(content, str) or len(content) > 10000 or not isinstance(calls, list) or len(calls) > 3:
            raise provider_api.ProviderError('provider_response_invalid')
        reasoning = provider_api.validated_reasoning(response)
        if not calls:
            self.reply = self.clean_text(content).strip()[:2200] or '请补充想查询的赛事或队伍。'
            self.code = 'completed'
            return {'role': 'assistant', 'content': self.reply, 'tool_calls': []}
        normalized = []
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get('function'), dict):
                raise provider_api.ProviderError('provider_response_invalid')
            identifier = call.get('id')
            if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier)
                    or identifier in self.seen_ids):
                raise provider_api.ProviderError('provider_response_invalid')
            self.seen_ids.add(identifier)
            function = call['function']
            name, raw = function.get('name'), function.get('arguments')
            if not isinstance(name, str) or len(name) > 64:
                raise provider_api.ProviderError('provider_response_invalid')
            try:
                if name not in self.schemas or not isinstance(raw, str) or len(raw) > 4096:
                    raise ValueError('invalid tool')
                arguments = json.loads(raw)
                self.validate_arguments(arguments, self.schemas[name])
                safe = json.dumps(self.public_payload(arguments), ensure_ascii=False)
            except (ValueError, TypeError):
                self.invalid_calls.add(identifier)
                safe = '{}'
            normalized.append({'id': identifier, 'type': 'function', 'function': {
                'name': name if name in self.schemas else 'unknown_tool', 'arguments': safe}})
        message = {'role': 'assistant', 'content': self.clean_text(content), 'tool_calls': normalized}
        if reasoning is not None:
            self.reasoning_bytes += len(reasoning.encode('utf-8'))
            if self.reasoning_bytes > provider_api.MAX_TURN_REASONING_BYTES:
                raise provider_api.ProviderError('provider_reasoning_limit')
            message['reasoning_content'] = reasoning
        self.pending.update((call['id'], call) for call in normalized)
        self.tool_order.extend(call['id'] for call in normalized)
        return message

    def tool_response(self, identifier, name, arguments):
        self._check_thread()
        call = self.pending.get(identifier)
        if (call is None or identifier in self.consumed or call['function']['name'] != name
                or json.loads(call['function']['arguments']) != arguments):
            raise provider_api.ProviderError('provider_response_invalid')
        if not self.tool_order or self.tool_order[0] != identifier:
            raise provider_api.ProviderError('tool_execution_order_changed')
        self.tool_order.pop(0)
        self.consumed.add(identifier)
        self.calls += 1
        if self.calls > self.max_calls:
            raise provider_api.ProviderError('tool_budget')
        self._remaining('tool_budget')
        tick = self.clock()
        try:
            if name not in self.schemas or identifier in self.invalid_calls:
                raise ValueError('unknown tool')
            self.validate_arguments(arguments, self.schemas[name])
            actor = self.authorize()
            result = self.execute_tool(actor, name, arguments)
            if not isinstance(result, dict):
                raise ValueError('invalid tool result')
            target_match = arguments.get('match_id')
            if (name in ('prepare_registration', 'check_registration')
                    and (name == 'prepare_registration' or result.get('status') == 'blocked')):
                previous = self._preview_cards.pop(target_match, [])
                self.cards[:] = [card for card in self.cards if not any(card is old for old in previous)]
            self.cards.extend(result.get('cards', [])[:5])
            if name == 'prepare_registration':
                self._preview_cards[target_match] = [card for card in result.get('cards', [])[:5]
                    if card.get('type') == 'registration_preview']
            candidates = []
            for card in result.get('cards', []):
                if name == 'get_my_team':
                    break  # 本人队伍是账号事实，不是新一组待选对象；保留刚列出的赛事候选。
                if card.get('type') not in ('matches', 'teams', 'players', 'player_stats'):
                    continue
                for item in card.get('items', [])[:20]:
                    kind = {'matches': 'match', 'teams': 'team', 'players': 'player', 'player_stats': 'stats_player'}[card['type']]
                    target = item.get('id')
                    if kind == 'stats_player':
                        if not isinstance(target, str) or not re.fullmatch(r'[0-9a-f]{32}', target):
                            continue
                    elif type(target) is not int or target < 1:
                        continue
                    candidates.append({'kind': kind, 'id': target, 'title': self.clean_text(item.get('title', ''))[:100],
                                       'match_id': arguments.get('match_id') or self.context.get('match_id')})
            if candidates:
                self.memory = {'last_candidates': candidates[:20]}
            selection = result.get('data', {}).get('selection')
            if isinstance(selection, dict) and selection.get('kind') == 'stats_player':
                self.memory = {'selection': self.public_payload(selection)}
            if (result.get('status') == 'needs_selection'
                    and any(card.get('type') == 'matches' for card in result.get('cards', []))):
                summary = self.clean_text(result.get('message', '请选择要继续的赛事。'))
                if self._selection_slot is None:
                    self._selection_slot = len(self.critical)
                    self.critical.append(summary)
                else:
                    self.critical[self._selection_slot] = summary
            elif (self._selection_slot is not None and name in (
                    'prepare_registration', 'check_registration', 'get_my_registration',
                    'get_match_rounds', 'get_match_standings', 'get_player_stats')
                    and ((result.get('data') or {}).get('match_id') or arguments.get('match_id'))):
                self.critical[self._selection_slot] = ''
            if (name in ('prepare_registration', 'check_registration', 'get_my_registration', 'get_my_verification', 'get_match_standings')
                    or (name == 'get_match_rounds' and arguments.get('next_only') is True)):
                # 修正方式后的成功预览取代同一赛事先前误用personal的错误，
                # 不把相互冲突的中间尝试全部拼成最终答复。
                data = result.get('data') or {}
                family = 'registration' if name in ('prepare_registration', 'check_registration', 'get_my_registration') else name
                subject = (family, data.get('match_id') or arguments.get('match_id'),
                           data.get('team_id') if name == 'get_match_rounds' else None)
                summary = self.clean_text(result.get('message', '请以结果卡片为准'))
                if (name == 'get_my_registration' and self._preview_cards.get(target_match)
                        and data.get('registration_status') == 'not_registered'):
                    pass  # 尚未确认的有效预览与“未报名”一致，保留预览作为当前操作提示。
                elif subject in self._summary_slots:
                    self.critical[self._summary_slots[subject]] = summary
                else:
                    self._summary_slots[subject] = len(self.critical)
                    self.critical.append(summary)
            result_code = str(result.get('status', 'ok'))[:40]
            model_result = self.public_payload(result)
        except (ValueError, TypeError, KeyError):
            result_code = 'invalid_tool_arguments'
            model_result = {'status': 'error', 'message': '工具或参数不在允许范围，请更正或询问用户。'}
        except HTTPException as error:
            result_code = 'permission_or_business_error'
            detail = error.detail if isinstance(error.detail, str) else error.detail.get('message', '操作不可用')
            model_result = {'status': 'blocked', 'message': self.clean_text(detail)}
        finally:
            self.db.rollback()
        self.traces.append((name if name in self.schemas else 'unknown_tool', result_code, int((self.clock() - tick) * 1000)))
        content = json.dumps(model_result, ensure_ascii=False)
        if len(content.encode('utf-8')) > 18000:
            content = json.dumps({'status': 'result_too_large', 'data': {'truncated': True},
                'message': '查询结果超出本轮预算，请缩小赛事、队伍或选手范围后重新查询；完整卡片以页面为准。'}, ensure_ascii=False)
        return {'role': 'tool', 'tool_call_id': identifier, 'content': content}


def run_legacy(execution):
    """Explicit rollback engine; never called as an automatic fallback."""
    while execution.steps < execution.max_steps:
        response = execution.model_response(execution.messages)
        calls = response.get('tool_calls') or []
        if not calls:
            break
        execution.messages.append(response)
        for call in calls:
            execution.messages.append(execution.tool_response(call['id'], call['function']['name'],
                json.loads(call['function']['arguments'])))
