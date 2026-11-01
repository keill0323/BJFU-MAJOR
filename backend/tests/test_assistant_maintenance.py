from datetime import datetime, timedelta, timezone
from uuid import uuid4
from tests.support import DatabaseTestCase
from app.models.assistant_session import AssistantConversation, AssistantTurn, AssistantToolTrace
from app.models.assistant_action import AssistantAction
from app.services.assistant_maintenance import cleanup_expired
from app.migrations.assistant import upgrade


class AssistantMaintenanceTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.make_user()
        self.other = self.make_user()
        self.match = self.make_match()
        self.at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.commit()

    def conversation(self, user, expired=True, leased=False):
        row = AssistantConversation(id=str(uuid4()), user_id=user.id, context_json='{}',
             expires_at=self.at + timedelta(hours=-1 if expired else 1),
             lease_until=self.at+timedelta(minutes=1) if leased else None)
        self.db.add(row)
        self.db.flush()
        turn = AssistantTurn(id=str(uuid4()), conversation_id=row.id, client_message_id='a-request-id',
             input_digest='0'*64, user_text='已脱敏的助手正文', state='completed', response_json='{}')
        self.db.add(turn)
        self.db.flush()
        self.db.add(AssistantToolTrace(id=str(uuid4()), turn_id=turn.id, tool='list_matches', result_code='ok', duration_ms=1))
        self.db.flush()
        return row

    def action(self, state, days):
        row = AssistantAction(id=str(uuid4()), user_id=self.actor.id, match_id=self.match.id,
              mode='personal', state=state, snapshot_hash='0'*64,
              expires_at=self.at-timedelta(days=days),
              completed_at=self.at-timedelta(days=days) if state == 'completed' else None)
        self.db.add(row)
        self.db.flush()
        return row

    def test_cleans_expired_users_globally_and_cascades_only_assistant_content(self):
        self.conversation(self.actor)
        self.conversation(self.other)
        keep = self.conversation(self.actor, expired=False)
        leased = self.conversation(self.other, leased=True)
        self.db.commit()
        result = cleanup_expired(self.db, at=self.at)
        self.assertEqual(result['conversations_deleted'], 2)
        self.assertEqual(self.db.query(AssistantConversation).count(), 2)
        self.assertEqual(self.db.query(AssistantTurn).count(), 2)
        self.assertEqual(self.db.query(AssistantToolTrace).count(), 2)
        self.assertEqual(self.db.query(type(self.actor)).count(), 2)

    def test_preserves_recent_completed_ledger_and_bounds_cleanup_batch(self):
        for _ in range(3):
            self.conversation(self.actor)
        self.action('pending', 2)
        recent = self.action('completed', 2)
        self.action('completed', 31)
        self.db.commit()
        first = cleanup_expired(self.db, at=self.at, limit=1)
        self.assertEqual(first, {'conversations_deleted': 1, 'actions_deleted': 1})
        cleanup_expired(self.db, at=self.at)
        self.assertIsNotNone(self.db.get(AssistantAction, recent.id))
        self.assertEqual(self.db.query(AssistantAction).count(), 1)
        self.assertEqual(self.db.query(AssistantConversation).count(), 0)

    def test_migration_is_repeatable_and_preserves_existing_assistant_records(self):
        self.conversation(self.actor, expired=False)
        self.db.commit()
        upgrade(self.engine)
        upgrade(self.engine)
        self.assertEqual(self.db.query(AssistantConversation).count(), 1)
        self.assertEqual(self.db.query(AssistantTurn).count(), 1)
