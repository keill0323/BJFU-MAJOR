"""
数据模型模块。

集中导入所有 ORM 模型，使 Base.metadata 能够发现并创建对应的数据库表。

Author: keill
Since: 2026-07-21
"""
from app.models.user import User, RankApplication
from app.models.team import Team, TeamMember, TeamApplication, TeamInvitation
from app.models.match import Match, MatchRound, TeamProgress, Registration, StageWindow
from app.models.hall import ChampionSnapshot
from app.models.notification import ScheduleNotification
from app.models.admin_notice import AdminNoticeBatch, AdminNotice
from app.models.admin_audit import AdminAudit
from app.models.recruitment import RecruitmentPost, WechatSubscription, WechatOutbox
from app.models.chat import ChatConversation, ChatMember, ChatMessage, ChatEvent, ChatBlock, ChatReport, ChatSanction, ChatAudit
from app.models.player_stats import PlayerStatsMap, PlayerMapStats
from app.models.assistant_session import AssistantConversation, AssistantTurn, AssistantToolTrace, AssistantKnowledgeRevision
from app.models.assistant_action import AssistantAction
from app.models.assistant_summary import AssistantSummary
