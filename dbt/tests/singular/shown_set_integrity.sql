-- Shown-set integrity (design-doc §3.7): every fact row marked was_shown = true
-- must have its question present in the originating submission's shown_questions.
-- Cross-checks marts back against the raw API-captured shown-set, so a regression
-- in the was_shown derivation is caught rather than trusted. Passes when it
-- returns zero rows.

with raw_shown as (
    select
        respondent_id,
        {{ surrogate_key(['survey_id', 'survey_version']) }} as survey_version_id,
        sq #>> '{}' as stable_name
    from {{ ref('stg_raw_responses') }},
        lateral jsonb_array_elements(shown_questions) as sq
),

fact_shown as (
    select
        fri.fact_id,
        fri.respondent_id,
        fri.survey_version_id,
        dq.stable_name
    from {{ ref('fact_response_item') }} as fri
    inner join {{ ref('dim_question') }} as dq
        on fri.question_id = dq.question_id
    where fri.was_shown
)

select fs.fact_id
from fact_shown as fs
left join raw_shown as rs
    on fs.respondent_id = rs.respondent_id
    and fs.survey_version_id = rs.survey_version_id
    and fs.stable_name = rs.stable_name
where rs.stable_name is null
