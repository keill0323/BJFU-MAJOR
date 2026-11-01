"""滚动摘要的来源、scope、短事务和失败边界；全部使用合成历史与mock摘要。"""
from datetime import datetime, timedelta
import json
from unittest.mock import patch, Mock
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable
from tests.support import DatabaseTestCase
from app.config import settings
from app.models.assistant_session import AssistantConversation as Conversation, AssistantTurn as Turn
from app.models.assistant_summary import AssistantSummary as Summary
from app.models.assistant_action import AssistantAction
from app.migrations.assistant import upgrade
from app.services.assistant_maintenance import cleanup_expired
from app.services import assistant_summary_service as summary


def semantic(text='用户希望了解本届对阵，动态结果重新查询'):
    return json.dumps({'topics': [text], 'preferences': ['喜欢简洁中文'],
                       'open_questions': ['后续赛程待查询'], 'source_notes': ['来自已完成历史']}, ensure_ascii=False)


class SummaryTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        for name, value in {'ASSISTANT_SUMMARY_ENABLED': True, 'ASSISTANT_SUMMARY_TRIGGER_BYTES': 1000,
            'ASSISTANT_SUMMARY_KEEP_TURNS': 2, 'ASSISTANT_HISTORY_BUDGET_BYTES': 20000,
            'ASSISTANT_SUMMARY_MAX_BYTES': 4000, 'ASSISTANT_SUMMARY_INPUT_MAX_BYTES': 20000,
            'ASSISTANT_SUMMARY_TIMEOUT_SECONDS': 8}.items():
            mocked = patch.object(settings, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.actor = self.make_user()
        self.at = datetime.utcnow()
        self.conv = Conversation(id='conv-summary', user_id=self.actor.id, context_json='{}',
            expires_at=self.at + timedelta(hours=1), lease_token='lease-one', lease_until=self.at + timedelta(minutes=2))
        self.db.add(self.conv)
        self.db.flush()
        self.current = Turn(id='current-turn', conversation_id=self.conv.id, client_message_id='current-request',
            input_digest='0'*64, user_text='当前问题', state='processing', created_at=self.at)
        self.db.add(self.current)
        self.db.commit()
        self.calls = []

    def add_turn(self, index, *, scope=None, text=None, reply=None, created_at=None, identifier=None):
        row = Turn(id=identifier or f'turn-{index:04d}', conversation_id=self.conv.id,
            client_message_id=f'client-{index:04d}', input_digest='0'*64, state='completed',
            user_text=text or f'第{index}轮用户希望查询赛程。' + '用户意图' * 70,
            response_json=json.dumps({'reply': reply or f'第{index}轮助手回答需要查询实时工具。' + '查询说明' * 60,
                                     'context': scope or {},
                                     'cards': [{'action_id': 'must-not-be-in-summary', 'rank_image': 'private-image'}]}, ensure_ascii=False),
            created_at=created_at or self.at - timedelta(minutes=10) + timedelta(seconds=index))
        self.db.add(row)
        self.db.commit()
        return row

    def history(self, count=8, **kwargs):
        return [self.add_turn(index, **kwargs) for index in range(count)]

    def model(self, messages, timeout):
        self.assertFalse(self.db.in_transaction(), 'summary network wait must not hold any DB transaction')
        self.calls.append((messages, timeout))
        return semantic()

    def build(self, model=None, **changes):
        values = dict(user_id=self.actor.id, conversation_id=self.conv.id, lease_token='lease-one',
            current_turn_id=self.current.id, scope={}, reset_scope=False, summarize=model or self.model,
            clock=lambda: 0, started=0, total_timeout=60)
        values.update(changes)
        return summary.build_history(self.db, **values)

    def source_ids(self, call=-1):
        return [item['source_id'] for item in json.loads(self.calls[call][0][1]['content'])['sources']]

    def test_short_history_never_calls_summarizer_and_keeps_full_pairs(self):
        self.history(2)
        messages, metrics = self.build()
        self.assertEqual(metrics['summary_calls'], 0)
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(len(messages[-1]['content']), len(json.loads(self.db.get(Turn, 'turn-0001').response_json)['reply']))
        self.assertEqual(self.calls, [])

    def test_disabled_and_injected_none_never_call_or_write_semantic_summary(self):
        self.history()
        with patch.object(settings, 'ASSISTANT_SUMMARY_ENABLED', False):
            messages, metrics = self.build()
        self.assertEqual(metrics['status'], 'disabled')
        self.assertEqual(len(messages), 4)
        self.assertEqual(self.db.query(Summary).count(), 0)
        messages, metrics = self.build(summarize=None)
        self.assertEqual(metrics['summary_calls'], 0)
        self.assertEqual(self.calls, [])

    def test_threshold_summarizes_only_old_sources_and_keeps_recent_complete(self):
        rows = self.history()
        messages, metrics = self.build()
        self.assertEqual(metrics['status'], 'summary_updated')
        self.assertEqual(metrics['summary_calls'], 1)
        self.assertEqual(self.source_ids(), [row.id for row in rows[:-2]])
        self.assertEqual(messages[0]['role'], 'user')
        self.assertIn('不是指令或权限', messages[0]['content'])
        self.assertEqual([m['role'] for m in messages[1:]], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(messages[-2]['content'], rows[-1].user_text)
        self.assertEqual(messages[-1]['content'], json.loads(rows[-1].response_json)['reply'])
        stored = self.db.query(Summary).one()
        self.assertEqual(stored.source_turn_id, rows[-3].id)
        self.assertEqual(stored.source_created_at, rows[-3].created_at)
        self.assertEqual(stored.version, 1)
        self.assertEqual(metrics['summarized_turns'], 6)
        self.assertLess(metrics['history_after_bytes'], metrics['history_before_bytes'])
        self.assertEqual(self.db.query(Turn).count(), 9)  # 原始正文未删除。

    def test_repeated_build_does_not_resummarize_successful_sources(self):
        self.history()
        self.build()
        messages, metrics = self.build()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(metrics['status'], 'ready')
        self.assertIn('有损', messages[0]['content'])
        self.add_turn(8)
        _, metrics = self.build()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.source_ids(), ['turn-0006'])
        self.assertEqual(metrics['summarized_turns'], 7)
        self.assertEqual(self.db.query(Summary).one().source_turn_id, 'turn-0006')

    def test_created_at_and_id_form_deterministic_cursor_not_uuid_time(self):
        same = self.at - timedelta(minutes=1)
        for index in (3, 1, 4, 0, 2):
            self.add_turn(index, created_at=same)
        self.build()
        self.assertEqual(self.source_ids(), ['turn-0000', 'turn-0001', 'turn-0002'])
        self.assertEqual(self.db.query(Summary).one().source_turn_id, 'turn-0002')

    def test_mixed_scope_same_second_group_is_not_ordered_by_random_id(self):
        same_second = (self.at - timedelta(minutes=1)).replace(microsecond=0)
        self.add_turn(0, created_at=same_second - timedelta(seconds=1), scope={'match_id': 7})
        self.add_turn(9, created_at=same_second, scope={'match_id': 7})
        self.add_turn(1, created_at=same_second, scope={'match_id': 8})
        self.add_turn(10, created_at=same_second + timedelta(seconds=1), scope={'match_id': 7})
        messages, metrics = self.build(scope={'match_id': 7})
        self.assertEqual(len(messages), 2)
        self.assertIn('第10轮', messages[0]['content'])
        self.assertEqual(metrics['summary_calls'], 0)

    def test_legacy_second_precision_epoch_excludes_entire_same_second_group(self):
        same_second = self.at.replace(microsecond=0)
        self.current.created_at = same_second
        self.db.commit()
        self.add_turn(9, created_at=same_second, scope={'match_id': 7}, identifier='zz-old-same-second')
        messages, metrics = self.build(scope={'match_id': 7}, reset_scope=True)
        self.assertEqual(messages, [])
        messages, _ = self.build(scope={'match_id': 7})
        self.assertEqual(messages, [])
        self.assertEqual(metrics['summary_calls'], 0)

    def test_new_mysql_summary_timestamps_explicitly_preserve_microseconds(self):
        ddl = str(CreateTable(Summary.__table__).compile(dialect=mysql.dialect()))
        self.assertEqual(ddl.count('DATETIME(6)'), 4)

    def test_input_budget_advances_contiguous_prefix_and_never_skips_large_first_source(self):
        rows = self.history()
        full = [{'role': 'system', 'content': summary.SUMMARY_INSTRUCTION},
            {'role': 'user', 'content': summary._json({'prior_summary': {}, 'sources': [
                {'source_id': rows[0].id, 'user': rows[0].user_text,
                 'assistant': json.loads(rows[0].response_json)['reply']}]})}]
        with patch.object(settings, 'ASSISTANT_SUMMARY_INPUT_MAX_BYTES', summary._bytes(full)):
            _, metrics = self.build()
        self.assertEqual(self.source_ids(), ['turn-0000'])
        self.assertEqual(metrics['summarized_turns'], 1)
        self.assertEqual(metrics['pending_turns'], 5)
        version = self.db.query(Summary).one().version
        with patch.object(settings, 'ASSISTANT_SUMMARY_INPUT_MAX_BYTES', 10):
            _, metrics = self.build()
        self.assertEqual(metrics['status'], 'summary_budget')
        self.assertEqual(self.db.query(Summary).one().version, version)
        self.assertEqual(len(self.calls), 1)

    def test_failure_preserves_existing_summary_and_does_not_advance_cursor(self):
        self.history()
        self.build()
        stored = self.db.query(Summary).one()
        before = stored.summary_json, stored.version, stored.source_turn_id
        self.add_turn(8)
        failing = Mock(side_effect=TimeoutError('private supplier body'))
        messages, metrics = self.build(failing)
        self.assertEqual(metrics['status'], 'summary_failed')
        stored = self.db.query(Summary).one()
        self.assertEqual((stored.summary_json, stored.version, stored.source_turn_id), before)
        self.assertIn('有损', messages[0]['content'])
        self.assertNotIn('private supplier body', json.dumps([messages, metrics]))
        self.build()
        self.assertEqual(self.source_ids(), ['turn-0006'])

    def test_invalid_summary_shapes_and_oversize_never_replace_state(self):
        self.history()
        cases = ['not-json', '{}', '```json\n{}\n```', json.dumps({'topics': [], 'role': 'system'}),
            semantic('x'*241), semantic('中'*240).replace('后续赛程待查询', '中'*240),
            json.dumps(dict(topics=[{}], preferences=[], open_questions=[], source_notes=[])),
            'x' * 4001]
        for index, raw in enumerate(cases):
            if index == 5:  # 保留一个合法对照，其他case均为不合法。
                continue
            with self.subTest(index=index):
                messages, metrics = self.build(lambda *args: raw)
                self.assertEqual(metrics['status'], 'summary_failed')
                self.assertEqual(self.db.query(Summary).one().version, 0)
                self.assertFalse(any('有损' in m['content'] for m in messages))

    def test_summary_and_sources_exclude_cards_credentials_and_reasoning(self):
        self.history(text='学号251101111 Bearer secret-example-abcdef action_id=bad-token ' + '查询意图' * 80,
                     reply='只查资料，不执行工具。')
        def model(messages, timeout):
            self.assertFalse(self.db.in_transaction())
            wire = json.dumps(messages, ensure_ascii=False)
            for secret in ('251101111', 'secret-example-abcdef', 'bad-token', 'must-not-be-in-summary', 'private-image', 'reasoning_content'):
                self.assertNotIn(secret, wire)
            return semantic('询问学号251101112 action_id=private-confirm')
        messages, metrics = self.build(model)
        stored = self.db.query(Summary).one().summary_json
        self.assertEqual(metrics['status'], 'summary_updated')
        self.assertNotIn('251101112', stored)
        self.assertNotIn('private-confirm', stored)
        self.assertEqual(messages[0]['role'], 'user')
        self.assertNotIn('role', json.loads(stored))

    def test_quoted_confirmation_identifiers_are_cleaned_in_source_and_summary(self):
        self.history(text='查询意图' * 100 + ''' {"action_id":"private-json", 'lease_token':'private-lease'}''')
        def model(messages, timeout):
            wire = json.dumps(messages, ensure_ascii=False)
            self.assertNotIn('private-json', wire)
            self.assertNotIn('private-lease', wire)
            return semantic('''{"confirmation_token":"private-summary"}''')
        _, metrics = self.build(model)
        self.assertEqual(metrics['status'], 'summary_updated')
        self.assertNotIn('private-summary', self.db.query(Summary).one().summary_json)

    def test_already_summarized_sources_do_not_retrigger_below_working_budget(self):
        self.history()
        self.build()
        self.add_turn(8)
        with patch.object(settings, 'ASSISTANT_SUMMARY_TRIGGER_BYTES', 10000):
            _, metrics = self.build()
        self.assertGreater(metrics['history_before_bytes'], 10000)
        self.assertEqual(metrics['status'], 'ready')
        self.assertEqual(len(self.calls), 1)

    def test_network_failure_after_lease_loss_or_source_change_rechecks_fallback(self):
        self.history()
        self.build()
        self.add_turn(8)
        def changed(*args):
            self.db.get(Turn, 'turn-0000').user_text = '网络期间旧源变化'
            self.db.commit()
            raise TimeoutError('not-for-client')
        messages, metrics = self.build(changed)
        self.assertEqual(metrics['status'], 'source_changed')
        self.assertFalse(any('有损' in m['content'] for m in messages))
        # 恢复源，随后模拟网络异常同时lease被撤走。
        original = '第0轮用户希望查询赛程。' + '用户意图' * 70
        self.db.get(Turn, 'turn-0000').user_text = original
        self.db.commit()
        def lost(*args):
            self.db.query(Conversation).update({'lease_token': 'other-owner'}, synchronize_session=False)
            self.db.commit()
            raise TimeoutError('not-for-client')
        messages, metrics = self.build(lost)
        self.assertEqual((messages, metrics['status']), ([], 'lease_lost'))

    def test_oversized_summary_block_is_omitted_as_a_whole(self):
        self.history()
        self.build(lambda *args: semantic('中' * 240))
        with patch.object(settings, 'ASSISTANT_HISTORY_BUDGET_BYTES', 1000):
            messages, metrics = self.build()
        self.assertLessEqual(metrics['history_after_bytes'], 1000)
        self.assertFalse(any('有损' in m['content'] for m in messages))

    def test_source_edited_during_network_rejects_generated_summary(self):
        self.history()
        def changed(messages, timeout):
            self.assertFalse(self.db.in_transaction())
            row = self.db.get(Turn, 'turn-0000')
            row.user_text = '用户修改过的来源'
            self.db.commit()
            return semantic('不得被保存的过期摘要')
        messages, metrics = self.build(changed)
        self.assertEqual(metrics['status'], 'source_changed')
        self.assertEqual(self.db.query(Summary).one().version, 0)
        self.assertFalse(any('不得被保存' in m['content'] for m in messages))

    def test_previously_summarized_source_edit_disables_stale_summary(self):
        self.history()
        self.build()
        stored = self.db.query(Summary).one()
        prior = stored.summary_json, stored.version
        self.db.get(Turn, 'turn-0000').user_text = '已改变的旧来源'
        self.db.commit()
        messages, metrics = self.build()
        self.assertEqual(metrics['status'], 'source_changed')
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(any('有损' in m['content'] for m in messages))
        self.assertEqual((self.db.query(Summary).one().summary_json, self.db.query(Summary).one().version), prior)

    def test_unanchored_summary_without_source_cursor_is_not_forwarded(self):
        self.history()
        self.build()
        self.db.query(Summary).update({'source_created_at': None, 'source_turn_id': None,
                                      'source_fingerprint': ''}, synchronize_session=False)
        self.db.commit()
        messages, metrics = self.build()
        self.assertEqual(metrics['status'], 'source_changed')
        self.assertFalse(any('有损' in m['content'] for m in messages))

    def test_cas_also_compares_source_cursor_even_if_other_writer_did_not_bump_version(self):
        self.history()
        def conflict(*args):
            self.db.query(Summary).update({'source_turn_id': 'different-cursor'}, synchronize_session=False)
            self.db.commit()
            return semantic('迟到摘要')
        _, metrics = self.build(conflict)
        self.assertEqual(metrics['status'], 'summary_conflict')
        self.assertEqual(self.db.query(Summary).one().source_turn_id, 'different-cursor')
        self.assertEqual(self.db.query(Summary).one().summary_json, '{}')

    def test_cas_version_conflict_never_overwrites_competing_state(self):
        self.history()
        def conflict(*args):
            self.db.query(Summary).update({'version': 7, 'summary_json': semantic('其他合法写入')}, synchronize_session=False)
            self.db.commit()
            return semantic('迟到摘要')
        messages, metrics = self.build(conflict)
        self.assertEqual(metrics['status'], 'summary_conflict')
        self.assertEqual(self.db.query(Summary).one().version, 7)
        self.assertNotIn('迟到摘要', self.db.query(Summary).one().summary_json)
        self.assertFalse(any('迟到摘要' in m['content'] for m in messages))

    def test_lease_loss_and_expiry_during_network_never_save_or_forward(self):
        self.history()
        def lost(*args):
            self.db.query(Conversation).update({'lease_token': 'another-owner'}, synchronize_session=False)
            self.db.commit()
            return semantic('迟到摘要')
        messages, metrics = self.build(lost)
        self.assertEqual((messages, metrics['status']), ([], 'lease_lost'))
        self.assertEqual(self.db.query(Summary).one().version, 0)
        self.db.query(Conversation).update({'lease_token': 'lease-one', 'lease_until': self.at - timedelta(seconds=1)}, synchronize_session=False)
        self.db.commit()
        model = Mock()
        messages, metrics = self.build(model)
        model.assert_not_called()
        self.assertEqual((messages, metrics['status']), ([], 'lease_lost'))

    def test_total_budget_counts_summary_and_does_not_save_after_deadline(self):
        self.history()
        elapsed = [0]
        def too_late(*args):
            elapsed[0] = 60
            return semantic('不保存超时输出')
        _, metrics = self.build(too_late, clock=lambda: elapsed[0])
        self.assertEqual(metrics['status'], 'summary_budget')
        self.assertEqual(self.db.query(Summary).one().version, 0)
        model = Mock()
        _, metrics = self.build(model, clock=lambda: 59.1)
        model.assert_not_called()
        self.assertEqual(metrics['status'], 'summary_budget')

    def test_scope_switch_and_clear_do_not_resurrect_prior_summary(self):
        self.history(scope={'match_id': 7})
        self.build(scope={'match_id': 7})
        self.assertNotEqual(self.db.query(Summary).one().summary_json, '{}')
        messages, metrics = self.build(scope={'match_id': 8}, reset_scope=True)
        self.assertEqual(messages, [])
        self.assertEqual(self.db.query(Summary).one().summary_json, '{}')
        self.assertEqual(self.db.query(Summary).one().scope_json, '{"match_id":8}')
        messages, _ = self.build(scope={}, reset_scope=True)
        self.assertEqual(messages, [])
        self.assertEqual(self.db.query(Summary).one().scope_json, '{}')
        messages, _ = self.build(scope={'match_id': 7}, reset_scope=True)
        self.assertEqual(messages, [])
        self.assertEqual(len(self.calls), 1)

    def test_disabled_scope_reset_is_not_revived_when_summary_reenabled(self):
        self.history()
        self.build()
        with patch.object(settings, 'ASSISTANT_SUMMARY_ENABLED', False):
            messages, _ = self.build(reset_scope=True)
        self.assertEqual(messages, [])
        self.assertEqual(self.db.query(Summary).one().summary_json, '{}')
        messages, metrics = self.build()
        self.assertEqual(messages, [])
        self.assertEqual(metrics['summary_calls'], 0)

    def test_old_conversation_without_state_uses_only_last_contiguous_scope(self):
        self.add_turn(0, scope={'match_id': 7})
        self.add_turn(1, scope={'match_id': 8})
        self.add_turn(2, scope={'match_id': 7})
        messages, metrics = self.build(scope={'match_id': 7})
        self.assertEqual(len(messages), 2)
        self.assertIn('第2轮', messages[0]['content'])
        self.assertEqual(metrics['summary_calls'], 0)

    def test_account_conversation_isolation_and_missing_lease_fail_closed(self):
        self.history()
        other = self.make_user()
        self.db.commit()
        model = Mock()
        messages, metrics = self.build(model, user_id=other.id)
        self.assertEqual((messages, metrics['status']), ([], 'lease_lost'))
        model.assert_not_called()
        self.assertEqual(self.db.query(Summary).count(), 0)

    def test_missing_new_table_falls_back_to_recent_complete_history(self):
        self.history()
        Summary.__table__.drop(self.engine)
        messages, metrics = self.build()
        self.assertEqual(metrics['status'], 'summary_failed')
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.query(Turn).count(), 9)

    def test_history_budget_removes_only_whole_pairs(self):
        self.history()
        with (patch.object(settings, 'ASSISTANT_SUMMARY_ENABLED', False),
              patch.object(settings, 'ASSISTANT_HISTORY_BUDGET_BYTES', 2000)):
            messages, metrics = self.build()
        self.assertEqual(len(messages) % 2, 0)
        self.assertLessEqual(metrics['history_after_bytes'], 2000)
        if messages:
            self.assertEqual(messages[-1]['content'], json.loads(self.db.get(Turn, 'turn-0007').response_json)['reply'])

    def test_migration_is_idempotent_preserves_old_columns_history_and_registration_ledger(self):
        self.history(1)
        match = self.make_match()
        self.db.add(AssistantAction(id='ledger', user_id=self.actor.id, match_id=match.id,
            mode='personal', state='completed', snapshot_hash='0'*64,
            expires_at=self.at + timedelta(hours=1), completed_at=self.at))
        self.db.commit()
        columns = [row['name'] for row in inspect(self.engine).get_columns('assistant_conversations')]
        Summary.__table__.drop(self.engine)
        upgrade(self.engine)
        upgrade(self.engine)
        self.assertEqual([row['name'] for row in inspect(self.engine).get_columns('assistant_conversations')], columns)
        self.assertEqual(self.db.query(Turn).count(), 2)
        self.assertEqual(self.db.query(AssistantAction).one().id, 'ledger')
        self.build()
        self.assertEqual(self.db.query(Summary).count(), 1)

    def test_summary_cascades_with_expired_conversation_without_touching_user(self):
        self.history()
        self.build()
        self.db.query(Conversation).update({'expires_at': self.at - timedelta(seconds=1), 'lease_until': None}, synchronize_session=False)
        self.db.commit()
        cleanup_expired(self.db, at=self.at)
        self.assertEqual(self.db.query(Summary).count(), 0)
        self.assertEqual(self.db.query(Turn).count(), 0)
        self.assertIsNotNone(self.db.get(type(self.actor), self.actor.id))
