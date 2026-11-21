"""Validation never mutates a client or stages credentials on invalid input."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.domain.errors import DomainError
from trade_extract.services import clients

NOW = datetime(2026, 11, 21, tzinfo=UTC)


@pytest.mark.parametrize("scopes", [[], ["unknown"], ["extract:write", "unknown"]])
def test_unknown_or_empty_scopes_are_rejected(scopes):
    with pytest.raises(DomainError):
        clients.validate_scopes(scopes)


@pytest.mark.parametrize("expiry", [NOW, NOW - timedelta(seconds=1), datetime(2027, 1, 1)])
def test_expiry_must_be_future_and_timezone_aware(expiry):
    with pytest.raises(DomainError):
        clients.validate_expiry(expiry, NOW)


def test_valid_expiry_and_scope_deduplication():
    clients.validate_expiry(NOW + timedelta(seconds=1), NOW)
    assert clients.validate_scopes(["results:read", "results:read"]) == ["results:read"]


@pytest.mark.parametrize("name,slug", [(" ", "valid"), ("ok", ""), ("ok", "UPPER"), ("ok", "a/b")])
def test_invalid_details_leave_client_unchanged(name, slug):
    session = Mock()
    client = Client(id="cli_test", name="Original", slug="original", status="active")
    with pytest.raises(DomainError):
        clients.update_details(session, client, name=name, slug=slug, now=NOW)
    assert (client.name, client.slug) == ("Original", "original")


def test_invalid_provision_does_not_add_anything():
    session = Mock()
    with pytest.raises(DomainError):
        clients.provision(
            session,
            name="ok",
            slug="ok",
            webhook_url="ftp://host/",
            scopes=clients.DEFAULT_SCOPES,
            settings=Settings(),
            now=NOW,
        )
    session.add.assert_not_called()


def test_enabled_webhook_requires_url_and_does_not_mutate():
    client = Client(webhook_url="https://example.com", webhook_enabled=True)
    with pytest.raises(DomainError):
        clients.configure_webhook(client, url="", enabled=True, settings=Settings(), now=NOW)
    assert client.webhook_url == "https://example.com"
    assert client.webhook_enabled


def test_status_confirmation_and_terminal_state():
    client = Client(status="active")
    with pytest.raises(DomainError):
        clients.change_status(client, status="terminated", confirmed=False, now=NOW)
    assert client.status == "active" and client.terminated_at is None
    clients.change_status(client, status="suspended", confirmed=False, now=NOW)
    assert client.status == "suspended" and client.terminated_at is None
    clients.change_status(client, status="active", confirmed=False, now=NOW)
    clients.change_status(client, status="terminated", confirmed=True, now=NOW)
    assert client.terminated_at == NOW
    with pytest.raises(DomainError):
        clients.change_status(client, status="active", confirmed=False, now=NOW)
    assert client.status == "terminated" and client.terminated_at == NOW
