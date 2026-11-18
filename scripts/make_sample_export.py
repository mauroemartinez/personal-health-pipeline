"""Generate a synthetic Mi Fitness export so anyone can try the pipeline without a Mi Band.

Writes CSVs in the same format as the real Xiaomi export (same file names, columns
and JSON payloads, including fake device identifiers so the scrubbing can be seen
working) to data/sample/mi_fitness_export/. A SYNTHETIC_SAMPLE.txt marker is
written next to them: the importer refuses to mix this data with real output,
and the loader refuses to upload it unless explicitly allowed.

The data is invented but behaves plausibly, so an LLM has patterns to find:
  * workout days tend to be followed by more deep sleep,
  * high-stress days tend to be followed by worse sleep,
  * weight drifts slowly downward,
  * resting heart rate improves slightly over the period.

Deterministic for a given --seed, --days and --end-date.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "sample" / "mi_fitness_export"
MARKER = "SYNTHETIC_SAMPLE.txt"
FAKE_UID = "1000000000"
FAKE_SID = "sample-band-0001"
PREFIX = "{day}_{uid}_MiFitness_"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_offset(raw: str) -> timedelta:
    raw = (raw or "+00:00").strip()
    sign = -1 if raw.startswith("-") else 1
    hours, _, minutes = raw.lstrip("+-").partition(":")
    return sign * timedelta(hours=int(hours), minutes=int(minutes or 0))


class Day:
    """Everything generated for one local calendar day."""

    def __init__(self, local: date, offset: timedelta):
        self.local = local
        self.offset = offset
        # UTC epoch of local midnight, and the "label" Mi Fitness uses for daily rows.
        self.midnight = int((datetime(local.year, local.month, local.day, tzinfo=timezone.utc) - offset).timestamp())
        self.label = int(datetime(local.year, local.month, local.day, tzinfo=timezone.utc).timestamp())

    def at(self, hour: float) -> int:
        return self.midnight + int(hour * 3600)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def generate(days: int, end: date, offset: timedelta, rng: random.Random):
    tz_q15 = int(offset.total_seconds() // 900)
    fitness, aggregated, sports = [], [], []
    weight = rng.uniform(78, 84)
    prev_workout = False
    prev_stress = 35.0

    def obs(key: str, t: int, value: dict) -> None:
        fitness.append([FAKE_UID, FAKE_SID, key, t, json.dumps(value, separators=(",", ":")), t + 3600])

    def agg(day: Day, key: str, value: dict, tag: str = "daily_report") -> None:
        aggregated.append([FAKE_UID, FAKE_SID, tag, key, day.label,
                           json.dumps(value, separators=(",", ":")), day.label + 86400 + 7200])

    for i in range(days):
        local = end - timedelta(days=days - 1 - i)
        day = Day(local, offset)
        progress = i / max(days - 1, 1)
        weekend = local.weekday() >= 5

        # --- Sleep (night ending this morning; assigned to this day) -----------------
        bed = day.at(-rng.uniform(0.3, 1.8))                       # previous evening
        quality = 0.55 + (0.12 if prev_workout else 0) - (prev_stress - 35) / 150 + rng.uniform(-0.12, 0.12)
        total = int(clamp(rng.gauss(420 if not weekend else 470, 35) + 30 * (quality - 0.55), 300, 560))
        deep = int(clamp(total * (0.13 + 0.1 * quality) + rng.uniform(-8, 8), 30, 150))
        rem = int(clamp(total * rng.uniform(0.17, 0.23), 40, 140))
        awake = int(clamp(rng.gauss(18, 8) - 10 * (quality - 0.55), 3, 60))
        light = total - deep - rem - awake
        wake = bed + total * 60
        rhr = round(clamp(62 - 4 * progress + rng.gauss(0, 1.5) - (2 if quality > 0.65 else 0), 48, 75))
        score = int(clamp(55 + 45 * quality + rng.gauss(0, 4), 40, 98))
        stages, cursor = [], bed
        for state, minutes in ((2, light // 2), (3, deep), (4, rem), (2, light - light // 2), (5, awake)):
            stages.append({"start_time": cursor, "end_time": cursor + minutes * 60, "state": state})
            cursor += minutes * 60
        obs("sleep", bed, {
            "bedtime": bed, "wake_up_time": wake, "device_bedtime": bed, "device_wake_up_time": wake,
            "duration": total, "timezone": tz_q15, "sleep_deep_duration": deep, "sleep_light_duration": light,
            "sleep_rem_duration": rem, "sleep_awake_duration": awake, "awake_count": max(1, awake // 8),
            "avg_hr": rhr + 3, "min_hr": rhr - 2, "max_hr": rhr + 18, "items": stages,
            "is_uncomplete": False, "version": 2, "protoTime": wake, "sleep_algorithm_version": 1,
        })
        agg(day, "sleep", {
            "total_duration": total, "sleep_deep_duration": deep, "sleep_light_duration": light,
            "sleep_rem_duration": rem, "sleep_awake_duration": awake, "sleep_score": score,
            "avg_hr": rhr + 3, "min_hr": rhr - 2, "max_hr": rhr + 18, "avg_spo2": rng.randint(95, 98),
            "day_sleep_evaluation": 0, "long_sleep_evaluation": 1, "sleep_stage": 1,
            "segment_details": [{"bedtime": bed, "wake_up_time": wake, "duration": total, "timezone": tz_q15}],
        })
        obs("resting_heart_rate", day.at(9), {"bpm": rhr, "date_time": day.label})

        # --- Workout --------------------------------------------------------------------
        workout = None
        roll = rng.random()
        if local.weekday() in (1, 3) and roll < 0.85:
            workout = ("strength_training", "strength_training")
        elif roll < (0.55 if not weekend else 0.35):
            workout = ("outdoor_walking", "walking") if rng.random() < 0.7 else ("indoor_running", "running")
        workout_steps = 0
        if workout:
            key, category = workout
            start = day.at(rng.choice([7.0, 13.2, 18.5]) + rng.uniform(0, 0.4))
            minutes = {"outdoor_walking": rng.uniform(25, 50), "indoor_running": rng.uniform(25, 40),
                       "strength_training": rng.uniform(40, 60)}[key]
            duration = int(minutes * 60)
            pace = {"outdoor_walking": rng.uniform(560, 680), "indoor_running": rng.uniform(380, 450) - 30 * progress}.get(key)
            distance = int(duration / pace * 1000) if pace else 0
            workout_steps = int(distance / 0.78) if distance else 0
            avg_hr = int({"outdoor_walking": 112, "indoor_running": 148, "strength_training": 118}[key] - 4 * progress + rng.gauss(0, 4))
            payload = {
                "start_time": start, "end_time": start + duration + rng.randint(0, 120), "time": start,
                "duration": duration, "valid_duration": duration, "timezone": tz_q15, "sport_type": 1,
                "avg_hrm": avg_hr, "max_hrm": avg_hr + rng.randint(18, 30), "min_hrm": avg_hr - rng.randint(25, 35),
                "calories": int(minutes * {"outdoor_walking": 4.5, "indoor_running": 10, "strength_training": 6}[key]),
                "total_cal": 0, "train_load": int(minutes * (1.2 if key == "outdoor_walking" else 2.4)),
                "train_effect": round(rng.uniform(1.5, 3.8), 1), "aerobic_train_effect_level": 2,
                "anaerobic_train_effect": round(rng.uniform(0, 1.5), 1), "anaerobic_train_effect_level": 1,
                "recover_time": rng.randint(6, 30), "version": 3, "proto_type": 1, "energy_consume": 0,
                "highlight_events": {}, "reserve_hr_zone": 0,
                "hrm_warm_up_duration": 300, "hrm_fat_burning_duration": int(duration * 0.4),
                "hrm_aerobic_duration": int(duration * 0.3), "hrm_anaerobic_duration": 60, "hrm_extreme_duration": 0,
            }
            if distance:
                payload.update({"distance": distance, "steps": workout_steps, "min_pace": int(pace * 0.9),
                                "max_pace": int(pace * 1.15), "max_cadence": rng.randint(110, 170),
                                "vo2_max": int(40 + 4 * progress), "vo2_max_level": 3,
                                "running_ability_index": round(rng.uniform(35, 45), 1), "running_ability_level": 2})
            sports.append([FAKE_UID, FAKE_SID, key, start, category, json.dumps(payload, separators=(",", ":")), start + 600])

        # --- Daytime series ---------------------------------------------------------------
        stress_base = clamp(rng.gauss(38 if not weekend else 28, 8) - 6 * (quality - 0.55), 15, 70)
        hr_values, stress_values, steps_total, dist_total, kcal_total, active, stands = [], [], 0, 0, 0, 0, 0
        for slot in range(144):                                   # every 10 minutes
            hour = slot / 6
            t = day.at(hour) + rng.randint(0, 59)
            asleep = hour < (wake - day.midnight) / 3600 or hour >= 23.3
            bpm = int(clamp(rhr + (0 if asleep else rng.gauss(18, 7)), 40, 180))
            hr_values.append(bpm)
            obs("heart_rate", t, {"bpm": bpm, "time": t, "type": 0})
            if not asleep and slot % 2 == 0:
                level = int(clamp(stress_base + 18 * math.sin(math.pi * (hour - 8) / 10) + rng.gauss(0, 8), 1, 99))
                stress_values.append(level)
                obs("stress", t + 30, {"stress": level, "time": t + 30})
        for hour in range(7, 23):                                 # hourly steps / calories
            t = day.at(hour + 0.98)
            steps = int(clamp(rng.gauss(450, 200), 0, 2500))
            if workout and workout[0] != "strength_training" and hour == int((sports[-1][3] - day.midnight) / 3600):
                steps += workout_steps
            distance = int(steps * 0.76)
            kcal = int(steps * 0.04) + 55
            steps_total += steps; dist_total += distance; kcal_total += kcal
            obs("steps", t, {"steps": steps, "distance": distance, "calories": kcal, "time": t})
            obs("calories", t + 1, {"calories": kcal, "time": t + 1})
            if rng.random() < 0.6:
                stands += 1
                obs("valid_stand", day.at(hour + 0.3), {"start_time": day.at(hour + 0.3), "end_time": day.at(hour + 0.32)})
        active_minutes = int(clamp(rng.gauss(25, 10) + (35 if workout else 0), 0, 180))
        for m in range(active_minutes):
            t = day.at(12 + m / 60) if not workout else sports[-1][3] + m * 60
            obs("intensity", t, {"time": t})
        active = active_minutes

        # --- Weight (about twice a week) ---------------------------------------------------
        weight -= rng.uniform(0.01, 0.06)
        if local.weekday() in (0, 4) and rng.random() < 0.85:
            w = round(weight + rng.gauss(0, 0.3), 1)
            obs("weight", day.at(7.5), {"weight": w, "bmi": round(w / 1.78 ** 2, 1), "time": day.at(7.5)})

        # --- Daily reports + has_data marks + goals ----------------------------------------
        scale_total = len(stress_values) * 10
        relax = sum(10 for s in stress_values if s < 40); mild = sum(10 for s in stress_values if 40 <= s < 60)
        moderate = sum(10 for s in stress_values if 60 <= s < 80); severe = scale_total - relax - mild - moderate
        agg(day, "steps", {"steps": steps_total, "distance": dist_total, "calories": kcal_total})
        agg(day, "calories", {"calories": kcal_total})
        agg(day, "intensity", {"duration": active})
        agg(day, "valid_stand", {"count": stands})
        agg(day, "heart_rate", {
            "avg_hr": int(sum(hr_values) / len(hr_values)), "avg_rhr": rhr, "max_hr": max(hr_values), "min_hr": min(hr_values),
            "abnormal_hr_count": 0, "warm_up_hr_zone_duration": 20, "fat_burning_hr_zone_duration": active // 2,
            "aerobic_hr_zone_duration": active // 3, "anaerobic_hr_zone_duration": 2, "extreme_hr_zone_duration": 0,
            "latest_hr": {"bpm": hr_values[-1], "time": day.at(23.9), "sid": FAKE_SID, "dbKey": "hr-sample-0001", "dbTime": day.at(23.9)},
        })
        agg(day, "stress", {"avg_stress": int(sum(stress_values) / len(stress_values)), "max_stress": max(stress_values),
                            "min_stress": min(stress_values),
                            "stress_scale": {"relax": relax, "mild": mild, "moderate": moderate, "severe": severe}})
        for key in ("steps", "calories", "intensity", "valid_stand", "heart_rate", "stress", "sleep"):
            agg(day, key, {"has_data": True}, tag="daily_mark")
        agg(day, "goal", {"date_time": day.label, "sidList": [FAKE_SID], "timeInZero": 0,
                          "goal_items": [{"field": 1, "target_value": 8000, "achieved_value": steps_total}]},
            tag="daily_fitness")

        prev_workout = workout is not None
        prev_stress = sum(stress_values) / len(stress_values)

    return fitness, aggregated, sports


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=56, help="How many days to generate (default 56)")
    parser.add_argument("--end-date", type=date.fromisoformat, help="Last local date (default: yesterday)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    out = args.output.resolve()
    sample_root = (ROOT / "data" / "sample").resolve()
    if out != sample_root and sample_root not in out.parents:
        parser.error("Synthetic data must stay under data/sample/ so it never mixes with real exports.")
    if not 7 <= args.days <= 730:
        parser.error("--days must be between 7 and 730")

    load_env(ROOT / ".env")
    offset = parse_offset(os.environ.get("HEALTH_UTC_OFFSET", "+00:00"))
    end = args.end_date or (datetime.now(timezone.utc) + offset).date() - timedelta(days=1)
    fitness, aggregated, sports = generate(args.days, end, offset, random.Random(args.seed))

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*_MiFitness_*.csv"):
        old.unlink()
    prefix = PREFIX.format(day=end.strftime("%Y%m%d"), uid=FAKE_UID)
    write_csv(out / f"{prefix}hlth_center_fitness_data.csv", ["Uid", "Sid", "Key", "Time", "Value", "UpdateTime"], fitness)
    write_csv(out / f"{prefix}hlth_center_aggregated_fitness_data.csv",
              ["Uid", "Sid", "Tag", "Key", "Time", "Value", "UpdateTime"], aggregated)
    write_csv(out / f"{prefix}hlth_center_sport_record.csv",
              ["Uid", "Sid", "Key", "Time", "Category", "Value", "UpdateTime"], sports)
    (out / MARKER).write_text("Synthetic data generated by scripts/make_sample_export.py. Not real health data.\n",
                              encoding="utf-8")
    print(f"Synthetic Mi Fitness export: {args.days} days ending {end.isoformat()} (seed {args.seed})")
    print(f"  fitness rows: {len(fitness)}  daily rows: {len(aggregated)}  workouts: {len(sports)}")
    print(f"  output: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
