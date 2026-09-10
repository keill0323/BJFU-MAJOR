"""Public honours built from completed finals and effective certified player ranks."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.hall import ChampionSnapshot
from app.models.match import Match, MatchRound, MatchStatus, RoundStatus
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services.locking import lock_row


RANK_ORDER = {name: value for value, name in enumerate(
    ("D", "C", "C+", "C++", "B", "B+", "B++", "A", "A+", "A++"))}
RANK_ORDER.update({f"S{stars}": 10 + stars for stars in range(51)})
RANK_ORDER["S"] = RANK_ORDER["S0"]


def _page(items, total, offset, limit):
    return {"items": items, "total": total, "offset": offset, "limit": limit,
            "has_more": offset + len(items) < total}


def list_players(db: Session, *, offset=0, limit=50) -> dict:
    """Rank in SQL before pagination; no student/authentication data is loaded."""
    score = case(RANK_ORDER, value=User.rank, else_=-1)
    ranked = db.query(
        User.id.label("user_id"), User.nickname, User.avatar, User.rank,
        User.individual_rating,
        func.rank().over(order_by=score.desc()).label("position"),
    ).filter(User.rank.in_(tuple(RANK_ORDER))).subquery()
    total = db.query(func.count()).select_from(ranked).scalar()
    rows = db.query(ranked).order_by(ranked.c.position, ranked.c.user_id).offset(offset).limit(limit).all()
    return _page([dict(row._mapping) for row in rows], total, offset, limit)


def list_champions(db: Session, *, offset=0, limit=20) -> dict:
    query = db.query(ChampionSnapshot).filter(ChampionSnapshot.is_valid.is_(True))
    total = query.count()
    rows = query.order_by(ChampionSnapshot.event_date.desc(), ChampionSnapshot.match_id.desc()).offset(offset).limit(limit).all()
    available = {row[0] for row in db.query(Match.id).filter(
        Match.id.in_([snapshot.match_id for snapshot in rows]),
    ).all()} if rows else set()
    return _page([_champion_info(row, match_available=row.match_id in available) for row in rows], total, offset, limit)


def _champion_info(row, *, match_available=True):
    return {
        "match_id": row.match_id, "match_name": row.match_name, "match_type": row.match_type,
        "match_available": match_available,
        "event_date": row.event_date, "awarded_at": row.awarded_at,
        "champion_team_id": row.champion_team_id, "champion_name": row.champion_name,
        "champion_logo": row.champion_logo, "runner_up_name": row.runner_up_name,
        "champion_score": row.champion_score, "runner_up_score": row.runner_up_score,
        "roster": json.loads(row.roster_json), "snapshot_source": row.snapshot_source,
    }


def _admin_champion_info(snapshot):
    return {
        "champion": _champion_info(snapshot) if snapshot and snapshot.is_valid else None,
        "note": snapshot.manual_note if snapshot else None,
        "updated_at": snapshot.updated_at if snapshot else None,
        "version": (snapshot.manual_version or 0) if snapshot else 0,
    }


def get_admin_champion(db: Session, match_id: int):
    if not db.get(Match, match_id):
        raise HTTPException(status_code=404, detail="赛事不存在")
    return _admin_champion_info(db.get(ChampionSnapshot, match_id))


def save_manual_champion(db: Session, match_id: int, request, admin_id: int):
    """Upsert one explicitly sourced historical honour under the event lock."""
    try:
        match = lock_row(db, Match, match_id)
        if not match:
            raise HTTPException(status_code=404, detail="赛事不存在")
        if match.status != MatchStatus.FINISHED:
            raise HTTPException(status_code=400, detail="仅已结束的赛事可以补录冠军")
        snapshot = db.query(ChampionSnapshot).filter_by(match_id=match_id).populate_existing().with_for_update().first()
        version = (snapshot.manual_version or 0) if snapshot else 0
        if request.expected_version is not None and request.expected_version != version:
            raise HTTPException(status_code=409, detail="冠军记录已被其他管理员更新，请重新打开后再保存")
        if snapshot and snapshot.snapshot_source != "manual" and snapshot.is_valid:
            raise HTTPException(status_code=409, detail="此赛事已有赛程生成的冠军，请通过决赛比分修正")
        if (not snapshot or snapshot.snapshot_source != "manual") and _completed_final(db, match_id):
            raise HTTPException(status_code=409, detail="此赛事有完整决赛结果，请使用赛程生成冠军，不能手动覆盖")
        if request.champion_team_id is not None and not db.get(Team, request.champion_team_id):
            raise HTTPException(status_code=400, detail="所选队伍不存在；历史队伍可取消关联后填写队名")
        member_ids = {member.user_id for member in request.roster if member.user_id is not None}
        known_ids = {row[0] for row in db.query(User.id).filter(User.id.in_(member_ids)).all()} if member_ids else set()
        if known_ids != member_ids:
            raise HTTPException(status_code=400, detail="部分选手已不存在；可取消关联后填写历史昵称")
        now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        if snapshot is None:
            snapshot = ChampionSnapshot(match_id=match_id)
            db.add(snapshot)
        if snapshot.snapshot_source != "manual":
            snapshot.manual_created_by = admin_id
            snapshot.manual_created_at = now
        snapshot.match_name = match.name
        snapshot.match_type = match.match_type
        snapshot.event_date = request.event_date
        snapshot.final_round_id = None
        snapshot.awarded_at = None  # Date of entry is not the unknown time of victory.
        snapshot.champion_team_id = request.champion_team_id
        snapshot.champion_name = request.champion_name
        snapshot.champion_logo = request.champion_logo
        snapshot.runner_up_name = request.runner_up_name
        snapshot.champion_score = request.champion_score
        snapshot.runner_up_score = request.runner_up_score
        snapshot.roster_json = json.dumps([member.model_dump() for member in request.roster], ensure_ascii=False)
        snapshot.finalists_json = "{}"
        snapshot.snapshot_source = "manual"
        snapshot.is_valid = True
        snapshot.manual_updated_by = admin_id
        snapshot.manual_note = request.note
        snapshot.manual_version = version + 1
        snapshot.updated_at = now
        db.flush()
        result = _admin_champion_info(snapshot)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _decisive(round_) -> bool:
    if (round_.status != RoundStatus.FINISHED or not round_.team1_id or not round_.team2_id
            or round_.team1_id == round_.team2_id):
        return False
    s1, s2 = round_.team1_score, round_.team2_score
    if s1 is None or s2 is None or min(s1, s2) < 0 or s1 == s2:
        return False
    return round_.winner_id == (round_.team1_id if s1 > s2 else round_.team2_id)


def _completed_final(db: Session, match_id: int):
    """Verify the project's six-team bracket, never guess from event status/rank."""
    rounds = db.query(MatchRound).filter(
        MatchRound.match_id == match_id, MatchRound.group_name == "淘汰赛",
    ).order_by(MatchRound.round_number, MatchRound.id).populate_existing().with_for_update().all()
    if len(rounds) != 5 or not all(_decisive(round_) for round_ in rounds):
        return None
    q1, q2, s1, s2, final = rounds
    quarter_ids = {q1.team1_id, q1.team2_id, q2.team1_id, q2.team2_id}
    semi1, semi2 = {s1.team1_id, s1.team2_id}, {s2.team1_id, s2.team2_id}
    if len(quarter_ids) != 4 or q2.winner_id not in semi1 or q1.winner_id not in semi2:
        return None
    bye1, bye2 = semi1 - {q2.winner_id}, semi2 - {q1.winner_id}
    if len(bye1 | bye2) != 2 or (bye1 | bye2) & quarter_ids:
        return None
    if {final.team1_id, final.team2_id} != {s1.winner_id, s2.winner_id}:
        return None
    return final


def _freeze_finalists(db: Session, final: MatchRound):
    team_ids = (final.team1_id, final.team2_id)
    teams = db.query(Team.id, Team.name, Team.logo).filter(Team.id.in_(team_ids)).all()
    if len(teams) != 2:
        return None
    snapshots = {str(team.id): {"name": team.name, "logo": team.logo, "roster": []}
                 for team in teams}
    members = db.query(TeamMember.team_id, User.id.label("user_id"), User.nickname,
                       User.avatar, User.rank).join(User, User.id == TeamMember.user_id).filter(
        TeamMember.team_id.in_(team_ids),
    ).order_by(TeamMember.team_id, User.id).all()
    for member in members:
        snapshots[str(member.team_id)]["roster"].append({
            "user_id": member.user_id, "nickname": member.nickname,
            "avatar": member.avatar, "rank": member.rank,
        })
    return snapshots


def sync_champion_snapshot(db: Session, match_id: int, *, source="final_result") -> bool:
    """Called under the event row lock; participates in the caller's transaction.

    Invalid corrections withdraw publication while retaining the original frozen
    finalists for any later valid correction. This function never commits.
    """
    match = db.get(Match, match_id)
    if not match:
        return False
    # Current reads are essential after waiting for the event lock under MySQL's
    # REPEATABLE READ: an earlier round read may predate another final submission.
    snapshot = db.query(ChampionSnapshot).filter_by(match_id=match_id).populate_existing().with_for_update().first()
    # A sourced manual archive is independent of missing or subsequently edited
    # schedules. Only the explicit administrator endpoint may replace it.
    if snapshot and snapshot.snapshot_source == "manual":
        return snapshot.is_valid
    final = _completed_final(db, match_id)
    if final is None:
        if snapshot:
            snapshot.is_valid = False
        return False
    if snapshot:
        finalists = json.loads(snapshot.finalists_json)
        # Existing history cannot be silently repurposed for a different bracket.
        if snapshot.final_round_id != final.id or set(finalists) != {str(final.team1_id), str(final.team2_id)}:
            snapshot.is_valid = False
            return False
    else:
        finalists = _freeze_finalists(db, final)
        if finalists is None:
            return False
        snapshot = ChampionSnapshot(
            match_id=match_id, final_round_id=final.id,
            match_name=match.name, match_type=match.match_type,
            event_date=match.match_start or match.created_at,
            awarded_at=(datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
                        if source == "final_result" else None),
            snapshot_source=source,
            finalists_json=json.dumps(finalists, ensure_ascii=False),
        )
        db.add(snapshot)
    champion = finalists[str(final.winner_id)]
    runner_id = final.team2_id if final.winner_id == final.team1_id else final.team1_id
    snapshot.champion_team_id = final.winner_id
    snapshot.champion_name = champion["name"]
    snapshot.champion_logo = champion["logo"]
    snapshot.roster_json = json.dumps(champion["roster"], ensure_ascii=False)
    snapshot.runner_up_name = finalists[str(runner_id)]["name"]
    snapshot.champion_score = final.team1_score if final.winner_id == final.team1_id else final.team2_score
    snapshot.runner_up_score = final.team2_score if final.winner_id == final.team1_id else final.team1_score
    snapshot.is_valid = True
    return True


def backfill_champions(db: Session) -> dict:
    """Explicit deployment/maintenance operation; never called by a read route.

    Legacy records cannot prove the roster at the time of victory, so their source
    is 'backfill' and awarded_at is unknown. Existing snapshots remain untouched.
    """
    result = {"created": 0, "skipped": 0}
    ids = [row[0] for row in db.query(MatchRound.match_id).filter(
        MatchRound.group_name == "淘汰赛",
    ).distinct().order_by(MatchRound.match_id).all()]
    try:
        for match_id in ids:
            match = lock_row(db, Match, match_id)
            existing = db.query(ChampionSnapshot).filter_by(match_id=match_id).populate_existing().with_for_update().first()
            if not match or existing:
                result["skipped"] += 1
                continue
            if sync_champion_snapshot(db, match_id, source="backfill"):
                result["created"] += 1
            else:
                result["skipped"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result
