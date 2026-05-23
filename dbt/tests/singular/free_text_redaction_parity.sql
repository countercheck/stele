-- Free-text redaction integrity (invariant 6, design-doc §3.9): cross-check the
-- fact's value_text / value_text_redacted against the question's pii_risk in the
-- snapshot. The gating lives in fact_response_item; this test re-derives the
-- expectation from int_response_answers so a regression in that gating is caught
-- rather than trusted. Effective risk defaults absent → 'high' (the safe path).
-- A row fails when:
--   - high-risk free text isn't redacted, or leaked a value_text;
--   - low-risk free text is flagged redacted, or (when answered) value_text does
--     not match the raw answer;
--   - a non-free-text row carries value_text or is flagged redacted.
-- Passes when it returns zero rows.

with answers as (
    select
        respondent_id,
        {{ surrogate_key(['survey_id', 'survey_version']) }} as survey_version_id,
        {{ surrogate_key(['stable_name']) }} as question_id,
        question_type,
        coalesce(pii_risk, 'high') as effective_risk,
        answered,
        answer_value
    from {{ ref('int_response_answers') }}
),

joined as (
    select
        fri.fact_id,
        fri.value_text,
        fri.value_text_redacted,
        a.question_type,
        a.effective_risk,
        a.answered,
        a.answer_value
    from {{ ref('fact_response_item') }} as fri
    inner join answers as a
        on fri.respondent_id = a.respondent_id
        and fri.survey_version_id = a.survey_version_id
        and fri.question_id = a.question_id
)

select fact_id
from joined
where
    (
        question_type in ('text', 'comment')
        and effective_risk = 'high'
        and (value_text is not null or value_text_redacted = false)
    )
    or (
        question_type in ('text', 'comment')
        and effective_risk = 'low'
        and (
            value_text_redacted = true
            or (answered and value_text is distinct from answer_value)
        )
    )
    or (
        question_type not in ('text', 'comment')
        and (value_text is not null or value_text_redacted = true)
    )
