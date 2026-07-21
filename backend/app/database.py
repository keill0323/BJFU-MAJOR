"""数据库连接模块。

封装 SQLAlchemy 引擎创建、会话管理及 FastAPI 依赖注入。

Author: keill
Since: 2026-07-21
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


# 数据库引擎：持有底层数据库连接和连接池
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False}
    if "sqlite" in settings.DATABASE_URL
    else {},
    echo=settings.DB_ECHO,
)

# 会话工厂：绑定了上述引擎，SessionLocal() 调用即获取一个新会话
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """ORM 模型基类。

    所有数据表模型（User、Team、Match 等）必须继承此类。
    SQLAlchemy 通过 Base.metadata 发现并自动创建所有子类的数据库表。
    """


def get_db():
    """获取数据库会话，供 FastAPI 路由函数通过 Depends 注入使用。

    利用 yield 实现请求级会话管理：
    请求进入时创建会话，路由处理完毕后 finally 块自动关闭并归还连接。

    Yields:
        Session: SQLAlchemy 会话对象。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
