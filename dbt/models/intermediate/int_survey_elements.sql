-- Shared unnest of the embedded definition snapshot into one row per question
-- element, carrying the raw element jsonb so both int_survey_questions (metadata)
-- and int_survey_options (choices) build on a single parse. Postgres-specific
-- JSON handling, confined to intermediate by design (see stg_raw_responses).

with definitions as (
    -- Snapshots are identical across every response to a published version, so
    -- collapse to one row per version before unnesting.
    select distinct
        survey_id,
        survey_version,
        definition_hash,
        published_at,
        definition
    from {{ ref('stg_raw_responses') }}
),

pages as (
    -- SurveyJS allows either pages[].elements[] or top-level elements[]; wrap the
    -- latter in a synthetic single page so both shapes unnest uniformly.
    select
        survey_id,
        survey_version,
        definition_hash,
        published_at,
        coalesce(
            definition -> 'pages',
            jsonb_build_array(jsonb_build_object('elements', definition -> 'elements'))
        ) as pages
    from definitions
)

select
    p.survey_id,
    p.survey_version,
    p.definition_hash,
    p.published_at,
    elem.value ->> 'name' as stable_name,
    elem.value as element,
    elem.ordinality::int as display_order
from pages as p,
    lateral jsonb_array_elements(p.pages) as page(value),
    lateral jsonb_array_elements(page.value -> 'elements')
        with ordinality as elem(value, ordinality)
where elem.value ->> 'name' is not null
