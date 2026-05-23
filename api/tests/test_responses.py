from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VALID_DEFINITION: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [{"type": "radiogroup", "name": "q1", "choices": ["a", "b"]}],
        }
    ]
}


def _free_text_definition(pii_risk: str | None, *, rationale: str | None = None) -> dict[str, Any]:
    """A one-question free-text survey, optionally tagged with pii_risk."""
    element: dict[str, Any] = {"type": "comment", "name": "ft1"}
    if pii_risk is not None:
        element["pii_risk"] = pii_risk
    if rationale is not None:
        element["pii_risk_rationale"] = rationale
    return {"pages": [{"name": "p1", "elements": [element]}]}


async def _publish_survey(
    client: AsyncClient, definition: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Create + publish a survey; return (survey_id, definition_hash)."""
    created = await client.post(
        "/surveys", json={"definition_json": definition or VALID_DEFINITION}
    )
    survey_id = created.json()["survey_id"]
    published = await client.post(f"/surveys/{survey_id}/versions/1/publish")
    return survey_id, published.json()["definition_hash"]


async def _free_text_rows(db_session: AsyncSession, survey_id: str) -> list[tuple[str, str, str]]:
    rows = (
        await db_session.execute(
            text(
                "SELECT ft.question_name, ft.value_text, ft.pii_risk "
                "FROM pii.free_text_responses ft "
                "JOIN app.raw_responses rr ON rr.id = ft.raw_response_id "
                "WHERE rr.survey_id = :sid"
            ),
            {"sid": survey_id},
        )
    ).all()
    return [(r[0], r[1], r[2]) for r in rows]


async def test_submit_persists_raw_and_read_model(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    survey_id, definition_hash = await _publish_survey(client)

    response = await client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={
            "definition_hash": definition_hash,
            "payload": {"q1": "a"},
            "shown_questions": ["q1"],
        },
    )
    assert response.status_code == 201
    assert response.json()["raw_response_id"] > 0

    raw_count = (
        await db_session.execute(
            text("SELECT count(*) FROM app.raw_responses WHERE survey_id = :sid"),
            {"sid": survey_id},
        )
    ).scalar_one()
    assert raw_count == 1

    shown = (
        await db_session.execute(
            text("SELECT shown_questions FROM app.raw_responses WHERE survey_id = :sid"),
            {"sid": survey_id},
        )
    ).scalar_one()
    assert shown == ["q1"]

    item_count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM app.response_items ri "
                "JOIN app.responses r ON r.id = ri.response_id "
                "WHERE r.survey_id = :sid"
            ),
            {"sid": survey_id},
        )
    ).scalar_one()
    assert item_count == 1


async def test_submit_embeds_definition_snapshot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The raw row freezes the published definition + hash so dbt can rebuild
    dimensions from raw_responses alone (invariant 1/4, NFR-1)."""
    survey_id, definition_hash = await _publish_survey(client)

    await client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={
            "definition_hash": definition_hash,
            "payload": {"q1": "a"},
            "shown_questions": ["q1"],
        },
    )

    snapshot = (
        await db_session.execute(
            text("SELECT definition_snapshot FROM app.raw_responses WHERE survey_id = :sid"),
            {"sid": survey_id},
        )
    ).scalar_one()
    assert snapshot is not None
    assert snapshot["definition_hash"] == definition_hash
    assert snapshot["definition"] == VALID_DEFINITION
    assert snapshot["published_at"] is not None


async def test_submit_rejects_hash_drift(client: AsyncClient) -> None:
    survey_id, _ = await _publish_survey(client)
    response = await client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={
            "definition_hash": "0" * 64,
            "payload": {"q1": "a"},
            "shown_questions": ["q1"],
        },
    )
    assert response.status_code == 409


async def test_submit_rejects_unpublished(client: AsyncClient) -> None:
    created = await client.post("/surveys", json={"definition_json": VALID_DEFINITION})
    survey_id = created.json()["survey_id"]
    response = await client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={"definition_hash": "x", "payload": {"q1": "a"}, "shown_questions": ["q1"]},
    )
    assert response.status_code == 409


async def test_submit_unknown_survey_404(client: AsyncClient) -> None:
    response = await client.post(
        "/surveys/00000000-0000-0000-0000-000000000000/versions/1/responses",
        json={"definition_hash": "x", "payload": {}, "shown_questions": []},
    )
    assert response.status_code == 404


async def test_high_risk_freetext_routed_to_pii(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """High-risk free text lands in the restricted pii store, and the operational
    read-model still keeps a faithful copy of the payload (design doc §3.9)."""
    definition = _free_text_definition("high")
    survey_id, definition_hash = await _publish_survey(client, definition)

    await client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={
            "definition_hash": definition_hash,
            "payload": {"ft1": "my secret note"},
            "shown_questions": ["ft1"],
        },
    )

    assert await _free_text_rows(db_session, survey_id) == [("ft1", "my secret note", "high")]

    # response_items keeps the value as-is — same app-schema trust boundary as raw.
    item_value = (
        await db_session.execute(
            text(
                "SELECT ri.value FROM app.response_items ri "
                "JOIN app.responses r ON r.id = ri.response_id "
                "WHERE r.survey_id = :sid AND ri.question_name = 'ft1'"
            ),
            {"sid": survey_id},
        )
    ).scalar_one()
    assert item_value == "my secret note"


async def test_low_risk_freetext_not_in_pii(client: AsyncClient, db_session: AsyncSession) -> None:
    definition = _free_text_definition("low", rationale="non-identifying free text")
    survey_id, definition_hash = await _publish_survey(client, definition)

    await client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={
            "definition_hash": definition_hash,
            "payload": {"ft1": "fine to keep"},
            "shown_questions": ["ft1"],
        },
    )

    assert await _free_text_rows(db_session, survey_id) == []


async def test_absent_pii_risk_defaults_high(client: AsyncClient, db_session: AsyncSession) -> None:
    """No pii_risk tag → treated as high; value routes to the pii store."""
    definition = _free_text_definition(None)
    survey_id, definition_hash = await _publish_survey(client, definition)

    await client.post(
        f"/surveys/{survey_id}/versions/1/responses",
        json={
            "definition_hash": definition_hash,
            "payload": {"ft1": "untagged answer"},
            "shown_questions": ["ft1"],
        },
    )

    assert await _free_text_rows(db_session, survey_id) == [("ft1", "untagged answer", "high")]
