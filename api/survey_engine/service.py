"""Survey authoring + publishing logic.

Drafts are mutable; publishing freezes a definition: it validates, computes a
canonical SHA-256 hash, and flips status to 'published'. Published rows are
immutable — a change means a new draft at the next version (design doc §3.6,
invariant 2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.survey_engine import round_trip
from api.survey_engine.models import (
    FreeTextResponse,
    FreeTextReviewDecision,
    FreeTextScrub,
    RawResponse,
    Response,
    ResponseItem,
    SurveyDefinition,
    SurveyShortCode,
    Withdrawal,
)
from api.survey_engine.round_trip import RoundTripUnavailable
from api.survey_engine.schemas import ResponseSubmit

# Publish-time definition validation (schema + lint + PII gate) lives in
# validation.py. Re-exported here so callers (router catches
# service.InvalidDefinition) and submit_response keep one import surface.
from api.survey_engine.validation import (
    FreeTextQuestion,
    InvalidDefinition,
    extract_free_text_questions,
    validate_definition,
)

__all__ = [
    "InvalidDefinition",
    "RoundTripUnavailable",
    "extract_free_text_questions",
    "validate_definition",
]


class SurveyNotFound(Exception):
    pass


class SurveyConflict(Exception):
    """Operation not allowed in the survey's current state."""


class SubmissionRejected(Exception):
    """Submission could not be accepted (survey not published, or hash drift)."""


class FreeTextResponseNotFound(Exception):
    """No high-risk free-text answer with the given id (reviewer screening)."""


class ScrubIncomplete(Exception):
    """A field-level scrub could not be verified to have nulled the raw value.

    Raised (and the transaction rolled back) when the live raw_responses row was
    matched but its targeted value did not become JSON null — i.e. the resolved
    path was wrong or out of range. Erasure code must not commit a scrub audit row
    (and clear the PII copy) while PII survives in the append-only source, and the
    audit-row idempotency guard would otherwise lock that partial state in. No
    data is changed; the caller may retry.
    """


class InvalidShortCode(Exception):
    """The proposed short code doesn't satisfy the link-safe format."""


class ShortCodeTaken(Exception):
    """Another survey already owns the requested short code."""


def canonical_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _free_text_answer_to_text(answer: Any) -> str | None:
    if answer is None:
        return None
    if isinstance(answer, str):
        return answer
    # Keep non-string JSON payload values auditable and stable in storage.
    return json.dumps(answer, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _high_risk_answers(
    question: FreeTextQuestion, payload: dict[str, Any]
) -> list[tuple[int, Any]]:
    """The (occurrence, answer) pairs to copy into the PII store for one free-text
    question. A plain question yields at most one pair at occurrence 1; a panel cell
    (M5.4) yields one per occurrence present in the panel array. Occurrences with
    the cell key absent are skipped — there's no answer to copy."""
    if question.panel_name is None:
        if question.name in payload:
            return [(1, payload[question.name])]
        return []
    instances = payload.get(question.panel_name)
    if not isinstance(instances, list):
        return []
    pairs: list[tuple[int, Any]] = []
    for occurrence, instance in enumerate(instances, start=1):
        if isinstance(instance, dict) and question.element_name in instance:
            pairs.append((occurrence, instance[question.element_name]))
    return pairs


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


async def create_draft(
    session: AsyncSession,
    definition_json: dict[str, Any],
    for_real_respondents: bool | None = None,
) -> SurveyDefinition:
    survey = SurveyDefinition(
        survey_id=uuid.uuid4(),
        version=1,
        definition_json=definition_json,
        status="draft",
        # Gate by default when the caller doesn't specify.
        for_real_respondents=True if for_real_respondents is None else for_real_respondents,
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
    for_real_respondents = True
    if clone:
        latest = await _get(session, survey_id, max_version)
        if latest is not None:
            definition = dict(latest.definition_json)
            for_real_respondents = latest.for_real_respondents

    survey = SurveyDefinition(
        survey_id=survey_id,
        version=max_version + 1,
        definition_json=definition,
        status="draft",
        for_real_respondents=for_real_respondents,
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
    for_real_respondents: bool | None = None,
) -> SurveyDefinition:
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "draft":
        raise SurveyConflict("published surveys are immutable; create a new draft to make changes")
    survey.definition_json = definition_json
    # None preserves the existing flag (a definition-only edit shouldn't flip it).
    if for_real_respondents is not None:
        survey.for_real_respondents = for_real_respondents
    await session.commit()
    await session.refresh(survey)
    return survey


async def publish(session: AsyncSession, survey_id: uuid.UUID, version: int) -> SurveyDefinition:
    survey = await _get(session, survey_id, version)
    if survey is None:
        raise SurveyNotFound(f"{survey_id} v{version}")
    if survey.status != "draft":
        raise SurveyConflict("survey is already published")
    # Publish gate, in order (design doc §3.6): schema + lint, then — for surveys
    # going to real respondents — the headless round-trip, then hash + freeze.
    validate_definition(survey.definition_json)
    if survey.for_real_respondents:
        # run_round_trip shells out to Node (subprocess, up to 30s). Off-load it
        # to a thread so it doesn't block the event loop for other requests.
        await asyncio.to_thread(round_trip.run_round_trip, survey.definition_json)
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


async def list_definitions(session: AsyncSession) -> list[SurveyDefinition]:
    """All survey/version rows, newest first — backs the admin survey list.

    Returns every (survey_id, version) row, not just the latest per survey: the
    admin needs to see draft and published versions side by side to decide where
    to edit or publish. Definition JSON is omitted from the list view (callers use
    the detail endpoint); the ORM rows still carry it but the list schema drops it.
    """
    result = await session.execute(
        select(SurveyDefinition).order_by(
            SurveyDefinition.created_at.desc(), SurveyDefinition.version.desc()
        )
    )
    return list(result.scalars().all())


async def list_definitions_with_counts(
    session: AsyncSession,
) -> list[tuple[SurveyDefinition, int]]:
    """Every survey/version row (newest first), each with its live response count.

    Counts come from app.raw_responses (the source of truth), not the read-model,
    and exclude withdrawn responses. The filter keys on `definition_snapshot IS NOT
    NULL` — the same discriminator dbt's stg_raw_responses uses to drop tombstoned
    rows (the tombstone workflow nulls all content columns together; see the model
    docstring on why definition_snapshot is the canonical choice). A version with no
    responses yields 0 via the outer join.
    """
    counts = (
        select(
            RawResponse.survey_id.label("survey_id"),
            RawResponse.survey_version.label("survey_version"),
            func.count().label("n"),
        )
        .where(RawResponse.definition_snapshot.isnot(None))
        .group_by(RawResponse.survey_id, RawResponse.survey_version)
        .subquery()
    )
    result = await session.execute(
        select(SurveyDefinition, func.coalesce(counts.c.n, 0))
        .outerjoin(
            counts,
            (counts.c.survey_id == SurveyDefinition.survey_id)
            & (counts.c.survey_version == SurveyDefinition.version),
        )
        .order_by(SurveyDefinition.created_at.desc(), SurveyDefinition.version.desc())
    )
    return [(definition, int(count)) for definition, count in result.all()]


# Link-safe short codes: lowercase letters/digits/hyphens, no leading/trailing
# hyphen, 3-64 chars. Keeps the /s/<code> path clean and unambiguous (URLs are
# case-sensitive in the path, so we normalise to lowercase before validating).
_SHORT_CODE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_SHORT_CODE_MIN = 3
_SHORT_CODE_MAX = 64


def normalize_short_code(raw: str) -> str:
    """Trim + lowercase a proposed code and validate its format.

    Raises InvalidShortCode with a human-readable reason (surfaced to the operator
    as a 422 detail). Returns the canonical form to store.
    """
    code = raw.strip().lower()
    if not (_SHORT_CODE_MIN <= len(code) <= _SHORT_CODE_MAX):
        raise InvalidShortCode(f"short code must be {_SHORT_CODE_MIN}-{_SHORT_CODE_MAX} characters")
    if not _SHORT_CODE_RE.fullmatch(code):
        raise InvalidShortCode(
            "short code may contain only lowercase letters, digits, and hyphens, "
            "and may not start or end with a hyphen"
        )
    return code


async def survey_exists(session: AsyncSession, survey_id: uuid.UUID) -> bool:
    return (
        await session.execute(
            select(SurveyDefinition.id).where(SurveyDefinition.survey_id == survey_id).limit(1)
        )
    ).first() is not None


async def set_short_code(
    session: AsyncSession, survey_id: uuid.UUID, raw_code: str
) -> SurveyShortCode:
    """Assign (or reassign) a survey's short code.

    Idempotent per survey: a survey already holding a code has it replaced. Raises
    SurveyNotFound for an unknown survey, InvalidShortCode for a bad format, and
    ShortCodeTaken if a *different* survey already owns the code (the unique
    constraint is the real guard against a concurrent claim; the pre-check just
    yields a clean error on the common path).
    """
    if not await survey_exists(session, survey_id):
        raise SurveyNotFound(str(survey_id))
    code = normalize_short_code(raw_code)

    owner = (
        await session.execute(
            select(SurveyShortCode.survey_id).where(SurveyShortCode.short_code == code)
        )
    ).scalar_one_or_none()
    if owner is not None and owner != survey_id:
        raise ShortCodeTaken(code)

    now = datetime.now(UTC)
    stmt = pg_insert(SurveyShortCode).values(
        survey_id=survey_id, short_code=code, created_at=now, updated_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SurveyShortCode.survey_id],
        set_={"short_code": code, "updated_at": now},
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except IntegrityError as exc:
        # Lost the race on the short_code unique constraint to a concurrent claim.
        await session.rollback()
        raise ShortCodeTaken(code) from exc

    row = (
        await session.execute(select(SurveyShortCode).where(SurveyShortCode.survey_id == survey_id))
    ).scalar_one()
    return row


async def clear_short_code(session: AsyncSession, survey_id: uuid.UUID) -> bool:
    """Remove a survey's short code (frees it for reuse). Returns True if a code
    was removed, False if the survey had none — both are success for the caller."""
    result = cast(
        CursorResult[Any],
        await session.execute(
            delete(SurveyShortCode).where(SurveyShortCode.survey_id == survey_id)
        ),
    )
    await session.commit()
    return (result.rowcount or 0) > 0


async def get_short_codes_map(session: AsyncSession) -> dict[uuid.UUID, str]:
    """survey_id → short_code for every survey that has one (backs the admin list)."""
    rows = (
        await session.execute(select(SurveyShortCode.survey_id, SurveyShortCode.short_code))
    ).all()
    return {survey_id: code for survey_id, code in rows}


async def resolve_short_code(session: AsyncSession, raw_code: str) -> tuple[uuid.UUID, int] | None:
    """Resolve a short code to (survey_id, latest published version) for the
    public link. Returns None when the code is unknown *or* the survey has no
    published version yet — the respondent endpoint treats both as 404, so a
    public caller can't probe which codes exist."""
    code = raw_code.strip().lower()
    survey_id = (
        await session.execute(
            select(SurveyShortCode.survey_id).where(SurveyShortCode.short_code == code)
        )
    ).scalar_one_or_none()
    if survey_id is None:
        return None
    version = (
        await session.execute(
            select(func.max(SurveyDefinition.version)).where(
                SurveyDefinition.survey_id == survey_id,
                SurveyDefinition.status == "published",
            )
        )
    ).scalar_one_or_none()
    if version is None:
        return None
    return (survey_id, version)


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

    # Copy high-PII-risk free-text answers into the restricted pii store for the
    # reviewer (design doc §3.9). The operational read-model above stays a
    # faithful copy of the payload — same app-schema trust boundary as raw, so
    # redacting it gains nothing; the analyst boundary is the dbt marts.
    #
    # A free-text panel cell (paneldynamic, M5.4) repeats per occurrence: its
    # answer lives at payload[panel][i][element], so copy one row per occurrence,
    # keyed by the composite "panel.element" name + the 1-based occurrence — the
    # same grain the marts resolve the promotion decision at. A plain free-text
    # question is occurrence 1.
    for question in extract_free_text_questions(survey.definition_json):
        if question.effective_risk != "high":
            continue
        for occurrence, answer in _high_risk_answers(question, submission.payload):
            session.add(
                FreeTextResponse(
                    raw_response_id=raw.id,
                    question_name=question.name,
                    occurrence=occurrence,
                    value_text=_free_text_answer_to_text(answer),
                    pii_risk="high",
                )
            )

    await session.commit()
    await session.refresh(response)
    return response


@dataclass(frozen=True)
class FreeTextReviewItem:
    """One high-risk free-text answer in the reviewer queue, with its raw-row
    context and (when decided) screening status. status is None when pending."""

    id: int
    raw_response_id: int
    respondent_id: uuid.UUID
    survey_id: uuid.UUID
    survey_version: int
    question_name: str
    occurrence: int
    value_text: str | None
    created_at: datetime
    status: str | None


@dataclass(frozen=True)
class FreeTextDecisionResult:
    """Outcome of a recorded promote/reject decision."""

    free_text_id: int
    raw_response_id: int
    question_name: str
    status: str
    reviewed_at: datetime


@dataclass(frozen=True)
class FreeTextScrubResult:
    """Outcome of a field-level scrub (design §3.8).

    On the idempotent path (the answer was already scrubbed) `already_scrubbed`
    is true and the counts reflect the original scrub's effect as zero — nothing
    was changed this call. `scrubbed_at` is the original timestamp in that case.
    """

    free_text_id: int
    raw_response_id: int
    question_name: str
    occurrence: int
    scrubbed_at: datetime
    already_scrubbed: bool
    raw_payload_scrubbed: bool
    read_model_items_scrubbed: int
    pii_value_cleared: bool


@dataclass(frozen=True)
class WithdrawalResult:
    """Outcome of a withdrawal: the audit record plus what this call erased.

    On the idempotent path (respondent already withdrawn) the counts are zero
    and `already_withdrawn` is true — the original `requested_at` is preserved.
    """

    respondent_id: uuid.UUID
    requested_at: datetime
    already_withdrawn: bool
    raw_rows_tombstoned: int
    responses_purged: int
    pii_rows_deleted: int


async def withdraw_respondent(
    session: AsyncSession, respondent_id: uuid.UUID, reason: str | None = None
) -> WithdrawalResult:
    """Tombstone every trace of a respondent across all surveys, in one
    transaction (design doc §3.8 steps 1,2,3,5). Step 4 (marts) is automatic on
    the next dbt build: stg_raw_responses excludes the now-null-snapshot rows.

    Erasure is idempotent by nature: a repeat request for an already-withdrawn
    respondent returns the existing record with zero counts, without
    re-processing or overwriting the original timestamp. A respondent with no
    responses is still recorded as withdrawn (request honored, zero counts).
    """
    existing = (
        await session.execute(select(Withdrawal).where(Withdrawal.respondent_id == respondent_id))
    ).scalar_one_or_none()
    if existing is not None:
        return WithdrawalResult(
            respondent_id=respondent_id,
            requested_at=existing.requested_at,
            already_withdrawn=True,
            raw_rows_tombstoned=0,
            responses_purged=0,
            pii_rows_deleted=0,
        )

    # Step 1 — record the withdrawal. The unique constraint on respondent_id is
    # the real guard against a concurrent double-request; the check above just
    # makes the common repeat case a clean no-op.
    requested_at = datetime.now(UTC)
    session.add(Withdrawal(respondent_id=respondent_id, requested_at=requested_at, reason=reason))
    await session.flush()

    # Resolve the raw_response_ids up front. pii.free_text_responses cascades
    # only on a raw-row DELETE, and the tombstone NULLs (never deletes) raw
    # rows, so the PII deletion must be explicit (invariant 1 / design §3.8).
    raw_ids = (
        (
            await session.execute(
                select(RawResponse.id).where(RawResponse.respondent_id == respondent_id)
            )
        )
        .scalars()
        .all()
    )

    # Step 5 — delete the PII copy first (erase identifying data as early as
    # possible). No-op when the respondent had no high-risk free text.
    pii_rows_deleted = 0
    if raw_ids:
        pii_result = cast(
            CursorResult[Any],
            await session.execute(
                delete(FreeTextResponse).where(FreeTextResponse.raw_response_id.in_(raw_ids))
            ),
        )
        pii_rows_deleted = pii_result.rowcount or 0

    # Step 3 — purge the rebuildable read-model. response_items rows cascade via
    # the ON DELETE CASCADE FK on response_items.response_id.
    responses_result = cast(
        CursorResult[Any],
        await session.execute(delete(Response).where(Response.respondent_id == respondent_id)),
    )
    responses_purged = responses_result.rowcount or 0

    # Step 2 — tombstone raw: null the four content columns, keep the row so the
    # append-only audit log stays structurally complete. This and the field-level
    # scrub (scrub_free_text) are the only sanctioned UPDATEs of raw_responses
    # (CLAUDE.md / design §3.8); id, respondent_id, survey_id, survey_version and
    # submitted_at are preserved.
    raw_result = cast(
        CursorResult[Any],
        await session.execute(
            update(RawResponse)
            .where(RawResponse.respondent_id == respondent_id)
            .values(
                payload=None,
                shown_questions=None,
                client_metadata=None,
                definition_snapshot=None,
            )
        ),
    )
    raw_rows_tombstoned = raw_result.rowcount or 0

    await session.commit()
    return WithdrawalResult(
        respondent_id=respondent_id,
        requested_at=requested_at,
        already_withdrawn=False,
        raw_rows_tombstoned=raw_rows_tombstoned,
        responses_purged=responses_purged,
        pii_rows_deleted=pii_rows_deleted,
    )


async def list_withdrawals(
    session: AsyncSession, limit: int = 100, offset: int = 0
) -> list[Withdrawal]:
    """The pii.withdrawals erasure audit, newest first — backs the admin GDPR
    console. Read-only; the trigger path is withdraw_respondent."""
    result = await session.execute(
        select(Withdrawal).order_by(Withdrawal.requested_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


# Reviewer screening states. 'pending' = a high-risk free-text row with no
# decision yet (LEFT JOIN miss); 'promoted'/'rejected' read the recorded decision;
# 'scrubbed' = the answer's PII has been destroyed in place (field-level scrub,
# §3.8). Scrub is terminal and takes precedence: a scrubbed answer leaves the
# pending/promoted/rejected views (even if it once had a decision) and shows only
# under 'scrubbed', with a null value_text — the content is gone.
ReviewStatus = Literal["pending", "promoted", "rejected", "scrubbed"]


async def list_free_text_for_review(
    session: AsyncSession,
    status: ReviewStatus = "pending",
    limit: int = 100,
    offset: int = 0,
) -> list[FreeTextReviewItem]:
    """High-risk free-text answers in the reviewer queue (design §3.9 / §3.8).

    Joins pii.free_text_responses to its raw row (for respondent/survey context)
    and LEFT JOINs both the decision table and the scrub audit. 'pending' filters
    to undecided, un-scrubbed answers; 'promoted'/'rejected' to the recorded
    decision on still-present (un-scrubbed) answers; 'scrubbed' to answers whose
    PII has been destroyed. Carries the screened value_text (null once scrubbed) —
    gated to the reviewer role at the route.
    """
    decision = FreeTextReviewDecision
    scrub = FreeTextScrub
    query = (
        select(
            FreeTextResponse.id,
            FreeTextResponse.raw_response_id,
            RawResponse.respondent_id,
            RawResponse.survey_id,
            RawResponse.survey_version,
            FreeTextResponse.question_name,
            FreeTextResponse.occurrence,
            FreeTextResponse.value_text,
            FreeTextResponse.created_at,
            decision.status,
            scrub.id.label("scrub_id"),
        )
        .join(RawResponse, RawResponse.id == FreeTextResponse.raw_response_id)
        .outerjoin(
            decision,
            (decision.raw_response_id == FreeTextResponse.raw_response_id)
            & (decision.question_name == FreeTextResponse.question_name)
            & (decision.occurrence == FreeTextResponse.occurrence),
        )
        .outerjoin(
            scrub,
            (scrub.raw_response_id == FreeTextResponse.raw_response_id)
            & (scrub.question_name == FreeTextResponse.question_name)
            & (scrub.occurrence == FreeTextResponse.occurrence),
        )
        .order_by(FreeTextResponse.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status == "scrubbed":
        query = query.where(scrub.id.isnot(None))
    elif status == "pending":
        query = query.where(decision.status.is_(None), scrub.id.is_(None))
    else:
        query = query.where(decision.status == status, scrub.id.is_(None))

    rows = (await session.execute(query)).all()
    return [
        FreeTextReviewItem(
            id=r.id,
            raw_response_id=r.raw_response_id,
            respondent_id=r.respondent_id,
            survey_id=r.survey_id,
            survey_version=r.survey_version,
            question_name=r.question_name,
            occurrence=r.occurrence,
            value_text=r.value_text,
            created_at=r.created_at,
            # Scrub is terminal: report it as the status regardless of any prior
            # promote/reject decision the row may still carry.
            status="scrubbed" if r.scrub_id is not None else r.status,
        )
        for r in rows
    ]


async def record_free_text_decision(
    session: AsyncSession,
    free_text_id: int,
    reviewer_id: int | None,
    status: Literal["promoted", "rejected"],
    note: str | None = None,
) -> FreeTextDecisionResult:
    """Record (or update) a reviewer's promote/reject decision for one high-risk
    free-text answer. Idempotent: re-deciding the same answer overwrites the prior
    decision (status, reviewer, timestamp, note) rather than inserting a duplicate
    — the (raw_response_id, question_name, occurrence) unique constraint is the
    anchor (occurrence distinguishes a panel cell's repeated answers, M5.4).

    The write is a single-statement INSERT ... ON CONFLICT DO UPDATE so two
    concurrent decisions on the same answer can't race a SELECT-then-write into a
    constraint violation; the upsert resolves to one row either way.

    Raises FreeTextResponseNotFound if free_text_id is unknown.
    """
    free_text = (
        await session.execute(select(FreeTextResponse).where(FreeTextResponse.id == free_text_id))
    ).scalar_one_or_none()
    if free_text is None:
        raise FreeTextResponseNotFound

    decided_at = datetime.now(UTC)
    insert_stmt = pg_insert(FreeTextReviewDecision).values(
        raw_response_id=free_text.raw_response_id,
        question_name=free_text.question_name,
        occurrence=free_text.occurrence,
        status=status,
        reviewed_by=reviewer_id,
        reviewed_at=decided_at,
        note=note,
    )
    await session.execute(
        insert_stmt.on_conflict_do_update(
            constraint="uq_free_text_review_decisions_raw_question_occurrence",
            set_={
                "status": status,
                "reviewed_by": reviewer_id,
                "reviewed_at": decided_at,
                "note": note,
            },
        )
    )
    await session.commit()
    return FreeTextDecisionResult(
        free_text_id=free_text_id,
        raw_response_id=free_text.raw_response_id,
        question_name=free_text.question_name,
        status=status,
        reviewed_at=decided_at,
    )


def _scrub_target(
    raw: RawResponse, question_name: str, occurrence: int
) -> tuple[list[str], str, list[str] | None]:
    """Resolve where a scrubbed answer lives, from the row's own frozen definition.

    Returns ``(raw_path, item_key, item_path)``:

    - ``raw_path`` — the jsonb path inside ``raw_responses.payload`` to null.
    - ``item_key`` — the ``response_items.question_name`` (top-level payload key)
      holding the read-model copy.
    - ``item_path`` — the jsonb path inside that item's ``value`` to null, or
      ``None`` to null the whole value.

    A plain free-text answer sits at ``payload[name]`` (item_key = name, whole
    value nulled). A paneldynamic free-text cell (M5.4) sits at
    ``payload[panel][occurrence-1][element]`` (item_key = panel, the cell nulled
    inside the array). The panel/element split is read from the response's own
    definition_snapshot via the one shared free-text parser — never by splitting
    the composite "panel.element" name. If the question can't be located (e.g. a
    tombstoned row with a null snapshot, which a live free-text row never has), we
    fall back to the plain top-level path — correct for every non-panel answer, and
    for the (unreachable) case where it would be wrong for a panel, scrub_free_text
    verifies the raw value actually became null and aborts loudly rather than
    committing a partial erasure.
    """
    snapshot = raw.definition_snapshot or {}
    definition = snapshot.get("definition") if isinstance(snapshot, dict) else None
    if isinstance(definition, dict):
        for question in extract_free_text_questions(definition):
            if question.name != question_name:
                continue
            if question.panel_name is not None and question.element_name is not None:
                idx = str(occurrence - 1)
                return (
                    [question.panel_name, idx, question.element_name],
                    question.panel_name,
                    [idx, question.element_name],
                )
            break
    return ([question_name], question_name, None)


async def scrub_free_text(
    session: AsyncSession,
    free_text_id: int,
    reviewer_id: int | None,
    reason: str | None = None,
) -> FreeTextScrubResult:
    """Field-level scrub: destroy one high-risk free-text answer's PII in place,
    leaving the rest of the response intact (design doc §3.8).

    The surgical sibling of withdraw_respondent. In one transaction it nulls the
    answer across all three durable copies — the raw_responses payload value (the
    append-only ETL source), the operational read-model item, and
    pii.free_text_responses.value_text — and records a scrub audit row. The value
    is nulled *in place* (the payload key is kept), so downstream the answer still
    reads as shown + answered (jsonb_exists stays true) with a null value — the
    same redacted state high-risk free text already has in the marts — rather than
    collapsing into "skipped" (CLAUDE.md §"silent defaults"). shown_questions and
    every other answer are untouched, so the response stays in the warehouse.

    This is the second sanctioned UPDATE of append-only raw_responses, alongside
    the withdrawal tombstone (CLAUDE.md / design §3.8, invariant 1); it never
    DELETEs a raw row.

    Idempotent: a repeat scrub of an already-scrubbed answer is a no-op that
    returns the original record. Raises FreeTextResponseNotFound if free_text_id
    is unknown.
    """
    free_text = (
        await session.execute(select(FreeTextResponse).where(FreeTextResponse.id == free_text_id))
    ).scalar_one_or_none()
    if free_text is None:
        raise FreeTextResponseNotFound

    # Idempotency: the (raw_response_id, question_name, occurrence) unique
    # constraint is the real guard; this check makes the common repeat a clean
    # no-op that preserves the original scrubbed_at.
    existing = (
        await session.execute(
            select(FreeTextScrub).where(
                FreeTextScrub.raw_response_id == free_text.raw_response_id,
                FreeTextScrub.question_name == free_text.question_name,
                FreeTextScrub.occurrence == free_text.occurrence,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return FreeTextScrubResult(
            free_text_id=free_text_id,
            raw_response_id=free_text.raw_response_id,
            question_name=free_text.question_name,
            occurrence=free_text.occurrence,
            scrubbed_at=existing.scrubbed_at,
            already_scrubbed=True,
            raw_payload_scrubbed=False,
            read_model_items_scrubbed=0,
            pii_value_cleared=False,
        )

    raw = (
        await session.execute(
            select(RawResponse).where(RawResponse.id == free_text.raw_response_id)
        )
    ).scalar_one()
    raw_path, item_key, item_path = _scrub_target(
        raw, free_text.question_name, free_text.occurrence
    )

    # 1 — null the value in the append-only source, in place. The payload-not-null
    # guard skips a tombstoned row. create_missing is false: only an existing key
    # is nulled, never invented. RETURNING reports the post-update type at the
    # target path so we can *verify* the erasure rather than trust that a row
    # matched — jsonb_set is a silent no-op when the path is absent/out of range,
    # which would otherwise leave PII in the source while reporting success.
    returned = (
        await session.execute(
            text(
                "update app.raw_responses "
                "set payload = jsonb_set(payload, :path ::text[], 'null'::jsonb, false) "
                "where id = :raw_id and payload is not null "
                "returning jsonb_typeof(payload #> :path ::text[]) as leaf_type"
            ).bindparams(path=raw_path, raw_id=raw.id)
        )
    ).all()
    # No row → tombstoned/null payload: the source already holds no PII, so the
    # scrub is vacuously satisfied (proceed to clear the copy + audit). A matched
    # row whose target did not become JSON null (leaf_type != 'null', incl. an
    # absent path → NULL typeof) means the resolved path was wrong/out of range —
    # abort loudly so no audit row locks in a partial erasure.
    raw_payload_scrubbed = len(returned) > 0
    if raw_payload_scrubbed and returned[0].leaf_type != "null":
        # Capture before rollback: rollback expires the ORM row, so reading its
        # attributes afterwards would trigger lazy IO outside the async context.
        detail = (
            f"raw value for {free_text.question_name!r} (occurrence "
            f"{free_text.occurrence}) was not nulled; scrub aborted"
        )
        await session.rollback()
        raise ScrubIncomplete(detail)

    # 2 — mirror the null into the operational read-model item. A plain answer's
    # item value is nulled wholesale; a panel cell is nulled inside the item's
    # array. No-op when the read-model row is absent.
    if item_path is None:
        read_model_result = cast(
            CursorResult[Any],
            await session.execute(
                update(ResponseItem)
                .where(
                    ResponseItem.response_id.in_(
                        select(Response.id).where(Response.raw_response_id == raw.id)
                    ),
                    ResponseItem.question_name == item_key,
                )
                .values(value=None)
            ),
        )
    else:
        read_model_result = cast(
            CursorResult[Any],
            await session.execute(
                text(
                    "update app.response_items "
                    "set value = jsonb_set(value, :path ::text[], 'null'::jsonb, false) "
                    "where response_id in "
                    "(select id from app.responses where raw_response_id = :raw_id) "
                    "and question_name = :item_key and value is not null"
                ).bindparams(path=item_path, raw_id=raw.id, item_key=item_key)
            ),
        )
    read_model_items_scrubbed = read_model_result.rowcount or 0

    # 3 — clear the reviewer's PII copy (keep the row as the screening anchor).
    pii_value_cleared = free_text.value_text is not None
    free_text.value_text = None

    # 4 — record the scrub audit row. Capture the grain into locals first: a
    # rollback (the concurrent-race path below) expires the ORM row, so reading
    # its attributes afterwards would trigger lazy IO outside the async context.
    raw_response_id = free_text.raw_response_id
    question_name = free_text.question_name
    occurrence = free_text.occurrence
    scrubbed_at = datetime.now(UTC)
    session.add(
        FreeTextScrub(
            raw_response_id=raw_response_id,
            question_name=question_name,
            occurrence=occurrence,
            scrubbed_by=reviewer_id,
            scrubbed_at=scrubbed_at,
            reason=reason,
        )
    )

    try:
        await session.commit()
    except IntegrityError:
        # Lost the race on uq_free_text_scrubs_raw_question_occurrence: a
        # concurrent scrub of the same answer committed first. Its raw/read-model/
        # PII nulling is the same erasure we attempted, so the idempotent outcome
        # holds — return the winning record instead of surfacing a 500.
        await session.rollback()
        existing = (
            await session.execute(
                select(FreeTextScrub).where(
                    FreeTextScrub.raw_response_id == raw_response_id,
                    FreeTextScrub.question_name == question_name,
                    FreeTextScrub.occurrence == occurrence,
                )
            )
        ).scalar_one()
        return FreeTextScrubResult(
            free_text_id=free_text_id,
            raw_response_id=raw_response_id,
            question_name=question_name,
            occurrence=occurrence,
            scrubbed_at=existing.scrubbed_at,
            already_scrubbed=True,
            raw_payload_scrubbed=False,
            read_model_items_scrubbed=0,
            pii_value_cleared=False,
        )
    return FreeTextScrubResult(
        free_text_id=free_text_id,
        raw_response_id=raw_response_id,
        question_name=question_name,
        occurrence=occurrence,
        scrubbed_at=scrubbed_at,
        already_scrubbed=False,
        raw_payload_scrubbed=raw_payload_scrubbed,
        read_model_items_scrubbed=read_model_items_scrubbed,
        pii_value_cleared=pii_value_cleared,
    )
