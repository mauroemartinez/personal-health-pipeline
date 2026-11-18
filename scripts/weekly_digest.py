"""Build a weekly Markdown health digest from the private normalized data.

Input:  data/processed/mi_fitness/*.jsonl (written by import_mifitness.py)
Output: data/reports/weekly/<ISO week>.md  (ignored by Git)

The digest contains daily/weekly summaries and per-workout summaries (no
intraday series, routes or identifiers) and is meant to be pasted into an LLM.
Text is in Spanish. Optional personal context for the LLM (routine, goals) is
read from HEALTH_DIGEST_CONTEXT in .env, so it never lives in the code.
The console only prints the output path and how many days had data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "mi_fitness"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "reports" / "weekly"

SPORT_LABELS = {
    "outdoor_walking": "Caminata",
    "indoor_running": "Cinta / running indoor",
    "strength_training": "Fuerza",
}
WEEKDAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def load_env(path: Path) -> None:
    """Minimal .env reader (no dependency). Existing environment variables win."""
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


def scaled(value: Any, divisor: float) -> float | None:
    n = num(value)
    return None if n is None else n / divisor


def build_daily(folder: Path) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]]]:
    """One row of metrics per local date. Missing data stays missing (never zero)."""
    daily: dict[str, dict[str, float]] = defaultdict(dict)

    for row in read_jsonl(folder / "daily_summaries.jsonl"):
        d, p, m = row["local_date"], row["payload"], row["metric"]
        if m == "steps":
            daily[d]["steps"] = num(p.get("steps"))
            daily[d]["distance_km"] = scaled(p.get("distance"), 1000)
        elif m == "calories":
            daily[d]["kcal"] = num(p.get("calories"))
        elif m == "intensity":
            daily[d]["active_min"] = num(p.get("duration"))
        elif m == "valid_stand":
            daily[d]["stand_hours"] = num(p.get("count"))
        elif m == "heart_rate":
            daily[d]["avg_hr"] = num(p.get("avg_hr"))
            daily[d]["rhr"] = num(p.get("avg_rhr"))
        elif m == "stress":
            daily[d]["avg_stress"] = num(p.get("avg_stress"))
            raw = p.get("stress_scale")
            scale = {k: num(v) or 0.0 for k, v in raw.items()} if isinstance(raw, dict) else {}
            total = sum(scale.values())
            if total:
                daily[d]["stress_high_pct"] = 100 * (scale.get("moderate", 0) + scale.get("severe", 0)) / total
                daily[d]["relax_pct"] = 100 * scale.get("relax", 0) / total
        elif m == "sleep":
            daily[d]["sleep_h"] = scaled(p.get("total_duration"), 60)
            daily[d]["deep_h"] = scaled(p.get("sleep_deep_duration"), 60)
            daily[d]["rem_h"] = scaled(p.get("sleep_rem_duration"), 60)
            daily[d]["sleep_score"] = num(p.get("sleep_score"))

    for row in read_jsonl(folder / "observations.jsonl"):
        d = row["local_date"]
        if row["metric"] == "resting_heart_rate" and daily[d].get("rhr") is None:
            daily[d]["rhr"] = num(row["value"])
        elif row["metric"] == "weight":
            daily[d]["weight_kg"] = num(row["value"])  # last measurement of the day wins (sorted input)

    activities = read_jsonl(folder / "activities.jsonl")
    for act in activities:
        d = act["local_date"]
        daily[d]["workouts"] = daily[d].get("workouts", 0) + 1
        daily[d]["workout_min"] = daily[d].get("workout_min", 0) + (num(act.get("duration_s")) or 0) / 60

    cleaned = {d: {k: v for k, v in m.items() if v is not None} for d, m in daily.items()}
    return {d: m for d, m in cleaned.items() if m}, activities


def week_bounds(iso_week: str) -> tuple[date, date]:
    year, _, week = iso_week.upper().partition("-W")
    monday = date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + timedelta(days=6)


def days_between(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def avg(days: list[str], daily: dict[str, dict[str, float]], key: str) -> tuple[float | None, int]:
    values = [daily[d][key] for d in days if key in daily.get(d, {})]
    return (mean(values) if values else None), len(values)


def total(days: list[str], daily: dict[str, dict[str, float]], key: str) -> float:
    return sum(daily.get(d, {}).get(key, 0) for d in days)


def fmt(value: float | None, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "—"
    if round(value, digits) == 0:
        value = 0.0  # avoid "-0"
    return f"{value:,.{digits}f}".replace(",", " ") + suffix


def fmt_delta(current: float | None, base: float | None, digits: int = 0) -> str:
    if current is None or base is None:
        return "—"
    diff = current - base
    pct = f" ({diff / base * 100:+.0f}%)" if base else ""
    return f"{diff:+.{digits}f}{pct}"


def pace(distance_m: float | None, duration_s: float | None) -> str:
    if not distance_m or not duration_s or distance_m < 100:
        return "—"
    sec_per_km = duration_s / (distance_m / 1000)
    return f"{int(sec_per_km // 60)}:{int(sec_per_km % 60):02d} /km"


# (label, daily key, decimals, suffix, aggregation)
SUMMARY_ROWS: list[tuple[str, str, int, str, str]] = [
    ("Pasos / día", "steps", 0, "", "avg"),
    ("Distancia / día", "distance_km", 1, " km", "avg"),
    ("Minutos activos / día", "active_min", 0, " min", "avg"),
    ("Calorías / día", "kcal", 0, " kcal", "avg"),
    ("Horas de pie (stand) / día", "stand_hours", 0, " h", "avg"),
    ("FC media", "avg_hr", 0, " lpm", "avg"),
    ("FC en reposo", "rhr", 0, " lpm", "avg"),
    ("Estrés medio (0-100)", "avg_stress", 0, "", "avg"),
    ("% tiempo estrés moderado+alto", "stress_high_pct", 0, " %", "avg"),
    ("Sueño total / noche", "sleep_h", 1, " h", "avg"),
    ("Sueño profundo / noche", "deep_h", 1, " h", "avg"),
    ("REM / noche", "rem_h", 1, " h", "avg"),
    ("Puntaje de sueño", "sleep_score", 0, "", "avg"),
    ("Entrenamientos", "workouts", 0, "", "sum"),
    ("Minutos de entrenamiento", "workout_min", 0, " min", "sum"),
]


def render(iso_week: str, baseline_weeks: int, daily: dict[str, dict[str, float]],
           activities: list[dict[str, Any]], synthetic: bool = False) -> tuple[str, int]:
    start, end = week_bounds(iso_week)
    week_days = days_between(start, end)
    base_start = start - timedelta(weeks=baseline_weeks)
    base_days = days_between(base_start, start - timedelta(days=1))
    days_with_data = sum(1 for d in week_days if d in daily)

    out: list[str] = []
    w = out.append
    w(f"# Digest semanal de salud — {iso_week}")
    w("")
    if synthetic:
        w("> **Datos sintéticos de ejemplo** (generados con `scripts/make_sample_export.py`). No son datos reales.")
        w("")
    w(f"Período: **{start.isoformat()} → {end.isoformat()}** · Línea base: {baseline_weeks} semanas previas "
      f"({base_start.isoformat()} → {(start - timedelta(days=1)).isoformat()}) · Fuente: export Mi Fitness (Mi Band).")
    w("")
    w(f"Días con datos en la semana: **{days_with_data}/7**. Los días sin datos se omiten de los promedios; no son ceros.")
    w("")

    w("## Resumen vs. línea base")
    w("")
    w("| Métrica | Semana | Línea base | Δ | Días con dato (sem / base) |")
    w("|---|---:|---:|---:|---:|")
    for label, key, digits, suffix, agg in SUMMARY_ROWS:
        if agg == "sum":
            cur = total(week_days, daily, key) if days_with_data else None
            # Baseline as a 7-day equivalent of the days that actually have data,
            # so partial weeks or short history don't bias it.
            base_n = sum(1 for d in base_days if d in daily)
            base = total(base_days, daily, key) / base_n * 7 if base_n else None
            cover = f"{days_with_data} / {base_n}"
            w(f"| {label} (total semana) | {fmt(cur, digits, suffix)} | {fmt(base, 1, suffix)} | "
              f"{fmt_delta(cur, base, digits)} | {cover} |")
        else:
            cur, n_cur = avg(week_days, daily, key)
            base, n_base = avg(base_days, daily, key)
            w(f"| {label} | {fmt(cur, digits, suffix)} | {fmt(base, digits, suffix)} | "
              f"{fmt_delta(cur, base, digits)} | {n_cur} / {n_base} |")
    w("")

    weights = [(d, daily[d]["weight_kg"]) for d in sorted(daily) if "weight_kg" in daily[d] and d <= end.isoformat()]
    w("## Peso")
    w("")
    in_week = [x for x in weights if x[0] >= start.isoformat()]
    if in_week:
        last_day, last = in_week[-1]
        previous = [x for x in weights if x[0] < start.isoformat()]
        delta = f" · Δ vs. registro anterior ({previous[-1][0]}): {last - previous[-1][1]:+.1f} kg" if previous else ""
        w(f"Último registro de la semana: **{last:.1f} kg** ({last_day}){delta}.")
    elif weights:
        w(f"Sin pesajes esta semana. Último registro: {weights[-1][1]:.1f} kg ({weights[-1][0]}).")
    else:
        w("Sin pesajes registrados.")
    w("")

    w("## Detalle diario")
    w("")
    w("| Día | Pasos | Km | Min activos | FC reposo | Estrés | Sueño h | Profundo h | Score sueño | Entrenos |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i, d in enumerate(week_days):
        m = daily.get(d, {})
        w(f"| {WEEKDAYS[i]} {d[5:]} | {fmt(m.get('steps'))} | {fmt(m.get('distance_km'), 1)} | "
          f"{fmt(m.get('active_min'))} | {fmt(m.get('rhr'))} | {fmt(m.get('avg_stress'))} | "
          f"{fmt(m.get('sleep_h'), 1)} | {fmt(m.get('deep_h'), 1)} | {fmt(m.get('sleep_score'))} | "
          f"{fmt(m.get('workouts'))} |")
    w("")

    week_acts = [a for a in activities if start.isoformat() <= a["local_date"] <= end.isoformat()]
    w("## Entrenamientos")
    w("")
    if week_acts:
        w("| Día | Tipo | Duración | Distancia | Ritmo medio | FC media / máx | Carga | Efecto aeróbico |")
        w("|---|---|---:|---:|---:|---:|---:|---:|")
        for a in week_acts:
            dur = num(a.get("duration_s"))
            dist = num(a.get("distance_m"))
            w(f"| {a['local_date'][5:]} | {SPORT_LABELS.get(a['sport'], a['sport'])} | "
              f"{fmt(dur / 60 if dur else None)} min | {fmt(dist / 1000 if dist else None, 2, ' km')} | "
              f"{pace(dist, dur)} | {fmt(num(a.get('avg_hr')))} / {fmt(num(a.get('max_hr')))} | "
              f"{fmt(num(a.get('train_load')))} | {fmt(num(a.get('train_effect')), 1)} |")
        w("")
        w("> Variables que la banda no mide (p. ej. inclinación en cinta) pueden registrarse en `activities.notes`.")
    else:
        w("Sin entrenamientos registrados.")
    w("")

    w("## Para el análisis con un LLM")
    w("")
    context = os.environ.get("HEALTH_DIGEST_CONTEXT", "").strip()
    if context:
        w(f"Contexto: {context}")
        w("")
    w("Preguntas sugeridas:")
    w("")
    w("1. ¿Las noches con menos sueño total o profundo anticipan más estrés o FC en reposo más alta al día siguiente?")
    w("2. ¿Cómo evolucionan FC media y ritmo en entrenamientos comparables (señal de mejora aeróbica)?")
    w("3. ¿El volumen semanal de actividad acompaña la tendencia de peso, según la cantidad de pesajes disponibles?")
    w("4. ¿Qué dato falta o es demasiado escaso para sacar conclusiones confiables?")
    w("")
    w("_Advertencias: datos de un wearable de muñeca (estimaciones, no clínicos); "
      "estrés y sueño son algoritmos propietarios de Xiaomi._")
    w("")
    return "\n".join(out), days_with_data


def last_complete_week(today: date) -> str:
    year, week, _ = (today - timedelta(days=today.isoweekday())).isocalendar()
    return f"{year}-W{week:02d}"


def latest_week_with_data(daily: dict[str, dict[str, float]]) -> str | None:
    if not daily:
        return None
    year, week, _ = date.fromisoformat(max(daily)).isocalendar()
    return f"{year}-W{week:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", help="ISO week like 2026-W46 (default: last complete week)")
    parser.add_argument("--latest", action="store_true", help="Use the most recent week that has data")
    parser.add_argument("--baseline-weeks", type=int, default=4)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    load_env(ROOT / ".env")
    if args.week:
        args.week = args.week.upper()
        if not re.fullmatch(r"\d{4}-W\d{2}", args.week):
            parser.error("--week must look like 2026-W46")
        try:
            week_bounds(args.week)
        except ValueError:
            parser.error(f"{args.week} is not a valid ISO week")
    reports = (ROOT / "data" / "reports").resolve()
    out_dir = args.output_dir.resolve()
    if out_dir != reports and reports not in out_dir.parents:
        parser.error("Digests contain personal data; output must stay under data/reports/ (ignored by Git).")

    daily, activities = build_daily(args.input)
    if not daily:
        parser.error(f"No normalized data found in {args.input}. Run scripts/import_mifitness.py first.")
    week = args.week or (latest_week_with_data(daily) if args.latest else last_complete_week(date.today()))
    manifest_path = args.input / "manifest.json"
    synthetic = manifest_path.exists() and bool(json.loads(manifest_path.read_text(encoding="utf-8")).get("synthetic"))
    markdown, days_with_data = render(week, args.baseline_weeks, daily, activities, synthetic=synthetic)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{week}.md"
    path.write_text(markdown, encoding="utf-8", newline="\n")
    print(f"Digest {week}: {days_with_data}/7 days with data -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
