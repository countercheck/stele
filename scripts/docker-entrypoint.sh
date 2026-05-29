#!/usr/bin/env bash
#
# Entrypoint dispatcher for the production image (M7.2). Maps a short verb to the
# real process and exec's it, so the chosen process becomes PID 1 and receives
# signals directly (clean shutdown on SIGTERM from the orchestrator).
#
#   web              uvicorn serving the composed app (API under /api + SPA at /)
#   migrate          alembic upgrade head
#   etl              the logged dbt build (scripts/run_etl.py)
#   seed             bootstrap the initial admin operator (scripts/bootstrap_admin.py)
#   provision-worker drain the DB-credential outbox + run the role DDL (api.credential_worker)
#
# Trailing arguments pass through, e.g. `etl -- --select dim_question`.
#
# Connection/secret env (supplied by the deploy, not baked in):
#   web      STELE_DATABASE_URL (stele_api role), STELE_SESSION_SECRET, STELE_COOKIE_SECURE.
#            STELE_ANALYST_DATABASE_URL (stele_analyst role) for the marts-backed
#            survey CSV export — stele_api has no marts grant, so without it the
#            export 500s; falls back to STELE_DATABASE_URL (fine only where that
#            role can read marts, i.e. the dev/CI superuser).
#   seed     STELE_DATABASE_URL (stele_api role) + STELE_ADMIN_EMAIL/STELE_ADMIN_PASSWORD.
#            A one-off (e.g. `railway run … seed`): seeds ONLY the initial admin
#            login (idempotent), never example surveys — synthetic respondents must
#            not enter the append-only app.raw_responses of a real instance.
#   migrate  the admin identity that also bootstraps roles, on
#            STELE_ADMIN_DATABASE_URL (preferred) or STELE_DATABASE_URL — so a deploy
#            that runs migrate as a pre-deploy step in the *web* service can keep
#            STELE_DATABASE_URL=stele_api for the web process and set the admin
#            connection only on STELE_ADMIN_DATABASE_URL. Dev/CI set just
#            STELE_DATABASE_URL (one admin identity). Both bootstrap_roles.py and
#            alembic resolve this precedence, so bootstrap-er still == migrator.
#            STELE_{API,ETL,ANALYST,PII_REVIEWER}_PASSWORD on the FIRST deploy so
#            bootstrap_roles.py can create the four roles (re-deploys need none);
#            STELE_SKIP_BOOTSTRAP=1 to skip when roles are managed out of band
#   etl      DBT_HOST/DBT_USER/DBT_PASSWORD/DBT_DBNAME + STELE_ETL_DATABASE_URL;
#            GIT_SHA for run provenance (no git binary in the image — baked at build)
#
# Verb selection: the verb is the first argument, else $STELE_ENTRYPOINT, else
# `web`. A Railway service deploying this image from a registry (not a connected
# repo) can't set a railway.json start command through the OpenTofu provider, so
# the ETL cron service selects its verb with STELE_ENTRYPOINT=etl instead of a
# start-command override (see infra/railway/main.tf).
set -euo pipefail

APP_DIR="${STELE_APP_DIR:-/app}"
API_DIR="${STELE_API_DIR:-/app/api}"
PORT="${PORT:-8000}"

# Bootstrap roles, schemas, and grants as THIS (admin) identity. Idempotent and
# prod-only: skipped in the print-mode test hook (no DB) and via
# STELE_SKIP_BOOTSTRAP=1 when roles are managed out of band. Runs from the app
# root so bootstrap_roles.py finds the shared grant SQL by its repo-relative path.
# Shared by the `migrate` verb and web's migrate-on-start (below).
maybe_bootstrap() {
  if [[ -z "${STELE_ENTRYPOINT_PRINT:-}" && "${STELE_SKIP_BOOTSTRAP:-}" != "1" ]]; then
    ( cd "$APP_DIR" && python scripts/bootstrap_roles.py )
  fi
}

cmd="${1:-${STELE_ENTRYPOINT:-web}}"
if [[ $# -gt 0 ]]; then shift; fi

case "$cmd" in
  web)
    cd "$APP_DIR"
    # Run migrations in-process before serving when STELE_MIGRATE_ON_START=1. An
    # image-based Railway deploy can't use railway.json's preDeployCommand (no repo
    # checkout for config-as-code), so the web service migrates on start instead.
    # At num_replicas=1 the release-safety property holds: a failed migrate aborts
    # under `set -e` before uvicorn binds, the new container never goes healthy, and
    # Railway keeps the prior deploy serving. >1 replica would race (alembic isn't
    # concurrent) — that's the trigger for a separate migrate service (roadmap D3).
    # Skipped in print mode (no DB). bootstrap-er == migrator: both resolve the
    # admin identity from STELE_ADMIN_DATABASE_URL (preferred) or STELE_DATABASE_URL.
    if [[ -z "${STELE_ENTRYPOINT_PRINT:-}" && "${STELE_MIGRATE_ON_START:-}" == "1" ]]; then
      maybe_bootstrap
      # alembic.ini sets script_location via %(here)s (absolute), so cwd only
      # affects prepend_sys_path; cd into api/ to mirror the dev/CI invocation.
      ( cd "$API_DIR" && alembic upgrade head )
    fi
    set -- uvicorn api.main:app --host 0.0.0.0 --port "$PORT" "$@"
    ;;
  migrate)
    # Bootstrap roles/schemas/grants as the admin identity *before* migrating, so
    # the grant SQL's ALTER DEFAULT PRIVILEGES — which is grantor-specific — reaches
    # the tables alembic creates in the same run (bootstrap-er must equal migrator).
    # set -e fails the migrate closed if bootstrap fails, before any schema change.
    maybe_bootstrap
    # alembic.ini sets script_location via %(here)s (absolute), so cwd only
    # affects prepend_sys_path; cd into api/ to mirror the dev/CI invocation.
    cd "$API_DIR"
    set -- alembic upgrade head "$@"
    ;;
  etl)
    # run_etl.py reads git_sha from the repo root and sets dbt's own cwd, so it
    # must run from the app root (mirrors the CI step).
    cd "$APP_DIR"
    set -- python scripts/run_etl.py "$@"
    ;;
  seed)
    # Seed the initial admin operator from STELE_ADMIN_EMAIL/STELE_ADMIN_PASSWORD,
    # as least-privilege stele_api (STELE_DATABASE_URL) — bootstrap_admin only
    # INSERTs into app.users, which stele_api may do. Idempotent (existing admin
    # left untouched). Run from the app root so `import api` resolves like dev/CI.
    cd "$APP_DIR"
    set -- python scripts/bootstrap_admin.py "$@"
    ;;
  provision-worker)
    # Long-running privileged worker (design doc §3.10 revision): drains
    # app.provision_requests and runs the CREATE ROLE / GRANT over the elevated
    # STELE_PROVISION_DATABASE_URL — never the stele_api connection. It has no
    # inbound network surface. Encrypts delivered passwords with STELE_ENCRYPTION_KEY.
    # Runs from the app root so `import api` resolves like dev/CI.
    cd "$APP_DIR"
    set -- python -m api.credential_worker "$@"
    ;;
  *)
    echo "docker-entrypoint: unknown command '$cmd' (expected: web | migrate | etl | seed | provision-worker)" >&2
    exit 64
    ;;
esac

if [[ -n "${STELE_ENTRYPOINT_PRINT:-}" ]]; then
  # Inspection/test hook: report the resolved working dir + argv instead of
  # exec'ing. Lets the dispatcher be unit-tested without Docker or a real venv.
  printf 'cwd=%s argv=%s\n' "$(pwd)" "$*"
  exit 0
fi

exec "$@"
