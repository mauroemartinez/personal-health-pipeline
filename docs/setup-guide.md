# Setup guide: from your Mi Fitness export to chatting with Claude about your data
<p>
  <a href="setup-guide.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/en-active.svg" alt="English" height="30"></a>
  <a href="setup-guide.es.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/es.svg" alt="Leer en español" height="30"></a>
</p>

This guide takes you from zero to the full pipeline with **your own data**. Each stage is useful on its own, so you can stop wherever you want:

| Stage | You get | Needs |
|---|---|---|
| [1. Get your data](#1-get-your-data) | Your Mi Fitness history as CSV files | A Xiaomi account |
| [2. Set up the repo](#2-set-up-the-repo) | The scripts, configured for your timezone | Python 3.10+, Git |
| [3. Normalize](#3-normalize-the-export) | Clean, de-duplicated JSONL files | — |
| [4. Weekly digest](#4-weekly-digest) | A Markdown summary to paste into any LLM | — |
| [5. Supabase](#5-load-into-supabase-optional) *(optional)* | Your data in a locked-down Postgres schema | A free Supabase project |
| [6. Claude connector](#6-chat-with-claude-optional) *(optional)* | Ask Claude about your data from web or phone | A Claude account |

Then: [keeping it up to date](#7-keeping-it-up-to-date) · [troubleshooting](#8-troubleshooting) · [costs](#9-costs) · [uninstall](#10-uninstall).

Everything personal stays on your machine or in your own Supabase project. Nothing in this guide sends your data to this repository.

> **No export yet?** Clone the repo (stage 2) and run `python scripts/refresh.py --sample`. It generates eight weeks of realistic synthetic data in the Mi Fitness format, normalizes it into `data/processed/sample/` and writes a digest under `data/reports/sample/`. Synthetic data carries a marker: the importer refuses to write it into your real output folder, and the loader refuses to upload it unless you pass `--allow-synthetic` (only for a database without real data).

---

## 1. Get your data

The source is the official **Xiaomi account data export**. It covers the devices that sync with the Mi Fitness app, such as Xiaomi Smart Band and Xiaomi watches. Only this export format is supported.

> **Why an export and not automatic sync?** The export works with any phone, including Android 13 and lower. On Android 14 and later, Health Connect is built into the system, which makes automatic background sync possible with no batch step. That path is planned, not built yet.

1. Sign in at **https://account.xiaomi.com** with the account you use in Mi Fitness.
2. Go to **Privacy → Manage your data → Mi Fitness** and click **Download**.
3. Follow the prompts to bind an email address. Menu labels change from time to time; see [Xiaomi's support article](https://www.mi.com/ph/support/article/KA-11566).
4. Wait for the email. Xiaomi announces up to about 15 business days, but it can arrive sooner. It contains a download link and a **one-time password for the ZIP that expires after a few days**, so download and unzip it as soon as it arrives.

Keep the ZIP's password private and do not share the files: they include your account id, device ids and, in the GPX file, precise locations.

---

## 2. Set up the repo

```bash
git clone https://github.com/mauroemartinez/personal-health-pipeline.git
cd personal-health-pipeline
```

Create your local configuration from the template. `.env` is ignored by Git:

```bash
cp .env.example .env          # macOS / Linux
copy .env.example .env        # Windows (cmd / PowerShell)
```

Open `.env` and set at least:

| Variable | What to put | Example |
|---|---|---|
| `HEALTH_UTC_OFFSET` | Your UTC offset. Used for records that don't carry their own timezone. | `+01:00`, `-05:00` |
| `HEALTH_DIGEST_CONTEXT` | *(optional)* One line about your routine and goals, added to the digest so the LLM has context. | `morning runs, strength twice a week; goal: better sleep` |

Unzip the Xiaomi export into **`data/private/mi_fitness_export/`**. Subfolders are fine, for example one folder per export. The whole `data/private/` tree is ignored by Git.

```text
data/private/mi_fitness_export/
└── 2026-11-export/
    ├── <date>_<id>_MiFitness_hlth_center_fitness_data.csv
    ├── <date>_<id>_MiFitness_hlth_center_aggregated_fitness_data.csv
    ├── <date>_<id>_MiFitness_hlth_center_sport_record.csv
    └── … (other files are ignored by the importer)
```

**Python:** stages 2 to 4 use only the standard library, so there is nothing to install. On Windows use `python`; on macOS/Linux use `python3` in every command below.

---

## 3. Normalize the export

Optional sanity check. It prints column names and row counts only, never values:

```bash
python scripts/inspect_export.py
```

Run the importer:

```bash
python scripts/import_mifitness.py --dry-run   # parse and count, write nothing
python scripts/import_mifitness.py             # write data/processed/mi_fitness/
```

You should see something like:

```text
Mi Fitness import:
  observations: 25000
  daily_summaries: 140
  activities: 15
  skipped: 0  duplicates resolved: 0
  output: data/processed/mi_fitness
```

What it produces, all inside `data/processed/mi_fitness/` (ignored by Git):

| File | Content |
|---|---|
| `observations.jsonl` | Point measurements: heart rate, stress, steps, calories, intensity minutes, stand intervals, sleep sessions, resting HR, weight |
| `daily_summaries.jsonl` | Mi Fitness daily reports: HR zones, stress distribution, sleep stages and score, steps… |
| `activities.jsonl` | One row per workout: duration, distance, HR, training load… |
| `manifest.json` | Counts per metric, skipped rows per reason, date range |

Properties you can rely on:

- **Idempotent:** running it again on the same files produces byte-identical output, and a newer export updates records in place instead of duplicating them.
- **Private by construction:** account and device identifiers are removed; GPX routes, profiles and device settings are never read; the console shows counts only.
- **Safe on mistakes:** if it finds no records (export still zipped, wrong folder), it stops and leaves your previous output untouched.

---

## 4. Weekly digest

```bash
python scripts/weekly_digest.py                  # last complete ISO week (Mon–Sun)
python scripts/weekly_digest.py --week 2026-W46  # a specific week
python scripts/weekly_digest.py --latest         # most recent week with data (may be partial)
python scripts/weekly_digest.py --baseline-weeks 8
```

The file is written to `data/reports/weekly/<ISO week>.md` (ignored by Git). It contains:

- **Summary vs. baseline:** the week's averages and totals next to the previous N weeks, with deltas and how many days had data.
- **Weight:** the latest weigh-in and the change from the previous one.
- **Daily detail:** steps, km, active minutes, resting HR, stress and sleep for each day.
- **Workouts:** duration, distance, pace, HR, training load.
- **Prompt section:** your `HEALTH_DIGEST_CONTEXT` plus suggested questions.

Paste it into any chat assistant (Claude, ChatGPT, Gemini…) and ask away. Days without data are left out of averages, never counted as zeros. **The digest text is in Spanish.** Labels live in `scripts/weekly_digest.py` if you want to translate them.

> If the digest is all you need, you can stop here.

---

## 5. Load into Supabase (optional)

This puts your data in a Postgres database so it can be queried with SQL and by the Claude connector (stage 6). Everything goes into a self-contained schema called `health`, so you can use a new free project or an existing one: the migrations never touch `public` or any other schema.

### 5.1 Create the schema

1. Create a project at **https://supabase.com** (free plan is enough), or open an existing one.
2. Open **SQL Editor → New query**, paste the contents of [`supabase/migrations/0001_health_schema.sql`](../supabase/migrations/0001_health_schema.sql) and click **Run**. It creates the tables, analytical views and the `health_writer` role, enables RLS and revokes all access from the API roles. It is safe to run more than once.
3. In a new query, create the login the loader will use. Pick a password made of **letters and numbers only**: symbols such as `@ : / #` break connection URLs.

   ```sql
   create role health_loader login password 'YourLongRandomPassword' in role health_writer;
   ```

4. Check that `health` is **not** listed under the project's **Data API → Exposed schemas**. It isn't by default; just don't add it.

### 5.2 Get the connection string

1. In the dashboard, click **Connect** (top bar) and choose the **Session pooler** connection string. Avoid the *direct* connection: on the free plan it is IPv6-only and often fails from home networks.
2. It looks like:

   ```text
   postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

3. Replace `postgres.<project-ref>` with `health_loader.<project-ref>`, and `[YOUR-PASSWORD]` with the password from 5.1. Keep everything after the `@` as is.
4. Put it in `.env`, together with your project ref (the `<ref>` in `https://<ref>.supabase.co`):

   ```text
   HEALTH_SUPABASE_PROJECT_REF=<project-ref>
   HEALTH_DATABASE_URL=postgresql://health_loader.<project-ref>:YourLongRandomPassword@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

Why not just use the `postgres` URL? It would work, but that user can read and drop everything in the project. `health_loader` can only write to `health`, so a leaked `.env` can't touch anything else.

### 5.3 Load

```bash
python -m pip install -r requirements.txt     # installs psycopg (only needed for this stage)
python scripts/load_supabase.py               # validates and counts, writes nothing
python scripts/load_supabase.py --apply       # upserts and logs the run in health.import_runs
```

- Re-running is safe: rows are matched by a deterministic id, and unchanged rows are skipped entirely.
- The loader refuses to write if the URL doesn't belong to `HEALTH_SUPABASE_PROJECT_REF`. It reports database errors by SQLSTATE only, never with your values.

Check it in the SQL Editor:

```sql
select * from health.weekly_metrics order by week_start desc;
select * from health.daily_metrics order by local_date desc limit 14;
```

---

## 6. Chat with Claude (optional)

This deploys a small **read-only MCP server** as a Supabase Edge Function and connects it to Claude as a *custom connector*. After that you can ask Claude, from the web or the mobile app, things like *"did I sleep worse on nights after treadmill workouts?"* and it will query your data on the spot.

What Claude gets:

| Tool | Returns |
|---|---|
| `data_coverage` | Date range with data, rows per table, last load time |
| `weekly_summary` | Weekly averages: steps, sleep, stress, HR, workouts, last weight |
| `daily_metrics` | One row per day for a date range (max 186 days) |
| `activities` | Each workout: duration, distance, pace, HR, load, incline/notes |

Every query runs as `health_reader`, a role without login that can only `SELECT` from `health`, inside a read-only transaction with a 5-second timeout. There is no free-form SQL. The endpoint only answers at a secret path token; any other path returns 404.

### 6.1 Create a temporary access token

1. Go to **https://supabase.com/dashboard/account/tokens → Generate new token**. Give it the shortest expiry available.
2. If it offers fine-grained permissions, grant only these three (everything else **None**):

   | Section | Permission | Level |
   |---|---|---|
   | Database | **Database** | Read-write |
   | Application services | **Edge Functions** | Read-write |
   | Application services | **Edge Function Secrets** | Read-write |

3. Copy the token (`sbp_…`, shown only once) into `.env` as `SUPABASE_ACCESS_TOKEN=`.

### 6.2 Deploy

Requires stage 5. Then run:

```bash
python scripts/deploy_connector.py
```

It uses the Supabase Management API (no CLI needed) to:

1. Apply [`0002_health_reader.sql`](../supabase/migrations/0002_health_reader.sql), which creates the read-only role.
2. Generate a random path token once and store it as the function secret `MCP_PATH_TOKEN`.
3. Deploy [`supabase/functions/health-mcp`](../supabase/functions/health-mcp/index.ts) with JWT verification off; the path token is the auth.
4. Smoke-test the endpoint, including that a wrong token gets a 404.
5. Write the connector URL to `data/private/connector_url.txt`.

Expected output:

```text
Deploying MCP connector:
  migration 0002 (health_reader): HTTP 201 ok
  secret MCP_PATH_TOKEN: HTTP 201 ok
  deploy function: HTTP 201 ok
  initialize: personal-health (protocol 2025-06-18)
  tools: data_coverage, weekly_summary, daily_metrics, activities
  data_coverage: ok
  wrong token rejected: True (HTTP 404)
Connector URL saved to data/private/connector_url.txt (keep it secret).
```

Then **revoke the access token** on the same Supabase page and clear it from `.env`. The connector keeps working; you only need a new token to redeploy changes. Re-running the script later is safe, and the connector URL stays the same.

<details>
<summary>Prefer the Supabase CLI? (not the path tested here)</summary>

```bash
# 1. Run supabase/migrations/0002_health_reader.sql in the SQL Editor
# 2. Then:
supabase login
supabase link --project-ref <project-ref>
supabase secrets set MCP_PATH_TOKEN=<a long random string>
supabase functions deploy health-mcp --no-verify-jwt
# Connector URL: https://<project-ref>.supabase.co/functions/v1/health-mcp/<MCP_PATH_TOKEN>
```
</details>

### 6.3 Add it to Claude

1. On **claude.ai** (web), open **Settings / Customize → Connectors → Add custom connector**.
2. **Name:** anything, e.g. `My health`. **URL:** the content of `data/private/connector_url.txt`. Leave OAuth / advanced settings empty.
3. In a new chat, open the tools menu, make sure the connector is enabled, and ask something like *"Use My health: how was my sleep over the last two weeks, and does it relate to stress?"*. Allow the tools when asked; they are all read-only.
4. Connectors added on the web show up in the Claude mobile apps automatically.

Treat the connector URL like a password: anyone who has it can read your data.

---

## 7. Keeping it up to date

Data is as fresh as your last export. Every week or two:

1. Request a new export (stage 1) and unzip it into `data/private/mi_fitness_export/`. You can keep the old ones next to it; the newest value of each record wins.
2. Run the whole pipeline with one command:

   ```bash
   python scripts/refresh.py
   ```

   It normalizes, loads into Supabase (only if `HEALTH_DATABASE_URL` is set; `--skip-load` to skip it) and writes the digest for the last complete week (`--week 2026-W46` for another). It stops at the first step that fails. The three underlying commands still work on their own:

   ```bash
   python scripts/import_mifitness.py
   python scripts/load_supabase.py --apply
   python scripts/weekly_digest.py
   ```

Claude sees the new data immediately; the connector doesn't need redeploying.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pip: command not found` | Use `python -m pip install -r requirements.txt` (or `python3 -m pip`). |
| `No records found (is the export still zipped…)` | Unzip the export into `data/private/mi_fitness_export/`. File names must keep the `…_MiFitness_hlth_center_…csv` pattern. Your previous output was not touched. |
| Days shifted by one / workouts on the wrong date | Set `HEALTH_UTC_OFFSET` in `.env` to your real offset and re-run the importer. |
| `HEALTH_DATABASE_URL does not belong to HEALTH_SUPABASE_PROJECT_REF` | The URL's host or pooler user (`health_loader.<ref>`) doesn't match the ref. Copy the ref from `https://<ref>.supabase.co`. |
| `Database error during 'connect' (SQLSTATE …)` | Usually one of: wrong password (reset it with `alter role health_loader password '…';`), user not written as `health_loader.<ref>`, or the direct `db.<ref>.supabase.co` host instead of the **Session pooler** URL. |
| `SQLSTATE 42501` (permission denied) or `42P01` (relation does not exist) | Migration `0001` wasn't applied, or `health_loader` wasn't created `in role health_writer`. |
| `deploy_connector.py`: a step shows `HTTP 401/403 FAILED` | The access token is expired, revoked or missing one of the three permissions in 6.1. |
| Connector added but Claude doesn't use it | Enable it in the chat's tools menu, or mention it by name in the question. |
| Connector URL returns 404 | The URL or token is wrong. Re-run `deploy_connector.py`, which re-sets the secret from `data/private/mcp_path_token`. |

---

## 9. Costs

- **Stages 1–4:** free.
- **Supabase:** the free plan covers this use. Reference size: about 9 MB for 20 days of history (~14 MB/month), almost all of it intraday heart rate and stress. Against the free plan's 500 MB database (at the time of writing) that is years of data. If space ever gets tight, load only daily summaries and activities. The connector makes a handful of Edge Function calls per question.
- **Claude:** custom connectors are available on Claude plans, including Free, which is limited to one custom connector at the time of writing. See [Claude's help article](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

---

## 10. Uninstall

In the Supabase SQL Editor:

```sql
drop schema if exists health cascade;
drop role if exists health_loader;
drop role if exists health_reader;
drop role if exists health_writer;
```

Then delete the `health-mcp` function and the `MCP_PATH_TOKEN` secret under **Edge Functions**, remove the connector in claude.ai, and delete `data/` and `.env` locally.
