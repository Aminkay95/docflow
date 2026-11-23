"""Worker entry point: ``python -m trade_extract.workers``."""

import signal
from threading import Event

from trade_extract.config import get_settings
from trade_extract.db.session import make_engine, session_factory
from trade_extract.workers.runner import run_forever


def main() -> None:
    settings = get_settings()
    if settings.environment == "production" and settings.provider == "mock":
        raise SystemExit("Production workers require a real extraction provider.")
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    engine = make_engine(settings)
    try:
        run_forever(settings=settings, session_factory_=session_factory(engine), stop=stop)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
