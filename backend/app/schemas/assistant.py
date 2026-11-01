"""助手请求仅接受有限字段；身份始终来自 JWT。"""
from typing import Literal
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class AssistantContext(StrictModel):
    match_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    player_id: int | None = Field(default=None, ge=1)
    stats_player_key: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class AssistantMessageRequest(StrictModel):
    message: str = Field(min_length=1, max_length=1200)
    client_message_id: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    context: AssistantContext | None = None


class KnowledgeSource(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    reference: str = Field(min_length=1, max_length=250)
    version: str = Field(min_length=1, max_length=64)


class KnowledgeEntry(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9_-]+$')
    title: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1500)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    scope_match_id: int | None = Field(default=None, ge=1)
    status: Literal['active', 'draft', 'retired'] = 'draft'
    effective_from: str | None = Field(default=None, max_length=40)
    effective_until: str | None = Field(default=None, max_length=40)
    source: KnowledgeSource


class PublicAdminContact(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    responsibilities: list[str] = Field(min_length=1, max_length=10)
    # 这里只发布参赛须知入口；不得从账号角色自动建立通讯录。
    contact_kind: Literal['rules_page'] = 'rules_page'
    source: KnowledgeSource


class KnowledgeUpdateRequest(StrictModel):
    expected_version: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')
    maintainer: str = Field(min_length=1, max_length=100)
    entries: list[KnowledgeEntry] = Field(max_length=100)
    public_admin_contacts: list[PublicAdminContact] = Field(default_factory=list, max_length=10)
