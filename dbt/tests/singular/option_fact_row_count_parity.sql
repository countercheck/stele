-- Row-count parity (design-doc §3.7): every answered closed-ended question in
-- the raw payload yields exactly one fact_response_item row carrying an
-- option_key. int_response_answers.answered is derived 1:1 from raw payload, so
-- it is the faithful count of raw selections for the slice. A mismatch means
-- either fan-out is wrong or an answer failed to resolve to a defined option.
-- Free-text answers resolve to value_text, not option_key, so they are excluded
-- here (their routing is covered by free_text_redaction_parity).
-- Passes when it returns zero rows.

with raw_selections as (
    select count(*) as n
    from {{ ref('int_response_answers') }}
    where answered and question_type not in ('text', 'comment')
),

fact_selections as (
    select count(*) as n
    from {{ ref('fact_response_item') }}
    where option_key is not null
)

select raw_selections.n as raw_n, fact_selections.n as fact_n
from raw_selections, fact_selections
where raw_selections.n != fact_selections.n
