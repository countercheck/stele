-- One row per choice per question per version. SurveyJS choices are either plain
-- scalars ("a") or objects ({"value": "a", "text": "Apple"}); normalize both to a
-- value + label. `#>> '{}'` extracts a scalar jsonb element as unquoted text.

with options as (
    select
        e.survey_id,
        e.survey_version,
        e.stable_name,
        choice.value as choice,
        choice.ordinality::int as display_order
    from {{ ref('int_survey_elements') }} as e
    left join lateral jsonb_array_elements(
        case
            when jsonb_typeof(e.element -> 'choices') = 'array' then e.element -> 'choices'
            else '[]'::jsonb
        end
    ) with ordinality as choice(value, ordinality) on true
)

select
    survey_id,
    survey_version,
    stable_name,
    case
        when jsonb_typeof(choice) = 'object' then choice ->> 'value'
        else choice #>> '{}'
    end as value,
    case
        when jsonb_typeof(choice) = 'object' then coalesce(choice ->> 'text', choice ->> 'value')
        else choice #>> '{}'
    end as label,
    display_order
from options
