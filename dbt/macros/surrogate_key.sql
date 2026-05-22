{#
  Deterministic surrogate key from a list of natural-key expressions.

  Used instead of bigint sequence PKs (design-doc nominal type) so keys are
  stable across full-refresh rebuilds and computable independently in each model
  — there is no shared sequence to coordinate. md5 + cast + coalesce + || are all
  portable to DuckDB, so this is dependency-free (no dbt_utils) and keeps the
  future-port surface small. Documented as a deviation in _marts.yml.

  Each component is cast to text and null-coalesced so a null can never collapse
  the whole key to null (which would break the not_null PK tests).
#}
{% macro surrogate_key(fields) %}
md5(
    {%- for field in fields %}
    coalesce(cast({{ field }} as text), '')
    {%- if not loop.last %} || '|' || {%- endif %}
    {%- endfor %}
)
{%- endmacro %}
