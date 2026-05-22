# Copilot review instructions

Survey engine + warehouse. `CLAUDE.md` is the operational reference and
`survey-engine-design-doc.md` is authoritative for architecture. When reviewing
a pull request, check the diff against the load-bearing invariants below and
flag concerns rather than rubber-stamping.

## Review focus

- **Append-only `raw_responses`.** It is the sole ETL source. dbt reads it only,
  never the normalized `app.responses` / `app.response_items` read-model. No
  `UPDATE`/`DELETE` on `raw_responses` outside the tombstone workflow.
- **Publish-time immutability.** Published surveys never change in place: edits
  become a new draft → new version → new hash. Every response must carry the
  hash it was answered against.
- **Shown-set capture at submit time.** The shown-set comes from the SurveyJS
  engine at submit. Never reconstruct routing by re-evaluating `visibleIf` in
  SQL. Don't collapse `was_shown = false` and `was_shown = true, value null`.
- **PII defaults high.** Free-text defaults to `pii_risk = 'high'`;
  `value_text` is populated only for explicit `low`. Downgrades are deliberate
  decisions, never inferred from a prompt "looking innocuous."
- **No silent defaults for methodological judgments.** Cross-version
  equivalence (`parent_question_id` / `parent_question_rationale`) is a
  researcher's call — never auto-populated from a heuristic. The two fields must
  be written together.
- **Fact grain & polymorphic value.** Fact grain is
  `(respondent, survey_version, question_id, occurrence, selected_option)`;
  `fact_response` is a separate respondent-question grain. Exactly one of
  `{option_key, value_numeric, value_text, value_date}` per fact row.
- **One JSON parser, two consumers.** API → operational read-model;
  dbt → warehouse. Both derive from `raw_responses`. Never chain them.

## Conventions

- Python 3.12 (uv), FastAPI, psycopg3, SQLAlchemy 2, Alembic, Postgres 16,
  dbt-postgres, SurveyJS. Lint with Ruff, types with mypy.
- New behavior needs tests; bug fixes need a regression test. dbt invariant
  tests (row-count parity, polymorphic value, shown-set integrity, version
  coverage, parent-question integrity) must stay green.
- Keep dbt SQL portable between Postgres and DuckDB; use `{{ adapter.dispatch }}`
  rather than unguarded Postgres-specific SQL.
- A new question type means work in three places: runtime, publish test, and
  dbt staging.
