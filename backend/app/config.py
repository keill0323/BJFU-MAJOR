"""应用全局配置模块。

通过 pydantic-settings 读取 .env 文件，统一管理所有可变配置项。
避免将密码、密钥等敏感值硬编码在业务代码中。

配置优先级（由低到高）：
    类属性默认值 → .env 文件 → 系统环境变量

Author: keill
Since: 2026-07-21
"""
from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    """系统配置类。

    继承 pydantic-settings 的 BaseSettings，自动从 .env 文件加载配置值。
    所有类属性均可被同名环境变量覆盖。

    Attributes:
        DATABASE_URL: 数据库连接串（SQLAlchemy 格式）
        SECRET_KEY: JWT 令牌签名密钥，生产环境必须替换为随机字符串
        ALGORITHM: JWT 签名算法，默认 HS256
        ACCESS_TOKEN_EXPIRE_MINUTES: 令牌有效期（分钟），默认 1440 = 24 小时
        DB_ECHO: 打印SQL语句
        ADMIN_USERNAME: 管理员初始用户名
        ADMIN_PASSWORD: 管理员初始密码
    """

    DATABASE_URL: str = "sqlite:///" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cs2_competition.db")
    SECRET_KEY: str = "change-me-in-env-file"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DB_ECHO: bool = True
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "123456"

    class Config:
        env_file = ".env"


# 全局配置单例
settings = Settings()
