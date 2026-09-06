from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AccountCreate(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    account_context: str | None = Field(default=None, max_length=10000)


class AccountResponse(AccountCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    followups_paused: bool = False
    followups_paused_reason: str | None = None
    last_reply_at: datetime | None = None


class PauseRequest(BaseModel):
    """Resume clears the reply pause; the operator states why."""

    paused: bool
    reason: str | None = Field(default=None, max_length=1000)
