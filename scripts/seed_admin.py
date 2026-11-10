"""Seed a development review-console admin account.

Development convenience only. Refuses to run against production unless
``--force`` is passed. Idempotent: re-running updates the password hash,
role and active flag, and bumps ``session_epoch`` so existing sessions are
revoked.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from trade_extract.config import get_settings
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.ids import new_id
from trade_extract.review.auth import password_hash

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "password123"
DEFAULT_EMAIL = "admin@example.com"
DEFAULT_ROLE = "admin"

MIN_PASSWORD_LENGTH = 12
CONNECT_TIMEOUT_SECONDS = 10


def with_connect_timeout(database_url: str) -> str:
    """Fail fast when a host resolves to an unreachable address (e.g. IPv6 localhost)."""
    if database_url.startswith("sqlite") or "connect_timeout" in database_url:
        return database_url
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}connect_timeout={CONNECT_TIMEOUT_SECONDS}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument(
        "--role",
        choices=("reviewer", "admin", "auditor"),
        default=DEFAULT_ROLE,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow seeding when ENVIRONMENT=production",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    if settings.environment == "production" and not args.force:
        print(
            "refusing to seed a default account in production (pass --force to override)",
            file=sys.stderr,
        )
        return 1

    username = args.username.strip()
    if not username or len(username) > 128:
        print("username must contain 1-128 characters", file=sys.stderr)
        return 1

    email = args.email.strip().lower()
    if not email or "@" not in email or len(email) > 320:
        print("email must be a valid address", file=sys.stderr)
        return 1

    if len(args.password) < MIN_PASSWORD_LENGTH:
        print(
            f"warning: password is shorter than the {MIN_PASSWORD_LENGTH}-character "
            "minimum used by scripts/provision_reviewer.py",
            file=sys.stderr,
        )

    engine = make_engine(
        settings.model_copy(update={"database_url": with_connect_timeout(settings.database_url)})
    )
    factory = session_factory(engine)
    try:
        with factory() as session:
            existing = session.scalar(
                select(Reviewer).where(func.lower(Reviewer.username) == username.lower())
            )
            if existing is None:
                session.add(
                    Reviewer(
                        id=new_id("usr_"),
                        username=username,
                        email=email,
                        display_name=username,
                        role=args.role,
                        password_hash=password_hash(args.password),
                        is_active=True,
                        session_epoch=0,
                    )
                )
                action = "created"
            else:
                existing.username = username
                existing.email = email
                existing.display_name = username
                existing.role = args.role
                existing.password_hash = password_hash(args.password)
                existing.is_active = True
                existing.session_epoch += 1
                action = "updated"

            session.commit()

        print(f"{action} {args.role} account '{username}'")
        print(f"sign in at /review/login as '{username}'")
        print(f"password reset email: {email}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
