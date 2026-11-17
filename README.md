# Personal Health Data Pipeline

Private-by-default ETL for personal activity and health data from a Xiaomi Mi Band (Mi Fitness app), built to ask an LLM grounded questions about sleep, stress, aerobic activity and weight.

```text
Mi Fitness account export (CSV)
        │  scripts/import_mifitness.py   normalize · scrub identifiers · idempotent
        ▼
data/processed/mi_fitness/*.jsonl        private, ignored by Git
        ├──► scripts/weekly_digest.py → data/reports/weekly/<ISO week>.md   (Markdown for LLM analysis)
        │
        └──► scripts/load_supabase.py    upsert by deterministic id
                     ▼
             Supabase Postgres · schema `health`    not exposed to the API, RLS on, no anon access
             views health.daily_metrics / health.weekly_metrics   (SQL analysis; future digest source)
```

## Privacy boundary

This repository is public; the data is not.

- Real exports, normalized records, digests and credentials live under ignored paths: `data/private/`, `data/processed/`, `data/reports/`, `.env`.
- The importer drops account and device identifiers (`Uid`, `Sid`, `Did`, `sid`, `dbKey`, `sidList`, …) and never reads GPX routes, member profiles, device settings or social records.
- Scripts print counts and paths only, never biometric values. Database errors are reported by SQLSTATE only.
- In Supabase, health data lives in a self-contained `health` schema. The schema is not exposed through the Data API, API roles (`anon`, `authenticated`) have no privileges, and loading uses a least-privilege role.

## Quick start

Requires Python 3.10+. The importer and digest use only the standard library.

```powershell
copy .env.example .env                        # set HEALTH_UTC_OFFSET to your local offset
# Put the unzipped Mi Fitness export in data/private/mi_fitness_export/
python scripts/inspect_export.py              # optional: column names and row counts only
python scripts/import_mifitness.py            # normalize (add --dry-run to only count)
python scripts/weekly_digest.py               # last complete ISO week; or --week 2026-W46 / --latest
```

The importer is safe to re-run: the same input produces byte-identical output, and a newer export updates records in place. The digest compares the week against a baseline of previous weeks (`--baseline-weeks`, default 4). Its text is in Spanish, the author's working language.

### Loading into Supabase (optional)

1. Run `supabase/migrations/0001_health_schema.sql` in the SQL editor.
2. Create the loader login: `create role health_loader login password '<strong password>' in role health_writer;`
3. Make sure `health` is **not** listed under *Settings → Data API → Exposed schemas*.
4. Fill `HEALTH_SUPABASE_PROJECT_REF` and `HEALTH_DATABASE_URL` (as `health_loader`) in `.env`.
5. Install the driver and load:

```powershell
python -m pip install -r requirements.txt
python scripts/load_supabase.py               # validates and counts, writes nothing
python scripts/load_supabase.py --apply       # upsert; logs the run in health.import_runs
```

## Data model

| Table | Grain | Content |
|---|---|---|
| `health.observations` | metric × timestamp | heart rate, stress, steps, calories, intensity minutes, stand intervals, sleep sessions, resting HR, weight |
| `health.daily_summaries` | metric × local date | Mi Fitness daily reports (HR zones, stress distribution, sleep stages and score, steps…) |
| `health.activities` | sport × start time | walks, indoor running, strength; duration, distance, HR, training load; manual `incline_pct` and `notes` |
| `health.import_runs` | load run | counts and skipped rows per load |

Timestamps are stored in UTC. `local_date` uses the record's own timezone when Mi Fitness provides it (15-minute units), otherwise `HEALTH_UTC_OFFSET`. A night of sleep is assigned to the day you wake up.

## Scope

- **Now:** periodic manual Mi Fitness exports → local ETL → (optional) Supabase → weekly digest.
- **Future ideas:** incremental capture through Android Health Connect, and near-real-time ingestion so an LLM can answer questions about the latest data on demand. See [docs/ingestion-plan.md](docs/ingestion-plan.md).

## License

[MIT](LICENSE)
