"""赛事年份简称只作明确名称兼容，歧义仍需用户选择。"""
from tests.support import DatabaseTestCase
from app.models.match import MatchStatus
from app.services import assistant_query_service as query


class AssistantMatchAliasTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.make_user()

    def test_short_year_and_full_year_resolve_actual_public_cup_name(self):
        actual = self.make_match(name='2026 BJFU 起源杯大赛')
        self.make_match(name='起源杯测试赛事')
        self.make_match(name='2025 BJFU 起源杯大赛')
        self.db.commit()
        for keyword in ('26届起源杯', '2026届起源杯', '2026起源杯'):
            with self.subTest(keyword=keyword):
                match, failed = query._resolve_match(self.db, {'match_keyword': keyword})
                self.assertIsNone(failed)
                self.assertEqual(match.id, actual.id)
                result = query.dispatch_read_tool(self.db, self.actor, 'list_matches', {'keyword': keyword})
                self.assertEqual([item['id'] for item in result['data']['items']], [actual.id])

    def test_same_year_multiple_cups_remain_selection_not_first_id(self):
        self.make_match(name='2026 BJFU 起源杯大赛')
        self.make_match(name='2026届起源杯秋季赛')
        self.db.commit()
        match, result = query._resolve_match(self.db, {'match_keyword': '26届起源杯'})
        self.assertIsNone(match)
        self.assertEqual(result['status'], 'needs_selection')
        self.assertEqual(len(result['cards'][0]['items']), 2)

    def test_full_official_name_remains_exact_even_when_abbreviations_are_ambiguous(self):
        actual = self.make_match(name='2026 BJFU 起源杯大赛')
        self.make_match(name='2026届起源杯秋季赛')
        self.db.commit()
        match, result = query._resolve_match(self.db, {'match_keyword': actual.name})
        self.assertEqual(match.id, actual.id)
        self.assertIsNone(result)

    def test_yearless_test_cup_draft_and_wrong_year_are_not_assumed_current(self):
        self.make_match(name='起源杯测试赛事')
        self.make_match(name='2025 BJFU 起源杯大赛')
        self.make_match(name='2026 BJFU 起源杯大赛', status=MatchStatus.DRAFT)
        self.db.commit()
        match, result = query._resolve_match(self.db, {'match_keyword': '26届起源杯'})
        self.assertIsNone(match)
        self.assertEqual(result['status'], 'not_found')

    def test_generic_short_year_alone_does_not_turn_into_unfiltered_search(self):
        self.make_match(name='2026 BJFU 起源杯大赛')
        self.db.commit()
        self.assertEqual(query._match_search(self.db, '26届'), [])
