# Design notes: how this pipeline was built and why
<p>
  <a href="design-notes.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/en-active.svg" alt="English" height="30"></a>
  <a href="design-notes.es.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/es.svg" alt="Leer en español" height="30"></a>
</p>

Companion to the [setup guide](setup-guide.md). This file explains the decisions behind each piece, how the data was explored without exposing it, and the problems found along the way, so the same approach can be reused or extended.

## Goals and constraints

- **Goal:** centralize personal activity and health history from a Mi Band, and be able to ask an LLM grounded questions about sleep, stress, aerobic activity and weight.
- **Public code, private data:** the repository is public, while every real record, export, digest and credential stays on the owner's machine or in the owner's database.
- **Re-runnable:** exports arrive manually and occasionally, so every step has to be safe to repeat on overlapping data.
- **Least privilege everywhere:** each component gets only the database access it needs (write-only loader, read-only connector).

## 1. Source: the Mi Fitness account export

The Xiaomi account data export is the only source. It is requested from the Xiaomi account privacy center and delivered by email as a password-protected ZIP. It contains many CSVs; only three are used.

### Files used

| Export file | Becomes | Notes |
|---|---|---|
| `hlth_center_fitness_data.csv` | `health.observations` | `Key` = metric, `Time` = epoch seconds (UTC), `Value` = JSON payload |
| `hlth_center_aggregated_fitness_data.csv` | `health.daily_summaries` | `Tag` = `daily_report` (metrics), `daily_mark` (`has_data` flag, skipped) or `daily_fitness` (goals). `Time` is UTC midnight of the *local* calendar date |
| `hlth_center_sport_record.csv` | `health.activities` | One row per workout; `timezone` in 15-minute units |

### Files deliberately ignored

`hlth_center_sport_track_data.csv` (GPX routes: precise location), `user_member_profile.csv`, `user_fitness_profile.csv`, `user_device_setting.csv`, `hlth_center_data_source.csv` (device identifiers), social/challenge records and the embedded `*_data_copy.zip`. `scripts/inspect_export.py` also reads only the three files above.

### Exploring the data without looking at it

The export was mapped without ever printing a biometric value or an identifier:

1. **Schema probe:** for each file, print headers, row counts and, for each metric, the *shape* of the JSON payload (field names and types only).
2. **Semantic probe:** check hypotheses and print only booleans and counts. For example: are there duplicates on (metric, timestamp)? Does the `timezone` field match the expected local offset when read as 15-minute units? Are aggregated rows aligned to UTC midnight? Do sleep stage minutes add up to the total? Is distance in meters?
3. **Output verification:** after normalizing, scan the output for any identifier value present in the raw export and for any identifier-like key, and cross-check that summed step observations per day match Mi Fitness's own daily totals (they did, within 2% on every day). That validates the timezone handling end to end.

## 2. Normalization (`scripts/import_mifitness.py`)

- **Natural keys:** observation = metric + timestamp; daily summary = metric + local date; activity = sport + start time. The record id is a truncated SHA-256 of the source plus the natural key. The payload is *not* part of the id, so a value corrected in a newer export updates the same record instead of creating a duplicate.
- **Several exports side by side:** files are read recursively, and on a collision the row with the newest `UpdateTime` wins.
- **Identifier scrubbing:** `Uid`, `Sid`, `Did` columns are dropped, and keys such as `sid`, `dbKey`, `sidList`, `did`, `deviceId`, `model` and `serial` are removed recursively from payloads.
- **Local dates:** each record uses its own `timezone` field when present (15-minute units), otherwise `HEALTH_UTC_OFFSET`. A night of sleep belongs to the day you wake up.
- **Validation:** plausible ranges per metric (e.g. HR 25–240 bpm, stress 0–100, weight 20–300 kg) and plausible timestamps (2000–2100). Rejected rows are counted per reason in `manifest.json`, never printed.
- **Deterministic output:** records are sorted, written as JSONL with sorted keys, and files are replaced atomically. Two runs on the same input produce byte-identical files.
- **Guard rails:** writes only under `data/processed/` or `data/private/`; refuses to overwrite good output with an empty result unless `--allow-empty` is passed.

## 3. Weekly digest (`scripts/weekly_digest.py`)

- Built from local JSONL, so it works without any database.
- One metrics row per day: daily summaries first, observations as fallback (resting HR, weight).
- **Missing is not zero:** days without a metric are left out of averages.
- **Baselines:** averages compare against the previous N weeks' daily values. Totals (workouts, minutes) compare against a 7-day equivalent of the baseline days that actually have data, so a short history does not bias them.
- Personal context for the LLM comes from `HEALTH_DIGEST_CONTEXT` in `.env`, never from the code.
- Output is forced under `data/reports/`, which Git ignores.

### Synthetic sample data (`scripts/make_sample_export.py`)

- Generates an export with the same file names, columns and JSON payloads as the real one, including fake device identifiers, so the scrubbing can be seen working.
- Internally consistent: hourly steps add up to the daily totals, and intensity markers match active minutes.
- Built-in patterns for an LLM to discover: workout days tend to be followed by more deep sleep, high-stress days by worse sleep, weight drifts down and resting HR improves slightly.
- Deterministic for a given seed. A `SYNTHETIC_SAMPLE.txt` marker travels with it: the importer flags `synthetic: true` in the manifest and refuses to write into the real output folder, the loader refuses to upload it without `--allow-synthetic`, and the digest prints a banner.

### Dashboard (`scripts/dashboard.py`)

- One self-contained HTML file: data inlined as JSON, charts drawn as plain SVG with no libraries. The only network request is the font stylesheet, which carries no data; offline it falls back to system fonts.
- Look and logic are separate: `dashboard/theme.json` holds every color (both modes), the fonts and the titles; `dashboard/template.html` only references those tokens. `scripts/dashboard.py` validates the theme (required keys, safe values) and injects it with the data.
- The default theme takes the GlobalAIze brand: night-navy surfaces, the brand cyan and the warm orange already used for charts on its site. A brand violet was tried as the third series and failed the color-blind check against the cyan, so a deeper violet that passes in both modes took its place.
- Ideal ranges come from `dashboard/references.json`, each with its source, and were checked before being drawn: Xiaomi's own support pages give its stress levels (relaxed 1–25, mild 26–50, moderate 51–80, high 81–100), which differ from other brands' cutoffs; Xiaomi publishes no sleep-score scale, so the "80+ is good" band borrows the convention of other apps and says so. The bands sit behind the marks, and their labels are drawn on top with a halo so they stay readable.
- The healthy-weight band needs a height, read from `HEALTH_HEIGHT_CM`. Synthetic output ignores it (only an explicit `--height-cm` counts), so the README screenshots can't reveal the owner's height.
- Frosted-glass cards: translucent fills with a backdrop blur over soft brand-colored glows; in light mode, an off-white page with optical-white cards. All of it comes from theme tokens.
- Responsive grid: two-thirds plus one-third per row on wide screens, two columns on tablets (dense packing, so no half-empty rows), one column on phones; every plot stretches to fill its card.
- Built following explicit data-visualization rules: the palette (three categorical slots, light and dark) was validated with a script for colorblind separation instead of eyeballed; there are no dual-axis charts (stress and sleep are a scatter, not two lines on two scales); marks are thin, gridlines are solid hairlines, and every chart has a hover or keyboard tooltip plus a table with the same values.
- The "emphasis" form for steps: workout days in the accent color, the rest in gray, instead of a value ramp.
- Privacy guard: the HTML must be written under `data/reports/`, and a screenshot of real data can't be written outside it. The README pictures come from synthetic data.

## 4. Storage and access model (Supabase)

Everything lives in a self-contained `health` schema. The migrations create objects only inside it plus three roles, so it can live in a new or an existing project and can be dumped or dropped independently.

| Role | Login | Can do | Used by |
|---|---|---|---|
| `health_writer` | no | SELECT / INSERT / UPDATE on `health` tables (no DELETE) | group role |
| `health_loader` | yes (password in local `.env`) | member of `health_writer` | `scripts/load_supabase.py` |
| `health_reader` | no | SELECT on `health` tables and views | the MCP Edge Function, via `SET LOCAL ROLE` |
| `anon`, `authenticated` | (API roles) | nothing: no schema usage, no grants | not used |

- **Not exposed to the Data API:** `health` is never added to the exposed schemas, so there is no REST/GraphQL path to it.
- **RLS on every table** with policies only for `health_writer` (all) and `health_reader` (select). API roles would be denied even if the schema were exposed by mistake.
- **No superuser credentials in the pipeline:** neither the `postgres` URL nor the service-role key is used. They can read every schema, so they are treated as health-grade secrets.
- **Wrong-project guard:** the loader checks that the URL's host or pooler user (`role.<ref>`) matches `HEALTH_SUPABASE_PROJECT_REF` before writing.
- **Value-free errors:** database errors are reported by SQLSTATE only, because Postgres error details can include row values.
- **Upserts that don't churn:** `ON CONFLICT (id) DO UPDATE … WHERE` the source row is at least as recent *and* something actually changed (`IS DISTINCT FROM`). Re-loading identical data writes nothing.
- **Views:** `health.daily_metrics` and `health.weekly_metrics` are `security_invoker`, so they run with the caller's privileges. A helper `health.num(jsonb)` returns NULL for non-numeric JSON values, so one odd value can't break a whole view.

## 5. The MCP connector (`supabase/functions/health-mcp`)

**Why MCP:** it lets a chat assistant (Claude on web and mobile) decide on its own when to fetch data while you talk to it, without exporting files or building a dashboard. An MCP server is a small API with a standard JSON-RPC format in which every "endpoint" is a *tool* with a description for the model and a JSON schema for its parameters.

Design:

- **Stateless Streamable HTTP, hand-written:** each POST carries one JSON-RPC message (`initialize`, `ping`, `tools/list`, `tools/call`) and gets a JSON response; notifications get `202`. At this size no SDK is needed (about 250 lines).
- **Fixed, read-only tools:** four tools with validated parameters (date format, max 186-day range, row limits), annotated `readOnlyHint`. There is no free-form SQL. `data_coverage` exists so the model can learn the available date range first, and the server instructions ask it to start there.
- **Privilege drop per call:** the function connects with the project's built-in database URL and runs each tool inside `BEGIN READ ONLY; SET LOCAL ROLE health_reader; SET LOCAL statement_timeout = '5s'`.
- **Auth:** the function is deployed with JWT verification off and answers only at `/health-mcp/<secret>`, using a constant-time comparison; everything else gets a 404. That fits a single-user connector. OAuth would be the next step for multi-user use.
- **Errors:** validation errors are explained to the model so it can fix its parameters; database errors return a generic `query failed` with nothing logged.
- **Deploy without a CLI:** `scripts/deploy_connector.py` uses the Supabase Management API (`database/query`, `secrets`, `functions/deploy`) with a short-lived, three-permission personal access token, then smoke-tests the endpoint.

## 6. Problems found and how they were solved

| Problem | Fix |
|---|---|
| Aggregated CSV has two rows per metric/day (`daily_report` + `daily_mark`); mixing them overwrote metrics with `{has_data: true}` | Keep `daily_report`; skip `daily_mark`; give other tags their own metric name |
| Payload contained device identifiers nested inside daily reports (`latest_hr.sid`, `dbKey`, `sidList`) | Recursive key scrubbing before writing anything |
| Ids derived from the payload would duplicate records when Xiaomi corrects a value | Ids from the natural key only; newest `UpdateTime` wins |
| `timezone` field is in 15-minute units, not hours or seconds | Verified with a boolean probe against the expected local offset (e.g. `4` ⇔ UTC+01:00) and converted accordingly |
| `.gitignore` pattern `*_MiFitness*` also hid `scripts/import_mifitness.py` (Git on Windows matches case-insensitively) | Pattern narrowed to `*_MiFitness_*` plus `!scripts/**/*.py`; verified in a throwaway mirror repo before the first push |
| Empty or still-zipped export would silently wipe good output | Abort on zero records unless `--allow-empty` |
| `datetime.fromtimestamp` raises on Windows for out-of-range values, aborting the import | Accept only epochs between 2000 and 2100 |
| Direct database host is IPv6-only on Supabase's free plan and fails from many home networks | Use the Session pooler URL with user `role.<project-ref>` |
| `SET TRANSACTION READ ONLY` after other statements doesn't apply | Open the transaction as read-only (`sql.begin("read only", …)`) |
| Since Postgres 16, creating a role doesn't let the creator `SET ROLE` to it | Explicit `grant health_reader to postgres` |
| `security_invoker` views failed for the reader role | Grant SELECT on base tables, EXECUTE on `health.num`, and add reader RLS policies |
| `postgres.js` returns `numeric` as strings and `date` as JS dates | Cast in SQL to `::float8` and `::text` |
| Converting an ISO week label to a date in SQL | `to_date('2026-W46', 'IYYY-"W"IW')` returns that week's Monday |

## 7. Build log

1. Inventory the export structure with privacy-safe probes (`inspect_export.py`).
2. Idempotent importer to private JSONL (`import_mifitness.py`).
3. Weekly Markdown digest from local data (`weekly_digest.py`).
4. Private schema, roles and views (`0001_health_schema.sql`); loader with project guard (`load_supabase.py`).
5. Independent reviews before publishing: one for privacy and requirements, one for code and SQL correctness. Their findings were fixed before the first push.
6. Data loaded into Supabase as `health_loader`; re-load verified to write zero rows.
7. Read-only MCP connector (`0002_health_reader.sql`, `health-mcp`, `deploy_connector.py`), deployed and smoke-tested.
8. One-command pipeline (`refresh.py`) and a synthetic sample export with guards against mixing it with real data (`make_sample_export.py`).
9. Self-contained HTML dashboard (`dashboard.py`) and the architecture image for the README.

## 8. Known gaps

- History grows only with each manual export, so trends need several weeks of regular wear, and weight needs regular weigh-ins.
- The band doesn't measure treadmill incline; `health.activities.incline_pct` and `notes` exist for manual enrichment.
- Stress and sleep scores are proprietary Xiaomi algorithms, which makes them good for trends but not clinical measures.
- The digest is generated from local files, not from `health.weekly_metrics`.

## 9. Future ideas

- **Automatic sync for Android 14 and later.** Health Connect is part of the OS from Android 14, so an on-phone sync could replace batch exports for those users, while the export path stays for Android 13 and lower. **Incremental capture through Health Connect** would reuse the same tables and natural keys so exports and incremental data merge without duplicates. Health Connect has no stress record type, so stress would keep coming from exports.
- **Near-real-time ingestion,** so the connector always answers with today's data.
- **OAuth for the connector** instead of a secret path token.
- **A write tool for manual enrichment** (e.g. "yesterday's treadmill session was at 8% incline"), with its own narrowly scoped role.
