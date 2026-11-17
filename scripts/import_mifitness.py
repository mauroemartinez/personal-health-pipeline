"""Normalize a Mi Fitness account export into private, idempotent NDJSON files.

Reads only the health CSVs needed for analysis. Never reads GPX tracks, member
profiles, device settings or social (PK) records. Drops account/device
identifiers (Uid, Sid, Did, sid, dbKey, sidList) before writing anything.

Idempotency: every record gets a deterministic id derived from its natural key
(metric + timestamp, metric + date, sport + start time), output is sorted, and
files are replaced atomically. Re-running over the same export produces
byte-identical output; running over a newer export updates values in place.

Console output contains counts only, never biometric values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "private" / "mi_fitness_export"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "mi_fitness"
SOURCE = "mi_fitness"
SCHEMA_VERSION = 2

# Keys that identify the account or a device. Removed recursively from payloads.
IDENTIFIER_KEYS = {
    "uid", "sid", "did", "dbkey", "sidlist", "mac", "xiaomiid", "identifier",
    "deviceid", "device_id", "device", "devicename", "device_name", "model", "serial", "sn",
}
# Plausible epoch range (2000-01-01 .. 2100-01-01); anything else is treated as invalid.
MIN_EPOCH, MAX_EPOCH = 946_684_800, 4_102_444_800

# metric -> (payload field holding the primary value, unit, plausible range)
OBSERVATION_VALUE = {
    "heart_rate": ("bpm", "bpm", (25, 240)),
    "resting_heart_rate": ("bpm", "bpm", (25, 150)),
    "stress": ("stress", "score_0_100", (0, 100)),
    "steps": ("steps", "steps", (0, 100_000)),
    "calories": ("calories", "kcal", (0, 20_000)),
    "weight": ("weight", "kg", (20, 300)),
    "sleep": ("duration", "min", (0, 1_440)),
    "intensity": (None, None, None),     # one row per active minute marker
    "valid_stand": (None, None, None),   # start/end interval
}

ACTIVITY_COLUMNS = {
    # output column: payload field
    "duration_s": "duration",
    "valid_duration_s": "valid_duration",
    "distance_m": "distance",
    "calories_kcal": "calories",
    "steps": "steps",
    "avg_hr": "avg_hrm",
    "max_hr": "max_hrm",
    "min_hr": "min_hrm",
    "train_load": "train_load",
    "train_effect": "train_effect",
    "vo2_max": "vo2_max",
}


def load_env(path: Path) -> None:
    """Minimal .env reader (no dependency). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def default_offset() -> timedelta:
    """UTC offset for records without a timezone field (HEALTH_UTC_OFFSET, e.g. +01:00)."""
    raw = os.environ.get("HEALTH_UTC_OFFSET", "+00:00").strip() or "+00:00"
    sign = -1 if raw.startswith("-") else 1
    hours, _, minutes = raw.lstrip("+-").partition(":")
    return sign * timedelta(hours=int(hours), minutes=int(minutes or 0))


def stable_id(*parts: Any) -> str:
    encoded = json.dumps([SOURCE, *parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def epoch_seconds(value: Any) -> int | None:
    """Accept epoch seconds/ms/us/ns as int or numeric string."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    magnitude = abs(number)
    for threshold, divisor in ((10**17, 10**9), (10**14, 10**6), (10**11, 10**3), (10**8, 1)):
        if magnitude >= threshold:
            seconds = number // divisor
            return seconds if MIN_EPOCH <= seconds < MAX_EPOCH else None
    return None


def iso_utc(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def local_date(seconds: int, offset: timedelta) -> str:
    return (datetime.fromtimestamp(seconds, tz=timezone.utc) + offset).date().isoformat()


def payload_offset(payload: dict[str, Any], fallback: timedelta) -> timedelta:
    """Mi Fitness stores `timezone` in 15-minute units (e.g. 4 == UTC+01:00)."""
    tz = payload.get("timezone")
    if isinstance(tz, int) and not isinstance(tz, bool) and -56 <= tz <= 56:
        return timedelta(minutes=15 * tz)
    return fallback


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k.lower() not in IDENTIFIER_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def read_rows(folder: Path, suffix: str) -> Iterator[dict[str, str]]:
    """Yield rows from every matching CSV, recursively (supports several exports side by side)."""
    for path in sorted(folder.rglob(f"*_{suffix}.csv")):
        if path.stat().st_size == 0:
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)


def load_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


class Collector:
    """Keeps one record per id; on collision the most recently updated source row wins."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.replaced = 0

    def add(self, record: dict[str, Any]) -> None:
        current = self.records.get(record["id"])
        if current is not None:
            self.replaced += 1
            if (current.get("source_updated_at") or "") > (record.get("source_updated_at") or ""):
                return
        self.records[record["id"]] = record

    def sorted(self, *keys: str) -> list[dict[str, Any]]:
        return sorted(self.records.values(), key=lambda r: tuple(str(r.get(k) or "") for k in keys))


def normalize(folder: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    fallback = default_offset()
    skipped: Counter[str] = Counter()
    observations, summaries, activities = Collector(), Collector(), Collector()

    # 1) Point-in-time observations (intraday series, sleep sessions, weight).
    for row in read_rows(folder, "hlth_center_fitness_data"):
        metric = (row.get("Key") or "").strip()
        seconds = epoch_seconds(row.get("Time"))
        payload = load_json(row.get("Value"))
        if metric not in OBSERVATION_VALUE:
            skipped["observation:unknown_metric"] += 1
            continue
        if seconds is None or not isinstance(payload, dict):
            skipped[f"observation:{metric}:unparseable"] += 1
            continue
        field, unit, bounds = OBSERVATION_VALUE[metric]
        value = number(payload.get(field)) if field else None
        if bounds and (value is None or not bounds[0] <= value <= bounds[1]):
            skipped[f"observation:{metric}:out_of_range"] += 1
            continue
        offset = payload_offset(payload, fallback)
        # A night of sleep belongs to the day you wake up.
        anchor = epoch_seconds(payload.get("wake_up_time")) if metric == "sleep" else None
        clean = scrub(payload)
        clean.pop("time", None)  # duplicated in measured_at
        observations.add({
            "id": stable_id("observation", metric, seconds),
            "source": SOURCE,
            "metric": metric,
            "measured_at": iso_utc(seconds),
            "local_date": local_date(anchor or seconds, offset),
            "value": value,
            "unit": unit,
            "payload": clean,
            "source_updated_at": iso_utc(epoch_seconds(row.get("UpdateTime"))),
        })

    # 2) Daily summaries computed by Mi Fitness. `daily_report` rows carry metrics and
    #    `daily_fitness` carries goals; `daily_mark` rows are just {has_data: bool} flags.
    #    Non-report tags get their own metric name so they can never overwrite a report.
    for row in read_rows(folder, "hlth_center_aggregated_fitness_data"):
        metric = (row.get("Key") or "").strip()
        tag = (row.get("Tag") or "").strip()
        seconds = epoch_seconds(row.get("Time"))
        payload = load_json(row.get("Value"))
        if tag == "daily_mark":
            continue
        if tag != "daily_report" and metric != "goal":
            metric = f"{metric}:{tag or 'untagged'}"
        if not metric or seconds is None or not isinstance(payload, dict):
            skipped["daily_summary:unparseable"] += 1
            continue
        # The day label is encoded as UTC midnight of the local calendar date.
        day = datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        clean = scrub(payload)
        clean.pop("date_time", None)
        summaries.add({
            "id": stable_id("daily_summary", metric, day),
            "source": SOURCE,
            "metric": metric,
            "local_date": day,
            "payload": clean,
            "source_updated_at": iso_utc(epoch_seconds(row.get("UpdateTime"))),
        })

    # 3) Workouts. Route/GPX data lives in a different file and is never read.
    for row in read_rows(folder, "hlth_center_sport_record"):
        sport = (row.get("Key") or "").strip() or "unknown"
        payload = load_json(row.get("Value"))
        if not isinstance(payload, dict):
            skipped["activity:unparseable"] += 1
            continue
        start = epoch_seconds(payload.get("start_time")) or epoch_seconds(row.get("Time"))
        end = epoch_seconds(payload.get("end_time"))
        if start is None:
            skipped["activity:no_start"] += 1
            continue
        clean = scrub(payload)
        for redundant in ("time", "start_time", "end_time"):
            clean.pop(redundant, None)
        record = {
            "id": stable_id("activity", sport, start),
            "source": SOURCE,
            "sport": sport,
            "category": (row.get("Category") or "").strip() or None,
            "started_at": iso_utc(start),
            "ended_at": iso_utc(end),
            "local_date": local_date(start, payload_offset(payload, fallback)),
        }
        for column, field in ACTIVITY_COLUMNS.items():
            record[column] = number(payload.get(field))
        record["payload"] = clean
        record["source_updated_at"] = iso_utc(epoch_seconds(row.get("UpdateTime")))
        activities.add(record)

    tables = {
        "observations": observations.sorted("measured_at", "metric"),
        "daily_summaries": summaries.sorted("local_date", "metric"),
        "activities": activities.sorted("started_at", "sport"),
    }
    by_metric: dict[str, Counter[str]] = {
        "observations": Counter(r["metric"] for r in tables["observations"]),
        "daily_summaries": Counter(r["metric"] for r in tables["daily_summaries"]),
        "activities": Counter(r["sport"] for r in tables["activities"]),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "counts": {name: len(rows) for name, rows in tables.items()},
        "counts_by_metric": {name: dict(sorted(c.items())) for name, c in by_metric.items()},
        "skipped": dict(sorted(skipped.items())),
        "duplicates_resolved": observations.replaced + summaries.replaced + activities.replaced,
        "local_date_range": date_range(tables),
    }
    return tables, manifest


def date_range(tables: dict[str, list[dict[str, Any]]]) -> dict[str, str] | None:
    days = [r["local_date"] for rows in tables.values() for r in rows if r.get("local_date")]
    return {"first": min(days), "last": max(days)} if days else None


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_outputs(output: Path, tables: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]) -> None:
    for name, rows in tables.items():
        text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
        write_atomic(output / f"{name}.jsonl", text)
    write_atomic(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def ensure_private(output: Path) -> None:
    """Refuse to write outside data/ folders that .gitignore excludes."""
    allowed = [ROOT / "data" / "processed", ROOT / "data" / "private"]
    resolved = output.resolve()
    if not any(resolved == a or a in resolved.parents for a in allowed):
        raise SystemExit(f"Refusing to write personal data outside data/processed or data/private: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Unzipped Mi Fitness export folder")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Private output folder")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report counts without writing")
    parser.add_argument("--allow-empty", action="store_true", help="Write output even if no records were found")
    args = parser.parse_args()
    load_env(ROOT / ".env")
    if not args.input.is_dir():
        parser.error(f"Mi Fitness export folder not found: {args.input}")
    ensure_private(args.output)

    tables, manifest = normalize(args.input)
    if not any(manifest["counts"].values()) and not args.allow_empty:
        parser.error("No records found (is the export still zipped, or in another folder?). "
                     "Existing output was left untouched; pass --allow-empty to overwrite anyway.")
    if not args.dry_run:
        write_outputs(args.output, tables, manifest)

    print("Mi Fitness import" + (" (dry run)" if args.dry_run else "") + ":")
    for name, count in manifest["counts"].items():
        print(f"  {name}: {count}")
    print(f"  skipped: {sum(manifest['skipped'].values())}  duplicates resolved: {manifest['duplicates_resolved']}")
    if not args.dry_run:
        print(f"  output: {args.output.resolve().relative_to(ROOT)}")


if __name__ == "__main__":
    main()
