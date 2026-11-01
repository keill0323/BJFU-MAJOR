"""只读聚合全赛事进度与整场胜负，不用截断对阵猜冠军或奖金名次。"""
import json

from fastapi import HTTPException
from sqlalchemy import or_

from app.models.match import MatchRound, Registration, RegistrationStatus, TeamProgress
from app.models.team import Team
from app.models.user import User


MAX_TEAMS = 200
MAX_ROUNDS = 5000
MAX_TOOL_BYTES = 15500


def _value(value):
    return getattr(value, 'value', value)


def _participants(round_):
    return {value for value in (round_.team1_id, round_.team2_id) if value is not None}


def _valid_result(round_):
    """BO3比分是地图胜场，整场只算一次；轮空永远不是已打胜场。"""
    if (_value(round_.status) != 'finished' or round_.team1_id is None or round_.team2_id is None
            or round_.team1_id == round_.team2_id):
        return False
    left, right = round_.team1_score, round_.team2_score
    if type(left) is not int or type(right) is not int or min(left, right) < 0 or left == right:
        return False
    if round_.winner_id != (round_.team1_id if left > right else round_.team2_id):
        return False
    return round_.group_name != '淘汰赛' or (max(left, right) == 2 and min(left, right) in (0, 1))


def _verified_final(rounds):
    """与荣誉页同一六强交叉路径校验，纯内存运行，无锁、无写入。"""
    if len(rounds) != 5 or len({row.round_number for row in rounds}) != 5 or not all(_valid_result(row) for row in rounds):
        return None
    q1, q2, s1, s2, final = rounds
    quarter_ids = _participants(q1) | _participants(q2)
    semi1, semi2 = _participants(s1), _participants(s2)
    if len(quarter_ids) != 4 or q2.winner_id not in semi1 or q1.winner_id not in semi2:
        return None
    bye1, bye2 = semi1 - {q2.winner_id}, semi2 - {q1.winner_id}
    if len(bye1 | bye2) != 2 or (bye1 | bye2) & quarter_ids:
        return None
    if _participants(final) != {s1.winner_id, s2.winner_id}:
        return None
    return final


def _record(team_id, rounds, public_text):
    totals = {'wins': 0, 'losses': 0, 'pending': 0, 'unresolved': 0, 'byes': 0}
    groups = {}
    for round_ in rounds:
        if team_id not in _participants(round_):
            continue
        group = round_.group_name or ''
        if group not in groups:
            groups[group] = {'group_name': public_text(group), 'format': 'BO3' if group == '淘汰赛' else 'BO1',
                             'wins': 0, 'losses': 0, 'pending': 0, 'unresolved': 0, 'byes': 0}
        if round_.team1_id is None or round_.team2_id is None:
            outcome = 'byes'
        elif _valid_result(round_):
            outcome = 'wins' if round_.winner_id == team_id else 'losses'
        else:
            outcome = 'unresolved' if _value(round_.status) == 'finished' else 'pending'
        totals[outcome] += 1
        groups[group][outcome] += 1
    # 把同一历史组/当前组都保留，不把不同计分单位相加成所谓净胜分。
    totals['stages'] = list(groups.values())[-12:]
    totals['stage_group_count'] = len(groups)
    totals['stage_records_complete'] = len(groups) <= 12
    return totals


def _stage(team_id, progress, rounds, knockout, qualifiers, final):
    own = [row for row in rounds if team_id in _participants(row)]
    stage = _value(progress.stage) if progress else None
    group = progress.group_name if progress else None
    order, label = 7, '挑战者组'
    eliminated = stage == 'eliminated'
    if stage == 'legend' or group in ('上区', '下区') or any(row.group_name in ('上区', '下区') for row in own):
        order, label = 5, '传奇组'
    elif group == '附加赛' or any(row.group_name == '附加赛' for row in own):
        order, label = 6, '附加赛'
    if stage == 'playoff' or group == '淘汰赛' or any(team_id in _participants(row) for row in knockout):
        order, label = 4, '六强'
    quarter, semi = knockout[:2], knockout[2:4]
    quarter_ids = set().union(*(_participants(row) for row in quarter)) if quarter else set()
    has_byes = (len(quarter) == 2 and len(quarter_ids) == 4 and len(qualifiers) == 6
                and quarter_ids <= qualifiers)
    if (any(team_id in _participants(row) for row in semi)
            or any(_valid_result(row) and row.winner_id == team_id for row in quarter)
            or (has_byes and team_id in qualifiers and team_id not in quarter_ids)):
        order, label = 3, '四强'
    # 即使决赛尚未生成，半决赛的有效赢家已进入决赛。
    if any(_valid_result(row) and row.winner_id == team_id for row in semi):
        order, label = 2, '决赛'
    last = next((row for row in reversed(knockout) if team_id in _participants(row)), None)
    if last is not None and _valid_result(last):
        eliminated = last.winner_id != team_id
    if final is not None and team_id in _participants(final):
        order, label = (0, '冠军') if final.winner_id == team_id else (1, '亚军')
    seed = progress.seed if progress else 0
    if (stage == 'challenger' and not seed and not group and not own) or (stage is None and not own):
        order, label = 8, '待分组'
    return order, label, eliminated


def _bounded_limit(args):
    value = args.get('limit')
    value = 5 if value is None else value
    if type(value) is not int or not 1 <= value <= 20:
        raise HTTPException(422, '展示数量应为1至20')
    return value


def get_match_standings(db, actor, args):
    """actor由JWT编排器传入，排名和胜负均使用当前数据库记录。"""
    from app.services.assistant_query_service import _resolve_match, _visible_teams, _public_text, _response

    if not isinstance(args, dict) or set(args) - {'match_id', 'match_keyword', 'metric', 'limit'}:
        raise HTTPException(422, '排名工具参数无效')
    actor_id = getattr(actor, 'id', None)
    if type(actor_id) is not int or actor_id < 1:
        raise HTTPException(403, '请使用正常登录账号查询')
    metric = 'progression' if args.get('metric') is None else args['metric']
    if metric not in ('progression', 'match_wins'):
        raise HTTPException(422, '排名口径无效')
    limit = _bounded_limit(args)
    if (args.get('match_id') is not None and (type(args['match_id']) is not int or args['match_id'] < 1)
            or args.get('match_keyword') is not None and (not isinstance(args['match_keyword'], str)
                                                         or len(args['match_keyword']) > 100)):
        raise HTTPException(422, '赛事参数无效')
    with db.no_autoflush:
        if not db.query(User.id).filter(User.id == actor_id, User.is_hidden.is_(False)).first():
            raise HTTPException(403, '账号暂不可用')
        match, selection = _resolve_match(db, args)
        if selection:
            return selection
        progressed = db.query(TeamProgress.team_id).filter(TeamProgress.match_id == match.id)
        enrolled = db.query(Registration.team_id).filter(Registration.match_id == match.id,
                                                         Registration.status == RegistrationStatus.APPROVED)
        teams_query = _visible_teams(db).filter(or_(Team.id.in_(progressed), Team.id.in_(enrolled)))
        rounds_query = db.query(MatchRound).filter(MatchRound.match_id == match.id)
        total_teams, total_rounds = teams_query.count(), rounds_query.count()
        basis = ('赛事最高晋级阶段；同阶段并列，种子号仅稳定排列，不推断完整奖金名次'
                 if metric == 'progression' else '本赛事已录入的有效整场胜场；BO3整场只算一次，不用于晋级或奖金名次')
        base = {'match_id': match.id, 'match_name': _public_text(match.name), 'match_status': _value(match.status),
                'metric': metric, 'ranking_basis': basis, 'total_teams': total_teams, 'total_rounds': total_rounds,
                'scope_complete': True, 'record_scope': '公开可见且已批准的参赛队伍；当前赛事全部已录入对阵，轮空不计胜负；不同阶段不合并回合或地图净胜分',
                'bye_scope': 'byes仅计赛程显式记录的轮空；晋级免首轮不会虚构对阵或增加胜负'}
        if total_teams > MAX_TEAMS or total_rounds > MAX_ROUNDS:
            return _response('teams', '赛事进度与战绩', [], status='scope_incomplete',
                message='赛事记录超过本次安全聚合范围，不能完整判断哪队领先，请查看赛事页。',
                data={**base, 'scope_complete': False, 'champion_decided': False, 'items': [], 'leaders': [],
                      'leader_team_ids': [], 'truncated': True})
        teams = teams_query.all()
        progress_rows = db.query(TeamProgress).filter(TeamProgress.match_id == match.id).limit(MAX_TEAMS + 1).all()
        if len(progress_rows) > MAX_TEAMS:
            return _response('teams', '赛事进度与战绩', [], status='scope_incomplete',
                message='赛事进度记录超过聚合范围，暂不能确认完整排行。',
                data={**base, 'scope_complete': False, 'champion_decided': False, 'items': [], 'leaders': [],
                      'leader_team_ids': [], 'truncated': True})
        progress = {row.team_id: row for row in progress_rows}
        rounds = rounds_query.order_by(MatchRound.round_number, MatchRound.id).all()
        knockout = [row for row in rounds if row.group_name == '淘汰赛']
        final = _verified_final(knockout)
        qualifiers = {row.team_id for row in progress_rows if _value(row.stage) == 'playoff'}
        rows = []
        for team in teams:
            state = progress.get(team.id)
            order, label, eliminated = _stage(team.id, state, rounds, knockout, qualifiers, final)
            rows.append({'team_id': team.id, 'team_name': _public_text(team.name, 100),
                         'stage_text': label, 'stage_order': order, 'eliminated': eliminated,
                         'group_name': _public_text(state.group_name, 100) if state else None,
                         'seed': state.seed if state else 0, **_record(team.id, rounds, _public_text)})
        seed_key = lambda row: (row['seed'] if row['seed'] and row['seed'] > 0 else float('inf'), row['team_id'])
        rows.sort(key=(lambda row: (row['stage_order'], *seed_key(row))) if metric == 'progression'
                  else (lambda row: (-row['wins'], *seed_key(row))))
        leaders = ([row for row in rows if row['stage_order'] == rows[0]['stage_order']] if rows and metric == 'progression'
                   else [row for row in rows if row['wins'] == rows[0]['wins']] if rows else [])
        if rows and metric == 'progression' and rows[0]['stage_order'] >= 8:
            leaders = []
        if rows and metric == 'match_wins' and rows[0]['wins'] == 0:
            leaders = []
        selected = rows[:limit]
        display_name = lambda row: (row['team_name'][:40] + ('…' if len(row['team_name']) > 40 else '')) if row['team_name'] else '队伍'
        names = '、'.join(display_name(row) for row in leaders[:5])
        suffix = '等%s队' % len(leaders) if len(leaders) > 5 else ''
        if not leaders:
            message = ('公开队伍尚无有效已结束胜场，不能以累计胜场判断哪队最好。' if metric == 'match_wins'
                       else '该赛事尚无可确认的公开参赛进度，不能判断哪队战绩最好。')
        elif metric == 'match_wins':
            message = '按本赛事有效整场累计胜场，%s%s%s最多（%s胜）。不同队伍赛程路径可能不同，此口径不决定晋级或奖金。' % (
                names, suffix, '并列' if len(leaders) > 1 else '', leaders[0]['wins'])
        else:
            message = '按当前公开队伍的最高晋级阶段，%s%s%s领先，处于%s。' % (
                names, suffix, '并列' if len(leaders) > 1 else '', leaders[0]['stage_text'])
            message += ('完整六强淘汰赛结果已确认冠军。' if final is not None else '冠军尚未确定；这是赛事进度，不是跨组胜率或奖金总排名。')
        if leaders and len(leaders) <= 5:
            message += ' 已录入整场战绩：' + '；'.join('%s %s胜%s负' % (display_name(row), row['wins'], row['losses']) for row in leaders) + '。'
        leader_fields = ('team_id', 'team_name', 'stage_text', 'stage_order', 'wins', 'losses', 'pending', 'unresolved', 'byes')
        leader_summaries = [{key: row[key] for key in leader_fields} for row in leaders[:20]]
        data = {**base, 'champion_decided': final is not None,
                'valid_finished_rounds': sum(_valid_result(row) for row in rounds),
                'items': selected, 'leaders': leader_summaries, 'leader_team_ids': [row['team_id'] for row in leaders[:20]],
                'leader_count': len(leaders), 'leaders_truncated': len(leaders) > 20,
                'truncated': len(rows) > len(selected), 'visible_team_count': len(rows)}
        # 主编排器会剔除cards，只把status/message/data发给文本模型。按同口径
        # 整行减少展示项，为其18KB工具JSON上限留余量，不截断任何JSON或统计来源。
        while selected and len(json.dumps({'status': 'ok', 'message': message[:700], 'data': data}, ensure_ascii=False).encode('utf-8')) > MAX_TOOL_BYTES:
            selected.pop()
            data['truncated'] = True
        cards = [{'id': row['team_id'], 'title': row['team_name'], 'badge': row['stage_text'],
                  'subtitle': '%s胜 · %s负 · %s场待赛' % (row['wins'], row['losses'], row['pending']),
                  'detail': '当前晋级阶段：%s；已录入轮空%s场、结果待核实%s场。同阶段并列，种子号只影响显示顺序。' % (
                      row['stage_text'], row['byes'], row['unresolved']),
                  'action': {'kind': 'open_match', 'id': match.id}} for row in selected]
        return _response('teams', '赛事进度与战绩', cards, status='ok' if rows else 'not_found', message=message[:700],
                         summary=basis, data=data)
