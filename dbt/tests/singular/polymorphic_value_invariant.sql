-- Invariant 8: a fact row carries at most one value slot. An answered row has
-- exactly one (a single-select resolves to option_key, a low-risk free-text to
-- value_text); an unanswered row (shown-skipped / routed-past) or a redacted
-- high-risk free-text row legitimately carries none. So the violation to catch
-- is MORE than one slot populated.
-- A singular test passes when it returns zero rows.

select fact_id
from {{ ref('fact_response_item') }}
where (
    case when option_key is not null then 1 else 0 end
    + case when value_numeric is not null then 1 else 0 end
    + case when value_text is not null then 1 else 0 end
    + case when value_date is not null then 1 else 0 end
) > 1
