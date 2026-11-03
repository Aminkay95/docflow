"""Retained review labels, assignments and conservative trust."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_review_trust"
down_revision = "0004_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "review_items", sa.Column("assigned_to", sa.Text(), sa.ForeignKey("reviewers.id"))
    )
    op.create_table(
        "review_resolutions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("review_item_id", sa.Text(), sa.ForeignKey("review_items.id"), nullable=False),
        sa.Column("reviewer_id", sa.Text(), sa.ForeignKey("reviewers.id"), nullable=False),
        sa.Column("field_path", sa.Text()),
        sa.Column("prior_value", JSONB()),
        sa.Column("corrected_value", JSONB()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("chosen_document_id", sa.Text(), sa.ForeignKey("documents.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_review_resolutions_review_item_id", "review_resolutions", ["review_item_id"]
    )
    op.create_table(
        "trust_records",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("client_id", sa.Text(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("supplier_key", sa.Text(), nullable=False),
        sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("template_fingerprint", sa.Text(), nullable=False),
        sa.Column("review_depth", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("correction_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "client_id",
            "supplier_key",
            "document_type",
            "template_fingerprint",
            name="uq_trust_identity",
        ),
        sa.CheckConstraint("state IN ('unseen','observed','trusted')", name="ck_trust_state"),
        sa.CheckConstraint("review_depth IN ('full','sampled')", name="ck_trust_depth"),
        sa.CheckConstraint("correction_count >= 0", name="ck_trust_corrections"),
    )
    op.create_table(
        "trust_observations",
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("trust_record_id", sa.Text(), sa.ForeignKey("trust_records.id"), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_trust_observations_trust_record_id", "trust_observations", ["trust_record_id"]
    )


def downgrade():
    op.drop_table("trust_observations")
    op.drop_table("trust_records")
    op.drop_table("review_resolutions")
    op.drop_column("review_items", "assigned_to")
