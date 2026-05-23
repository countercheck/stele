"""pii.free_text_responses: restricted store for high-PII-risk free text

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-05-23 00:00:00.000000

Free-text answers default to pii_risk='high' (design doc §3.9, invariant 6). The
high-risk value is copied here at submission time, keyed by submission, so the
PII-cleared reviewer role can screen it without access to all of raw_responses.
The analyst-facing marts.fact_response_item carries a null value_text and
value_text_redacted=true for these; only an explicit 'low' downgrade lets text
reach the marts. This table never holds 'low'-risk values.

No grants here: the pii schema's ALTER DEFAULT PRIVILEGES already grant stele_api
INSERT/UPDATE/SELECT (+ sequence USAGE) and stele_pii_reviewer SELECT/UPDATE, so
new pii tables inherit. stele_etl has no access to pii, so dbt cannot read this.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "free_text_responses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("raw_response_id", sa.BigInteger(), nullable=False),
        sa.Column("question_name", sa.Text(), nullable=False),
        # The free-text answer. Null when the question was shown but skipped.
        sa.Column("value_text", sa.Text(), nullable=True),
        # Always 'high' — this table only holds high-risk values. Stored so the
        # routing decision is auditable next to the value it gated.
        sa.Column("pii_risk", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # ON DELETE CASCADE so the M2 withdrawal/tombstone workflow erases the PII
        # copy when its raw row is deleted.
        sa.ForeignKeyConstraint(
            ["raw_response_id"],
            ["app.raw_responses.id"],
            name="fk_free_text_responses_raw_response",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "raw_response_id",
            "question_name",
            name="uq_free_text_responses_raw_question",
        ),
        schema="pii",
    )
    op.create_index(
        "ix_free_text_responses_raw_response",
        "free_text_responses",
        ["raw_response_id"],
        schema="pii",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_free_text_responses_raw_response",
        table_name="free_text_responses",
        schema="pii",
    )
    op.drop_table("free_text_responses", schema="pii")
