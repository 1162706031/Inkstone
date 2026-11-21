from datetime import datetime
from typing import Annotated, Generic, Literal, Self, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from ai_platform.schemas.identity import Input

Name = Annotated[str, Field(min_length=1, max_length=80)]
Endpoint = Annotated[str, Field(min_length=1, max_length=2048)]
Key = Annotated[str, Field(min_length=1, max_length=8192, pattern=r"^[!-~]+$")]
Revision = Annotated[int, Field(ge=1)]
Tokens = Annotated[int, Field(gt=0, le=10**9)]
ModelName = Annotated[str, Field(min_length=1, max_length=200)]
State = Literal["active", "disabled"]
UsageSupport = Literal["unknown", "supported", "unsupported"]
Mode = Literal["soft", "strict"]
T = TypeVar("T")


class ConnectionCreate(Input):
    name: Name
    base_url: Endpoint
    api_key: Key
    protocol: Literal["chat_completions"] = "chat_completions"


class Patch(Input):
    revision: Revision

    @model_validator(mode="after")
    def changes(self) -> Self:
        fields = self.model_fields_set - {"revision"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("Provide at least one non-null change")
        return self


class ConnectionPatch(Patch):
    name: Name | None = None
    api_key: Key | None = None
    status: State | None = None


class ConnectionView(BaseModel):
    id: UUID
    name: str
    base_url: str
    protocol: Literal["chat_completions"]
    status: State
    revision: int
    credential_version: int
    has_credential: bool = True
    created_at: datetime


class ModelCreate(Input):
    upstream_model: ModelName
    display_name: Name
    context_limit_tokens: Tokens
    max_output_tokens: Tokens
    supports_temperature: bool
    usage_support: UsageSupport

    @model_validator(mode="after")
    def token_limits(self) -> Self:
        if self.max_output_tokens >= self.context_limit_tokens:
            raise ValueError("Output must leave room for input")
        return self


class ModelPatch(Patch):
    display_name: Name | None = None
    upstream_model: ModelName | None = None
    context_limit_tokens: Tokens | None = None
    max_output_tokens: Tokens | None = None
    supports_temperature: bool | None = None
    status: State | None = None


class AvailableModelView(BaseModel):
    id: UUID
    display_name: str
    context_limit_tokens: int
    max_output_tokens: int
    supports_temperature: bool


class ModelView(AvailableModelView):
    connection_id: UUID
    upstream_model: str
    usage_support: UsageSupport
    capability_status: Literal["verified", "unverified"]
    last_test_status: Literal["untested", "passed", "failed"]
    strict_budget_eligible: bool
    status: State
    revision: int
    test_run_id: UUID | None = None
    test_run_status: str | None = None
    test_requested_at: datetime | None = None
    test_error: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class ModelSelection(Input):
    model_definition_id: UUID


class DiscoveredModel(BaseModel):
    upstream_model: str
    context_limit_tokens: int | None
    registered: bool


class Discovery(BaseModel):
    items: list[DiscoveredModel]


class ImportItem(Input):
    upstream_model: ModelName
    display_name: Name | None = None
    context_limit_tokens: Annotated[int, Field(ge=2, le=10**9)] | None = None


class ModelImport(Input):
    models: Annotated[list[ImportItem], Field(min_length=1, max_length=500)]


class ImportResult(BaseModel):
    created: int
    skipped: int


class RunView(BaseModel):
    id: UUID
    conversation_id: UUID | None
    kind: Literal["chat", "connection_test"]
    agent_version_id: UUID | None
    status: str
    error: str | None
    context_compression: str | None
    input_message_id: UUID | None
    output_message_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_event_sequence: int


class QuotaPatch(Input):
    monthly_token_limit: Annotated[int, Field(ge=0, le=10**12)]
    mode: Mode
    revision: Revision


class QuotaView(BaseModel):
    workspace_id: UUID
    mode: Mode
    monthly_token_limit: int
    revision: int


class UsageSummary(BaseModel):
    period_start: datetime
    period_end: datetime
    timezone: str = "Asia/Shanghai"
    mode: Mode
    token_limit: int
    confirmed_tokens: int
    reserved_tokens: int
    uncertain_reserved_tokens: int
    unknown_attempts: int
    remaining_tokens: int
    estimated_costs: list[dict[str, str]]
    unpriced_attempts: int


class AttemptView(BaseModel):
    id: UUID
    model_call_id: UUID
    run_id: UUID
    model_definition_id: UUID
    purpose: str
    state: str
    input_tokens: int | None
    output_tokens: int | None
    cost: str | None
    currency: str | None
    usage_source: Literal["reported", "unknown"]
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
