"""initial Phase 1 schema: identity, policy, jobs, submissions, files, tasks, webhooks

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-01

This migration is a frozen, forward-only DDL script. It deliberately does not call
``Base.metadata.create_all``: a deploy schema must not change when the ORM models do.
Later stories add their tables in new revisions.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPDATED_AT_TABLES = (
    "clients",
    "reviewers",
    "jobs",
    "segments",
    "documents",
    "job_tasks",
)

_UPGRADE_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS btree_gist",
    """
    CREATE OR REPLACE FUNCTION set_updated_at()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$
    """,
    # ------------------------------------------------------------------
    # Tenants and credentials
    # ------------------------------------------------------------------
    """
    CREATE TABLE clients (
        id                              text PRIMARY KEY,
        name                            text NOT NULL,
        slug                            text NOT NULL UNIQUE,
        status                          text NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active', 'suspended', 'terminated')),
        webhook_url                     text,
        webhook_secret_ciphertext       bytea,
        webhook_enabled                 boolean NOT NULL DEFAULT false,
        retention_days_after_termination integer NOT NULL DEFAULT 90
                                        CHECK (retention_days_after_termination >= 0),
        created_at                      timestamptz NOT NULL DEFAULT now(),
        updated_at                      timestamptz NOT NULL DEFAULT now(),
        terminated_at                   timestamptz,
        CHECK (webhook_enabled = false OR webhook_url IS NOT NULL)
    )
    """,
    """
    CREATE TABLE api_keys (
        id              text PRIMARY KEY,
        client_id       text NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        name            text NOT NULL,
        key_prefix      text NOT NULL,
        key_hash        text NOT NULL UNIQUE,
        scopes          text[] NOT NULL DEFAULT ARRAY['extract:write', 'results:read'],
        last_used_at    timestamptz,
        expires_at      timestamptz,
        revoked_at      timestamptz,
        created_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX ix_api_keys_client ON api_keys (client_id) WHERE revoked_at IS NULL",
    """
    CREATE UNIQUE INDEX ux_api_keys_prefix_active
        ON api_keys (key_prefix) WHERE revoked_at IS NULL
    """,
    # ------------------------------------------------------------------
    # Review-console users
    # ------------------------------------------------------------------
    """
    CREATE TABLE reviewers (
        id              text PRIMARY KEY,
        username        text NOT NULL,
        password_hash   text NOT NULL,
        display_name    text NOT NULL,
        role            text NOT NULL DEFAULT 'reviewer'
                        CHECK (role IN ('reviewer', 'admin', 'auditor')),
        is_active       boolean NOT NULL DEFAULT true,
        session_epoch   integer NOT NULL DEFAULT 0,
        last_login_at   timestamptz,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX ux_reviewers_username_lower ON reviewers (lower(username))",
    # ------------------------------------------------------------------
    # Versioned policy
    # ------------------------------------------------------------------
    """
    CREATE TABLE policies (
        id              text PRIMARY KEY,
        version         integer NOT NULL UNIQUE CHECK (version > 0),
        name            text NOT NULL,
        config          jsonb NOT NULL,
        status          text NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'active', 'retired')),
        created_by      text REFERENCES reviewers(id) ON DELETE SET NULL,
        created_at      timestamptz NOT NULL DEFAULT now(),
        activated_at    timestamptz,
        retired_at      timestamptz,
        CHECK (status <> 'active' OR activated_at IS NOT NULL)
    )
    """,
    "CREATE UNIQUE INDEX ux_policies_one_active ON policies (status) WHERE status = 'active'",
    "CREATE INDEX ix_policies_status ON policies (status, version DESC)",
    # ------------------------------------------------------------------
    # Jobs: the logical accumulator
    # ------------------------------------------------------------------
    """
    CREATE TABLE jobs (
        id                  text PRIMARY KEY,
        client_id           text NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
        job_ref             text,
        status              text NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'processing', 'review_required',
                                              'completed', 'failed')),
        policy_id           text NOT NULL REFERENCES policies(id) ON DELETE RESTRICT,
        document_count      integer NOT NULL DEFAULT 0 CHECK (document_count >= 0),
        open_review_count   integer NOT NULL DEFAULT 0 CHECK (open_review_count >= 0),
        open_conflict_count integer NOT NULL DEFAULT 0 CHECK (open_conflict_count >= 0),
        terminal_at         timestamptz,
        terminal_reason     text CHECK (terminal_reason IN ('completed', 'failed', 'cancelled')),
        created_at          timestamptz NOT NULL DEFAULT now(),
        updated_at          timestamptz NOT NULL DEFAULT now(),
        last_submission_at  timestamptz NOT NULL DEFAULT now(),
        CHECK ((terminal_at IS NULL) = (terminal_reason IS NULL))
    )
    """,
    """
    CREATE UNIQUE INDEX ux_jobs_client_ref
        ON jobs (client_id, job_ref) WHERE job_ref IS NOT NULL
    """,
    "CREATE INDEX ix_jobs_client_created ON jobs (client_id, created_at DESC)",
    "CREATE INDEX ix_jobs_status ON jobs (status, updated_at)",
    "CREATE INDEX ix_jobs_terminal ON jobs (terminal_at) WHERE terminal_at IS NOT NULL",
    # ------------------------------------------------------------------
    # Submissions: one API call, one result
    # ------------------------------------------------------------------
    """
    CREATE TABLE submissions (
        id                  text PRIMARY KEY,
        result_id           text NOT NULL UNIQUE,
        job_id              text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        client_id           text NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
        status              text NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
        webhook_url         text,
        idempotency_key     text,
        request_fingerprint text,
        document_count      integer NOT NULL DEFAULT 0 CHECK (document_count >= 0),
        duplicate_count     integer NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
        error_code          text,
        error_message       text,
        created_at          timestamptz NOT NULL DEFAULT now(),
        completed_at        timestamptz
    )
    """,
    """
    CREATE UNIQUE INDEX ux_submissions_client_idempotency
        ON submissions (client_id, idempotency_key) WHERE idempotency_key IS NOT NULL
    """,
    "CREATE INDEX ix_submissions_job ON submissions (job_id, created_at)",
    "CREATE INDEX ix_submissions_client_created ON submissions (client_id, created_at DESC)",
    # ------------------------------------------------------------------
    # Files: uploaded containers
    # ------------------------------------------------------------------
    """
    CREATE TABLE files (
        id              text PRIMARY KEY,
        submission_id   text NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        job_id          text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        client_id       text NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
        filename        text NOT NULL,
        content_type    text NOT NULL,
        size_bytes      bigint NOT NULL CHECK (size_bytes > 0),
        sha256          char(64) NOT NULL,
        storage_key     text NOT NULL,
        page_count      integer CHECK (page_count IS NULL OR page_count > 0),
        status          text NOT NULL DEFAULT 'received'
                        CHECK (status IN ('received', 'processing', 'processed',
                                          'failed', 'purged')),
        error_code      text,
        error_message   text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        processed_at    timestamptz,
        purged_at       timestamptz
    )
    """,
    "CREATE UNIQUE INDEX ux_files_job_sha256 ON files (job_id, sha256)",
    "CREATE INDEX ix_files_submission ON files (submission_id)",
    "CREATE INDEX ix_files_job ON files (job_id)",
    "CREATE INDEX ix_files_purgeable ON files (job_id) WHERE purged_at IS NULL",
    # ------------------------------------------------------------------
    # Segments: non-overlapping page ranges
    # ------------------------------------------------------------------
    """
    CREATE TABLE segments (
        id              text PRIMARY KEY,
        file_id         text NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        job_id          text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        page_start      integer NOT NULL CHECK (page_start >= 0),
        page_end        integer NOT NULL CHECK (page_end >= page_start),
        document_type   text NOT NULL CHECK (document_type IN (
                            'commercial_invoice', 'proforma_invoice', 'bill_of_lading',
                            'delivery_order', 'port_pass', 'custom_entry',
                            'verification_memo', 'packing_list', 'unknown')),
        confidence      numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        fingerprint     text NOT NULL,
        status          text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'extracting', 'extracted', 'failed')),
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ex_segments_page_range EXCLUDE USING gist (
            file_id WITH =,
            int4range(page_start, page_end, '[]') WITH &&
        )
    )
    """,
    "CREATE INDEX ix_segments_file ON segments (file_id, page_start)",
    "CREATE INDEX ix_segments_job ON segments (job_id)",
    "CREATE INDEX ix_segments_type ON segments (document_type)",
    # ------------------------------------------------------------------
    # Documents: one extraction record per segment
    # ------------------------------------------------------------------
    """
    CREATE TABLE documents (
        id                  text PRIMARY KEY,
        job_id              text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        submission_id       text NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        file_id             text NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        segment_id          text NOT NULL UNIQUE REFERENCES segments(id) ON DELETE CASCADE,
        client_id           text NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
        document_type       text NOT NULL CHECK (document_type IN (
                                'commercial_invoice', 'proforma_invoice', 'bill_of_lading',
                                'delivery_order', 'port_pass', 'custom_entry',
                                'verification_memo', 'packing_list', 'unknown')),
        document_number     text,
        supplier_name       text,
        supplier_key        text,
        template_fingerprint text,
        source_file_hash    char(64) NOT NULL,
        document_hash       char(64) NOT NULL,
        idempotency_key     text NOT NULL,
        status              text NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'processing', 'extracted',
                                              'review_required', 'resolved',
                                              'unreadable', 'failed')),
        decision            text CHECK (decision IN ('pass', 'review', 'unreadable', 'failed')),
        decision_reason     text,
        extraction          jsonb,
        provenance_version  text NOT NULL DEFAULT '1.0',
        page_start          integer NOT NULL CHECK (page_start >= 0),
        page_end            integer NOT NULL CHECK (page_end >= page_start),
        created_at          timestamptz NOT NULL DEFAULT now(),
        updated_at          timestamptz NOT NULL DEFAULT now(),
        extracted_at        timestamptz,
        resolved_at         timestamptz,
        purged_at           timestamptz,
        CONSTRAINT uq_documents_job_idempotency UNIQUE (job_id, idempotency_key),
        CONSTRAINT uq_documents_job_hash UNIQUE (job_id, document_hash)
    )
    """,
    "CREATE INDEX ix_documents_job ON documents (job_id, created_at)",
    "CREATE INDEX ix_documents_submission ON documents (submission_id)",
    "CREATE INDEX ix_documents_client_created ON documents (client_id, created_at DESC)",
    "CREATE INDEX ix_documents_status ON documents (status)",
    "CREATE INDEX ix_documents_type ON documents (document_type)",
    # ------------------------------------------------------------------
    # Durable queue
    # ------------------------------------------------------------------
    """
    CREATE TABLE job_tasks (
        id              text PRIMARY KEY,
        job_id          text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        submission_id   text REFERENCES submissions(id) ON DELETE CASCADE,
        document_id     text REFERENCES documents(id) ON DELETE CASCADE,
        segment_id      text REFERENCES segments(id) ON DELETE CASCADE,
        kind            text NOT NULL CHECK (kind IN (
                            'prepare_file', 'segment_file', 'extract_segment',
                            'finalize_submission', 'deliver_webhook',
                            'purge_documents', 'recover_stale')),
        status          text NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'dead')),
        priority        smallint NOT NULL DEFAULT 100,
        run_after       timestamptz NOT NULL DEFAULT now(),
        attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        max_attempts    integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
        requeues        integer NOT NULL DEFAULT 0 CHECK (requeues >= 0),
        dedupe_key      text UNIQUE,
        locked_by       text,
        locked_at       timestamptz,
        started_at      timestamptz,
        finished_at     timestamptz,
        last_error_code text,
        last_error      text,
        payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        CHECK (status <> 'running' OR locked_by IS NOT NULL)
    )
    """,
    """
    CREATE INDEX ix_tasks_claim
        ON job_tasks (status, run_after, priority DESC, created_at)
        WHERE status = 'queued'
    """,
    "CREATE INDEX ix_tasks_running ON job_tasks (locked_at) WHERE status = 'running'",
    "CREATE INDEX ix_tasks_job ON job_tasks (job_id, created_at)",
    "CREATE INDEX ix_tasks_dead ON job_tasks (status) WHERE status = 'dead'",
    # ------------------------------------------------------------------
    # Webhook outbox
    # ------------------------------------------------------------------
    """
    CREATE TABLE webhook_deliveries (
        id              text PRIMARY KEY,
        submission_id   text NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        job_id          text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        event_type      text NOT NULL CHECK (event_type IN (
                            'result.completed', 'result.review_required', 'result.failed')),
        url             text NOT NULL,
        payload         jsonb NOT NULL,
        payload_sha256  char(64) NOT NULL,
        status          text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'delivering', 'delivered',
                                          'failed', 'dead')),
        attempts        integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        next_attempt_at timestamptz NOT NULL DEFAULT now(),
        last_status_code integer,
        last_error      text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        delivered_at    timestamptz,
        CONSTRAINT uq_webhooks_submission_event UNIQUE (submission_id, event_type)
    )
    """,
    """
    CREATE INDEX ix_webhooks_due
        ON webhook_deliveries (next_attempt_at)
        WHERE status IN ('pending', 'failed')
    """,
    "CREATE INDEX ix_webhooks_submission ON webhook_deliveries (submission_id)",
)


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)

    for table_name in _UPDATED_AT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_updated_at
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
            """
        )


def downgrade() -> None:
    for table_name in (
        "webhook_deliveries",
        "job_tasks",
        "documents",
        "segments",
        "files",
        "submissions",
        "jobs",
        "policies",
        "reviewers",
        "api_keys",
        "clients",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    # Extensions are intentionally left in place: other schemas may rely on them.
