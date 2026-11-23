"""Enforce tenant ownership across ingestion and add tenant workspace accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0009_tenant_workspace"
down_revision = "0008_inbound_email"


def upgrade() -> None:
    op.add_column("reviewers", sa.Column("client_id", sa.Text(), nullable=True))
    op.add_column(
        "reviewers",
        sa.Column("client_login_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_foreign_key(
        "fk_reviewers_client", "reviewers", "clients", ["client_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_reviewers_client_id", "reviewers", ["client_id"])
    op.drop_constraint("reviewers_role_check", "reviewers", type_="check")
    op.create_check_constraint(
        "ck_reviewers_role",
        "reviewers",
        "(role = 'client' AND client_id IS NOT NULL AND NOT is_active) OR "
        "(role IN ('reviewer', 'admin', 'auditor') AND client_id IS NULL "
        "AND NOT client_login_enabled)",
    )
    op.create_unique_constraint("uq_jobs_id_client", "jobs", ["id", "client_id"])
    op.create_unique_constraint(
        "uq_submissions_id_job_client", "submissions", ["id", "job_id", "client_id"]
    )
    op.create_unique_constraint("uq_files_id_job_client", "files", ["id", "job_id", "client_id"])
    op.create_unique_constraint("uq_segments_id_file", "segments", ["id", "file_id"])
    op.create_foreign_key(
        "fk_submissions_job_tenant",
        "submissions",
        "jobs",
        ["job_id", "client_id"],
        ["id", "client_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_files_submission_tenant",
        "files",
        "submissions",
        ["submission_id", "job_id", "client_id"],
        ["id", "job_id", "client_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_documents_submission_tenant",
        "documents",
        "submissions",
        ["submission_id", "job_id", "client_id"],
        ["id", "job_id", "client_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_documents_file_tenant",
        "documents",
        "files",
        ["file_id", "job_id", "client_id"],
        ["id", "job_id", "client_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_documents_segment_file",
        "documents",
        "segments",
        ["segment_id", "file_id"],
        ["id", "file_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_conflicts_job_tenant",
        "conflicts",
        "jobs",
        ["job_id", "client_id"],
        ["id", "client_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_conflicts_job_tenant", "conflicts", type_="foreignkey")
    op.drop_constraint("fk_documents_segment_file", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_file_tenant", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_submission_tenant", "documents", type_="foreignkey")
    op.drop_constraint("fk_files_submission_tenant", "files", type_="foreignkey")
    op.drop_constraint("fk_submissions_job_tenant", "submissions", type_="foreignkey")
    op.drop_constraint("uq_segments_id_file", "segments", type_="unique")
    op.drop_constraint("uq_files_id_job_client", "files", type_="unique")
    op.drop_constraint("uq_submissions_id_job_client", "submissions", type_="unique")
    op.drop_constraint("uq_jobs_id_client", "jobs", type_="unique")
    op.drop_constraint("ck_reviewers_role", "reviewers", type_="check")
    op.create_check_constraint(
        "reviewers_role_check", "reviewers", "role IN ('reviewer', 'admin', 'auditor')"
    )
    op.drop_index("ix_reviewers_client_id", table_name="reviewers")
    op.drop_constraint("fk_reviewers_client", "reviewers", type_="foreignkey")
    op.drop_column("reviewers", "client_login_enabled")
    op.drop_column("reviewers", "client_id")
