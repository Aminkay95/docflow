from __future__ import annotations

import pytest

from tests.support import temp_snapshot


@pytest.fixture
def temp_upload_snapshot() -> set[str]:
    return temp_snapshot()
