"""Survey authoring + publishing endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from api.auth.deps import require_role
from api.db import AnalystSessionDep, SessionDep
from api.survey_engine import export, service
from api.survey_engine.schemas import (
    ResponseSubmit,
    ResponseSubmitOut,
    ShortCodeOut,
    ShortCodeResolved,
    ShortCodeSet,
    SurveyDefinitionDetail,
    SurveyDefinitionOut,
    SurveyDraftCreate,
    SurveyListItem,
)

router = APIRouter(prefix="/surveys", tags=["surveys"])

# Authoring (create/edit/publish/version) is operator-only; respondent-facing
# GET and submit stay public (design doc §3.10). Reviewers read PII, not authors.
_author_only = Depends(require_role("researcher", "admin"))


@router.get("", response_model=list[SurveyListItem], dependencies=[_author_only])
async def list_surveys(session: SessionDep) -> list[SurveyListItem]:
    rows = await service.list_definitions_with_counts(session)
    short_codes = await service.get_short_codes_map(session)
    return [
        SurveyListItem(
            **SurveyDefinitionOut.model_validate(s).model_dump(),
            response_count=n,
            short_code=short_codes.get(s.survey_id),
        )
        for s, n in rows
    ]


@router.post("", status_code=201, response_model=SurveyDefinitionOut, dependencies=[_author_only])
async def create_survey(body: SurveyDraftCreate, session: SessionDep) -> SurveyDefinitionOut:
    survey = await service.create_draft(session, body.definition_json, body.for_real_respondents)
    return SurveyDefinitionOut.model_validate(survey)


@router.post(
    "/{survey_id}/drafts",
    status_code=201,
    response_model=SurveyDefinitionOut,
    dependencies=[_author_only],
)
async def create_draft_version(
    survey_id: uuid.UUID, session: SessionDep, clone: bool = True
) -> SurveyDefinitionOut:
    try:
        survey = await service.create_draft_version(session, survey_id, clone)
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey not found") from None
    return SurveyDefinitionOut.model_validate(survey)


@router.put(
    "/{survey_id}/versions/{version}",
    response_model=SurveyDefinitionOut,
    dependencies=[_author_only],
)
async def edit_survey(
    survey_id: uuid.UUID, version: int, body: SurveyDraftCreate, session: SessionDep
) -> SurveyDefinitionOut:
    try:
        survey = await service.edit_draft(
            session, survey_id, version, body.definition_json, body.for_real_respondents
        )
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey version not found") from None
    except service.SurveyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return SurveyDefinitionOut.model_validate(survey)


@router.post(
    "/{survey_id}/versions/{version}/publish",
    response_model=SurveyDefinitionOut,
    dependencies=[_author_only],
)
async def publish_survey(
    survey_id: uuid.UUID, version: int, session: SessionDep
) -> SurveyDefinitionOut:
    try:
        survey = await service.publish(session, survey_id, version)
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey version not found") from None
    except service.SurveyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except service.InvalidDefinition as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except service.RoundTripUnavailable as exc:
        # The definition is fine; the round-trip oracle itself couldn't run. Fail
        # closed for a flagged survey (503) rather than publish without the gate.
        raise HTTPException(
            status_code=503, detail=f"round-trip validation unavailable: {exc}"
        ) from None
    return SurveyDefinitionOut.model_validate(survey)


@router.put(
    "/{survey_id}/short-code",
    response_model=ShortCodeOut,
    dependencies=[_author_only],
)
async def set_short_code(
    survey_id: uuid.UUID, body: ShortCodeSet, session: SessionDep
) -> ShortCodeOut:
    try:
        row = await service.set_short_code(session, survey_id, body.short_code)
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey not found") from None
    except service.InvalidShortCode as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except service.ShortCodeTaken:
        raise HTTPException(status_code=409, detail="that short code is already in use") from None
    return ShortCodeOut.model_validate(row)


@router.delete("/{survey_id}/short-code", status_code=204, dependencies=[_author_only])
async def clear_short_code(survey_id: uuid.UUID, session: SessionDep) -> None:
    # Idempotent: clearing a survey with no code is still a 204 (the resource is
    # absent either way), so the UI needn't special-case it.
    await service.clear_short_code(session, survey_id)


@router.get("/{survey_id}/export", dependencies=[_author_only])
async def export_survey_responses(
    survey_id: uuid.UUID,
    session: SessionDep,
    analyst: AnalystSessionDep,
) -> StreamingResponse:
    """Download a survey's responses as a tidy/long CSV (one row per selection).

    Reads the marts warehouse over the analyst connection (stele_api can't), so
    it reflects the last ETL build and carries no un-promoted PII. 404 if the
    survey is unknown; a known survey with no warehouse rows yields a header-only
    CSV rather than an error.
    """
    if not await service.survey_exists(session, survey_id):
        raise HTTPException(status_code=404, detail="survey not found")
    rows = await export.fetch_survey_export_rows(analyst, survey_id)
    return StreamingResponse(
        export.iter_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="survey-{survey_id}-responses.csv"'},
    )


@router.get("/by-code/{short_code}", response_model=ShortCodeResolved)
async def resolve_short_code(short_code: str, session: SessionDep) -> ShortCodeResolved:
    resolved = await service.resolve_short_code(session, short_code)
    if resolved is None:
        # Unknown code or no published version yet — same 404 either way so a
        # public caller can't probe which codes exist.
        raise HTTPException(status_code=404, detail="no published survey for this link")
    survey_id, version = resolved
    return ShortCodeResolved(survey_id=survey_id, version=version)


@router.get("/{survey_id}/versions/{version}", response_model=SurveyDefinitionDetail)
async def get_survey(
    survey_id: uuid.UUID, version: int, session: SessionDep
) -> SurveyDefinitionDetail:
    survey = await service.get_definition(session, survey_id, version)
    if survey is None:
        raise HTTPException(status_code=404, detail="survey version not found")
    return SurveyDefinitionDetail.model_validate(survey)


@router.post(
    "/{survey_id}/versions/{version}/responses",
    status_code=201,
    response_model=ResponseSubmitOut,
)
async def submit_response(
    survey_id: uuid.UUID, version: int, body: ResponseSubmit, session: SessionDep
) -> ResponseSubmitOut:
    try:
        response = await service.submit_response(session, survey_id, version, body)
    except service.SurveyNotFound:
        raise HTTPException(status_code=404, detail="survey version not found") from None
    except service.SubmissionRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ResponseSubmitOut.model_validate(response)
