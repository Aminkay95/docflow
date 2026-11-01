from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    """Return a prefixed opaque identifier.

    IDs are ``prefix + uuid4().hex``. They do not leak sequencing and are safe in
    URLs and logs.
    """

    return f"{prefix}{uuid4().hex}"
