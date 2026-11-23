"""Provision a staff or client workspace account with a prompted password."""

import argparse
import getpass

from sqlalchemy import func, select

from trade_extract.config import get_settings
from trade_extract.db.models.client import Client
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.ids import new_id
from trade_extract.review.auth import password_hash

parser = argparse.ArgumentParser()
parser.add_argument("username")
parser.add_argument("--email", required=True, help="Address used for password reset links")
parser.add_argument(
    "--role", choices=("reviewer", "admin", "auditor", "client"), default="reviewer"
)
parser.add_argument("--client-slug", help="Required for a client workspace account")
args = parser.parse_args()
if (args.role == "client") != bool(args.client_slug):
    parser.error("--client-slug is required only for --role client")
if not args.username.strip() or len(args.username) > 128:
    raise SystemExit("Username must contain 1–128 characters")
email = args.email.strip().lower()
if not email or "@" not in email or len(email) > 320:
    raise SystemExit("Email must be a valid address")
password = getpass.getpass()
if len(password) < 12:
    raise SystemExit("Password must contain at least 12 characters")
with session_factory(make_engine(get_settings()))() as session:
    client_id = None
    if args.client_slug:
        client_id = session.scalar(select(Client.id).where(Client.slug == args.client_slug))
        if client_id is None:
            raise SystemExit("Client not found")
    if session.scalar(
        select(Reviewer.id).where(func.lower(Reviewer.username) == args.username.lower())
    ):
        raise SystemExit("Reviewer already exists")
    session.add(
        Reviewer(
            id=new_id("usr_"),
            client_id=client_id,
            username=args.username,
            email=email,
            display_name=args.username,
            role=args.role,
            password_hash=password_hash(password),
            is_active=client_id is None,
            client_login_enabled=client_id is not None,
            session_epoch=0,
        )
    )
    session.commit()
