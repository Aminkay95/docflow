"""Reviewer email addresses and single-use password reset tokens."""

from alembic import op

revision = "0006_password_reset"
down_revision = "0005_review_trust"
branch_labels = None
depends_on = None

_UPGRADE_STATEMENTS = (
    "ALTER TABLE reviewers ADD COLUMN email text",
    "CREATE UNIQUE INDEX ux_reviewers_email_lower ON reviewers (lower(email))",
    """
    CREATE TABLE review_password_resets (
        id           text PRIMARY KEY,
        reviewer_id  text NOT NULL REFERENCES reviewers(id) ON DELETE CASCADE,
        token_hash   text NOT NULL UNIQUE,
        expires_at   timestamptz NOT NULL,
        used_at      timestamptz,
        requested_ip text,
        created_at   timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX ix_review_password_resets_reviewer_id ON review_password_resets (reviewer_id)",
)

_DOWNGRADE_STATEMENTS = (
    "DROP TABLE review_password_resets",
    "DROP INDEX ux_reviewers_email_lower",
    "ALTER TABLE reviewers DROP COLUMN email",
)


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE_STATEMENTS:
        op.execute(statement)
