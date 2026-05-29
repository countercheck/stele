"""Flat-file (tidy/long) export of a survey's responses from the marts warehouse.

One row per ``marts.fact_response_item`` row — the selection grain (invariant 7):
a respondent's answer to a question occurrence, one row per chosen option for a
multi-select, one routing row for an unanswered question. This is a faithful
serialization of the mart, NOT a wide table — the design doc (§4.1) rejected
wide-table exports as the analytical *model* because their versioned column
names fragment across study iterations. Analysts pivot to whatever wide shape
they need downstream (pandas/R), where they control the missing-data encoding.

The distinctions the design docs insist on stay visible as their own columns, so
a downstream pivot can't silently collapse them:
  - ``was_shown`` separates shown-and-skipped (true, blank answer) from
    routed-past (false) — never "missing".
  - ``value_text_redacted`` marks a high-risk free-text answer withheld here
    (it lives in pii, not marts), distinct from a genuinely empty answer.

Reads marts only (over the stele_analyst connection), so no PII reaches the file:
high-risk free text is already redacted in ``fact_response_item`` unless a
reviewer promoted it (invariant 6).
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Column order of the exported CSV. ``answer`` is the single coalesced value
# (the four polymorphic value columns are mutually exclusive, invariant 8);
# ``option_label`` carries the human label for a choice answer alongside it.
EXPORT_COLUMNS: tuple[str, ...] = (
    "respondent_id",
    "survey_id",
    "survey_version",
    "question",
    "prompt_text",
    "value_kind",
    "occurrence",
    "answer",
    "option_label",
    "was_shown",
    "value_text_redacted",
    "rank",
)

# ``answer`` resolves a choice to its stable option value (not the drift-prone
# label), else the numeric / date / free-text value cast to text. A routed-past
# or redacted row coalesces to NULL → an empty cell, disambiguated by was_shown /
# value_text_redacted. survey_id is bound as a uuid (psycopg adapts uuid.UUID),
# so the comparison is uuid = uuid with no cast.
_EXPORT_SQL = text(
    """
    select
        fri.respondent_id,
        sv.survey_id,
        sv.version as survey_version,
        q.stable_name as question,
        qv.prompt_text,
        qv.value_kind,
        fri.occurrence,
        coalesce(
            o.value,
            cast(fri.value_numeric as text),
            cast(fri.value_date as text),
            fri.value_text
        ) as answer,
        o.label as option_label,
        fri.was_shown,
        fri.value_text_redacted,
        fri.rank
    from marts.fact_response_item as fri
    join marts.dim_survey_version as sv on sv.survey_version_id = fri.survey_version_id
    join marts.dim_question as q on q.question_id = fri.question_id
    join marts.dim_question_version as qv on qv.question_version_id = fri.question_version_id
    left join marts.dim_option as o on o.option_key = fri.option_key
    where sv.survey_id = :survey_id
    order by fri.respondent_id, sv.version, q.stable_name, fri.occurrence, o.display_order
    """
)


async def fetch_survey_export_rows(
    session: AsyncSession, survey_id: uuid.UUID
) -> list[Mapping[str, Any]]:
    """Read a survey's export rows from marts, ordered for a stable file.

    Rows are materialized here (not streamed): the result feeds a
    StreamingResponse whose body runs *after* the request handler returns, by
    which point the injected session is closed — so the DB read must complete
    while the session is still open.
    """
    result = await session.execute(_EXPORT_SQL, {"survey_id": survey_id})
    return [dict(row) for row in result.mappings().all()]


def _cell(value: Any) -> Any:
    """Render one value for CSV: blank for NULL, lowercase for booleans, else
    as-is (csv stringifies uuid/int/Decimal). Values are emitted verbatim — no
    Excel formula-injection neutralizing — because mangling cells would corrupt
    a faithful analytical export whose consumers are pandas/R, not Excel."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def iter_csv(rows: Sequence[Mapping[str, Any]]) -> Iterator[str]:
    """Yield the export as CSV text: a header row, then one row per selection.

    A survey with no warehouse rows (ETL not yet run, or no responses) yields
    just the header — an empty-but-valid CSV, not an error.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        chunk = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return chunk

    writer.writerow(EXPORT_COLUMNS)
    yield flush()
    for row in rows:
        writer.writerow([_cell(row[column]) for column in EXPORT_COLUMNS])
        yield flush()
