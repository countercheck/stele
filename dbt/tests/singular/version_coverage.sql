-- Version coverage (design-doc §3.7): every (respondent, survey_version) present
-- in raw_responses has at least one fact_response_item row. Catches submissions
-- silently dropped between raw and marts. Passes when it returns zero rows.

with raw_pairs as (
    select distinct
        respondent_id,
        {{ surrogate_key(['survey_id', 'survey_version']) }} as survey_version_id
    from {{ ref('stg_raw_responses') }}
),

fact_pairs as (
    select distinct respondent_id, survey_version_id
    from {{ ref('fact_response_item') }}
)

select rp.respondent_id, rp.survey_version_id
from raw_pairs as rp
left join fact_pairs as fp
    on rp.respondent_id = fp.respondent_id
    and rp.survey_version_id = fp.survey_version_id
where fp.respondent_id is null
