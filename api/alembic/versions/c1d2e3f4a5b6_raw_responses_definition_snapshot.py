"""raw_responses: add definition_snapshot

Revision ID: c1d2e3f4a5b6
Revises: fa3b82f432f9
Create Date: 2026-05-22 14:10:00.000000

Adds a frozen copy of the published definition (the SurveyJS JSON plus its hash
and published_at) to each raw_responses row, written by the API at submit time.

Why: dbt reads app.raw_responses exclusively and marts must be reproducible from
it alone (invariant 1/4, NFR-1), but the raw row otherwise carries no definition
— so dimension metadata (prompt text, option labels, definition_hash,
published_at, question type) had no source. Embedding the snapshot keeps
raw_responses the single ETL source while letting dbt build rich dimensions.
Published definitions are immutable, so the snapshot can never drift from the
version that was answered. Nullable, consistent with payload/shown_questions/
client_metadata, so the M2 tombstone workflow can null it on withdrawal.

NOTE: this extends design-doc §3.4's raw_responses column list; the
corresponding survey-engine-design-doc.md update is included in this PR.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "fa3b82f432f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_responses",
        sa.Column("definition_snapshot", postgresql.JSONB(), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("raw_responses", "definition_snapshot", schema="app")
