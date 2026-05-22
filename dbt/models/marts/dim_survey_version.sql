-- One row per published survey version answered. definition_hash + published_at
-- come from the embedded snapshot, so the dimension is reproducible from raw.

select distinct
    {{ surrogate_key(['survey_id', 'survey_version']) }} as survey_version_id,
    survey_id,
    survey_version as version,
    definition_hash,
    published_at
from {{ ref('stg_raw_responses') }}
