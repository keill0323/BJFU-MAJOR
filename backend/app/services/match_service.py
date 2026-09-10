"""赛事业务逻辑

Author: keill
Since: 2026-07-23
"""

import json
from itertools import combinations
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.match import Match, MatchRound, MatchStatus, RoundStatus, StageStatus, TeamProgress, Registration, RegistrationStatus, StageWindow
from app.models.team import Team
from app.models.user import User
from app.services.locking import lock_row


_REGISTRATION_TIMEZONE = timezone(timedelta(hours=8))


def _registration_local_time(value: Optional[datetime]) -> Optional[datetime]:
    """报名时间以北京时间 naive datetime 存储；带时区输入先转换，不能直接丢弃偏移。"""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(_REGISTRATION_TIMEZONE).replace(tzinfo=None)


def _registration_now() -> datetime:
    """不依赖服务器操作系统的时区配置。"""
    return datetime.now(_REGISTRATION_TIMEZONE).replace(tzinfo=None)


def _normalize_registration_window(register_start, register_end):
    register_start = _registration_local_time(register_start)
    register_end = _registration_local_time(register_end)
    if register_start is not None and register_end is not None and register_start >= register_end:
        raise HTTPException(status_code=400, detail="报名开始时间必须早于截止时间")
    return register_start, register_end


def _lock_match(db: Session, match_id: int) -> Match:
    match = lock_row(db, Match, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="赛事不存在")
    return match


def is_roster_locked(db: Session, match_id: int, *, for_update: bool = False) -> bool:
    """是否已经开始编排参赛名单；供详情只读展示，事务校验可使用当前读。"""
    progresses = db.query(TeamProgress.id).filter(
        TeamProgress.match_id == match_id,
        or_(
            TeamProgress.seed > 0,
            and_(TeamProgress.group_name.isnot(None), TeamProgress.group_name != ""),
            TeamProgress.stage.in_([StageStatus.LEGEND, StageStatus.PLAYOFF, StageStatus.ELIMINATED]),
        ),
    )
    rounds = db.query(MatchRound.id).filter(MatchRound.match_id == match_id)
    if for_update:
        progresses = progresses.with_for_update()
        rounds = rounds.with_for_update()
    return progresses.first() is not None or rounds.first() is not None


def _ensure_roster_open(db: Session, match_id: int) -> None:
    """调用方先持有赛事行锁，避免检查完成后与首次分种子交错。"""
    if is_roster_locked(db, match_id, for_update=True):
        raise HTTPException(status_code=400, detail="赛事已开始编排，参赛名单已锁定，不能新增队伍或通过新的队伍报名")


def _ensure_can_reseed(db: Session, match_id: int) -> None:
    """对阵生成后，种子和分组成为赛程的一部分，不能单独重置。"""
    rounds = db.query(MatchRound).filter(MatchRound.match_id == match_id).with_for_update().first()
    progresses = db.query(TeamProgress).filter(TeamProgress.match_id == match_id).with_for_update().all()
    if rounds or any(p.stage in (StageStatus.PLAYOFF, StageStatus.ELIMINATED)
                     or (p.stage == StageStatus.LEGEND and p.group_name) for p in progresses):
        raise HTTPException(status_code=400, detail="已生成对阵或完成阶段晋级，不能重新分配种子或分组")


def _ensure_group_finished(db: Session, match_id: int, group_name: str, team_ids: list) -> None:
    """循环赛必须完整生成，并录完所有场次，才可以根据结果晋级。"""
    rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id, MatchRound.group_name == group_name,
    ).populate_existing().with_for_update().all()
    expected_pairs = {frozenset(pair) for pair in combinations(team_ids, 2)}
    played_pairs = {frozenset((r.team1_id, r.team2_id)) for r in rounds}
    if not expected_pairs or not expected_pairs.issubset(played_pairs):
        raise HTTPException(status_code=400, detail=f"「{group_name}」循环赛对阵尚未完整生成")
    unfinished = sum(r.status != RoundStatus.FINISHED for r in rounds)
    if unfinished:
        raise HTTPException(status_code=400, detail=f"「{group_name}」还有 {unfinished} 场未结束，请先录入全部比分")


def _ensure_result_editable(db: Session, match_round: MatchRound) -> None:
    """晋级后锁定前序结果，避免修改胜者后留下不一致的后续对阵。"""
    # 未录分场次尚未用于晋级；无组别的手动对阵不参与阶段排名。
    if match_round.status != RoundStatus.FINISHED or not match_round.group_name:
        return
    if match_round.group_name == "淘汰赛":
        rounds = db.query(MatchRound).filter(
            MatchRound.match_id == match_round.match_id,
            MatchRound.group_name == "淘汰赛",
        ).order_by(MatchRound.round_number, MatchRound.id).with_for_update().all()
        index = next(i for i, r in enumerate(rounds) if r.id == match_round.id)
        advanced = (index < 2 and len(rounds) > 2) or (2 <= index < 4 and len(rounds) > 4)
    else:
        progresses = db.query(TeamProgress).filter(
            TeamProgress.match_id == match_round.match_id,
            TeamProgress.team_id.in_([match_round.team1_id, match_round.team2_id]),
        ).populate_existing().with_for_update().all()
        expected_stage = StageStatus.LEGEND if match_round.group_name in ("上区", "下区") else StageStatus.CHALLENGER
        advanced = any(p.stage != expected_stage or p.group_name != match_round.group_name for p in progresses)
    if advanced:
        raise HTTPException(status_code=400, detail="该场比赛已用于后续阶段晋级，不能直接修改比分")


def create_match(
    db: Session,
    name: str,
    description: Optional[str] = None,
    max_teams: int = 16,
    team_size: int = 5,
    match_type: str = "major",
    register_start: Optional[datetime] = None,
    register_end: Optional[datetime] = None,
    match_start: Optional[datetime] = None,
) -> Match:
    """创建赛事"""
    register_start, register_end = _normalize_registration_window(register_start, register_end)
    match = Match(
        name=name,
        description=description,
        max_teams=max_teams,
        team_size=team_size,
        match_type=match_type,
        register_start=register_start,
        register_end=register_end,
        match_start=match_start,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


def update_registration_window(db: Session, match_id: int,
                               register_start: Optional[datetime],
                               register_end: Optional[datetime]) -> Match:
    """设置报名时间限制；仅报名中且处于所设时段的赛事允许报名。"""
    match = _lock_match(db, match_id)
    register_start, register_end = _normalize_registration_window(register_start, register_end)
    match.register_start = register_start
    match.register_end = register_end
    db.commit()
    db.refresh(match)
    return match


def get_match_by_id(db: Session, match_id: int) -> Optional[Match]:
    """根据ID获取赛事"""
    return db.query(Match).filter(Match.id == match_id).first()

def get_all_matches(db: Session) -> list:
    """获取所有赛事（含已报名队伍数 registered_count）"""
    matches = db.query(Match).order_by(Match.created_at.desc()).all()
    result = []
    for m in matches:
        reg_count = db.query(Registration.team_id).filter(
            Registration.match_id == m.id,
            Registration.team_id.isnot(None),
        ).distinct().count()
        result.append({
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "max_teams": m.max_teams,
            "team_size": m.team_size,
            "match_type": m.match_type or "major",
            "status": m.status.value if hasattr(m.status, "value") else m.status,
            "register_start": m.register_start,
            "register_end": m.register_end,
            "match_start": m.match_start,
            "created_at": m.created_at,
            "registered_count": reg_count,
        })
    return result


def search_matches(db: Session, keyword: str) -> list:
    """根据名称模糊查询赛事（含已报名队伍数）"""
    matches = db.query(Match).filter(Match.name.contains(keyword)).all()
    result = []
    for m in matches:
        reg_count = db.query(Registration.team_id).filter(
            Registration.match_id == m.id,
            Registration.team_id.isnot(None),
        ).distinct().count()
        result.append({
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "max_teams": m.max_teams,
            "team_size": m.team_size,
            "match_type": m.match_type or "major",
            "status": m.status.value if hasattr(m.status, "value") else m.status,
            "register_start": m.register_start,
            "register_end": m.register_end,
            "match_start": m.match_start,
            "created_at": m.created_at,
            "registered_count": reg_count,
        })
    return result


def update_match_status(db: Session, match_id: int, status: MatchStatus) -> Optional[Match]:
    """更新赛事状态"""
    match = get_match_by_id(db, match_id)
    if match:
        match.status = status
        db.commit()
        db.refresh(match)
    return match


def add_round(db: Session, match_id: int, round_number: int,
              team1_id: int, team2_id: Optional[int] = None,
              group_name: Optional[str] = None, *, commit: bool = True) -> MatchRound:
    """手动添加对阵（校验参赛队伍已报名该赛事，防止对阵混入未报名队伍）"""
    _lock_match(db, match_id)
    for tid in (team1_id, team2_id):
        if tid is None:
            continue
        reg = db.query(Registration).filter(
            Registration.match_id == match_id,
            Registration.team_id == tid,
        ).with_for_update().first()
        if not reg:
            raise HTTPException(status_code=400, detail=f"队伍 {tid} 未报名该赛事，无法添加对阵")
    match_round = MatchRound(
        match_id=match_id,
        round_number=round_number,
        team1_id=team1_id,
        team2_id=team2_id,
        group_name=group_name,
    )
    db.add(match_round)
    db.flush()
    if commit:
        db.commit()
        db.refresh(match_round)
    return match_round


def update_round_result(db: Session, round_id: int,
                        team1_score: int, team2_score: int,
                        winner_id: int = None,
                        bo3_scores: Optional[list] = None) -> Optional[MatchRound]:
    """更新对阵结果（比分必填；未指定胜者时按比分自动判定，支持平局）

    淘汰赛为 BO3：传 bo3_scores（三局小分数组 [{t1,t2},...]），
    总比分（team1_score/team2_score）与胜者按小分局数自动判定。
    """
    match_round = db.query(MatchRound).filter(MatchRound.id == round_id).first()
    if not match_round:
        return None
    _lock_match(db, match_round.match_id)
    match_round = lock_row(db, MatchRound, round_id)
    _ensure_result_editable(db, match_round)
    was_finished = match_round.status == RoundStatus.FINISHED
    if team1_score is None or team2_score is None:
        raise HTTPException(status_code=400, detail="请填写两队比分")

    if bo3_scores is not None:
        # ===== BO3（淘汰赛）=====
        if match_round.group_name != "淘汰赛":
            raise HTTPException(status_code=400, detail="BO3 比分仅用于淘汰赛对阵")
        try:
            games = json.loads(bo3_scores) if isinstance(bo3_scores, str) else list(bo3_scores)
        except Exception:
            raise HTTPException(status_code=400, detail="BO3 比分格式错误")
        if not games or len(games) not in (2, 3):
            raise HTTPException(status_code=400, detail="BO3 需填写 2 或 3 局小分")
        s1 = s2 = 0
        for g in games:
            try:
                t1 = int(g.get("t1", 0))
                t2 = int(g.get("t2", 0))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="BO3 小分必须为数字")
            if t1 < 0 or t2 < 0:
                raise HTTPException(status_code=400, detail="BO3 小分不能为负数")
            if t1 > t2:
                s1 += 1
            elif t2 > t1:
                s2 += 1
        if s1 == s2 or max(s1, s2) != 2:
            raise HTTPException(status_code=400, detail="BO3 总比分需为 2-0 或 2-1")
        if int(team1_score) != s1 or int(team2_score) != s2:
            raise HTTPException(status_code=400, detail="总比分与小分局数不一致")
        match_round.bo3_scores = json.dumps(games, ensure_ascii=False)
        match_round.team1_score = s1
        match_round.team2_score = s2
        # 关键：把判定的胜者写入（此前漏写导致 winner 为 None）
        match_round.winner_id = match_round.team1_id if s1 > s2 else match_round.team2_id
    else:
        # ===== 单局（小组赛/附加赛/传奇组循环赛）=====
        if team1_score < 0 or team2_score < 0:
            raise HTTPException(status_code=400, detail="比分不能为负数")
        # 未指定胜者：按比分自动判定
        if winner_id is None:
            if team1_score > team2_score:
                winner_id = match_round.team1_id
            elif team2_score > team1_score:
                winner_id = match_round.team2_id
        if winner_id is not None and winner_id not in (match_round.team1_id, match_round.team2_id):
            raise HTTPException(status_code=400, detail="胜者必须是参赛队伍之一")
        match_round.team1_score = team1_score
        match_round.team2_score = team2_score
        match_round.winner_id = winner_id
        match_round.bo3_scores = None

    match_round.status = RoundStatus.FINISHED
    try:
        db.flush()
        if match_round.group_name == "淘汰赛":
            from app.services.hall_service import sync_champion_snapshot
            sync_champion_snapshot(db, match_round.match_id,
                                   source="backfill" if was_finished else "final_result")
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(match_round)
    # 返回 dict（含 BO3 小分解析），不改动实例属性避免污染 session
    return _round_to_dict(db, match_round)


def _round_to_dict(db: Session, r: MatchRound) -> dict:
    """将对阵 ORM 转 dict（含队名、BO3 小分、时间协商状态），不改动实例属性"""
    from app.models.team import Team
    t1 = db.query(Team).filter(Team.id == r.team1_id).first() if r.team1_id else None
    t2 = db.query(Team).filter(Team.id == r.team2_id).first() if r.team2_id else None
    # 所属阶段的时间窗口（用于展示限定时段）
    window = None
    if r.group_name:
        window = db.query(StageWindow).filter(
            StageWindow.match_id == r.match_id,
            StageWindow.group_name == r.group_name,
        ).first()
    if r.scheduled_time:
        schedule_status = "confirmed" if (r.team1_confirmed and r.team2_confirmed) else "pending"
    else:
        schedule_status = "unconfirmed"
    return {
        "id": r.id,
        "match_id": r.match_id,
        "round_number": r.round_number,
        "team1_id": r.team1_id,
        "team2_id": r.team2_id,
        "team1_name": t1.name if t1 else None,
        "team2_name": t2.name if t2 else None,
        "team1_score": r.team1_score,
        "team2_score": r.team2_score,
        "winner_id": r.winner_id,
        "status": r.status.value if hasattr(r.status, "value") else r.status,
        "group_name": r.group_name,
        "scheduled_time": r.scheduled_time,
        "bo3_scores": json.loads(r.bo3_scores) if r.bo3_scores else None,
        "team1_confirmed": bool(r.team1_confirmed),
        "team2_confirmed": bool(r.team2_confirmed),
        "window_start": window.window_start if window else None,
        "window_end": window.window_end if window else None,
        "schedule_status": schedule_status,
    }


def get_rounds_by_match(db: Session, match_id: int) -> list:
    """获取某赛事所有对阵,按轮次排序（返回 dict 列表）"""
    rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number).all()
    return [_round_to_dict(db, r) for r in rounds]


def _stage_groups(stage: Optional[str], group_name: Optional[str]) -> list:
    """根据队伍当前阶段返回该阶段对阵的组名范围（用于统计胜场）"""
    if stage == "legend":
        return ["上区", "下区"]
    if stage == "playoff":
        return ["淘汰赛"]
    if stage == "eliminated":
        # 已淘汰：按它最后一轮所在组统计（淘汰赛/上下区/小组）
        if group_name in ("上区", "下区"):
            return ["上区", "下区"]
        if group_name == "淘汰赛":
            return ["淘汰赛"]
    return ["A", "B", "C", "D", "E", "F", "附加赛"]


def _team_record(db: Session, match_id: int, team_id: int, groups: list) -> tuple:
    """统计某队在某阶段范围的胜场/负场/净胜分"""
    rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name.in_(groups),
        MatchRound.status == RoundStatus.FINISHED,
    ).all()
    wins = 0
    losses = 0
    diff = 0
    for r in rounds:
        if r.team1_id == team_id:
            diff += (r.team1_score or 0) - (r.team2_score or 0)
        elif r.team2_id == team_id:
            diff += (r.team2_score or 0) - (r.team1_score or 0)
        if r.winner_id == team_id:
            wins += 1
        elif r.winner_id is not None and team_id in (r.team1_id, r.team2_id) and r.winner_id != team_id:
            losses += 1
    return wins, losses, diff


def get_match_admin_detail(db: Session, match_id: int) -> Optional[dict]:
    """管理后台赛事详情：赛事信息 + 报名队伍(含进度) + 对阵(含队名)"""
    from app.models.team import Team, TeamMember
    from app.models.user import User
    from app.services.team_service import get_team_registration_status
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        return None

    team_ids = db.query(Registration.team_id).filter(
        Registration.match_id == match_id,
        Registration.team_id.isnot(None),
    ).distinct().all()
    teams = []
    for (team_id,) in team_ids:
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team:
            continue
        progress = db.query(TeamProgress).filter(
            TeamProgress.match_id == match_id,
            TeamProgress.team_id == team.id,
        ).first()
        captain = db.query(User).filter(User.id == team.captain_id).first()
        member_count = db.query(TeamMember).filter(TeamMember.team_id == team.id).count()
        stage = progress.stage.value if progress and hasattr(progress.stage, "value") else (progress.stage if progress else None)
        wins, losses, diff = _team_record(db, match_id, team.id, _stage_groups(stage, progress.group_name if progress else None))
        teams.append({
            "team_id": team.id,
            "team_name": team.name,
            "rating": team.rating or 0,
            "registration_status": get_team_registration_status(db, match_id, team_id).value,
            "stage": stage,
            "group_name": progress.group_name if progress else None,
            "seed": progress.seed if progress else 0,
            "captain_name": captain.nickname if captain else None,
            "member_count": member_count,
            "wins": wins,
            "losses": losses,
            "diff": diff,
        })

    # 对阵（含队名与 BO3 小分）
    rounds = get_rounds_by_match(db, match_id)
    return {"match": match, "teams": teams, "rounds": rounds}


def auto_assign_seeds(db: Session, match_id: int) -> list:
    """全局排位：按rating前4直升传奇组，其余为挑战者组（幂等，可重复执行）

    若赛事已进入后续阶段（已有晋级/分区的传奇组队伍），拒绝重排避免破坏数据。
    """
    from app.models.team import Team
    _lock_match(db, match_id)
    _ensure_can_reseed(db, match_id)
    # 全部队伍按 rating 排名，前4直升传奇组，其余挑战者（重置小组名）
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id
    ).join(Team).order_by(Team.rating.desc(), Team.id).populate_existing().with_for_update().all()
    for i, progress in enumerate(progresses, 1):
        progress.seed = i
        progress.stage = StageStatus.CHALLENGER
        progress.group_name = None
        if i <= 4:
            progress.stage = StageStatus.LEGEND  # 前四直升传奇组，不额外做种子标记

    db.commit()
    return progresses


def _validate_group_names(group_names: list) -> None:
    """两种编排入口使用相同的组名与赛制约束。"""
    if not group_names:
        raise HTTPException(status_code=400, detail="请至少提供一个小组名（如 A/B/C）")
    if any(not isinstance(name, str) or not name.strip() or len(name) > 20
           or name in ("附加赛", "上区", "下区", "淘汰赛") for name in group_names) or len(set(group_names)) != len(group_names):
        raise HTTPException(status_code=400, detail="小组名必须唯一，且不能使用后续阶段名称")
    if len(group_names) not in (3, 4):
        raise HTTPException(status_code=400, detail="当前八队传奇赛制仅支持3或4个挑战者小组（三组设附加赛，四组不设）")


def _assign_challenger_groups(progresses: list, group_names: list) -> None:
    n = len(group_names)
    if len(progresses) < n * 2:
        raise HTTPException(status_code=400, detail=f"{n}个挑战者小组至少需要{n * 2}支挑战者队伍，每组至少2支")
    for i, progress in enumerate(progresses):
        group_idx = i % n
        if (i // n) % 2 == 1:
            group_idx = n - 1 - group_idx
        progress.group_name = group_names[group_idx]


def seed_and_group_teams(db: Session, match_id: int, group_names: list) -> list:
    """在同一赛事锁与事务中排种子、分小组，失败不留下锁名单的种子记录。"""
    try:
        _validate_group_names(group_names)
        _lock_match(db, match_id)
        _ensure_can_reseed(db, match_id)
        progresses = db.query(TeamProgress).filter(
            TeamProgress.match_id == match_id,
        ).join(Team).order_by(Team.rating.desc(), Team.id).populate_existing().with_for_update().all()
        # 先检查全部挑战者人数，再写入种子、直升名额和小组，避免部分编排。
        _assign_challenger_groups(progresses[4:], group_names)
        for seed, progress in enumerate(progresses, 1):
            progress.seed = seed
            progress.stage = StageStatus.LEGEND if seed <= 4 else StageStatus.CHALLENGER
            if seed <= 4:
                progress.group_name = None
        db.commit()
        return progresses
    except Exception:
        db.rollback()
        raise


def auto_group_teams(db: Session, match_id: int, group_names: list) -> list:
    """兼容旧入口：将已分种子的挑战者队伍蛇形分配到各组。"""
    _validate_group_names(group_names)
    _lock_match(db, match_id)
    _ensure_can_reseed(db, match_id)
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.CHALLENGER
    ).join(Team).order_by(Team.rating.desc(), Team.id).populate_existing().with_for_update().all()

    _assign_challenger_groups(progresses, group_names)
    db.commit()
    return progresses


def _ensure_no_group_rounds(db: Session, match_id: int, group_name: str) -> None:
    """幂等保护：该组已生成过对阵时拒绝重复生成，防止重复点击产生重复对阵"""
    existing = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name == group_name,
    ).with_for_update().first()
    if existing:
        raise HTTPException(status_code=400, detail=f"「{group_name}」已生成过对阵，请勿重复生成")


def auto_generate_group_matches(db: Session, match_id: int, group_name: str) -> list:
    """为指定小组生成双循环对阵"""
    _lock_match(db, match_id)
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == group_name
    ).populate_existing().with_for_update().all()
    
    team_ids = [p.team_id for p in progresses]
    if len(team_ids) < 2:
        raise HTTPException(status_code=400, detail=f"「{group_name}」组内队伍不足2支，无法生成对阵")
    # 幂等保护：防止重复点击重复生成对阵
    _ensure_no_group_rounds(db, match_id, group_name)
    rounds = []
    max_round = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number.desc()).first()
    round_num = (max_round.round_number + 1) if max_round else 1

    for i in range(len(team_ids)):
        for j in range(i + 1, len(team_ids)):
            rounds.append(add_round(db, match_id, round_num, team_ids[i], team_ids[j], group_name, commit=False))
            round_num += 1
            rounds.append(add_round(db, match_id, round_num, team_ids[j], team_ids[i], group_name, commit=False))
            round_num += 1

    db.commit()
    return rounds


def _check_registration_time(match: Optional[Match]) -> None:
    """个人和队伍共用状态与北京时间校验；设置时段不自动开启报名。"""
    if not match:
        raise HTTPException(status_code=404, detail="赛事不存在")
    if match.status != MatchStatus.REGISTERING:
        raise HTTPException(status_code=400, detail="该赛事当前不在报名阶段，无法报名")
    now = _registration_now()
    if match.register_start and now < _registration_local_time(match.register_start):
        raise HTTPException(status_code=400, detail="报名尚未开始")
    if match.register_end and now > _registration_local_time(match.register_end):
        raise HTTPException(status_code=400, detail="报名已截止")


def _check_match_registerable(db: Session, match: Optional[Match]) -> None:
    """队伍报名前置校验：赛事存在 + 报名中 + 报名时间窗 + 容量未满。"""
    _check_registration_time(match)
    # 容量校验：已报名队伍数（去重，排除已驳回）达到上限则拒绝
    if match.max_teams:
        team_ids = db.query(Registration.team_id).filter(
            Registration.match_id == match.id,
            Registration.team_id.isnot(None),
            Registration.status != RegistrationStatus.REJECTED,
        ).with_for_update().all()
        count = len({team_id for (team_id,) in team_ids})
        if count >= match.max_teams:
            raise HTTPException(status_code=400, detail=f"报名队伍已满（{match.max_teams}/{match.max_teams}），无法报名")


def _validate_team_registration(db: Session, match: Match, team_id: int) -> list:
    """报名和审批共用当前阵容资格检查；调用方须先锁定赛事和队伍。"""
    from app.models.team import TeamMember, TeamStatus
    from app.services.auth_service import effective_identity
    team = lock_row(db, Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    if team.status != TeamStatus.APPROVED:
        raise HTTPException(status_code=400, detail="队伍尚未通过审核")
    members = db.query(TeamMember).filter(TeamMember.team_id == team_id).populate_existing().with_for_update().all()
    if not members:
        raise HTTPException(status_code=400, detail="队伍没有成员，无法报名")
    users = {u.id: u for u in db.query(User).filter(User.id.in_([m.user_id for m in members]))
             .populate_existing().with_for_update().all()}

    # 校验：所有队员（含队长）必须完成学籍认证
    unverified = []
    for member in members:
        user = users.get(member.user_id)
        if not user or not user.is_verified:
            name = (user.nickname or user.game_id) if user else f"用户{member.user_id}"
            unverified.append(name)
    if unverified:
        raise HTTPException(status_code=400, detail=f"以下队员未完成学籍认证，无法报名：{'、'.join(unverified)}")

    # 校验：所有队员（含队长）必须完成段位认证（防炸鱼）
    no_rank = []
    for member in members:
        user = users.get(member.user_id)
        if not user or not user.rank:
            name = (user.nickname or user.game_id) if user else f"用户{member.user_id}"
            no_rank.append(name)
    if no_rank:
        raise HTTPException(
            status_code=400,
            detail=f"以下队员未完成段位认证（上传段位截图），无法报名：{'、'.join(no_rank)}"
        )

    # 队伍人数上限校验：不能超过该赛事的每队人数（防止超编队伍整队报名）
    if match.team_size and len(members) > match.team_size:
        raise HTTPException(
            status_code=400,
            detail=f"队伍当前 {len(members)} 人，超过该赛事每队 {match.team_size} 人上限"
        )

    # 新生赛校验：队伍至少 3 名新生（现场动态算，跨年自动正确）
    if (match.match_type or "major") == "freshman":
        new_count = 0
        for member in members:
            user = users.get(member.user_id)
            if user and effective_identity(user) == "new_student":
                new_count += 1
        if new_count < 3:
            raise HTTPException(status_code=400, detail=f"新生赛要求队伍至少 3 名新生，当前仅 {new_count} 名")
    return members


def register_team(db: Session, match_id: int, team_id: int) -> list:
    """整队报名统一待审，个人报名升级也不能替代队伍参赛审核。"""
    from app.services import team_service
    match = _lock_match(db, match_id)
    _ensure_roster_open(db, match_id)
    members = _validate_team_registration(db, match, team_id)
    _check_match_registerable(db, match)
    team_registered = db.query(Registration).filter(
        Registration.match_id == match_id, Registration.team_id == team_id,
    ).with_for_update().first()
    if team_registered:
        raise HTTPException(status_code=400, detail="该队伍已报名此赛事，请勿重复报名")

    registrations = []
    for member in members:
        # 该队员是否已报名此赛事（个人或其它队伍）
        existing = db.query(Registration).filter(
            Registration.match_id == match_id,
            Registration.user_id == member.user_id,
        ).populate_existing().with_for_update().first()
        if existing:
            if existing.team_id not in (None, team_id):
                raise HTTPException(status_code=400, detail="队员在该赛事中已有其他队伍报名，请先处理原报名")
            reg = existing
            reg.team_id = team_id
            reg.status = RegistrationStatus.PENDING
        else:
            reg = Registration(match_id=match_id, user_id=member.user_id, team_id=team_id,
                               status=RegistrationStatus.PENDING)
            db.add(reg)
        registrations.append(reg)
    try:
        team_service.recalc_team_rating(db, team_id, commit=False)
        db.commit()
    except IntegrityError:
        # 并发重复报名触发 (match_id, user_id) 唯一约束
        db.rollback()
        raise HTTPException(status_code=400, detail="该队伍已报名此赛事，请勿重复报名")
    for reg in registrations:
        db.refresh(reg)
    return registrations


def register_user(db: Session, match_id: int, user_id: int) -> Registration:
    """个人报名

    个人报名只需「学籍认证 + 段位认证」两个判断，直接通过（APPROVED），
    无需管理员额外审核；报名即出现在人才市场，方便队长查看并邀请入队。
    """
    from app.models.team import TeamMember

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 已在队伍中的用户不能个人报名（应由队长发起队伍报名）
    in_team = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
    if in_team:
        raise HTTPException(status_code=400, detail="你已在队伍中，请由队长发起队伍报名")

    # 赛事状态 / 报名时间窗校验（个人报名不占队伍容量名额）
    match = _lock_match(db, match_id)
    _check_registration_time(match)

    # 个人报名的两个判断：学籍认证 + 段位认证
    if not user.is_verified:
        raise HTTPException(status_code=400, detail="未完成学籍认证，无法报名")
    if not user.rank:
        raise HTTPException(status_code=400, detail="未完成段位认证（上传段位截图），无法报名")

    # 检查是否已报名该赛事（无论个人还是随队伍）
    existing = db.query(Registration).filter(
        Registration.match_id == match_id,
        Registration.user_id == user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="你已报名该赛事，请勿重复报名")
    reg = Registration(
        match_id=match_id,
        user_id=user_id,
        status=RegistrationStatus.APPROVED,  # 个人报名直接通过
    )
    db.add(reg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="你已报名该赛事，请勿重复报名")
    db.refresh(reg)
    return reg


def ensure_team_progress(db: Session, match_id: int, team_id: int, *, commit: bool = True) -> TeamProgress:
    """确保已有报名审批的队伍拥有待分组进度；编排后不能新增。"""
    _lock_match(db, match_id)
    progress = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.team_id == team_id,
    ).populate_existing().with_for_update().first()
    if not progress:
        _ensure_roster_open(db, match_id)
        progress = TeamProgress(
            match_id=match_id,
            team_id=team_id,
            stage=StageStatus.CHALLENGER,
        )
        db.add(progress)
        db.flush()
        if commit:
            db.commit()
            db.refresh(progress)
    return progress


def approve_registration(db: Session, reg_id: int) -> Optional[Registration]:
    """审核通过报名（队伍报名通过时自动创建赛事进度，供自动编排使用）"""
    reg = db.query(Registration).filter(Registration.id == reg_id).first()
    if reg:
        _lock_match(db, reg.match_id)
        reg = lock_row(db, Registration, reg_id)
        if not reg:
            return None
        if reg.team_id:
            approve_team_registration(db, reg.match_id, reg.team_id)
            db.refresh(reg)
            return reg
        user = db.query(User).filter(User.id == reg.user_id).first()
        if not user or not user.is_verified or not user.rank:
            raise HTTPException(status_code=400, detail="该用户尚未完成学籍和段位认证")
        reg.status = RegistrationStatus.APPROVED
        db.commit()
        db.refresh(reg)
    return reg


def approve_team_registration(db: Session, match_id: int, team_id: int) -> dict:
    """审核通过某队伍在某赛事的全部报名记录（队员逐条 pending 记录一次性通过）

    报名审核链修复：此前队伍报名后每名队员各一条 pending 记录，
    且前端无审核入口导致队伍永远停留在 pending、自动编排被跳过。
    本接口把该队伍在该赛事的所有 pending 记录批量置为 approved 并建进度。
    """
    match = _lock_match(db, match_id)
    regs = db.query(Registration).filter(
        Registration.match_id == match_id,
        Registration.team_id == team_id,
    ).populate_existing().with_for_update().all()
    if not regs:
        raise HTTPException(status_code=404, detail="该队伍未报名此赛事")
    progress = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id, TeamProgress.team_id == team_id,
    ).populate_existing().with_for_update().first()
    # 编排后的重复审批只能读取既有完整结果，不重新验证或重置历史阶段。
    if is_roster_locked(db, match_id, for_update=True):
        if progress and all(reg.status == RegistrationStatus.APPROVED for reg in regs):
            db.commit()
            return {"team_id": team_id, "approved": 0}
        raise HTTPException(status_code=400, detail="赛事已开始编排，参赛名单已锁定，不能新增队伍或通过新的队伍报名")
    members = _validate_team_registration(db, match, team_id)
    if {r.user_id for r in regs} != {m.user_id for m in members}:
        raise HTTPException(status_code=400, detail="队伍阵容与报名记录不一致，请先处理报名记录")
    count = 0
    for reg in regs:
        if reg.status != RegistrationStatus.APPROVED:
            reg.status = RegistrationStatus.APPROVED
            count += 1
    ensure_team_progress(db, match_id, team_id, commit=False)
    db.commit()
    return {"team_id": team_id, "approved": count}


def get_my_registration(db: Session, match_id: int, user_id: int) -> Optional[Registration]:
    """查询当前用户在某赛事的报名记录（未报名返回 None）"""
    return db.query(Registration).filter(
        Registration.match_id == match_id,
        Registration.user_id == user_id,
    ).first()


def auto_generate_single_round_matches(db: Session, match_id: int, group_name: str) -> list:
    """为指定小组生成单循环对阵"""
    _lock_match(db, match_id)
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == group_name
    ).populate_existing().with_for_update().all()

    team_ids = [p.team_id for p in progresses]
    if len(team_ids) < 2:
        raise HTTPException(status_code=400, detail=f"「{group_name}」组内队伍不足2支，无法生成对阵")
    # 幂等保护：防止重复点击重复生成对阵
    _ensure_no_group_rounds(db, match_id, group_name)
    rounds = []
    max_round = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number.desc()).first()
    round_num = (max_round.round_number + 1) if max_round else 1

    for i in range(len(team_ids)):
        for j in range(i + 1, len(team_ids)):
            rounds.append(add_round(db, match_id, round_num, team_ids[i], team_ids[j], group_name, commit=False))
            round_num += 1

    db.commit()
    return rounds


def _rank_group_teams(db: Session, match_id: int, group_name: str, progresses: list) -> list:
    """按小组内已结束对阵统计排名：胜场 → 净胜分 → rating"""
    from app.models.team import Team
    stats = {p.team_id: {"wins": 0, "diff": 0, "progress": p} for p in progresses}
    rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name == group_name,
        MatchRound.status == RoundStatus.FINISHED
    ).populate_existing().with_for_update().all()
    for r in rounds:
        # 对晋级队伍排名时，仍保留它与未晋级队伍的比赛成绩。
        if r.team1_id in stats:
            stats[r.team1_id]["diff"] += r.team1_score - r.team2_score
            if r.winner_id == r.team1_id:
                stats[r.team1_id]["wins"] += 1
        if r.team2_id in stats:
            stats[r.team2_id]["diff"] += r.team2_score - r.team1_score
            if r.winner_id == r.team2_id:
                stats[r.team2_id]["wins"] += 1
    ratings = {t.id: t.rating for t in db.query(Team).filter(Team.id.in_(list(stats))).all()}
    ordered = sorted(
        stats.values(),
        key=lambda s: (-s["wins"], -s["diff"], -ratings.get(s["progress"].team_id, 0))
    )
    return [s["progress"] for s in ordered]


def finish_group_stage(db: Session, match_id: int) -> dict:
    """每组冠军晋级传奇；三组时亚军进附加赛，四组时非冠军直接淘汰。"""
    _lock_match(db, match_id)
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.CHALLENGER
    ).populate_existing().with_for_update().all()
    groups = {}
    for p in progresses:
        groups.setdefault(p.group_name, []).append(p)

    if not groups or None in groups or "附加赛" in groups:
        raise HTTPException(status_code=400, detail="小组赛尚未分组或已经结束")
    group_count = len(groups)
    if group_count not in (3, 4):
        raise HTTPException(status_code=400, detail="当前八队传奇赛制仅支持3或4个挑战者小组（三组设附加赛，四组不设）")
    has_playoff = group_count == 3
    # 先检查所有组，再修改任何进度，避免部分组晋级后才报错。
    for gname, plist in groups.items():
        _ensure_group_finished(db, match_id, gname, [p.team_id for p in plist])

    winners, runner_ups = [], []
    for gname, plist in groups.items():
        if not gname or gname == "附加赛":
            continue
        ranked = _rank_group_teams(db, match_id, gname, plist)
        if not ranked:
            continue
        ranked[0].stage = StageStatus.LEGEND    # 小组第1直接晋级传奇组
        winners.append(ranked[0])
        if has_playoff and len(ranked) >= 2:
            ranked[1].group_name = "附加赛"      # 小组第2进附加赛
            runner_ups.append(ranked[1])
        for p in ranked[2 if has_playoff else 1:]:
            p.stage = StageStatus.ELIMINATED    # 其余淘汰

    # 生成附加赛单循环对阵
    added = 0
    if len(runner_ups) >= 2:
        max_round = db.query(MatchRound).filter(
            MatchRound.match_id == match_id
        ).order_by(MatchRound.round_number.desc()).first()
        round_num = (max_round.round_number + 1) if max_round else 1
        rids = [p.team_id for p in runner_ups]
        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                add_round(db, match_id, round_num, rids[i], rids[j], "附加赛", commit=False)
                round_num += 1
                added += 1

    db.commit()
    return {
        "group_winners": [p.team_id for p in winners],
        "runner_ups": [p.team_id for p in runner_ups],
        "added_rounds": added,
        "group_count": group_count,
        "has_playoff": has_playoff,
    }


def finish_playoff_stage(db: Session, match_id: int) -> dict:
    """三小组赛制结束附加赛：第1晋级传奇组，其余淘汰；四小组不设附加赛。"""
    _lock_match(db, match_id)
    # 原小组赛对阵保留组名，可在队伍晋级改组后继续识别赛制，防止旧四组附加赛额外晋级。
    original_groups = db.query(MatchRound.group_name).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name.notin_(["附加赛", "上区", "下区", "淘汰赛"]),
    ).distinct().all()
    if len(original_groups) == 4:
        raise HTTPException(status_code=400, detail="四个挑战者小组不设附加赛，各组冠军直接晋级传奇组")
    if len(original_groups) != 3:
        raise HTTPException(status_code=400, detail="仅三个挑战者小组的赛制设附加赛，请先完成小组赛")
    playoff_progs = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == "附加赛"
    ).populate_existing().with_for_update().all()
    if not playoff_progs:
        raise HTTPException(status_code=400, detail="当前没有附加赛阶段，请先结束小组赛")
    if any(p.stage != StageStatus.CHALLENGER for p in playoff_progs):
        raise HTTPException(status_code=400, detail="附加赛已经结束，不能重复晋级")
    _ensure_group_finished(db, match_id, "附加赛", [p.team_id for p in playoff_progs])
    ranked = _rank_group_teams(db, match_id, "附加赛", playoff_progs)
    ranked[0].stage = StageStatus.LEGEND       # 附加赛第1晋级传奇组
    for p in ranked[1:]:
        p.stage = StageStatus.ELIMINATED
    db.commit()
    return {
        "playoff_winner": ranked[0].team_id,
        "eliminated": [p.team_id for p in ranked[1:]],
    }


def divide_legend(db: Session, match_id: int) -> dict:
    """传奇组8队分上下区，生成各区单循环对阵（每区4队6场）"""
    from app.models.team import Team
    _lock_match(db, match_id)
    # 幂等保护：已分区并生成过上下区对阵时拒绝重复执行
    existing_zone = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name.in_(["上区", "下区"]),
    ).with_for_update().first()
    if existing_zone:
        raise HTTPException(status_code=400, detail="传奇组已分区并生成对阵，请勿重复执行")
    # 队伍恰好达到8支不代表前序阶段已完成；旧数据可能仍包含待晋级的附加赛队伍，
    # 不能提前分区后再让新晋级队伍无处进入赛程。
    unfinished_challenger = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.CHALLENGER,
    ).populate_existing().with_for_update().first()
    if unfinished_challenger:
        raise HTTPException(status_code=400, detail="挑战者组尚未完成晋级，请先结束小组赛；三组赛制还需完成附加赛")
    legends = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.LEGEND
    ).populate_existing().with_for_update().all()
    if len(legends) != 8:
        raise HTTPException(
            status_code=400,
            detail=f"传奇组当前 {len(legends)} 支（需8支），请先完成挑战者阶段晋级（三组含附加赛，四组冠军直晋）"
        )
    # 晋级的4队补 seed 5-8（按 rating），直升的已为 1-4
    no_seed = [p for p in legends if not p.seed]
    if no_seed:
        ratings = {t.id: t.rating for t in db.query(Team).filter(Team.id.in_([p.team_id for p in no_seed])).all()}
        no_seed.sort(key=lambda p: -ratings.get(p.team_id, 0))
        for i, p in enumerate(no_seed, 5):
            p.seed = i
    # 蛇形分上下区：seed 1,4,5,8 上区；2,3,6,7 下区
    ordered = sorted(legends, key=lambda p: p.seed)
    for i, p in enumerate(ordered):
        p.group_name = "上区" if i in (0, 3, 4, 7) else "下区"
    # 生成上下区单循环
    added = 0
    max_round = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number.desc()).first()
    round_num = (max_round.round_number + 1) if max_round else 1
    for zone in ("上区", "下区"):
        tids = [p.team_id for p in ordered if p.group_name == zone]
        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                add_round(db, match_id, round_num, tids[i], tids[j], zone, commit=False)
                round_num += 1
                added += 1
    db.commit()
    return {"zone_teams": [p.team_id for p in ordered], "added_rounds": added}


def finish_legend_stage(db: Session, match_id: int) -> dict:
    """结束传奇组循环赛：每区前3晋级淘汰赛，第4名淘汰"""
    _lock_match(db, match_id)
    legends = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.LEGEND
    ).populate_existing().with_for_update().all()
    zones = {}
    for p in legends:
        zones.setdefault(p.group_name, []).append(p)
    if set(zones) != {"上区", "下区"} or any(len(plist) != 4 for plist in zones.values()):
        raise HTTPException(status_code=400, detail="传奇组必须先完成上下区分组，每区4队，且不能重复结束")
    for zone, plist in zones.items():
        _ensure_group_finished(db, match_id, zone, [p.team_id for p in plist])
    qualified, eliminated = [], []
    for zone, plist in zones.items():
        if len(plist) < 4:
            raise HTTPException(status_code=400, detail=f"「{zone}」只有 {len(plist)} 队")
        ranked = _rank_group_teams(db, match_id, zone, plist)
        for p in ranked[:3]:
            p.stage = StageStatus.PLAYOFF      # 每区前3晋级淘汰赛
            qualified.append(p)
        for p in ranked[3:]:
            p.stage = StageStatus.ELIMINATED
            eliminated.append(p)
    db.commit()
    return {
        "qualified": [p.team_id for p in qualified],
        "eliminated": [p.team_id for p in eliminated],
    }


def generate_knockout(db: Session, match_id: int) -> dict:
    """生成6强淘汰赛1/4决赛：每区第1轮空，第2 vs 第3（各1场）"""
    _lock_match(db, match_id)
    qualifiers = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.PLAYOFF
    ).populate_existing().with_for_update().all()
    # 幂等保护：已生成过淘汰赛对阵时拒绝重复生成
    _ensure_no_group_rounds(db, match_id, "淘汰赛")
    if len(qualifiers) != 6:
        raise HTTPException(
            status_code=400,
            detail=f"当前淘汰赛队伍 {len(qualifiers)} 支（需6支），请先完成「结束传奇组」"
        )
    z1 = _rank_group_teams(db, match_id, "上区", [p for p in qualifiers if p.group_name == "上区"])
    z2 = _rank_group_teams(db, match_id, "下区", [p for p in qualifiers if p.group_name == "下区"])
    if len(z1) < 3 or len(z2) < 3:
        raise HTTPException(status_code=400, detail="上下区队伍不足3支")
    # 记录名次：上1=1, 下1=2, 上2=3, 下2=4, 上3=5, 下3=6
    seeds = [z1[0], z2[0], z1[1], z2[1], z1[2], z2[2]]
    for i, p in enumerate(seeds, 1):
        p.seed = i
    max_round = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number.desc()).first()
    round_num = (max_round.round_number + 1) if max_round else 1
    # 1/4决赛：上2v上3、下2v下3（顺序固定：第一场上区，第二场下区）
    add_round(db, match_id, round_num, z1[1].team_id, z1[2].team_id, "淘汰赛", commit=False)
    round_num += 1
    add_round(db, match_id, round_num, z2[1].team_id, z2[2].team_id, "淘汰赛", commit=False)
    db.commit()
    return {"knockout_teams": [p.team_id for p in seeds], "added_rounds": 2}


def advance_knockout(db: Session, match_id: int) -> dict:
    """推进淘汰赛：1/4打完→半决赛（交叉），半决赛打完→决赛"""
    _lock_match(db, match_id)
    ko_rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name == "淘汰赛"
    ).order_by(MatchRound.round_number, MatchRound.id).populate_existing().with_for_update().all()
    if not ko_rounds:
        raise HTTPException(status_code=400, detail="还没有淘汰赛对阵，请先「生成淘汰赛」")
    finished = [r for r in ko_rounds if r.status == RoundStatus.FINISHED]
    if len(finished) < len(ko_rounds):
        raise HTTPException(status_code=400, detail="当前淘汰赛轮次尚未打完，请先录入比分")
    # 防御：已打完的对阵缺少胜者时拒绝推进，避免生成空对阵
    no_winner = [r for r in finished if not r.winner_id]
    if no_winner:
        raise HTTPException(status_code=400, detail="有淘汰赛对阵缺少胜者，请重新录入该场比分")
    max_round = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number.desc()).first()
    round_num = (max_round.round_number + 1) if max_round else 1
    added = 0
    if len(ko_rounds) == 2:
        # 1/4打完 → 半决赛（交叉：上1 vs 下区胜者，下1 vs 上区胜者）
        upper_winner = finished[0].winner_id   # 第1场=上区1/4
        lower_winner = finished[1].winner_id   # 第2场=下区1/4
        q = db.query(TeamProgress).filter(
            TeamProgress.match_id == match_id,
            TeamProgress.stage == StageStatus.PLAYOFF
        ).populate_existing().with_for_update().all()
        upper_first = next((p for p in q if p.group_name == "上区" and p.seed == 1), None)
        # 种子规则：上1=1、下1=2、上2=3、下2=4、上3=5、下3=6，故下区第1是 seed=2
        lower_first = next((p for p in q if p.group_name == "下区" and p.seed == 2), None)
        if upper_first is None or lower_first is None:
            raise HTTPException(status_code=400, detail="找不到上下区第1名队伍，请先「生成淘汰赛」")
        add_round(db, match_id, round_num, upper_first.team_id, lower_winner, "淘汰赛", commit=False)
        round_num += 1
        add_round(db, match_id, round_num, lower_first.team_id, upper_winner, "淘汰赛", commit=False)
        round_num += 1
        added = 2
    elif len(ko_rounds) == 4:
        # 半决赛打完 → 决赛
        sf1 = finished[2].winner_id
        sf2 = finished[3].winner_id
        add_round(db, match_id, round_num, sf1, sf2, "淘汰赛", commit=False)
        round_num += 1
        added = 1
    else:
        raise HTTPException(status_code=400, detail="淘汰赛已全部结束")
    db.commit()
    return {"added_rounds": added}


def delete_match(db: Session, match_id: int) -> Optional[Match]:
    """管理员删除比赛，删除比赛及所有关联记录"""
    match = get_match_by_id(db, match_id)
    if not match:
        return None
    # 清理对阵、队伍进度、报名记录、阶段时间窗口（有外键引用，必须先删）
    db.query(MatchRound).filter(MatchRound.match_id == match_id).delete()
    db.query(TeamProgress).filter(TeamProgress.match_id == match_id).delete()
    db.query(Registration).filter(Registration.match_id == match_id).delete()
    db.query(StageWindow).filter(StageWindow.match_id == match_id).delete()
    db.delete(match)
    db.commit()
    return match


def remove_team_from_match(db: Session, match_id: int, team_id: int) -> None:
    """管理员将某队伍从赛事中移除（删除报名/进度/相关对阵）"""
    match = get_match_by_id(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="赛事不存在")
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="队伍不存在")
    # 清理该队在此赛事的报名记录、进度、参与的对阵
    db.query(Registration).filter(
        Registration.match_id == match_id,
        Registration.team_id == team_id,
    ).delete()
    db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.team_id == team_id,
    ).delete()
    db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        (MatchRound.team1_id == team_id) | (MatchRound.team2_id == team_id),
    ).delete()
    db.commit()


# ===== 阶段时间窗口 + 对阵时间协商 =====

def set_stage_window(db: Session, match_id: int, group_name: str,
                     window_start: datetime, window_end: datetime) -> StageWindow:
    """设置/更新某阶段（group_name）的时间窗口（幂等 upsert）

    同一赛事同一阶段只有一个窗口，重复设置会覆盖旧值。
    窗口变更后，落在新窗口之外的已有约定时间会被作废。
    """
    window_start = _strip_tz(window_start)
    window_end = _strip_tz(window_end)
    if window_start >= window_end:
        raise HTTPException(status_code=400, detail="限定时段开始时间必须早于结束时间")
    window = db.query(StageWindow).filter(
        StageWindow.match_id == match_id,
        StageWindow.group_name == group_name,
    ).first()
    if window:
        window.window_start = window_start
        window.window_end = window_end
    else:
        window = StageWindow(
            match_id=match_id,
            group_name=group_name,
            window_start=window_start,
            window_end=window_end,
        )
        db.add(window)
    db.flush()
    # 窗口变更后：作废落在新窗口之外的已有约定时间
    affected_rounds = (
        db.query(MatchRound)
        .filter(
            MatchRound.match_id == match_id,
            MatchRound.group_name == group_name,
            MatchRound.scheduled_time.isnot(None),
        )
        .all()
    )
    for r in affected_rounds:
        st = _strip_tz(r.scheduled_time)
        if st < window_start or st > window_end:
            r.scheduled_time = None
            r.team1_confirmed = False
            r.team2_confirmed = False
    db.commit()
    db.refresh(window)
    return window


def get_stage_windows(db: Session, match_id: int) -> list:
    """查询某赛事所有阶段的时间窗口"""
    return (
        db.query(StageWindow)
        .filter(StageWindow.match_id == match_id)
        .order_by(StageWindow.id.asc())
        .all()
    )


def _strip_tz(dt):
    """去掉时区信息，统一为 naive datetime（防御前后端传入带时区的时间）"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _get_round_window(db: Session, match_round: MatchRound) -> Optional[StageWindow]:
    """取对阵所属阶段的时间窗口，无则返回 None"""
    if not match_round.group_name:
        return None
    return db.query(StageWindow).filter(
        StageWindow.match_id == match_round.match_id,
        StageWindow.group_name == match_round.group_name,
    ).first()


def _get_captain_side(db: Session, match_round: MatchRound, user_id: int) -> Optional[str]:
    """判断 user_id 是对阵中哪一方的队长，返回 'team1'/'team2'，都不是返回 None"""
    for side, team_id in (("team1", match_round.team1_id), ("team2", match_round.team2_id)):
        if not team_id:
            continue
        team = db.query(Team).filter(Team.id == team_id).first()
        if team and team.captain_id == user_id:
            return side
    return None


def schedule_round(db: Session, round_id: int, user_id: int, scheduled_time: datetime) -> dict:
    """队长提交/修改约定比赛时间（己方自动确认，对方重置为待确认）

    约定时间必须落在管理员设定的阶段窗口内。
    """
    scheduled_time = _strip_tz(scheduled_time)
    match_round = db.query(MatchRound).filter(MatchRound.id == round_id).first()
    if not match_round:
        raise HTTPException(status_code=404, detail="对阵不存在")
    # 已开始/已结束的对阵不再协商时间
    if match_round.status != RoundStatus.PENDING:
        raise HTTPException(status_code=400, detail="该对阵已开始或已结束，无法约定比赛时间")
    # 轮空场次不启用时间协商
    if not match_round.team1_id or not match_round.team2_id:
        raise HTTPException(status_code=400, detail="该对阵存在轮空，无需约定比赛时间")
    side = _get_captain_side(db, match_round, user_id)
    if not side:
        raise HTTPException(status_code=403, detail="只有对阵双方的队长才能设定比赛时间")
    # 双方已确认的时间不可单方修改，需先作废再重新约定
    if match_round.team1_confirmed and match_round.team2_confirmed:
        raise HTTPException(status_code=400, detail="比赛时间已确定，如需修改请先作废再重新约定")
    window = _get_round_window(db, match_round)
    if not window:
        raise HTTPException(status_code=400, detail="管理员尚未设置该阶段的时间窗口，无法约定时间")
    if scheduled_time < window.window_start or scheduled_time > window.window_end:
        raise HTTPException(
            status_code=400,
            detail="约定时间必须在限定时段内（%s ~ %s）" % (
                window.window_start.strftime("%m-%d %H:%M"),
                window.window_end.strftime("%m-%d %H:%M"),
            ),
        )
    # 约定时间必须晚于当前时间（防止约定过去的时间）
    if scheduled_time <= datetime.now():
        raise HTTPException(status_code=400, detail="约定时间必须晚于当前时间")
    match_round.scheduled_time = scheduled_time
    if side == "team1":
        match_round.team1_confirmed = True
        match_round.team2_confirmed = False
    else:
        match_round.team2_confirmed = True
        match_round.team1_confirmed = False
    db.commit()
    db.refresh(match_round)
    return _round_to_dict(db, match_round)


def confirm_round_schedule(db: Session, round_id: int, user_id: int,
                           expected_time: Optional[datetime] = None) -> dict:
    """一方队长确认约定时间（己方标记已确认，双方都确认后生效）

    expected_time 为客户端发起确认时所看到的提议时间；
    与数据库当前值不一致说明对方刚修改过，返回 409 提示刷新。
    """
    match_round = db.query(MatchRound).filter(MatchRound.id == round_id).first()
    if not match_round:
        raise HTTPException(status_code=404, detail="对阵不存在")
    if match_round.status != RoundStatus.PENDING:
        raise HTTPException(status_code=400, detail="该对阵已开始或已结束，无法确认比赛时间")
    if not match_round.scheduled_time:
        raise HTTPException(status_code=400, detail="尚未有人提交约定时间")
    if expected_time is not None and _strip_tz(expected_time) != match_round.scheduled_time:
        raise HTTPException(status_code=409, detail="约定时间已被对方修改，请刷新后重新确认")
    side = _get_captain_side(db, match_round, user_id)
    if not side:
        raise HTTPException(status_code=403, detail="只有对阵双方的队长才能确认")
    if side == "team1":
        match_round.team1_confirmed = True
    else:
        match_round.team2_confirmed = True
    db.commit()
    db.refresh(match_round)
    return _round_to_dict(db, match_round)


def reject_round_schedule(db: Session, round_id: int, user_id: int,
                          expected_time: Optional[datetime] = None) -> dict:
    """一方队长拒绝/作废约定时间（清空提议，回到未约定状态）

    expected_time 为客户端发起拒绝时所看到的提议时间；
    与数据库当前值不一致说明对方刚修改过，返回 409 提示刷新。
    """
    match_round = db.query(MatchRound).filter(MatchRound.id == round_id).first()
    if not match_round:
        raise HTTPException(status_code=404, detail="对阵不存在")
    if match_round.status != RoundStatus.PENDING:
        raise HTTPException(status_code=400, detail="该对阵已开始或已结束，无法作废比赛时间")
    if not match_round.scheduled_time:
        raise HTTPException(status_code=400, detail="尚未有人提交约定时间")
    if expected_time is not None and _strip_tz(expected_time) != match_round.scheduled_time:
        raise HTTPException(status_code=409, detail="约定时间已被对方修改，请刷新后再操作")
    side = _get_captain_side(db, match_round, user_id)
    if not side:
        raise HTTPException(status_code=403, detail="只有对阵双方的队长才能拒绝")
    match_round.scheduled_time = None
    match_round.team1_confirmed = False
    match_round.team2_confirmed = False
    db.commit()
    db.refresh(match_round)
    return _round_to_dict(db, match_round)
