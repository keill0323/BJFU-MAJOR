"""
数据模型模块。

集中导入所有 ORM 模型，使 Base.metadata 能够发现并创建对应的数据库表。

Author: keill
Since: 2026-07-21
"""
from app.models.user import User
from app.models.team import Team, TeamMember
from app.models.match import Match, MatchRound