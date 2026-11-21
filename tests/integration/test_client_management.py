"""Client onboarding authorization, persistence and real API access effects."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.integration.test_review_trust import login
from tests.support import make_pdf
from trade_extract.db.models.api_key import ApiKey
from trade_extract.db.models.client import Client
from trade_extract.db.models.job import Job
from trade_extract.domain.hashing import hash_api_key
from trade_extract.services import clients
from trade_extract.services.webhooks import decrypt_webhook_secret

pytestmark = pytest.mark.integration


def onboard(api, headers, slug="console-client", **overrides):
    return api.client.post(
        "/review/clients",
        headers=headers,
        json={"name": "Console Client", "slug": slug, **overrides},
    )


def test_onboarding_credentials_are_once_and_work_for_extraction(api):
    headers = login(api, "admin", "admin")
    assert 'href="/review/clients/new"' in api.client.get("/review/clients").text
    assert api.client.get("/review/clients/new").status_code == 200
    response = onboard(api, headers, webhook_url="https://hooks.example.test/receive")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    credentials = response.json()
    client_id = credentials["client_id"]
    with api.session_factory() as session:
        client = session.get(Client, client_id)
        key = session.scalar(select(ApiKey).where(ApiKey.client_id == client_id))
        assert client.status == "active" and client.webhook_enabled
        assert (
            decrypt_webhook_secret(client.webhook_secret_ciphertext, api.settings.secret_key)
            == credentials["webhook_secret"]
        )
        assert key.key_hash == hash_api_key(api.settings.api_key_pepper, credentials["api_key"])
        assert key.scopes == clients.DEFAULT_SCOPES
    for _ in range(2):
        detail = api.client.get(credentials["client_url"])
        assert detail.status_code == 200
        assert credentials["api_key"] not in detail.text
        assert credentials["webhook_secret"] not in detail.text
        assert credentials["key_prefix"] in detail.text
    submitted = api.client.post(
        "/v1/extract",
        headers={"Authorization": f"Bearer {credentials['api_key']}"},
        files=[("documents", ("doc.pdf", make_pdf(), "application/pdf"))],
    )
    assert submitted.status_code == 202, submitted.text
    with api.session_factory() as session:
        assert session.get(Job, submitted.json()["job_id"]).client_id == client_id


@pytest.mark.parametrize("role", ["reviewer", "auditor"])
def test_nonadmins_read_metadata_but_cannot_manage_or_see_keys(api, role):
    headers = login(api, role, role)
    page = api.client.get("/review/clients")
    assert page.status_code == 200 and 'href="/review/clients/new"' not in page.text
    page = api.client.get(f"/review/clients/{api.client_id}")
    assert page.status_code == 200 and "Create API key" not in page.text
    assert api.api_key[:16] not in page.text
    assert api.client.get("/review/clients/new").status_code == 403
    assert onboard(api, headers).status_code == 403
    for suffix, payload in [
        ("details", {"name": "Changed", "slug": "changed"}),
        ("keys", {"name": "key", "scopes": ["results:read"]}),
        ("webhook", {"enabled": False}),
        ("webhook/rotate", {}),
        ("status", {"status": "suspended"}),
        ("keys/unknown/revoke", {}),
    ]:
        assert (
            api.client.post(
                f"/review/clients/{api.client_id}/{suffix}", json=payload, headers=headers
            ).status_code
            == 403
        )


def test_missing_session_and_csrf_do_not_provision(api):
    assert onboard(api, {}).status_code == 401
    login(api, "admin", "admin")
    denied = onboard(api, {})
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store"
    with api.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Client)) == 1


def test_validation_conflicts_and_atomic_rollback(api):
    headers = login(api, "admin", "admin")
    for override in [
        {"name": " "},
        {"slug": "bad slug"},
        {"scopes": ["admin"]},
        {"webhook_url": "ftp://invalid"},
    ]:
        assert onboard(api, headers, **override).status_code == 400
    assert onboard(api, headers).status_code == 200
    assert onboard(api, headers).status_code == 409
    # Force the database uniqueness-race branch after validation has succeeded.
    with patch(
        "trade_extract.services.clients.create_key",
        side_effect=IntegrityError("insert", {}, Exception()),
    ):
        assert onboard(api, headers, slug="racing-client").status_code == 409
    with api.session_factory() as session:
        assert session.scalar(select(Client.id).where(Client.slug == "racing-client")) is None
        assert session.scalar(select(func.count()).select_from(Client)) == 2
        assert session.scalar(select(func.count()).select_from(ApiKey)) == 2


def test_key_lifecycle_checks_ownership_scopes_expiry_and_revocation(api):
    headers = login(api, "admin", "admin")
    client = onboard(api, headers).json()
    base = client["client_url"]
    for payload in [
        {"name": "key", "scopes": ["unknown"]},
        {"name": "key", "scopes": ["results:read"], "expires_at": "yesterday"},
        {"name": "key", "scopes": ["results:read"], "expires_at": "2020-01-01T00:00:00Z"},
        {"name": "key", "scopes": ["results:read"], "expires_at": "2099-01-01T00:00:00"},
    ]:
        assert api.client.post(base + "/keys", headers=headers, json=payload).status_code in (
            400,
            422,
        )
    response = api.client.post(
        base + "/keys",
        headers=headers,
        json={
            "name": "Read only",
            "scopes": ["results:read"],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["api_key"]
    assert response.headers["cache-control"] == "no-store"
    auth = {"Authorization": f"Bearer {token}"}
    assert api.client.get("/v1/results/res_unknown", headers=auth).status_code == 404
    denied = api.client.post(
        "/v1/extract",
        headers=auth,
        files=[("documents", ("doc.pdf", make_pdf(), "application/pdf"))],
    )
    assert denied.status_code == 403
    with api.session_factory() as session:
        key = session.scalar(
            select(ApiKey).where(
                ApiKey.key_hash == hash_api_key(api.settings.api_key_pepper, token)
            )
        )
        assert key.scopes == ["results:read"]
        key_id = key.id
    assert (
        api.client.post(
            f"/review/clients/{api.client_id}/keys/{key_id}/revoke", headers=headers
        ).status_code
        == 404
    )
    assert api.client.get("/v1/results/res_unknown", headers=auth).status_code == 404
    assert api.client.post(base + f"/keys/{key_id}/revoke", headers=headers).status_code == 200
    assert api.client.get("/v1/results/res_unknown", headers=auth).status_code == 401


def test_webhook_and_status_lifecycle(api):
    headers = login(api, "admin", "admin")
    created = onboard(api, headers).json()
    base = created["client_url"]
    auth = {"Authorization": f"Bearer {created['api_key']}"}
    assert (
        api.client.post(base + "/webhook", headers=headers, json={"enabled": True}).status_code
        == 400
    )
    assert (
        api.client.post(
            base + "/webhook", headers=headers, json={"enabled": True, "webhook_url": "ftp://host"}
        ).status_code
        == 400
    )
    assert (
        api.client.post(
            base + "/webhook",
            headers=headers,
            json={"enabled": True, "webhook_url": "https://example.test/hook"},
        ).status_code
        == 200
    )
    rotated = api.client.post(base + "/webhook/rotate", headers=headers)
    assert rotated.headers["cache-control"] == "no-store"
    secret = rotated.json()["webhook_secret"]
    assert secret != created["webhook_secret"]
    assert secret not in api.client.get(base).text
    with api.session_factory() as session:
        client = session.get(Client, created["client_id"])
        assert (
            decrypt_webhook_secret(client.webhook_secret_ciphertext, api.settings.secret_key)
            == secret
        )
    assert (
        api.client.post(base + "/status", headers=headers, json={"status": "bogus"}).status_code
        == 400
    )
    assert (
        api.client.post(
            base + "/status", headers=headers, json={"status": "terminated"}
        ).status_code
        == 400
    )
    for state, expected in [("suspended", 403), ("active", 404), ("terminated", 403)]:
        response = api.client.post(
            base + "/status",
            headers=headers,
            json={"status": state, "confirmed": state == "terminated"},
        )
        assert response.status_code == 200, response.text
        assert api.client.get("/v1/results/res_unknown", headers=auth).status_code == expected
        with api.session_factory() as session:
            client = session.get(Client, created["client_id"])
            assert (client.terminated_at is not None) == (state == "terminated")
    assert (
        api.client.post(base + "/status", headers=headers, json={"status": "active"}).status_code
        == 409
    )


def test_details_update_and_conflict_preserve_existing_accounts(api):
    headers = login(api, "admin", "admin")
    created = onboard(api, headers).json()
    base = created["client_url"]
    response = api.client.post(
        base + "/details", headers=headers, json={"name": "Renamed", "slug": "renamed"}
    )
    assert response.status_code == 200
    with api.session_factory() as session:
        existing = session.get(Client, api.client_id)
        existing_slug = existing.slug
    response = api.client.post(
        base + "/details", headers=headers, json={"name": "Should not save", "slug": existing_slug}
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    with api.session_factory() as session:
        changed = session.get(Client, created["client_id"])
        assert (changed.name, changed.slug) == ("Renamed", "renamed")
        assert session.get(Client, api.client_id).slug == existing_slug


def test_expired_key_is_rejected_immediately(api):
    headers = login(api, "admin", "admin")
    created = onboard(api, headers).json()
    auth = {"Authorization": f"Bearer {created['api_key']}"}
    assert api.client.get("/v1/results/res_unknown", headers=auth).status_code == 404
    with api.session_factory() as session:
        key = session.scalar(select(ApiKey).where(ApiKey.client_id == created["client_id"]))
        key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert api.client.get("/v1/results/res_unknown", headers=auth).status_code == 401


def test_failed_commit_never_discloses_or_persists_credentials(api):
    headers = login(api, "admin", "admin")
    failing_client = TestClient(api.app, raise_server_exceptions=False)
    failing_client.cookies.update(api.client.cookies)
    try:
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("commit failed")):
            response = failing_client.post(
                "/review/clients",
                headers=headers,
                json={"name": "No partial client", "slug": "commit-failed"},
            )
    finally:
        failing_client.close()
    assert response.status_code == 500
    assert '"api_key"' not in response.text and '"webhook_secret"' not in response.text
    with api.session_factory() as session:
        assert session.scalar(select(Client.id).where(Client.slug == "commit-failed")) is None
        assert session.scalar(select(func.count()).select_from(Client)) == 1
        assert session.scalar(select(func.count()).select_from(ApiKey)) == 1
