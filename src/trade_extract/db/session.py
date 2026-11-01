from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.config import Settings


def make_engine(settings: Settings) -> Engine:
    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "future": True,
    }
    if not settings.database_url.startswith("sqlite"):
        kwargs["pool_size"] = settings.database_pool_size
        kwargs["max_overflow"] = settings.database_pool_max_overflow
    return create_engine(settings.database_url, **kwargs)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, autoflush=False)
