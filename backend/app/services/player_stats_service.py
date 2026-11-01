"""Controlled map imports and public aggregates; no external requests or user matching."""
import hashlib
import hmac
import json
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models.match import Match, MatchRound, MatchStatus, TeamProgress
from app.models.player_stats import PlayerMapStats, PlayerStatsMap, utc_now
from app.models.team import Team
from app.schemas.player_stats import ImportedStatsMap
from app.services.locking import lock_row


PROVIDER = "perfect_match"
MAX_MAPS = 500
MAP_COLUMNS = ("provider", "external_match_id", "source_url", "cup_name", "map_name",
               "source_team1_name", "source_team2_name", "team1_id", "team2_id",
               "team1_score", "team2_score", "round_id", "map_index")


def _canonical(data: ImportedStatsMap):
    payload = data.model_dump()
    payload["players"].sort(key=lambda player: player["steam_id"])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def import_perfect_maps(db: Session, match_id: int, maps: list[dict]) -> dict:
    """Import one verified batch atomically. Caller supplies data, never credentials.

    Existing source-map IDs cannot move between events. Reimports replace that
    map's lines, preserving stable event/player keys; byte-equivalent data is a
    no-op. A caller-owned deferred-commit Session may wrap this in maintenance.
    """
    try:
        if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id < 1:
            raise ValueError("赛事ID无效")
        if not isinstance(maps, list) or not 1 <= len(maps) <= MAX_MAPS:
            raise ValueError("每批须包含1—500张真实地图")
        validated = [ImportedStatsMap.model_validate(value) for value in maps]
        keys = [(value.provider, value.external_match_id) for value in validated]
        if len(set(keys)) != len(keys):
            raise ValueError("同批地图重复")
        match = lock_row(db, Match, match_id)
        if match is None:
            raise ValueError("赛事不存在")
        team_ids = {value for item in validated for value in (item.team1_id, item.team2_id) if value is not None}
        known = {row[0] for row in db.query(Team.id).filter(Team.id.in_(team_ids)).all()} if team_ids else set()
        enrolled = {row[0] for row in db.query(TeamProgress.team_id).filter(
            TeamProgress.match_id == match_id, TeamProgress.team_id.in_(team_ids)).all()} if team_ids else set()
        if known != team_ids or enrolled != team_ids:
            raise ValueError("关联队伍必须存在并属于该赛事")
        round_ids = {item.round_id for item in validated if item.round_id is not None}
        rounds = {row.id: row for row in db.query(MatchRound).filter(MatchRound.id.in_(round_ids)).all()} if round_ids else {}
        inserted = updated = unchanged = 0
        for item in validated:
            if item.round_id is not None:
                linked = rounds.get(item.round_id)
                if linked is None or linked.match_id != match_id:
                    raise ValueError("来源地图关联的对阵不属于该赛事")
                supplied = {value for value in (item.team1_id, item.team2_id) if value is not None}
                if supplied and not supplied.issubset({linked.team1_id, linked.team2_id}):
                    raise ValueError("来源地图与关联对阵的队伍不一致")
            row = db.query(PlayerStatsMap).filter_by(provider=item.provider,
                external_match_id=item.external_match_id).populate_existing().with_for_update().first()
            if row is not None and row.match_id != match_id:
                raise ValueError("来源地图已属于其他赛事，不能移动")
            payload_hash = _canonical(item)
            if row is not None and row.payload_sha256 == payload_hash:
                unchanged += 1
                continue
            if row is None:
                row = PlayerStatsMap(match_id=match_id)
                db.add(row)
                inserted += 1
            else:
                db.query(PlayerMapStats).filter_by(map_id=row.id).delete(synchronize_session=False)
                updated += 1
            for field in MAP_COLUMNS:
                setattr(row, field, getattr(item, field))
            row.payload_sha256 = payload_hash
            row.updated_at = utc_now()
            db.flush()
            for player in item.players:
                db.add(PlayerMapStats(map_id=row.id, **player.model_dump()))
        db.flush()
        map_count = db.query(PlayerStatsMap).filter_by(match_id=match_id).count()
        if map_count > MAX_MAPS:
            raise ValueError("单赛事最多导入500张地图")
        player_rows = db.query(PlayerMapStats).join(PlayerStatsMap,
            PlayerMapStats.map_id == PlayerStatsMap.id).filter(PlayerStatsMap.match_id == match_id).count()
        db.commit()
        return {"inserted": inserted, "updated": updated, "unchanged": unchanged,
                "processed_maps": len(validated), "map_count": map_count, "player_rows": player_rows}
    except Exception:
        db.rollback()
        raise


def _player_key(match_id, steam_id):
    return hmac.new(settings.SECRET_KEY.encode(),
                    ("player-stats:" + str(match_id) + ":" + steam_id).encode(),
                    hashlib.sha256).hexdigest()[:32]


def _public_nickname(match_id, steam_id, nickname):
    # Some platform views fall back to displaying UID when a nickname is absent.
    # A public nickname must not accidentally undo the opaque-ID boundary.
    return ("未命名选手 " + _player_key(match_id, steam_id)[-6:]
            if nickname.strip() == steam_id else nickname)


def list_player_stats(db: Session, match_id: int, *, keyword="", team_id=None, offset=0, limit=50):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(400, "分页参数无效")
    match = db.get(Match, match_id)
    if match is None or getattr(match, "is_hidden", False) or match.status == MatchStatus.DRAFT:
        raise HTTPException(404, "赛事不存在或尚未公开")
    source_maps = db.query(PlayerStatsMap).filter_by(match_id=match_id, provider=PROVIDER).order_by(PlayerStatsMap.id).all()
    teams = {row.id: row for row in db.query(Team.id, Team.name, Team.is_hidden).all()}
    hidden_names = {row.name.strip().casefold() for row in teams.values() if row.is_hidden}

    def visible(row):
        if any(value in teams and teams[value].is_hidden for value in (row.team1_id, row.team2_id)):
            return False
        return not any(name.strip().casefold() in hidden_names for name in (row.source_team1_name, row.source_team2_name))

    source_maps = [row for row in source_maps if visible(row) and
                   (team_id is None or team_id in (row.team1_id, row.team2_id))]
    by_map = {row.id: row for row in source_maps}
    lines = db.query(PlayerMapStats).filter(PlayerMapStats.map_id.in_(by_map)).order_by(
        PlayerMapStats.map_id, PlayerMapStats.id).all() if by_map else []
    buckets = {}
    map_player_counts = {}
    for line in lines:
        source = by_map[line.map_id]
        map_player_counts[source.id] = map_player_counts.get(source.id, 0) + 1
        local_team_id = source.team1_id if line.team_slot == 1 else source.team2_id
        if team_id is not None and local_team_id != team_id:
            continue
        local_name = source.source_team1_name if line.team_slot == 1 else source.source_team2_name
        if local_team_id in teams:
            local_name = teams[local_team_id].name
        display_name = _public_nickname(match_id, line.steam_id, line.nickname)
        bucket = buckets.setdefault(line.steam_id, {"nickname": display_name, "names": set(),
            "teams": {}, "maps_played": 0, "kills": 0, "deaths": 0, "assists": 0,
            "adr_sum": 0.0, "adr_rounds": 0, "adr_maps": 0,
            "rating_sum": 0.0, "rating_maps": 0, "first_kills": 0, "first_kill_maps": 0})
        bucket["nickname"] = display_name
        bucket["names"].add(display_name.casefold())
        bucket["teams"][(local_team_id, local_name)] = {"team_id": local_team_id, "team_name": local_name}
        bucket["maps_played"] += 1
        for field in ("kills", "deaths", "assists"):
            bucket[field] += getattr(line, field)
        rounds = source.team1_score + source.team2_score
        if line.adr is not None:
            bucket["adr_sum"] += line.adr * rounds
            bucket["adr_rounds"] += rounds
            bucket["adr_maps"] += 1
        if line.rating is not None:
            bucket["rating_sum"] += line.rating
            bucket["rating_maps"] += 1
        if line.first_kills is not None:
            bucket["first_kills"] += line.first_kills
            bucket["first_kill_maps"] += 1
    needle = keyword.strip().casefold()
    items = []
    for steam_id, bucket in buckets.items():
        if needle and not any(needle in name for name in bucket["names"]):
            continue
        items.append({"player_key": _player_key(match_id, steam_id), "nickname": bucket["nickname"],
            "teams": sorted(bucket["teams"].values(), key=lambda team: (team["team_name"], team["team_id"] or 0)),
            "maps_played": bucket["maps_played"], "kills": bucket["kills"], "deaths": bucket["deaths"], "assists": bucket["assists"],
            "kd": round(bucket["kills"] / bucket["deaths"], 3) if bucket["deaths"] else None,
            "adr": round(bucket["adr_sum"] / bucket["adr_rounds"], 2) if bucket["adr_rounds"] else None,
            "adr_maps": bucket["adr_maps"],
            "average_rating": round(bucket["rating_sum"] / bucket["rating_maps"], 3) if bucket["rating_maps"] else None,
            "rating_maps": bucket["rating_maps"],
            "first_kills": bucket["first_kills"] if bucket["first_kill_maps"] else None,
            "first_kill_maps": bucket["first_kill_maps"]})
    items.sort(key=lambda item: (item["average_rating"] is None, -(item["average_rating"] or 0),
                               item["kd"] is None, -(item["kd"] or 0), -item["kills"],
                               item["nickname"].casefold(), item["player_key"]))
    for position, item in enumerate(items, 1):
        item["position"] = position
    total = len(items)
    latest = max((row.updated_at for row in source_maps), default=None)
    notices = ["仅统计已导入的真实地图；弃权与缺失地图不计入选手出场。",
               "ADR按有值地图的回合数加权，为平台展示值计算的近似值。",
               "完美Rating均值按有值地图平均，未核实其与HLTV Rating算法的对应关系。",
               "缺失指标保留为空；平台选手未自动关联本站账号。"]
    if team_id is not None:
        notices.append("队伍筛选只统计选手代表该队伍的地图。")
    return {"match_id": match_id, "source": PROVIDER, "source_label": "完美赛事平台",
        "map_count": len(source_maps), "player_count": len(buckets), "total": total,
        "offset": offset, "limit": limit, "has_more": offset + len(items[offset:offset + limit]) < total,
        "updated_at": latest.replace(tzinfo=timezone.utc).isoformat() if latest else None,
        "items": items[offset:offset + limit],
        "source_maps": [{"external_match_id": row.external_match_id, "source_url": row.source_url,
            "map_name": row.map_name, "source_team1_name": row.source_team1_name, "source_team2_name": row.source_team2_name,
            "team1_id": row.team1_id, "team2_id": row.team2_id, "team1_score": row.team1_score, "team2_score": row.team2_score,
            "round_id": row.round_id, "map_index": row.map_index,
            "player_count": map_player_counts.get(row.id, 0)} for row in source_maps],
        "notices": notices}
