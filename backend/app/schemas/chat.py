from typing import Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenConversation(StrictBody):
    target_id: int = Field(gt=0)
    source: Literal["user", "team", "round"] = "user"


class SendMessage(StrictBody):
    client_id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    client_created_at: datetime
    kind: Literal["text", "team", "round"] = "text"
    content: str = Field(default="", max_length=1000)
    source_id: int | None = Field(default=None, gt=0)


class ReadPosition(StrictBody):
    seq: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]{0,17})$")
    message_ids: list[int] = Field(default_factory=list, max_length=100)


class Preferences(StrictBody):
    pinned: bool | None = None
    muted: bool | None = None


class ReportMessage(StrictBody):
    message_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=300)


class ResolveReport(StrictBody):
    action: Literal["dismiss", "hide"]
    reason: str = Field(min_length=1, max_length=300)


class SanctionUser(StrictBody):
    user_id: int = Field(gt=0)
    hours: int = Field(ge=0, le=720)
    reason: str = Field(min_length=1, max_length=300)
