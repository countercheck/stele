"""Survey authoring + publishing logic.

Drafts are mutable; publishing freezes a definition: it validates, computes a
canonical SHA-256 hash, and flips status to 'published'. Published rows are
immutable — a change means a new draft at the next version (design doc §3.6,
invariant 2).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.survey_engine.models import RawResponse, Response, ResponseItem, SurveyDefinition
from api.survey_engine.schemas import ResponseSubmit


class SurveyNotFound(Exception):
    pass


class SurveyConflict(Exception):
    """Operation not allowed in the survey's current state."""


class InvalidDefinition(Exception):
    """Definition failed publish-time validation."""


class SubmissionRejected(Exception):
    """Submission could not be accepted (survey not published, or hash drift)."""


def canonical_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_definition(definition: dict[str, Any]) -> None:
    # Minimal gate for the slice. Full schema/lint/round-trip checks land in M3.
    if not definition:
        raise InvalidDefinition("definition must be a non-empty object")
    if "pages" not in definition and "elements" not in definition:
        raise InvalidDefinition("definition must contain 'pages' or 'elements'")


async def _get(
    session: AsyncSession, survey_id: uuid.UUID, version: int
) -> SurveyDefinition | None:
    result = await session.execute(
        select(SurveyDefinition).where(
            SurveyDefinition.survey_id == survey_id,
            SurveyDefinition.version == version,
        )
    )
    return result.scalar_one_or_none()


async def create_draft(session: AsyncSession, definition_json: dict[str, Any]) -> SurveyDefinition:
    survey = SurveyDefinition(
        survey_id=uuid.uuid4(),
        version=1,
        definition_json=definition_json,
        status="draft",
    )
    session.add(survey)
    await session.commit()
    await session.refresh(survey)
    return survey


async def create_draft_version(
    session: AsyncSession, survey_id: uuid.UUID, clone: bool
) -> SurveyDefinition:
    """Start a new draft at the next version, optionally cloning the latest definition."""
    max_version = (
        await session.execute(
            select(func.max(SurveyDefinition.version)).where(
                SurveyDefinition.survey_id == survey_id
            )
        )
    ).scalar_one_or_none()
    if max_version is None:
        raise SurveyNotFound(str(survey_id))

    definition: dict[str, Any] = {}
    if clone:
        latest = await _get(session, survey_id, max_version)
        if latest is not None:
            definition = dict(latest.definition_json)

    survey = SurveyDefinition(
        survey_id=survey_id,
        version=max_version + 1,
        definition_json=definition,
        status="draft",
    )
    session.add(survey)
    await session.commit()
    await session.refresh(survey)
    return survey


async def edit_draft(
    session: AsyncSession,
    survey_id: uuid.UUID,
    version: int,
    definition_json: dict[str, Any],
) -> SurveyDefinition:
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "draft":
        raise SurveyConflict("published surveys are immutable; create a new draft to make changes")
    survey.definition_json = definition_json
    await session.commit()
    await session.refresh(survey)
    return survey


async def publish(session: AsyncSession, survey_id: uuid.UUID, version: int) -> SurveyDefinition:
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "draft":
        raise SurveyConflict("survey is already published")
    validate_definition(survey.definition_json)
    survey.definition_hash = canonical_hash(survey.definition_json)
    survey.status = "published"
    survey.published_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(survey)
    return survey


async def get_definition(
    session: AsyncSession, survey_id: uuid.UUID, version: int
) -> SurveyDefinition | None:
    return await _get(session, survey_id, version)


async def submit_response(
    session: AsyncSession,
    survey_id: uuid.UUID,
    version: int,
    submission: ResponseSubmit,
) -> Response:
    """Append the raw submission and derive the normalized read-model in one
    transaction. Both come from the same in-memory payload — the API never reads
    raw_responses back to build the read-model (invariant 1/4).
    """
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "published":
        raise SubmissionRejected("survey version is not published")
    if submission.definition_hash != survey.definition_hash:
        raise SubmissionRejected("definition hash mismatch; the survey has drifted")

    respondent_id = submission.respondent_id or uuid.uuid4()
    submitted_at = datetime.now(UTC)

    raw = RawResponse(
        respondent_id=respondent_id,
        survey_id=survey_id,
        survey_version=version,
        submitted_at=submitted_at,
        payload=submission.payload,
        shown_questions=submission.shown_questions,
        client_metadata=submission.client_metadata,
        # Freeze the definition this response was answered against, so the
        # warehouse can rebuild dimensions from raw_responses alone. The
        # published row is immutable, so this snapshot can never drift.
        definition_snapshot={
            "definition": survey.definition_json,
            "definition_hash": survey.definition_hash,
            "published_at": survey.published_at.isoformat() if survey.published_at else None,
        },
    )
    session.add(raw)
    await session.flush()

    response = Response(
        raw_response_id=raw.id,
        respondent_id=respondent_id,
        survey_id=survey_id,
        survey_version=version,
        submitted_at=submitted_at,
    )
    session.add(response)
    await session.flush()

    for question_name, value in submission.payload.items():
        session.add(ResponseItem(response_id=response.id, question_name=question_name, value=value))

    await session.commit()
    await session.refresh(response)
    return response
