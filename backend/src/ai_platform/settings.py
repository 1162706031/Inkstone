import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Containers mount Docker secrets as files named after the settings fields, e.g. /run/secrets/database_url.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True,
                                      secrets_dir=os.getenv("AI_PLATFORM_SECRETS_DIR"))

    environment: Literal["development", "test", "production"] = "development"
    database_url: str | None = Field(default=None, repr=False)
    checkpoint_database_url: str | None = Field(default=None, repr=False)
    redis_url: str | None = Field(default=None, repr=False)
    postgres_host: str | None = None
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "ai_platform_dev"
    postgres_user: str = "ai_dev"
    postgres_password: SecretStr | None = None
    redis_host: str | None = None
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_user: str = "default"
    redis_password: SecretStr | None = None
    redis_prefix: str = "ai"
    model_workspace_requests_per_minute: int = Field(default=20, ge=1)
    model_workspace_max_concurrent: int = Field(default=2, ge=1)
    model_global_max_concurrent: int = Field(default=4, ge=1)
    app_origin: str = "http://127.0.0.1:5174"
    credential_key_file: Path | None = None
    credential_key_version: str = "v1"
    private_model_allowlist: str = ""
    context_limit_tokens: int = 32768
    deletion_archive_path: Path | None = None

    @model_validator(mode="after")
    def build_connection_urls(self) -> Self:
        if self.database_url is None and self.postgres_host:
            self.database_url = connection_url("postgresql", self.postgres_host, self.postgres_port,
                                               self.postgres_user, self.postgres_password, self.postgres_db)
        if self.checkpoint_database_url is None:
            self.checkpoint_database_url = self.database_url
        if self.redis_url is None and self.redis_host:
            self.redis_url = connection_url("redis", self.redis_host, self.redis_port, self.redis_user,
                                            self.redis_password, str(self.redis_db))
        return self

    @model_validator(mode="after")
    def validate_origin(self) -> Self:
        origin = urlsplit(self.app_origin)
        loopback = origin.hostname in {"127.0.0.1", "::1", "localhost"}
        if not (
            origin.hostname and not (origin.username or origin.password or origin.path
                                    or origin.query or origin.fragment)
            and (origin.scheme == "https" or (
                origin.scheme == "http" and loopback and self.environment != "production"
            ))
            and self.app_origin == f"{origin.scheme}://{origin.netloc}"
        ):
            raise ValueError("APP_ORIGIN must be an HTTPS origin or local development HTTP origin")
        return self

    @property
    def secure_cookie(self) -> bool:
        return self.app_origin.startswith("https://")

    @property
    def session_cookie(self) -> str:
        return "__Host-ai_session" if self.secure_cookie else "ai_session_dev"


@lru_cache
def settings() -> Settings:
    return Settings()


def connection_url(scheme: str, host: str, port: int, user: str, password: SecretStr | None, database: str) -> str:
    if not password or not password.get_secret_value():
        raise ValueError(f"Set the {scheme} password for the configured host")
    if any(char in host for char in "/?#@") or any(char.isspace() for char in host):
        raise ValueError(f"Invalid {scheme} host")
    address = f"[{host}]" if ":" in host and not host.startswith("[") else host
    credentials = f"{quote(user, safe='')}:{quote(password.get_secret_value(), safe='')}"
    return f"{scheme}://{credentials}@{address}:{port}/{quote(database, safe='')}"
