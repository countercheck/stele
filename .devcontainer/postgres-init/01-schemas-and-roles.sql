-- Stele initial database setup.
-- Idempotent; safe to re-run.

-- Database and developer role (created by post-create.sh as superuser before this runs).

\c stele

-- Schemas
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS pii;

-- Roles (per design doc §3.3)
--
-- In dev we don't enforce least-privilege strictly — the developer
-- needs to wear all hats — but we create the roles so that connection
-- strings, migrations, and dbt profiles match what will exist in prod.
-- The dev superuser inherits all of them.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_api') THEN
        CREATE ROLE stele_api LOGIN PASSWORD 'dev';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_etl') THEN
        CREATE ROLE stele_etl LOGIN PASSWORD 'dev';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_analyst') THEN
        CREATE ROLE stele_analyst LOGIN PASSWORD 'dev';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_pii_reviewer') THEN
        CREATE ROLE stele_pii_reviewer LOGIN PASSWORD 'dev';
    END IF;
END
$$;

-- Grants per design doc §3.3
-- API writes to app and pii.
GRANT USAGE, CREATE ON SCHEMA app, pii TO stele_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA app, pii
    GRANT SELECT, INSERT, UPDATE ON TABLES TO stele_api;
-- INSERTs into serial/bigserial tables call nextval(), which needs USAGE on
-- the backing sequence. Without this, least-privilege writes fail.
ALTER DEFAULT PRIVILEGES IN SCHEMA app, pii
    GRANT USAGE ON SEQUENCES TO stele_api;

-- ETL reads from app, writes to stg and marts.
GRANT USAGE ON SCHEMA app TO stele_etl;
-- ALTER DEFAULT PRIVILEGES is grantor-specific: it only covers tables created by
-- the role that runs it. app tables are created by whichever role runs the
-- migrations (stele_api in prod, stele_dev in the dev container, postgres in CI),
-- NOT by this init script's runner — so the default-privilege rule must be set
-- FOR each of those creator roles, or stele_etl silently has no SELECT on its
-- sole ETL source and dbt cannot read it. The unqualified block below covers the
-- init runner (postgres in CI); the FOR ROLE variants cover the others.
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT SELECT ON TABLES TO stele_etl;
ALTER DEFAULT PRIVILEGES FOR ROLE stele_api IN SCHEMA app
    GRANT SELECT ON TABLES TO stele_etl;
-- Catch any app tables that already exist when this (idempotent) script is re-run
-- after migrations — a no-op on a fresh build where no app tables exist yet.
GRANT SELECT ON ALL TABLES IN SCHEMA app TO stele_etl;
GRANT USAGE, CREATE ON SCHEMA stg, marts TO stele_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA stg, marts
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO stele_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA stg, marts
    GRANT USAGE ON SEQUENCES TO stele_etl;

-- Analyst reads marts only. marts tables are created by stele_etl (dbt), not by
-- this init runner, so the same FOR ROLE hardening as app→stele_etl applies.
GRANT USAGE ON SCHEMA marts TO stele_analyst;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts
    GRANT SELECT ON TABLES TO stele_analyst;
ALTER DEFAULT PRIVILEGES FOR ROLE stele_etl IN SCHEMA marts
    GRANT SELECT ON TABLES TO stele_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO stele_analyst;

-- PII reviewer reads pii.
GRANT USAGE ON SCHEMA pii TO stele_pii_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA pii
    GRANT SELECT, UPDATE ON TABLES TO stele_pii_reviewer;

-- Dev container only: migrations run as the superuser stele_dev, so app/marts
-- tables it creates need the same FOR ROLE default-privilege rule. Guarded
-- because stele_dev does not exist in CI (where postgres runs migrations and the
-- unqualified blocks above already apply). No-op anywhere stele_dev is absent.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stele_dev') THEN
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE stele_dev IN SCHEMA app '
            'GRANT SELECT ON TABLES TO stele_etl';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE stele_dev IN SCHEMA marts '
            'GRANT SELECT ON TABLES TO stele_analyst';
    END IF;
END
$$;