from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("paddle", "paddleocr", "cv2", "numpy", "pypdfium2", "trade_extract.providers")

pytestmark = pytest.mark.contract


def test_api_import_does_not_load_ocr_or_pdf_libraries() -> None:
    code = (
        "import sys\n"
        "import trade_extract.api.app\n"
        "import trade_extract.domain.conditioning\n"
        "import trade_extract.services.conditioning\n"
        "import trade_extract.services.routing\n"
        "import trade_extract.services.tables\n"
        f"print(','.join(name for name in {FORBIDDEN!r} if name in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip() == "", result.stderr
