"""Print a privacy-safe structural summary of a local Mi Fitness export.

Reads only the three health CSVs the importer uses (never GPX routes, profiles
or device files) and prints column names, row counts and known metric labels.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "private" / "mi_fitness_export"
FILES = ("hlth_center_fitness_data", "hlth_center_aggregated_fitness_data", "hlth_center_sport_record")
LABEL_COLUMNS = ("Key", "Tag", "Category")
# Only these known labels are reported; any other value could be an identifier.
KNOWN_LABELS = {
    "heart_rate", "stress", "steps", "calories", "intensity", "valid_stand", "sleep",
    "resting_heart_rate", "weight", "goal", "daily_report", "daily_mark", "daily_fitness",
    "outdoor_walking", "indoor_running", "strength_training", "walking", "running",
}


def inspect_csv(path: Path, name: str) -> None:
    counts: Counter[str] = Counter()
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        for row in reader:
            rows += 1
            for column in LABEL_COLUMNS:
                value = (row.get(column) or "").strip()
                if value in KNOWN_LABELS:
                    counts[f"{column}={value}"] += 1
    print(f"{name}: {rows} rows; columns={','.join(columns)}")
    if counts:
        print("  labels: " + ", ".join(f"{k}:{n}" for k, n in sorted(counts.items())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_INPUT,
                        help="Folder containing the unzipped Mi Fitness export")
    args = parser.parse_args()
    if not args.path.is_dir():
        parser.error(f"folder not found: {args.path}")
    found = 0
    for name in FILES:
        for path in sorted(args.path.rglob(f"*_{name}.csv")):
            if path.stat().st_size:
                inspect_csv(path, name)
                found += 1
    if not found:
        parser.error(f"no Mi Fitness health CSVs found in: {args.path}")


if __name__ == "__main__":
    main()
