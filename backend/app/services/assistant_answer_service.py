"""Keep answers to other queried topics beside authoritative action/status text.

These are excerpts of server-produced result cards, never the model's draft or
an authorization decision. The most recent complete query snapshot replaces
its earlier version within the same turn.
"""
import hashlib
import json
import re

from app.services.assistant_action_policy import action_claim_kind


_PUBLIC_TYPES = {'knowledge', 'rounds', 'teams', 'players', 'player_stats', 'matches'}
_PRIORITY = {'knowledge': 0, 'rounds': 1, 'player_stats': 2, 'teams': 3, 'players': 4, 'matches': 5}


def annotate_result(result, tool, arguments):
    """Tag cards with their server-known query scope; the tag never leaves the turn."""
    if not isinstance(result, dict) or not isinstance(result.get('cards'), list):
        return result
    scope = {key: value for key, value in arguments.items() if value is not None and key != 'limit'}
    if tool == 'recommend_teams':
        scope['eligible_only'] = arguments.get('eligible_only') is not False
    elif tool == 'recommend_players':
        scope['free_only'] = (not arguments.get('user_id')) if arguments.get('free_only') is None else arguments['free_only']
        scope['verified_only'] = arguments.get('verified_only') is True
        scope['order_by'] = 'rank' if arguments.get('order_by') == 'rank' else 'stable'
    elif tool == 'get_match_rounds':
        scope['next_only'] = bool(arguments.get('next_only'))
        scope['status'] = arguments.get('status') or 'all'
    elif tool == 'get_match_standings':
        scope['metric'] = arguments.get('metric') or 'progression'
    for key, value in list(scope.items()):
        if isinstance(value, str):
            value = value.strip().casefold()
            if value:
                scope[key] = value
            else:
                del scope[key]
    data = result.get('data') or {}
    for key in ('match_id', 'team_id'):
        if isinstance(data, dict) and data.get(key) is not None:
            scope[key] = data[key]
            if key == 'match_id':
                scope.pop('match_keyword', None)
            elif tool == 'get_match_rounds':
                scope.pop('my_team', None)  # The actual resolved team is authoritative.
    digest = hashlib.sha256(json.dumps([tool, scope], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return dict(result, cards=[dict(card, _answer_scope=digest + ':' + str(index))
        for index, card in enumerate(result['cards']) if isinstance(card, dict)])


def public_cards(cards):
    return [{key: value for key, value in card.items() if key != '_answer_scope'} for card in cards]


def supporting_answers(cards, authoritative, clean_text, *, max_chars=1200):
    def text(value, limit):
        if not isinstance(value, str):
            return ''
        value = clean_text(value)
        value = re.sub(r'(?i)(?:https?|file|wxfile)://[^\s<>"\'，。；]+', '[地址已隐藏]', value)
        value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value).strip()
        if action_claim_kind(value):
            return ''  # User-authored names/descriptions are not proof of an action.
        return value[:limit]

    known = '\n'.join(value for value in authoritative if isinstance(value, str))
    groups = {}
    for card in cards[-40:]:
        if not isinstance(card, dict) or card.get('type') not in _PUBLIC_TYPES:
            continue
        kind = card['type']
        title = text(card.get('title'), 80)
        summary = text(card.get('summary'), 250)
        if summary and summary in known:
            continue  # A next-round result already has its authoritative answer.
        # Replace a complete query snapshot, including rows that disappeared.
        # Different match/group/filter scopes remain independent.
        group = {'kind': kind, 'title': title, 'rows': {}}
        groups[(kind, card.get('_answer_scope') or title)] = group
        items = card.get('items')
        if not isinstance(items, list):
            continue
        if not items:
            group['rows'].clear()
        for item in items[:30]:
            if not isinstance(item, dict):
                continue
            name = text(item.get('title'), 80)
            fields = [name]
            fields_to_show = (('detail', 240),) if kind == 'matches' else (('subtitle', 100), ('detail', 240))
            for field, limit in fields_to_show:
                value = text(item.get(field), limit)
                if value and value not in fields and value not in known:
                    fields.append(value)
            line = '；'.join(value for value in fields if value)
            if not line or line in known:
                continue
            identity = item.get('id')
            action = item.get('action') or {}
            if not isinstance(identity, (str, int)):
                identity = action.get('id') if isinstance(action, dict) else None
            key = str(identity) if isinstance(identity, (str, int)) else name
            group['rows'][key] = line

    answers, size = [], 0
    for group in sorted(groups.values(), key=lambda value: _PRIORITY[value['kind']]):
        rows = list(group['rows'].values())
        if not rows:
            rows = ['本次查询未返回可展示的匹配结果。']
        body = '\n'.join(rows[:3])
        body = body[:420] + ('…其余见结果卡片。' if len(body) > 420 or len(rows) > 3 else '')
        answer = (group['title'] or '查询资料') + '：\n' + body
        if size + len(answer) > max_chars:
            remaining = max_chars - size
            if remaining > 80:
                answers.append(answer[:remaining - 12] + '…详见结果卡片。')
            break
        answers.append(answer)
        size += len(answer) + 2
    return '\n\n'.join(answers)
