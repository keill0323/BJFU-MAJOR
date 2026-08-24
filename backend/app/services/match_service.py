"""赛事业务逻辑

Author: keill
Since: 2026-07-23
"""

import json
from typing import Optional, List
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.match import Match, MatchRound, MatchStatus, RoundStatus, StageStatus, TeamProgress, Registration, RegistrationStatus
from app.models.team import Team
from app.models.user import User


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
              group_name: Optional[str] = None) -> MatchRound:
    """手动添加对阵"""
    match_round = MatchRound(
        match_id=match_id,
        round_number=round_number,
        team1_id=team1_id,
        team2_id=team2_id,
        group_name=group_name,
    )
    db.add(match_round)
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
            t1 = int(g.get("t1", 0))
            t2 = int(g.get("t2", 0))
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
    db.commit()
    db.refresh(match_round)
    # 返回 dict（含 BO3 小分解析），不改动实例属性避免污染 session
    return _round_to_dict(db, match_round)


def _round_to_dict(db: Session, r: MatchRound) -> dict:
    """将对阵 ORM 转 dict（含队名与 BO3 小分解析），不改动实例属性"""
    from app.models.team import Team
    t1 = db.query(Team).filter(Team.id == r.team1_id).first() if r.team1_id else None
    t2 = db.query(Team).filter(Team.id == r.team2_id).first() if r.team2_id else None
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
        reg = db.query(Registration).filter(
            Registration.match_id == match_id,
            Registration.team_id == team_id,
        ).first()
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
            "registration_status": reg.status.value if reg and hasattr(reg.status, "value") else (reg.status if reg else None),
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
    # 保护：存在已晋级/已分区的传奇组队伍时不允许重排
    advanced = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.LEGEND,
        TeamProgress.group_name.isnot(None),
    ).first()
    if advanced:
        raise HTTPException(
            status_code=400,
            detail="赛事已进入后续阶段，不能重新分配种子（请重新创建赛事或重置）"
        )
    # 全部队伍按 rating 排名，前4直升传奇组，其余挑战者（重置小组名）
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id
    ).join(Team).order_by(Team.rating.desc()).all()
    for i, progress in enumerate(progresses, 1):
        progress.seed = i
        progress.stage = StageStatus.CHALLENGER
        progress.group_name = None
        if i <= 4:
            progress.stage = StageStatus.LEGEND  # 前四直升传奇组，不额外做种子标记

    db.commit()
    return progresses


def auto_group_teams(db: Session, match_id: int, group_names: list) -> list:
    """将挑战者组队伍按种子蛇形分配到各组"""
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.CHALLENGER
    ).join(Team).order_by(Team.rating.desc()).all()

    n = len(group_names)
    for i, progress in enumerate(progresses):
        group_idx = i % n
        if (i // n) % 2 == 1:
            group_idx = n - 1 - group_idx
        progress.group_name = group_names[group_idx]

    db.commit()
    return progresses


def auto_generate_group_matches(db: Session, match_id: int, group_name: str) -> list:
    """为指定小组生成双循环对阵"""
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == group_name
    ).all()
    
    team_ids = [p.team_id for p in progresses]
    if len(team_ids) < 2:
        raise HTTPException(status_code=400, detail=f"「{group_name}」组内队伍不足2支，无法生成对阵")
    rounds = []
    max_round = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number.desc()).first()
    round_num = (max_round.round_number + 1) if max_round else 1

    for i in range(len(team_ids)):
        for j in range(i + 1, len(team_ids)):
            rounds.append(add_round(db, match_id, round_num, team_ids[i], team_ids[j], group_name))
            round_num += 1
            rounds.append(add_round(db, match_id, round_num, team_ids[j], team_ids[i], group_name))
            round_num += 1

    return rounds


def register_team(db: Session, match_id: int, team_id: int) -> list:
    """队伍报名，所有队员自动获得报名记录

    队员若已对该赛事报名（如之前个人报名），则跳过该队员，
    避免同一用户产生多条报名记录。
    """
    from app.models.team import TeamMember
    from app.services import team_service
    from app.services.auth_service import effective_identity
    from app.services.auth_service import _auto_identity

    # 队伍级校验：该队伍是否已报名此赛事（防止队长重复报名）
    team_registered = db.query(Registration).filter(
        Registration.match_id == match_id,
        Registration.team_id == team_id,
    ).first()
    if team_registered:
        raise HTTPException(status_code=400, detail="该队伍已报名此赛事，请勿重复报名")

    members = db.query(TeamMember).filter(TeamMember.team_id == team_id).all()

    # 校验：所有队员（含队长）必须完成学籍认证
    unverified = []
    for member in members:
        user = db.query(User).filter(User.id == member.user_id).first()
        if not user or not user.is_verified:
            name = (user.nickname or user.game_id) if user else f"用户{member.user_id}"
            unverified.append(name)
    if unverified:
        raise HTTPException(status_code=400, detail=f"以下队员未完成学籍认证，无法报名：{'、'.join(unverified)}")

    # 新生赛校验：队伍至少 3 名新生（现场动态算，跨年自动正确）
    match = get_match_by_id(db, match_id)
    if match and (match.match_type or "major") == "freshman":
        new_count = 0
        for member in members:
            user = db.query(User).filter(User.id == member.user_id).first()
            if user and effective_identity(user) == "new_student":
                new_count += 1
        if new_count < 3:
            raise HTTPException(status_code=400, detail=f"新生赛要求队伍至少 3 名新生，当前仅 {new_count} 名")

    registrations = []
    for member in members:
        # 该队员是否已报名此赛事（个人或其它队伍）
        existing = db.query(Registration).filter(
            Registration.match_id == match_id,
            Registration.user_id == member.user_id,
        ).first()
        if existing:
            # 已有报名记录，跳过，避免 (match_id, user_id) 唯一约束冲突
            continue
        reg = Registration(match_id=match_id, user_id=member.user_id, team_id=team_id)
        db.add(reg)
        registrations.append(reg)
    db.commit()
    for reg in registrations:
        db.refresh(reg)
    team_service.recalc_team_rating(db, team_id)
    return registrations


def register_user(db: Session, match_id: int, user_id: int) -> Registration:
    """个人报名

    个人报名直接通过（APPROVED），报名即出现在人才市场，
    方便队长查看并邀请入队。
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not user.is_verified:
        raise HTTPException(status_code=400, detail="未完成学籍认证，无法报名")

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
    db.commit()
    db.refresh(reg)
    return reg


def ensure_team_progress(db: Session, match_id: int, team_id: int) -> TeamProgress:
    """确保队伍在赛事中有进度记录，不存在则创建（默认挑战者组）"""
    progress = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.team_id == team_id,
    ).first()
    if not progress:
        progress = TeamProgress(
            match_id=match_id,
            team_id=team_id,
            stage=StageStatus.CHALLENGER,
        )
        db.add(progress)
        db.commit()
        db.refresh(progress)
    return progress


def approve_registration(db: Session, reg_id: int) -> Optional[Registration]:
    """审核通过报名（队伍报名通过时自动创建赛事进度，供自动编排使用）"""
    reg = db.query(Registration).filter(Registration.id == reg_id).first()
    if reg:
        reg.status = RegistrationStatus.APPROVED
        if reg.team_id:
            ensure_team_progress(db, reg.match_id, reg.team_id)
        db.commit()
        db.refresh(reg)
    return reg


def get_my_registration(db: Session, match_id: int, user_id: int) -> Optional[Registration]:
    """查询当前用户在某赛事的报名记录（未报名返回 None）"""
    return db.query(Registration).filter(
        Registration.match_id == match_id,
        Registration.user_id == user_id,
    ).first()


def auto_generate_single_round_matches(db: Session, match_id: int, group_name: str) -> list:
    """为指定小组生成单循环对阵"""
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == group_name
    ).all()

    team_ids = [p.team_id for p in progresses]
    if len(team_ids) < 2:
        raise HTTPException(status_code=400, detail=f"「{group_name}」组内队伍不足2支，无法生成对阵")
    rounds = []
    max_round = db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number.desc()).first()
    round_num = (max_round.round_number + 1) if max_round else 1

    for i in range(len(team_ids)):
        for j in range(i + 1, len(team_ids)):
            rounds.append(add_round(db, match_id, round_num, team_ids[i], team_ids[j], group_name))
            round_num += 1

    return rounds


def _rank_group_teams(db: Session, match_id: int, group_name: str, progresses: list) -> list:
    """按小组内已结束对阵统计排名：胜场 → 净胜分 → rating"""
    from app.models.team import Team
    stats = {p.team_id: {"wins": 0, "diff": 0, "progress": p} for p in progresses}
    rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name == group_name,
        MatchRound.status == RoundStatus.FINISHED
    ).all()
    for r in rounds:
        if r.team1_id in stats and r.team2_id in stats:
            stats[r.team1_id]["diff"] += r.team1_score - r.team2_score
            stats[r.team2_id]["diff"] += r.team2_score - r.team1_score
            if r.winner_id == r.team1_id:
                stats[r.team1_id]["wins"] += 1
            elif r.winner_id == r.team2_id:
                stats[r.team2_id]["wins"] += 1
    ratings = {t.id: t.rating for t in db.query(Team).filter(Team.id.in_(list(stats))).all()}
    ordered = sorted(
        stats.values(),
        key=lambda s: (-s["wins"], -s["diff"], -ratings.get(s["progress"].team_id, 0))
    )
    return [s["progress"] for s in ordered]


def finish_group_stage(db: Session, match_id: int) -> dict:
    """结束小组赛：每组第1直接晋级，每组第2进附加赛单循环，其余淘汰"""
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.CHALLENGER
    ).all()
    groups = {}
    for p in progresses:
        groups.setdefault(p.group_name, []).append(p)

    winners, runner_ups = [], []
    for gname, plist in groups.items():
        if not gname or gname == "附加赛":
            continue
        ranked = _rank_group_teams(db, match_id, gname, plist)
        if not ranked:
            continue
        ranked[0].stage = StageStatus.LEGEND    # 小组第1直接晋级传奇组
        winners.append(ranked[0])
        if len(ranked) >= 2:
            ranked[1].group_name = "附加赛"      # 小组第2进附加赛
            runner_ups.append(ranked[1])
        for p in ranked[2:]:
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
                add_round(db, match_id, round_num, rids[i], rids[j], "附加赛")
                round_num += 1
                added += 1

    db.commit()
    return {
        "group_winners": [p.team_id for p in winners],
        "runner_ups": [p.team_id for p in runner_ups],
        "added_rounds": added,
    }


def finish_playoff_stage(db: Session, match_id: int) -> dict:
    """结束附加赛：附加赛第1晋级淘汰赛，其余淘汰"""
    playoff_progs = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == "附加赛"
    ).all()
    if not playoff_progs:
        raise HTTPException(status_code=400, detail="当前没有附加赛阶段，请先结束小组赛")
    finished = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name == "附加赛",
        MatchRound.status == RoundStatus.FINISHED
    ).count()
    if finished == 0:
        raise HTTPException(status_code=400, detail="附加赛尚未打完，请先录入附加赛比分")
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
    legends = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.LEGEND
    ).all()
    if len(legends) != 8:
        raise HTTPException(
            status_code=400,
            detail=f"传奇组当前 {len(legends)} 支（需8支），请先完成「结束小组赛」和「结束附加赛」"
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
                add_round(db, match_id, round_num, tids[i], tids[j], zone)
                round_num += 1
                added += 1
    db.commit()
    return {"zone_teams": [p.team_id for p in ordered], "added_rounds": added}


def finish_legend_stage(db: Session, match_id: int) -> dict:
    """结束传奇组循环赛：每区前3晋级淘汰赛，第4名淘汰"""
    legends = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.LEGEND
    ).all()
    zones = {}
    for p in legends:
        zones.setdefault(p.group_name, []).append(p)
    # 校验上下区循环赛全部打完
    unfinished = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name.in_(["上区", "下区"]),
        MatchRound.status != RoundStatus.FINISHED
    ).count()
    if unfinished > 0:
        raise HTTPException(status_code=400, detail=f"传奇组还有 {unfinished} 场循环赛未打完，请先录入比分")
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
    qualifiers = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.PLAYOFF
    ).all()
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
    add_round(db, match_id, round_num, z1[1].team_id, z1[2].team_id, "淘汰赛")
    round_num += 1
    add_round(db, match_id, round_num, z2[1].team_id, z2[2].team_id, "淘汰赛")
    db.commit()
    return {"knockout_teams": [p.team_id for p in seeds], "added_rounds": 2}


def advance_knockout(db: Session, match_id: int) -> dict:
    """推进淘汰赛：1/4打完→半决赛（交叉），半决赛打完→决赛"""
    ko_rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id,
        MatchRound.group_name == "淘汰赛"
    ).order_by(MatchRound.round_number).all()
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
        ).all()
        upper_first = next((p for p in q if p.group_name == "上区" and p.seed == 1), None)
        # 种子规则：上1=1、下1=2、上2=3、下2=4、上3=5、下3=6，故下区第1是 seed=2
        lower_first = next((p for p in q if p.group_name == "下区" and p.seed == 2), None)
        if upper_first is None or lower_first is None:
            raise HTTPException(status_code=400, detail="找不到上下区第1名队伍，请先「生成淘汰赛」")
        add_round(db, match_id, round_num, upper_first.team_id, lower_winner, "淘汰赛")
        round_num += 1
        add_round(db, match_id, round_num, lower_first.team_id, upper_winner, "淘汰赛")
        round_num += 1
        added = 2
    elif len(ko_rounds) == 4:
        # 半决赛打完 → 决赛
        sf1 = finished[2].winner_id
        sf2 = finished[3].winner_id
        add_round(db, match_id, round_num, sf1, sf2, "淘汰赛")
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
    # 清理对阵、队伍进度、报名记录（有外键引用，必须先删）
    db.query(MatchRound).filter(MatchRound.match_id == match_id).delete()
    db.query(TeamProgress).filter(TeamProgress.match_id == match_id).delete()
    db.query(Registration).filter(Registration.match_id == match_id).delete()
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