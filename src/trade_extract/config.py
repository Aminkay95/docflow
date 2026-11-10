from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_MARKERS = (
    "change-me",
    "development-only",
    "replace-me",
    "example-secret",
    "your-secret",
)

_DEFAULT_MAIL_FROM = "Trade Extract <no-reply@localhost>"


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

    # Password reset delivery. Resend wins when RESEND_API_KEY is set, then
    # SMTP when SMTP_HOST is set. With neither, the API writes messages to
    # MAIL_OUTBOX_DIR instead of sending them (development only).
    resend_api_key: SecretStr = SecretStr("")
    resend_api_url: str = "https://api.resend.com/emails"
    resend_from: str = ""
    resend_timeout_seconds: float = Field(default=10, gt=0, le=60)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = _DEFAULT_MAIL_FROM
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = Field(default=10, gt=0, le=60)
    mail_outbox_dir: Path = Path("./var/outbox")
    password_reset_ttl_minutes: int = Field(default=30, ge=5, le=1440)
    password_reset_min_length: int = Field(default=12, ge=8, le=128)

    provider: Literal["mock", "deepseek"] = "mock"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_vision_model: Literal["deepseek-flash"] = "deepseek-flash"
    deepseek_text_model: str = "deepseek-v4-pro"
    provider_timeout_seconds: float = Field(default=60, gt=0, le=300)
    provider_connect_timeout_seconds: float = Field(default=5, gt=0, le=60)
    provider_max_attempts: int = Field(default=3, ge=1, le=5)
    provider_max_repairs: int = Field(default=1, ge=0, le=2)
    provider_max_output_tokens: int = Field(default=8_192, ge=1, le=65_536)
    provider_max_evidence_characters_per_page: int = Field(default=24_000, ge=1, le=200_000)
    provider_max_estimated_input_tokens_per_page: int = Field(default=12_000, ge=1)
    provider_max_calls_per_page: int = Field(default=40, ge=1, le=200)
    provider_cost_target_usd_per_page: float = Field(default=0.04, gt=0, le=1)
    provider_cost_safety_ceiling_usd_per_page: float = Field(default=0.12, gt=0, le=2)
    provider_input_price_usd_per_million: float = Field(default=0.66, ge=0, le=100)
    provider_output_price_usd_per_million: float = Field(default=1.98, ge=0, le=100)
    provider_backoff_max_seconds: float = Field(default=30, gt=0, le=60)
    provider_max_images: int = Field(default=20, ge=1, le=100)
    provider_max_image_bytes: int = Field(default=20_000_000, ge=1, le=50_000_000)

    routing_version: str = "reader-routing-v1-provisional"
    routing_min_text_characters: int = Field(default=40, ge=1, le=100000)
    routing_dense_row_threshold: int = Field(default=20, ge=1)
    ocr_max_pixels: int = Field(default=40_000_000, ge=1)
    ocr_max_spans: int = Field(default=10000, ge=1)
    ocr_max_regions: int = Field(default=100, ge=1)
    ocr_max_rows: int = Field(default=2000, ge=1)
    ocr_max_cells: int = Field(default=20000, ge=1)
    ocr_max_html_bytes: int = Field(default=1000000, ge=1)
    ocr_detection_model: str = Field(default="PP-OCRv5_mobile_det", min_length=1)
    ocr_recognition_model: str = Field(default="PP-OCRv5_mobile_rec", min_length=1)
    table_wired_structure_model: str = Field(default="SLANet_plus", min_length=1)
    table_wireless_structure_model: str = Field(default="SLANet_plus", min_length=1)
    table_max_crops: int = Field(default=2000, ge=1)
    table_max_crop_pixels: int = Field(default=8_000_000, ge=1)
    table_max_total_crop_pixels: int = Field(default=80_000_000, ge=1)
    table_max_batches: int = Field(default=200, ge=1)

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

    # Page rendering and always-on segmentation. Handlers read these; no literal
    # rendering or classifier threshold lives in a handler. The confidence floor
    # gates both the top type's share of the top-two score and its raw weighted
    # score, so a single weak signal never classifies.
    page_render_dpi: int = Field(default=300, ge=36, le=1200)
    page_thumbnail_max_px: int = Field(default=512, ge=16, le=4096)
    page_image_quality: int = Field(default=85, ge=1, le=100)
    conditioning_max_pixels: int = Field(default=40_000_000, ge=1024, le=100_000_000)
    conditioning_analysis_max_px: int = Field(default=1600, ge=128, le=4096)
    conditioning_min_short_edge: int = Field(default=600, ge=16, le=10000)
    conditioning_min_contrast: float = Field(default=25, ge=0, le=255)
    conditioning_min_blur: float = Field(default=15, ge=0, le=10000)
    conditioning_min_ink: float = Field(default=0.001, ge=0, le=1)
    conditioning_max_ink: float = Field(default=0.8, ge=0, le=1)
    conditioning_max_skew: float = Field(default=12, ge=0, le=30)
    conditioning_max_enlargement: float = Field(default=1, ge=1, le=2)
    segmentation_min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)

    # ICM stage-file pipeline: stage-01 LLM classify between prepare and extract,
    # stage-02 type-file extraction contracts. Both default to legacy behavior.
    llm_classification_enabled: bool = False
    stage_prompts_enabled: bool = False
    # LLM-primary extraction: always call the provider with the stage contract
    # and document instead of taking the local-complete fast path. Local
    # candidates still merge in as corroboration (agreement/disagreement).
    llm_extraction_priority: bool = False

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
        if self.conditioning_min_ink >= self.conditioning_max_ink:
            raise ValueError("CONDITIONING_MIN_INK must be below CONDITIONING_MAX_INK")
        if self.provider_cost_safety_ceiling_usd_per_page < self.provider_cost_target_usd_per_page:
            raise ValueError(
                "PROVIDER_COST_SAFETY_CEILING_USD_PER_PAGE must be at least "
                "PROVIDER_COST_TARGET_USD_PER_PAGE"
            )
        if self.provider == "deepseek" and not self.deepseek_api_key.get_secret_value().strip():
            raise ValueError("DEEPSEEK_API_KEY is required when PROVIDER=deepseek")
        if not self.deepseek_base_url.startswith("https://"):
            raise ValueError("DEEPSEEK_BASE_URL must use HTTPS")
        if self.max_request_bytes == 0:
            overhead = 8 * 1024 * 1024
            self.max_request_bytes = self.max_documents_per_group * self.max_file_bytes + overhead
        if self.max_request_bytes <= self.max_file_bytes:
            raise ValueError("MAX_REQUEST_BYTES must exceed MAX_FILE_BYTES")
        if self.max_pages_per_submission < self.max_pages_per_file:
            raise ValueError("MAX_PAGES_PER_SUBMISSION must be at least MAX_PAGES_PER_FILE")
        if self.resend_api_key.get_secret_value().strip() and self.mail_from == _DEFAULT_MAIL_FROM:
            raise ValueError(
                "RESEND_FROM is required when RESEND_API_KEY is set; use an address on a "
                "domain verified in Resend"
            )
        if self.environment == "production":
            _reject_placeholder("SECRET_KEY", self.secret_key)
            _reject_placeholder("API_KEY_PEPPER", self.api_key_pepper)
            if not (self.resend_api_key.get_secret_value().strip() or self.smtp_host.strip()):
                raise ValueError(
                    "RESEND_API_KEY or SMTP_HOST is required in production so password "
                    "reset emails can be delivered"
                )
            if self.webhook_allow_private_targets:
                raise ValueError(
                    "WEBHOOK_ALLOW_PRIVATE_TARGETS is not permitted in production; "
                    "the SSRF guard must stay enabled"
                )
            if self.storage_backend == "s3" and not self.s3_bucket:
                raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        return self

    @property
    def mail_from(self) -> str:
        """Sender address shared by Resend and SMTP."""

        return self.resend_from.strip() or self.smtp_from

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
