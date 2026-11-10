"""Seed the active policy version 1.

Idempotent: running it twice leaves the existing active policy untouched.
"""

from __future__ import annotations

import sys

from sqlalchemy import func, select

from trade_extract.config import Settings, get_settings
from trade_extract.db.models.policy import Policy
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.clock import SystemClock
from trade_extract.domain.ids import new_id


def build_policy_config(settings: Settings) -> dict[str, object]:
    from trade_extract.domain.policy import GatePolicy

    return GatePolicy(invoice_pass_enabled=True, review_depth="checks").model_dump(mode="json")

def main() -> int:
    settings = get_settings()
    engine = make_engine(settings)
    factory = session_factory(engine)
    clock = SystemClock()

    with factory() as session:
        active = session.execute(select(Policy).where(Policy.status == "active")).scalars().first()
        if active is not None:
            print(f"active policy v{active.version} already exists ({active.id})")
            return 0

        highest = session.execute(select(func.max(Policy.version))).scalar() or 0
        next_version = int(highest) + 1

        policy = Policy(
            id=new_id("pol_"),
            version=next_version,
            name="2026-11 baseline",
            config=build_policy_config(settings),
            status="active",
            activated_at=clock.now(),
        )
        session.add(policy)
        session.commit()
        print(f"activated policy v{policy.version} ({policy.id})")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
