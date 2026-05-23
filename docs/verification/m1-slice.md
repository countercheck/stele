# M1 vertical slice — end-to-end verification (NFR-1)

Proves the architectural spine end-to-end for the single-select question type:
author → publish → submit → ETL → analyst query, and that **marts are fully
reproducible from `app.raw_responses` alone** (NFR-1, design-doc §3.7). dbt reads
only `raw_responses`; the embedded `definition_snapshot` is what makes the rich
dimensions reproducible without reading `app.survey_definitions` (invariant 1/4).

Run from the repo root inside the dev container.

## 1. Wipe and rebuild from scratch

```bash
# From the HOST (wipes the Postgres volume — the real test of rebuild-from-raw):
docker compose -f .devcontainer/docker-compose.yml down -v
# Rebuild/reopen the dev container, then in the container:
uv sync
cd api && uv run alembic upgrade head && cd ..
```

The container's init applies `.devcontainer/postgres-init/01-schemas-and-roles.sql`
(schemas, roles, grants). `alembic upgrade head` creates the `app` tables —
including `raw_responses.definition_snapshot`.

## 2. Seed responses (real write path)

```bash
uv run python scripts/seed_example_survey.py
```

Publishes the example survey (two single-select questions plus two free-text
questions — see `scripts/seed_example_survey.py`) and submits 4 responses
spanning all three routing states (answered / shown-skipped / routed-past) via
the actual `api.survey_engine.service`, so each `raw_responses` row carries its
`definition_snapshot`. The single-select slice is verified below; the free-text
PII routing is verified in `m2-pii-slice.md`.

## 3. Build the warehouse (as the real ETL role)

```bash
cd dbt
DBT_HOST=localhost DBT_USER=stele_etl DBT_PASSWORD=dev DBT_DBNAME=stele \
  uv run dbt build --profiles-dir .
```

Expect `PASS=42 ... ERROR=0`. The custom invariant tests
(`option_fact_row_count_parity`, `polymorphic_value_invariant`,
`shown_set_integrity`, `version_coverage`, `free_text_redaction_parity`) must be
green. Running as `stele_etl` (not the dev superuser) is deliberate — it
exercises the real grants.

## 4. Analyst query (as the analyst role)

```bash
cd dbt && uv run dbt compile --profiles-dir . --select single_select_counts
# then run the rendered SQL as the analyst:
PGPASSWORD=dev psql -h localhost -U stele_analyst -d stele \
  -f target/compiled/survey_engine/analyses/single_select_counts.sql
```

Expected per-option selections:

| question | option | selections | respondents |
|---|---|---|---|
| q1 | a | 2 | 2 |
| q1 | b | 1 | 1 |
| q1 | c | 1 | 1 |
| q2 | x | 1 | 1 |
| q2 | y | 1 | 1 |

`q2` additionally shows 1 shown-but-skipped and 1 routed-past — distinct, not
collapsed to "missing".

## 5. Reproducibility (NFR-1)

```bash
cd dbt && DBT_HOST=localhost DBT_USER=stele_etl DBT_PASSWORD=dev DBT_DBNAME=stele \
  uv run dbt build --profiles-dir . --full-refresh
```

A second full-refresh build from the unchanged `raw_responses` reproduces
identical marts (same row counts, same surrogate keys — keys are deterministic
md5 hashes, see `macros/surrogate_key.sql`). No operational `app.responses` /
`app.response_items` table is ever read.
