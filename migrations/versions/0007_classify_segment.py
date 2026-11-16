"""Allow the classification fallback task in the PostgreSQL queue."""

from alembic import op

revision = "0007_classify_segment"
down_revision = "0006_password_reset"
branch_labels = None
depends_on = None

_EXISTING_KINDS = (
    "'prepare_file', 'segment_file', 'extract_segment', 'finalize_submission', "
    "'deliver_webhook', 'purge_documents', 'recover_stale'"
)


def upgrade() -> None:
    op.drop_constraint("job_tasks_kind_check", "job_tasks", type_="check")
    op.create_check_constraint(
        "ck_job_tasks_kind", "job_tasks", f"kind IN ({_EXISTING_KINDS}, 'classify_segment')"
    )


def downgrade() -> None:
    # The old worker cannot handle classification tasks. Refuse the downgrade
    # if any such tasks remain so queued work is preserved.
    op.drop_constraint("ck_job_tasks_kind", "job_tasks", type_="check")
    op.create_check_constraint("job_tasks_kind_check", "job_tasks", f"kind IN ({_EXISTING_KINDS})")
