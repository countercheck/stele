# CLAUDE.md

Survey engine + warehouse. This file is the operational shortcut; `survey-engine-design-doc.md` is the authoritative source for architectural questions ("should I add X", "is this the right grain", "what's the migration path"). When uncertain, read the design doc first.

## Stack

Python 3.12 (uv) · FastAPI · psycopg3 · SQLAlchemy 2 · Alembic · Postgres 16 · dbt-postgres · SurveyJS (Vite) · Ruff · mypy · pytest.

DB: `stele` on `localhost:5432`. Dev runs in a VS Code dev container (Postgres + Python + Node in one image).

## Layout

```
api/          FastAPI survey API
dbt/          dbt project
frontend/     SurveyJS frontend
patterns/     Annotated SurveyJS examples (LLM context)
.devcontainer/
```

## Commands

```bash
uv sync                            # deps
uv run pytest                      # tests
uv run ruff check . && uv run ruff format .
uv run mypy api/
uv run uvicorn api.main:app --reload --port 8000
cd frontend && npm run dev         # :5173
cd dbt && dbt build                # ETL = full-refresh dbt build
psql -d stele                      # schemas: app, stg, marts, pii

# Nuke + rebuild from raw_responses (from host):
docker compose -f .devcontainer/docker-compose.yml down -v
```

## Schemas

| Schema | Writer | Reader | Contents |
|---|---|---|---|
| `app` | `stele_api` | `stele_etl` | Operational, transactional |
| `stg` | `stele_etl` | `stele_etl` | dbt staging |
| `marts` | `stele_etl` | `stele_analyst` | Star schema |
| `pii` | `stele_api` | `stele_pii_reviewer` | Identifying data |

Role/grant plumbing: `.devcontainer/postgres-init/01-roles.sql` (dev/CI role creation) + `02-schemas-and-grants.sql` (schemas/grants, shared verbatim with prod via `scripts/bootstrap_roles.py`); both idempotent. Table changes: Alembic.

Analyst/reviewer DB credentials are minted out of band of `stele_api` (design doc §3.10): the API only enqueues into `app.provision_requests`; the privileged `api.credential_worker` (elevated `STELE_PROVISION_DATABASE_URL`, the one connection with `CREATEROLE`) drains the outbox, runs the `CREATE ROLE`/`GRANT`, and writes the one-time Fernet-encrypted password to `app.secret_deliveries`, which the recipient reveals exactly once from their own session. Shared DDL: `api.auth.provisioning` (also used by the `scripts/provision_db_credential.py` break-glass CLI).

## Invariants — don't break

1. `app.raw_responses` is append-only and the sole ETL source. dbt reads it only, never the normalized `app.responses` / `app.response_items` read-model.
2. Published surveys are immutable. Edits → new draft → new version → new hash. Every response carries the hash it was answered against.
3. Shown-set AND render order are captured at submit time from the SurveyJS engine. `shown_questions` is in render order; `shown_choice_orders` carries the rendered choice order for any question whose options were randomized. Never reconstruct routing or display order by re-evaluating the definition in SQL. (Design doc §3.5 "Randomization and display-order capture", §4.11.)
4. One JSON parser. API → operational read-model. dbt → warehouse. Both from `raw_responses`. Never chain them. *Enforced by `scripts/check_invariants.py`, which scans dbt SQL for forbidden table references and verifies `sources.yml` declares only `raw_responses` under the `app` schema. Pre-commit runs it; CI verifies.*
5. No cross-version pooling by default. Opt-in via `dim_question.parent_question_id` + `parent_question_rationale`. Never auto-populate either. `construct_block` / `construct_item` are **provenance, not a pooling key** — they surface "this is a PHQ-9 item" but never license `GROUP BY construct_block` across surveys; pooling stays the `parent_question_id` opt-in. *Lint checks that writes to `parent_question_id` co-occur with `parent_question_rationale`; `construct_pair_integrity` enforces item⇒block; `construct_tag_stability` rejects within-survey tag drift across versions.*
6. Free-text defaults to `pii_risk = 'high'`. `marts.fact_response_item.value_text` populated only for explicit `low`. *Lint checks that `value_text` writes to `fact_response_item` reference `pii_risk`.*
7. Fact grain: `(respondent, survey_version, question_id, occurrence, selected_option)`. `fact_response` (respondent-question grain) is a separate table. Don't conflate.
8. Exactly one of `{option_key, value_numeric, value_text, value_date}` per fact row. dbt test enforces; don't skip.

## After every change

1. **Write tests** covering the change before considering it done. New behavior → new test. Bug fix → regression test first, then fix.
2. **Lint** (`uv run ruff check . && uv run ruff format .`) and fix anything it flags. Also run `python3 scripts/check_invariants.py` to enforce the load-bearing invariants. Both run automatically via pre-commit if installed (`pre-commit install`).
3. **Type-check** (`uv run mypy api/`) for Python changes.
4. **Run tests** (`uv run pytest`; `cd dbt && dbt build` for dbt changes). All green before moving on.
5. **Run `/code-review`** (engineering plugin) on the diff. If it surfaces concerns, stop and discuss — don't silently override.

Order matters: lint/format first so tests run against the code that'll be committed, then tests, then review. Don't skip the test-writing step because "it's a small change" — small changes are where regressions hide.

**Where tests live:**

- API: `api/tests/`, pytest + `httpx.AsyncClient` against a transactional fixture (rollback per test).
- dbt: built-in `tests:` blocks on models for relationships/uniqueness; `dbt/tests/singular/` for the custom invariants (row-count parity, polymorphic value, shown-set integrity, etc.).
- Frontend: colocated `*.test.ts` next to the module under test.

## Git workflow

Work on branches; never commit directly to `main`. One branch per story/unit of work, branched off `main`:

- Branch name: `m<milestone>.<story>-<slug>` for plan stories (e.g. `m1.4-frontend-render-submit`); `chore/<slug>` or `fix/<slug>` otherwise.
- Push the branch and open a PR into `main`. CI (`.github/workflows/ci.yml`) must pass; the human reviewer merges.
- Keep `main` green and releasable at all times.
- Don't force-push `main` or amend published commits; create new commits instead.
- After any rebase / cherry-pick / `--abort` gymnastics, verify the *remote* with `git ls-remote origin <branch>` and `gh pr view <n> --json headRefOid` before claiming a push landed. A local "[new branch]" or "Everything up-to-date" message and the `origin/<branch>` tracking ref can lie: an interrupted rebase can leave you on a detached HEAD while the branch ref (and every push) stays pinned to the pre-rebase commit. Trust the server, not local refs.

## Don't add silent defaults for methodological judgments

Four judgments stay explicit, always. Examples of what going-wrong-silently looks like:

- **Cross-version equivalence.** Don't auto-fill `parent_question_rationale` from prompt similarity between v1 and v2. That's a researcher's call, not a heuristic. Same for `construct_block` ⇒ `parent_question_id`: a shared scale tag is provenance, not equivalence — don't promote it.
- **Free-text safety.** Don't downgrade a question from `pii_risk = 'high'` to `'low'` because the prompt "looks innocuous." The default is `'high'`; downgrades are deliberate decisions at definition time.
- **Shown vs skipped vs routed-past.** Don't collapse `was_shown = false` and `was_shown = true, value null` into "missing." They mean different things; analyses depend on the distinction.
- **Display order.** Don't render randomized questions/choices without writing the realized order back to `raw_responses` (in `shown_questions` order + `shown_choice_orders`). Don't reconstruct `display_order` in SQL from the published definition — the definition holds the rule, not the per-respondent realization. Treat order capture as part of the submission contract, the same way the shown-set is.

If you're writing a default for any of these, that's the bug.

## Publish gate (in order)

Schema validation → lint (dup names, dangling `visibleIf`, dup option values, missing matrix row ids) → round-trip test (synthetic respondents, all branches) → hash + freeze.

New question type = work in three places: runtime, publish test, dbt staging.

Within-page (`Page.questionsOrder`), within-matrix (`Matrix.rowsOrder`), and within-question (`SelectBase.choicesOrder`) randomization are unconstrained — values are stable across re-orderings, and `visibleIf` reads values, not positions. Static-panel and survey-level `pagesOrder` shuffling aren't wired today (static panel is outside the accepted type surface; `pagesOrder` isn't native SurveyJS) — deferred per design doc §5. The page-order extension lands with its own publish-gate lint (reject cross-page `visibleIf` under page randomization).

## dbt

- Staging = views; intermediate + marts = tables; no materialized views.
- Full-refresh on demand. No incremental until rebuild > a few minutes.
- Keep SQL portable between Postgres and DuckDB. Use `{{ adapter.dispatch }}` when you can't.
- Custom tests that must stay green: option/fact row-count parity, version coverage, polymorphic value invariant, parent-question integrity, shown-set integrity.

## Don't without asking

- Modify `survey-engine-*.md`. Treat the design docs as read-only reference; propose edits in chat, don't sync them to code changes.
- Modify role/grant SQL in `.devcontainer/postgres-init/{01-roles,02-schemas-and-grants}.sql` (or the prod runner `scripts/bootstrap_roles.py` that applies the latter). Grant changes are silent until they bite under a non-superuser role — the `prod-bootstrap-sim` CI job rehearses that path.
- Give `stele_api` `CREATEROLE`, or put credential role-DDL in the request path. Role-minting lives ONLY in `api.credential_worker` / the `scripts/provision_db_credential.py` CLI over `STELE_PROVISION_DATABASE_URL`; the API just enqueues into `app.provision_requests`. And `stele_analyst`/`stele_pii_reviewer` must never accumulate grants beyond their one schema (`marts`/`pii`) — that boundary is what keeps a leaked analyst credential low-stakes.
- Make dbt read normalized `app.*` tables.
- `UPDATE`/`DELETE` `raw_responses` outside the tombstone workflow.
- Auto-populate `parent_question_id` from any heuristic — including a shared `construct_block` (provenance, not equivalence; design doc §4.10).
- Reconstruct per-respondent display order in SQL from the published definition. The definition holds the rule (`questionsOrder: 'random'`, etc.); the per-respondent realization is captured at submit time and read straight through to `fact_response_item.display_order` (design doc §4.11).
- Add a new runtime component (broker, search, separate analytics DB) — check design doc § 5 triggers first.
- Postgres-specific SQL in dbt without dispatch or a documented reason.
- `git rm` survey definitions or response data. Use the database.

## Dev container notes

- Likely running Claude Code with `--dangerously-skip-permissions`. Container = sandbox.
- Postgres data on a named volume. `down -v` wipes it — use it to verify rebuild-from-raw actually works.
- Claude auth + bash history on separate volumes, survive rebuilds.
- CI fails / local passes → suspect role grants first. Dev superuser hides grant bugs.

## When stuck

Design rationale: `survey-engine-design-doc.md`. Methodological view: `survey-engine-for-researcher.md`. Rejected alternatives: design doc § 4. Load-bearing assumptions: § 2.4 (A-1..A-5).
