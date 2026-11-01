"""Create a client, an API key and a webhook secret out of band.

The API key and webhook secret are printed once. Only the HMAC of the key and the
encrypted webhook secret are stored.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys

from trade_extract.config import get_settings
from trade_extract.db.models.api_key import ApiKey
from trade_extract.db.models.client import Client
from trade_extract.db.repositories import clients as clients_repo
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.clock import SystemClock
from trade_extract.domain.errors import DomainError
from trade_extract.domain.hashing import hash_api_key
from trade_extract.domain.ids import new_id
from trade_extract.services.limits import validate_webhook_url
from trade_extract.services.webhooks import encrypt_webhook_secret

DEFAULT_SCOPES = ["extract:write", "results:read", "account:read", "account:write"]


def generate_api_key(environment: str) -> str:
    prefix = "tx_live_" if environment == "production" else "tx_test_"
    random_part = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    return prefix + random_part


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Client display name")
    parser.add_argument("--slug", required=True, help="Unique client slug")
    parser.add_argument("--webhook-url", default=None, help="Default webhook URL")
    parser.add_argument(
        "--scopes",
        default=",".join(DEFAULT_SCOPES),
        help="Comma-separated scopes",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    try:
        webhook_url = validate_webhook_url(args.webhook_url, settings)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    engine = make_engine(settings)
    factory = session_factory(engine)
    clock = SystemClock()

    with factory() as session:
        if clients_repo.get_client_by_slug(session, args.slug) is not None:
            print(f"client slug '{args.slug}' already exists", file=sys.stderr)
            return 2

        api_key_value = generate_api_key(settings.environment)
        webhook_secret = "whsec_" + secrets.token_urlsafe(32)
        scopes = [scope.strip() for scope in args.scopes.split(",") if scope.strip()]

        client = Client(
            id=new_id("cli_"),
            name=args.name,
            slug=args.slug,
            status="active",
            webhook_url=webhook_url,
            webhook_enabled=bool(webhook_url),
            webhook_secret_ciphertext=encrypt_webhook_secret(webhook_secret, settings.secret_key),
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        session.add(client)
        session.flush()

        api_key = ApiKey(
            id=new_id("key_"),
            client_id=client.id,
            name=f"{args.slug} default",
            key_prefix=api_key_value[:16],
            key_hash=hash_api_key(settings.api_key_pepper, api_key_value),
            scopes=scopes,
            created_at=clock.now(),
        )
        session.add(api_key)
        session.commit()

        print(
            json.dumps(
                {
                    "client_id": client.id,
                    "api_key": api_key_value,
                    "key_prefix": api_key.key_prefix,
                    "webhook_secret": webhook_secret,
                    "scopes": scopes,
                },
                indent=2,
            )
        )

    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
