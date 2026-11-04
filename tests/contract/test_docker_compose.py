import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI unavailable")
PROVIDER_ENVIRONMENT = {
    "PROVIDER",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_VISION_MODEL",
    "DEEPSEEK_TEXT_MODEL",
    "PROVIDER_MAX_OUTPUT_TOKENS",
}


def test_provider_budget_and_paddlex_cache_are_worker_only():
    environment = os.environ.copy()
    environment.update(
        {
            "PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "compose-contract-key",
            "PROVIDER_MAX_OUTPUT_TOKENS": "2048",
            "OCR_DETECTION_MODEL": "compose-det",
            "OCR_RECOGNITION_MODEL": "compose-rec",
            "TABLE_WIRED_STRUCTURE_MODEL": "compose-wired",
            "TABLE_WIRELESS_STRUCTURE_MODEL": "compose-wireless",
        }
    )
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)
    services = config["services"]

    assert services["worker"]["environment"]["PROVIDER_MAX_OUTPUT_TOKENS"] == "2048"
    assert services["worker"]["environment"]["DEEPSEEK_API_KEY"] == "compose-contract-key"
    assert services["worker"]["environment"]["OCR_DETECTION_MODEL"] == "compose-det"
    assert services["worker"]["environment"]["OCR_RECOGNITION_MODEL"] == "compose-rec"
    assert services["worker"]["environment"]["TABLE_WIRED_STRUCTURE_MODEL"] == "compose-wired"
    assert services["worker"]["environment"]["TABLE_WIRELESS_STRUCTURE_MODEL"] == "compose-wireless"
    for service_name in ("api", "maintenance", "migrate"):
        assert PROVIDER_ENVIRONMENT.isdisjoint(services[service_name]["environment"])

    assert "paddlex_cache" in config["volumes"]
    assert {
        "type": "volume",
        "source": "paddlex_cache",
        "target": "/home/appuser/.paddlex",
        "volume": {},
    } in services["worker"]["volumes"]
