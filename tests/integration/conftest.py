from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.api.app import create_app
from trade_extract.config import Settings
from trade_extract.db.base import Base
from trade_extract.db.models.api_key import ApiKey
from trade_extract.db.models.client import Client
from trade_extract.db.models.policy import Policy
from trade_extract.domain.ids import new_id
from trade_extract.services.webhooks import encrypt_webhook_secret

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://trade:trade@localhost:5432/trade_extract_test",
)

_DB_AVAILABLE: bool | None = None
_DB_ERROR: str = ""


def _ensure_database(url_text: str) -> None:
    url = make_url(url_text)
    admin_url = url.set(database="postgres").update_query_dict({"connect_timeout": "3"})
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).first()
            if exists is None:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(PROJECT_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    global _DB_AVAILABLE, _DB_ERROR
    if _DB_AVAILABLE is False:
        pytest.skip(_DB_ERROR)
    try:
        _ensure_database(TEST_DATABASE_URL)
        engine = create_engine(
            make_url(TEST_DATABASE_URL).update_query_dict({"connect_timeout": "3"}),
            pool_pre_ping=True,
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        _DB_AVAILABLE = False
        _DB_ERROR = f"PostgreSQL is not available at {TEST_DATABASE_URL}: {exc}"
        pytest.skip(_DB_ERROR)

    _DB_AVAILABLE = True
    # Integration fixtures use the migrated schema, never ORM metadata only.
    command.downgrade(_alembic_config(), "base")
    command.upgrade(_alembic_config(), "head")
    yield engine
    engine.dispose()
    command.downgrade(_alembic_config(), "base")


@pytest.fixture
def clean_database(db_engine: Engine) -> None:
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with db_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def hash_key(pepper: str, key: str) -> str:
    return hmac.new(pepper.encode("utf-8"), key.encode("utf-8"), hashlib.sha256).hexdigest()


def new_api_key() -> str:
    return "tx_test_" + secrets.token_urlsafe(32)


@dataclass
class ApiHarness:
    app: object
    client: TestClient
    settings: Settings
    session_factory: sessionmaker[Session]
    api_key: str
    client_id: str
    storage_dir: Path

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


def seed_client(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    scopes: list[str] | None = None,
    webhook_url: str | None = None,
    webhook_enabled: bool | None = None,
) -> tuple[str, str, str]:
    token = new_api_key()
    with factory() as session:
        existing_policy = (
            session.execute(select(Policy).where(Policy.status == "active")).scalars().first()
        )
        if existing_policy is None:
            session.add(
                Policy(
                    id=new_id("pol_"),
                    version=1,
                    name="test policy",
                    config={},
                    status="active",
                    activated_at=datetime.now(UTC),
                )
            )
        client = Client(
            id=new_id("cli_"),
            name="Test client",
            slug=new_id("slug_"),
            status="active",
            webhook_url=webhook_url,
            webhook_enabled=bool(webhook_url) if webhook_enabled is None else webhook_enabled,
            webhook_secret_ciphertext=encrypt_webhook_secret(
                "whsec_test_secret", settings.secret_key
            ),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(client)
        session.flush()
        api_key = ApiKey(
            id=new_id("key_"),
            client_id=client.id,
            name="test key",
            key_prefix=token[:16],
            key_hash=hash_key(settings.api_key_pepper, token),
            scopes=scopes or ["extract:write", "results:read"],
            created_at=datetime.now(UTC),
        )
        session.add(api_key)
        session.commit()
        return token, client.id, "policy"


@pytest.fixture
def api(
    tmp_path: Path, db_engine: Engine, clean_database: None
) -> Generator[ApiHarness, None, None]:
    settings = Settings(
        environment="development",
        database_url=TEST_DATABASE_URL,
        secret_key="integration-test-secret",
        api_key_pepper="integration-test-pepper",
        storage_dir=tmp_path / "storage",
        mail_outbox_dir=tmp_path / "outbox",
        resend_api_key="",
        smtp_host="",
        base_url="http://testserver",
        webhook_allow_private_targets=True,
    )
    app = create_app(settings)
    factory: sessionmaker[Session] = app.state.session_factory

    token, client_id, _policy_id = seed_client(factory, settings)
    with TestClient(app) as client:
        yield ApiHarness(
            app=app,
            client=client,
            settings=settings,
            session_factory=factory,
            api_key=token,
            client_id=client_id,
            storage_dir=settings.storage_dir,
        )


@pytest.fixture
def second_tenant(api: ApiHarness) -> tuple[str, str]:
    token, client_id, _ = seed_client(api.session_factory, api.settings)
    return token, client_id


def rows(api: ApiHarness, table: str) -> list[SimpleNamespace]:
    with api.session_factory() as session:
        result = session.execute(text(f'SELECT * FROM "{table}"')).mappings().all()
    return [SimpleNamespace(**row) for row in result]


def count_rows(api: ApiHarness, table: str) -> int:
    with api.session_factory() as session:
        return int(session.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())
