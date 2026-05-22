-- One row per (survey version, question): the specific rendering, with prompt
-- text and response type. Joins to dim_question via question_id.

select distinct
    {{ surrogate_key(['survey_id', 'survey_version', 'stable_name']) }} as question_version_id,
    {{ surrogate_key(['stable_name']) }} as question_id,
    prompt_text,
    question_type as response_type
from {{ ref('int_survey_questions') }}
