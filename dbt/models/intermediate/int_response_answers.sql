-- The routing-fidelity core: fan every submission across the full question set of
-- the version it answered (from the snapshot), then attach the answer and the
-- shown-set. This yields the three states distinctly — never collapsed to
-- "missing" (design-doc §3.5, CLAUDE.md "silent defaults"):
--   shown & answered : was_shown = true,  answered = true
--   shown & skipped  : was_shown = true,  answered = false
--   routed past      : was_shown = false, answered = false
-- was_shown comes straight from the API-captured shown_questions (invariant 3);
-- routing is never reconstructed from visibleIf in SQL.

with responses as (
    select
        raw_response_id,
        respondent_id,
        survey_id,
        survey_version,
        submitted_at,
        payload,
        shown_questions
    from {{ ref('stg_raw_responses') }}
),

questions as (
    select survey_id, survey_version, stable_name, question_type, pii_risk
    from {{ ref('int_survey_questions') }}
)

select
    r.raw_response_id,
    r.respondent_id,
    r.survey_id,
    r.survey_version,
    r.submitted_at,
    q.stable_name,
    -- question_type and pii_risk ride along so fact_response_item can gate
    -- value_text on them (free-text + pii_risk='low') without re-parsing.
    q.question_type,
    q.pii_risk,
    coalesce(jsonb_exists(r.shown_questions, q.stable_name), false) as was_shown,
    coalesce(jsonb_exists(r.payload, q.stable_name), false) as answered,
    r.payload ->> q.stable_name as answer_value
from responses as r
inner join questions as q
    on r.survey_id = q.survey_id
    and r.survey_version = q.survey_version
