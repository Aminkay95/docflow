from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC timestamp."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Deterministic clock for tests and retention/webhook schedules."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> datetime:
        from datetime import timedelta

        self._now = self._now + timedelta(**kwargs)
        return self._now
