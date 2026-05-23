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
    -- pii_risk tags free-text questions (design-doc §3.9). Read from the snapshot
    -- exactly like question_type; null for untagged / non-free-text questions.
    -- The fact gates value_text on this; the API defaults absent → 'high'.
    element ->> 'pii_risk' as pii_risk,
    coalesce(element ->> 'title', stable_name) as prompt_text,
    display_order
from {{ ref('int_survey_elements') }}
