"""
队伍相关 API 路由
Author: keill
Since: 2026-7-24
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.schemas.team import TeamCreateRequest, TeamInfo, JoinTeamRequest, AssignMemberRequest, TalentMarketItem, UpdateMarketDescription
from app.services import team_service
from app.services.auth_service import get_current_user, require_admin

router = APIRouter(prefix="/api/teams", tags=["队伍"])


@router.post("", response_model=TeamInfo, status_code=201)
def create_team(
    request: TeamCreateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """创建队伍，创建者自动成为队长"""
    return team_service.create_team(db, request.name, current_user.id, request.description)


@router.get("/talent-market", response_model=List[TalentMarketItem])
def talent_market(
    match_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """人才市场：列出某赛事中已报名但尚未加入队伍的用户"""
    return team_service.get_users_without_team(db, match_id)


@router.put("/talent-market/description")
def update_market_description(
    request: UpdateMarketDescription,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """更新人才市场自我介绍"""
    current_user.user_description = request.user_description
    db.commit()
    return {"message": "已更新"}


@router.post("/join")
def join_team(
    request: JoinTeamRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """加入队伍"""
    team = team_service.get_team_by_id(db, request.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != current_user.id:
        raise HTTPException(status_code=403, detail="只有队长才能添加队员")

    team_service.join_team(db, request.team_id, request.user_id)
    return {"message": "加入成功"}


@router.post("/assign")
def assign_member(
    request: AssignMemberRequest,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员分配队员"""
    team = team_service.get_team_by_id(db, request.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    team_service.join_team(db, request.team_id, request.user_id)
    return {"message": "分配成功"}


@router.get("/{team_id}", response_model=TeamInfo)
def get_team(team_id: int, db: Session = Depends(get_db)):
    """获取队伍详情"""
    team = team_service.get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    return team


@router.post("/{team_id}/approve")
def approve_team(
    team_id: int,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """审核通过队伍"""
    team = team_service.approve_team(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    return {"message": "审核通过"}


@router.post("/{team_id}/reject")
def reject_team(
    team_id: int,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """驳回队伍"""
    team = team_service.reject_team(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    return {"message": "已驳回"}


@router.delete("/{team_id}/leave")
def leave_team(
    team_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队员主动退出队伍"""
    team_service.leave_team(db, team_id, current_user.id)
    return {"message": "已退出队伍"}


@router.delete("/{team_id}/members/{user_id}")
def kick_member(
    team_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队长踢出队员"""
    team_service.kick_member(db, team_id, current_user.id, user_id)
    return {"message": "已踢出队员"}


@router.delete("/{team_id}")
def disband_team(
    team_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队长解散队伍"""
    team_service.disband_team(db, team_id, current_user.id)
    return {"message": "队伍已解散"}