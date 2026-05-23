-- Selection-grain fact: (respondent, survey_version, question, occurrence,
-- selected_option) — invariant 7. Single-select answers resolve to one row with
-- an option_key; multi-select will fan out to several (M4).
--
-- Unanswered questions still produce a row so routing fidelity is queryable:
--   shown & answered : option_key set, was_shown = true
--   shown & skipped  : option_key null, was_shown = true
--   routed past      : option_key null, was_shown = false
--
-- value_text carries free-text answers, but ONLY for pii_risk='low' questions
-- (invariant 6). High-risk free text is redacted here (value_text null,
-- value_text_redacted true) and lives in pii.free_text_responses for the
-- reviewer; the default is high, so the safe path is the default (design-doc
-- §3.9). The value_text CASE references pii_risk inline so the invariant-6 lint
-- binds the guard to this statement. occurrence is fixed at 1 until repeating
-- groups land (M4).

with answers as (
    select
        a.respondent_id,
        a.answer_value,
        a.was_shown,
        a.question_type,
        a.pii_risk,
        {{ surrogate_key(['a.survey_id', 'a.survey_version']) }} as survey_version_id,
        {{ surrogate_key(['a.stable_name']) }} as question_id,
        {{ surrogate_key(['a.survey_id', 'a.survey_version', 'a.stable_name']) }} as question_version_id
    from {{ ref('int_response_answers') }} as a
),

-- CTE name carries the `fact_response_item` token so the invariant-6 lint (which
-- scans for a value_text-writing statement referencing both fact_response_item
-- and pii_risk) binds to the gated SELECT below.
fact_response_item_rows as (
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
        case
            when a.question_type in ('text', 'comment') and a.pii_risk = 'low'
                then a.answer_value
        end as value_text,
        case
            when a.question_type in ('text', 'comment') and coalesce(a.pii_risk, 'high') = 'high'
                then true
            else false
        end as value_text_redacted,
        a.was_shown,
        cast(null as int) as rank
    from answers as a
    left join {{ ref('dim_option') }} as o
        on a.question_version_id = o.question_version_id
        and a.answer_value = o.value
)

select * from fact_response_item_rows
