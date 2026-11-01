"""Pure offline preparation fixtures; no .env, database, browser or network access."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("prepare_perfect_stats", ROOT / "tools/prepare-perfect-stats.py")
prepare_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_tool)


def raw_player(team):
    return ["7656119800000000" + str(team), "Nickname " + str(team),
            "土匪" if team % 2 else "警察", "external-" + str(team),
            "15", "8", "4", "2", "54.5%", "40%", "16.4", "1.24", "98.5", "356ms",
            "2", "1", "0", "0", "0", "0", "0"]


def round_evidence(first, second, scores):
    first_wins, second_wins = scores
    rows = [["tkill"] * first_wins + [""] * second_wins,
            [""] * first_wins + ["ctkill"] * second_wins]
    for row in rows:
        if len(row) > 12:
            row.insert(12, "divide")
    return {"teams": [first, second], "rows": rows}


def fixture():
    names = {1: "Team A", 2: "Team B", 3: "Team C", 4: "Team D"}
    records, entries, rounds = [], [], []
    for index, pair in enumerate(((1, 2), (1, 3), (2, 3)), 1):
        first, second = pair
        room = str(100 + index)
        record = {"external_match_id": room, "source_url": "https://match.wmpvp.com/csgo/detail/" + room,
            "source_team1_name": names[first], "source_team2_name": names[second],
            "team1_score": 13, "team2_score": 5, "map_name": "de_mirage", "cup_name": "Test Cup",
            # The opponent deliberately appears first; camp is irrelevant to identity.
            "players_raw": [raw_player(second), raw_player(first)],
            "round_evidence": round_evidence(names[first], names[second], (13, 5))}
        records.append(record)
        entries.append({"id": room, "team1": names[first], "team2": names[second], "score": "13:5",
            "map": "de_mirage", "url": record["source_url"], "label": "查看详情", "listed_time": "2026.10.10 13:00"})
        rounds.append({"id": index, "status": "finished", "group_name": "A",
            "team1_id": second if index == 1 else first, "team2_id": first if index == 1 else second,
            "team1_score": 5 if index == 1 else 13, "team2_score": 13 if index == 1 else 5, "bo3_scores": None})
    return ({"provider": "perfect_match", "cup_name": "Test Cup", "records": records, "entries": entries, "errors": []},
            {"match": {"id": 8}, "teams": [{"team_id": key, "team_name": value} for key, value in names.items()],
             "rounds": rounds}, {})


class PreparePerfectStatsTests(unittest.TestCase):
    def test_constraints_resolve_unique_teams_without_camp_names_or_row_order(self):
        source, detail, aliases = fixture()
        for record in source["records"]:
            for player in record["players_raw"]:
                player[1] = "Deliberately unrelated nickname"
                player[2] = "UNTRUSTED SIDE LABEL"
        payload, evidence = prepare_tool.prepare(source, detail, aliases)
        mapping = {row["source_team_id"]: row["team_id"] for row in evidence["team_mapping"]}
        self.assertEqual(mapping, {"external-1": 1, "external-2": 2, "external-3": 3})
        self.assertEqual(payload["maps"][0]["players"][0]["team_slot"], 2)
        self.assertEqual(payload["maps"][0]["players"][1]["team_slot"], 1)
        self.assertEqual([row["round_id"] for row in payload["maps"]], [1, 2, 3])
        self.assertEqual(evidence["summary"]["player_rows"], 6)
        self.assertEqual(evidence["summary"]["unique_players"], 3)
        self.assertEqual(evidence["status"], "candidate_requires_independent_review")
        for row in evidence["team_mapping"]:
            self.assertEqual(row["final_candidates"], [row["team_id"]])
            self.assertTrue(row["observations"])

    def test_per_map_propagation_and_multiple_external_ids_may_represent_one_team(self):
        source, detail, aliases = fixture()
        source["records"][2]["players_raw"][1][3] = "recreated-team-2"
        _, evidence = prepare_tool.prepare(source, detail, aliases)
        mapping = {row["source_team_id"]: row for row in evidence["team_mapping"]}
        self.assertEqual(mapping["external-2"]["candidate_intersection"], [1, 2])
        self.assertEqual(mapping["external-2"]["final_candidates"], [2])
        self.assertEqual(mapping["recreated-team-2"]["candidate_intersection"], [2, 3])
        self.assertEqual(mapping["recreated-team-2"]["final_candidates"], [2])

    def test_single_pair_ambiguity_and_conflicting_external_reuse_are_rejected(self):
        source, detail, aliases = fixture()
        source["records"] = source["records"][:1]
        source["entries"] = source["entries"][:1]
        with self.assertRaises(prepare_tool.PreparationError) as error:
            prepare_tool.prepare(source, detail, aliases)
        self.assertEqual(error.exception.evidence["code"], "external_team_unresolved")
        self.assertTrue(error.exception.evidence["team_mapping"])
        source, detail, aliases = fixture()
        record, entry = source["records"][2], source["entries"][2]
        record["source_team1_name"] = entry["team1"] = "Team D"
        record["players_raw"][1][3] = "external-1"
        record["round_evidence"]["teams"][0] = "Team D"
        with self.assertRaises(prepare_tool.PreparationError) as error:
            prepare_tool.prepare(source, detail, aliases)
        self.assertEqual(error.exception.evidence["code"], "external_team_conflict")

    def test_alias_and_unicode_normalization_require_unique_resolutions(self):
        source, detail, aliases = fixture()
        source["records"][0]["source_team1_name"] = source["entries"][0]["team1"] = "Ｔｅａｍ－Ａ"
        source["records"][1]["source_team1_name"] = source["entries"][1]["team1"] = "来源阿"
        payload, _ = prepare_tool.prepare(source, detail, {"来源阿": 1})
        self.assertEqual(payload["maps"][0]["team1_id"], 1)
        with self.assertRaises(prepare_tool.PreparationError):
            prepare_tool.prepare(source, detail, {"来源阿": 999})
        detail["teams"].append({"team_id": 5, "team_name": "Team-A"})
        source["records"][0]["source_team1_name"] = source["entries"][0]["team1"] = "ＴＥＡＭＡ"
        with self.assertRaises(prepare_tool.PreparationError) as error:
            prepare_tool.prepare(source, detail, {"来源阿": 1})
        self.assertEqual(error.exception.evidence["code"], "ambiguous_team_name")

    def test_round_icon_validation_rejects_unknown_missing_duplicate_and_misaligned_data(self):
        mutators = [
            lambda record: record["round_evidence"]["rows"][0].__setitem__(0, "unknown-icon"),
            lambda record: record["round_evidence"]["rows"][0].__setitem__(0, ""),
            lambda record: record["round_evidence"]["rows"][1].__setitem__(0, "ctkill"),
            lambda record: record["round_evidence"]["rows"][1].__setitem__(12, ""),
            lambda record: record["round_evidence"]["rows"][0].append(""),
        ]
        for mutate in mutators:
            source, detail, aliases = fixture()
            mutate(source["records"][0])
            with self.subTest(mutate=mutate), self.assertRaises(prepare_tool.PreparationError):
                prepare_tool.prepare(source, detail, aliases)
        source, detail, aliases = fixture()
        source["records"][0]["round_evidence"]["rows"][0][0] = "timeout"
        self.assertTrue(prepare_tool.prepare(source, detail, aliases)[1]["maps"][0]["round_evidence"]["verified"])

    def test_numeric_units_optional_nulls_uid_preservation_and_invalid_required_counts(self):
        source, detail, aliases = fixture()
        row = source["records"][0]["players_raw"][0]
        row[9], row[13], row[11] = "４０％", "356 ms", "--"
        player = prepare_tool.prepare(source, detail, aliases)[0]["maps"][0]["players"][0]
        self.assertEqual((player["hit_percent"], player["hs_percent"], player["reaction_ms"]), (54.5, 40.0, 356.0))
        self.assertIsNone(player["rating"])
        self.assertIsInstance(player["steam_id"], str)
        for index, value in ((0, 76561198000000002), (4, "--"), (12, "NaN")):
            broken = deepcopy(source)
            broken["records"][0]["players_raw"][0][index] = value
            with self.subTest(index=index), self.assertRaises(prepare_tool.PreparationError):
                prepare_tool.prepare(broken, detail, aliases)

    def test_bo3_index_and_direction_must_match_finished_existing_map_not_pending_final(self):
        source, detail, aliases = fixture()
        source["entries"][0]["label"] = "查看详情（BO3第二场）"
        detail["rounds"][0].update(group_name="淘汰赛", team1_score=2, team2_score=1,
            bo3_scores=[{"t1": 13, "t2": 2}, {"t1": 5, "t2": 13}, {"t1": 13, "t2": 4}])
        payload, _ = prepare_tool.prepare(source, detail, aliases)
        self.assertEqual((payload["maps"][0]["round_id"], payload["maps"][0]["map_index"]), (1, 2))
        detail["rounds"][0]["status"] = "pending"
        payload, evidence = prepare_tool.prepare(source, detail, aliases)
        self.assertIsNone(payload["maps"][0]["round_id"])
        self.assertIsNone(payload["maps"][0]["map_index"])
        self.assertEqual(evidence["maps"][0]["round_link"]["source_map_index"], 2)

    def test_repeated_equal_bo1_results_do_not_guess_round_or_mutate_the_fixture(self):
        source, detail, aliases = fixture()
        duplicate = deepcopy(detail["rounds"][0])
        duplicate["id"] = 100
        detail["rounds"].append(duplicate)
        original_source, original_detail = deepcopy(source), deepcopy(detail)
        payload, evidence = prepare_tool.prepare(source, detail, aliases)
        self.assertIsNone(payload["maps"][0]["round_id"])
        self.assertIsNone(payload["maps"][0]["map_index"])
        self.assertEqual(evidence["maps"][0]["round_link"]["candidate_round_ids"], [1, 100])
        self.assertEqual(source, original_source)
        self.assertEqual(detail, original_detail)

    def test_list_metadata_capture_errors_and_duplicate_uids_reject_whole_candidate(self):
        variants = [
            lambda source: source["errors"].append("read error"),
            lambda source: source["entries"][0].update(score="12:5"),
            lambda source: source["records"].pop(),
            lambda source: source["records"][0]["players_raw"][0].pop(),
            lambda source: source["records"][0]["players_raw"][0].__setitem__(0, source["records"][0]["players_raw"][1][0]),
        ]
        for mutate in variants:
            source, detail, aliases = fixture()
            mutate(source)
            with self.subTest(mutate=mutate), self.assertRaises(prepare_tool.PreparationError):
                prepare_tool.prepare(source, detail, aliases)

    def test_cli_creates_candidate_only_validates_contract_and_never_overwrites_outputs(self):
        source, detail, aliases = fixture()
        with tempfile.TemporaryDirectory(prefix="perfect-preparation-test-") as directory:
            directory = Path(directory)
            paths = [directory / name for name in ("source.json", "detail.json", "aliases.json", "payload.json", "evidence.json")]
            for path, value in zip(paths, (source, detail, aliases)):
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            command = [sys.executable, "-B", str(ROOT / "tools/prepare-perfect-stats.py")]
            for name, path in zip(("source", "match-detail", "team-aliases", "output", "evidence-output"), paths):
                command.extend(("--" + name, str(path)))
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(paths[3].read_text(encoding="utf-8"))["maps"][0]["players"][0]["team_slot"], 2)
            evidence = json.loads(paths[4].read_text(encoding="utf-8"))
            self.assertEqual(evidence["status"], "candidate_requires_independent_review")
            self.assertIn("payload_sha256", evidence)
            old = paths[3].read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=30).returncode, 0)
            self.assertEqual(paths[3].read_bytes(), old)

    def test_rejected_cli_writes_diagnostics_but_no_import_payload(self):
        source, detail, aliases = fixture()
        source["records"] = source["records"][:1]
        source["entries"] = source["entries"][:1]
        with tempfile.TemporaryDirectory(prefix="perfect-rejected-test-") as directory:
            directory = Path(directory)
            paths = [directory / name for name in ("source.json", "detail.json", "aliases.json", "payload.json", "evidence.json")]
            for path, value in zip(paths, (source, detail, aliases)):
                path.write_text(json.dumps(value), encoding="utf-8")
            command = [sys.executable, "-B", str(ROOT / "tools/prepare-perfect-stats.py")]
            for name, path in zip(("source", "match-detail", "team-aliases", "output", "evidence-output"), paths):
                command.extend(("--" + name, str(path)))
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(paths[3].exists())
            evidence = json.loads(paths[4].read_text(encoding="utf-8"))
            self.assertEqual(evidence["status"], "rejected")
            self.assertEqual(evidence["code"], "external_team_unresolved")


if __name__ == "__main__":
    unittest.main()
