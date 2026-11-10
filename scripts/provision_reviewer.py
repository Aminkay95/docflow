"""Provision an internal reviewer without storing a plaintext password."""

import argparse
import getpass

from sqlalchemy import func, select

from trade_extract.config import get_settings
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.ids import new_id
from trade_extract.review.auth import password_hash

parser = argparse.ArgumentParser()
parser.add_argument("username")
parser.add_argument("--email", required=True, help="Address used for password reset links")
parser.add_argument("--role", choices=("reviewer", "admin", "auditor"), default="reviewer")
args = parser.parse_args()
if not args.username.strip() or len(args.username) > 128:
    raise SystemExit("Username must contain 1–128 characters")
email = args.email.strip().lower()
if not email or "@" not in email or len(email) > 320:
    raise SystemExit("Email must be a valid address")
password = getpass.getpass()
if len(password) < 12:
    raise SystemExit("Password must contain at least 12 characters")
with session_factory(make_engine(get_settings()))() as session:
    if session.scalar(
        select(Reviewer.id).where(func.lower(Reviewer.username) == args.username.lower())
    ):
        raise SystemExit("Reviewer already exists")
    session.add(
        Reviewer(
            id=new_id("usr_"),
            username=args.username,
            email=email,
            display_name=args.username,
            role=args.role,
            password_hash=password_hash(password),
            is_active=True,
            session_epoch=0,
        )
    )
    session.commit()
