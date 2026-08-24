"""
队伍相关 API 路由
Author: keill
Since: 2026-7-24
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.schemas.team import (
    TeamCreateRequest, TeamInfo, TeamListInfo, JoinTeamRequest, AssignMemberRequest,
    TalentMarketItem, UpdateMarketDescription, ApplyJoinRequest, TeamApplicationInfo,
    InviteRequest, InvitationInfo, RecruitByStudentRequest,
)
from app.models.user import User
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


@router.get("", response_model=List[TeamListInfo])
def get_teams(db: Session = Depends(get_db)):
    """获取公开队伍列表（仅审核通过的队伍）"""
    return team_service.get_all_teams(db, only_approved=True)


@router.get("/admin", response_model=List[TeamListInfo])
def get_admin_teams(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """管理后台获取全部队伍（含待审核/已驳回）"""
    return team_service.get_all_teams(db)


@router.post("/recruit")
def recruit_by_student(
    request: RecruitByStudentRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队长按学号拉人入队（入队后自动为已报名赛事的队伍补报名）"""
    team = team_service.get_team_by_id(db, request.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != current_user.id:
        raise HTTPException(status_code=403, detail="只有队长才能拉人入队")
    user = db.query(User).filter(User.student_id == request.student_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="未找到该学号对应的用户")
    team_service.join_team(db, request.team_id, user.id)
    return {"message": "已拉入队伍", "user_id": user.id}


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


@router.get("/my", response_model=Optional[TeamInfo])
def get_my_team(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """查询当前用户所在队伍（无队伍返回 null）"""
    return team_service.get_my_team(db, current_user.id)


@router.get("/invitations/my", response_model=List[InvitationInfo])
def get_my_invitations(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """查看我收到的入队邀请"""
    return team_service.get_my_invitations(db, current_user.id)


@router.post("/invitations/{invitation_id}/accept")
def accept_invitation(
    invitation_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """同意入队邀请"""
    team_service.accept_invitation(db, invitation_id, current_user.id)
    return {"message": "已接受邀请"}


@router.post("/invitations/{invitation_id}/reject")
def reject_invitation(
    invitation_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """拒绝入队邀请"""
    team_service.reject_invitation(db, invitation_id, current_user.id)
    return {"message": "已拒绝邀请"}


@router.post("/applications/{application_id}/approve")
def approve_application(
    application_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队长同意入队申请（申请人自动加入队伍）"""
    team_service.approve_application(db, application_id, current_user.id)
    return {"message": "已同意申请"}


@router.post("/applications/{application_id}/reject")
def reject_application(
    application_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队长拒绝入队申请"""
    team_service.reject_application(db, application_id, current_user.id)
    return {"message": "已拒绝申请"}


@router.get("/{team_id}", response_model=TeamInfo)
def get_team(team_id: int, db: Session = Depends(get_db)):
    """获取队伍详情"""
    team = team_service.get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    return team


@router.post("/{team_id}/apply")
def apply_join_team(
    team_id: int,
    request: ApplyJoinRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """用户申请加入队伍"""
    application = team_service.apply_join_team(
        db, team_id, current_user.id, request.message
    )
    return {"message": "申请已提交", "application_id": application.id}


@router.post("/{team_id}/invite")
def invite_player(
    team_id: int,
    request: InviteRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队长邀请玩家入队"""
    invitation = team_service.invite_player(
        db, team_id, current_user.id, request.user_id, request.message
    )
    return {"message": "邀请已发送", "invitation_id": invitation.id}


@router.get("/{team_id}/applications", response_model=List[TeamApplicationInfo])
def get_team_applications(
    team_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """队长查看本队待审核的入队申请"""
    return team_service.get_team_applications(db, team_id, current_user.id)


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


@router.delete("/admin/{team_id}")
def delete_team(
    team_id: int,
    db: Session = Depends(get_db),
    admin = Depends(require_admin),
):
    """管理员删除队伍"""
    team = team_service.delete_team(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    return {"message": "队伍已删除"}
    