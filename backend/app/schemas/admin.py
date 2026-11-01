"""Minimal administrator inbox counts; no private review material."""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class RegistrationTodo(BaseModel):
    match_id: int
    match_name: str
    count: int
    roster_locked: bool


class AdminTodos(BaseModel):
    verification_count: int
    rank_application_count: int
    team_count: int
    registration_count: int
    total: int
    registration_matches: list[RegistrationTodo]
    blocked_registration_count: int = 0
    blocked_registration_matches: list[RegistrationTodo] = Field(default_factory=list)


class AdminAuditInfo(BaseModel):
    id: int
    actor_id: int
    actor_role: str
    target_user_id: int
    action: str
    application_id: Optional[int] = None
    before_values: dict
    after_values: dict
    reason: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def audit_time_is_utc(cls, value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    class Config:
        from_attributes = True


class AdminAuditPage(BaseModel):
    items: list[AdminAuditInfo]
    total: int
    page: int
    page_size: int
