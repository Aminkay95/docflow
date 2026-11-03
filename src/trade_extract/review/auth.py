"""Versioned scrypt passwords and signed, revocable sessions."""

import hashlib
import hmac
import secrets
from typing import Any

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from trade_extract.api.deps import get_session
from trade_extract.db.models.reviewer import Reviewer


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt$16384$8$1${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=salt.encode(), n=int(n), r=int(r), p=int(p)
        ).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


def signer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.settings.secret_key, salt="review-v1")


def principal(request: Request, session: Session = Depends(get_session)) -> Reviewer:
    try:
        data: dict[str, Any] = signer(request).loads(
            request.cookies.get("review_session", ""), max_age=28800
        )
        if not isinstance(data, dict) or not isinstance(data.get("csrf"), str):
            raise ValueError("invalid_session")
        now = request.app.state.clock.now().timestamp()
        if now - data["issued"] > 28800 or now - data["seen"] > 1800:
            raise ValueError("expired")
        reviewer = session.get(Reviewer, data["id"])
        if reviewer is None or not reviewer.is_active or reviewer.session_epoch != data["epoch"]:
            raise ValueError("revoked")
    except (BadSignature, ValueError, KeyError, TypeError):
        raise HTTPException(401, "Session expired. Log in at /review/login.") from None
    request.state.review_session = data
    request.state.review_role = reviewer.role
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if reviewer.role == "auditor" and not request.url.path.endswith(("/logout", "/logout-all")):
            raise HTTPException(403, "Read-only account")
        token = request.headers.get("X-CSRF-Token", "")
        if not token or not hmac.compare_digest(token, data["csrf"]):
            raise HTTPException(403, "Invalid CSRF token")
    return reviewer
