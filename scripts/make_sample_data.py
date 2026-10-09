"""Generate synthetic inputs for performance_analysis.py.

No real staff data is included in this repository. This script writes three
made-up input files for 15 fictional technicians (Tech 01 to Tech 15) so the
model can run end to end:

    data/metrics_summary.csv     semester totals per technician
    data/benchmark_scores.csv    comparison against a reference technician
    data/activity_events.csv     sampled ticket actions with shift context

Usage:
    python3 scripts/make_sample_data.py
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEED = 11
TECHS = [f"Tech {i:02d}" for i in range(1, 16)]
PARTIAL = {"Tech 13", "Tech 14", "Tech 15"}  # only partial records, excluded from the baseline


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    rng = random.Random(SEED)
    DATA.mkdir(exist_ok=True)

    # Each technician gets a skill level and a schedule profile. Night, weekend
    # and solo-heavy schedules lower raw throughput, which is what the model
    # is built to account for.
    profile = {}
    for t in TECHS:
        profile[t] = {
            "skill": rng.gauss(0, 1),
            "night": rng.choice([0.05, 0.15, 0.35, 0.55]),
            "weekend": rng.choice([0.0, 0.15, 0.35, 0.6]),
            "solo": rng.choice([0.05, 0.2, 0.45]),
        }

    metrics, bench = [], []
    for t in TECHS:
        p = profile[t]
        load = p["night"] + p["weekend"] + p["solo"]
        hours = rng.choice([40, 52, 61, 75, 88])
        ups_hr = max(0.8, 3.2 + 0.7 * p["skill"] - 1.1 * load + rng.gauss(0, 0.3))
        inc_hr = max(0.3, 1.1 + 0.25 * p["skill"] - 0.4 * load + rng.gauss(0, 0.1))
        incidents = round(inc_hr * hours)
        updates = round(ups_hr * hours)
        followup = round(incidents * max(0.3, 0.65 + 0.08 * p["skill"] + rng.gauss(0, 0.05)))
        corrections = max(0, round(incidents * max(0.0, 0.05 - 0.015 * p["skill"] + rng.gauss(0, 0.01))))
        metrics.append({
            "Student": t,
            "Coverage": "Partial" if t in PARTIAL else "Full",
            "ScheduledHours": f"{hours:.1f}",
            "Updates": updates,
            "Incidents": incidents,
            "UpdatesPerHour": f"{updates / hours:.2f}",
            "IncidentsPerHour": f"{incidents / hours:.2f}",
            "OnShiftPct": f"{min(100, 90 + 3 * p['skill'] + rng.gauss(0, 2)):.1f}",
            "FollowupValid": followup,
            "FollowupPerIncident": f"{followup / incidents:.2f}",
            "AccuracyCorrections": corrections,
        })

    ref = max(metrics[:12], key=lambda r: float(r["UpdatesPerHour"]))
    for m in metrics:
        prod = 100 * float(m["UpdatesPerHour"]) / float(ref["UpdatesPerHour"])
        execution = 100 * float(m["FollowupPerIncident"]) / float(ref["FollowupPerIncident"])
        bench.append({
            "Student": m["Student"],
            "Coverage": m["Coverage"],
            "ProdPctOfBenchmark": f"{prod:.1f}",
            "ExecPctOfBenchmark": f"{execution:.1f}",
            "QualityVsBenchmark": "At/Better" if execution >= 95 else "Below",
            "BenchmarkBand": "Meets benchmark" if prod >= 80 and execution >= 90 else "Below benchmark",
        })

    events = []
    start = datetime(2025, 8, 25)
    incident_no = 10000
    for t in TECHS:
        p = profile[t]
        for _ in range(rng.randint(10, 24)):
            incident_no += 1
            weekend = rng.random() < p["weekend"]
            night = rng.random() < p["night"]
            day = start + timedelta(days=rng.randint(0, 100))
            while (day.weekday() >= 5) != weekend:
                day += timedelta(days=1)
            hour = rng.randint(20, 23) if night else rng.randint(8, 19)
            dt = day.replace(hour=hour, minute=rng.randint(0, 59))
            solo = rng.random() < p["solo"]
            peers = [] if solo else rng.sample([x for x in TECHS if x != t], rng.randint(1, 3))
            issue = rng.random() < max(0.01, 0.06 - 0.03 * p["skill"])
            slow = rng.random() < (0.15 + 0.25 * (night or weekend or solo))
            events.append({
                "Student": t,
                "Incident": f"INC{incident_no}",
                "Hour24": hour,
                "IsWeekend": int(weekend),
                "IsNightByClock": int(night),
                "Category": "issue" if issue else "strength",
                "StudentScheduledInSlot": int(rng.random() > 0.02),
                "ScheduledTeamSize": 1 + len(peers),
                "CoScheduledPeers": "|".join(peers),
                "SlowIncidentNonZeroP75": int(slow),
                "SlowIncidentNonZeroP90": int(slow and rng.random() < 0.4),
                "IncidentObservedSpanMinutes": round(rng.lognormvariate(4.5, 1.2), 1),
                "MinutesSincePreviousIncidentAction": round(rng.lognormvariate(3.5, 1.3), 1),
            })

    write_csv(DATA / "metrics_summary.csv", list(metrics[0]), metrics)
    write_csv(DATA / "benchmark_scores.csv", list(bench[0]), bench)
    write_csv(DATA / "activity_events.csv", list(events[0]), events)
    print(f"Wrote {len(metrics)} technicians and {len(events)} sampled actions to {DATA}")


if __name__ == "__main__":
    main()
