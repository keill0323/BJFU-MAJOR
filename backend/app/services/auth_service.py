"""
密码加密、JWT 令牌生成、 用户相关
Author: keill
Since: 2026-7-22
"""

from datetime import datetime, timedelta
from typing import Optional

from jose import jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """对密码进行 bcrypt 哈希加密"""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, role: str) -> str:
    """生成 JWT 登陆令牌，有效期由配置文件决定"""
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """根据用户ID查询用户"""
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_student_id(db: Session, student_id: str) -> Optional[User]:
    """根据学号查询用户"""
    return db.query(User).filter(User.student_id == student_id)