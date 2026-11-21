from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from ai_platform.schemas.identity import Input
from ai_platform.schemas.models import Name, Revision, Tokens, RunView

Description = Annotated[str, Field(max_length=500)]
Prompt = Annotated[str, Field(max_length=16000)]
Temperature = Annotated[float, Field(ge=0, le=2)]
Output = Annotated[int, Field(ge=1, le=4096)]
Threshold = Annotated[int, Field(ge=50, le=95)]
History = Annotated[int, Field(ge=2, le=100, multiple_of=2)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
Visibility = Literal["private", "public"]


class AgentConfig(Input):
    name: Name
    model_definition_id: UUID
    description: Description = ""
    system_prompt: Prompt = ""
    temperature: Temperature | None = None
    max_output_tokens: Output = 1024
    context_budget_tokens: Tokens = 8192
    context_compression_threshold_percent: Threshold = 80
    history_max_messages: History = 20


class AgentCreate(AgentConfig):
    visibility: Visibility = "private"


class Publish(Input):
    revision: Revision


class AgentPatch(Publish):
    visibility: Visibility | None = None
    name: Name | None = None
    model_definition_id: UUID | None = None
    description: Description | None = None
    system_prompt: Prompt | None = None
    temperature: Temperature | None = None
    max_output_tokens: Output | None = None
    context_budget_tokens: Tokens | None = None
    context_compression_threshold_percent: Threshold | None = None
    history_max_messages: History | None = None


class AgentView(BaseModel):
    id: UUID
    can_edit: bool
    is_creator: bool
    can_manage: bool
    creator_id: UUID
    creator_name: str
    visibility: Visibility
    published_version: int | None
    published_revision: int | None
    published_name: str | None
    published_description: str | None
    name: str
    model_definition_id: UUID
    description: str
    system_prompt: str | None
    temperature: float | None
    max_output_tokens: int
    context_budget_tokens: int
    context_compression_threshold_percent: int
    history_max_messages: int
    revision: int
    status: str
    created_at: datetime
    updated_at: datetime


class Version(BaseModel):
    id: UUID
    published_version: int | None
    revision: int
    config: AgentConfig
    model_config_version_id: UUID
    created_at: datetime


class NewConversation(Input):
    agent_revision: Revision
    title: Title | None = None


class Rename(Input):
    title: Title


class Conversation(BaseModel):
    id: UUID
    agent_id: UUID
    agent_version_id: UUID
    agent_name: str
    release_version: int | None
    title: str
    status: str
    active_run_id: UUID | None
    created_at: datetime
    updated_at: datetime


class TextInput(Input):
    type: Literal["text"]
    text: Annotated[str, Field(min_length=1, max_length=8000)]

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or len(value.encode()) > 32768:
            raise ValueError("Invalid text")
        return value


class ChatInput(Input):
    content: Annotated[list[TextInput], Field(min_length=1, max_length=1)]


class NewRun(Input):
    conversation_id: UUID
    input: ChatInput


class Block(BaseModel):
    type: Literal["text"]
    block_id: UUID
    text: str


class Message(BaseModel):
    id: UUID
    conversation_id: UUID
    run_id: UUID
    sequence: int
    role: Literal["user", "assistant"]
    content: list[Block]
    status: str
    created_at: datetime


class HistoryPage(BaseModel):
    items: list[Message]
    run_cursors: dict[UUID, int]
    compression: dict[UUID, str]
    next_cursor: str | None


class RunDetail(RunView):
    output_message: Message | None
