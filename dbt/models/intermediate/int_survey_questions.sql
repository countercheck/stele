-- One row per question per survey version, from the embedded definition snapshot.
-- Feeds dim_question / dim_question_version and the question set that
-- int_response_answers fans every submission across.

select
    survey_id,
    survey_version,
    definition_hash,
    published_at,
    stable_name,
    element ->> 'type' as question_type,
    coalesce(element ->> 'title', stable_name) as prompt_text,
    display_order
from {{ ref('int_survey_elements') }}
