"""有限的回复语言检查，不替代业务鉴权、确认卡片或实时状态。

只识别助手自己的明确完成声明，不把“已报名的队伍”等事实中的动词
当成助手执行了写操作。不能据此推断模型所有自然语言的真实含义。
"""
import re


_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|\'[^\'\n]*\'|`[^`\n]*`')
_CLAUSES = re.compile(r'([^，,。；;\n!?！？]+)([，,。；;\n!?！？]?)')
_CONDITIONAL = re.compile(r'^(?:如果|假如|若|只要|一旦|等到|待|当(?!前))')
_AGENT_DONE = re.compile(r'^(?:我(?:这边)?|本助手|赛事助手)(?:现已|已经|已|刚刚|刚才|刚)'
                         r'(?:成功)?(?:替[你您]|帮[你您]|为[你您]|代[你您])?(?:成功)?')
_DELEGATED_DONE = re.compile(r'^(?:现已|已经|已)(?:替[你您]|帮[你您]|为[你您]|代[你您])(?:成功)?')
_AGENT_DELEGATED = re.compile(r'^(?:我|本助手|赛事助手)(?:替[你您]|帮[你您]|为[你您]|代[你您])')
_AGENT = re.compile(r'^(?:我|本助手|赛事助手)')
_COMPLETED = re.compile(r'成功(?:了)?|通过(?:了)?|了')
_BARE_REGISTRATION_DONE = re.compile(r'^报名(?:已(?:经)?)?成功(?:了)?')
_RECIPIENT = re.compile(r'^(?:向|给)[^，。；;!?！？\n]{1,24}?(?=发送|发出|发起|提交|递交|发)')
_RELOCATION = re.compile(r'^(?:将|把)(?:你的|您的|本次的|本次)?(?:个人|队伍|入队)?'
                         r'(?:报名|申请|邀请)(?:申请)?(?:提交|递交|发送|发出|确认)')
_CERT_PASS = re.compile(r'(?:在校认证|学籍认证|学生认证|学籍审核|段位认证|段位审核|认证审核|认证)'
                       r'(?:均|都)?(?:已(?:经)?(?:审核)?通过|通过了|成功)')
_CERT_PASS_BEFORE = re.compile(r'已(?:经)?通过(?:了)?(?:你的|您的)?'
                              r'(?:在校认证|学籍认证|学生认证|学籍审核|段位认证|段位审核|认证)')
_RANK_EFFECTIVE = re.compile(r'^(?:(?:你|您|本账号|本次)(?:的)?)?(?:当前|新的|新)?'
                            r'(?:段位|[ABCD]\+{0,2}|S[0-9]{1,2})已(?:经)?(?:更新|生效)')
_INFORMATIONAL = ('预览', '条件', '状态', '资格', '说明', '要求', '规则', '信息', '时间', '步骤', '流程', '查询', '检索')
_SELF_CERT_PREFIX = re.compile(r'^(?:(?:你|您|本账号|当前账号|本次)(?:的)?)?'
                               r'(?:(?:在校认证|学籍认证|学生认证|学籍审核|段位认证|段位审核|认证审核|认证)(?:和|与|及))*$')
_READ_PREFIX = re.compile(r'^(?:我)?(?:已(?:经)?)?(?:查询到|查到|看到|了解到|核实到|确认到|发现)(?:了)?')


def _business_or_verification(action):
    """action紧跟明确主语/完成标志，只判断直接动作，不跨过查询等谓语。"""
    action = re.sub(r'^(?:完成了?|成功)(?:你的|您的|本次的|本次)?', '', action)
    action = _RECIPIENT.sub('', action, count=1)
    for noun in ('在校认证', '学籍认证', '学生认证', '段位认证', '学籍审核', '段位审核', '认证审核', '认证'):
        if action.startswith(noun):
            if not action[len(noun):].startswith((*_INFORMATIONAL, '材料', '截图', '入口', '帮助')):
                return 'verification'
            return None
    if action.startswith(('审核通过', '审批', '批准')):
        return 'verification' if any(word in action for word in ('认证', '学籍', '段位')) else 'business'
    if action.startswith('通过') and action[2:].startswith(('了认证', '认证', '了审核', '审核', '你的认证', '段位审核', '学籍审核')):
        return 'verification'
    if _RELOCATION.match(action):
        return 'business'
    for verb in ('报名', '申请', '邀请', '私信', '联系'):
        if not action.startswith(verb):
            continue
        target = action[len(verb):].removeprefix('了')
        if verb == '申请' and target.startswith(('查询', '检索', '读取', '调用', '查看', '获取', '访问')):
            continue
        if not target.startswith(_INFORMATIONAL):
            return 'business'
    for verb in ('提交', '递交', '确认提交'):
        if action.startswith(verb):
            target = re.sub(r'^(?:了)?(?:你的|您的|本次的|本次)?', '', action[len(verb):])
            if target.startswith(('报名', '申请', '入队申请', '队伍报名', '个人报名')):
                return 'business'
            if target.startswith(('认证材料', '学籍材料', '在校证明', '段位截图')):
                return 'verification'
    for verb in ('发送', '发出', '发起', '发'):
        if action.startswith(verb):
            target = re.sub(r'^(?:了)?(?:你的|您的|本次的|本次)?', '', action[len(verb):])
            if target.startswith(('邀请', '申请', '入队邀请', '入队申请', '组队邀请', '私信', '消息', '通知')):
                return 'business'
    return None


def _conditional_or_relative(clause, terminator, match):
    if terminator in ('?', '？') or _CONDITIONAL.match(clause):
        return True
    tail = clause[match.end():]
    return tail.startswith(('后', '之后', '以后', '前', '时', '才', '才能', '方可', '再', '即可', '便可',
                            '需要', '要求', '取决于', '需先', '必须', '还需',
                            '的用户', '的选手', '的玩家', '的队长', '的成员', '的账号', '的标准', '的条件',
                            '的要求', '的流程', '的说明', '的页面', '的提示', '吗', '了吗', '么'))


def action_claim_kind(reply):
    """返回business/verification或None；仅用于没有受控业务摘要时的文案兜底。"""
    if not isinstance(reply, str):
        return None
    text = _QUOTED.sub('[引述]', reply)
    for match in _CLAUSES.finditer(text):
        clause = re.sub(r'^\d+[.、)]\s*', '', match[1].strip().lstrip('-*•> '))
        clause = re.sub(r'\s+', '', clause)
        clause = re.sub(r'^(?:目前|当前|现在)(?=我|本助手|赛事助手)', '', clause)
        terminator = match[2]
        if not clause or _CONDITIONAL.match(clause) or terminator in ('?', '？'):
            continue
        completed = _AGENT_DONE.match(clause) or _DELEGATED_DONE.match(clause)
        if completed:
            kind = _business_or_verification(clause[completed.end():])
            if kind:
                return kind
        delegated = _AGENT_DELEGATED.match(clause)
        if delegated:
            action = clause[delegated.end():]
            if '了' in action or action.endswith(('成功', '审核通过', '认证通过')):
                kind = _business_or_verification(action)
                if kind:
                    return kind
        # “我提交了报名申请”等没有前置“已”的明确完成语气。
        agent = _AGENT.match(clause)
        if agent:
            action = clause[agent.end():]
            kind, completion = _business_or_verification(action), _COMPLETED.search(action)
            if kind and completion and not _conditional_or_relative(action, terminator, completion):
                return kind
        # 单独的成功通知仍需受控结果；“A队报名成功”等第三方事实不在此分支。
        registration_done = _BARE_REGISTRATION_DONE.match(clause)
        if registration_done and not _conditional_or_relative(clause, terminator, registration_done):
            return 'business'
        # 无当前认证工具证明时，仍拦面向本人的“认证已通过/段位已生效”。
        # 群体统计、条件句及页面文案引用不被当作当前账号的结果。
        target = _READ_PREFIX.sub('', clause, count=1)
        for pattern in (_CERT_PASS, _CERT_PASS_BEFORE, _RANK_EFFECTIVE):
            certificate = pattern.search(target)
            if certificate is None:
                continue
            # 代词须直接修饰认证主体；“你的队伍中有3人认证已通过”仍是群体事实。
            own_certificate = bool(_SELF_CERT_PREFIX.fullmatch(target[:certificate.start()]))
            explicit_self_object = pattern is _CERT_PASS_BEFORE and any(
                pronoun in certificate[0] for pronoun in ('你的', '您的'))
            if pattern is _RANK_EFFECTIVE or own_certificate or explicit_self_object:
                if certificate[0].endswith('成功') and target[certificate.end():].startswith(('不代表', '并不代表', '不等于', '并不等于')):
                    continue
                if not _conditional_or_relative(target, terminator, certificate):
                    return 'verification'
    return None
