from __future__ import annotations

import pytest

from tests.support import temp_snapshot
from trade_extract.config import Settings


@pytest.fixture(scope="session", autouse=True)
def disable_developer_settings_file():
    # Tests supply explicit settings or use defaults. A developer's account,
    # provider and extraction switches must not change recorded behavior.
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(Settings.model_config, "env_file", None)
        yield


@pytest.fixture
def temp_upload_snapshot() -> set[str]:
    return temp_snapshot()
