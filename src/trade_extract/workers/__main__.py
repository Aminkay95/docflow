"""Worker entry point: ``python -m trade_extract.workers``."""

from trade_extract.config import get_settings
from trade_extract.db.session import make_engine, session_factory
from trade_extract.workers.runner import run_forever


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings)
    try:
        run_forever(settings=settings, session_factory_=session_factory(engine))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
