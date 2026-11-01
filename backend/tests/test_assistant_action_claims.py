"""回复动作声明和普通赛事事实区分；纯样例及隔离编排，不调用模型。"""
from unittest.mock import patch

from tests.support import DatabaseTestCase
from tests.test_assistant_agent import ScriptedProvider, answer, tool_call
from app.config import settings
from app.models.match import Registration
from app.services import assistant_service
from app.services.assistant_action_policy import action_claim_kind


class AssistantActionPolicyTests(DatabaseTestCase):
    def test_normal_query_facts_and_third_person_results_are_not_assistant_execution_claims(self):
        facts = (
            '我查到已报名的队伍中，拉面队目前战绩最好。',
            '赛事报名已经截止，当前进入淘汰赛。',
            '我已查询报名状态，A队仍然待审。',
            '我查到赛事报名截止时间为今晚。',
            '我查询到你的队伍已提交申请。',
            '队伍已提交申请，等待管理员处理。',
            '管理员已审核通过A队报名。',
            'A队报名成功，目前3胜1负。',
            '我已经为你整理了报名条件。',
            '我已为你准备报名预览，请你点击确认。',
            '我已经替你完成了报名预览。',
            '我已完成在校认证材料的说明。',
            '我已通过公开数据计算对局结果。',
            '我已向你展示报名步骤。',
            '我已经申请查询赛事数据。',
            '我已经申请了查询赛事数据。',
            '你的队伍中有3位队员认证已通过。',
            '已报名的队伍中有两队3胜1负，不能只按胜场决定名次。',
            '邀请已由队长发送；我只能查询当前记录。',
            '你可以在队伍页面提交申请。',
        )
        for text in facts:
            with self.subTest(text=text):
                self.assertIsNone(action_claim_kind(text))

    def test_explicit_assistant_completed_writes_are_blocked(self):
        claims = (
            '我已替你报名并审核通过。',
            '我已替你提交报名并审核通过，报名成功。',
            '已为你发邀请。',
            '我已经帮你报名成功。',
            '我已为你批准报名。',
            '我已审核通过你的入队申请。',
            '我已向队长发送入队申请。',
            '我已给对方队长发送消息。',
            '我已将你的报名申请提交给管理员。',
            '我替你发私信了。',
            '本助手已帮你联系对方队长。',
            '我已为你发起组队邀请。',
            '我这边已经为你提交报名。',
            '目前我已替你报名。',
            '我已为您提交报名。',
            '已帮您发送邀请。',
            '我已向管理员递交报名申请。',
            '我提交了报名申请。',
            '我报名成功了。',
            '报名成功。',
        )
        for text in claims:
            with self.subTest(text=text):
                self.assertEqual(action_claim_kind(text), 'business')

    def test_certification_results_without_live_proof_are_blocked(self):
        claims = (
            '我已替你认证通过。',
            '已帮你完成在校认证。',
            '我已替你提交认证材料。',
            '你的在校认证已经通过。',
            '认证已通过，请继续报名。',
            '你已经通过了段位认证。',
            '你的段位已更新为S25。',
            '段位已经生效。',
            '你的S25已经生效。',
            '我查到你的认证已通过。',
        )
        for text in claims:
            with self.subTest(text=text):
                self.assertEqual(action_claim_kind(text), 'verification')

    def test_conditions_questions_and_quoted_ui_labels_are_not_assertions(self):
        explanations = (
            '认证通过后，还需要满足报名条件。',
            '认证成功的标准是材料真实有效。',
            '认证成功不代表满足报名资格。',
            '在校认证已通过即可继续查询报名资格。',
            '在校认证已通过的用户仍需满足报名条件。',
            '已通过认证的队长可以提交队伍报名。',
            '如果你的认证已通过，可以继续查看报名资格。',
            '如果我已替你提交报名，就不需要再确认吗？',
            '你的认证已通过了吗？',
            '页面显示“认证已通过”时，才表示本次流程结束。',
            '不要相信“我已替你报名”这类没有状态卡证明的话。',
            '目前有36名选手认证已通过。',
            '我看到有36名选手认证已通过。',
            '我可以帮你准备报名预览。',
            '我会替你查询报名状态。',
            '报名成功后，还需要按时参赛。',
            '报名成功需要满足人数和身份要求。',
            '我报名成功后就可以参加比赛吗？',
        )
        for text in explanations:
            with self.subTest(text=text):
                self.assertIsNone(action_claim_kind(text))

    def test_markdown_and_multiple_clauses_keep_explicit_claim_detection(self):
        self.assertEqual(action_claim_kind('好的。\n- 我已为你发出邀请。'), 'business')
        self.assertEqual(action_claim_kind('1. 我已替你报名。'), 'business')
        self.assertIsNone(action_claim_kind(None))


class AssistantActionGuardIntegrationTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        mocked = patch.object(settings, 'ASSISTANT_ENABLED', True)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.actor = self.make_user()
        self.db.commit()
        self.conversation_id = assistant_service.create_conversation(self.db, self.actor)['conversation_id']

    def send(self, model, client_id='claim_test_1'):
        return assistant_service.send_message(self.db, self.actor, self.conversation_id,
            '26届起源杯目前哪个队战绩最好？', client_id, provider=model)

    def test_rank_question_fact_answer_no_longer_becomes_unsupported_action_claim(self):
        text = '我查到已报名的队伍中，拉面队目前战绩最好。赛事报名已经截止。'
        result = self.send(ScriptedProvider(self.db, [answer(text)]))
        self.assertEqual((result['code'], result['reply']), ('completed', text))
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_public_match_query_then_ordinary_status_report_stays_completed(self):
        self.make_match(name='起源杯')
        self.db.commit()
        text = '我已查询报名状态，赛事报名已经截止，接下来需比较实际完赛成绩。'
        model = ScriptedProvider(self.db, [tool_call('list_matches'), answer(text)])
        result = self.send(model)
        self.assertEqual(result['code'], 'completed')
        self.assertEqual(result['reply'], text)
        self.assertEqual(result['cards'][0]['type'], 'matches')

    def test_explicit_unsupported_write_is_still_blocked_and_never_writes_registration(self):
        result = self.send(ScriptedProvider(self.db, [answer('我已替你报名并审核通过。')]))
        self.assertEqual(result['code'], 'unsupported_action_claim')
        self.assertNotIn('报名并审核通过', result['reply'])
        self.assertEqual(self.db.query(Registration).count(), 0)

    def test_real_registration_precheck_summary_keeps_priority_over_model_claim(self):
        controlled = {'status': 'blocked', 'message': '当前资格尚不满足，尚未提交报名。', 'data': {}, 'cards': []}
        model = ScriptedProvider(self.db, [tool_call('check_registration', {'match_id': 1, 'mode': 'personal'}),
                                         answer('我已替你报名并审核通过。')])
        with patch.object(assistant_service, 'execute_tool', return_value=controlled):
            result = self.send(model)
        self.assertEqual(result['reply'], controlled['message'])
        self.assertEqual(self.db.query(Registration).count(), 0)
