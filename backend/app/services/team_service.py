"""
队伍业务逻辑
Author: keill
Since: 2026-7-23
"""


from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.team import Team, TeamMember, TeamStatus, MemberRole
from app.models.user import User
from app.models.match import Registration, RegistrationStatus


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
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="你已在某支队伍中，不能重复创建")
    db.refresh(team)
    return team

def get_team_by_id(db: Session, team_id: int) -> Optional[Team]:
    """根据队伍ID获取队伍"""
    return db.query(Team).filter(Team.id == team_id).first()


def join_team(db: Session, team_id: int, user_id: int) -> TeamMember:
    """加入队伍（一人只能加入一支队伍）"""
    # 检查是否已在任何队伍中
    already_in_team = db.query(TeamMember).filter(
        TeamMember.user_id == user_id
    ).first()
    if already_in_team:
        raise HTTPException(status_code=400, detail="该用户已在其他队伍中")
    
    # 检查是否已在此队伍中（理论上不会到这，但保留）
    existing = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user_id
    ).first()
    if existing:
        return existing
    
    member = TeamMember(team_id=team_id, user_id=user_id, role=MemberRole.MEMBER)
    db.add(member)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="该用户已在其他队伍中")
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


def get_users_without_team(db: Session, match_id: int) -> list:
    """查询某赛事中已报名但无队伍的用户"""
    # 已报名且通过审核的个人用户
    registered = db.query(Registration).filter(
        Registration.match_id == match_id,
        Registration.status == RegistrationStatus.APPROVED
    )
    registered_ids = {r.user_id for r in registered.all() if r.user_id}

    # 已有队伍的用户
    teamed_ids = {t[0] for t in db.query(TeamMember.user_id).distinct().all()}

    # 已报名但无队伍的
    free_ids = registered_ids - teamed_ids
    if free_ids:
        return db.query(User).filter(User.id.in_(free_ids)).all()
    return []


def leave_team(db: Session, team_id: int, user_id: int) -> None:
    """队员主动退队（队长不能退）"""
    member = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user_id
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="你不在该队伍中")
    if member.role == MemberRole.CAPTAIN:
        raise HTTPException(status_code=400, detail="队长不能退队，请先转让队长或解散队伍")
    db.delete(member)
    db.commit()


def kick_member(db: Session, team_id: int, captain_id: int, user_id: int) -> None:
    """队长踢人"""
    team = get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能踢人")

    if captain_id == user_id:
        raise HTTPException(status_code=400, detail="队长不能踢自己")

    member = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user_id
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="该用户不在队伍中")

    db.delete(member)
    db.commit()


def disband_team(db: Session, team_id: int, captain_id: int) -> None:
    """队长解散队伍，删除队伍及所有成员记录"""
    team = get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能解散队伍")

    db.query(TeamMember).filter(TeamMember.team_id == team_id).delete()
    db.delete(team)
    db.commit()
