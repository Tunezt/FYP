-- Warung Pintar — database bootstrap.
--
-- Creates the restricted `app_role` the running app connects with (DATABASE_URL).
-- This mirrors the Supabase restore sequence in docs/PROJECT-STATUS.md §5 so local and
-- production behave identically:
--
--   * `app_role` has LOGIN and NOBYPASSRLS. Row-Level Security only enforces anything
--     when the app connects as a role without BYPASSRLS — the superuser (`postgres`)
--     silently bypasses every tenant_isolation policy while every test still passes.
--   * Grants cover everything that exists now, and DEFAULT PRIVILEGES cover every table
--     and sequence that alembic (running as the superuser) creates later.
--
-- Runs as the superuser against the target database:
--   * Docker: mounted into /docker-entrypoint-initdb.d, executed once on first start.
--   * scripts/local-pg.py: executed on every `start` (the script is idempotent).
--   * Supabase: paste into the SQL editor after the first `alembic upgrade head`.
--
-- The password is a local-dev placeholder. On a hosted database, change it.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_role') then
    create role app_role login password 'app_role' nobypassrls;
  end if;
end
$$;

-- Belt and braces: even if the role pre-existed with the wrong attribute, fix it.
alter role app_role nobypassrls;

do $$
begin
  execute format('grant connect on database %I to app_role', current_database());
end
$$;

grant usage on schema public to app_role;
grant select, insert, update, delete on all tables in schema public to app_role;
grant usage, select on all sequences in schema public to app_role;

-- Applies to objects the *current* role (the superuser running this file, which is
-- also the role alembic uses) creates from now on — i.e. every migration's tables.
alter default privileges in schema public
  grant select, insert, update, delete on tables to app_role;
alter default privileges in schema public
  grant usage, select on sequences to app_role;
