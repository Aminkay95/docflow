from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import ApiHarness
from tests.support import make_pdf

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_script(api: ApiHarness, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DATABASE_URL": api.settings.database_url,
        "SECRET_KEY": api.settings.secret_key,
        "API_KEY_PEPPER": api.settings.api_key_pepper,
        "ENVIRONMENT": "development",
    }
    return subprocess.run(
        [sys.executable, *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_bootstrap_and_create_client_then_authenticate(api: ApiHarness) -> None:
    bootstrap = run_script(api, "scripts/bootstrap.py")
    assert bootstrap.returncode == 0, bootstrap.stderr
    assert "policy" in bootstrap.stdout.lower()

    created = run_script(
        api,
        "scripts/create_client.py",
        "--name",
        "Script Client",
        "--slug",
        "script-client",
    )
    assert created.returncode == 0, created.stderr
    credentials = json.loads(created.stdout)
    assert credentials["client_id"].startswith("cli_")
    assert credentials["api_key"].startswith("tx_test_")

    response = api.client.post(
        "/v1/extract",
        headers={"Authorization": f"Bearer {credentials['api_key']}"},
        data={"job_ref": "BL-SCRIPT"},
        files=[("documents", ("doc.pdf", make_pdf(), "application/pdf"))],
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"


def test_bootstrap_is_idempotent_and_advances_the_version(api: ApiHarness) -> None:
    from sqlalchemy import text

    first = run_script(api, "scripts/bootstrap.py")
    assert first.returncode == 0, first.stderr
    assert "already exists" in first.stdout

    # Retiring the active policy must not make the next run reuse version 1.
    with api.session_factory() as session:
        session.execute(text("UPDATE policies SET status = 'retired' WHERE status = 'active'"))
        session.commit()

    second = run_script(api, "scripts/bootstrap.py")
    assert second.returncode == 0, second.stderr
    assert "policy v2" in second.stdout

    third = run_script(api, "scripts/bootstrap.py")
    assert third.returncode == 0, third.stderr
    assert "already exists" in third.stdout

    with api.session_factory() as session:
        rows = session.execute(text("SELECT version, status FROM policies ORDER BY version")).all()
    assert [(row[0], row[1]) for row in rows] == [(1, "retired"), (2, "active")]


def test_create_client_rejects_an_invalid_webhook_url(api: ApiHarness) -> None:
    result = run_script(
        api,
        "scripts/create_client.py",
        "--name",
        "Bad Hook",
        "--slug",
        "bad-hook",
        "--webhook-url",
        "ftp://hooks.example.test/ingest",
    )
    assert result.returncode == 1
    assert "webhook_url" in result.stderr
    assert "provide an absolute http(s) url" in result.stderr.lower()
