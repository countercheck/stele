-- Stable question dimension: one row per stable_name across all versions. The
-- question_id is keyed on stable_name only, so it pools versions of the same
-- question under one id (per-version detail lives in dim_question_version).
--
-- parent_question_id / parent_question_rationale capture cross-version
-- equivalence. They are NEVER auto-populated (invariant 5) — that is a
-- researcher judgment — so both are emitted as explicit nulls here.

select
    {{ surrogate_key(['stable_name']) }} as question_id,
    stable_name,
    min(question_type) as question_type,
    -- Pooled PII-risk for the stable question. min() resolves to 'high' if any
    -- version is high (the safe direction); per-version truth is in
    -- int_survey_questions. Null for non-free-text / untagged questions.
    min(pii_risk) as pii_risk,
    min(published_at) as first_published_at,
    cast(null as text) as parent_question_id,
    cast(null as text) as parent_question_rationale
from {{ ref('int_survey_questions') }}
group by stable_name
