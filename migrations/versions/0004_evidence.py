"""Retained deterministic evaluation evidence."""
from alembic import op

revision = '0004_evidence'
down_revision = '0003_provider_calls'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''CREATE TABLE checks (
      id text PRIMARY KEY, document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      code text NOT NULL, field_path text NOT NULL, check_version text NOT NULL, outcome jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_checks_identity UNIQUE(document_id, code, field_path, check_version))''')
    op.execute('CREATE INDEX ix_checks_document_id ON checks(document_id)')
    op.execute('''CREATE TABLE decisions (
      id text PRIMARY KEY, job_id text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      document_id text UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
      policy_id text NOT NULL REFERENCES policies(id) ON DELETE RESTRICT, outcome jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('CREATE INDEX ix_decisions_job_id ON decisions(job_id)')
    op.execute('''CREATE TABLE conflicts (
      id text PRIMARY KEY, job_id text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      client_id text NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
      canonical_key text NOT NULL, field_label text NOT NULL, fingerprint text NOT NULL,
      status text NOT NULL DEFAULT 'open' CONSTRAINT ck_conflicts_status CHECK(status IN ('open','resolved','waived')),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      last_seen_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('CREATE INDEX ix_conflicts_job_id ON conflicts(job_id)')
    op.execute("CREATE UNIQUE INDEX ux_conflicts_open_key ON conflicts(job_id,canonical_key) WHERE status = 'open'")
    op.execute('''CREATE TABLE conflict_sources (
      id text PRIMARY KEY, conflict_id text NOT NULL REFERENCES conflicts(id) ON DELETE CASCADE,
      document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE, field_path text NOT NULL,
      stated_value text NOT NULL, evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_conflict_sources_identity UNIQUE(conflict_id, document_id, field_path))''')
    op.execute('CREATE INDEX ix_conflict_sources_conflict_id ON conflict_sources(conflict_id)')

    op.execute("""CREATE TABLE review_items (
      id text PRIMARY KEY, job_id text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      document_id text REFERENCES documents(id) ON DELETE CASCADE,
      conflict_id text REFERENCES conflicts(id) ON DELETE CASCADE,
      track text NOT NULL, status text NOT NULL DEFAULT 'open', reason_code text NOT NULL,
      summary text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_review_items_track CHECK (
        (track = 'extraction' AND document_id IS NOT NULL AND conflict_id IS NULL) OR
        (track = 'conflict' AND conflict_id IS NOT NULL AND document_id IS NULL)))""")
    op.execute('CREATE INDEX ix_review_items_job_id ON review_items(job_id)')
    op.execute("CREATE UNIQUE INDEX ux_review_open_document ON review_items(document_id) WHERE track = 'extraction' AND status IN ('open','in_progress')")
    op.execute("CREATE UNIQUE INDEX ux_review_open_conflict ON review_items(conflict_id) WHERE track = 'conflict' AND status IN ('open','in_progress')")


def downgrade() -> None:
    for name in ('review_items', 'conflict_sources', 'conflicts', 'decisions', 'checks'):
        op.execute(f'DROP TABLE {name}')
