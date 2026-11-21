"""Create a client, an API key and a webhook secret out of band.

The API key and webhook secret are printed once. Only the HMAC of the key and the
encrypted webhook secret are stored.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy.exc import IntegrityError

from trade_extract.config import get_settings
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.clock import SystemClock
from trade_extract.domain.errors import DomainError
from trade_extract.services.clients import DEFAULT_SCOPES, provision


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
    engine = make_engine(settings)
    try:
        with session_factory(engine)() as session:
            try:
                _client, credentials = provision(
                    session,
                    name=args.name,
                    slug=args.slug,
                    webhook_url=args.webhook_url,
                    scopes=[scope.strip() for scope in args.scopes.split(",") if scope.strip()],
                    settings=settings,
                    now=SystemClock().now(),
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                print(f"client slug '{args.slug}' already exists", file=sys.stderr)
                return 2
            except DomainError as exc:
                session.rollback()
                print(exc.message, file=sys.stderr)
                return 2 if exc.code == "client_slug_conflict" else 1
            print(json.dumps(credentials, indent=2))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
