from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_extract.config import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "development",
        "database_url": "postgresql+psycopg://trade:trade@localhost:5432/trade_extract",
        "secret_key": "unit-test-secret",
        "api_key_pepper": "unit-test-pepper",
        "storage_dir": tmp_path / "storage",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_limits_are_derived_from_configuration(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_documents_per_group=5, max_file_bytes=2048)
    assert settings.max_request_bytes == 5 * 2048 + 8 * 1024 * 1024
    assert settings.retry_after_seconds == 2


def test_explicit_request_cap_is_kept(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_file_bytes=2048, max_request_bytes=4096)
    assert settings.max_request_bytes == 4096


def test_production_rejects_env_example_placeholders(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _settings(
            tmp_path,
            environment="production",
            secret_key="change-me-development-secret-key",
            api_key_pepper="a-real-pepper-value",
        )
    with pytest.raises(ValidationError):
        _settings(
            tmp_path,
            environment="production",
            secret_key="a-real-secret-value",
            api_key_pepper="change-me-development-api-key-pepper",
        )


def test_production_accepts_real_secrets(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        environment="production",
        secret_key="a-real-production-secret",
        api_key_pepper="a-real-production-pepper",
    )
    assert settings.require_https_webhooks is True


def test_development_allows_local_http_webhooks(tmp_path: Path) -> None:
    assert _settings(tmp_path).require_https_webhooks is False
