"""应用全局配置模块。

通过 pydantic-settings 读取 .env 文件，统一管理所有可变配置项。
避免将密码、密钥等敏感值硬编码在业务代码中。

配置优先级（由低到高）：
    类属性默认值 → .env 文件 → 系统环境变量

Author: keill
Since: 2026-07-21
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal
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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 令牌有效期（分钟），默认 10080 = 7 天
    DB_ECHO: bool = True
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "123456"

    # 上传文件保存目录（学信网截图等），默认 backend/uploads
    UPLOAD_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")

    # 上线配置
    CORS_ORIGINS: str = "*"       # 允许的来源，逗号分隔；生产填小程序/后台域名
    WX_APPID: str = ""            # 微信小程序 AppID（上线必填）
    WX_SECRET: str = ""           # 微信小程序 AppSecret（上线必填）
    WX_MOCK_LOGIN: bool = True    # True=开发用 mock 登录；上线设 False 走真实微信登录
    WX_SUBSCRIBE_ENABLED: bool = False
    WX_SUBSCRIBE_TEMPLATES: str = "{}"  # kind -> {template_id, fields: {thing1: team_name, ...}}
    WX_SUBSCRIBE_ENV: str = "formal"  # formal / trial / developer
    CHAT_ENABLED: bool = False
    CHAT_WRITE_ENABLED: bool = False
    CHAT_ALLOW_USERS: str = ""  # 灰度用户 ID，逗号分隔；空值表示全部正常账号。
    CHAT_MODERATION_ENABLED: bool = True
    CHAT_DELETION_LEDGER: str = ""  # 生产指向独立持久卷；未配置时禁止删除正文。
    """AI配置"""
    AI_REVIEW_ENABLED: bool = False                                         #总开关,上线启用AI审核
    AI_API_KEY: str = ""                                                    #智谱API密钥
    AI_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"               #智谱接口地址
    AI_MODEL: str = "GLM-5V-Turbo"                                          #智谱模型名称

    class Config:
        env_file = ".env"

    # Text assistant configuration is independent from image review and private chat.
    ASSISTANT_ENABLED: bool = False
    ASSISTANT_EXTERNAL_ENABLED: bool = False
    ASSISTANT_TOOL_MODEL_VERIFIED: bool = False
    ASSISTANT_API_KEY: str = ''
    ASSISTANT_BASE_URL: str = 'https://open.bigmodel.cn/api/paas/v4'
    ASSISTANT_MODEL: str = 'glm-5.3-flash'
    ASSISTANT_ENGINE: Literal['langchain', 'legacy'] = 'langchain'
    ASSISTANT_TIMEOUT_SECONDS: int = Field(default=60, ge=5, le=65)
    ASSISTANT_MAX_STEPS: int = Field(default=6, ge=1, le=8)
    ASSISTANT_MAX_TOOL_CALLS: int = Field(default=8, ge=1, le=12)
    ASSISTANT_SUMMARY_ENABLED: bool = False
    ASSISTANT_SUMMARY_TRIGGER_BYTES: int = Field(default=20000, ge=1000, le=60000)
    ASSISTANT_SUMMARY_KEEP_TURNS: int = Field(default=6, ge=1, le=8)
    ASSISTANT_HISTORY_BUDGET_BYTES: int = Field(default=50000, ge=4000, le=60000)
    ASSISTANT_SUMMARY_MAX_BYTES: int = Field(default=4000, ge=256, le=8000)
    ASSISTANT_SUMMARY_INPUT_MAX_BYTES: int = Field(default=50000, ge=1000, le=60000)
    ASSISTANT_SUMMARY_TIMEOUT_SECONDS: int = Field(default=8, ge=1, le=8)


# 全局配置单例
settings = Settings()
