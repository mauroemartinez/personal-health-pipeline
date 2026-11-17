-- Personal health data: private, self-contained schema.
--
-- Only creates/alters objects inside schema `health` plus the `health_writer`
-- role; never touches `public` or any other schema. Security model:
--   * Schema `health` must NOT be added to "Exposed schemas" in the Supabase
--     Data API settings -> no REST/GraphQL access.
--   * anon / authenticated get no privileges at all. RLS is enabled on every
--     table and the only policies target the `health_writer` role, so API
--     roles are denied even if the schema were exposed by mistake.
--   * Loading happens over a direct Postgres connection
--     (scripts/load_supabase.py) with a least-privilege login role, never
--     from client-side code.
--
-- Idempotent: safe to run more than once.

create schema if not exists health;

revoke all on schema health from public;
revoke all on schema health from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Tables (ids are deterministic sha256-based keys produced by the importer)
-- ---------------------------------------------------------------------------

create table if not exists health.observations (
    id                text primary key,
    source            text        not null default 'mi_fitness',
    metric            text        not null,
    measured_at       timestamptz not null,
    local_date        date        not null,
    value             numeric,
    unit              text,
    payload           jsonb       not null default '{}'::jsonb,
    source_updated_at timestamptz,
    loaded_at         timestamptz not null default now(),
    constraint observations_natural_key unique (source, metric, measured_at)
);
create index if not exists observations_metric_date_idx on health.observations (metric, local_date);

create table if not exists health.daily_summaries (
    id                text primary key,
    source            text        not null default 'mi_fitness',
    metric            text        not null,
    local_date        date        not null,
    payload           jsonb       not null,
    source_updated_at timestamptz,
    loaded_at         timestamptz not null default now(),
    constraint daily_summaries_natural_key unique (source, metric, local_date)
);

create table if not exists health.activities (
    id                text primary key,
    source            text        not null default 'mi_fitness',
    sport             text        not null,
    category          text,
    started_at        timestamptz not null,
    ended_at          timestamptz,
    local_date        date        not null,
    duration_s        numeric,
    valid_duration_s  numeric,
    distance_m        numeric,
    calories_kcal     numeric,
    steps             numeric,
    avg_hr            numeric,
    max_hr            numeric,
    min_hr            numeric,
    train_load        numeric,
    train_effect      numeric,
    vo2_max           numeric,
    -- Manual enrichment the band cannot measure (e.g. treadmill incline).
    -- The loader never overwrites these two columns.
    incline_pct       numeric check (incline_pct between -10 and 40),
    notes             text,
    payload           jsonb       not null default '{}'::jsonb,
    source_updated_at timestamptz,
    loaded_at         timestamptz not null default now(),
    constraint activities_natural_key unique (source, sport, started_at)
);
create index if not exists activities_date_idx on health.activities (local_date);

create table if not exists health.import_runs (
    id          bigint generated always as identity primary key,
    source      text        not null,
    started_at  timestamptz not null default now(),
    counts      jsonb       not null,
    skipped     jsonb       not null default '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- Helper: numeric value of a JSON field, NULL if it is not a JSON number
-- (keeps the views from failing on a stray string/boolean).
-- ---------------------------------------------------------------------------

create or replace function health.num(v jsonb)
returns numeric
language sql
immutable
set search_path = ''
as $$
    select case when jsonb_typeof(v) = 'number' then (v #>> '{}')::numeric end
$$;

revoke all on function health.num(jsonb) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Analytical views (security_invoker: run with the caller's privileges).
-- Dropped and recreated so column changes apply on re-run (views hold no data).
-- ---------------------------------------------------------------------------

drop view if exists health.weekly_metrics;
drop view if exists health.daily_metrics;

create view health.daily_metrics
with (security_invoker = true) as
with s as (
    select local_date,
           max(health.num(payload->'steps'))               filter (where metric = 'steps')        as steps,
           max(health.num(payload->'distance'))            filter (where metric = 'steps') / 1000 as distance_km,
           max(health.num(payload->'calories'))            filter (where metric = 'calories')     as kcal,
           max(health.num(payload->'duration'))            filter (where metric = 'intensity')    as active_min,
           max(health.num(payload->'count'))               filter (where metric = 'valid_stand')  as stand_hours,
           max(health.num(payload->'avg_hr'))              filter (where metric = 'heart_rate')   as avg_hr,
           max(health.num(payload->'avg_rhr'))             filter (where metric = 'heart_rate')   as rhr,
           max(health.num(payload->'avg_stress'))          filter (where metric = 'stress')       as avg_stress,
           max(health.num(payload->'total_duration'))      filter (where metric = 'sleep') / 60   as sleep_h,
           max(health.num(payload->'sleep_deep_duration')) filter (where metric = 'sleep') / 60   as deep_h,
           max(health.num(payload->'sleep_rem_duration'))  filter (where metric = 'sleep') / 60   as rem_h,
           max(health.num(payload->'sleep_score'))         filter (where metric = 'sleep')        as sleep_score
    from health.daily_summaries
    where metric in ('steps', 'calories', 'intensity', 'valid_stand', 'heart_rate', 'stress', 'sleep')
    group by local_date
),
o as (
    -- Resting HR fallback and last weight of the day, from point observations.
    select local_date,
           avg(value) filter (where metric = 'resting_heart_rate') as rhr_obs,
           (array_agg(value order by measured_at desc) filter (where metric = 'weight'))[1] as weight_kg
    from health.observations
    where metric in ('resting_heart_rate', 'weight')
    group by local_date
),
a as (
    select local_date, count(*) as workouts, sum(duration_s) / 60 as workout_min
    from health.activities
    group by local_date
)
select coalesce(s.local_date, o.local_date, a.local_date) as local_date,
       s.steps, s.distance_km, s.kcal, s.active_min, s.stand_hours,
       s.avg_hr, coalesce(s.rhr, o.rhr_obs) as rhr, s.avg_stress,
       s.sleep_h, s.deep_h, s.rem_h, s.sleep_score,
       o.weight_kg,
       coalesce(a.workouts, 0) as workouts,
       coalesce(a.workout_min, 0) as workout_min
from s
full join o using (local_date)
full join a using (local_date);

create view health.weekly_metrics
with (security_invoker = true) as
select to_char(local_date, 'IYYY-"W"IW')       as iso_week,
       date_trunc('week', local_date)::date     as week_start,
       count(*)                                 as days_with_data,
       round(avg(steps))                        as avg_steps,
       round(avg(distance_km), 2)               as avg_distance_km,
       round(avg(active_min))                   as avg_active_min,
       round(avg(kcal))                         as avg_kcal,
       round(avg(stand_hours), 1)               as avg_stand_hours,
       round(avg(avg_hr))                       as avg_hr,
       round(avg(rhr))                          as avg_rhr,
       round(avg(avg_stress))                   as avg_stress,
       round(avg(sleep_h), 2)                   as avg_sleep_h,
       round(avg(deep_h), 2)                    as avg_deep_h,
       round(avg(rem_h), 2)                     as avg_rem_h,
       round(avg(sleep_score))                  as avg_sleep_score,
       sum(workouts)                            as workouts,
       round(sum(workout_min))                  as workout_min,
       (array_agg(weight_kg order by local_date desc) filter (where weight_kg is not null))[1] as last_weight_kg
from health.daily_metrics
group by 1, 2;

-- ---------------------------------------------------------------------------
-- Lock down: no access for API roles; RLS on, policies only for health_writer.
-- ---------------------------------------------------------------------------

alter table health.observations    enable row level security;
alter table health.daily_summaries enable row level security;
alter table health.activities      enable row level security;
alter table health.import_runs     enable row level security;

revoke all on all tables    in schema health from public, anon, authenticated;
revoke all on all sequences in schema health from public, anon, authenticated;

-- Least-privilege group role for the loader. Create the LOGIN user once,
-- manually, with a strong password stored only in your local .env:
--   create role health_loader login password '<generate-locally>' in role health_writer;
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'health_writer') then
        create role health_writer nologin;
    end if;
end $$;

grant usage on schema health to health_writer;
grant select, insert, update on health.observations, health.daily_summaries, health.activities to health_writer;
grant select, insert on health.import_runs to health_writer;
grant execute on function health.num(jsonb) to health_writer;
grant select on health.daily_metrics, health.weekly_metrics to health_writer;

-- RLS applies to the loader too (it is not the table owner), so grant it
-- access through policies scoped to that role only.
drop policy if exists writer_all on health.observations;
create policy writer_all on health.observations    for all to health_writer using (true) with check (true);
drop policy if exists writer_all on health.daily_summaries;
create policy writer_all on health.daily_summaries for all to health_writer using (true) with check (true);
drop policy if exists writer_all on health.activities;
create policy writer_all on health.activities      for all to health_writer using (true) with check (true);
drop policy if exists writer_all on health.import_runs;
create policy writer_all on health.import_runs     for all to health_writer using (true) with check (true);
