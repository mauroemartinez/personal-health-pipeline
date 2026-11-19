"""Build a self-contained HTML dashboard from the normalized data.

Input:  data/processed/mi_fitness/*.jsonl (written by import_mifitness.py)
Look:   dashboard/theme.json (colors for dark/light, fonts, titles) + dashboard/template.html (layout, charts)
Output: data/reports/dashboard.html (ignored by Git). One file; works offline (fonts fall back to system ones).

  python scripts/dashboard.py                                  # your data, Spanish
  python scripts/dashboard.py --lang en
  python scripts/dashboard.py --theme-file my-theme.json       # your own brand
  python scripts/dashboard.py --input data/processed/sample --output data/reports/sample/dashboard.html
  python scripts/dashboard.py ... --png assets/dashboard-en.png --png-height 1100   # screenshot (needs Playwright)

Real data stays under data/reports/. A screenshot outside data/reports/ is only allowed for
synthetic sample data, so a picture of real health data can't end up in the public repo by mistake.

HEALTH_CALIBRATION_DAYS in .env (optional) shades the first N days of history as band calibration.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "mi_fitness"
DEFAULT_OUTPUT = ROOT / "data" / "reports" / "dashboard.html"
REPORTS = ROOT / "data" / "reports"
TEMPLATE = ROOT / "dashboard" / "template.html"
DEFAULT_THEME = ROOT / "dashboard" / "theme.json"

THEME_KEYS = ["page", "surface", "surface-2", "text", "text-2", "muted", "grid", "axis", "border", "accent",
              "series-1", "series-2", "series-3", "de-emphasis", "good", "bad", "calibration"]
SAFE_VALUE = re.compile(r"^[#\w\s().,%-]+$")  # colors like #0da4cf or rgba(5, 30, 69, 0.12)
SAFE_FONT = re.compile(r"^[\w\s'\",.-]+$")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def hours(minutes: Any) -> float | None:
    n = num(minutes)
    return None if n is None else round(n / 60, 2)


def build_rows(folder: Path) -> list[dict[str, Any]]:
    """One row per local date with the metrics the dashboard draws. Missing stays missing."""
    days: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in read_jsonl(folder / "daily_summaries.jsonl"):
        d, p, m = row["local_date"], row["payload"], row["metric"]
        if m == "sleep":
            days[d].update(sleep_h=hours(p.get("total_duration")), deep_h=hours(p.get("sleep_deep_duration")),
                           light_h=hours(p.get("sleep_light_duration")), rem_h=hours(p.get("sleep_rem_duration")),
                           sleep_score=num(p.get("sleep_score")))
        elif m == "stress":
            days[d]["stress"] = num(p.get("avg_stress"))
        elif m == "heart_rate":
            days[d].update(avg_hr=num(p.get("avg_hr")), rhr=num(p.get("avg_rhr")))
        elif m == "steps":
            dist = num(p.get("distance"))
            days[d].update(steps=num(p.get("steps")), km=None if dist is None else round(dist / 1000, 2))
        elif m == "intensity":
            days[d]["active_min"] = num(p.get("duration"))
    for row in read_jsonl(folder / "observations.jsonl"):
        d = row["local_date"]
        if row["metric"] == "resting_heart_rate" and days[d].get("rhr") is None:
            days[d]["rhr"] = num(row["value"])
        elif row["metric"] == "weight":
            days[d]["weight"] = num(row["value"])  # input is sorted: last weigh-in of the day wins
    for act in read_jsonl(folder / "activities.jsonl"):
        dur, dist = num(act.get("duration_s")), num(act.get("distance_m"))
        days[act["local_date"]].setdefault("workouts", []).append({
            "sport": act.get("sport"),
            "min": None if dur is None else round(dur / 60),
            "km": None if not dist else round(dist / 1000, 2),
            "hr": num(act.get("avg_hr")),
        })
    rows = []
    for d in sorted(days):
        values = {k: v for k, v in days[d].items() if v is not None}
        if values:
            rows.append({"date": d, **values})
    return rows


def load_theme(path: Path) -> dict[str, Any]:
    theme = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for mode in ("dark", "light"):
        colors = theme.get("modes", {}).get(mode, {})
        problems += [f"modes.{mode}.{k} is missing" for k in THEME_KEYS if k not in colors]
        problems += [f"modes.{mode}.{k} has an unexpected value" for k, v in colors.items()
                     if not isinstance(v, str) or not SAFE_VALUE.match(v)]
    for key in ("body", "display"):
        if not SAFE_FONT.match(theme.get("fonts", {}).get(key, "")):
            problems.append(f"fonts.{key} is missing or has unexpected characters")
    sheet = theme.get("fonts", {}).get("stylesheet", "")
    if sheet and not sheet.startswith("https://"):
        problems.append("fonts.stylesheet must be an https:// URL (or empty)")
    if theme.get("defaultMode") not in ("dark", "light"):
        problems.append('defaultMode must be "dark" or "light"')
    if problems:
        raise SystemExit(f"Invalid theme {path}:\n  - " + "\n  - ".join(problems))
    return theme


def theme_css(theme: dict[str, Any]) -> str:
    fonts = theme["fonts"]
    blocks = []
    for mode in ("dark", "light"):
        tokens = "".join(f"  --{k}: {v};\n" for k, v in theme["modes"][mode].items())
        blocks.append(f':root[data-theme="{mode}"] {{\n  color-scheme: {mode};\n{tokens}'
                      f"  --font-body: {fonts['body']};\n  --font-display: {fonts['display']};\n}}")
    return "\n".join(blocks)


def render_html(rows: list[dict[str, Any]], theme: dict[str, Any], lang: str, synthetic: bool,
                calibration_days: int) -> str:
    title = theme.get("title", {}).get(lang) or "Health dashboard"
    meta = {"lang": lang, "synthetic": synthetic, "calibrationDays": calibration_days, "title": title,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    payload = json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # never close the <script> tag from inside the data
    sheet = theme["fonts"].get("stylesheet", "")
    font_link = f'<link rel="stylesheet" href="{html.escape(sheet)}">' if sheet else ""
    page = TEMPLATE.read_text(encoding="utf-8")
    for key, value in {"__LANG__": lang, "__DEFAULT_MODE__": theme["defaultMode"], "__TITLE__": html.escape(title),
                       "__FONT_LINK__": font_link, "__THEME_CSS__": theme_css(theme)}.items():
        page = page.replace(key, value)
    return page.replace("__DATA__", payload)


def screenshot(page: Path, png: Path, mode: str | None, width: int, height: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            tab = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
            tab.goto(page.as_uri(), wait_until="networkidle")
            if mode:
                tab.evaluate("t => { document.documentElement.dataset.theme = t; }", mode)
            tab.evaluate("document.fonts.ready.then(() => { window.dispatchEvent(new Event('resize')); })")
            tab.wait_for_timeout(500)
            if height:
                tab.screenshot(path=str(png), full_page=True, clip={"x": 0, "y": 0, "width": width, "height": height})
            else:
                tab.screenshot(path=str(png), full_page=True)
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--lang", choices=("es", "en"), default="es")
    parser.add_argument("--theme-file", type=Path, default=DEFAULT_THEME, help="Brand colors and fonts (JSON)")
    parser.add_argument("--calibration-days", type=int, help="Shade the first N days (default: HEALTH_CALIBRATION_DAYS or 0)")
    parser.add_argument("--png", type=Path, help="Also save a screenshot (needs Playwright)")
    parser.add_argument("--mode", choices=("light", "dark"), help="Color mode for --png (default: the theme's)")
    parser.add_argument("--width", type=int, default=1440, help="Viewport width for --png")
    parser.add_argument("--png-height", type=int, default=0, help="Crop --png to this height (0 = full page)")
    args = parser.parse_args()
    load_env(ROOT / ".env")

    out = args.output.resolve()
    if REPORTS.resolve() not in out.parents:
        parser.error("The dashboard embeds personal data; --output must be under data/reports/ (ignored by Git).")
    rows = build_rows(args.input)
    if not rows:
        parser.error(f"No normalized data in {args.input}. Run scripts/import_mifitness.py first.")
    manifest = args.input / "manifest.json"
    synthetic = manifest.exists() and bool(json.loads(manifest.read_text(encoding="utf-8")).get("synthetic"))
    if args.png and not synthetic and REPORTS.resolve() not in args.png.resolve().parents:
        parser.error("Screenshots of real data must stay under data/reports/. Use synthetic data for public images.")
    calibration = args.calibration_days
    if calibration is None:
        calibration = 0 if synthetic else int(os.environ.get("HEALTH_CALIBRATION_DAYS", "0") or 0)

    theme = load_theme(args.theme_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(rows, theme, args.lang, synthetic, max(calibration, 0)), encoding="utf-8")
    print(f"Dashboard ({theme.get('name', 'custom')} theme): {len(rows)} days -> {out.relative_to(ROOT)}")
    if args.png:
        args.png.parent.mkdir(parents=True, exist_ok=True)
        screenshot(out, args.png.resolve(), args.mode, args.width, args.png_height)
        print(f"Screenshot ({args.mode or theme['defaultMode']}) -> {args.png}")


if __name__ == "__main__":
    main()
