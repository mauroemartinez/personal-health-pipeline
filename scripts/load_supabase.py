"""Upsert normalized Mi Fitness data into the private `health` schema in Supabase Postgres.

Requires:
  python -m pip install -r requirements.txt
  .env with HEALTH_DATABASE_URL and HEALTH_SUPABASE_PROJECT_REF (see .env.example)
  supabase/migrations/0001_health_schema.sql applied once in that project.

Safety:
  * Without --apply it only validates and counts; nothing is written.
  * Refuses to write unless the connection's host or pooler user belongs to
    HEALTH_SUPABASE_PROJECT_REF, so it cannot silently target the wrong project.
  * Upserts by deterministic id and skips unchanged rows: re-running never
    duplicates or rewrites data.
  * Prints counts only. Database errors are reported by SQLSTATE, never with
    row values or the connection string.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "mi_fitness"
BATCH = 1000

TABLES: dict[str, list[str]] = {
    "observations": ["id", "source", "metric", "measured_at", "local_date", "value", "unit",
                     "payload", "source_updated_at"],
    "daily_summaries": ["id", "source", "metric", "local_date", "payload", "source_updated_at"],
    "activities": ["id", "source", "sport", "category", "started_at", "ended_at", "local_date",
                   "duration_s", "valid_duration_s", "distance_m", "calories_kcal", "steps",
                   "avg_hr", "max_hr", "min_hr", "train_load", "train_effect", "vo2_max",
                   "payload", "source_updated_at"],
}


def load_env(path: Path) -> None:
    """Minimal .env reader (no dependency). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def url_matches_project(url: str, ref: str) -> bool:
    """True if the host is db.<ref>.supabase.co or the pooler user is <role>.<ref>."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    user = (parts.username or "").lower()
    ref = ref.lower()
    return host == f"db.{ref}.supabase.co" or user.endswith(f".{ref}")


def upsert_sql(table: str, columns: list[str]) -> str:
    data_cols = [c for c in columns if c != "id"]
    cols = ", ".join(columns)
    placeholders = ", ".join(f"%({c})s" for c in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in data_cols)
    current = ", ".join(f"t.{c}" for c in data_cols)
    incoming = ", ".join(f"excluded.{c}" for c in data_cols)
    # Update only when the source row is at least as recent AND something changed.
    return (
        f"insert into health.{table} as t ({cols}) values ({placeholders}) "
        f"on conflict (id) do update set {updates}, loaded_at = now() "
        f"where (t.source_updated_at is null or excluded.source_updated_at >= t.source_updated_at) "
        f"and ({current}) is distinct from ({incoming})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--apply", action="store_true", help="Actually write to the database")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    url = os.environ.get("HEALTH_DATABASE_URL", "")
    ref = os.environ.get("HEALTH_SUPABASE_PROJECT_REF", "")

    data = {t: read_jsonl(args.input / f"{t}.jsonl") for t in TABLES}
    manifest = json.loads((args.input / "manifest.json").read_text(encoding="utf-8"))
    for table, rows in data.items():
        missing = sum(1 for r in rows for c in TABLES[table] if c not in r)
        print(f"  {table}: {len(rows)} rows, {missing} missing fields")

    if not args.apply:
        print("Validation only: nothing written. Use --apply to upsert.")
        return
    if not url or not ref:
        parser.error("Set HEALTH_DATABASE_URL and HEALTH_SUPABASE_PROJECT_REF in .env")
    if not url_matches_project(url, ref):
        parser.error("HEALTH_DATABASE_URL does not belong to HEALTH_SUPABASE_PROJECT_REF; refusing to write.")

    import psycopg
    from psycopg.types.json import Jsonb

    step = "connect"
    try:
        # prepare_threshold=None keeps it compatible with Supabase's transaction pooler.
        with psycopg.connect(url, connect_timeout=15, prepare_threshold=None) as conn:
            with conn.cursor() as cur:
                for table, rows in data.items():
                    step = table
                    sql = upsert_sql(table, TABLES[table])
                    for i in range(0, len(rows), BATCH):
                        batch = [{**{c: r.get(c) for c in TABLES[table]},
                                  "payload": Jsonb(r.get("payload") or {})}
                                 for r in rows[i:i + BATCH]]
                        cur.executemany(sql, batch)
                    print(f"  upserted {table}: {len(rows)} (unchanged rows skipped)")
                step = "import_runs"
                cur.execute(
                    "insert into health.import_runs (source, counts, skipped) values (%s, %s, %s)",
                    ("mi_fitness", Jsonb(manifest.get("counts", {})), Jsonb(manifest.get("skipped", {}))),
                )
            conn.commit()
    except psycopg.Error as error:
        # Never print the message: Postgres DETAIL can include row values or connection info.
        print(f"Database error during '{step}' (SQLSTATE {error.sqlstate or 'n/a'}). Nothing was committed.")
        raise SystemExit(1) from None
    print("Done.")


if __name__ == "__main__":
    main()
