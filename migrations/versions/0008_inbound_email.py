"""Allow durable inbound email tasks before an extraction job exists."""

from alembic import op

revision = "0008_inbound_email"
down_revision = "0007_classify_segment"
branch_labels = None
depends_on = None

_KINDS = (
    "'prepare_file', 'segment_file', 'classify_segment', 'extract_segment', "
    "'finalize_submission', 'deliver_webhook', 'purge_documents', 'recover_stale'"
)


def upgrade() -> None:
    op.alter_column("job_tasks", "job_id", nullable=True)
    op.drop_constraint("ck_job_tasks_kind", "job_tasks", type_="check")
    op.create_check_constraint(
        "ck_job_tasks_kind", "job_tasks", f"kind IN ({_KINDS}, 'import_email')"
    )
    op.create_check_constraint(
        "ck_job_tasks_job", "job_tasks", "job_id IS NOT NULL OR kind = 'import_email'"
    )


def downgrade() -> None:
    op.alter_column("job_tasks", "job_id", nullable=False)
    op.drop_constraint("ck_job_tasks_job", "job_tasks", type_="check")
    op.drop_constraint("ck_job_tasks_kind", "job_tasks", type_="check")
    op.create_check_constraint("ck_job_tasks_kind", "job_tasks", f"kind IN ({_KINDS})")
