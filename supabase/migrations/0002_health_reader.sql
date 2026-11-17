-- Read-only role for the MCP connector (supabase/functions/health-mcp).
--
-- The Edge Function connects with the project's built-in database URL and
-- immediately drops privileges with `SET LOCAL ROLE health_reader`, so every
-- query it runs can only read the `health` schema. No login, no password.
--
-- Idempotent: safe to run more than once.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'health_reader') then
        create role health_reader nologin;
    end if;
end $$;

-- Allow the function's connection role to switch into health_reader.
grant health_reader to postgres;

grant usage on schema health to health_reader;
grant select on health.observations, health.daily_summaries, health.activities, health.import_runs
    to health_reader;
grant select on health.daily_metrics, health.weekly_metrics to health_reader;
grant execute on function health.num(jsonb) to health_reader;

-- RLS: read-only policies scoped to this role.
drop policy if exists reader_select on health.observations;
create policy reader_select on health.observations    for select to health_reader using (true);
drop policy if exists reader_select on health.daily_summaries;
create policy reader_select on health.daily_summaries for select to health_reader using (true);
drop policy if exists reader_select on health.activities;
create policy reader_select on health.activities      for select to health_reader using (true);
drop policy if exists reader_select on health.import_runs;
create policy reader_select on health.import_runs     for select to health_reader using (true);
