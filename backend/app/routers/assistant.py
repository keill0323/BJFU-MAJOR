from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.assistant import AssistantMessageRequest, KnowledgeUpdateRequest
from app.services.auth_service import get_current_user, require_admin_only
from app.services import assistant_service as service
from app.services import assistant_registration_service as registration
from app.services import assistant_knowledge_service as knowledge

router = APIRouter(prefix='/api/assistant', tags=['赛事助手'])


@router.get('/status')
def status():
    return service.status()


@router.get('/verification')
def verification(request: Request, response: Response, db: Session = Depends(get_db), actor=Depends(get_current_user)):
    if request.query_params:
        raise HTTPException(422, '仅查询本人认证状态，不接受用户编号或筛选参数')
    service.require_user(db, actor)
    response.headers['Cache-Control'] = 'no-store'
    from app.services.assistant_verification_service import verification_status
    return verification_status(db, actor)


@router.post('/conversations')
def create_conversation(db: Session = Depends(get_db), actor=Depends(get_current_user)):
    return service.create_conversation(db, actor)


@router.get('/conversations/{conversation_id}')
def history(conversation_id: str, db: Session = Depends(get_db), actor=Depends(get_current_user)):
    return service.conversation_history(db, actor, conversation_id)


@router.post('/conversations/{conversation_id}/messages')
def send(conversation_id: str, body: AssistantMessageRequest, db: Session = Depends(get_db), actor=Depends(get_current_user)):
    return service.send_message(db, actor, conversation_id, body.message, body.client_message_id,
                                body.context.model_dump(exclude_none=True) if body.context is not None else None)


@router.post('/actions/{action_id}/confirm')
def confirm(action_id: str, db: Session = Depends(get_db), actor=Depends(get_current_user)):
    service.require_user(db, actor)
    return registration.confirm_registration(db, actor, action_id)


@router.get('/knowledge')
def get_knowledge(db: Session = Depends(get_db), actor=Depends(require_admin_only)):
    return knowledge.current_knowledge(db)


@router.put('/knowledge')
def update_knowledge(body: KnowledgeUpdateRequest, db: Session = Depends(get_db), actor=Depends(require_admin_only)):
    return knowledge.update_knowledge(db, actor, body)
