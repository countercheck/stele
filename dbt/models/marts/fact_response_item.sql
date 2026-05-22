-- Selection-grain fact: (respondent, survey_version, question, occurrence,
-- selected_option) — invariant 7. Single-select answers resolve to one row with
-- an option_key; multi-select will fan out to several (M4).
--
-- Unanswered questions still produce a row so routing fidelity is queryable:
--   shown & answered : option_key set, was_shown = true
--   shown & skipped  : option_key null, was_shown = true
--   routed past      : option_key null, was_shown = false
--
-- value_text is intentionally absent in this slice. It arrives in M2.1 together
-- with pii_risk gating; omitting it now keeps invariant 6 (no value_text write to
-- fact_response_item without a pii_risk reference) satisfied by construction.
-- occurrence is fixed at 1 until repeating groups land (M4).

with answers as (
    select
        a.respondent_id,
        a.answer_value,
        a.was_shown,
        {{ surrogate_key(['a.survey_id', 'a.survey_version']) }} as survey_version_id,
        {{ surrogate_key(['a.stable_name']) }} as question_id,
        {{ surrogate_key(['a.survey_id', 'a.survey_version', 'a.stable_name']) }} as question_version_id
    from {{ ref('int_response_answers') }} as a
)

select
    {{ surrogate_key([
        'a.respondent_id', 'a.survey_version_id', 'a.question_id',
        '1', "coalesce(o.option_key, '')"
    ]) }} as fact_id,
    a.respondent_id,
    a.survey_version_id,
    a.question_id,
    a.question_version_id,
    1 as occurrence,
    o.option_key,
    cast(null as numeric) as value_numeric,
    cast(null as date) as value_date,
    a.was_shown,
    cast(null as int) as rank
from answers as a
left join {{ ref('dim_option') }} as o
    on a.question_version_id = o.question_version_id
    and a.answer_value = o.value
