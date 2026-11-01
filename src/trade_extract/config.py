from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_MARKERS = (
    "change-me",
    "development-only",
    "replace-me",
    "example-secret",
    "your-secret",
)


class Settings(BaseSettings):
    """Environment-only process configuration.

    Limits resolved here are the single source of truth for handlers; handlers never
    carry literals for them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://trade:trade@localhost:5432/trade_extract"
    secret_key: str = "development-only-secret"
    api_key_pepper: str = "development-only-pepper"

    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    storage_dir: Path = Path("./var/storage")
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"

    base_url: str = "http://localhost:8000"

    max_documents_per_group: int = Field(default=20, ge=1, le=100)
    max_file_bytes: int = Field(default=26_214_400, ge=1024)
    max_pages_per_file: int = Field(default=500, ge=1)
    max_pages_per_submission: int = Field(default=2_000, ge=1)
    # 0 means "derive from the per-file cap plus multipart framing overhead".
    max_request_bytes: int = Field(default=0, ge=0)

    max_job_ref_length: int = Field(default=128, ge=1)
    min_idempotency_key_length: int = Field(default=8, ge=1)
    max_idempotency_key_length: int = Field(default=128, ge=8)
    max_webhook_url_length: int = Field(default=2048, ge=1)

    webhook_max_attempts: int = Field(default=8, ge=1, le=20)
    webhook_timeout_seconds: float = Field(default=10.0, gt=0)
    webhook_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    webhook_allow_private_targets: bool = False
    webhook_stale_after_seconds: int = Field(default=900, ge=1)

    task_stale_after_seconds: int = Field(default=900, ge=1)
    task_max_requeues: int = Field(default=3, ge=1)
    task_backoff_base_seconds: int = Field(default=2, ge=1)
    task_backoff_max_seconds: int = Field(default=300, ge=1)
    retry_after_seconds: int = Field(default=2, ge=1)

    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_id: str = ""
    job_max_attempts: int = Field(default=3, ge=1)
    database_pool_size: int = Field(default=5, ge=1)
    database_pool_max_overflow: int = Field(default=10, ge=0)

    @field_validator("base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _resolve_limits_and_check_secrets(self) -> Settings:
        if self.max_request_bytes == 0:
            overhead = 8 * 1024 * 1024
            self.max_request_bytes = self.max_documents_per_group * self.max_file_bytes + overhead
        if self.max_request_bytes <= self.max_file_bytes:
            raise ValueError("MAX_REQUEST_BYTES must exceed MAX_FILE_BYTES")
        if self.max_pages_per_submission < self.max_pages_per_file:
            raise ValueError("MAX_PAGES_PER_SUBMISSION must be at least MAX_PAGES_PER_FILE")
        if self.environment == "production":
            _reject_placeholder("SECRET_KEY", self.secret_key)
            _reject_placeholder("API_KEY_PEPPER", self.api_key_pepper)
            if self.webhook_allow_private_targets:
                raise ValueError(
                    "WEBHOOK_ALLOW_PRIVATE_TARGETS is not permitted in production; "
                    "the SSRF guard must stay enabled"
                )
            if self.storage_backend == "s3" and not self.s3_bucket:
                raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        return self

    @property
    def allowed_content_types(self) -> list[str]:
        return [
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/tiff",
        ]

    @property
    def require_https_webhooks(self) -> bool:
        return self.environment != "development"

    @property
    def docs_base_url(self) -> str:
        return "https://docs.trade-extract.example/errors"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _reject_placeholder(name: str, value: str) -> None:
    lowered = value.strip().lower()
    if not lowered or any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        raise ValueError(
            f"{name} must be set to a real secret in production; "
            "copying a placeholder from .env.example is not accepted"
        )
