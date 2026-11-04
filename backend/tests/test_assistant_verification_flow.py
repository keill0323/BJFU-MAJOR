"""认证办理入口和本人消息摘要；隔离DB、脚本模型，不上传或访问外部API。"""
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event

from tests.support import DatabaseTestCase
from tests.test_assistant_agent import ScriptedProvider, answer, tool_call
from app.config import settings
from app.models.admin_notice import AdminNotice, AdminNoticeBatch
from app.models.notification import ScheduleNotification
from app.models.team import TeamApplication, TeamInvitation, ApplicationStatus
from app.models.user import User, RankApplication
from app.services import assistant_query_service as queries, assistant_service as service
from app.services.assistant_verification_service import (
    prepare_verification, prepare_verification_from_status, verification_status,
)


def available_engines():
    # 生产旧执行器没有此配置项；框架候选运行时则验证两条显式执行路径。
    if not hasattr(settings, 'ASSISTANT_ENGINE'):
        return (None,)
    if all(importlib.util.find_spec(name) is not None for name in ('langchain', 'langchain_core', 'langgraph')):
        return ('legacy', 'langchain')
    return ('legacy',)


class AssistantVerificationFlowTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.make_user(is_verified=False, rank=None)
        self.db.commit()

    def prepare(self, scope='both'):
        return queries.dispatch_read_tool(self.db, self.actor, 'prepare_verification', {'scope': scope})

    def update(self, **changes):
        for key, value in changes.items():
            setattr(self.actor, key, value)
        self.db.commit()

    def application(self, **changes):
        values = {'user_id': self.actor.id, 'status': 'pending', 'ai_rank': 'S25',
                  'rank_image': '/uploads/not-for-assistant.png', 'created_at': datetime(2026, 11, 1)}
        values.update(changes)
        self.db.add(RankApplication(**values))
        self.db.commit()

    def test_default_prepares_both_existing_user_clicked_entries(self):
        result = queries.dispatch_read_tool(self.db, self.actor, 'prepare_verification', {})
        self.assertEqual(set(result['data']), {'scope', 'student', 'rank'})
        self.assertEqual(result['data']['scope'], 'both')
        self.assertEqual([item['action'] for item in result['cards'][0]['items']], [
            {'kind': 'open_verify', 'scope': 'student'}, {'kind': 'open_verify', 'scope': 'rank'}])
        self.assertEqual([item['title'] for item in result['cards'][0]['items']],
                         ['提交在校认证材料', '提交段位材料'])
        self.assertIn('由你点击完成', result['message'])

    def test_selected_scope_returns_only_its_state_and_card(self):
        for scope in ('student', 'rank'):
            with self.subTest(scope=scope):
                result = self.prepare(scope)
                self.assertEqual(set(result['data']), {'scope', scope})
                self.assertEqual(len(result['cards'][0]['items']), 1)
                self.assertEqual(result['cards'][0]['items'][0]['action']['scope'], scope)

    def test_existing_valid_rank_can_be_updated_without_invalidating_it(self):
        self.update(rank='A+')
        result = self.prepare('rank')
        self.assertEqual(result['cards'][0]['items'][0]['title'], '更新段位材料')
        self.assertEqual(result['data']['rank']['next_action'], 'update')
        self.assertTrue(result['data']['rank']['can_upload'])
        self.assertEqual(self.db.get(User, self.actor.id).rank, 'A+')
        self.assertIn('原识别与审核流程', result['message'])

    def test_pending_rank_offers_progress_without_applying_or_duplicating_candidate(self):
        self.update(rank='B')
        self.application()
        result = self.prepare('rank')
        state = result['data']['rank']
        self.assertEqual((state['current_rank'], state['pending_rank']), ('B', 'S25'))
        self.assertFalse(state['can_upload'])
        self.assertEqual(result['cards'][0]['items'][0]['title'], '查看段位审核进度')
        self.assertIn('勿重复提交', result['message'])
        self.assertIn('尚未生效', result['message'])
        self.assertEqual(self.db.query(RankApplication).count(), 1)
        self.assertEqual(self.db.get(User, self.actor.id).rank, 'B')

    def test_rejected_rank_update_can_reupload_with_effective_rank_retained(self):
        self.update(rank='A')
        self.application(status='rejected', reject_reason='请使用PC历史页')
        result = self.prepare('rank')
        self.assertEqual(result['cards'][0]['items'][0]['title'], '重新提交段位材料')
        self.assertIn('PC历史页', result['cards'][0]['items'][0]['detail'])
        self.assertIn('当前有效段位仍为A', result['message'])

    def test_uncertain_rank_has_check_entry_instead_of_retry_upload(self):
        self.update(rank_image='/uploads/unknown.png')
        result = self.prepare('rank')
        self.assertEqual(result['cards'][0]['items'][0]['title'], '查看段位认证状态')
        self.assertFalse(result['data']['rank']['can_upload'])
        self.assertIn('避免重复提交', result['message'])

    def test_verified_student_offers_existing_status_without_recertification(self):
        self.update(is_verified=True, ai_review_status='auto_reject')
        result = self.prepare('student')
        self.assertEqual(result['cards'][0]['items'][0]['title'], '查看在校认证状态')
        self.assertFalse(result['data']['student']['can_upload'])
        self.assertIn('无需重复认证', result['message'])

    def test_pending_student_offers_progress_and_preserves_existing_upload_policy(self):
        self.update(verify_image='/uploads/student.png', ai_review_status='pending')
        result = self.prepare('student')
        self.assertEqual(result['cards'][0]['items'][0]['title'], '查看在校认证进度')
        self.assertEqual(result['data']['student']['status'], 'pending_review')
        self.assertTrue(result['data']['student']['can_upload'])

    def test_missing_or_hidden_actor_is_rechecked_in_live_database(self):
        for actor in (None, SimpleNamespace(id=True), SimpleNamespace(id=0), SimpleNamespace(id=99999)):
            with self.subTest(actor=actor), self.assertRaises(HTTPException):
                prepare_verification(self.db, actor)
        self.update(is_hidden=True)
        with self.assertRaises(HTTPException):
            prepare_verification(self.db, SimpleNamespace(id=self.actor.id, is_hidden=False))

    def test_closed_optional_scope_schema_rejects_forged_subjects_and_materials(self):
        definition = next(item['function'] for item in service.definitions()
                          if item['function']['name'] == 'prepare_verification')
        self.assertEqual(definition['parameters']['required'], [])
        self.assertFalse(definition['parameters']['additionalProperties'])
        self.assertNotIn('strict', definition)
        arguments = ({'scope': 'invalid'}, {'scope': 1}, {'scope': None},
                     {'user_id': self.actor.id}, {'image': '/uploads/image.png'},
                     {'url': 'https://example.test/material.png'}, {'rank': 'S50'}, {'approve': True})
        for value in arguments:
            with self.subTest(arguments=value):
                with self.assertRaises((HTTPException, ValueError)):
                    service.validate_arguments(value, definition['parameters'])
                    queries.dispatch_read_tool(self.db, self.actor, 'prepare_verification', value)

    def test_live_status_not_cached_and_sensitive_reasons_are_sanitized(self):
        self.assertEqual(self.prepare('rank')['data']['rank']['status'], 'not_submitted')
        self.application(ai_reason='学号269999991 token=secret-token https://private.test/uploads/rank.png')
        result = self.prepare('rank')
        self.assertEqual(result['data']['rank']['status'], 'pending_review')
        raw = json.dumps(result, ensure_ascii=False)
        for private in ('269999991', 'secret-token', 'private.test', 'not-for-assistant.png'):
            self.assertNotIn(private, raw)

    def test_prepare_has_no_business_writes_upload_recognition_or_network(self):
        statements = []
        def capture(_connection, _cursor, statement, *_args):
            statements.append(statement.lstrip().upper())
        event.listen(self.engine, 'before_cursor_execute', capture)
        targets = ('app.services.ai_review_service.review_image', 'app.services.ai_review_service.review_rank',
                   'app.services.ai_review_service._send_ai', 'app.services.auth_service.create_rank_application',
                   'app.services.auth_service.apply_rank_upload', 'app.services.auth_service.apply_verify_result',
                   'app.services.upload_service.save_upload_image')
        try:
            with ExitStack() as stack:
                operations = [stack.enter_context(patch(target)) for target in targets]
                self.prepare()
                for operation in operations:
                    operation.assert_not_called()
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertTrue(statements)
        self.assertTrue(all(statement.startswith('SELECT') for statement in statements))
        self.assertEqual(self.db.query(RankApplication).count(), 0)

    def test_preparation_from_snapshot_is_pure_and_preserves_original_status(self):
        self.update(rank='A+')
        snapshot = verification_status(self.db, self.actor)
        original = deepcopy(snapshot)
        statements = []
        def capture(_connection, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            result = prepare_verification_from_status(snapshot, 'rank')
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(snapshot, original)
        self.assertEqual(statements, [])
        self.assertEqual(result['data']['rank']['next_action'], 'update')
        self.assertEqual(snapshot['data']['rank']['next_action'], 'view')

    def test_preparation_controls_false_model_approval_and_preserves_query_tool(self):
        self.application()
        for engine in available_engines():
            engine_setting = patch.object(settings, 'ASSISTANT_ENGINE', engine) if engine else nullcontext()
            with self.subTest(engine=engine), engine_setting, patch.object(settings, 'ASSISTANT_ENABLED', True):
                conversation = service.create_conversation(self.db, self.actor)['conversation_id']
                model = ScriptedProvider(self.db, [tool_call('get_my_verification', identifier='status_1'),
                    tool_call('prepare_verification', {'scope': 'rank'}, identifier='prepare_2'),
                    answer('认证已替你通过，S25已生效。')])
                result = service.send_message(self.db, self.actor, conversation, '帮我更新段位',
                                              'prepare_verify_test', provider=model)
                self.assertEqual(result['code'], 'completed')
                self.assertIn('已准备认证入口', result['reply'])
                self.assertIn('尚未生效', result['reply'])
                self.assertNotIn('已替你通过', result['reply'])
                self.assertNotIn('在校认证：', result['reply'])  # 后续办理提示替代本轮先前查询摘要。
                self.assertEqual(len(result['cards']), 1)
                self.assertEqual(result['cards'][-1]['items'][0]['action'], {'kind': 'open_verify', 'scope': 'rank'})
                payload = json.loads(next(message['content'] for message in model.calls[-1]['messages']
                                          if message['role'] == 'tool' and message['tool_call_id'] == 'prepare_2'))
                self.assertEqual(set(payload['data']), {'scope', 'rank'})
        self.assertEqual(self.db.query(RankApplication).count(), 1)
        self.assertIsNone(self.db.get(User, self.actor.id).rank)

    def test_status_query_after_preparation_keeps_selected_flow_and_replaces_card(self):
        self.update(rank='A+', is_verified=True)
        for engine in available_engines():
            for scope in ('student', 'rank', 'both'):
                engine_setting = patch.object(settings, 'ASSISTANT_ENGINE', engine) if engine else nullcontext()
                with self.subTest(engine=engine, scope=scope), engine_setting, patch.object(settings, 'ASSISTANT_ENABLED', True):
                    conversation = service.create_conversation(self.db, self.actor)['conversation_id']
                    model = ScriptedProvider(self.db, [tool_call('prepare_verification', {'scope': scope}, 'prepare_1'),
                        tool_call('get_my_verification', identifier='refresh_2'), answer()])
                    result = service.send_message(self.db, self.actor, conversation, '帮我办理认证',
                                                  'prepare_refresh_test', provider=model)
                    targets = ['student', 'rank'] if scope == 'both' else [scope]
                    self.assertEqual(result['code'], 'completed')
                    self.assertEqual(len(result['cards']), 1)
                    self.assertEqual([item['action']['scope'] for item in result['cards'][0]['items']], targets)
                    self.assertIn('已准备认证入口', result['reply'])
                    if 'rank' in targets:
                        self.assertIn('更新段位材料', result['reply'])
                    payload = json.loads(next(message['content'] for message in model.calls[-1]['messages']
                                              if message['role'] == 'tool' and message['tool_call_id'] == 'refresh_2'))
                    self.assertEqual(payload['data']['scope'], scope)
                    self.assertEqual(set(payload['data']), {'scope', *targets})

    def test_status_refresh_uses_new_pending_application_without_stale_update_entry(self):
        self.update(rank='A+')
        for engine in available_engines():
            self.db.query(RankApplication).delete()
            self.db.commit()
            engine_setting = patch.object(settings, 'ASSISTANT_ENGINE', engine) if engine else nullcontext()
            with self.subTest(engine=engine), engine_setting, patch.object(settings, 'ASSISTANT_ENABLED', True):
                conversation = service.create_conversation(self.db, self.actor)['conversation_id']
                def application_arrives(_messages, _tools, _timeout):
                    self.application()
                    return tool_call('get_my_verification', identifier='refresh_2')
                model = ScriptedProvider(self.db, [tool_call('prepare_verification', {'scope': 'rank'}, 'prepare_1'),
                    application_arrives, answer('你现在可以继续提交新的段位材料。')])
                result = service.send_message(self.db, self.actor, conversation, '帮我更新段位',
                                              'prepare_pending_refresh', provider=model)
                self.assertEqual(result['code'], 'completed')
                self.assertEqual(len(result['cards']), 1)
                self.assertEqual(result['cards'][0]['items'][0]['title'], '查看段位审核进度')
                self.assertEqual(len(result['cards'][0]['items']), 1)
                self.assertIn('勿重复提交', result['reply'])
                self.assertIn('S25尚未生效', result['reply'])
                self.assertNotIn('可点击“更新段位材料”', result['reply'])
                payload = json.loads(next(message['content'] for message in model.calls[-1]['messages']
                                          if message['role'] == 'tool' and message['tool_call_id'] == 'refresh_2'))
                self.assertEqual(payload['data']['rank']['status'], 'pending_review')
                self.assertFalse(payload['data']['rank']['can_upload'])
                self.assertEqual(payload['data']['rank']['current_rank'], 'A+')

    def test_approved_refresh_restores_update_entry_without_stale_pending_state(self):
        for engine in available_engines():
            self.db.query(RankApplication).delete()
            self.update(rank='A+')
            self.application()
            engine_setting = patch.object(settings, 'ASSISTANT_ENGINE', engine) if engine else nullcontext()
            with self.subTest(engine=engine), engine_setting, patch.object(settings, 'ASSISTANT_ENABLED', True):
                conversation = service.create_conversation(self.db, self.actor)['conversation_id']
                def approval_arrives(_messages, _tools, _timeout):
                    self.db.query(RankApplication).filter_by(user_id=self.actor.id).update({'status': 'approved'})
                    self.update(rank='S25')
                    return tool_call('get_my_verification', identifier='refresh_2')
                model = ScriptedProvider(self.db, [tool_call('prepare_verification', {'scope': 'rank'}, 'prepare_1'),
                    approval_arrives, answer('仍然待审，请等待。')])
                result = service.send_message(self.db, self.actor, conversation, '帮我更新段位',
                                              'prepare_approved_refresh', provider=model)
                self.assertEqual(result['code'], 'completed')
                self.assertEqual(len(result['cards']), 1)
                self.assertEqual(result['cards'][0]['items'][0]['title'], '更新段位材料')
                self.assertIn('当前有效段位为S25', result['reply'])
                self.assertNotIn('尚未生效', result['reply'])
                self.assertNotIn('待管理员审核', result['reply'])
                payload = json.loads(next(message['content'] for message in model.calls[-1]['messages']
                                          if message['role'] == 'tool' and message['tool_call_id'] == 'refresh_2'))
                self.assertEqual(payload['data']['rank']['status'], 'verified')
                self.assertTrue(payload['data']['rank']['can_upload'])
                self.assertIsNone(payload['data']['rank']['pending_rank'])


class AssistantNoticeSummaryTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.make_user()
        self.other = self.make_user()
        self.db.commit()

    def summary(self, actor=None, arguments=None):
        return queries.dispatch_read_tool(self.db, actor or self.actor, 'get_my_notice_summary', arguments or {})

    def schedule(self, user, *, read_at=None):
        row = ScheduleNotification(user_id=user.id, match_id=800, round_id=900,
            kind='admin_scheduled', match_name='private-match-name', team1_name='private-team-1',
            team2_name='private-team-2', scheduled_time=datetime(2026, 11, 5, 13),
            created_at=datetime(2026, 11, 4, 12), read_at=read_at)
        self.db.add(row)
        self.db.flush()
        return row

    def notice(self, user, *, read_at=None):
        number = self._next()
        batch = AdminNoticeBatch(sender_id=self.other.id, request_id='summary-' + str(number),
            payload_hash='a' * 64, title='secret-notice-title', content='secret-notice-body 学号269999991',
            recipient_count=1, created_at=datetime(2026, 11, 4, 12))
        self.db.add(batch)
        self.db.flush()
        row = AdminNotice(batch_id=batch.id, user_id=user.id, read_at=read_at)
        self.db.add(row)
        self.db.flush()
        return row

    def test_zero_counts_do_not_mean_private_chat_or_all_messages_are_empty(self):
        result = self.summary()
        self.assertEqual(result['data'], dict(invitation_count=0, application_count=0, schedule_count=0,
                                             admin_notice_count=0, total=0))
        self.assertEqual(result['cards'], [])
        self.assertIn('不含私聊', result['message'])

    def test_only_recipients_unread_notices_count_without_exposing_text_or_marking_read(self):
        mine = self.notice(self.actor)
        schedule = self.schedule(self.actor)
        self.notice(self.actor, read_at=datetime(2026, 11, 4))
        self.schedule(self.actor, read_at=datetime(2026, 11, 4))
        self.notice(self.other)
        self.schedule(self.other)
        self.db.commit()
        statements = []
        def capture(_connection, _cursor, statement, *_args):
            statements.append(statement.lstrip().upper())
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            result = self.summary(SimpleNamespace(id=self.actor.id, role='admin'))
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual((result['data']['schedule_count'], result['data']['admin_notice_count'], result['data']['total']),
                         (1, 1, 2))
        raw = json.dumps(result, ensure_ascii=False)
        for secret in ('secret-notice', 'private-match', 'private-team', '269999991', 'admin_notice_title'):
            self.assertNotIn(secret, raw)
        self.assertTrue(all(statement.startswith('SELECT') for statement in statements))
        self.assertFalse(any('ADMIN_NOTICE_BATCHES.CONTENT' in statement for statement in statements))
        self.assertIsNone(self.db.get(AdminNotice, mine.id).read_at)
        self.assertIsNone(self.db.get(ScheduleNotification, schedule.id).read_at)

    def test_actionable_invitations_and_captain_applications_follow_existing_rules(self):
        my_team = self.make_team(captain=self.actor)
        free_player, joined_player = self.make_user(), self.make_user()
        self.make_team(captain=joined_player)
        pending = TeamApplication(team_id=my_team.id, user_id=free_player.id, message='secret-application-text')
        stale = TeamApplication(team_id=my_team.id, user_id=joined_player.id)
        rejected = TeamApplication(team_id=my_team.id, user_id=self.other.id, status=ApplicationStatus.REJECTED)
        self.db.add_all([pending, stale, rejected])
        invite = TeamInvitation(team_id=my_team.id, user_id=self.other.id, message='secret-invitation-text')
        expired = TeamInvitation(team_id=my_team.id, user_id=self.other.id, invalidated_at=datetime(2026, 11, 1))
        self.db.add_all([invite, expired])
        self.db.commit()
        result = self.summary()
        self.assertEqual(result['data']['application_count'], 1)
        self.assertEqual(result['data']['invitation_count'], 0)
        self.assertEqual(self.summary(self.other)['data']['invitation_count'], 1)
        self.assertEqual(self.summary(self.other)['data']['application_count'], 0)
        self.assertNotIn('secret-', json.dumps(result))
        self.assertEqual(self.db.get(TeamApplication, pending.id).status, ApplicationStatus.PENDING)
        self.assertEqual(self.db.get(TeamInvitation, invite.id).status, ApplicationStatus.PENDING)

    def test_hidden_or_missing_actor_and_forged_parameters_cannot_query_other_accounts(self):
        self.db.query(User).filter_by(id=self.actor.id).update({'is_hidden': True})
        self.db.commit()
        for actor in (SimpleNamespace(id=self.actor.id), SimpleNamespace(id=99999), SimpleNamespace(id=True)):
            with self.subTest(actor=actor), self.assertRaises(HTTPException):
                self.summary(actor)
        for arguments in ({'user_id': self.other.id}, {'mark_read': True}, {'include_content': True}):
            with self.subTest(arguments=arguments), self.assertRaises(HTTPException) as error:
                self.summary(self.other, arguments)
            self.assertEqual(error.exception.status_code, 422)

    def test_summary_response_is_sent_to_model_but_model_cannot_claim_notices_were_handled(self):
        self.notice(self.actor)
        self.db.commit()
        for engine in available_engines():
            engine_setting = patch.object(settings, 'ASSISTANT_ENGINE', engine) if engine else nullcontext()
            with self.subTest(engine=engine), engine_setting, patch.object(settings, 'ASSISTANT_ENABLED', True):
                conversation = service.create_conversation(self.db, self.actor)['conversation_id']
                model = ScriptedProvider(self.db, [tool_call('get_my_notice_summary'),
                    answer('已全部处理，并替你批准所有申请。')])
                result = service.send_message(self.db, self.actor, conversation, '我有哪些要处理的消息',
                                              'notice_summary_test', provider=model)
                self.assertEqual(result['code'], 'completed')
                self.assertIn('管理员通知1条', result['reply'])
                self.assertNotIn('已全部处理', result['reply'])
                self.assertIn('未读不代表必须操作', result['reply'])
                payload = json.loads(next(message['content'] for message in model.calls[1]['messages']
                                          if message['role'] == 'tool'))
                self.assertEqual(payload['data']['total'], 1)
                self.assertNotIn('secret-notice', json.dumps(model.calls[1]['messages']))
