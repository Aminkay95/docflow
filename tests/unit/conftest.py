from __future__ import annotations

from pathlib import Path

import pytest

from trade_extract.config import Settings


@pytest.fixture
def unit_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="development",
        database_url="postgresql+psycopg://trade:trade@localhost:5432/trade_extract",
        secret_key="unit-test-secret",
        api_key_pepper="unit-test-pepper",
        storage_dir=tmp_path / "storage",
        base_url="http://testserver",
        resend_api_key="",
        smtp_host="",
        webhook_allow_private_targets=True,
    )
