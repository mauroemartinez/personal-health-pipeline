"""Run the whole pipeline with one command.

Real data (default):
  1. import_mifitness.py   data/private/mi_fitness_export/ → data/processed/mi_fitness/
  2. load_supabase.py      --apply, only if HEALTH_DATABASE_URL is set in .env (skip with --skip-load)
  3. weekly_digest.py      → data/reports/weekly/<ISO week>.md

Try it without a Mi Band (--sample):
  1. make_sample_export.py synthetic export → data/sample/mi_fitness_export/
  2. import_mifitness.py   → data/processed/sample/
  3. weekly_digest.py      → data/reports/sample/<ISO week>.md
  Synthetic data is never loaded into Supabase by this script.

Stops at the first failing step. Each step prints counts and paths only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run(title: str, script: str, *args: str) -> None:
    print(f"\n== {title}", flush=True)
    result = subprocess.run([sys.executable, str(SCRIPTS / script), *args], cwd=ROOT)
    if result.returncode != 0:
        print(f"\nStopped: {script} failed (exit code {result.returncode}).", flush=True)
        raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", action="store_true", help="Run end to end on synthetic data instead of your export")
    parser.add_argument("--skip-load", action="store_true", help="Do not upload to Supabase")
    parser.add_argument("--week", help="ISO week for the digest, e.g. 2026-W46 (default: last complete week)")
    args = parser.parse_args()
    load_env(ROOT / ".env")
    digest_week = ["--week", args.week] if args.week else []

    if args.sample:
        run("1/3 Generate synthetic export", "make_sample_export.py")
        run("2/3 Normalize", "import_mifitness.py",
            "--input", "data/sample/mi_fitness_export", "--output", "data/processed/sample")
        run("3/3 Weekly digest", "weekly_digest.py",
            "--input", "data/processed/sample", "--output-dir", "data/reports/sample", *digest_week)
        print("\nDone (synthetic data). Open the digest under data/reports/sample/.")
        return

    run("1/3 Normalize", "import_mifitness.py")
    if args.skip_load:
        print("\n== 2/3 Load into Supabase\n  skipped (--skip-load)")
    elif not os.environ.get("HEALTH_DATABASE_URL"):
        print("\n== 2/3 Load into Supabase\n  skipped (HEALTH_DATABASE_URL not set in .env)")
    else:
        run("2/3 Load into Supabase", "load_supabase.py", "--apply")
    run("3/3 Weekly digest", "weekly_digest.py", *digest_week)
    print("\nDone.")


if __name__ == "__main__":
    main()
