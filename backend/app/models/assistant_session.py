"""AI 助手自己的短期会话；不关联、读取或复制用户私聊。"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME
from app.database import Base


class AssistantConversation(Base):
    __tablename__ = 'assistant_conversations'
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    context_json = Column(Text, nullable=False, default='{}')
    memory_json = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    lease_token = Column(String(36), nullable=True)
    lease_until = Column(DateTime, nullable=True)


class AssistantTurn(Base):
    __tablename__ = 'assistant_turns'
    __table_args__ = (UniqueConstraint('conversation_id', 'client_message_id', name='uq_assistant_client_turn'),)
    id = Column(String(36), primary_key=True)
    conversation_id = Column(String(36), ForeignKey('assistant_conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    client_message_id = Column(String(64), nullable=False)
    input_digest = Column(String(64), nullable=False)
    user_text = Column(Text, nullable=False)
    state = Column(String(20), nullable=False, default='processing')
    response_json = Column(Text, nullable=True)
    # MySQL默认DATETIME只有秒精度，不能靠随机UUID恢复同秒轮次先后。
    created_at = Column(DateTime().with_variant(DATETIME(fsp=6), 'mysql', 'mariadb'),
                        nullable=False, default=datetime.utcnow)


class AssistantToolTrace(Base):
    __tablename__ = 'assistant_tool_traces'
    id = Column(String(36), primary_key=True)
    turn_id = Column(String(36), ForeignKey('assistant_turns.id', ondelete='CASCADE'), nullable=False, index=True)
    tool = Column(String(64), nullable=False)
    result_code = Column(String(40), nullable=False)
    duration_ms = Column(Integer, nullable=False)
    # 不存参数、资料全文、模型思考、供应商回复、密钥或私信。
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AssistantKnowledgeRevision(Base):
    __tablename__ = 'assistant_knowledge_revisions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(64), nullable=False, unique=True)
    previous_version = Column(String(64), nullable=False, unique=True)
    payload_json = Column(Text, nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
