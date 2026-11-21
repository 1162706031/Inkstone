from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Password = Annotated[str, Field(min_length=12, max_length=128)]
ExistingPassword = Annotated[str, Field(min_length=1, max_length=128)]
Code = Annotated[str, Field(pattern=r"^[0-9]{6}$")]
Token = Annotated[str, Field(min_length=1, max_length=128)]
Step = Literal["ready", "initial_password_change", "mfa_setup", "mfa_verify"]
Status = Literal["pending", "active", "disabled"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class UserView(BaseModel):
    id: UUID
    username: str
    display_name: str
    role: Literal["member", "admin"]
    status: Status
    created_at: datetime


class AdminUserView(UserView):
    revision: int


class Username(Input):
    username: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,63}$")]

    @field_validator("username", mode="before")
    @classmethod
    def normalize(cls, value):
        return value.lower() if isinstance(value, str) else value


class Login(Username):
    password: ExistingPassword


class MemberCreate(Username):
    display_name: Annotated[str, Field(min_length=1, max_length=80)]


class NewPassword(Input):
    new_password: Password


class ChangePassword(NewPassword):
    current_password: ExistingPassword


class PasswordCheck(Input):
    password: ExistingPassword


class Activation(PasswordCheck):
    token: Token
    password: Password


class ResetPassword(NewPassword):
    token: Token


class CodeInput(Input):
    code: Code


class RecentAuth(PasswordCheck, CodeInput):
    pass


class Verify(Input):
    code: Code | None = None
    recovery_code: Token | None = None

    @model_validator(mode="after")
    def exclusive(self) -> Self:
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Provide exactly one code")
        return self


class MemberStatus(Input):
    status: Literal["active", "disabled"]
    revision: Annotated[int, Field(ge=1)]


class CSRFView(BaseModel):
    csrf_token: str


class LoginView(BaseModel):
    next: Step
    user: UserView | None = None


class Me(CSRFView):
    user: UserView
    workspace_id: UUID


class SetupView(BaseModel):
    secret: str
    provisioning_uri: str


class UserResult(BaseModel):
    user: UserView


class ConfirmView(UserResult):
    recovery_codes: list[str]


class FreshView(BaseModel):
    verified_until: datetime


class StatusResult(BaseModel):
    status: Literal["activated", "password_changed"]


class ActivationToken(BaseModel):
    activation_token: str
    expires_at: datetime


class MemberCreated(ActivationToken):
    user: AdminUserView


class ResetToken(BaseModel):
    reset_token: str
    expires_at: datetime


class MemberPage(BaseModel):
    items: list[AdminUserView]
    next_cursor: str | None
