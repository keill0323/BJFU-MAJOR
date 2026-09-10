"""Test fixtures that never load the project's .env or use its database."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_test_directory = tempfile.TemporaryDirectory(prefix="cs2-regressions-")
_original_cwd = os.getcwd()
try:
    os.chdir(_test_directory.name)
    with patch.dict(os.environ, {
        "DATABASE_URL": "sqlite:///:memory:",
        "DB_ECHO": "false",
        "SECRET_KEY": "isolated-regression-test-key",
        "AI_REVIEW_ENABLED": "false",
        "AI_API_KEY": "",
        "WX_MOCK_LOGIN": "true",
        "UPLOAD_DIR": _test_directory.name,
    }):
        from app.config import settings
        from app.database import Base
finally:
    os.chdir(_original_cwd)

import app.models  # noqa: E402,F401
from app.models.user import User  # noqa: E402
from app.models.team import Team, TeamMember, TeamStatus, MemberRole  # noqa: E402
from app.models.match import Match, MatchStatus  # noqa: E402


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self._sequence = 0

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _next(self):
        self._sequence += 1
        return self._sequence

    def make_user(self, **kwargs):
        number = self._next()
        values = dict(wx_openid=f"test-{number}", nickname=f"Player {number}",
                      student_id=f"260{number:06d}", is_verified=True,
                      rank="A", individual_rating=25)
        values.update(kwargs)
        user = User(**values)
        self.db.add(user)
        self.db.flush()
        return user

    def make_team(self, captain=None, members=None, **kwargs):
        captain = captain or self.make_user()
        values = dict(name=f"Team {self._next()}", captain_id=captain.id,
                      status=TeamStatus.APPROVED, rating=100)
        values.update(kwargs)
        team = Team(**values)
        self.db.add(team)
        self.db.flush()
        for user in [captain] + list(members or []):
            self.db.add(TeamMember(team_id=team.id, user_id=user.id,
                                  role=MemberRole.CAPTAIN if user.id == captain.id else MemberRole.MEMBER))
        self.db.flush()
        return team

    def make_match(self, **kwargs):
        values = dict(name=f"Match {self._next()}", status=MatchStatus.REGISTERING,
                      team_size=5, max_teams=16, match_type="major")
        values.update(kwargs)
        match = Match(**values)
        self.db.add(match)
        self.db.flush()
        return match
