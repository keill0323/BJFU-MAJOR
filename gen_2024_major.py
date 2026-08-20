# -*- coding: utf-8 -*-
"""生成 2024 北林 CS2 Major 完整赛事数据 SQL
16 队（6 支现有测试队 + 10 支新测试队），完整赛事过程：
挑战者组小组赛(A/B/C/D) -> 传奇组(上区/下区) -> 淘汰赛BO3(1/4/半/决赛)
"""
import json

MATCH_ID = 3  # 2024 北林 CS2 Major

# ============ 现有 6 支测试队（用户 16-45） ============
# 队 id: (队名, 队长, [成员], rating)
existing_teams = [
    (100, "北林狼王", 16, [16, 17, 18, 19, 20], 530),
    (101, "林间狙神", 21, [21, 22, 23, 24, 25], 524),
    (102, "校园枪王", 26, [26, 27, 28, 29, 30], 553),
    (103, "银枪不倒", 31, [31, 32, 33, 34, 35], 482),
    (104, "白给小队", 36, [36, 37, 38, 39, 40], 431),
    (105, "北林之光", 41, [41, 42, 43, 44, 45], 556),
]

# ============ 新 10 支测试队（用户 46-95） ============
# 每队 5 人 rating 之和 = 队 rating
new_teams_meta = [
    ("森之卫队",   480, [100, 100, 95, 95, 90]),
    ("银杏狙击",   470, [100, 95, 95, 90, 90]),
    ("田家炳枪神", 460, [95, 95, 95, 90, 85]),
    ("学研猛男",   450, [95, 95, 90, 90, 80]),
    ("林业机师",   440, [95, 90, 90, 85, 80]),
    ("森工特战队", 430, [90, 90, 85, 85, 80]),
    ("生物圈枪王", 420, [90, 85, 85, 80, 80]),
    ("园林神射",   410, [85, 85, 80, 80, 80]),
    ("水保突击",   400, [85, 80, 80, 80, 75]),
    ("经管雄鹰",   390, [80, 80, 80, 75, 75]),
]

# 昵称池（50 个）
nicknames = [
    "森之狐", "绿茵狙", "树海枪", "松针", "林间风", "银杏叶", "金秋神", "白蜡木", "满穗", "黄栌",
    "田家炳", "枪火", "突击手", "守门员", "后座力", "学研楼", "猛男", "自习侠", "科研狗", "熬夜王",
    "林业机", "伐木工", "锯木机", "育苗师", "松果", "森工队", "特战员", "护林员", "瞭望塔", "防火线",
    "生物圈", "枪王", "显微镜", "培养皿", "基因棒", "园林师", "神射手", "园艺剪", "剪枝刀", "花匠",
    "水保员", "突击队", "水土派", "泥沙控", "涵养林", "经管人", "雄鹰", "账本", "博弈论", "操盘手",
]

def rating_to_rank(r):
    """rating -> 段位字符串（近似 S 段）"""
    if r >= 100:
        return "S50"
    n = round((r - 40) * 49 / 60) + 1
    n = max(1, min(50, n))
    return f"S{n}"


lines = []
def w(s):
    lines.append(s)

w("-- ============ 2024 北林 CS2 Major 赛事数据 ============")
w("SET FOREIGN_KEY_CHECKS=0;")
w("")

# ============ 1. 新用户 46-95 ============
w("-- 新测试用户 46-95")
uid = 46
new_teams = []  # (team_id, name, captain, members, rating)
nick_idx = 0
for ti, (name, team_rating, ratings) in enumerate(new_teams_meta):
    team_id = 108 + ti
    members = []
    for k in range(5):
        r = ratings[k]
        rank = rating_to_rank(r)
        nickname = nicknames[nick_idx % len(nicknames)]
        nick_idx += 1
        game_id = f"5e_10{uid}"
        student = f"2024{uid:04d}"
        w(f"INSERT INTO users (id, wx_openid, nickname, game_id, student_id, is_verified, role, individual_rating, `rank`) "
          f"VALUES ({uid}, 'wx_fake_{uid}', '{nickname}', '{game_id}', '{student}', 1, 'user', {r}, '{rank}');")
        members.append(uid)
        uid += 1
    new_teams.append((team_id, name, members[0], members, team_rating))

w("")

# ============ 2. 新队伍 108-117 ============
w("-- 新测试队伍 108-117")
for team_id, name, captain, members, team_rating in new_teams:
    w(f"INSERT INTO teams (id, name, captain_id, status, rating) "
      f"VALUES ({team_id}, '{name}', {captain}, 'APPROVED', {team_rating});")
w("")

# ============ 3. 新队伍成员 ============
w("-- 新队伍成员")
for team_id, name, captain, members, team_rating in new_teams:
    for i, m in enumerate(members):
        role = 'CAPTAIN' if m == captain else 'MEMBER'
        w(f"INSERT INTO team_members (team_id, user_id, role) VALUES ({team_id}, {m}, '{role}');")
w("")

# ============ 4. 全部 16 队汇总 ============
all_teams = []
for team_id, name, captain, members, team_rating in existing_teams:
    all_teams.append({"id": team_id, "name": name, "captain": captain, "members": members, "rating": team_rating})
for team_id, name, captain, members, team_rating in new_teams:
    all_teams.append({"id": team_id, "name": name, "captain": captain, "members": members, "rating": team_rating})

# 按 rating 排序，分配种子
all_teams_sorted = sorted(all_teams, key=lambda t: -t["rating"])
for i, t in enumerate(all_teams_sorted, 1):
    t["seed"] = i

# ============ 5. 报名 + 进度 ============
w("-- 16 队报名 2024 major + 队伍进度（前4直升传奇组）")
for t in all_teams_sorted:
    seed = t["seed"]
    if seed <= 4:
        stage = "LEGEND"
        group = None
    else:
        stage = "CHALLENGER"
        group = None
    w(f"INSERT INTO team_progress (match_id, team_id, stage, group_name, seed) "
      f"VALUES ({MATCH_ID}, {t['id']}, '{stage}', NULL, {seed});")
    for m in t["members"]:
        w(f"INSERT INTO registrations (match_id, team_id, user_id, status) "
          f"VALUES ({MATCH_ID}, {t['id']}, {m}, 'APPROVED');")
w("")

# ============ 6. 挑战者组分组（12 队蛇形分 A/B/C/D） ============
challengers = [t for t in all_teams_sorted if t["seed"] > 4]
groups = {"A": [], "B": [], "C": [], "D": []}
group_names = ["A", "B", "C", "D"]
for i, t in enumerate(challengers):
    gi = i % 4
    if (i // 4) % 2 == 1:
        gi = 3 - gi
    groups[group_names[gi]].append(t)

# 写分组到 team_progress
w("-- 挑战者组蛇形分组")
for g, ts in groups.items():
    for t in ts:
        w(f"UPDATE team_progress SET group_name='{g}' WHERE match_id={MATCH_ID} AND team_id={t['id']};")
w("")

round_num = 1
def add_round(team1, team2, group, s1, s2):
    """生成一条已结束对阵（单局）"""
    global round_num
    winner = team1["id"] if s1 > s2 else team2["id"]
    w(f"INSERT INTO match_rounds (match_id, round_number, team1_id, team2_id, team1_score, team2_score, winner_id, status, group_name) "
      f"VALUES ({MATCH_ID}, {round_num}, {team1['id']}, {team2['id']}, {s1}, {s2}, {winner}, 'FINISHED', '{group}');")
    round_num += 1

def add_bo3(team1, team2, group, games):
    """生成一条已结束淘汰赛 BO3 对阵
    games: [(t1, t2), ...] 每局小分
    """
    global round_num
    s1 = sum(1 for g in games if g[0] > g[1])
    s2 = sum(1 for g in games if g[0] < g[1])
    winner = team1["id"] if s1 > s2 else team2["id"]
    bo3 = json.dumps([{"t1": g[0], "t2": g[1]} for g in games], ensure_ascii=False)
    w(f"INSERT INTO match_rounds (match_id, round_number, team1_id, team2_id, team1_score, team2_score, winner_id, status, group_name, bo3_scores) "
      f"VALUES ({MATCH_ID}, {round_num}, {team1['id']}, {team2['id']}, {s1}, {s2}, {winner}, 'FINISHED', '{group}', '{bo3}');")
    round_num += 1

# ============ 7. 小组赛单循环（每组 3 队，3 场，共 12 场） ============
w("-- 挑战者组小组赛（A/B/C/D 每组单循环）")
group_results = {}  # 每组 -> 晋级名单（按小组排名）
for g, ts in groups.items():
    # ts 已按 seed 排序（蛇形后每组内顺序可能乱，重新按 seed 排）
    ts = sorted(ts, key=lambda t: t["seed"])
    t1, t2, t3 = ts
    # seed 小者胜；制造一点比分差异
    add_round(t1, t2, g, 13, 9)
    add_round(t1, t3, g, 13, 7)
    add_round(t2, t3, g, 13, 10)
    # 小组前 2 晋级：t1(seed最小), t2
    group_results[g] = [t1, t2]
w("")

# ============ 8. 传奇组（4 种子 + 8 晋级 = 12 队，分上区/下区） ============
legends_seed = [t for t in all_teams_sorted if t["seed"] <= 4]
qualified = [group_results["A"][0], group_results["A"][1],
             group_results["B"][0], group_results["B"][1],
             group_results["C"][0], group_results["C"][1],
             group_results["D"][0], group_results["D"][1]]

# 上区：种子1、3 + A/B组晋级；下区：种子2、4 + C/D组晋级
upper = [legends_seed[0], legends_seed[2],
         group_results["A"][0], group_results["A"][1],
         group_results["B"][0], group_results["B"][1]]
lower = [legends_seed[1], legends_seed[3],
         group_results["C"][0], group_results["C"][1],
         group_results["D"][0], group_results["D"][1]]

# 写传奇组分区 + 晋级者 stage 更新
w("-- 传奇组分区（上区/下区）")
for t in upper:
    w(f"UPDATE team_progress SET stage='LEGEND', group_name='上区' WHERE match_id={MATCH_ID} AND team_id={t['id']};")
for t in lower:
    w(f"UPDATE team_progress SET stage='LEGEND', group_name='下区' WHERE match_id={MATCH_ID} AND team_id={t['id']};")
# 小组赛未晋级的 4 队标记淘汰
advanced_ids = {t["id"] for t in upper + lower}
for t in challengers:
    if t["id"] not in advanced_ids:
        w(f"UPDATE team_progress SET stage='ELIMINATED' WHERE match_id={MATCH_ID} AND team_id={t['id']};")
w("")

# ============ 9. 传奇组循环赛（每区 6 队单循环，15 场/区，共 30 场） ============
w("-- 传奇组循环赛（上区/下区）")
def legend_rounds(zone, ts):
    ts = sorted(ts, key=lambda t: t["seed"])
    # 单循环，seed 小者胜
    for i in range(len(ts)):
        for j in range(i + 1, len(ts)):
            add_round(ts[i], ts[j], zone, 13, 8)

legend_rounds("上区", upper)
legend_rounds("下区", lower)
w("")

# ============ 10. 淘汰赛（每区前 3 晋级，共 6 队） ============
upper_sorted = sorted(upper, key=lambda t: t["seed"])
lower_sorted = sorted(lower, key=lambda t: t["seed"])
# 每区前 3 进淘汰赛（6 队循环后取前 3）
playoff_upper = upper_sorted[:3]
playoff_lower = lower_sorted[:3]

# 淘汰的传奇组队伍（每区后 3）
for t in upper_sorted[3:] + lower_sorted[3:]:
    w(f"UPDATE team_progress SET stage='ELIMINATED' WHERE match_id={MATCH_ID} AND team_id={t['id']};")

# 淘汰赛对阵（前端解析顺序：1/4 x2, 半决赛 x2, 决赛 x1）
w("-- 淘汰赛（BO3）")
# 上区：第1 轮空进半决赛；第2 vs 第3
u1, u2, u3 = playoff_upper
l1, l2, l3 = playoff_lower

# 1/4 决赛（2 场）
add_bo3(u2, u3, "淘汰赛", [(13, 10), (9, 13), (13, 11)])  # u2 胜 2-1
add_bo3(l2, l3, "淘汰赛", [(13, 7), (13, 9)])            # l2 胜 2-0
# 半决赛（2 场）
add_bo3(u1, u2, "淘汰赛", [(13, 11), (11, 13), (13, 9)])  # u1 胜 2-1
add_bo3(l1, l2, "淘汰赛", [(13, 6), (13, 8)])            # l1 胜 2-0
# 决赛（1 场）
add_bo3(u1, l1, "淘汰赛", [(13, 9), (10, 13), (13, 7)])  # u1 冠军 2-1

# 淘汰赛队伍 stage 更新
for t in playoff_upper + playoff_lower:
    w(f"UPDATE team_progress SET stage='PLAYOFF' WHERE match_id={MATCH_ID} AND team_id={t['id']};")
w("")

w("SET FOREIGN_KEY_CHECKS=1;")
w("")
w("-- 更新自增（避免与显式插入的 id 冲突）")
w("ALTER TABLE users AUTO_INCREMENT = 96;")
w("ALTER TABLE teams AUTO_INCREMENT = 118;")

# 输出
out = "\n".join(lines) + "\n"
with open("gen_2024_major.sql", "w", encoding="utf-8") as f:
    f.write(out)
print(f"生成完成：{len(lines)} 行 SQL")
print("冠军：", upper_sorted[0]["name"])
