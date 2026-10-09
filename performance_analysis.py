#!/usr/bin/env python3
"""Generate raw performance-analysis outputs for scorecard standardization.

This script does not write narrative conclusions. It writes auditable analysis tables:
  - student-level standardized metrics
  - metric distribution stats used in normalization
  - pairing context performance (actor view)
  - shift-window performance slices
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Optional, Sequence


ROOT = Path(__file__).resolve().parent

METRICS_PATH = ROOT / "data" / "metrics_summary.csv"
BENCH_PATH = ROOT / "data" / "benchmark_scores.csv"
ENRICHED_PATH = ROOT / "data" / "activity_events.csv"

OUT_DIR = ROOT / "output"
OUT_STUDENT = OUT_DIR / "performance_student_analysis.csv"
OUT_METRIC_DIST = OUT_DIR / "performance_metric_distribution.csv"
OUT_PAIRING = OUT_DIR / "performance_pairing_actor_view.csv"
OUT_SHIFT = OUT_DIR / "performance_shift_window_analysis.csv"
OUT_README = OUT_DIR / "README.md"


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def to_float(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v: Optional[str]) -> Optional[int]:
    f = to_float(v)
    if f is None:
        return None
    return int(round(f))


def safe_div(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return num / den


def ratio_or_none(num: float, den: float) -> Optional[float]:
    if den == 0:
        return None
    return num / den


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def zscore(value: Optional[float], mu: float, sigma: float) -> Optional[float]:
    if value is None:
        return None
    if sigma == 0:
        return 0.0
    return (value - mu) / sigma


def split_peers(s: str) -> List[str]:
    s = (s or "").strip()
    if not s:
        return []
    return [p.strip() for p in s.split("|") if p.strip()]


def shift_window(hour: Optional[int]) -> str:
    if hour is None:
        return "Unknown"
    if 8 <= hour <= 11:
        return "Morning(08-11)"
    if 12 <= hour <= 16:
        return "Afternoon(12-16)"
    if 17 <= hour <= 19:
        return "Evening(17-19)"
    if 20 <= hour <= 23:
        return "Night(20-23)"
    return "Other"


def mean_or_blank(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return mean(vals)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    metrics_rows = read_csv_rows(METRICS_PATH)
    bench_rows = read_csv_rows(BENCH_PATH)
    event_rows = read_csv_rows(ENRICHED_PATH)

    bench_by_student = {r["Student"].strip(): r for r in bench_rows}
    metrics_by_student = {r["Student"].strip(): r for r in metrics_rows}

    roster = sorted(
        set(metrics_by_student.keys())
        | {r["Student"].strip() for r in event_rows if r.get("Student", "").strip()}
        | set(bench_by_student.keys())
    )

    # Event-derived per-student stats.
    events_by_student: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in event_rows:
        student = r["Student"].strip()
        if student:
            events_by_student[student].append(r)

    student_rows: List[Dict[str, object]] = []
    for student in roster:
        m = metrics_by_student.get(student, {})
        b = bench_by_student.get(student, {})
        evs = events_by_student.get(student, [])

        sample_events = len(evs)
        sample_incidents = {r["Incident"] for r in evs if r.get("Incident")}
        weekend_events = sum(1 for r in evs if to_int(r.get("IsWeekend")) == 1)
        night_events = sum(1 for r in evs if to_int(r.get("IsNightByClock")) == 1)
        slow75_events = sum(1 for r in evs if to_int(r.get("SlowIncidentNonZeroP75")) == 1)
        slow90_events = sum(1 for r in evs if to_int(r.get("SlowIncidentNonZeroP90")) == 1)
        issue_events = sum(1 for r in evs if (r.get("Category", "").strip().lower() == "issue"))
        strength_events = sum(1 for r in evs if (r.get("Category", "").strip().lower() == "strength"))
        on_sched_events = sum(1 for r in evs if to_int(r.get("StudentScheduledInSlot")) == 1)
        off_sched_events = sum(1 for r in evs if to_int(r.get("StudentScheduledInSlot")) == 0)

        team_sizes = [to_int(r.get("ScheduledTeamSize")) for r in evs]
        team_sizes_clean = [x for x in team_sizes if x is not None and x > 0]
        solo_events = sum(1 for x in team_sizes_clean if x == 1)
        peers = set()
        for r in evs:
            for p in split_peers(r.get("CoScheduledPeers", "")):
                peers.add(p)

        incidents_n = to_float(m.get("Incidents")) or 0.0
        acc_corr = to_float(m.get("AccuracyCorrections")) or 0.0
        corr_per_100 = safe_div(acc_corr * 100.0, incidents_n) if incidents_n > 0 else None

        row = {
            "Student": student,
            "Coverage": m.get("Coverage", b.get("Coverage", "")),
            "FullCoverageForStandardization": int(
                str(m.get("Coverage", b.get("Coverage", ""))).strip().lower() == "full"
            ),
            "ScheduledHours": to_float(m.get("ScheduledHours")),
            "Updates": to_float(m.get("Updates")),
            "Incidents": to_float(m.get("Incidents")),
            "UpdatesPerHour": to_float(m.get("UpdatesPerHour")),
            "IncidentsPerHour": to_float(m.get("IncidentsPerHour")),
            "FollowupPerIncident": to_float(m.get("FollowupPerIncident")),
            "OnShiftPct": to_float(m.get("OnShiftPct")),
            "AccuracyCorrections": to_float(m.get("AccuracyCorrections")),
            "CorrectionsPer100IncidentsCalc": corr_per_100,
            "SampleEvents": sample_events,
            "SampleUniqueIncidents": len(sample_incidents),
            "WeekendEvents": weekend_events,
            "NightEvents": night_events,
            "SlowIncidentNonZeroP75Events": slow75_events,
            "SlowIncidentNonZeroP90Events": slow90_events,
            "WeekendEventShare": ratio_or_none(weekend_events, sample_events),
            "NightEventShare": ratio_or_none(night_events, sample_events),
            "SlowIncidentNonZeroP75EventShare": ratio_or_none(slow75_events, sample_events),
            "SlowIncidentNonZeroP90EventShare": ratio_or_none(slow90_events, sample_events),
            "IssueEvents": issue_events,
            "StrengthEvents": strength_events,
            "IssueRateSample": ratio_or_none(issue_events, sample_events),
            "StrengthRateSample": ratio_or_none(strength_events, sample_events),
            "OnScheduledEventRate": ratio_or_none(on_sched_events, sample_events),
            "OffScheduledEventRate": ratio_or_none(off_sched_events, sample_events),
            "MeanScheduledTeamSizeAtActivity": mean(team_sizes_clean) if team_sizes_clean else None,
            "SoloEventShare": ratio_or_none(solo_events, len(team_sizes_clean)),
            "UniqueCoScheduledPeersInSample": len(peers),
            "ProdPctOfBenchmark": to_float(b.get("ProdPctOfBenchmark")),
            "ExecPctOfBenchmark": to_float(b.get("ExecPctOfBenchmark")),
            "QualityVsBenchmark": b.get("QualityVsBenchmark", ""),
            "BenchmarkBand": b.get("BenchmarkBand", ""),
        }
        student_rows.append(row)

    # Metric normalization config (data-driven, equal-weight components).
    metric_specs = [
        ("UpdatesPerHour", True),
        ("IncidentsPerHour", True),
        ("FollowupPerIncident", True),
        ("OnShiftPct", True),
        ("CorrectionsPer100IncidentsCalc", False),
        ("IssueRateSample", False),
        ("NightEventShare", True),  # context load (higher means heavier context)
        ("WeekendEventShare", True),  # context load
        ("SlowIncidentNonZeroP75EventShare", True),  # context load
        ("SoloEventShare", True),  # context load
    ]

    # Use only Full-coverage students for normalization baseline.
    full_rows = [r for r in student_rows if str(r.get("Coverage", "")).strip().lower() == "full"]
    metric_distribution = []
    metric_stats = {}
    for metric, higher_is_better in metric_specs:
        vals = [float(r[metric]) for r in full_rows if r.get(metric) is not None]
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        mu = mean(vals_sorted) if vals_sorted else 0.0
        sigma = pstdev(vals_sorted) if len(vals_sorted) > 1 else 0.0
        metric_stats[metric] = (mu, sigma, higher_is_better)
        metric_distribution.append(
            {
                "Metric": metric,
                "HigherIsBetter": int(higher_is_better),
                "N": n,
                "Mean": mu,
                "StdDev": sigma,
                "Min": vals_sorted[0] if vals_sorted else "",
                "P25": quantile(vals_sorted, 0.25) if vals_sorted else "",
                "Median": quantile(vals_sorted, 0.50) if vals_sorted else "",
                "P75": quantile(vals_sorted, 0.75) if vals_sorted else "",
                "Max": vals_sorted[-1] if vals_sorted else "",
            }
        )

    # Add z-scores and standardized components.
    for r in student_rows:
        if int(r.get("FullCoverageForStandardization", 0)) != 1:
            for metric, _ in metric_specs:
                r[f"z_{metric}"] = None
            r["ProductivityZ"] = None
            r["ExecutionZ"] = None
            r["CorePerformanceZ"] = None
            r["ContextLoadZ"] = None
            r["ContextAdjustedPerformanceZ"] = None
            continue

        oriented_z = {}
        for metric, _ in metric_specs:
            mu, sigma, higher_is_better = metric_stats[metric]
            val = r.get(metric)
            z = zscore(float(val), mu, sigma) if val is not None else None
            if z is not None and not higher_is_better:
                z = -z
            oriented_z[f"z_{metric}"] = z
            r[f"z_{metric}"] = z

        productivity_component = mean_or_blank(
            [oriented_z["z_UpdatesPerHour"], oriented_z["z_IncidentsPerHour"]]
        )
        execution_component = mean_or_blank(
            [
                oriented_z["z_FollowupPerIncident"],
                oriented_z["z_OnShiftPct"],
                oriented_z["z_CorrectionsPer100IncidentsCalc"],
                oriented_z["z_IssueRateSample"],
            ]
        )
        context_load_component = mean_or_blank(
            [
                oriented_z["z_NightEventShare"],
                oriented_z["z_WeekendEventShare"],
                oriented_z["z_SlowIncidentNonZeroP75EventShare"],
                oriented_z["z_SoloEventShare"],
            ]
        )
        core_perf = mean_or_blank([productivity_component, execution_component])
        context_adjusted = (
            (core_perf + context_load_component)
            if core_perf is not None and context_load_component is not None
            else None
        )

        r["ProductivityZ"] = productivity_component
        r["ExecutionZ"] = execution_component
        r["CorePerformanceZ"] = core_perf
        r["ContextLoadZ"] = context_load_component
        r["ContextAdjustedPerformanceZ"] = context_adjusted

    # Ranking columns (higher is stronger on that component).
    def add_rank(rows: List[Dict[str, object]], metric: str, col_name: str) -> None:
        vals = [(i, rows[i].get(metric)) for i in range(len(rows)) if rows[i].get(metric) is not None]
        vals_sorted = sorted(vals, key=lambda x: x[1], reverse=True)
        rank = 0
        prev = None
        for pos, (idx, val) in enumerate(vals_sorted, start=1):
            if prev is None or val != prev:
                rank = pos
            rows[idx][col_name] = rank
            prev = val
        for i in range(len(rows)):
            rows[i].setdefault(col_name, "")

    add_rank(student_rows, "ProductivityZ", "RankProductivityZ")
    add_rank(student_rows, "ExecutionZ", "RankExecutionZ")
    add_rank(student_rows, "CorePerformanceZ", "RankCorePerformanceZ")
    add_rank(student_rows, "ContextLoadZ", "RankContextLoadZ")
    add_rank(student_rows, "ContextAdjustedPerformanceZ", "RankContextAdjustedPerformanceZ")

    # Pairing actor-view analysis: performance when student acts while peer is co-scheduled.
    pair_agg: Dict[tuple, Dict[str, object]] = {}
    for r in event_rows:
        student = r.get("Student", "").strip()
        if not student:
            continue
        peers = split_peers(r.get("CoScheduledPeers", ""))
        if not peers:
            continue
        incident = r.get("Incident", "")
        is_issue = 1 if (r.get("Category", "").strip().lower() == "issue") else 0
        is_weekend = 1 if to_int(r.get("IsWeekend")) == 1 else 0
        is_night = 1 if to_int(r.get("IsNightByClock")) == 1 else 0
        is_slow = 1 if to_int(r.get("SlowIncidentNonZeroP75")) == 1 else 0
        span_min = to_float(r.get("IncidentObservedSpanMinutes"))
        delta_prev = to_float(r.get("MinutesSincePreviousIncidentAction"))
        for peer in peers:
            key = (student, peer)
            if key not in pair_agg:
                pair_agg[key] = {
                    "Student": student,
                    "Peer": peer,
                    "EventsWithPeer": 0,
                    "Incidents": set(),
                    "IssueEvents": 0,
                    "WeekendEvents": 0,
                    "NightEvents": 0,
                    "SlowEvents": 0,
                    "SpanVals": [],
                    "DeltaVals": [],
                }
            a = pair_agg[key]
            a["EventsWithPeer"] += 1
            if incident:
                a["Incidents"].add(incident)
            a["IssueEvents"] += is_issue
            a["WeekendEvents"] += is_weekend
            a["NightEvents"] += is_night
            a["SlowEvents"] += is_slow
            if span_min is not None:
                a["SpanVals"].append(span_min)
            if delta_prev is not None:
                a["DeltaVals"].append(delta_prev)

    pairing_rows = []
    for (_, _), a in pair_agg.items():
        events_n = a["EventsWithPeer"]
        pairing_rows.append(
            {
                "Student": a["Student"],
                "Peer": a["Peer"],
                "EventsWithPeer": events_n,
                "UniqueIncidentsWithPeer": len(a["Incidents"]),
                "IssueRateWithPeer": safe_div(a["IssueEvents"], events_n),
                "WeekendShareWithPeer": safe_div(a["WeekendEvents"], events_n),
                "NightShareWithPeer": safe_div(a["NightEvents"], events_n),
                "SlowIncidentShareWithPeer": safe_div(a["SlowEvents"], events_n),
                "MeanIncidentObservedSpanMinutesWithPeer": mean(a["SpanVals"]) if a["SpanVals"] else "",
                "MeanMinutesSincePreviousIncidentActionWithPeer": mean(a["DeltaVals"]) if a["DeltaVals"] else "",
            }
        )

    pairing_rows.sort(key=lambda r: (r["Student"], -r["EventsWithPeer"], r["Peer"]))

    # Shift-window analysis by student x day type x time window.
    shift_agg: Dict[tuple, Dict[str, object]] = {}
    for r in event_rows:
        student = r.get("Student", "").strip()
        if not student:
            continue
        hour = to_int(r.get("Hour24"))
        day_type = "Weekend" if to_int(r.get("IsWeekend")) == 1 else "Weekday"
        window = shift_window(hour)
        key = (student, day_type, window)
        if key not in shift_agg:
            shift_agg[key] = {
                "Student": student,
                "DayType": day_type,
                "ShiftWindow": window,
                "Events": 0,
                "Incidents": set(),
                "IssueEvents": 0,
                "SlowEvents": 0,
                "OnSchedEvents": 0,
                "TeamSizes": [],
            }
        a = shift_agg[key]
        a["Events"] += 1
        if r.get("Incident"):
            a["Incidents"].add(r["Incident"])
        if r.get("Category", "").strip().lower() == "issue":
            a["IssueEvents"] += 1
        if to_int(r.get("SlowIncidentNonZeroP75")) == 1:
            a["SlowEvents"] += 1
        if to_int(r.get("StudentScheduledInSlot")) == 1:
            a["OnSchedEvents"] += 1
        ts = to_float(r.get("ScheduledTeamSize"))
        if ts is not None:
            a["TeamSizes"].append(ts)

    shift_rows = []
    for (_, _, _), a in shift_agg.items():
        n = a["Events"]
        shift_rows.append(
            {
                "Student": a["Student"],
                "DayType": a["DayType"],
                "ShiftWindow": a["ShiftWindow"],
                "Events": n,
                "UniqueIncidents": len(a["Incidents"]),
                "IssueRate": safe_div(a["IssueEvents"], n),
                "SlowIncidentShare": safe_div(a["SlowEvents"], n),
                "OnScheduledRate": safe_div(a["OnSchedEvents"], n),
                "MeanScheduledTeamSize": mean(a["TeamSizes"]) if a["TeamSizes"] else "",
            }
        )

    shift_rows.sort(key=lambda r: (r["Student"], r["DayType"], r["ShiftWindow"]))

    # Write outputs.
    student_fields = [
        "Student",
        "Coverage",
        "FullCoverageForStandardization",
        "ScheduledHours",
        "Updates",
        "Incidents",
        "UpdatesPerHour",
        "IncidentsPerHour",
        "FollowupPerIncident",
        "OnShiftPct",
        "AccuracyCorrections",
        "CorrectionsPer100IncidentsCalc",
        "SampleEvents",
        "SampleUniqueIncidents",
        "WeekendEvents",
        "NightEvents",
        "SlowIncidentNonZeroP75Events",
        "SlowIncidentNonZeroP90Events",
        "WeekendEventShare",
        "NightEventShare",
        "SlowIncidentNonZeroP75EventShare",
        "SlowIncidentNonZeroP90EventShare",
        "IssueEvents",
        "StrengthEvents",
        "IssueRateSample",
        "StrengthRateSample",
        "OnScheduledEventRate",
        "OffScheduledEventRate",
        "MeanScheduledTeamSizeAtActivity",
        "SoloEventShare",
        "UniqueCoScheduledPeersInSample",
        "ProdPctOfBenchmark",
        "ExecPctOfBenchmark",
        "QualityVsBenchmark",
        "BenchmarkBand",
        "z_UpdatesPerHour",
        "z_IncidentsPerHour",
        "z_FollowupPerIncident",
        "z_OnShiftPct",
        "z_CorrectionsPer100IncidentsCalc",
        "z_IssueRateSample",
        "z_NightEventShare",
        "z_WeekendEventShare",
        "z_SlowIncidentNonZeroP75EventShare",
        "z_SoloEventShare",
        "ProductivityZ",
        "ExecutionZ",
        "CorePerformanceZ",
        "ContextLoadZ",
        "ContextAdjustedPerformanceZ",
        "RankProductivityZ",
        "RankExecutionZ",
        "RankCorePerformanceZ",
        "RankContextLoadZ",
        "RankContextAdjustedPerformanceZ",
    ]
    with OUT_STUDENT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=student_fields)
        w.writeheader()
        for r in sorted(student_rows, key=lambda x: x["Student"]):
            w.writerow({k: r.get(k, "") for k in student_fields})

    metric_fields = ["Metric", "HigherIsBetter", "N", "Mean", "StdDev", "Min", "P25", "Median", "P75", "Max"]
    with OUT_METRIC_DIST.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=metric_fields)
        w.writeheader()
        w.writerows(metric_distribution)

    pair_fields = [
        "Student",
        "Peer",
        "EventsWithPeer",
        "UniqueIncidentsWithPeer",
        "IssueRateWithPeer",
        "WeekendShareWithPeer",
        "NightShareWithPeer",
        "SlowIncidentShareWithPeer",
        "MeanIncidentObservedSpanMinutesWithPeer",
        "MeanMinutesSincePreviousIncidentActionWithPeer",
    ]
    with OUT_PAIRING.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=pair_fields)
        w.writeheader()
        w.writerows(pairing_rows)

    shift_fields = [
        "Student",
        "DayType",
        "ShiftWindow",
        "Events",
        "UniqueIncidents",
        "IssueRate",
        "SlowIncidentShare",
        "OnScheduledRate",
        "MeanScheduledTeamSize",
    ]
    with OUT_SHIFT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=shift_fields)
        w.writeheader()
        w.writerows(shift_rows)

    readme = """# Performance Analysis Raw Output

Generated by `performance_analysis.py`.

Files:
- `performance_student_analysis.csv`: student-level performance and standardized metrics.
- `performance_metric_distribution.csv`: normalization baselines (mean/std/quantiles) per metric.
- `performance_pairing_actor_view.csv`: per-student performance context while co-scheduled with each peer.
- `performance_shift_window_analysis.csv`: performance sliced by weekday/weekend and time window.

Normalization notes:
- Z-scores are computed using only `Coverage == Full` students.
- For "lower is better" metrics, z-scores are inverted so higher standardized values always mean stronger performance.
- Context-load metrics (night/weekend/slow/solo shares) are standardized separately to expose workload context.
- `ContextAdjustedPerformanceZ = CorePerformanceZ + ContextLoadZ`.

This packet is intended as raw analysis evidence for scorecard standardization.
"""
    OUT_README.write_text(readme, encoding="utf-8")

    print("Wrote:")
    for p in [OUT_STUDENT, OUT_METRIC_DIST, OUT_PAIRING, OUT_SHIFT, OUT_README]:
        print(f"- {p.name}")


if __name__ == "__main__":
    main()
