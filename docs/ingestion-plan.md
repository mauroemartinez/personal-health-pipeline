# Ingestion plan

## Current source: Mi Fitness account export

Xiaomi's account data export (requested from the Mi Fitness privacy settings, delivered by email as a password-protected ZIP) is the only source for now. It is a manual, occasional process, so the pipeline is built to be re-run on every new export without creating duplicates.

### Files used

| Export file | Use | Notes |
|---|---|---|
| `hlth_center_fitness_data.csv` | `health.observations` | `Key` = metric, `Time` = epoch seconds (UTC), `Value` = JSON payload |
| `hlth_center_aggregated_fitness_data.csv` | `health.daily_summaries` | Two rows per metric/day: `daily_report` (metrics, kept) and `daily_mark` (`has_data` flag, skipped). `Time` is UTC midnight of the local calendar date |
| `hlth_center_sport_record.csv` | `health.activities` | Workout summary; `timezone` in 15-minute units |

### Files deliberately ignored

`hlth_center_sport_track_data.csv` (GPX routes: precise location), `user_member_profile.csv`, `user_fitness_profile.csv`, `user_device_setting.csv`, `hlth_center_data_source.csv` (device identifiers), PK/social records, and the `*_data_copy.zip` archive. `scripts/inspect_export.py` also reads only the three files above.

### Normalization rules

- Natural keys: observation = metric + timestamp; daily summary = metric + local date; activity = sport + start time. The record id is a truncated SHA-256 of `mi_fitness` + natural key, so a corrected value in a newer export updates the same row.
- When several exports are placed side by side under `data/private/mi_fitness_export/`, they are read recursively and the row with the newest `UpdateTime` wins.
- Identifier fields are removed recursively from payloads before writing.
- Basic range checks (e.g. heart rate 25–240 bpm, stress 0–100, weight 20–300 kg). Rejected rows are counted per reason in `manifest.json`, never printed.
- Output files (`.jsonl`) are sorted and written atomically; running twice on the same input yields byte-identical files.

### Known gaps

- Sleep and weight need regular use of the band and scale to produce enough points for trend analysis.
- The band does not measure treadmill incline. `health.activities.incline_pct` and `notes` exist for manual enrichment.
- Stress and sleep scores are proprietary Xiaomi algorithms, which makes them useful for trends but not as clinical measures.

## Storage and access model

- All health data lives in a self-contained schema `health`. The migration creates objects only inside that schema plus the `health_writer` role, so it can be applied, dumped or moved independently.
- The project's `postgres` credentials can read every schema, so they are treated as health-grade secrets. Neither they nor the service-role key are used by the pipeline.
- Schema `health` is not added to the Data API's exposed schemas. `anon`/`authenticated` have no grants; RLS is enabled on every table with policies only for the `health_writer` role.
- The loader connects over Postgres with a least-privilege `health_loader` login (member of `health_writer`), never with the service-role key, and only from a trusted machine.
- It refuses to write unless `HEALTH_DATABASE_URL` contains `HEALTH_SUPABASE_PROJECT_REF`.
- Digests contain daily/weekly summaries and per-workout summaries — no intraday series, routes or identifiers — and are written under `data/reports/` (ignored by Git).

## Rollout

1. ✅ Inventory export structure (`scripts/inspect_export.py`, column names and counts only).
2. ✅ Idempotent importer to private JSONL (`scripts/import_mifitness.py`).
3. ✅ Weekly Markdown digest from local normalized data (`scripts/weekly_digest.py`).
4. ✅ Private SQL schema and views (`supabase/migrations/0001_health_schema.sql`).
5. ⏳ Apply the migration in Supabase, create `health_loader`, run `load_supabase.py --apply`.
6. ⏳ Optional: generate the digest from `health.weekly_metrics` instead of local files.

## Future ideas (not scheduled)

- **Health Connect as an incremental source.** If added, it should reuse the same tables and natural keys so that historical exports and incremental data merge without duplicates. Health Connect has no stress record type, so stress would keep coming from Mi Fitness exports.
- **Near-real-time ("streaming") ingestion.** Keep the database continuously up to date so an LLM can answer questions about the latest data on demand, instead of relying only on the weekly digest.
