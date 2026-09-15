from typing import Annotated
from pydantic import BaseModel, Field, StringConstraints, field_validator


class SendAdminNotice(BaseModel):
    request_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,64}$")]
    recipient_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(min_length=1, max_length=100)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

    @field_validator("recipient_ids")
    @classmethod
    def unique_recipients(cls, value):
        return sorted(set(value))
