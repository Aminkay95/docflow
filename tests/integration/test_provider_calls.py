from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from trade_extract.db.models.provider_call import ProviderCall
from trade_extract.db.repositories.provider_calls import ProviderCallRecorder
from trade_extract.domain.inference import ProviderAttempt


def test_ledger_commits_independent_attempts(db_engine, clean_database):
    factory = sessionmaker(db_engine)
    recorder = ProviderCallRecorder(factory, provider="mock", reader="vision")
    attempt = ProviderAttempt("mock", "a" * 64, 1, "failed", 12, "http_429")
    recorder(attempt)
    with factory() as session:
        row = session.scalars(select(ProviderCall)).one()
        assert row.status == "rate_limited"
        assert row.prompt_hash == attempt.prompt_hash
        assert row.attempt == 1
        assert not {"evidence", "raw_json", "api_key", "fields", "image_bytes"} & set(
            ProviderCall.__table__.columns.keys()
        )
    assert "raw_json" not in asdict(attempt)
