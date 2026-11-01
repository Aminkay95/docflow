from __future__ import annotations

from datetime import UTC, datetime


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
