#!/usr/bin/env python3
"""Stage 3: grade every technician against the strongest full-coverage profile on the team.

The benchmark is whoever has the highest combined throughput (updates/hour and incidents/hour,
each scaled to the team maximum) among technicians with full audit coverage. Everyone is then
expressed as a percent of that profile. This is the strict view: it answers "how far is each
person from the best operating pattern we actually observed", not "how do they rank".

Inputs:   output/metrics_summary.csv
Outputs:  output/benchmark_scores.csv
          output/benchmark_report.md
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, is_full, read_csv_rows, write_csv  # noqa: E402
from common import to_float as _to_float  # noqa: E402

METRICS_PATH = OUT / "metrics_summary.csv"
OUT_SCORES = OUT / "benchmark_scores.csv"
OUT_REPORT = OUT / "benchmark_report.md"


def to_float(value: object) -> float:
    return _to_float(value) or 0.0

# Band is set by productivity as a percent of the benchmark.
BANDS = [(85.0, "Meets standard"), (65.0, "Approaching standard"), (45.0, "Below standard")]
BAND_FLOOR = "Critical gap vs standard"
BAND_NO_SCORE = "No score (data gap)"

# Gap rules, in the order they are reported.
GAP_THROUGHPUT_PROD_PCT = 50.0   # productivity below half of the benchmark
GAP_ONSHIFT_RATIO = 0.85         # on-shift share below 85% of the benchmark's
GAP_FOLLOWUP_RATIO = 0.70        # follow-up per incident below 70% of the benchmark's


def corrections_per_100(row: Dict[str, str]) -> float:
    incidents = to_float(row.get("Incidents"))
    if incidents <= 0:
        return 0.0
    return to_float(row.get("AccuracyCorrections")) / incidents * 100.0


def ratio(value: float, base: float) -> float:
    return value / base if base else 0.0


def pick_benchmark(rows: List[Dict[str, str]]) -> Dict[str, str]:
    full = [r for r in rows if is_full(r["Coverage"])]
    if not full:
        raise SystemExit("No full-coverage technicians in metrics_summary.csv; cannot pick a benchmark.")
    max_upd = max(to_float(r["UpdatesPerHour"]) for r in full) or 1.0
    max_inc = max(to_float(r["IncidentsPerHour"]) for r in full) or 1.0
    return max(full, key=lambda r: to_float(r["UpdatesPerHour"]) / max_upd + to_float(r["IncidentsPerHour"]) / max_inc)


def band_for(prod_pct: float) -> str:
    for floor, label in BANDS:
        if prod_pct >= floor:
            return label
    return BAND_FLOOR


def score_rows(rows: List[Dict[str, str]]) -> tuple[Dict[str, str], List[Dict[str, object]]]:
    bench = pick_benchmark(rows)
    b_upd = to_float(bench["UpdatesPerHour"])
    b_inc = to_float(bench["IncidentsPerHour"])
    b_fpi = to_float(bench["FollowupPerIncident"])
    b_on = to_float(bench["OnShiftPct"])
    b_corr = corrections_per_100(bench)

    scored: List[Dict[str, object]] = []
    for r in rows:
        upd = to_float(r["UpdatesPerHour"])
        inc = to_float(r["IncidentsPerHour"])
        fpi = to_float(r["FollowupPerIncident"])
        on = to_float(r["OnShiftPct"])
        corr = corrections_per_100(r)
        out: Dict[str, object] = {
            "Student": r["Student"],
            "Coverage": r["Coverage"],
            "UpdatesPerHour": round(upd, 2),
            "IncidentsPerHour": round(inc, 2),
            "FollowupPerIncident": round(fpi, 2),
            "OnShiftPct": round(on, 1),
            "AccuracyCorrectionsPer100Incidents": round(corr, 2),
        }
        if not is_full(r["Coverage"]):
            out.update({
                "ProdPctOfBenchmark": "",
                "ExecPctOfBenchmark": "",
                "QualityVsBenchmark": "n/a",
                "BenchmarkBand": BAND_NO_SCORE,
                "PrimaryGaps": "Missing full audit coverage",
            })
            scored.append(out)
            continue

        prod = (ratio(upd, b_upd) + ratio(inc, b_inc)) / 2.0 * 100.0
        execution = (ratio(fpi, b_fpi) + ratio(on, b_on)) / 2.0 * 100.0
        quality = "At/Better" if corr <= b_corr else "Worse"

        gaps: List[str] = []
        if prod < GAP_THROUGHPUT_PROD_PCT:
            gaps.append("Low throughput during scheduled shifts")
        if ratio(on, b_on) < GAP_ONSHIFT_RATIO:
            gaps.append("Low on-shift activity alignment")
        if ratio(fpi, b_fpi) < GAP_FOLLOWUP_RATIO:
            gaps.append("Follow-up rate below benchmark")
        if corr > b_corr:
            gaps.append("Higher correction rate than benchmark")

        out.update({
            "ProdPctOfBenchmark": round(prod, 1),
            "ExecPctOfBenchmark": round(execution, 1),
            "QualityVsBenchmark": quality,
            "BenchmarkBand": band_for(prod),
            "PrimaryGaps": "; ".join(gaps) if gaps else "No major gap vs benchmark",
        })
        scored.append(out)

    def sort_key(row: Dict[str, object]):
        prod = row["ProdPctOfBenchmark"]
        return (0 if row["Coverage"] == "Full" else 1, -(prod if isinstance(prod, float) else 0.0), str(row["Student"]))

    scored.sort(key=sort_key)
    return bench, scored


def fmt_pct(value: object) -> str:
    return f"{value:.1f}%" if isinstance(value, float) else "n/a"


def write_report(path: Path, bench: Dict[str, str], scored: List[Dict[str, object]]) -> None:
    name = bench["Student"]
    lines = [
        "# Benchmark Report",
        "",
        f"Each technician is graded against the strongest full-coverage operating profile on the team ({name}).",
        "Productivity is the mean of updates/hour and incidents/hour as a percent of the benchmark.",
        "Execution is the mean of follow-up per incident and on-shift activity as a percent of the benchmark.",
        "",
        f"## Benchmark baseline ({name})",
        f"- Updates/hour: **{to_float(bench['UpdatesPerHour']):.2f}**",
        f"- Incidents/hour: **{to_float(bench['IncidentsPerHour']):.2f}**",
        f"- Follow-up per incident: **{to_float(bench['FollowupPerIncident']):.2f}**",
        f"- On-shift activity alignment: **{to_float(bench['OnShiftPct']):.1f}%**",
        f"- Accuracy corrections per 100 incidents: **{corrections_per_100(bench):.2f}**",
        "",
        "## Team grading vs benchmark",
        "| Technician | Coverage | Prod % of benchmark | Exec % of benchmark | Quality vs benchmark | Band |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for r in scored:
        lines.append(
            f"| {r['Student']} | {r['Coverage']} | {fmt_pct(r['ProdPctOfBenchmark'])} | "
            f"{fmt_pct(r['ExecPctOfBenchmark'])} | {r['QualityVsBenchmark']} | {r['BenchmarkBand']} |"
        )
    lines += ["", "## Role-fulfillment findings (supervisor focus)"]
    for r in scored:
        lines += [
            f"### {r['Student']}",
            f"- Band: **{r['BenchmarkBand']}**",
            f"- Primary gaps: {r['PrimaryGaps']}",
            (
                f"- Metrics: updates/hr {r['UpdatesPerHour']:.2f}, incidents/hr {r['IncidentsPerHour']:.2f}, "
                f"follow-up/inc {r['FollowupPerIncident']:.2f}, on-shift {r['OnShiftPct']:.1f}%, "
                f"corrections/100inc {r['AccuracyCorrectionsPer100Incidents']:.2f}"
            ),
            "",
        ]
    lines += [
        "## Reading this report",
        "- This is the strict view. It does not adjust for night, weekend or solo shifts; stage 4 does.",
        "- A low band is a prompt for a conversation, not a verdict. Check the evidence log before acting on it.",
        "- Technicians without full audit coverage get no score rather than a low one.",
    ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    rows = read_csv_rows(METRICS_PATH)
    bench, scored = score_rows(rows)
    fields = list(scored[0].keys())
    write_csv(OUT_SCORES, fields, scored)
    write_report(OUT_REPORT, bench, scored)
    print(f"Benchmark: {bench['Student']}")
    print(f"- {OUT_SCORES.name}")
    print(f"- {OUT_REPORT.name}")


if __name__ == "__main__":
    main()
