-- One row per closed-ended choice, scoped to its question version. option_key is
-- what fact_response_item resolves a single-select answer to.

select
    {{ surrogate_key(['survey_id', 'survey_version', 'stable_name', 'value']) }} as option_key,
    {{ surrogate_key(['survey_id', 'survey_version', 'stable_name']) }} as question_version_id,
    value,
    label,
    display_order
from {{ ref('int_survey_options') }}
