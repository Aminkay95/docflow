from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.conftest import ApiHarness
from trade_extract.api.routes.health import REQUIRED_DATABASE_REVISION

pytestmark = pytest.mark.integration


def test_liveness_needs_no_auth(api: ApiHarness) -> None:
    response = api.client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_readiness_checks_database_storage_and_policy(api: ApiHarness) -> None:
    response = api.client.get("/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "storage": "ok", "policy": "ok"}


def test_readiness_is_503_without_an_active_policy(api: ApiHarness) -> None:
    with api.session_factory() as session:
        session.execute(text("UPDATE policies SET status = 'draft'"))
        session.commit()

    response = api.client.get("/v1/health/ready")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "service_unavailable"
    assert "policy" in error["message"]


def test_readiness_is_503_until_the_database_has_the_required_revision(api: ApiHarness) -> None:
    with api.session_factory.begin() as session:
        session.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": "0008_inbound_email"},
        )
    try:
        response = api.client.get("/v1/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "service_unavailable"
        assert "migration" in response.json()["error"]["message"]
        assert api.client.get("/v1/health").status_code == 200
    finally:
        with api.session_factory.begin() as session:
            session.execute(
                text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": REQUIRED_DATABASE_REVISION},
            )
    assert api.client.get("/v1/health/ready").status_code == 200


def test_expired_key_is_401(api: ApiHarness) -> None:
    with api.session_factory() as session:
        session.execute(text("UPDATE api_keys SET expires_at = now() - interval '1 hour'"))
        session.commit()
    response = api.client.get("/v1/health/ready")
    assert response.status_code == 200

    response = api.client.post(
        "/v1/extract",
        headers=api.headers,
        files=[("documents", ("doc.pdf", b"%PDF-1.4", "application/pdf"))],
    )
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"
    assert "expired" in error["message"]


def test_suspended_client_gets_a_dedicated_403(api: ApiHarness) -> None:
    with api.session_factory() as session:
        session.execute(text("UPDATE clients SET status = 'suspended'"))
        session.commit()
    response = api.client.get("/v1/results/res_anything", headers=api.headers)
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "client_inactive"
    assert error["details"]["account_status"] == "suspended"
    assert "suspended" in error["message"]
    assert "required_scope" not in error["details"]
