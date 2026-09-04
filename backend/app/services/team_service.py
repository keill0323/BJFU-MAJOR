"""
队伍业务逻辑
Author: keill
Since: 2026-7-23
"""


from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.team import Team, TeamMember, TeamStatus, MemberRole, TeamApplication, TeamInvitation, ApplicationStatus
from app.models.user import User
from app.models.match import Match, Registration, RegistrationStatus, TeamProgress


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
    recalc_team_rating(db, team.id)
    return team



def get_team_by_id(db: Session, team_id: int) -> Optional[Team]:
    """根据队伍ID获取队伍"""
    return db.query(Team).filter(Team.id == team_id).first()


def recalc_team_rating(db: Session, team_id: int) -> None:
    """计算队员rating之和作为队伍rating"""
    team = get_team_by_id(db, team_id)
    if not team:
        return

    # 获取队员评分
    rows = db.query(User.individual_rating).join(TeamMember, TeamMember.user_id == User.id).filter(TeamMember.team_id == team_id).all()
    # 取最高五人评分
    scores = sorted([(r[0] or 0) for r in rows], reverse=True)
    team.rating = sum(scores[:5])
    db.commit()


def recalc_user_team_rating(db: Session, user_id: int) -> None:
    """当用户 rating 变动时，重新计算其所在队伍的 rating"""
    member = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
    if member:
        recalc_team_rating(db, member.team_id)


def get_my_team(db: Session, user_id: int) -> Optional[Team]:
    """根据用户ID查询其所在队伍（无队伍返回 None）"""
    member = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
    if not member:
        return None
    return db.query(Team).filter(Team.id == member.team_id).first()


def _ensure_can_join(db: Session, team_id: int, user_id: int) -> None:
    """入队前校验：若队伍已报名赛事，新成员必须完成学籍认证 + 段位认证

    报名时全队校验 is_verified 与 rank，但报名后入队（拉人/申请/邀请）
    会触发自动补报名，因此入队入口必须同步拦截未认证用户，
    否则未认证成员会绕过报名校验直接获得报名记录。

    学籍与段位都必须通过：仅学籍通过但无段位（未认证段位）的玩家
    仍可被拉入已报名队伍并自动补报名，反而绕过了段位认证（防炸鱼）。
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_verified and user.rank:
        return
    team_regs = db.query(Registration).filter(
        Registration.team_id == team_id
    ).first()
    if team_regs:
        # 队伍已报名赛事，入队成员必须学籍 + 段位都认证
        if not user.is_verified:
            raise HTTPException(status_code=400, detail="该用户未完成学籍认证，无法加入已报名的队伍")
        raise HTTPException(status_code=400, detail="该用户未完成段位认证，无法加入已报名的队伍")


def _team_member_limit(db: Session, team_id: int) -> Optional[int]:
    """队伍人数上限：取所有已报名赛事 team_size 的最小值；未报名任何赛事返回 None（不限制）"""
    team_regs = db.query(Registration).filter(Registration.team_id == team_id).all()
    sizes = []
    for reg in team_regs:
        match = db.query(Match).filter(Match.id == reg.match_id).first()
        if match and match.team_size:
            sizes.append(match.team_size)
    return min(sizes) if sizes else None


def join_team(db: Session, team_id: int, user_id: int) -> TeamMember:
    """加入队伍（一人只能加入一支队伍）"""
    # 检查是否已在任何队伍中
    already_in_team = db.query(TeamMember).filter(
        TeamMember.user_id == user_id
    ).first()
    if already_in_team:
        raise HTTPException(status_code=400, detail="该用户已在其他队伍中")
    
    # 队伍已报名赛事时，新成员必须完成学籍认证
    _ensure_can_join(db, team_id, user_id)
    
    # 检查是否已在此队伍中（理论上不会到这，但保留）
    existing = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user_id
    ).first()
    if existing:
        return existing

    # 队伍人数上限校验：已报名赛事时不能超过赛事 team_size
    limit = _team_member_limit(db, team_id)
    if limit is not None:
        current = db.query(TeamMember).filter(TeamMember.team_id == team_id).count()
        if current >= limit:
            raise HTTPException(status_code=400, detail=f"队伍人数已达上限（{limit}人），无法拉人入队")
    
    member = TeamMember(team_id=team_id, user_id=user_id, role=MemberRole.MEMBER)
    db.add(member)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="该用户已在其他队伍中")
    db.refresh(member)
    # 队伍已报名的赛事，自动为该队员补报名
    sync_member_registrations(db, team_id, user_id)
    recalc_user_team_rating(db, user_id)
    return member


def sync_member_registrations(db: Session, team_id: int, user_id: int) -> None:
    """队员入队后，若该队伍已报名某赛事，自动为该队员补报名记录

    场景：队伍先报名，队员后加入 → 队员没有报名记录 → 死锁
    解决：加入时查出队伍已报名的赛事，给新队员也生成一条随队报名记录。

    若队员此前有该赛事的个人报名（team_id 为空），则升级为随队报名。
    """
    # 该队伍报名了哪些赛事（match_id 去重，避免同一赛事被多条队员记录重复处理）
    match_ids = [m[0] for m in db.query(Registration.match_id).filter(
        Registration.team_id == team_id
    ).distinct().all()]

    for match_id in match_ids:
        # 该用户是否已有此赛事的报名记录
        existing = db.query(Registration).filter(
            Registration.match_id == match_id,
            Registration.user_id == user_id,
        ).first()
        if existing:
            # 已有个人报名（team_id 为空）→ 升级为随队报名
            if existing.team_id is None:
                existing.team_id = team_id
            # 已有随队报名 → 无需处理
            continue
        # 取该队伍在此赛事的一条报名记录作为状态参考
        team_reg = db.query(Registration).filter(
            Registration.team_id == team_id,
            Registration.match_id == match_id,
        ).first()
        new_reg = Registration(
            match_id=match_id,
            team_id=team_id,
            user_id=user_id,
            status=team_reg.status if team_reg else RegistrationStatus.PENDING,
        )
        db.add(new_reg)
    db.commit()


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

    # 退队时：随队报名改回个人报名（恢复自由人状态）
    db.query(Registration).filter(
        Registration.team_id == team_id,
        Registration.user_id == user_id,
    ).update({Registration.team_id: None, Registration.status: RegistrationStatus.APPROVED})

    db.delete(member)
    db.commit()
    recalc_team_rating(db, team_id)


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

    # 被踢出时：随队报名改回个人报名（恢复自由人状态）
    db.query(Registration).filter(
        Registration.team_id == team_id,
        Registration.user_id == user_id,
    ).update({Registration.team_id: None, Registration.status: RegistrationStatus.APPROVED})

    db.delete(member)
    db.commit()
    recalc_team_rating(db, team_id)

def disband_team(db: Session, team_id: int, captain_id: int) -> None:
    """队长解散队伍，删除队伍及所有成员记录"""
    team = get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能解散队伍")

    # 清理该队伍的赛事报名记录、队伍进度
    db.query(Registration).filter(Registration.team_id == team_id).delete()
    db.query(TeamProgress).filter(TeamProgress.team_id == team_id).delete()
    # 清理入队申请和邀请记录（有外键引用，必须先删，否则删队伍会失败）
    db.query(TeamApplication).filter(TeamApplication.team_id == team_id).delete()
    db.query(TeamInvitation).filter(TeamInvitation.team_id == team_id).delete()
    db.query(TeamMember).filter(TeamMember.team_id == team_id).delete()
    db.delete(team)
    db.commit()


def delete_team(db: Session, team_id: int) -> Optional[Team]:
    """管理员删除队伍，删除队伍及所有关联记录"""
    team = get_team_by_id(db, team_id)
    if not team:
        return None

    # 清理该队伍的赛事报名记录、队伍进度
    db.query(Registration).filter(Registration.team_id == team_id).delete()
    db.query(TeamProgress).filter(TeamProgress.team_id == team_id).delete()
    # 清理入队申请和邀请记录（有外键引用，必须先删，否则删队伍会失败）
    db.query(TeamApplication).filter(TeamApplication.team_id == team_id).delete()
    db.query(TeamInvitation).filter(TeamInvitation.team_id == team_id).delete()
    db.query(TeamMember).filter(TeamMember.team_id == team_id).delete()
    db.delete(team)
    db.commit()
    return team


def get_all_teams(db: Session, only_approved: bool = False) -> list:
    """获取全部队伍（带成员数）。

    only_approved=True 时只返回审核通过的队伍（用户端公开列表）；
    默认返回全部（管理后台审核用，含待审核/已驳回）。
    """
    query = db.query(Team)
    if only_approved:
        query = query.filter(Team.status == TeamStatus.APPROVED)
    teams = query.order_by(Team.created_at.desc()).all()
    result = []
    for team in teams:
        member_count = db.query(TeamMember).filter(
            TeamMember.team_id == team.id
        ).count()
        # 队长昵称
        captain_name = None
        if team.captain:
            captain_name = team.captain.nickname or team.captain.game_id
        # 手动拼字段（Team 模型没有 member_count/captain_name 属性）
        team_data = {
            "id": team.id,
            "name": team.name,
            "captain_id": team.captain_id,
            "captain_name": captain_name,
            "description": team.description,
            "status": team.status.value if hasattr(team.status, "value") else team.status,
            "member_count": member_count,
            "rating": team.rating,
            "logo": team.logo,
            "created_at": team.created_at,
        }
        result.append(team_data)
    return result


def apply_join_team(db: Session, team_id: int, user_id: int, message: Optional[str] = None) -> TeamApplication:
    """用户申请加入队伍"""
    team = get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")

    # 一人只能在一支队伍
    already = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
    if already:
        raise HTTPException(status_code=400, detail="你已在队伍中")

    # 不能申请自己的队伍
    if team.captain_id == user_id:
        raise HTTPException(status_code=400, detail="你是队长，无需申请")

    # 不能重复申请（有 pending 的申请）
    pending = db.query(TeamApplication).filter(
        TeamApplication.team_id == team_id,
        TeamApplication.user_id == user_id,
        TeamApplication.status == ApplicationStatus.PENDING,
    ).first()
    if pending:
        raise HTTPException(status_code=400, detail="已有待审核的申请")

    application = TeamApplication(
        team_id=team_id,
        user_id=user_id,
        message=message,
        status=ApplicationStatus.PENDING,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def get_team_applications(db: Session, team_id: int, captain_id: int) -> list:
    """队长查看本队申请列表"""
    team = get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能查看申请")

    apps = db.query(TeamApplication).filter(
        TeamApplication.team_id == team_id,
        TeamApplication.status == ApplicationStatus.PENDING,
    ).order_by(TeamApplication.created_at.asc()).all()

    result = []
    for app in apps:
        nickname = app.user.nickname if app.user else None
        game_id = app.user.game_id if app.user else None
        result.append({
            "id": app.id,
            "team_id": app.team_id,
            "user_id": app.user_id,
            "nickname": nickname,
            "game_id": game_id,
            "message": app.message,
            "status": app.status.value if hasattr(app.status, "value") else app.status,
            "created_at": app.created_at,
        })
    return result


def approve_application(db: Session, application_id: int, captain_id: int) -> None:
    """队长同意申请：申请人加入队伍"""
    app = db.query(TeamApplication).filter(TeamApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")

    team = get_team_by_id(db, app.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能审核申请")

    # 申请人可能已进了别的队伍
    already = db.query(TeamMember).filter(TeamMember.user_id == app.user_id).first()
    if already:
        raise HTTPException(status_code=400, detail="申请人已加入其他队伍")

    # 队伍已报名赛事时，申请人必须完成学籍认证
    _ensure_can_join(db, app.team_id, app.user_id)

    # 入队
    member = TeamMember(team_id=app.team_id, user_id=app.user_id, role=MemberRole.MEMBER)
    db.add(member)
    app.status = ApplicationStatus.APPROVED
    db.commit()
    # 队伍已报名的赛事，自动为该新队员补报名
    sync_member_registrations(db, app.team_id, app.user_id)
    # 新队员入队，重算队伍评分
    recalc_team_rating(db, app.team_id)


def reject_application(db: Session, application_id: int, captain_id: int) -> None:
    """队长拒绝申请"""
    app = db.query(TeamApplication).filter(TeamApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")

    team = get_team_by_id(db, app.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能审核申请")

    app.status = ApplicationStatus.REJECTED
    db.commit()


def invite_player(db: Session, team_id: int, captain_id: int, user_id: int, message: Optional[str] = None) -> TeamInvitation:
    """队长邀请玩家入队（被邀请人需同意）"""
    team = get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能邀请")

    # 被邀请人已在队伍中
    already = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
    if already:
        raise HTTPException(status_code=400, detail="该玩家已在队伍中")

    # 被邀请人是队长自己
    if team.captain_id == user_id:
        raise HTTPException(status_code=400, detail="不能邀请自己")

    # 已有待处理的邀请
    pending = db.query(TeamInvitation).filter(
        TeamInvitation.team_id == team_id,
        TeamInvitation.user_id == user_id,
        TeamInvitation.status == ApplicationStatus.PENDING,
    ).first()
    if pending:
        raise HTTPException(status_code=400, detail="已邀请过该玩家，等待对方回应")

    invitation = TeamInvitation(
        team_id=team_id,
        user_id=user_id,
        message=message,
        status=ApplicationStatus.PENDING,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return invitation


def get_my_invitations(db: Session, user_id: int) -> list:
    """查看我收到的待处理邀请"""
    invites = db.query(TeamInvitation).filter(
        TeamInvitation.user_id == user_id,
        TeamInvitation.status == ApplicationStatus.PENDING,
    ).order_by(TeamInvitation.created_at.desc()).all()

    result = []
    for inv in invites:
        team_name = inv.team.name if inv.team else None
        captain_name = None
        if inv.team and inv.team.captain:
            captain_name = inv.team.captain.nickname or inv.team.captain.game_id
        result.append({
            "id": inv.id,
            "team_id": inv.team_id,
            "team_name": team_name,
            "captain_name": captain_name,
            "message": inv.message,
            "status": inv.status.value if hasattr(inv.status, "value") else inv.status,
            "created_at": inv.created_at,
        })
    return result


def accept_invitation(db: Session, invitation_id: int, user_id: int) -> None:
    """被邀请人同意入队"""
    inv = db.query(TeamInvitation).filter(TeamInvitation.id == invitation_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="邀请不存在")
    if inv.user_id != user_id:
        raise HTTPException(status_code=403, detail="这不是发给你的邀请")

    # 我已在队伍中
    already = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
    if already:
        raise HTTPException(status_code=400, detail="你已在队伍中，不能接受邀请")

    # 队伍已报名赛事时，新成员必须完成学籍认证
    _ensure_can_join(db, inv.team_id, user_id)

    # 入队
    member = TeamMember(team_id=inv.team_id, user_id=user_id, role=MemberRole.MEMBER)
    db.add(member)
    inv.status = ApplicationStatus.APPROVED
    db.commit()
    # 队伍已报名的赛事，自动为该新队员补报名
    sync_member_registrations(db, inv.team_id, user_id)
    # 新队员入队，重算队伍评分
    recalc_team_rating(db, inv.team_id)


def reject_invitation(db: Session, invitation_id: int, user_id: int) -> None:
    """被邀请人拒绝入队"""
    inv = db.query(TeamInvitation).filter(TeamInvitation.id == invitation_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="邀请不存在")
    if inv.user_id != user_id:
        raise HTTPException(status_code=403, detail="这不是发给你的邀请")

    inv.status = ApplicationStatus.REJECTED
    db.commit()
