"""只创建私聊新表；不生成聊天、不修改已有业务数据。"""
from app.models.chat import ChatConversation, ChatMember, ChatMessage, ChatEvent, ChatBlock, ChatReport, ChatSanction, ChatAudit

TABLES = [m.__table__ for m in (ChatConversation, ChatMember, ChatMessage, ChatEvent, ChatBlock, ChatReport, ChatSanction, ChatAudit)]


def upgrade(engine):
    for table in TABLES:
        table.create(engine, checkfirst=True)


if __name__ == "__main__":
    from app.database import engine
    upgrade(engine)
    print("私聊数据表已就绪")
