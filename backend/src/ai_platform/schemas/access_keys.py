from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ai_platform.schemas.chat import ChatInput, NewConversation
from ai_platform.schemas.identity import Input
from ai_platform.schemas.models import Name, Revision

ExternalUser = Annotated[str, Field(min_length=1, max_length=200)]
Agents = Annotated[list[UUID], Field(min_length=1, max_length=50)]


class KeyCreate(Input):
    name: Name
    agent_ids: Agents
    expires_at: datetime | None = None


class KeyPatch(Input):
    revision: Revision
    name: Name | None = None
    agent_ids: Agents | None = None
    expires_at: datetime | None = None
    status: Literal["revoked"] | None = None


class KeyRotate(Input):
    revision: Revision


class KeyView(BaseModel):
    """Never carries the token itself; only the prefix shown in listings."""

    id: UUID
    name: str
    key_prefix: str
    status: Literal["active", "revoked"]
    agent_ids: list[UUID]
    expires_at: datetime | None
    last_used_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class IssuedKey(BaseModel):
    """Returned once on create and rotate; the plaintext is not stored and cannot be read again."""

    key: KeyView
    token: str


# --- external API ------------------------------------------------------------

class ExternalConversation(NewConversation):
    external_user_id: ExternalUser


class ExternalRun(Input):
    conversation_id: UUID
    external_user_id: ExternalUser
    input: ChatInput
