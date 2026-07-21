from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean
import enum

from app.database import Base
"""用户数据模型
Author: keill
Since: 2026-07-21
"""

class UserRole(str,enum.Enum):
    """用户角色枚举类"""

    USER="user"
    ADMIN="admin"

class User(Base):
    """用户表，记录所有注册用户信息
    
    Attributes:
        id:主键，自增
        wx_openid:微信小程序唯一表示
        nickname:用户昵称
        game_id:游戏id
        student_id:学号(需上传截图人工审核)
        verify_image:学信网/教务系统截图URL
        is_verified:管理员是否审核通过
        role: user/admin
        created_at:注册时间
    """

    __tablename__="users"

    id=Column(Integer,primary_key=True,index=True,autoincrement=True)
    wx_openid=Column(String(64),unique=True,index=True,nullable=True)
    nickname=Column(String(64),nullable=True)
    game_id=Column(String(64),nullable=True)
    student_id=Column(String(64),unique=True,nullable=True)
    verify_image=Column(String(256),nullable=True)
    is_verified=Column(Boolean,default=False,index=True)
    role=Column(String(10),default="user")
    created_at=Column(DateTime,default=datetime.now,index=True)

