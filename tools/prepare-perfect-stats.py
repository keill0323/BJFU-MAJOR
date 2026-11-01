"""Prepare reviewed Perfect Match imports offline; never contacts a service or writes a database.

External team IDs are resolved by constraints from the verified opponents of
every source map. Camp, row order, and nicknames never assign a player's team.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata


WIN_ICONS = {"tkill", "tbomb", "ctkill", "ctbomb", "timeout"}
MISSING = {"", "-", "--", "—", "n/a", "null", "none"}


class PreparationError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.evidence = {"status": "rejected", "code": code, "message": message, **details}


def reject(code, message, **details):
    raise PreparationError(code, message, **details)


def normalized_name(value):
    if not isinstance(value, str):
        reject("invalid_name", "队伍名必须是文字")
    text = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in text if char.isalnum())


def text_value(value, label):
    if not isinstance(value, str) or not value.strip():
        reject("missing_text", label + "必须是非空文字")
    return value.strip()


def integer(value, label, optional=False):
    if value is None or (isinstance(value, str) and value.strip().casefold() in MISSING):
        if optional:
            return None
        reject("missing_integer", label + "缺少真实整数，不能补0")
    if isinstance(value, bool):
        reject("invalid_integer", label + "不能是布尔值")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        result = int(value.strip())
    else:
        reject("invalid_integer", label + "不是非负整数")
    if result < 0:
        reject("invalid_integer", label + "不能为负数")
    return result


def decimal(value, label, optional=True, unit=None):
    if value is None or (isinstance(value, str) and value.strip().casefold() in MISSING):
        if optional:
            return None
        reject("missing_number", label + "缺少真实数值")
    if isinstance(value, bool):
        reject("invalid_number", label + "不能是布尔值")
    if isinstance(value, str):
        value = unicodedata.normalize("NFKC", value).strip()
        if unit == "%" and value.endswith("%"):
            value = value[:-1].strip()
        elif unit == "ms":
            value = re.sub(r"\s*(?:ms|毫秒)$", "", value, flags=re.I).strip()
    try:
        result = float(value)
    except (TypeError, ValueError):
        reject("invalid_number", label + "无法解析")
    if not math.isfinite(result) or result < 0:
        reject("invalid_number", label + "必须是有限非负数")
    return result


def build_name_resolver(detail, aliases):
    teams = detail.get("teams")
    if not isinstance(teams, list) or not teams:
        reject("missing_teams", "赛事详情没有可核验的队伍")
    exact, normalized, by_id = {}, {}, {}
    for team in teams:
        tid = integer(team.get("team_id"), "本地队伍ID")
        if tid < 1 or tid in by_id:
            reject("invalid_local_team", "本地队伍ID必须为唯一正整数")
        name = text_value(team.get("team_name"), "本地队伍名")
        by_id[tid] = name
        exact.setdefault(unicodedata.normalize("NFKC", name).casefold(), set()).add(tid)
        normalized.setdefault(normalized_name(name), set()).add(tid)
    if not isinstance(aliases, dict):
        reject("invalid_aliases", "别名文件必须是名称到本地ID的对象")
    alias_lookup = {}
    for alias, value in aliases.items():
        tid = integer(value, "别名目标队伍ID")
        if tid not in by_id:
            reject("unknown_alias_target", "已确认别名指向不存在的队伍", alias=alias, team_id=tid)
        key = normalized_name(alias)
        if not key or (key in alias_lookup and alias_lookup[key] != tid):
            reject("alias_conflict", "规范化后别名冲突", alias=alias)
        canonical_exact = exact.get(unicodedata.normalize("NFKC", alias).casefold(), set())
        if canonical_exact and canonical_exact != {tid}:
            reject("alias_conflict", "别名覆盖了另一支队伍的正式名称", alias=alias)
        alias_lookup[key] = tid

    def resolve(name):
        key = normalized_name(name)
        if key in alias_lookup:
            return alias_lookup[key]
        direct = exact.get(unicodedata.normalize("NFKC", name).casefold(), set())
        choices = direct or normalized.get(key, set())
        if len(choices) != 1:
            reject("ambiguous_team_name", "来源队名不能唯一关联本地队伍", source_name=name,
                   candidates=sorted(choices))
        return next(iter(choices))

    return resolve, by_id


def check_round_evidence(record, pair, scores, resolve):
    evidence = record.get("round_evidence")
    room = record["external_match_id"]
    if not isinstance(evidence, dict):
        reject("missing_round_evidence", "地图缺少可视回合记录", external_match_id=room)
    rows, names = evidence.get("rows"), evidence.get("teams")
    if (not isinstance(rows, list) or len(rows) != 2 or not all(isinstance(row, list) for row in rows)
            or not isinstance(names, list) or len(names) != 2):
        reject("invalid_round_evidence", "回合记录须有两队及两行", external_match_id=room)
    evidence_pair = [resolve(name) for name in names]
    if set(evidence_pair) != set(pair) or len(set(evidence_pair)) != 2 or len(rows[0]) != len(rows[1]):
        reject("round_teams_mismatch", "回合记录队伍或列数不一致", external_match_id=room)
    for row in rows:
        if any(not isinstance(cell, str) or cell.strip() not in WIN_ICONS | {"", "divide"} for cell in row):
            reject("unknown_round_icon", "回合记录含未知图标，不能猜测", external_match_id=room)
    rows = [[cell.strip() for cell in row] for row in rows]
    dividers = [index for index, cell in enumerate(rows[0]) if cell == "divide"]
    if dividers != [index for index, cell in enumerate(rows[1]) if cell == "divide"]:
        reject("divider_mismatch", "两行换边分隔列不一致", external_match_id=room)
    played = [index for index in range(len(rows[0])) if rows[0][index] != "divide"]
    expected = dict(zip(pair, scores))
    wins = [sum(cell in WIN_ICONS for cell in row) for row in rows]
    if (len(played) != sum(scores) or wins != [expected[tid] for tid in evidence_pair]
            or any(sum(rows[side][index] in WIN_ICONS for side in (0, 1)) != 1 for index in played)):
        reject("round_score_mismatch", "可视回合记录与地图比分不一致", external_match_id=room,
               score=list(scores), evidence_team_ids=evidence_pair, row_wins=wins,
               non_divide_columns=len(played))
    return {"verified": True, "teams": evidence_pair, "row_wins": wins,
            "non_divide_columns": len(played), "divider_columns": dividers}


def candidate_evidence(observations, intersections, candidates, names):
    rows = []
    for external_id in sorted(observations):
        seen = observations[external_id]
        final = candidates.get(external_id, set())
        resolved = next(iter(final)) if len(final) == 1 else None
        rows.append({"source_team_id": external_id,
            "observations": seen,
            "source_rooms": sorted({row["external_match_id"] for row in seen}, key=int),
            "candidate_union": sorted(set().union(*(set(row["team_ids"]) for row in seen))),
            "candidate_intersection": sorted(intersections[external_id]),
            "final_candidates": sorted(final), "team_id": resolved,
            "team_name": names.get(resolved)})
    return rows


def resolve_external_teams(records, names):
    observations, constraints = {}, []
    for record in records:
        external_ids = record["_external_team_ids"]
        pair = record["_team_ids"]
        constraints.append((record["external_match_id"], external_ids, pair))
        for external_id in external_ids:
            observations.setdefault(external_id, []).append({"external_match_id": record["external_match_id"],
                                                            "team_ids": sorted(pair)})
    intersections = {external_id: set.intersection(*(set(row["team_ids"]) for row in rows))
                     for external_id, rows in observations.items()}
    candidates = {key: set(value) for key, value in intersections.items()}

    def failed(code, message):
        reject(code, message, team_mapping=candidate_evidence(observations, intersections, candidates, names))

    if any(not values for values in candidates.values()):
        failed("external_team_conflict", "同一外部队伍ID在不同地图的对手集合没有共同队伍")
    changed = True
    while changed:
        changed = False
        for _, external_ids, _ in constraints:
            a, b = external_ids
            for first, second in ((a, b), (b, a)):
                if len(candidates[first]) == 1:
                    revised = candidates[second] - candidates[first]
                    if not revised:
                        failed("external_team_conflict", "同一地图的两个外部队伍ID不能映射为同一支队伍")
                    if revised != candidates[second]:
                        candidates[second] = revised
                        changed = True
    if any(len(values) != 1 for values in candidates.values()):
        failed("external_team_unresolved", "仍有外部队伍ID存在多个候选，整批拒绝，不猜归队")
    mapping = {key: next(iter(value)) for key, value in candidates.items()}
    for room, external_ids, pair in constraints:
        if {mapping[key] for key in external_ids} != set(pair):
            failed("external_team_conflict", "映射后双方与已核实对阵不一致：" + room)
    return mapping, candidate_evidence(observations, intersections, candidates, names)


def parse_map_index(label):
    label = unicodedata.normalize("NFKC", label or "")
    if not re.search(r"BO\s*3", label, flags=re.I):
        return None
    found = re.search(r"BO\s*3\s*第\s*([一二三123])\s*[场局图]", label, flags=re.I)
    if not found:
        reject("unknown_bo3_index", "BO3标签未能明确第几张地图")
    return {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}[found.group(1)]


def round_link(record, rounds, index):
    pair, scores = record["_team_ids"], record["_scores"]
    candidates = []
    for row in rounds:
        if row.get("status") != "finished" or {row.get("team1_id"), row.get("team2_id")} != set(pair):
            continue
        if index is None:
            if row.get("group_name") == "淘汰赛":
                continue
            value = (row.get("team1_score"), row.get("team2_score"))
        else:
            if row.get("group_name") != "淘汰赛":
                continue
            games = row.get("bo3_scores")
            if not isinstance(games, list) or len(games) < index or not isinstance(games[index - 1], dict):
                continue
            value = (games[index - 1].get("t1"), games[index - 1].get("t2"))
        if row.get("team1_id") != pair[0]:
            value = value[::-1]
        if tuple(value) == tuple(scores):
            candidates.append(row["id"])
    unique = len(candidates) == 1
    return {"round_id": candidates[0] if unique else None, "map_index": index if unique else None}, {
        "kind": "BO3" if index is not None else "BO1", "source_map_index": index,
        "candidate_round_ids": candidates, "status": "linked" if unique else "unlinked",
        "reason": None if unique else "没有唯一的已结束对阵及方向一致比分"}


def parse_player(raw, slot, room, row_number):
    if not isinstance(raw, list) or len(raw) != 21:
        reject("invalid_player_columns", "选手行须为21元素（UID与昵称分列）", external_match_id=room, row=row_number)
    steam_id = text_value(raw[0], "SteamID")
    if not re.fullmatch(r"[0-9]{17}", steam_id):
        reject("invalid_steam_id", "SteamID必须保持17位字符串", external_match_id=room, row=row_number)
    player = {"steam_id": steam_id, "nickname": text_value(raw[1], "平台昵称"),
              "source_team_id": text_value(raw[3], "外部队伍ID"), "team_slot": slot}
    for field, column in (("kills", 4), ("deaths", 5), ("assists", 6)):
        player[field] = integer(raw[column], field)
    for field, column in (("first_kills", 7), ("kills_2", 14), ("kills_3", 15), ("kills_4", 16),
                          ("kills_5", 17), ("clutch_1v1", 18), ("clutch_1v2", 19), ("clutch_1v3", 20)):
        player[field] = integer(raw[column], field, optional=True)
    for field, column, unit in (("hit_percent", 8, "%"), ("hs_percent", 9, "%"), ("rws", 10, None),
                                ("rating", 11, None), ("adr", 12, None), ("reaction_ms", 13, "ms")):
        player[field] = decimal(raw[column], field, unit=unit)
    return player


def prepare(source, detail, aliases):
    if not isinstance(source, dict) or source.get("provider") != "perfect_match" or source.get("errors"):
        reject("invalid_capture", "来源标识不正确或采集包含读取错误")
    records, entries = source.get("records"), source.get("entries")
    if not isinstance(records, list) or not 1 <= len(records) <= 500 or not isinstance(entries, list):
        reject("invalid_capture", "采集须包含1—500张地图及可核对的赛事列表")
    match_id = integer(detail.get("match", {}).get("id"), "赛事ID")
    if match_id < 1:
        reject("invalid_match", "赛事ID必须为正整数")
    resolve, names = build_name_resolver(detail, aliases)
    listed = {}
    for entry in entries:
        room = text_value(entry.get("id"), "列表房间号")
        if room in listed:
            reject("duplicate_source_map", "来源列表房间号重复", external_match_id=room)
        listed[room] = entry
    seen, prepared, map_evidence = set(), [], []
    for original in records:
        record = dict(original)
        room = text_value(record.get("external_match_id"), "来源地图房间号")
        if room in seen or room not in listed:
            reject("duplicate_or_unlisted_map", "详情房间重复或不在来源列表中", external_match_id=room)
        seen.add(room)
        record["external_match_id"] = room
        entry = listed[room]
        pair = (resolve(record.get("source_team1_name")), resolve(record.get("source_team2_name")))
        if pair[0] == pair[1]:
            reject("same_team", "地图双方关联为同一队伍", external_match_id=room)
        scores = (integer(record.get("team1_score"), "地图TEAM1得分"), integer(record.get("team2_score"), "地图TEAM2得分"))
        if scores[0] == scores[1]:
            reject("unfinished_or_forfeit_map", "地图比分未决，不能导入真实选手统计", external_match_id=room)
        if normalized_name(record.get("cup_name")) != normalized_name(source.get("cup_name")):
            reject("cup_mismatch", "详情与列表赛事名称不一致", external_match_id=room)
        listed_scores = re.fullmatch(r"\s*([0-9]+)\s*[:：]\s*([0-9]+)\s*", text_value(entry.get("score"), "列表比分"))
        if (not listed_scores or tuple(map(int, listed_scores.groups())) != scores
                or (resolve(entry.get("team1")), resolve(entry.get("team2"))) != pair
                or entry.get("url") != record.get("source_url")
                or entry.get("map") != record.get("map_name")):
            reject("list_detail_mismatch", "列表与详情的队伍、比分、地图或来源链接不一致", external_match_id=room)
        for key in ("label", "listed_time"):
            if key in record and record[key] != entry.get(key):
                reject("list_detail_mismatch", "列表与详情的地图标签或显示时间不一致", external_match_id=room)
            record[key] = entry.get(key, "")
        raw_players = record.get("players_raw")
        if not isinstance(raw_players, list) or len(raw_players) < 2:
            reject("missing_players", "地图缺少双方实际选手统计", external_match_id=room)
        if any(not isinstance(row, list) or len(row) != 21 for row in raw_players):
            reject("invalid_player_columns", "选手行列数不完整", external_match_id=room)
        external_ids = sorted({text_value(row[3], "外部队伍ID") for row in raw_players})
        if len(external_ids) != 2:
            reject("invalid_external_teams", "每图必须恰好有两个外部队伍ID", external_match_id=room)
        record.update(_team_ids=pair, _scores=scores, _external_team_ids=external_ids)
        check = check_round_evidence(record, pair, scores, resolve)
        prepared.append(record)
        map_evidence.append({"external_match_id": room, "source_url": record["source_url"],
            "team1_id": pair[0], "team2_id": pair[1], "team1_name": names[pair[0]], "team2_name": names[pair[1]],
            "source_team_ids": external_ids, "source_player_rows": len(raw_players), "raw_columns_per_row": 21,
            "label": record["label"], "listed_time_uninterpreted": record["listed_time"],
            "round_evidence": check})
    if seen != set(listed):
        reject("capture_incomplete", "列表中仍有未采集的地图", missing_rooms=sorted(set(listed) - seen))
    mapping, mapping_rows = resolve_external_teams(prepared, names)
    maps = []
    for record, evidence in zip(prepared, map_evidence):
        room, pair = record["external_match_id"], record["_team_ids"]
        players = []
        for number, raw in enumerate(record["players_raw"], 1):
            local_id = mapping[raw[3].strip()]
            slot = 1 if local_id == pair[0] else 2
            players.append(parse_player(raw, slot, room, number))
        if len({player["steam_id"] for player in players}) != len(players):
            reject("duplicate_player", "同图UID重复", external_match_id=room)
        if not any(player["kills"] or player["deaths"] or player["assists"] for player in players):
            reject("empty_player_stats", "整图选手数据均为0，不能当作完整统计", external_match_id=room)
        index = parse_map_index(record["label"])
        link, link_evidence = round_link(record, detail.get("rounds", []), index)
        evidence["round_link"] = {**link_evidence, **link}
        evidence["mapped_sources"] = [{"source_team_id": key, "team_id": mapping[key],
            "team_slot": 1 if mapping[key] == pair[0] else 2} for key in record["_external_team_ids"]]
        maps.append({"provider": "perfect_match", "external_match_id": room,
            "source_url": record["source_url"], "cup_name": record["cup_name"], "map_name": record["map_name"],
            "source_team1_name": record["source_team1_name"], "source_team2_name": record["source_team2_name"],
            "team1_id": pair[0], "team2_id": pair[1], "team1_score": record["_scores"][0],
            "team2_score": record["_scores"][1], **link, "players": players})
    payload = {"match_id": match_id, "provider": "perfect_match", "maps": maps}
    evidence = {"status": "candidate_requires_independent_review", "match_id": match_id,
        "mapping_method": "opponent-set intersection and per-map inequality propagation",
        "identity_assumption": "source_team_id is stable within this captured cup",
        "never_used_for_assignment": ["camp", "row_order", "nickname"],
        "summary": {"map_count": len(maps), "player_rows": sum(len(item["players"]) for item in maps),
            "unique_players": len({player["steam_id"] for item in maps for player in item["players"]}),
            "external_teams": len(mapping), "linked_maps": sum(item["round_id"] is not None for item in maps),
            "unlinked_maps": sum(item["round_id"] is None for item in maps)},
        "team_mapping": mapping_rows, "maps": map_evidence,
        "notices": ["这是离线候选，不代表已导入数据库或发布。", "平台显示时间原样保留，未当作正式开赛时间。",
            "无法唯一关联的历史对阵留空，不按图号、日期或昵称猜场次。"]}
    return payload, evidence


def validate_payload_contract(payload):
    backend = str(Path(__file__).resolve().parents[1] / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.schemas.player_stats import ImportedStatsMap
    validated = []
    for item in payload["maps"]:
        try:
            validated.append(ImportedStatsMap.model_validate(item).model_dump())
        except ValueError:
            reject("import_contract_invalid", "候选地图未通过受控导入契约", external_match_id=item.get("external_match_id"))
    return {**payload, "maps": validated}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "match-detail", "team-aliases", "output", "evidence-output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = (args.source, args.match_detail, args.team_aliases)
    outputs = (args.output, args.evidence_output)
    if len({path.resolve() for path in (*inputs, *outputs)}) != 5:
        parser.error("输入、候选输出和证据输出必须是不同文件")
    if any(path.exists() for path in outputs):
        parser.error("输出已存在，拒绝覆盖；请使用新的候选文件名")
    try:
        payload, evidence = prepare(*(load_json(path) for path in inputs))
        payload = validate_payload_contract(payload)
        evidence["input_sha256"] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in zip(("source", "match_detail", "team_aliases"), inputs)}
        evidence["payload_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        write_json(args.evidence_output, evidence)
        try:
            write_json(args.output, payload)
        except Exception:
            # This path was created by this invocation only; never delete an old artifact.
            args.evidence_output.unlink()
            raise
    except PreparationError as error:
        if not args.evidence_output.exists():
            write_json(args.evidence_output, error.evidence)
        print(json.dumps(error.evidence, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps({"status": evidence["status"], **evidence["summary"],
        "output": str(args.output), "evidence_output": str(args.evidence_output)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
