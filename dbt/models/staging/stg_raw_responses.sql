-- Staging: 1:1 over app.raw_responses, the sole ETL source (invariant 1/4).
-- Each row's definition_snapshot embeds the published definition the response was
-- answered against, so every dimension is reproducible from raw alone (NFR-1).
--
-- Postgres-specific JSON access (jsonb operators, jsonb_exists) is used here and
-- in the intermediate models. DuckDB portability is a deferred decision
-- (design-doc §5); the JSON handling is deliberately confined to staging +
-- intermediate so a future port is localized.

with source as (
    select * from {{ source('app', 'raw_responses') }}
)

select
    id as raw_response_id,
    respondent_id,
    survey_id,
    survey_version,
    submitted_at,
    payload,
    shown_questions,
    definition_snapshot -> 'definition' as definition,
    definition_snapshot ->> 'definition_hash' as definition_hash,
    (definition_snapshot ->> 'published_at')::timestamptz as published_at
from source
-- Tombstoned rows (M2 withdrawal) null the snapshot; they carry no answers to
-- model. Excluding them keeps the warehouse erasure-aware from the start.
where definition_snapshot is not null
