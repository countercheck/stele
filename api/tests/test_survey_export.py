"""Survey response export (tidy/long CSV from the marts warehouse).

Three layers, so the suite stays meaningful even where marts isn't built:

* ``iter_csv`` is exercised directly with synthetic rows — pure, always runs.
* The endpoint's auth gate, 404, and CSV wiring are tested with the marts read
  stubbed, so they run in the bare pytest CI job (which has no marts tables).
* One end-to-end test seeds real marts rows and runs the actual SQL; it skips
  cleanly when the marts tables are absent (the same posture as elevated_conn).
"""

import csv
import io
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_analyst_session
from api.main import api_app as app
from api.survey_engine import export
from api.survey_engine.export import EXPORT_COLUMNS, iter_csv

VALID_DEFINITION: dict[str, Any] = {
    "pages": [{"name": "p1", "elements": [{"type": "radiogroup", "name": "q1", "choices": ["a"]}]}]
}
PASSWORD = "correct-horse-battery-staple"


def _parse(csv_text: str) -> list[dict[str, str]]:
    """CSV text → header-keyed dict rows (so assertions don't hinge on column order)."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == list(EXPORT_COLUMNS)
    return [dict(zip(EXPORT_COLUMNS, r, strict=True)) for r in rows[1:]]


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "respondent_id": "r1",
        "survey_id": "s1",
        "survey_version": 1,
        "question": "q",
        "prompt_text": "P?",
        "value_kind": "text",
        "occurrence": 1,
        "answer": None,
        "option_label": None,
        "was_shown": True,
        "value_text_redacted": False,
        "rank": None,
    }
    base.update(over)
    return base


# --- iter_csv (pure) -------------------------------------------------------


def test_iter_csv_emits_header_only_for_no_rows() -> None:
    out = "".join(iter_csv([]))
    assert _parse(out) == []


def test_iter_csv_renders_values_distinctly() -> None:
    rows = [
        # A resolved choice: answer is the stable option value, label alongside.
        {
            "respondent_id": "r1",
            "survey_id": "s1",
            "survey_version": 1,
            "question": "fav_color",
            "prompt_text": "Favorite colour?",
            "value_kind": "option",
            "occurrence": 1,
            "answer": "blue",
            "option_label": "Blue",
            "was_shown": True,
            "value_text_redacted": False,
            "rank": None,
        },
        # Free text containing a comma, quote, and newline — must round-trip via quoting.
        {
            "respondent_id": "r1",
            "survey_id": "s1",
            "survey_version": 1,
            "question": "comment",
            "prompt_text": "Anything else?",
            "value_kind": "text",
            "occurrence": 1,
            "answer": 'Great, really "good"\nthanks',
            "option_label": None,
            "was_shown": True,
            "value_text_redacted": False,
            "rank": None,
        },
        # Redacted high-risk free text: blank answer, but flagged distinct from empty.
        {
            "respondent_id": "r1",
            "survey_id": "s1",
            "survey_version": 1,
            "question": "email",
            "prompt_text": "Your email?",
            "value_kind": "text",
            "occurrence": 1,
            "answer": None,
            "option_label": None,
            "was_shown": True,
            "value_text_redacted": True,
            "rank": None,
        },
        # Routed-past: blank answer, was_shown false — never to be read as "skipped".
        {
            "respondent_id": "r1",
            "survey_id": "s1",
            "survey_version": 1,
            "question": "followup",
            "prompt_text": "Follow up?",
            "value_kind": "option",
            "occurrence": 1,
            "answer": None,
            "option_label": None,
            "was_shown": False,
            "value_text_redacted": False,
            "rank": None,
        },
    ]
    parsed = _parse("".join(iter_csv(rows)))
    by_q = {r["question"]: r for r in parsed}

    assert by_q["fav_color"]["answer"] == "blue"
    assert by_q["fav_color"]["option_label"] == "Blue"
    assert by_q["fav_color"]["was_shown"] == "true"

    assert by_q["comment"]["answer"] == 'Great, really "good"\nthanks'

    # A redacted answer and a routed-past answer both have a blank cell, but the
    # sidecar flags keep them distinguishable — the distinction the docs require.
    assert by_q["email"]["answer"] == ""
    assert by_q["email"]["value_text_redacted"] == "true"
    assert by_q["email"]["was_shown"] == "true"

    assert by_q["followup"]["answer"] == ""
    assert by_q["followup"]["was_shown"] == "false"
    assert by_q["followup"]["value_text_redacted"] == "false"


def test_iter_csv_faithful_by_default_keeps_formula_verbatim() -> None:
    # Default export is faithful: a formula-leading free-text answer is NOT mangled.
    rows = [_row(question="evil", value_kind="text", answer="=cmd|'/c calc'!A1")]
    by_q = {r["question"]: r for r in _parse("".join(iter_csv(rows)))}
    assert by_q["evil"]["answer"] == "=cmd|'/c calc'!A1"


def test_iter_csv_excel_safe_escapes_formula_in_free_text_only() -> None:
    rows = [
        # Untrusted free text that a spreadsheet would run as a formula → neutralized.
        _row(question="evil", value_kind="text", answer="=cmd|'/c calc'!A1"),
        _row(question="hyperlink", value_kind="text", answer="@SUM(1+1)"),
        # A typed numeric answer that legitimately leads with '-' stays verbatim.
        _row(question="rating", value_kind="numeric", answer="-5"),
        # An author-defined option value is trusted input — left as-is.
        _row(question="choice", value_kind="option", answer="-maybe", option_label="Maybe"),
    ]
    by_q = {r["question"]: r for r in _parse("".join(iter_csv(rows, excel_safe=True)))}

    assert by_q["evil"]["answer"] == "'=cmd|'/c calc'!A1"
    assert by_q["hyperlink"]["answer"] == "'@SUM(1+1)"
    assert by_q["rating"]["answer"] == "-5"
    assert by_q["choice"]["answer"] == "-maybe"


# --- endpoint (marts read stubbed) -----------------------------------------


@pytest_asyncio.fixture
async def _analyst_override(db_session: AsyncSession) -> AsyncIterator[None]:
    """Point the export's marts session at the transactional test session, so no
    real stele_analyst connection is opened and any seeded rows stay in-transaction."""

    async def _yield() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_analyst_session] = _yield
    yield
    app.dependency_overrides.pop(get_analyst_session, None)


async def _create_survey(authed_client: AsyncClient) -> str:
    resp = await authed_client.post("/surveys", json={"definition_json": VALID_DEFINITION})
    assert resp.status_code == 201
    return str(resp.json()["survey_id"])


@pytest.mark.usefixtures("_analyst_override")
async def test_export_requires_auth(client: AsyncClient) -> None:
    resp = await client.get(f"/surveys/{uuid.uuid4()}/export")
    assert resp.status_code == 401


@pytest.mark.usefixtures("_analyst_override")
async def test_export_forbidden_for_non_author(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from api.auth import service as auth_service

    email = f"reviewer-{uuid.uuid4().hex[:8]}@example.com"
    await auth_service.create_user(db_session, email, PASSWORD, ["reviewer"])
    assert (
        await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    ).status_code == 200

    resp = await client.get(f"/surveys/{uuid.uuid4()}/export")
    assert resp.status_code == 403


@pytest.mark.usefixtures("_analyst_override")
async def test_export_unknown_survey_404(authed_client: AsyncClient) -> None:
    resp = await authed_client.get(f"/surveys/{uuid.uuid4()}/export")
    assert resp.status_code == 404


@pytest.mark.usefixtures("_analyst_override")
async def test_export_streams_csv_with_disposition(
    authed_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    survey_id = await _create_survey(authed_client)

    canned = [
        {
            "respondent_id": "r1",
            "survey_id": survey_id,
            "survey_version": 1,
            "question": "q1",
            "prompt_text": "Q1?",
            "value_kind": "option",
            "occurrence": 1,
            "answer": "a",
            "option_label": "A",
            "was_shown": True,
            "value_text_redacted": False,
            "rank": None,
        }
    ]

    async def _stub(_session: AsyncSession, _survey_id: uuid.UUID) -> list[dict[str, Any]]:
        return canned

    monkeypatch.setattr(export, "fetch_survey_export_rows", _stub)

    resp = await authed_client.get(f"/surveys/{survey_id}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="survey-{survey_id}-responses.csv"'
    )
    parsed = _parse(resp.text)
    assert len(parsed) == 1
    assert parsed[0]["question"] == "q1"
    assert parsed[0]["answer"] == "a"


@pytest.mark.usefixtures("_analyst_override")
async def test_export_excel_safe_escapes_and_names_file(
    authed_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    survey_id = await _create_survey(authed_client)
    danger = [
        {
            "respondent_id": "r1",
            "survey_id": survey_id,
            "survey_version": 1,
            "question": "comment",
            "prompt_text": "Comment?",
            "value_kind": "text",
            "occurrence": 1,
            "answer": "=HYPERLINK(0)",
            "option_label": None,
            "was_shown": True,
            "value_text_redacted": False,
            "rank": None,
        }
    ]

    async def _stub(_session: AsyncSession, _survey_id: uuid.UUID) -> list[dict[str, Any]]:
        return danger

    monkeypatch.setattr(export, "fetch_survey_export_rows", _stub)

    # excel_safe neutralizes the formula and names the file distinctly.
    safe = await authed_client.get(f"/surveys/{survey_id}/export?excel_safe=true")
    assert safe.status_code == 200
    assert safe.headers["content-disposition"] == (
        f'attachment; filename="survey-{survey_id}-responses-excel.csv"'
    )
    assert _parse(safe.text)[0]["answer"] == "'=HYPERLINK(0)"

    # The default download stays faithful.
    plain = await authed_client.get(f"/surveys/{survey_id}/export")
    assert _parse(plain.text)[0]["answer"] == "=HYPERLINK(0)"
    assert "-excel" not in plain.headers["content-disposition"]


@pytest.mark.usefixtures("_analyst_override")
async def test_export_known_survey_no_rows_is_header_only(
    authed_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    survey_id = await _create_survey(authed_client)

    async def _empty(_session: AsyncSession, _survey_id: uuid.UUID) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(export, "fetch_survey_export_rows", _empty)

    resp = await authed_client.get(f"/surveys/{survey_id}/export")
    assert resp.status_code == 200
    assert _parse(resp.text) == []


# --- end-to-end against real marts (skips when not built) ------------------


async def _marts_built(session: AsyncSession) -> bool:
    return (
        await session.execute(text("select to_regclass('marts.fact_response_item')"))
    ).scalar() is not None


@pytest.mark.usefixtures("_analyst_override")
async def test_export_reads_real_marts_scoped_to_survey(
    authed_client: AsyncClient, db_session: AsyncSession
) -> None:
    if not await _marts_built(db_session):
        pytest.skip("marts tables not built in this environment (no dbt run)")

    survey_id = uuid.UUID(await _create_survey(authed_client))
    other_survey_id = uuid.UUID(await _create_survey(authed_client))
    respondent_id = uuid.uuid4()

    async def ins(sql: str, params: list[dict[str, Any]]) -> None:
        await db_session.execute(text(sql), params)

    await ins(
        "insert into marts.dim_respondent (respondent_id) values (:respondent_id)",
        [{"respondent_id": respondent_id}],
    )
    await ins(
        "insert into marts.dim_survey_version (survey_version_id, survey_id, version) "
        "values (:svid, :survey_id, :version)",
        [
            {"svid": "sv-mine", "survey_id": survey_id, "version": 1},
            {"svid": "sv-other", "survey_id": other_survey_id, "version": 1},
        ],
    )
    await ins(
        "insert into marts.dim_question (question_id, survey_id, stable_name) "
        "values (:qid, :survey_id, :stable_name)",
        [
            {"qid": "q-color", "survey_id": survey_id, "stable_name": "fav_color"},
            {"qid": "q-comment", "survey_id": survey_id, "stable_name": "comment"},
            {"qid": "q-other", "survey_id": other_survey_id, "stable_name": "other_q"},
        ],
    )
    await ins(
        "insert into marts.dim_question_version "
        "(question_version_id, question_id, prompt_text, value_kind) "
        "values (:qvid, :qid, :prompt, :value_kind)",
        [
            {"qvid": "qv-color", "qid": "q-color", "prompt": "Colour?", "value_kind": "option"},
            {"qvid": "qv-comment", "qid": "q-comment", "prompt": "Comment?", "value_kind": "text"},
            {"qvid": "qv-other", "qid": "q-other", "prompt": "Other?", "value_kind": "text"},
        ],
    )
    await ins(
        "insert into marts.dim_option (option_key, question_version_id, value, label, display_order) "
        "values (:ok, :qvid, :value, :label, :ord)",
        [{"ok": "opt-blue", "qvid": "qv-color", "value": "blue", "label": "Blue", "ord": 1}],
    )
    await ins(
        "insert into marts.fact_response_item "
        "(fact_id, respondent_id, survey_version_id, question_id, question_version_id, "
        " occurrence, option_key, value_text, value_text_redacted, was_shown) values "
        "(:fid, :rid, :svid, :qid, :qvid, 1, :ok, :vtext, :red, :shown)",
        [
            {
                "fid": "f-color",
                "rid": respondent_id,
                "svid": "sv-mine",
                "qid": "q-color",
                "qvid": "qv-color",
                "ok": "opt-blue",
                "vtext": None,
                "red": False,
                "shown": True,
            },
            {
                "fid": "f-comment",
                "rid": respondent_id,
                "svid": "sv-mine",
                "qid": "q-comment",
                "qvid": "qv-comment",
                "ok": None,
                "vtext": "Great, really",
                "red": False,
                "shown": True,
            },
            # Belongs to the OTHER survey — must not appear in this export.
            {
                "fid": "f-other",
                "rid": respondent_id,
                "svid": "sv-other",
                "qid": "q-other",
                "qvid": "qv-other",
                "ok": None,
                "vtext": "leak?",
                "red": False,
                "shown": True,
            },
        ],
    )

    resp = await authed_client.get(f"/surveys/{survey_id}/export")
    assert resp.status_code == 200
    parsed = _parse(resp.text)

    questions = {r["question"] for r in parsed}
    assert questions == {"fav_color", "comment"}  # other survey's row excluded
    by_q = {r["question"]: r for r in parsed}
    assert by_q["fav_color"]["answer"] == "blue"
    assert by_q["fav_color"]["option_label"] == "Blue"
    assert by_q["comment"]["answer"] == "Great, really"
