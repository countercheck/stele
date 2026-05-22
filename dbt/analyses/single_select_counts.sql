-- Analyst example for the M1 single-select slice. `dbt compile` renders this to
-- target/compiled/...; run the rendered SQL as the stele_analyst role against the
-- marts schema. It is an analysis, not a model — nothing is materialized.
--
-- Against the seed (scripts/seed_example_survey.py) it returns, per option:
--   q1  a 2 | b 1 | c 1        q2  x 1 | y 1
-- and the respondent count answering each question. Note how the was_shown
-- three-state distinction (answered / shown-skipped / routed-past) keeps "nobody
-- picked it" separate from "nobody was asked" — never collapsed to a single null.

with selections as (
    select
        sv.survey_id,
        sv.version,
        dq.stable_name as question,
        opt.value as option_value,
        opt.label as option_label,
        fri.respondent_id,
        fri.was_shown,
        fri.option_key
    from {{ ref('fact_response_item') }} as fri
    inner join {{ ref('dim_survey_version') }} as sv
        on fri.survey_version_id = sv.survey_version_id
    inner join {{ ref('dim_question') }} as dq
        on fri.question_id = dq.question_id
    left join {{ ref('dim_option') }} as opt
        on fri.option_key = opt.option_key
)

select
    question,
    option_label,
    count(*) filter (where option_key is not null) as selections,
    count(distinct respondent_id) filter (where option_key is not null) as respondents,
    count(*) filter (where was_shown and option_key is null) as shown_but_skipped,
    count(*) filter (where not was_shown) as routed_past
from selections
where option_value is not null
group by question, option_label
order by question, option_label
