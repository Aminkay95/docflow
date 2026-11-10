from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from trade_extract.db.models.password_reset import PasswordReset
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.review.auth import password_hash

pytestmark = pytest.mark.integration

EMAIL = "reset@example.com"
OLD_PASSWORD = "old-password-123"
NEW_PASSWORD = "new-password-456"


def add_reviewer(api, *, email: str = EMAIL, username: str = "reset-user") -> None:
    with api.session_factory() as session:
        session.add(
            Reviewer(
                id="usr_reset",
                username=username,
                email=email,
                password_hash=password_hash(OLD_PASSWORD),
                display_name=username,
                role="reviewer",
                is_active=True,
                session_epoch=0,
            )
        )
        session.commit()


def csrf_from(html: str) -> str:
    return re.search(r'name="csrf" type="hidden" value="([^"]+)"', html)[1]


def request_reset(api, email: str = EMAIL):
    page = api.client.get("/review/forgot-password")
    return api.client.post(
        "/review/forgot-password",
        data={"email": email, "csrf": csrf_from(page.text)},
    )


def reset_token(api) -> str:
    files = sorted(api.settings.mail_outbox_dir.glob("*.eml"))
    assert files, "expected a reset email in the outbox"
    match = re.search(r"token=([A-Za-z0-9_\-]+)", files[-1].read_text(encoding="utf-8"))
    assert match
    return match.group(1)


def login(api, password: str, username: str = "reset-user"):
    page = api.client.get("/review/login")
    return api.client.post(
        "/review/login",
        data={"username": username, "password": password, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )


def test_reset_link_changes_password_once_and_revokes_sessions(api):
    add_reviewer(api)
    assert login(api, OLD_PASSWORD).status_code == 303
    assert api.client.get("/review").status_code == 200

    response = request_reset(api)
    assert response.status_code == 200
    assert "If that email is registered" in response.text

    token = reset_token(api)
    form = api.client.get(f"/review/reset-password?token={token}")
    assert form.status_code == 200
    assert "Choose a new password" in form.text

    changed = api.client.post(
        "/review/reset-password",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "confirm": NEW_PASSWORD,
            "csrf": csrf_from(form.text),
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/review/login?reset=1"

    assert api.client.get("/review").status_code == 401
    assert login(api, OLD_PASSWORD).status_code == 401
    assert login(api, NEW_PASSWORD).status_code == 303
    assert api.client.get(f"/review/reset-password?token={token}").status_code == 400


def test_unknown_email_gets_the_same_response_without_sending(api):
    response = request_reset(api, "nobody@example.com")
    assert response.status_code == 200
    assert "If that email is registered" in response.text
    outbox = api.settings.mail_outbox_dir
    if outbox.exists():
        assert not list(outbox.glob("*.eml"))


def test_expired_token_is_rejected(api):
    add_reviewer(api)
    request_reset(api)
    token = reset_token(api)
    with api.session_factory() as session:
        row = session.scalar(select(PasswordReset))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    assert api.client.get(f"/review/reset-password?token={token}").status_code == 400


def test_requesting_again_invalidates_the_previous_link(api):
    add_reviewer(api)
    request_reset(api)
    first = reset_token(api)
    request_reset(api)
    second = reset_token(api)
    assert first != second
    assert api.client.get(f"/review/reset-password?token={first}").status_code == 400
    assert api.client.get(f"/review/reset-password?token={second}").status_code == 200


def test_password_rules_keep_the_link_usable(api):
    add_reviewer(api)
    request_reset(api)
    token = reset_token(api)
    form = api.client.get(f"/review/reset-password?token={token}")
    mismatch = api.client.post(
        "/review/reset-password",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "confirm": "different-password-789",
            "csrf": csrf_from(form.text),
        },
    )
    assert mismatch.status_code == 400
    assert "do not match" in mismatch.text

    short = api.client.post(
        "/review/reset-password",
        data={
            "token": token,
            "password": "short",
            "confirm": "short",
            "csrf": csrf_from(mismatch.text),
        },
    )
    assert short.status_code == 400
    assert "at least 12" in short.text

    changed = api.client.post(
        "/review/reset-password",
        data={
            "token": token,
            "password": NEW_PASSWORD,
            "confirm": NEW_PASSWORD,
            "csrf": csrf_from(short.text),
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303


def test_reset_requests_are_rate_limited(api):
    for _ in range(3):
        assert request_reset(api).status_code == 200
    limited = request_reset(api)
    assert limited.status_code == 429
    assert "Too many reset requests" in limited.text
