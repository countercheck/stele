{#
  Override dbt's create_schema so it skips the DDL when the schema already
  exists. Our schemas (stg, marts) are created and owned by the init SQL; the
  stele_etl role deliberately lacks CREATE-on-database (least privilege), so the
  stock `create schema if not exists` would fail with "permission denied for
  database" even though the schema is already there. Creating only genuinely
  missing schemas keeps the grant footprint minimal.
#}
{% macro postgres__create_schema(relation) -%}
  {%- call statement('check_schema_exists', fetch_result=True) -%}
    select count(*) from pg_namespace where nspname = '{{ relation.schema }}'
  {%- endcall -%}
  {%- set schema_exists = load_result('check_schema_exists').table.columns[0].values()[0] > 0 -%}
  {%- if not schema_exists -%}
    {%- call statement('create_schema') -%}
      create schema if not exists {{ relation.without_identifier().include(database=False) }}
    {%- endcall -%}
  {%- endif -%}
{%- endmacro %}
