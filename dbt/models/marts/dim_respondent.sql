-- Respondent dimension. pseudonym is identifying data and lives in the pii
-- schema (not in raw_responses); it stays null here until a pii promotion path
-- exists (out of scope for the slice).

select
    respondent_id,
    cast(null as text) as pseudonym,
    min(submitted_at) as first_seen_at
from {{ ref('stg_raw_responses') }}
group by respondent_id
