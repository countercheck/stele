{#
  Use the configured +schema name verbatim (stg, marts) instead of dbt's default
  "<target.schema>_<custom>" concatenation, which would produce marts_stg /
  marts_marts. Our schemas are fixed by the init SQL and the grant model
  (design-doc §3.3), so models must land in exactly stg and marts. Models without
  an explicit +schema fall back to the profile's target schema.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
