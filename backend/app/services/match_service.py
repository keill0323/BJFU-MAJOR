"""赛事业务逻辑

Author: keill
Since: 2026-07-23
"""

from typing import Optional, List
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.match import Match, MatchRound, MatchStatus, RoundStatus, StageStatus, TeamProgress
from app.models.team import Team


def create_match(
    db: Session,
    name: str,
    description: Optional[str] = None,
    max_teams: int = 16,
    team_size: int = 5,
    register_start: Optional[datetime] = None,
    register_end: Optional[datetime] = None,
    match_start: Optional[datetime] = None,
) -> Match:
    """创建赛事"""
    from datetime import datetime
    match = Match(
        name=name,
        description=description,
        max_teams=max_teams,
        team_size=team_size,
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

def get_all_matches(db: Session) -> List[Match]:
    """获取所有赛事"""
    return db.query(Match).order_by(Match.created_at.desc()).all()


def search_matches(db: Session, keyword: str) -> List[Match]:
    """根据名称模糊查询赛事"""
    return db.query(Match).filter(Match.name.contains(keyword)).all()


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
                        winner_id: int) -> Optional[MatchRound]:
    """更新对阵结果"""
    match_round = db.query(MatchRound).filter(MatchRound.id == round_id).first()
    if match_round:
        if winner_id not in (match_round.team1_id, match_round.team2_id):
            raise ValueError("胜者必须是参赛队伍之一")
        match_round.team1_score = team1_score
        match_round.team2_score = team2_score
        match_round.winner_id = winner_id
        match_round.status = RoundStatus.FINISHED
        db.commit()
        db.refresh(match_round)
    return match_round


def get_rounds_by_match(db: Session, match_id: int) -> List[MatchRound]:
    """获取某赛事所有对阵,按轮次排序"""
    return db.query(MatchRound).filter(
        MatchRound.match_id == match_id
    ).order_by(MatchRound.round_number).all()


def auto_assign_seeds(db: Session, match_id: int) -> list:
    """按队伍rating排序分配种子，前四直升传奇组"""
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.stage == StageStatus.CHALLENGER
    ).join(Team).order_by(Team.rating.desc()).all()

    for i, progress in enumerate(progresses, 1):
        progress.seed = i
        if i<=4:
            progress.stage = StageStatus.LEGEND
            progress.group_name = f"种子{i}"

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
        if(i//n) %2 ==1:
            group_idx = n-1 -group_idx
        progress.group_name =group_names[group_idx]

    db.commit()
    return progresses


def auto_generate_group_matches(db: Session, match_id: int, group_name: str) -> list:
    """为指定小组生成双循环对阵"""
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == group_name
    ).all()
    
    team_ids = [p.team_id for p in progresses]
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


def auto_generate_single_round_matches(db: Session, match_id: int, group_name: str) -> list:
    """为指定小组生成单循环对阵"""
    progresses = db.query(TeamProgress).filter(
        TeamProgress.match_id == match_id,
        TeamProgress.group_name == group_name
    ).all()

    team_ids = [p.team_id for p in progresses]
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