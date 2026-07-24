"""
队伍业务逻辑
Author: keill
Since: 2026-7-23
"""


from typing import Optional
from sqlalchemy.orm import Session

from app.models.team import Team, TeamMember, TeamStatus, MemberRole


def create_team(db: Session, name: str, captain_id: int, description: Optional[str] = None) -> Team:
    """创建队伍，创建者自动成为队长"""
    team = Team(
        name=name,
        captain_id=captain_id,
        description=description,
        status=TeamStatus.PENDING
    )
    db.add(team)
    db.flush()

    member = TeamMember(
        team_id=team.id,
        user_id=captain_id,
        role=MemberRole.CAPTAIN
    )
    db.add(member)
    db.commit()
    db.refresh(team)
    return team

def get_team_by_id(db: Session, team_id: int) -> Optional[Team]:
    """根据队伍ID获取队伍"""
    return db.query(Team).filter(Team.id == team_id).first()


def join_team(db: Session, team_id: int, user_id: int) -> TeamMember:
    """加入队伍"""
    existing = db.query(TeamMember).filter(
    TeamMember.team_id == team_id,
    TeamMember.user_id == user_id
    ).first()
    if existing:
        return existing    # 已经在队伍里了，直接返回
    member = TeamMember(team_id=team_id, user_id=user_id, role=MemberRole.MEMBER)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def approve_team(db: Session, team_id: int) -> Optional[Team]:
    """审核通过的队伍"""
    team = get_team_by_id(db, team_id)
    if team:
        team.status = TeamStatus.APPROVED
        db.commit()
        db.refresh(team)
    return team


def reject_team(db: Session, team_id: int) -> Optional[Team]:
    """驳回队伍"""
    team = get_team_by_id(db, team_id)
    if team:
        team.status = TeamStatus.REJECTED
        db.commit()
        db.refresh(team)
    return team