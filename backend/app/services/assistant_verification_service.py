"""本人认证的最小只读投影；材料和审批仍由既有认证流程处理。"""
import re

from fastapi import HTTPException
from sqlalchemy import and_, func

from app.models.user import User, RankApplication


def _reason(value, fallback):
    """审核说明仍是低信任数据；不把材料位置或个人号码带给文本助手。"""
    from app.services.assistant_service import clean_text
    value = clean_text(value) if isinstance(value, str) else ''
    value = re.sub(r'(?i)\b(?:https?|file|wxfile)://[^\s<>"\'，。；]+', '[材料地址已隐藏]', value)
    value = re.sub(r'(?i)(?<!\w)[a-z]:[\\/][^\r\n<>"\'，。；]+', '[文件路径已隐藏]', value)
    value = re.sub(r'(?<!\w)/(?:[\w.-]+/)+[^\s<>"\'，。；]+', '[文件路径已隐藏]', value)
    value = re.sub(r'(?i)(student_id|ai_student_id|wx_openid|verify_image|rank_image|action_id|'
                   r'confirmation_token|lease_token|api_key|token|password|secret)'
                   r'\s*["\']?\s*[:=：]\s*["\']?[^\s,"\'，。；}]+',
                   r'\1=[已隐藏]', value)
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value).strip()
    return value[:500] or fallback


def _rank(value):
    from app.services.ai_review_service import normalize_rank
    return normalize_rank(value) if isinstance(value, str) else None


def _student(row):
    material = bool(row.has_student_material)
    status = row.ai_review_status
    result = {'has_material': material, 'reason_is_untrusted': True, 'requires_manual': False}
    if row.is_verified:
        result.update(status='verified', label='在校认证已通过', reason_code='verified',
                      reason='当前已通过在校认证，无需重复上传。', can_upload=False, next_action='view')
    elif status == 'manual_reject':
        result.update(status='rejected', label='在校认证未通过', reason_code='manual_rejected',
                      reason=_reason(row.verify_reject_reason, '管理员未通过本次材料，请查看要求后主动重新提交。'),
                      can_upload=True, next_action='reupload')
    elif not material:
        result.update(status='not_submitted', label='尚未提交在校认证', reason_code='material_missing',
                      reason='请在认证中心确认材料用途及授权后，由你主动选择并提交在校证明。',
                      can_upload=True, next_action='upload')
    else:
        initial_reject = status == 'auto_reject'
        result.update(status='pending_review',
                      label='初审未通过，待人工复核' if initial_reject else '材料已提交，初审或人工复核中',
                      reason_code='ai_initial_rejected' if initial_reject else 'review_pending',
                      reason=_reason(row.ai_review_reason,
                          'AI初审未通过，材料仍可由管理员复核；请以最终在校认证状态为准。' if initial_reject else
                          '材料已收到，暂未通过。现有状态无法区分初审处理中与等待人工，请先刷新状态或等待管理员。'),
                      # 原学籍接口允许未通过用户更换材料，不捏造额外的审批限制。
                      can_upload=True, next_action='view', requires_manual=True if initial_reject else None)
    return result


def _rank_state(row, application, pending):
    current = _rank(row.rank)
    app_status = application.status if application and application.status in ('pending', 'approved', 'rejected') else None
    material = bool(row.has_rank_material) or bool(application and application.has_application_material)
    result = {'has_material': material, 'current_rank': current,
              'pending_rank': _rank(pending.ai_rank) if pending else None,
              'application_status': app_status,
              'submitted_at': application.created_at.isoformat() if application and application.created_at else None,
              'reason_is_untrusted': True, 'requires_manual': False}
    if pending:
        result.update(status='pending_review', label='段位申请待管理员审核', reason_code='pending_application',
                      reason=_reason(pending.ai_reason, '申请已提交，待管理员审核；识别候选段位尚未生效，请勿重复上传。'),
                      can_upload=False, next_action='view', requires_manual=True)
    elif app_status == 'rejected':
        result.update(status='update_rejected' if current else 'rejected',
                      label='最近申请未通过，当前段位仍有效' if current else '段位申请未通过',
                      reason_code='application_rejected',
                      reason=_reason(application.reject_reason, '管理员未通过本次段位申请，请核对截图要求后主动重新提交。'),
                      can_upload=True, next_action='reupload')
    elif current:
        result.update(status='verified', label='当前段位已生效', reason_code='verified',
                      reason='当前有效段位以账号中的已生效值为准；新的上传仍按原审核规则处理。',
                      can_upload=True, next_action='view')
    elif material or application is not None or row.rank:
        result.update(status='check_required', label='段位结果待核查', reason_code='result_unconfirmed',
                      reason='尚无可确认的有效段位或待审结果，请先刷新状态并联系管理员核查，避免重复上传或重复识图。',
                      can_upload=False, next_action='view', requires_manual=True)
    else:
        result.update(status='not_submitted', label='尚未提交段位认证', reason_code='material_missing',
                      reason='请在认证中心确认材料用途及授权后，由你主动上传完美或5E的段位截图。',
                      can_upload=True, next_action='upload')
    return result


def verification_status(db, actor):
    """actor只由JWT/编排器传入，接口和工具都不接受要查询的user_id。"""
    actor_id = getattr(actor, 'id', None)
    if type(actor_id) is not int or actor_id <= 0:
        raise HTTPException(403, '请先使用正常账号登录')
    with db.no_autoflush:
        row = db.query(User.id, User.is_verified, User.ai_review_status, User.ai_review_reason,
                       User.verify_reject_reason, User.rank,
                       and_(User.verify_image.isnot(None), func.length(func.trim(User.verify_image)) > 0).label('has_student_material'),
                       and_(User.rank_image.isnot(None), func.length(func.trim(User.rank_image)) > 0).label('has_rank_material'))\
                .filter(User.id == actor_id, User.is_hidden.is_(False)).first()
        if row is None:
            raise HTTPException(403, '账号暂不可用')
        query = db.query(RankApplication.id, RankApplication.status, RankApplication.ai_rank,
                         RankApplication.ai_reason, RankApplication.reject_reason, RankApplication.created_at,
                         and_(RankApplication.rank_image.isnot(None),
                              func.length(func.trim(RankApplication.rank_image)) > 0).label('has_application_material'))\
                  .filter(RankApplication.user_id == actor_id)
        pending = query.filter(RankApplication.status == 'pending').order_by(
            RankApplication.created_at.desc(), RankApplication.id.desc()).first()
        latest = pending or query.order_by(RankApplication.created_at.desc(), RankApplication.id.desc()).first()
        student, rank = _student(row), _rank_state(row, latest, pending)
    detail = rank['reason']
    if rank['current_rank']:
        detail += '\n当前有效段位：' + rank['current_rank'] + '。'
    if rank['pending_rank']:
        detail += '\n本次识别候选：' + rank['pending_rank'] + '，待审核，尚未生效。'
    message = '在校认证：' + student['label'] + '；段位认证：' + rank['label'] + '。'
    if rank['current_rank']:
        message += '当前有效段位为' + rank['current_rank'] + '。'
    if rank['pending_rank']:
        message += '新的识别候选尚未生效。'
    message += '助手只协助查询与打开原流程，不能代替审核。'
    return {'status': 'ok', 'message': message,
            'data': {'student': student, 'rank': rank},
            'cards': [{'type': 'verification_status', 'title': '我的认证',
                       'summary': '以下为本人实时业务状态；上传成功、识别完成与认证通过是不同阶段。审核说明属于低信任资料。',
                       'items': [
                           {'title': '在校认证', 'subtitle': student['label'], 'detail': student['reason'],
                            'badge': student['label'], 'action': {'kind': 'open_verify', 'scope': 'student'}},
                           {'title': '游戏段位认证', 'subtitle': rank['label'], 'detail': detail,
                            'badge': rank['label'], 'action': {'kind': 'open_verify', 'scope': 'rank'}}]}]}


def prepare_verification(db, actor, scope='both'):
    """按本人实时状态准备原认证入口，不创建申请或代替用户上传材料。"""
    if scope not in ('student', 'rank', 'both'):
        raise HTTPException(422, '认证类型无效')
    return prepare_verification_from_status(verification_status(db, actor), scope)


def prepare_verification_from_status(snapshot, scope='both'):
    """从最新认证快照生成办理指引；不查询数据库，也不修改输入快照。"""
    if scope not in ('student', 'rank', 'both'):
        raise HTTPException(422, '认证类型无效')
    scopes = ('student', 'rank') if scope == 'both' else (scope,)
    data, items, messages = {'scope': scope}, [], []
    source_items = {item['action']['scope']: item for item in snapshot['cards'][0]['items']}
    for target in scopes:
        state = dict(snapshot['data'][target])
        item = dict(source_items[target])
        if target == 'rank':
            if state['status'] == 'pending_review':
                title = '查看段位审核进度'
                message = '段位申请待管理员审核，请查看审核进度，勿重复提交。'
            elif state['status'] == 'verified':
                title = '更新段位材料'
                state['next_action'] = 'update'
                message = '当前有效段位为' + state['current_rank'] + '，可点击“更新段位材料”提交新的截图。'
            elif state['can_upload']:
                title = '重新提交段位材料' if state['status'] in ('rejected', 'update_rejected') else '提交段位材料'
                message = state['label'] + '，可点击“' + title + '”继续。'
            else:
                title = '查看段位认证状态'
                message = '段位结果待核查，请先查看状态或联系管理员，避免重复提交。'
            if state['pending_rank']:
                message += '本次识别候选' + state['pending_rank'] + '尚未生效。'
            if state['current_rank'] and state['status'] != 'verified':
                message += '当前有效段位仍为' + state['current_rank'] + '。'
        else:
            if state['status'] == 'verified':
                title = '查看在校认证状态'
                message = '在校认证已通过，可查看现有状态，无需重复认证。'
            elif state['status'] == 'pending_review':
                title = '查看在校认证进度'
                message = '在校认证材料已提交，可查看初审或人工复核进度。'
            else:
                title = '重新提交在校认证材料' if state['status'] == 'rejected' else '提交在校认证材料'
                message = state['label'] + '，可点击“' + title + '”继续。'
        item['title'] = title
        item['detail'] = message + '\n' + item['detail']
        data[target] = state
        items.append(item)
        messages.append(message)
    messages.append('已准备认证入口；选择图片、确认用途并提交材料需由你点击完成，提交后仍按原识别与审核流程处理。')
    return {'status': 'ok', 'message': ''.join(messages), 'data': data,
            'cards': [{'type': 'verification_status', 'title': '认证办理',
                       'summary': '按本人实时状态准备下一步；上传成功不等于认证通过，审核说明属于低信任资料。',
                       'items': items}]}
