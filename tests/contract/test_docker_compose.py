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
    "GROQ_API_KEY",
    "GROQ_BASE_URL",
}


def test_provider_budget_and_paddlex_cache_are_worker_only():
    environment = os.environ.copy()
    environment.update(
        {
            "PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "compose-contract-key",
            "PROVIDER_MAX_OUTPUT_TOKENS": "2048",
            "PROVIDER_TIMEOUT_SECONDS": "240",
            "PROVIDER_CONNECT_TIMEOUT_SECONDS": "12",
            "STAGE_PROMPTS_ENABLED": "true",
            "LLM_EXTRACTION_PRIORITY": "true",
            "LLM_CLASSIFICATION_ENABLED": "false",
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
    for key, value in {
        "PROVIDER_TIMEOUT_SECONDS": "240",
        "PROVIDER_CONNECT_TIMEOUT_SECONDS": "12",
        "STAGE_PROMPTS_ENABLED": "true",
        "LLM_EXTRACTION_PRIORITY": "true",
        "LLM_CLASSIFICATION_ENABLED": "false",
    }.items():
        assert services["worker"]["environment"][key] == value
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


def test_release_compose_sets_ten_workers_and_forwards_worker_overrides():
    overrides = {
        "PROVIDER": "groq",
        "GROQ_API_KEY": "contract-key",
        "GROQ_BASE_URL": "https://test.example/v1",
        "LLM_CLASSIFICATION_ENABLED": "false",
        "STAGE_PROMPTS_ENABLED": "false",
        "OCR_RECOGNITION_MODEL": "contract-rec",
        "PROVIDER_MAX_CALLS_PER_PAGE": "8",
        "PROVIDER_BACKOFF_MAX_SECONDS": "12",
    }
    environment = os.environ.copy()
    environment.pop("EXTRACTION_WORKER_REPLICAS", None)
    environment.update(
        {
            "API_IMAGE": "doc-hover-api:contract",
            "WORKER_IMAGE": "doc-hover-worker:contract",
            "DATABASE_URL": "postgresql+psycopg://test:test@db/doc_hover",
            "SECRET_KEY": "contract-secret",
            "API_KEY_PEPPER": "contract-pepper",
            "BASE_URL": "https://doc-hover.example",
            "S3_BUCKET": "contract-private-bucket",
            **overrides,
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            "compose.production.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    assert services["worker"]["deploy"]["replicas"] == 10
    assert overrides.items() <= services["worker"]["environment"].items()
    for service_name in ("api", "maintenance", "migrate"):
        assert PROVIDER_ENVIRONMENT.isdisjoint(services[service_name]["environment"])
