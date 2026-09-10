"""
队伍业务逻辑
Author: keill
Since: 2026-7-23
"""


from contextlib import contextmanager
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.team import Team, TeamMember, TeamStatus, MemberRole, TeamApplication, TeamInvitation, ApplicationStatus
from app.models.user import User
from app.models.match import Match, MatchStatus, Registration, RegistrationStatus, TeamProgress
from app.services.locking import lock_row


@contextmanager
def _team_transaction(db: Session, team_id: int):
    """按赛事、队伍顺序加锁，验证、补报名与评分共用一个事务。"""
    try:
        # 与赛事报名/审批/分种子的锁顺序一致，避免补报名时倒序争抢赛事锁。
        from app.services.match_service import _lock_match
        match_ids = db.query(Registration.match_id).filter(Registration.team_id == team_id).distinct().all()
        for match_id in sorted(mid for (mid,) in match_ids):
            _lock_match(db, match_id)
        team = lock_row(db, Team, team_id)
        if not team:
            raise HTTPException(status_code=404, detail="队伍不存在")
        yield team
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="成员或报名记录发生冲突，请刷新后重试")
    except Exception:
        db.rollback()
        raise


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


def recalc_team_rating(db: Session, team_id: int, *, commit: bool = True) -> None:
    """计算队员rating之和作为队伍rating"""
    team = get_team_by_id(db, team_id)
    if not team:
        return

    # 获取队员评分
    db.flush()
    rows = db.query(User.individual_rating).join(TeamMember, TeamMember.user_id == User.id).filter(TeamMember.team_id == team_id).with_for_update().all()
    # 取最高五人评分
    scores = sorted([(r[0] or 0) for r in rows], reverse=True)
    team.rating = sum(scores[:5])
    if commit:
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
    ).with_for_update().first()
    if team_regs:
        # 队伍已报名赛事，入队成员必须学籍 + 段位都认证
        if not user.is_verified:
            raise HTTPException(status_code=400, detail="该用户未完成学籍认证，无法加入已报名的队伍")
        raise HTTPException(status_code=400, detail="该用户未完成段位认证，无法加入已报名的队伍")


def _team_member_limit(db: Session, team_id: int) -> Optional[int]:
    """队伍人数上限：取所有已报名赛事 team_size 的最小值；未报名任何赛事返回 None（不限制）"""
    team_regs = db.query(Registration).filter(Registration.team_id == team_id).with_for_update().all()
    sizes = []
    for reg in team_regs:
        match = db.query(Match).filter(Match.id == reg.match_id).first()
        if match and match.team_size:
            sizes.append(match.team_size)
    return min(sizes) if sizes else None


def _ensure_member_limit(db: Session, team_id: int, user_name: str = "该用户") -> None:
    """入队前校验队伍人数上限（已报名赛事的队伍不能超过赛事 team_size）

    三个入队入口（拉人/申请批准/接受邀请）都要调用，
    否则已满员的报名队伍仍可被塞入新成员并自动补报名造成超编。
    """
    limit = _team_member_limit(db, team_id)
    if limit is not None:
        # 使用当前读：MySQL 默认 REPEATABLE READ 下，鉴权读取可能已建立旧快照。
        current = len(db.query(TeamMember.id).filter(TeamMember.team_id == team_id).with_for_update().all())
        if current >= limit:
            raise HTTPException(status_code=400, detail=f"队伍人数已达上限（{limit}人），无法继续加入")


def _add_member(db: Session, team_id: int, user_id: int) -> TeamMember:
    """调用方已锁定队伍；成员、报名与评分只 flush，不独立提交。"""
    already_in_team = db.query(TeamMember).filter(
        TeamMember.user_id == user_id
    ).with_for_update().first()
    if already_in_team:
        raise HTTPException(status_code=400, detail="该用户已在其他队伍中")
    _ensure_can_join(db, team_id, user_id)
    _ensure_member_limit(db, team_id)
    member = TeamMember(team_id=team_id, user_id=user_id, role=MemberRole.MEMBER)
    db.add(member)
    db.flush()
    sync_member_registrations(db, team_id, user_id, commit=False)
    recalc_team_rating(db, team_id, commit=False)
    return member


def join_team(db: Session, team_id: int, user_id: int) -> TeamMember:
    """加入队伍；数据库锁保证不同入队入口共用同一个人数上限。"""
    with _team_transaction(db, team_id):
        member = _add_member(db, team_id, user_id)
    db.refresh(member)
    return member


def get_team_registration_status(
    db: Session, match_id: int, team_id: int, *, for_update: bool = False
) -> RegistrationStatus:
    """队伍审批必须有赛事进度且全员报名通过，个人 approved 不代表整队通过。"""
    regs_query = db.query(Registration).filter(
        Registration.match_id == match_id, Registration.team_id == team_id
    )
    progress_query = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id, TeamProgress.team_id == team_id
    )
    if for_update:
        regs_query = regs_query.populate_existing().with_for_update()
        progress_query = progress_query.populate_existing().with_for_update()
    regs = regs_query.all()
    progress = progress_query.first()
    if progress and regs and all(r.status == RegistrationStatus.APPROVED for r in regs):
        return RegistrationStatus.APPROVED
    if not progress and regs and all(r.status == RegistrationStatus.REJECTED for r in regs):
        return RegistrationStatus.REJECTED
    return RegistrationStatus.PENDING


def sync_member_registrations(
    db: Session, team_id: int, user_id: int, *, commit: bool = True
) -> None:
    """队员入队后，若该队伍已报名某赛事，自动为该队员补报名记录

    场景：队伍先报名，队员后加入 → 队员没有报名记录 → 死锁
    解决：加入时查出队伍已报名的赛事，给新队员也生成一条随队报名记录。

    若队员此前有该赛事的个人报名（team_id 为空），则升级为随队报名。
    """
    from app.services.match_service import _lock_match, _ensure_roster_open
    # 该队伍报名了哪些赛事（match_id 去重，避免同一赛事被多条队员记录重复处理）
    match_ids = [m[0] for m in db.query(Registration.match_id).filter(
        Registration.team_id == team_id
    ).with_for_update().all()]
    # 同一赛事多条队员记录只处理一次；先计算原队伍状态，再挂接个人记录。
    statuses = {}
    for match_id in sorted(set(match_ids)):
        _lock_match(db, match_id)
        progress = db.query(TeamProgress).filter(
            TeamProgress.match_id == match_id, TeamProgress.team_id == team_id,
        ).populate_existing().with_for_update().first()
        # 既有参赛队伍可继续按原规则补成员；没有进度的新队伍不能绕过锁名单限制。
        if not progress:
            _ensure_roster_open(db, match_id)
        statuses[match_id] = get_team_registration_status(db, match_id, team_id, for_update=True)

    for match_id, status in statuses.items():
        # 该用户是否已有此赛事的报名记录
        existing = db.query(Registration).filter(
            Registration.match_id == match_id,
            Registration.user_id == user_id,
        ).populate_existing().with_for_update().first()
        if existing:
            if existing.team_id not in (None, team_id):
                raise HTTPException(status_code=400, detail="该用户已有其他队伍的赛事报名，请先处理原报名")
            existing.team_id = team_id
            existing.status = status
            continue
        new_reg = Registration(
            match_id=match_id,
            team_id=team_id,
            user_id=user_id,
            status=status,
        )
        db.add(new_reg)
    db.flush()
    if commit:
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


def _free_agent_registration_status(db: Session, user_id: int, team_id: int):
    """队员退队/被踢后，其随队报名改回个人报名的目标状态。

    产品规则：个人报名只需「学籍认证 + 段位认证」两个判断即可通过，无需管理员额外审核。
    因此恢复自由人时：
    - 用户已完成学籍 + 段位认证 → 个人报名直接 APPROVED；
    - 否则保持原报名状态（pending 继续等审核，不越级提升）。
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.is_verified and user.rank:
        return RegistrationStatus.APPROVED
    # None 表示逐条保留各赛事原状态，不能用任意一条报名覆盖其他赛事。
    return None


def _ensure_can_remove_member(db: Session, team_id: int, user_id: int) -> None:
    """未结束的新生赛报名必须在成员离开后仍满足至少三名新生。"""
    from app.services.auth_service import effective_identity

    regs = db.query(Registration).filter(
        Registration.team_id == team_id,
        Registration.status != RegistrationStatus.REJECTED,
    ).with_for_update().all()
    match_ids = {reg.match_id for reg in regs}
    if not match_ids:
        return
    freshman_match = db.query(Match).filter(
        Match.id.in_(match_ids),
        Match.match_type == "freshman",
        Match.status != MatchStatus.FINISHED,
    ).first()
    if not freshman_match:
        return
    remaining = db.query(User).join(TeamMember, TeamMember.user_id == User.id).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id != user_id,
    ).populate_existing().with_for_update().all()
    count = sum(effective_identity(user) == "new_student" for user in remaining)
    if count < 3:
        raise HTTPException(
            status_code=400,
            detail=f"该队伍已报名新生赛，成员离开后仅剩 {count} 名新生，不满足至少 3 名新生要求",
        )


def _remove_member(db: Session, team_id: int, member: TeamMember) -> None:
    """调用方持有队伍锁，资格校验和报名恢复与成员删除一起提交。"""
    _ensure_can_remove_member(db, team_id, member.user_id)
    target_status = _free_agent_registration_status(db, member.user_id, team_id)
    registrations = db.query(Registration).filter(
        Registration.team_id == team_id,
        Registration.user_id == member.user_id,
    ).populate_existing().with_for_update().all()
    for registration in registrations:
        registration.team_id = None
        if target_status is not None:
            registration.status = target_status
    db.delete(member)
    db.flush()
    recalc_team_rating(db, team_id, commit=False)


def leave_team(db: Session, team_id: int, user_id: int) -> None:
    """队员主动退队（队长不能退）"""
    with _team_transaction(db, team_id):
        member = db.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        ).populate_existing().with_for_update().first()
        if not member:
            raise HTTPException(status_code=404, detail="你不在该队伍中")
        if member.role == MemberRole.CAPTAIN:
            raise HTTPException(status_code=400, detail="队长不能退队，请先转让队长或解散队伍")
        _remove_member(db, team_id, member)


def kick_member(db: Session, team_id: int, captain_id: int, user_id: int) -> None:
    """队长踢人"""
    with _team_transaction(db, team_id) as team:
        if team.captain_id != captain_id:
            raise HTTPException(status_code=403, detail="只有队长才能踢人")
        if captain_id == user_id:
            raise HTTPException(status_code=400, detail="队长不能踢自己")
        member = db.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        ).populate_existing().with_for_update().first()
        if not member:
            raise HTTPException(status_code=404, detail="该用户不在队伍中")
        _remove_member(db, team_id, member)

def disband_team(db: Session, team_id: int, captain_id: int) -> None:
    """队长解散队伍，删除队伍及所有成员记录"""
    team = get_team_by_id(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.captain_id != captain_id:
        raise HTTPException(status_code=403, detail="只有队长才能解散队伍")

    # 队伍已参与赛事对阵时禁止解散：对阵表外键引用该队，
    # 直接删队会破坏对手战绩（SQLite 不强制外键所以此前无感，MySQL 下会外键错误）
    from app.models.match import MatchRound
    in_rounds = db.query(MatchRound).filter(
        (MatchRound.team1_id == team_id)
        | (MatchRound.team2_id == team_id)
        | (MatchRound.winner_id == team_id)
    ).first()
    if in_rounds:
        raise HTTPException(status_code=400, detail="该队伍已参与赛事对阵，无法解散，请联系管理员处理")

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
    """管理员删除队伍，删除队伍及所有关联记录（含该队参与的对阵）"""
    team = get_team_by_id(db, team_id)
    if not team:
        return None

    # 清理该队参与的对阵（否则 MySQL 外键错误，且留下悬空对阵）
    from app.models.match import MatchRound
    db.query(MatchRound).filter(
        (MatchRound.team1_id == team_id)
        | (MatchRound.team2_id == team_id)
        | (MatchRound.winner_id == team_id)
    ).delete()
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

    with _team_transaction(db, app.team_id) as team:
        app = lock_row(db, TeamApplication, application_id)
        if not app:
            raise HTTPException(status_code=404, detail="申请不存在")
        if team.captain_id != captain_id:
            raise HTTPException(status_code=403, detail="只有队长才能审核申请")
        if app.status != ApplicationStatus.PENDING:
            raise HTTPException(status_code=400, detail="该申请已处理，请重新提交申请")
        _add_member(db, app.team_id, app.user_id)
        app.status = ApplicationStatus.APPROVED


def reject_application(db: Session, application_id: int, captain_id: int) -> None:
    """队长拒绝申请"""
    app = db.query(TeamApplication).filter(TeamApplication.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="申请不存在")

    with _team_transaction(db, app.team_id) as team:
        app = lock_row(db, TeamApplication, application_id)
        if not app:
            raise HTTPException(status_code=404, detail="申请不存在")
        if team.captain_id != captain_id:
            raise HTTPException(status_code=403, detail="只有队长才能审核申请")
        if app.status != ApplicationStatus.PENDING:
            raise HTTPException(status_code=400, detail="该申请已处理")
        app.status = ApplicationStatus.REJECTED


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
    with _team_transaction(db, inv.team_id):
        inv = lock_row(db, TeamInvitation, invitation_id)
        if not inv:
            raise HTTPException(status_code=404, detail="邀请不存在")
        if inv.user_id != user_id:
            raise HTTPException(status_code=403, detail="这不是发给你的邀请")
        if inv.status != ApplicationStatus.PENDING:
            raise HTTPException(status_code=400, detail="该邀请已处理，请让队长重新邀请")
        _add_member(db, inv.team_id, user_id)
        inv.status = ApplicationStatus.APPROVED


def reject_invitation(db: Session, invitation_id: int, user_id: int) -> None:
    """被邀请人拒绝入队"""
    inv = db.query(TeamInvitation).filter(TeamInvitation.id == invitation_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="邀请不存在")
    with _team_transaction(db, inv.team_id):
        inv = lock_row(db, TeamInvitation, invitation_id)
        if not inv:
            raise HTTPException(status_code=404, detail="邀请不存在")
        if inv.user_id != user_id:
            raise HTTPException(status_code=403, detail="这不是发给你的邀请")
        if inv.status != ApplicationStatus.PENDING:
            raise HTTPException(status_code=400, detail="该邀请已处理")
        inv.status = ApplicationStatus.REJECTED
