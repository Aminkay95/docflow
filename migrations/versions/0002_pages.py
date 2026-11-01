"""pages: the rendered page atom and its embedded text

Revision ID: 0002_pages
Revises: 0001_initial
Create Date: 2026-11-01

Frozen, forward-only DDL. ``0001_initial`` is not edited; this revision adds the
``pages`` table, its unique key, indexes and updated-at trigger. The table mirrors
``docs/03-database-schema.md`` plus the ``text_layer`` column (embedded text lives
with the page, not in a re-parse of the source).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_pages"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE_STATEMENTS = (
    """
    CREATE TABLE pages (
        id                      text PRIMARY KEY,
        file_id                 text NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        page_index              integer NOT NULL CHECK (page_index >= 0),
        width_px                integer NOT NULL CHECK (width_px > 0),
        height_px               integer NOT NULL CHECK (height_px > 0),
        dpi                     integer NOT NULL DEFAULT 300 CHECK (dpi > 0),
        rotation_deg            numeric(6,2) NOT NULL DEFAULT 0,
        text_layer_present      boolean NOT NULL DEFAULT false,
        text_layer_chars        integer NOT NULL DEFAULT 0 CHECK (text_layer_chars >= 0),
        text_layer              text,
        legibility_score        numeric(5,4) CHECK (legibility_score BETWEEN 0 AND 1),
        conditioning            jsonb NOT NULL DEFAULT '{}'::jsonb,
        conditioning_version    text,
        status                  text NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'conditioned',
                                                  'unreadable', 'read')),
        unreadable_reason       text,
        image_key               text,
        thumbnail_key           text,
        created_at              timestamptz NOT NULL DEFAULT now(),
        updated_at              timestamptz NOT NULL DEFAULT now(),
        conditioned_at          timestamptz,
        purged_at               timestamptz,
        CONSTRAINT ux_pages_file_page UNIQUE (file_id, page_index)
    )
    """,
    "CREATE INDEX ix_pages_file ON pages (file_id, page_index)",
    "CREATE INDEX ix_pages_status ON pages (status)",
)

#: Dropping the table removes ``trg_pages_updated_at`` with it.
_DOWNGRADE_STATEMENTS = ("DROP TABLE IF EXISTS pages CASCADE",)


def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(
        """
        CREATE TRIGGER trg_pages_updated_at
        BEFORE UPDATE ON pages
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    for statement in _DOWNGRADE_STATEMENTS:
        op.execute(statement)
