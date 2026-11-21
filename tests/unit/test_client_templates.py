"""Client detail access labels use the supplied application clock."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trade_extract.review.routes import templates


@pytest.mark.parametrize("offset,label", [(-1, "Expired"), (1, "Active")])
def test_key_access_state_preserves_revocation_control(offset, label):
    now = datetime(2026, 11, 21, tzinfo=UTC)
    reviewer = SimpleNamespace(role="admin", display_name="Admin", username="admin")
    client = SimpleNamespace(
        id="cli_example",
        name="Example",
        slug="example",
        status="active",
        created_at=now,
        terminated_at=None,
        webhook_url=None,
        webhook_enabled=False,
    )
    key = SimpleNamespace(
        id="key_example",
        name="Example key",
        key_prefix="tx_test_prefix",
        scopes=["results:read"],
        expires_at=now + timedelta(days=offset),
        last_used_at=None,
        revoked_at=None,
    )
    html = templates.get_template("client.html").render(
        title="Example",
        request=SimpleNamespace(url=SimpleNamespace(path="/review/clients/cli_example")),
        reviewer=reviewer,
        client=client,
        keys=[key],
        now=now,
        scopes=[],
        counts={},
        breadcrumbs=[],
        csrf="test-csrf",
        read_only=False,
    )
    assert f">{label}</span>" in html
    assert "/review/clients/cli_example/keys/key_example/revoke" in html
    assert "Enable default webhook delivery" in html
    assert "does not cancel queued deliveries or explicit submission webhook overrides" in html
