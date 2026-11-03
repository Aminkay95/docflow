"""Add content-free provider attempt ledger."""
from alembic import op

revision = "0003_provider_calls"
down_revision = "0002_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE provider_calls (
        id text PRIMARY KEY,
        job_id text REFERENCES jobs(id) ON DELETE SET NULL,
        document_id text REFERENCES documents(id) ON DELETE SET NULL,
        page_id text REFERENCES pages(id) ON DELETE SET NULL,
        segment_id text REFERENCES segments(id) ON DELETE SET NULL,
        reader text NOT NULL CONSTRAINT ck_provider_calls_reader
            CHECK (reader IN ('text_layer','paddle','vision','text')),
        provider text NOT NULL, model text NOT NULL, operation text NOT NULL,
        prompt_hash varchar(64) NOT NULL,
        input_tokens integer NOT NULL, output_tokens integer NOT NULL,
        cached_tokens integer NOT NULL, latency_ms integer NOT NULL,
        attempt integer NOT NULL,
        status text NOT NULL CONSTRAINT ck_provider_calls_status
            CHECK (status IN ('ok','error','timeout','rate_limited')),
        error_code text, created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_provider_calls_counts CHECK (
            input_tokens >= 0 AND output_tokens >= 0 AND cached_tokens >= 0
            AND latency_ms >= 0 AND attempt >= 1)
    )
    """)
    op.execute("CREATE INDEX ix_provider_calls_job ON provider_calls (job_id, created_at)")
    op.execute("CREATE INDEX ix_provider_calls_created ON provider_calls (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE provider_calls")
