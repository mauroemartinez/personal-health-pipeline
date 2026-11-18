"""Build a self-contained HTML dashboard from the normalized data.

Input:  data/processed/mi_fitness/*.jsonl (written by import_mifitness.py)
Output: data/reports/dashboard.html (ignored by Git). One file, no dependencies, works offline.

  python scripts/dashboard.py                                  # your data, Spanish
  python scripts/dashboard.py --lang en
  python scripts/dashboard.py --input data/processed/sample --output data/reports/sample/dashboard.html
  python scripts/dashboard.py ... --png assets/dashboard-en.png --theme dark   # screenshot (needs Playwright)

Real data stays under data/reports/. A screenshot outside data/reports/ is only allowed for
synthetic sample data, so a picture of real health data can't end up in the public repo by mistake.

HEALTH_CALIBRATION_DAYS in .env (optional) shades the first N days of history as band calibration.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "mi_fitness"
DEFAULT_OUTPUT = ROOT / "data" / "reports" / "dashboard.html"
REPORTS = ROOT / "data" / "reports"


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
    days: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in read_jsonl(folder / "daily_summaries.jsonl"):
        d, p, m = row["local_date"], row["payload"], row["metric"]
        if m == "sleep":
            days[d].update(sleep_h=hours(p.get("total_duration")), deep_h=hours(p.get("sleep_deep_duration")),
                           light_h=hours(p.get("sleep_light_duration")), rem_h=hours(p.get("sleep_rem_duration")),
                           awake_h=hours(p.get("sleep_awake_duration")), sleep_score=num(p.get("sleep_score")))
        elif m == "stress":
            days[d]["stress"] = num(p.get("avg_stress"))
            scale = p.get("stress_scale") if isinstance(p.get("stress_scale"), dict) else {}
            parts = {k: num(v) or 0.0 for k, v in scale.items()}
            total = sum(parts.values())
            if total:
                days[d]["stress_high_pct"] = round(100 * (parts.get("moderate", 0) + parts.get("severe", 0)) / total, 1)
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


def render_html(rows: list[dict[str, Any]], lang: str, synthetic: bool, calibration_days: int) -> str:
    meta = {"lang": lang, "synthetic": synthetic, "calibrationDays": calibration_days,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    payload = json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # never close the <script> tag from inside the data
    return TEMPLATE.replace("__LANG__", lang).replace("__DATA__", payload)


def screenshot(html: Path, png: Path, theme: str, width: int, height: int = 0) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            tab = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
            tab.goto(html.as_uri(), wait_until="load")
            tab.evaluate("t => { document.documentElement.dataset.theme = t; window.dispatchEvent(new Event('resize')); }", theme)
            tab.wait_for_timeout(400)
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
    parser.add_argument("--calibration-days", type=int, help="Shade the first N days (default: HEALTH_CALIBRATION_DAYS or 0)")
    parser.add_argument("--png", type=Path, help="Also save a full-page screenshot (needs Playwright)")
    parser.add_argument("--theme", choices=("light", "dark"), default="light", help="Theme for --png")
    parser.add_argument("--width", type=int, default=1280, help="Viewport width for --png")
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

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(rows, args.lang, synthetic, max(calibration, 0)), encoding="utf-8")
    print(f"Dashboard: {len(rows)} days -> {out.relative_to(ROOT)}")
    if args.png:
        args.png.parent.mkdir(parents=True, exist_ok=True)
        screenshot(out, args.png.resolve(), args.theme, args.width, args.png_height)
        print(f"Screenshot ({args.theme}) -> {args.png}")


TEMPLATE = r"""<!doctype html>
<html lang="__LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Health dashboard</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --text: #0b0b0b; --text-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --deemph: #c9c7bf;
  --good: #006300; --bad: #d03b3b; --calib: rgba(11,11,11,0.045); --hover: rgba(11,11,11,0.05);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --text: #f0efec; --text-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --deemph: #4a4945;
    --good: #0ca30c; --bad: #d03b3b; --calib: rgba(255,255,255,0.045); --hover: rgba(255,255,255,0.06);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --text: #f0efec; --text-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --deemph: #4a4945;
  --good: #0ca30c; --bad: #d03b3b; --calib: rgba(255,255,255,0.045); --hover: rgba(255,255,255,0.06);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }
main { max-width: 1240px; margin: 0 auto; padding: 24px 16px 40px; }
header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px; }
h1 { margin: 0; font-size: 26px; font-weight: 650; letter-spacing: -.2px; }
.subtitle { color: var(--text-2); margin-top: 2px; }
.banner { margin-top: 12px; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--surface); color: var(--text-2); }
.controls { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 16px; align-items: center; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: var(--surface); }
.seg button, .theme { font: inherit; color: var(--text-2); background: transparent; border: 0; padding: 7px 12px; cursor: pointer; }
.seg button + button { border-left: 1px solid var(--border); }
.seg button[aria-pressed="true"] { color: var(--text); font-weight: 650; background: var(--hover); }
.theme { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); margin-left: auto; }
button:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--s1); outline-offset: 2px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.tile .label { color: var(--text-2); font-size: 13px; }
.tile .value { font-size: 28px; font-weight: 650; margin-top: 2px; }
.tile .value small { font-size: 15px; font-weight: 500; color: var(--text-2); margin-left: 3px; }
.tile .delta { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.tile .delta.good { color: var(--good); } .tile .delta.bad { color: var(--bad); }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px 10px; min-width: 0; }
.card.wide { grid-column: 1 / -1; }
.card h2 { margin: 0; font-size: 15px; font-weight: 650; }
.card .note { color: var(--text-2); font-size: 12.5px; margin-top: 1px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 8px; color: var(--text-2); font-size: 12.5px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.legend i.line { height: 2px; width: 14px; border-radius: 1px; }
.plot { margin-top: 6px; position: relative; }
.plot svg { display: block; overflow: visible; }
.plot text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.plot text.lbl { fill: var(--text-2); font-size: 11.5px; }
.plot text.calib { fill: var(--muted); font-size: 10.5px; }
.hit { fill: transparent; cursor: default; }
.hit:focus { outline: none; }
#tip { position: fixed; pointer-events: none; z-index: 10; min-width: 150px; max-width: 280px; padding: 8px 10px;
  background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0,0,0,.18); font-size: 12.5px; display: none; }
#tip .t { color: var(--text-2); margin-bottom: 4px; }
#tip .r { display: flex; align-items: center; gap: 7px; margin-top: 2px; }
#tip .r i { width: 12px; height: 2px; border-radius: 1px; flex: 0 0 12px; }
#tip .r b { font-weight: 650; font-variant-numeric: tabular-nums; }
#tip .r span { color: var(--text-2); }
section.tables { margin-top: 12px; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; font-size: 13px; }
th, td { padding: 6px 8px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--text-2); font-weight: 600; }
.scroll { overflow-x: auto; margin-top: 8px; }
details summary { cursor: pointer; font-weight: 650; }
footer { color: var(--muted); font-size: 12px; margin-top: 18px; }
@media (max-width: 760px) { .grid { grid-template-columns: minmax(0, 1fr); } .theme { margin-left: 0; } }
</style>
</head>
<body>
<main>
  <header>
    <div><h1 id="title"></h1><div class="subtitle" id="subtitle"></div></div>
  </header>
  <div class="banner" id="banner" hidden></div>
  <div class="controls">
    <div class="seg" id="ranges" role="group"></div>
    <button class="theme" id="theme" type="button"></button>
  </div>
  <section class="kpis" id="kpis"></section>
  <section class="grid">
    <div class="card wide" id="c-sleep"><h2></h2><div class="note"></div><div class="legend"></div><div class="plot"></div></div>
    <div class="card" id="c-stress"><h2></h2><div class="note"></div><div class="plot"></div></div>
    <div class="card" id="c-rhr"><h2></h2><div class="note"></div><div class="plot"></div></div>
    <div class="card wide" id="c-steps"><h2></h2><div class="note"></div><div class="legend"></div><div class="plot"></div></div>
    <div class="card wide" id="c-weight"><h2></h2><div class="note"></div><div class="plot"></div></div>
  </section>
  <section class="tables">
    <div class="card"><h2 id="t-week-title"></h2><div class="scroll"><table id="t-week"></table></div></div>
    <div class="card" style="margin-top:12px"><details><summary id="t-day-title"></summary>
      <div class="scroll"><table id="t-day"></table></div></details></div>
  </section>
  <footer id="footer"></footer>
</main>
<div id="tip" role="status" aria-live="polite"></div>
<script>
const DATA = __DATA__;
const I18N = {
  es: { locale: "es-AR", title: "Mi salud", subtitle: (a, b, n) => `${a} → ${b} · ${n} días con datos · export de Mi Fitness`,
    synthetic: "Datos sintéticos de ejemplo (generados con scripts/make_sample_export.py). No son datos reales.",
    ranges: [["all", "Todo"], [28, "Últimas 4 semanas"], [14, "Últimas 2 semanas"]], dark: "Modo oscuro", light: "Modo claro",
    kSleep: "Sueño por noche", kScore: "Puntaje de sueño", kRhr: "Pulso en reposo", kStress: "Estrés medio",
    kSteps: "Pasos por día", kWeight: "Peso (último)", vsPrev: n => `vs. ${n} días previos`, noPrev: "sin período previo",
    inRange: "en el período",
    sleepT: "Etapas del sueño por noche", sleepN: "Horas por etapa. La noche se asigna al día en que te despertaste.",
    deep: "Profundo", rem: "REM", light_: "Liviano", total: "Total", score: "Puntaje",
    stressT: "Estrés del día y sueño de esa noche", stressN: (r, n) => n >= 5 ? `Correlación r = ${r} con ${n} noches: es una pista, no una conclusión.` : "Hacen falta más noches para ver una relación.",
    stressX: "Estrés medio del día (0–100)", stressY: "Puntaje de sueño de esa noche", stressTip: "Estrés del día", scoreTip: "Puntaje de sueño de esa noche",
    rhrT: "Pulso en reposo", rhrN: "Latidos por minuto. Una baja sostenida suele acompañar una mejor forma aeróbica.",
    calib: "calibración", stepsT: "Pasos por día", stepsN: "Los días con entrenamiento registrado van resaltados.",
    withW: "Con entrenamiento", withoutW: "Sin entrenamiento", steps: "pasos", km: "km", active: "min activos",
    weightT: "Peso", weightN: n => `${n} pesajes en el período.`, kg: "kg", bpm: "lpm", h: "h",
    weekT: "Semana a semana", dayT: "Ver todos los días (tabla)",
    cols: { week: "Semana", days: "Días", steps: "Pasos/día", sleep: "Sueño (h)", deep: "Profundo (h)", rem: "REM (h)", light: "Liviano (h)",
      score: "Puntaje", rhr: "Pulso reposo", stress: "Estrés", workouts: "Entrenos", weight: "Peso (kg)", date: "Fecha", km: "Km", active: "Min activos" },
    sports: { outdoor_walking: "Caminata", indoor_running: "Cinta", strength_training: "Fuerza" },
    footer: g => `Generado el ${g}. Valores estimados por un wearable de muñeca, no son mediciones clínicas.` },
  en: { locale: "en-US", title: "My health", subtitle: (a, b, n) => `${a} → ${b} · ${n} days with data · Mi Fitness export`,
    synthetic: "Synthetic sample data (generated with scripts/make_sample_export.py). Not real health data.",
    ranges: [["all", "All"], [28, "Last 4 weeks"], [14, "Last 2 weeks"]], dark: "Dark mode", light: "Light mode",
    kSleep: "Sleep per night", kScore: "Sleep score", kRhr: "Resting heart rate", kStress: "Average stress",
    kSteps: "Steps per day", kWeight: "Weight (latest)", vsPrev: n => `vs. previous ${n} days`, noPrev: "no previous period",
    inRange: "in this period",
    sleepT: "Sleep stages per night", sleepN: "Hours per stage. Each night belongs to the day you woke up.",
    deep: "Deep", rem: "REM", light_: "Light", total: "Total", score: "Score",
    stressT: "Daytime stress and that night's sleep", stressN: (r, n) => n >= 5 ? `Correlation r = ${r} over ${n} nights: a hint, not a conclusion.` : "More nights are needed to see a relationship.",
    stressX: "Average stress of the day (0–100)", stressY: "Sleep score that night", stressTip: "Stress that day", scoreTip: "Sleep score that night",
    rhrT: "Resting heart rate", rhrN: "Beats per minute. A sustained drop usually comes with better aerobic fitness.",
    calib: "calibration", stepsT: "Steps per day", stepsN: "Days with a recorded workout are highlighted.",
    withW: "With a workout", withoutW: "No workout", steps: "steps", km: "km", active: "active min",
    weightT: "Weight", weightN: n => `${n} weigh-ins in this period.`, kg: "kg", bpm: "bpm", h: "h",
    weekT: "Week by week", dayT: "Show every day (table)",
    cols: { week: "Week", days: "Days", steps: "Steps/day", sleep: "Sleep (h)", deep: "Deep (h)", rem: "REM (h)", light: "Light (h)",
      score: "Score", rhr: "Resting HR", stress: "Stress", workouts: "Workouts", weight: "Weight (kg)", date: "Date", km: "Km", active: "Active min" },
    sports: { outdoor_walking: "Walk", indoor_running: "Treadmill", strength_training: "Strength" },
    footer: g => `Generated on ${g}. Values estimated by a wrist wearable, not clinical measurements.` },
};
const L = I18N[DATA.meta.lang] || I18N.en;
const NS = "http://www.w3.org/2000/svg";
const ROWS = DATA.rows;
const BY_DATE = Object.fromEntries(ROWS.map(r => [r.date, r]));
const DAY = 86400000;
const toD = s => new Date(s + "T00:00:00Z");
const toS = d => d.toISOString().slice(0, 10);
const addDays = (s, n) => toS(new Date(toD(s).getTime() + n * DAY));
const fmtDate = new Intl.DateTimeFormat(L.locale, { day: "numeric", month: "short", timeZone: "UTC" });
const fmtLong = new Intl.DateTimeFormat(L.locale, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const nf = (v, d = 0) => v == null || Number.isNaN(v) ? "—" : new Intl.NumberFormat(L.locale, { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const FIRST = ROWS[0].date, LAST = ROWS[ROWS.length - 1].date;
const CALIB_END = DATA.meta.calibrationDays > 0 ? addDays(FIRST, DATA.meta.calibrationDays) : null;
let range = "all";

// ---------- helpers ----------
function el(tag, attrs, parent, text) {
  const e = tag.startsWith("svg:") ? document.createElementNS(NS, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function datesIn(a, b) { const out = []; for (let s = a; s <= b; s = addDays(s, 1)) out.push(s); return out; }
function windowDates() {
  const start = range === "all" ? FIRST : (addDays(LAST, -range + 1) < FIRST ? FIRST : addDays(LAST, -range + 1));
  return datesIn(start, LAST);
}
function prevDates() {
  if (range === "all") return [];
  const start = addDays(LAST, -2 * range + 1), end = addDays(LAST, -range);
  return end < FIRST ? [] : datesIn(start < FIRST ? FIRST : start, end);
}
const vals = (dates, k) => dates.map(d => BY_DATE[d] && BY_DATE[d][k]).filter(v => v != null);
const mean = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
function niceTicks(lo, hi, n = 4) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw)), f = raw / mag;
  const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  const ticks = []; for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-6; v += step) ticks.push(+v.toFixed(6));
  if (ticks[ticks.length - 1] < hi) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(6));
  return ticks;
}
function axisFmt(ticks) {
  const compact = Math.max(...ticks.map(Math.abs)) >= 10000;
  return v => v === 0 ? "0" : compact ? nf(v / 1000, v % 1000 ? 1 : 0) + "K" : nf(v, Number.isInteger(v) ? 0 : 1);
}

// ---------- tooltip ----------
const tip = document.getElementById("tip");
function showTip(target, title, lines, evt) {
  tip.textContent = "";
  el("div", { class: "t" }, tip, title);
  for (const [color, value, label] of lines) {
    const r = el("div", { class: "r" }, tip);
    const key = el("i", {}, r); key.style.background = color || "transparent";
    el("b", {}, r, value); if (label) el("span", {}, r, label);
  }
  tip.style.display = "block";
  const box = target.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
  let x = evt && evt.clientX != null ? evt.clientX + 14 : box.left + box.width / 2 + 10;
  let y = evt && evt.clientY != null ? evt.clientY - th - 12 : box.top - th - 8;
  if (x + tw > window.innerWidth - 8) x = (evt && evt.clientX != null ? evt.clientX : box.left) - tw - 14;
  if (y < 8) y = (evt && evt.clientY != null ? evt.clientY : box.bottom) + 16;
  tip.style.left = Math.max(8, x) + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.display = "none"; };

// ---------- time axis frame ----------
function timeFrame(card, dates, yTicks, height = 230, padRight = 18) {
  const host = card.querySelector(".plot"); host.textContent = "";
  const W = Math.max(host.clientWidth, 280), m = { l: 44, r: padRight, t: 12, b: 26 };
  const tickFmt = axisFmt(yTicks);
  const svg = el("svg:svg", { width: W, height, viewBox: `0 0 ${W} ${height}`, role: "img" }, host);
  const pw = W - m.l - m.r, ph = height - m.t - m.b, band = pw / dates.length;
  const lo = yTicks[0], hi = yTicks[yTicks.length - 1];
  const x = i => m.l + band * (i + 0.5), y = v => m.t + ph - (v - lo) / (hi - lo) * ph;
  if (CALIB_END && dates[0] < CALIB_END) {
    const n = dates.filter(d => d < CALIB_END).length;
    el("svg:rect", { x: m.l, y: m.t, width: band * n, height: ph, style: "fill:var(--calib)" }, svg);
    el("svg:text", { x: m.l + 4, y: m.t + 11, class: "calib" }, svg, L.calib);
  }
  for (const t of yTicks) {
    el("svg:line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), style: `stroke:var(${t === lo ? "--axis" : "--grid"});stroke-width:1`, "shape-rendering": "crispEdges" }, svg);
    el("svg:text", { x: m.l - 6, y: y(t) + 3.5, "text-anchor": "end" }, svg, tickFmt(t));
  }
  const every = Math.max(1, Math.ceil(dates.length / Math.max(2, Math.floor(pw / 62))));
  dates.forEach((d, i) => { if (i % every === 0) el("svg:text", { x: x(i), y: height - 8, "text-anchor": "middle" }, svg, fmtDate.format(toD(d))); });
  return { svg, W, m, pw, ph, band, x, y, height };
}
function hitBands(f, dates, onShow, onHide) {
  dates.forEach((d, i) => {
    const r = el("svg:rect", { x: f.m.l + f.band * i, y: f.m.t, width: f.band, height: f.ph, class: "hit", tabindex: 0 }, f.svg);
    const show = e => onShow(i, d, r, e), hide = () => { hideTip(); onHide && onHide(i); };
    r.addEventListener("pointermove", show); r.addEventListener("focus", show);
    r.addEventListener("pointerleave", hide); r.addEventListener("blur", hide);
  });
}
function colPath(x, w, y0, y1, round) {
  const h = y0 - y1; if (h <= 0.5) return "";
  const r = round ? Math.min(4, h, w / 2) : 0;
  return `M${x},${y0}V${y1 + r}${r ? `Q${x},${y1} ${x + r},${y1}` : ""}H${x + w - r}${r ? `Q${x + w},${y1} ${x + w},${y1 + r}` : ""}V${y0}Z`;
}
function legend(card, items) {
  const box = card.querySelector(".legend"); box.textContent = "";
  for (const [color, label, line] of items) { const s = el("span", {}, box); const i = el("i", line ? { class: "line" } : {}, s); i.style.background = color; s.append(label); }
}
function setHead(card, title, note) { card.querySelector("h2").textContent = title; card.querySelector(".note").textContent = note; }

// ---------- charts ----------
function sleepChart(dates) {
  const card = document.getElementById("c-sleep");
  setHead(card, L.sleepT, L.sleepN);
  const S = [["deep_h", "--s1", L.deep], ["rem_h", "--s2", L.rem], ["light_h", "--s3", L.light_]];
  legend(card, S.map(([, c, l]) => [`var(${c})`, l]));
  const max = Math.max(1, ...dates.map(d => S.reduce((a, [k]) => a + ((BY_DATE[d] || {})[k] || 0), 0)));
  const f = timeFrame(card, dates, niceTicks(0, max, 4));
  const w = Math.max(2, Math.min(24, f.band * 0.62));
  const groups = dates.map((d, i) => {
    const r = BY_DATE[d] || {}, g = el("svg:g", {}, f.svg);
    let base = 0; const present = S.filter(([k]) => r[k] > 0);
    present.forEach(([k, c], j) => {
      const top = base + r[k], last = j === present.length - 1;
      const y0 = f.y(base), y1 = f.y(top) + (last ? 0 : 2);
      const p = colPath(f.x(i) - w / 2, w, y0, y1, last); if (p) el("svg:path", { d: p, style: `fill:var(${c})` }, g);
      base = top;
    });
    return g;
  });
  hitBands(f, dates, (i, d, r, e) => {
    groups.forEach((g, j) => g.style.opacity = j === i ? 1 : 0.55);
    const x = BY_DATE[d] || {};
    showTip(r, fmtLong.format(toD(d)), [
      [null, `${nf(x.sleep_h, 1)} ${L.h}`, L.total], ["var(--s1)", `${nf(x.deep_h, 1)} ${L.h}`, L.deep],
      ["var(--s2)", `${nf(x.rem_h, 1)} ${L.h}`, L.rem], ["var(--s3)", `${nf(x.light_h, 1)} ${L.h}`, L.light_],
      [null, nf(x.sleep_score), L.score]], e);
  }, () => groups.forEach(g => g.style.opacity = 1));
}

function stressChart(dates) {
  const card = document.getElementById("c-stress");
  const pts = dates.map(d => ({ d, x: (BY_DATE[d] || {}).stress, y: (BY_DATE[addDays(d, 1)] || {}).sleep_score }))
    .filter(p => p.x != null && p.y != null);
  let r = null;
  if (pts.length >= 3) {
    const mx = mean(pts.map(p => p.x)), my = mean(pts.map(p => p.y));
    const sxy = pts.reduce((a, p) => a + (p.x - mx) * (p.y - my), 0), sxx = pts.reduce((a, p) => a + (p.x - mx) ** 2, 0), syy = pts.reduce((a, p) => a + (p.y - my) ** 2, 0);
    r = sxx && syy ? sxy / Math.sqrt(sxx * syy) : null;
    var slope = sxx ? sxy / sxx : 0, icpt = my - slope * mx;
  }
  setHead(card, L.stressT, L.stressN(r == null ? "—" : nf(r, 2), pts.length));
  const host = card.querySelector(".plot"); host.textContent = "";
  if (!pts.length) return;
  const H = 262, W = Math.max(host.clientWidth, 260), m = { l: 44, r: 16, t: 26, b: 40 };
  const svg = el("svg:svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img" }, host);
  const xt = niceTicks(Math.min(...pts.map(p => p.x)) - 2, Math.max(...pts.map(p => p.x)) + 2, 4);
  const yt = niceTicks(Math.min(...pts.map(p => p.y)) - 3, Math.max(...pts.map(p => p.y)) + 3, 4);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const X = v => m.l + (v - xt[0]) / (xt[xt.length - 1] - xt[0]) * pw, Y = v => m.t + ph - (v - yt[0]) / (yt[yt.length - 1] - yt[0]) * ph;
  const fy = axisFmt(yt), fx = axisFmt(xt);
  for (const t of yt) { el("svg:line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), style: `stroke:var(${t === yt[0] ? "--axis" : "--grid"})`, "shape-rendering": "crispEdges" }, svg); el("svg:text", { x: m.l - 6, y: Y(t) + 3.5, "text-anchor": "end" }, svg, fy(t)); }
  for (const t of xt) el("svg:text", { x: X(t), y: m.t + ph + 15, "text-anchor": "middle" }, svg, fx(t));
  el("svg:text", { x: m.l + pw / 2, y: H - 4, "text-anchor": "middle", class: "lbl" }, svg, L.stressX);
  el("svg:text", { x: m.l - 30, y: 11, class: "lbl" }, svg, "↑ " + L.stressY);
  if (pts.length >= 5 && r != null) {
    const x0 = xt[0], x1 = xt[xt.length - 1];
    const clampY = v => Math.max(yt[0], Math.min(yt[yt.length - 1], v));
    el("svg:line", { x1: X(x0), x2: X(x1), y1: Y(clampY(icpt + slope * x0)), y2: Y(clampY(icpt + slope * x1)), style: "stroke:var(--muted);stroke-width:1.5" }, svg);
  }
  for (const p of pts) {
    const dot = el("svg:circle", { cx: X(p.x), cy: Y(p.y), r: 4.5, style: "fill:var(--s1);stroke:var(--surface);stroke-width:2" }, svg);
    const hit = el("svg:circle", { cx: X(p.x), cy: Y(p.y), r: 12, class: "hit", tabindex: 0 }, svg);
    const show = e => { dot.setAttribute("r", 6); showTip(hit, fmtLong.format(toD(p.d)), [["var(--s1)", nf(p.x), L.stressTip], [null, nf(p.y), L.scoreTip]], e); };
    const hide = () => { dot.setAttribute("r", 4.5); hideTip(); };
    hit.addEventListener("pointermove", show); hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", hide); hit.addEventListener("blur", hide);
  }
}

function lineChart(cardId, dates, key, title, note, unit, digits, connectGaps) {
  const card = document.getElementById(cardId);
  setHead(card, title, note);
  const v = vals(dates, key); const host = card.querySelector(".plot");
  if (!v.length) { host.textContent = "—"; return; }
  const f = timeFrame(card, dates, niceTicks(Math.min(...v) - (digits ? 0.5 : 2), Math.max(...v) + (digits ? 0.5 : 2), 4), 210, 64);
  const pts = dates.map((d, i) => (BY_DATE[d] || {})[key] != null ? [f.x(i), f.y(BY_DATE[d][key]), i] : null);
  let dStr = "", pen = false;
  for (const p of pts) { if (!p) { if (!connectGaps) pen = false; continue; } dStr += (pen ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1); pen = true; }
  el("svg:path", { d: dStr, style: "fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round" }, f.svg);
  const real = pts.filter(Boolean);
  if (connectGaps) for (const p of real) el("svg:circle", { cx: p[0], cy: p[1], r: 4, style: "fill:var(--s1);stroke:var(--surface);stroke-width:2" }, f.svg);
  const label = (p, where) => el("svg:text", where === "right" ? { x: p[0] + 9, y: p[1] + 4, class: "lbl" } : { x: p[0] + 6, y: p[1] - 9, class: "lbl" }, f.svg, `${nf(BY_DATE[dates[p[2]]][key], digits)} ${unit}`);
  const last = real[real.length - 1];
  if (!connectGaps) el("svg:circle", { cx: last[0], cy: last[1], r: 4, style: "fill:var(--s1);stroke:var(--surface);stroke-width:2" }, f.svg);
  label(last, "right");
  if (connectGaps && real.length > 1) label(real[0], "above");
  const cross = el("svg:line", { y1: f.m.t, y2: f.m.t + f.ph, style: "stroke:var(--axis);stroke-width:1;display:none", "shape-rendering": "crispEdges" }, f.svg);
  const focusDot = el("svg:circle", { r: 5, style: "fill:var(--s1);stroke:var(--surface);stroke-width:2;display:none" }, f.svg);
  hitBands(f, dates, (i, d, r, e) => {
    cross.setAttribute("x1", f.x(i)); cross.setAttribute("x2", f.x(i)); cross.style.display = "";
    const val = (BY_DATE[d] || {})[key];
    if (val != null) { focusDot.setAttribute("cx", f.x(i)); focusDot.setAttribute("cy", f.y(val)); focusDot.style.display = ""; } else focusDot.style.display = "none";
    showTip(r, fmtLong.format(toD(d)), [["var(--s1)", val == null ? "—" : `${nf(val, digits)} ${unit}`, title]], e);
  }, () => { cross.style.display = "none"; focusDot.style.display = "none"; });
}

function stepsChart(dates) {
  const card = document.getElementById("c-steps");
  setHead(card, L.stepsT, L.stepsN);
  legend(card, [["var(--s1)", L.withW], ["var(--deemph)", L.withoutW]]);
  const f = timeFrame(card, dates, niceTicks(0, Math.max(1000, ...vals(dates, "steps")), 4), 220);
  const w = Math.max(2, Math.min(24, f.band * 0.62));
  const bars = dates.map((d, i) => {
    const r = BY_DATE[d] || {}; if (!r.steps) return null;
    return el("svg:path", { d: colPath(f.x(i) - w / 2, w, f.y(0), f.y(r.steps), true), style: `fill:var(${r.workouts ? "--s1" : "--deemph"})` }, f.svg);
  });
  hitBands(f, dates, (i, d, rect, e) => {
    bars.forEach((b, j) => b && (b.style.opacity = j === i ? 1 : 0.55));
    const r = BY_DATE[d] || {};
    const lines = [[r.workouts ? "var(--s1)" : "var(--deemph)", nf(r.steps), L.steps], [null, nf(r.km, 1), L.km], [null, nf(r.active_min), L.active]];
    for (const w of r.workouts || []) lines.push(["var(--s1)", `${nf(w.min)} min`, (L.sports[w.sport] || w.sport) + (w.km ? ` · ${nf(w.km, 1)} km` : "") + (w.hr ? ` · ${nf(w.hr)} ${L.bpm}` : "")]);
    showTip(rect, fmtLong.format(toD(d)), lines, e);
  }, () => bars.forEach(b => b && (b.style.opacity = 1)));
}

// ---------- KPIs & tables ----------
function kpis(dates) {
  const box = document.getElementById("kpis"); box.textContent = "";
  const prev = prevDates();
  const defs = [[L.kSleep, "sleep_h", 1, L.h, +1], [L.kScore, "sleep_score", 0, "", +1], [L.kRhr, "rhr", 0, L.bpm, -1],
                [L.kStress, "stress", 0, "", -1], [L.kSteps, "steps", 0, "", +1]];
  for (const [label, key, dg, unit, goodDir] of defs) {
    const cur = mean(vals(dates, key)), pv = mean(vals(prev, key));
    const t = el("div", { class: "tile" }, box); el("div", { class: "label" }, t, label);
    const v = el("div", { class: "value" }, t, nf(cur, dg)); if (unit) el("small", {}, v, unit);
    let cls = "delta", txt = L.noPrev;
    if (cur != null && pv != null) {
      const diff = cur - pv, shown = nf(Math.abs(diff), dg);
      const flat = shown === nf(0, dg);
      txt = `${flat ? "＝" : diff > 0 ? "▲" : "▼"} ${shown}${unit ? " " + unit : ""} ${L.vsPrev(range)}`;
      if (!flat) cls += Math.sign(diff) === goodDir ? " good" : " bad";
    }
    el("div", { class: cls }, t, txt);
  }
  const w = dates.filter(d => BY_DATE[d] && BY_DATE[d].weight != null);
  const t = el("div", { class: "tile" }, box); el("div", { class: "label" }, t, L.kWeight);
  const last = w.length ? BY_DATE[w[w.length - 1]].weight : null;
  const v = el("div", { class: "value" }, t, nf(last, 1)); el("small", {}, v, L.kg);
  el("div", { class: "delta" }, t, w.length > 1 ? `${BY_DATE[w[w.length - 1]].weight - BY_DATE[w[0]].weight > 0 ? "▲" : "▼"} ${nf(Math.abs(BY_DATE[w[w.length - 1]].weight - BY_DATE[w[0]].weight), 1)} ${L.kg} ${L.inRange}` : L.noPrev);
}
function isoWeek(s) {
  const d = toD(s), day = (d.getUTCDay() + 6) % 7; d.setUTCDate(d.getUTCDate() - day + 3);
  const y = d.getUTCFullYear(), w1 = new Date(Date.UTC(y, 0, 4));
  return `${y}-W${String(1 + Math.round(((d - w1) / DAY - 3 + ((w1.getUTCDay() + 6) % 7)) / 7)).padStart(2, "0")}`;
}
function table(id, head, rows) {
  const t = document.getElementById(id); t.textContent = "";
  const tr = el("tr", {}, el("thead", {}, t)); head.forEach(h => el("th", {}, tr, h));
  const tb = el("tbody", {}, t); rows.forEach(r => { const row = el("tr", {}, tb); r.forEach(c => el("td", {}, row, c)); });
}
function tables(dates) {
  const C = L.cols, weeks = {};
  dates.forEach(d => { if (BY_DATE[d]) (weeks[isoWeek(d)] = weeks[isoWeek(d)] || []).push(d); });
  document.getElementById("t-week-title").textContent = L.weekT;
  table("t-week", [C.week, C.days, C.steps, C.sleep, C.deep, C.score, C.rhr, C.stress, C.workouts, C.weight],
    Object.entries(weeks).map(([w, ds]) => {
      const wts = vals(ds, "weight");
      return [w, ds.length, nf(mean(vals(ds, "steps"))), nf(mean(vals(ds, "sleep_h")), 1), nf(mean(vals(ds, "deep_h")), 1),
        nf(mean(vals(ds, "sleep_score"))), nf(mean(vals(ds, "rhr"))), nf(mean(vals(ds, "stress"))),
        ds.reduce((a, d) => a + ((BY_DATE[d].workouts || []).length), 0), wts.length ? nf(wts[wts.length - 1], 1) : "—"];
    }));
  document.getElementById("t-day-title").textContent = L.dayT;
  table("t-day", [C.date, C.steps, C.km, C.active, C.rhr, C.stress, C.sleep, C.deep, C.rem, C.light, C.score, C.weight, C.workouts],
    dates.filter(d => BY_DATE[d]).map(d => { const r = BY_DATE[d]; return [fmtLong.format(toD(d)), nf(r.steps), nf(r.km, 1), nf(r.active_min), nf(r.rhr), nf(r.stress),
      nf(r.sleep_h, 1), nf(r.deep_h, 1), nf(r.rem_h, 1), nf(r.light_h, 1), nf(r.sleep_score), nf(r.weight, 1), (r.workouts || []).length]; }));
}

// ---------- page ----------
function render() {
  const dates = windowDates();
  document.getElementById("title").textContent = L.title;
  document.getElementById("subtitle").textContent = L.subtitle(fmtDate.format(toD(dates[0])), fmtDate.format(toD(LAST)), dates.filter(d => BY_DATE[d]).length);
  kpis(dates); sleepChart(dates); stressChart(dates);
  lineChart("c-rhr", dates, "rhr", L.rhrT, L.rhrN, L.bpm, 0, false);
  stepsChart(dates);
  lineChart("c-weight", dates, "weight", L.weightT, L.weightN(vals(dates, "weight").length), L.kg, 1, true);
  tables(dates);
}
const seg = document.getElementById("ranges");
for (const [value, label] of L.ranges) {
  const b = el("button", { type: "button", "aria-pressed": String(value === range) }, seg, label);
  b.addEventListener("click", () => { range = value; seg.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b))); render(); });
}
const themeBtn = document.getElementById("theme");
const isDark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
const syncTheme = () => { themeBtn.textContent = isDark() ? L.light : L.dark; };
themeBtn.addEventListener("click", () => { document.documentElement.dataset.theme = isDark() ? "light" : "dark"; syncTheme(); });
if (DATA.meta.synthetic) { const b = document.getElementById("banner"); b.hidden = false; b.textContent = L.synthetic; }
document.getElementById("footer").textContent = L.footer(DATA.meta.generated);
syncTheme(); render();
let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { syncTheme(); render(); }, 120); });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
