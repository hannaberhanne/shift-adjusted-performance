#!/usr/bin/env python3
"""Stage 5: build the weighted 0-100 scorecard workbook, with its schema, confidence tiers and raw evidence.

Two components, each a weighted mean of metric scores:
  Queue & Ticket Handling (60%)   7 metrics
  Knowledge & Accuracy    (40%)   6 metrics
Each metric score is Excel's PERCENTRANK.INC of the technician's value within the full-coverage cohort,
times 100 (or 100 minus that when lower is better). Technicians without full coverage get no score.

Every score ships with a confidence tier based on how much evidence stands behind it, and with the
raw observation entries it came from, so a reviewer can check any number against the tickets.

Inputs:   output/performance_student_analysis.csv, output/evidence_activity_enriched.csv
Outputs:  output/standardized_scorecard.xlsx
          output/scorecard_summary.csv
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, percentrank_inc, quantile, read_csv_rows, to_float, write_csv  # noqa: E402

PERF_PATH = OUT / "performance_student_analysis.csv"
EVENT_PATH = OUT / "evidence_activity_enriched.csv"
OUT_PATH = OUT / "standardized_scorecard.xlsx"
OUT_SUMMARY = OUT / "scorecard_summary.csv"


QUEUE_RULES = {
    "Follow-up valid (Comment added)",
    "Initial handling response",
    "Resolved ticket",
    "Closure documentation",
    "Rerouted to correct department/person",
    "Reopened ticket (neutral per policy)",
}

KNOWLEDGE_RULES = {
    "Category correction needed",
    "Subcategory correction needed",
    "Rerouted to correct department/person",
}

KNOWLEDGE_ACTIONS = {"Category changed", "Subcategory changed"}


def safe_mean(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def is_queue_event(row: Dict[str, str]) -> bool:
    action = (row.get("ActionType") or "").strip()
    rule = (row.get("RuleMatched") or "").strip()
    if (
        action.startswith("Assignee")
        or action.startswith("Assignment")
        or action.startswith("State")
        or action.startswith("Comment")
        or action.startswith("Resolution")
    ):
        return True
    if rule in QUEUE_RULES:
        return True
    return False


def is_knowledge_event(row: Dict[str, str]) -> bool:
    action = (row.get("ActionType") or "").strip()
    rule = (row.get("RuleMatched") or "").strip()
    if rule in KNOWLEDGE_RULES:
        return True
    if action in KNOWLEDGE_ACTIONS:
        return True
    return False


def fmt_num(v: Optional[float], places: int = 2) -> str:
    if v is None:
        return ""
    return f"{v:.{places}f}"


def wrap_sheet(
    ws,
    wrap_cols: Optional[Sequence[int]] = None,
    freeze: Optional[str] = "A2",
    autofilter: bool = True,
    width_scan_rows: int = 300,
) -> None:
    if ws.max_row == 0:
        return
    for c in ws[1]:
        c.font = Font(bold=True)
    if freeze:
        ws.freeze_panes = freeze
    if autofilter:
        ws.auto_filter.ref = ws.dimensions
    max_cols = ws.max_column
    for c in range(1, max_cols + 1):
        col_letter = get_column_letter(c)
        values = [str(ws.cell(row=r, column=c).value or "") for r in range(1, min(ws.max_row, width_scan_rows) + 1)]
        width = min(80, max(12, max(len(v) for v in values) + 2))
        ws.column_dimensions[col_letter].width = width
    if wrap_cols:
        for c in wrap_cols:
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
        # Give wrapped rows more vertical space by default.
        for r in range(2, ws.max_row + 1):
            ws.row_dimensions[r].height = 60


def join_lines(lines: Sequence[str]) -> str:
    if not lines:
        return "No qualifying mined entries in available sample."
    return "\n".join(lines)


def build_summary_text(items: List[str], default_text: str) -> str:
    filtered = [i for i in items if i]
    if not filtered:
        return default_text
    return "; ".join(filtered[:4])


def confidence_from_counts(
    coverage: str,
    sample_events: int,
    sample_incidents: int,
    queue_events: int,
    knowledge_events: int,
) -> Tuple[float, str]:
    coverage_factor = 1.0 if coverage.lower() == "full" else 0.5
    sample_factor = min(1.0, sample_events / 20.0)
    incident_factor = min(1.0, sample_incidents / 10.0)
    subrow_factor = 0.5 * min(1.0, queue_events / 10.0) + 0.5 * min(1.0, knowledge_events / 6.0)
    score = 100.0 * coverage_factor * (0.4 * sample_factor + 0.3 * incident_factor + 0.3 * subrow_factor)
    if (
        coverage.lower() == "full"
        and sample_events >= 20
        and sample_incidents >= 10
        and queue_events >= 10
        and knowledge_events >= 6
    ):
        tier = "High"
    elif (
        coverage.lower() == "full"
        and sample_events >= 10
        and sample_incidents >= 5
        and queue_events >= 5
        and knowledge_events >= 3
    ):
        tier = "Medium"
    else:
        tier = "Low"
    return score, tier


def event_line(row: Dict[str, str]) -> str:
    return (
        f"{row.get('DateTimeParsed','')} | {row.get('Incident','')} | "
        f"{row.get('ActionType','')} | {row.get('RuleMatched','')} | "
        f"outcome={row.get('Outcome','')} | cat={row.get('Category','')} | "
        f"wknd={row.get('IsWeekend','')} | night={row.get('IsNightByClock','')} | "
        f"inSlot={row.get('StudentScheduledInSlot','')} | peers={row.get('CoScheduledPeers','')}"
    )


def main() -> None:
    perf_rows = read_csv_rows(PERF_PATH)
    event_rows = read_csv_rows(EVENT_PATH)

    perf_by_student = {r["Student"].strip(): r for r in perf_rows}
    students = sorted(perf_by_student.keys() | {r["Student"].strip() for r in event_rows if r.get("Student", "").strip()})

    events_by_student: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in event_rows:
        s = (r.get("Student") or "").strip()
        if s:
            events_by_student[s].append(r)
    for s in events_by_student:
        events_by_student[s].sort(key=lambda r: (r.get("DateTimeParsed", ""), r.get("Incident", "")))

    # Build student subrow metrics used by schema scoring.
    student_stats: Dict[str, Dict[str, Optional[float]]] = {}
    queue_raw_rows = []
    knowledge_raw_rows = []

    for student in students:
        perf = perf_by_student.get(student, {})
        events = events_by_student.get(student, [])

        queue_events = [r for r in events if is_queue_event(r)]
        knowledge_events = [r for r in events if is_knowledge_event(r)]

        queue_incidents = {r.get("Incident", "") for r in queue_events if r.get("Incident")}
        knowledge_incidents = {r.get("Incident", "") for r in knowledge_events if r.get("Incident")}

        queue_issues = sum(1 for r in queue_events if (r.get("Category", "").strip().lower() == "issue"))
        queue_issue_rate = (queue_issues / len(queue_events)) if queue_events else None

        on_sched_flags = [to_float(r.get("StudentScheduledInSlot")) for r in events]
        on_sched_clean = [x for x in on_sched_flags if x is not None]
        on_sched_rate = (sum(on_sched_clean) / len(on_sched_clean)) if on_sched_clean else None

        delta_vals = [to_float(r.get("MinutesSincePreviousIncidentAction")) for r in queue_events]
        delta_clean = [d for d in delta_vals if d is not None]
        mean_delta_prev = safe_mean(delta_clean)

        category_corr = sum(
            1
            for r in knowledge_events
            if (r.get("RuleMatched", "").strip() == "Category correction needed")
            or (r.get("ActionType", "").strip() == "Category changed")
        )
        subcategory_corr = sum(
            1
            for r in knowledge_events
            if (r.get("RuleMatched", "").strip() == "Subcategory correction needed")
            or (r.get("ActionType", "").strip() == "Subcategory changed")
        )
        category_corr_rate = (category_corr / len(knowledge_incidents)) if knowledge_incidents else None
        subcategory_corr_rate = (subcategory_corr / len(knowledge_incidents)) if knowledge_incidents else None

        reroute_total = sum(
            1 for r in knowledge_events if (r.get("RuleMatched", "").strip() == "Rerouted to correct department/person")
        )
        reroute_good = sum(
            1
            for r in knowledge_events
            if (r.get("RuleMatched", "").strip() == "Rerouted to correct department/person")
            and (r.get("Outcome", "").strip().lower() == "good")
        )
        reroute_precision = (reroute_good / reroute_total) if reroute_total else None

        knowledge_issues = sum(1 for r in knowledge_events if (r.get("Category", "").strip().lower() == "issue"))
        knowledge_issue_rate = (knowledge_issues / len(knowledge_events)) if knowledge_events else None

        for r in queue_events:
            queue_raw_rows.append(
                {
                    "Student": student,
                    "DateTimeParsed": r.get("DateTimeParsed", ""),
                    "Incident": r.get("Incident", ""),
                    "ActionType": r.get("ActionType", ""),
                    "RuleMatched": r.get("RuleMatched", ""),
                    "Outcome": r.get("Outcome", ""),
                    "Category": r.get("Category", ""),
                    "IsWeekend": r.get("IsWeekend", ""),
                    "IsNightByClock": r.get("IsNightByClock", ""),
                    "StudentScheduledInSlot": r.get("StudentScheduledInSlot", ""),
                    "CoScheduledPeers": r.get("CoScheduledPeers", ""),
                    "MinutesSincePreviousIncidentAction": r.get("MinutesSincePreviousIncidentAction", ""),
                }
            )

        for r in knowledge_events:
            knowledge_raw_rows.append(
                {
                    "Student": student,
                    "DateTimeParsed": r.get("DateTimeParsed", ""),
                    "Incident": r.get("Incident", ""),
                    "ActionType": r.get("ActionType", ""),
                    "RuleMatched": r.get("RuleMatched", ""),
                    "Outcome": r.get("Outcome", ""),
                    "Category": r.get("Category", ""),
                    "IsWeekend": r.get("IsWeekend", ""),
                    "IsNightByClock": r.get("IsNightByClock", ""),
                    "StudentScheduledInSlot": r.get("StudentScheduledInSlot", ""),
                    "CoScheduledPeers": r.get("CoScheduledPeers", ""),
                }
            )

        student_stats[student] = {
            "UpdatesPerHour": to_float(perf.get("UpdatesPerHour")),
            "IncidentsPerHour": to_float(perf.get("IncidentsPerHour")),
            "FollowupPerIncident": to_float(perf.get("FollowupPerIncident")),
            "OnShiftPct": to_float(perf.get("OnShiftPct")),
            "OnScheduledEventRate": on_sched_rate,
            "QueueMeanMinutesSincePrevAction": mean_delta_prev,
            "QueueIssueRate": queue_issue_rate,
            "CorrectionsPer100IncidentsCalc": to_float(perf.get("CorrectionsPer100IncidentsCalc")),
            "IssueRateSample": to_float(perf.get("IssueRateSample")),
            "CategoryCorrectionRate": category_corr_rate,
            "SubcategoryCorrectionRate": subcategory_corr_rate,
            "ReroutePrecisionRate": reroute_precision,
            "KnowledgeIssueRate": knowledge_issue_rate,
            "Coverage": (perf.get("Coverage") or "").strip(),
            "SampleEvents": int(float(perf.get("SampleEvents") or 0)),
            "SampleUniqueIncidents": int(float(perf.get("SampleUniqueIncidents") or 0)),
            "QueueEntryCount": len(queue_events),
            "KnowledgeEntryCount": len(knowledge_events),
            "NightEventShare": to_float(perf.get("NightEventShare")),
            "WeekendEventShare": to_float(perf.get("WeekendEventShare")),
            "SlowIncidentNonZeroP75EventShare": to_float(perf.get("SlowIncidentNonZeroP75EventShare")),
        }

    full_students = [s for s in students if student_stats[s]["Coverage"].lower() == "full"]

    queue_metrics = [
        ("UpdatesPerHour", True, 0.20, "Updates created per scheduled hour."),
        ("IncidentsPerHour", True, 0.20, "Unique incidents touched per scheduled hour."),
        ("FollowupPerIncident", True, 0.15, "Valid follow-up comments per incident."),
        ("OnShiftPct", True, 0.15, "Percent of activity aligned to scheduled windows."),
        ("OnScheduledEventRate", True, 0.10, "Share of mined events occurring on scheduled slot."),
        ("QueueMeanMinutesSincePrevAction", False, 0.10, "Average minutes between consecutive actions on queue/ticket events."),
        ("QueueIssueRate", False, 0.10, "Issue-tagged share among queue/ticket observation entries."),
    ]
    knowledge_metrics = [
        ("CorrectionsPer100IncidentsCalc", False, 0.30, "Accuracy corrections per 100 incidents (calculated)."),
        ("IssueRateSample", False, 0.25, "Issue-tagged share across all mined sample events."),
        ("CategoryCorrectionRate", False, 0.15, "Category corrections per incident in knowledge/accuracy observations."),
        ("SubcategoryCorrectionRate", False, 0.10, "Subcategory corrections per incident in knowledge/accuracy observations."),
        ("ReroutePrecisionRate", True, 0.10, "Good reroutes divided by all reroute observations."),
        ("KnowledgeIssueRate", False, 0.10, "Issue-tagged share among knowledge/accuracy observation entries."),
    ]

    # Build normalization ranges (full coverage only).
    metric_ranges: Dict[str, List[float]] = {}
    for metric, _, _, _ in queue_metrics + knowledge_metrics:
        vals = [
            student_stats[s][metric]
            for s in full_students
            if student_stats[s][metric] is not None
        ]
        metric_ranges[metric] = sorted(float(v) for v in vals)

    # Cohort median + p75 for summary language.
    cohort_median = {}
    cohort_p75 = {}
    for metric, vals in metric_ranges.items():
        if vals:
            cohort_median[metric] = median(vals)
            cohort_p75[metric] = quantile(vals, 0.75)
        else:
            cohort_median[metric] = None
            cohort_p75[metric] = None

    # Compute component scores.
    for student in students:
        st = student_stats[student]
        eligible = st["Coverage"].lower() == "full"
        st["ScoreEligible"] = 1 if eligible else 0

        def metric_score(metric: str, higher_better: bool) -> Optional[float]:
            val = st[metric]
            if val is None:
                return None
            rng = metric_ranges.get(metric, [])
            if not rng:
                return None
            p = percentrank_inc(rng, float(val))
            return 100.0 * p if higher_better else 100.0 * (1.0 - p)

        queue_scores = []
        for metric, higher_better, weight, _ in queue_metrics:
            ms = metric_score(metric, higher_better) if eligible else None
            st[f"MetricScore_{metric}"] = ms
            if ms is not None:
                queue_scores.append((ms, weight))
        st["QueueTicketComponentScore"] = (
            sum(ms * w for ms, w in queue_scores) / sum(w for _, w in queue_scores)
            if queue_scores
            else None
        )

        knowledge_scores = []
        for metric, higher_better, weight, _ in knowledge_metrics:
            ms = metric_score(metric, higher_better) if eligible else None
            st[f"MetricScore_{metric}"] = ms
            if ms is not None:
                knowledge_scores.append((ms, weight))
        st["KnowledgeAccuracyComponentScore"] = (
            sum(ms * w for ms, w in knowledge_scores) / sum(w for _, w in knowledge_scores)
            if knowledge_scores
            else None
        )

        q_score = st["QueueTicketComponentScore"]
        k_score = st["KnowledgeAccuracyComponentScore"]
        st["OverallStandardizedScore"] = (
            (0.60 * q_score + 0.40 * k_score) if (q_score is not None and k_score is not None) else None
        )

        conf_score, conf_tier = confidence_from_counts(
            st["Coverage"],
            int(st["SampleEvents"]),
            int(st["SampleUniqueIncidents"]),
            int(st["QueueEntryCount"]),
            int(st["KnowledgeEntryCount"]),
        )
        st["ConfidenceScore"] = conf_score
        st["ConfidenceTier"] = conf_tier

    # Build scorecard rows with raw observation entries + summary fields.
    scorecard_rows = []
    for student in students:
        st = student_stats[student]
        events = events_by_student.get(student, [])
        queue_events = [r for r in events if is_queue_event(r)]
        knowledge_events = [r for r in events if is_knowledge_event(r)]

        queue_lines = [event_line(r) for r in queue_events]
        knowledge_lines = [event_line(r) for r in knowledge_events]

        # Rule-based summaries (concise, data-anchored).
        strengths = []
        gaps = []
        improvements = []
        expectations = []

        if st["Coverage"].lower() == "full":
            u = st["UpdatesPerHour"]
            i = st["IncidentsPerHour"]
            f = st["FollowupPerIncident"]
            on = st["OnShiftPct"]
            corr = st["CorrectionsPer100IncidentsCalc"]
            issue = st["IssueRateSample"]

            if u is not None and i is not None:
                if (cohort_median["UpdatesPerHour"] is not None and u >= cohort_median["UpdatesPerHour"]) and (
                    cohort_median["IncidentsPerHour"] is not None and i >= cohort_median["IncidentsPerHour"]
                ):
                    strengths.append(f"Throughput above cohort median (updates/hr={u:.2f}, incidents/hr={i:.2f}).")
                else:
                    gaps.append(f"Throughput below cohort median (updates/hr={u:.2f}, incidents/hr={i:.2f}).")
                    improvements.append(
                        f"Raise first-touch queue volume to >= median (updates/hr {cohort_median['UpdatesPerHour']:.2f}, incidents/hr {cohort_median['IncidentsPerHour']:.2f})."
                    )

            if f is not None and on is not None:
                if (cohort_median["FollowupPerIncident"] is not None and f >= cohort_median["FollowupPerIncident"]) and (
                    cohort_median["OnShiftPct"] is not None and on >= cohort_median["OnShiftPct"]
                ):
                    strengths.append(f"Execution discipline at/above cohort median (follow-up/inc={f:.2f}, on-shift={on:.1f}%).")
                else:
                    gaps.append(f"Execution consistency below median on follow-up/on-shift (follow-up/inc={f:.2f}, on-shift={on:.1f}%).")
                    improvements.append(
                        f"Enforce follow-up on active incidents and sustain on-shift activity >= {cohort_median['OnShiftPct']:.1f}%."
                    )

            if corr is not None and issue is not None:
                if (
                    cohort_median["CorrectionsPer100IncidentsCalc"] is not None
                    and corr <= cohort_median["CorrectionsPer100IncidentsCalc"]
                    and cohort_median["IssueRateSample"] is not None
                    and issue <= cohort_median["IssueRateSample"]
                ):
                    strengths.append(f"Knowledge/accuracy risk below cohort median (corr/100={corr:.2f}, issue-rate={issue:.3f}).")
                else:
                    gaps.append(f"Knowledge/accuracy risk above cohort median (corr/100={corr:.2f}, issue-rate={issue:.3f}).")
                    improvements.append(
                        "Use category/subcategory validation checklist before reassignment or closure."
                    )

            n_share = st["NightEventShare"] or 0.0
            w_share = st["WeekendEventShare"] or 0.0
            s_share = st["SlowIncidentNonZeroP75EventShare"] or 0.0
            if (
                cohort_p75["OnScheduledEventRate"] is not None
                and (n_share >= 0.50 or w_share >= 0.50 or s_share >= 0.30)
            ):
                strengths.append(
                    f"Handled high-load context (night={n_share:.2f}, weekend={w_share:.2f}, slow-ticket={s_share:.2f})."
                )
        else:
            strengths.append("Partial coverage sample; strengths limited to directly observed events.")
            gaps.append("Coverage is not full; standardized ranking is withheld for defensibility.")
            improvements.append("Increase full-audit coverage before applying weighted score decisions.")

        if st["ReroutePrecisionRate"] is not None and cohort_median["ReroutePrecisionRate"] is not None:
            if st["ReroutePrecisionRate"] < cohort_median["ReroutePrecisionRate"]:
                gaps.append(f"Reroute precision below cohort median ({st['ReroutePrecisionRate']:.2f}).")
                improvements.append(
                    f"Confirm target queue ownership before reassignment; target reroute precision >= {cohort_median['ReroutePrecisionRate']:.2f}."
                )

        expectations.append("All queue actions require clear before/after state in-ticket.")
        if cohort_median["UpdatesPerHour"] is not None and cohort_median["IncidentsPerHour"] is not None:
            expectations.append(
                f"Maintain at least median throughput (updates/hr {cohort_median['UpdatesPerHour']:.2f}, incidents/hr {cohort_median['IncidentsPerHour']:.2f})."
            )
        expectations.append("Maintain evidence depth each cycle: >=20 events and >=10 incidents for high-confidence scoring.")

        strengths_text = build_summary_text(strengths, "No clear outlier strengths in the current mined sample.")
        gaps_text = build_summary_text(gaps, "No major recurring gaps detected in the current mined sample.")
        improvements_text = build_summary_text(improvements, "Maintain current process and continue evidence capture.")
        expectations_text = build_summary_text(expectations, "Continue standard queue and accuracy protocol.")

        common = {
            "Name": student,
            "Strengths (Summary)": strengths_text,
            "Performance Gaps (Summary)": gaps_text,
            "Improvements (Actionable)": improvements_text,
            "Clear Expectations (Going Forward)": expectations_text,
            "Coverage": st["Coverage"],
            "SampleEvents": st["SampleEvents"],
            "SampleUniqueIncidents": st["SampleUniqueIncidents"],
            "QueueEntryCount": st["QueueEntryCount"],
            "KnowledgeEntryCount": st["KnowledgeEntryCount"],
            "QueueTicketComponentScore": st["QueueTicketComponentScore"],
            "KnowledgeAccuracyComponentScore": st["KnowledgeAccuracyComponentScore"],
            "OverallStandardizedScore": st["OverallStandardizedScore"],
            "ConfidenceScore": st["ConfidenceScore"],
            "ConfidenceTier": st["ConfidenceTier"],
        }

        scorecard_rows.append(
            {
                **common,
                "Observation Subrow": "Queue & Ticket Handling",
                "Observation Entries (Raw, Non-Summary)": join_lines(queue_lines),
            }
        )
        scorecard_rows.append(
            {
                **common,
                "Observation Subrow": "Knowledge & Accuracy",
                "Observation Entries (Raw, Non-Summary)": join_lines(knowledge_lines),
            }
        )

    # Workbook output.
    wb = Workbook()
    ws_readme = wb.active
    ws_readme.title = "README"
    readme_lines = [
        "Standardized Scorecard Workbook (Standalone)",
        "",
        "Generated by pipeline/s5_scorecard.py from performance_student_analysis.csv and evidence_activity_enriched.csv.",
        "",
        "Included:",
        "1) Scoring schema with exact metric definitions, formulas, weights, and confidence thresholds.",
        "2) Scorecard_Subrows with required two observation subrows per student:",
        "   - Queue & Ticket Handling",
        "   - Knowledge & Accuracy",
        "   Observation entries are raw mined entries (non-summary).",
        "3) Student_Metric_Input with metric values and component scores.",
        "4) Raw_Queue_Obs and Raw_Knowledge_Obs with row-level evidence.",
    ]
    for line in readme_lines:
        ws_readme.append([line])
    ws_readme["A1"].font = Font(bold=True)
    ws_readme.column_dimensions["A"].width = 120

    # Schema sheet.
    ws_schema = wb.create_sheet("Scoring_Schema")
    ws_schema.append(
        [
            "Component",
            "OverallWeight",
            "Metric",
            "MetricDefinition",
            "Direction",
            "WeightInComponent",
            "MetricScoreFormula(0-100)",
            "DataSourceField",
        ]
    )
    for metric, higher, weight, definition in queue_metrics:
        ws_schema.append(
            [
                "Queue & Ticket Handling",
                0.60,
                metric,
                definition,
                "Higher is better" if higher else "Lower is better",
                weight,
                "PERCENTRANK.INC(range,value)*100 (higher) OR (1-PERCENTRANK.INC(range,value))*100 (lower)",
                metric,
            ]
        )
    for metric, higher, weight, definition in knowledge_metrics:
        ws_schema.append(
            [
                "Knowledge & Accuracy",
                0.40,
                metric,
                definition,
                "Higher is better" if higher else "Lower is better",
                weight,
                "PERCENTRANK.INC(range,value)*100 (higher) OR (1-PERCENTRANK.INC(range,value))*100 (lower)",
                metric,
            ]
        )
    ws_schema.append([])
    ws_schema.append(["ComponentScore", "", "", "", "", "", "SUMPRODUCT(metric_scores, metric_weights)", ""])
    ws_schema.append(["OverallStandardizedScore", "", "", "", "", "", "0.60*QueueTicketComponentScore + 0.40*KnowledgeAccuracyComponentScore", ""])
    wrap_sheet(ws_schema)

    # Confidence thresholds.
    ws_conf = wb.create_sheet("Confidence_Thresholds")
    ws_conf.append(["Tier", "Coverage", "SampleEvents", "SampleUniqueIncidents", "QueueEntryCount", "KnowledgeEntryCount", "UseCase"])
    ws_conf.append(["High", "Full", ">= 20", ">= 10", ">= 10", ">= 6", "Primary scorecard standardization decisions"])
    ws_conf.append(["Medium", "Full", ">= 10", ">= 5", ">= 5", ">= 3", "Use with caution; confirm with additional audit sample"])
    ws_conf.append(["Low", "Any other case", "< above thresholds", "< above thresholds", "< above thresholds", "< above thresholds", "Evidence only; do not use alone for ranking decisions"])
    ws_conf.append([])
    ws_conf.append(["ConfidenceScoreFormula", "100*coverage_factor*(0.4*sample_factor + 0.3*incident_factor + 0.3*subrow_factor)", "", "", "", "", ""])
    wrap_sheet(ws_conf)

    # Student metric input sheet.
    ws_input = wb.create_sheet("Student_Metric_Input")
    input_fields = [
        "Student",
        "Coverage",
        "ScoreEligible",
        "SampleEvents",
        "SampleUniqueIncidents",
        "QueueEntryCount",
        "KnowledgeEntryCount",
        "UpdatesPerHour",
        "IncidentsPerHour",
        "FollowupPerIncident",
        "OnShiftPct",
        "OnScheduledEventRate",
        "QueueMeanMinutesSincePrevAction",
        "QueueIssueRate",
        "CorrectionsPer100IncidentsCalc",
        "IssueRateSample",
        "CategoryCorrectionRate",
        "SubcategoryCorrectionRate",
        "ReroutePrecisionRate",
        "KnowledgeIssueRate",
        "MetricScore_UpdatesPerHour",
        "MetricScore_IncidentsPerHour",
        "MetricScore_FollowupPerIncident",
        "MetricScore_OnShiftPct",
        "MetricScore_OnScheduledEventRate",
        "MetricScore_QueueMeanMinutesSincePrevAction",
        "MetricScore_QueueIssueRate",
        "MetricScore_CorrectionsPer100IncidentsCalc",
        "MetricScore_IssueRateSample",
        "MetricScore_CategoryCorrectionRate",
        "MetricScore_SubcategoryCorrectionRate",
        "MetricScore_ReroutePrecisionRate",
        "MetricScore_KnowledgeIssueRate",
        "QueueTicketComponentScore",
        "KnowledgeAccuracyComponentScore",
        "OverallStandardizedScore",
        "ConfidenceScore",
        "ConfidenceTier",
    ]
    ws_input.append(input_fields)
    for student in students:
        st = student_stats[student]
        ws_input.append([student] + [st.get(k, "") for k in input_fields[1:]])
    wrap_sheet(ws_input)

    # Scorecard sheet with required two subrows and non-summary observation entries.
    ws_score = wb.create_sheet("Scorecard_Subrows")
    score_fields = [
        "Name",
        "Observation Subrow",
        "Observation Entries (Raw, Non-Summary)",
        "Strengths (Summary)",
        "Performance Gaps (Summary)",
        "Improvements (Actionable)",
        "Clear Expectations (Going Forward)",
        "Coverage",
        "SampleEvents",
        "SampleUniqueIncidents",
        "QueueEntryCount",
        "KnowledgeEntryCount",
        "QueueTicketComponentScore",
        "KnowledgeAccuracyComponentScore",
        "OverallStandardizedScore",
        "ConfidenceScore",
        "ConfidenceTier",
    ]
    ws_score.append(score_fields)
    for row in scorecard_rows:
        ws_score.append([row.get(k, "") for k in score_fields])
    wrap_sheet(ws_score, wrap_cols=[3, 4, 5, 6, 7], freeze="A2")

    # Raw queue observations.
    ws_q = wb.create_sheet("Raw_Queue_Obs")
    q_fields = [
        "Student",
        "DateTimeParsed",
        "Incident",
        "ActionType",
        "RuleMatched",
        "Outcome",
        "Category",
        "IsWeekend",
        "IsNightByClock",
        "StudentScheduledInSlot",
        "CoScheduledPeers",
        "MinutesSincePreviousIncidentAction",
    ]
    ws_q.append(q_fields)
    for r in queue_raw_rows:
        ws_q.append([r.get(k, "") for k in q_fields])
    wrap_sheet(ws_q, wrap_cols=[11], freeze="A2")

    # Raw knowledge observations.
    ws_k = wb.create_sheet("Raw_Knowledge_Obs")
    k_fields = [
        "Student",
        "DateTimeParsed",
        "Incident",
        "ActionType",
        "RuleMatched",
        "Outcome",
        "Category",
        "IsWeekend",
        "IsNightByClock",
        "StudentScheduledInSlot",
        "CoScheduledPeers",
    ]
    ws_k.append(k_fields)
    for r in knowledge_raw_rows:
        ws_k.append([r.get(k, "") for k in k_fields])
    wrap_sheet(ws_k, wrap_cols=[11], freeze="A2")

    wb.save(OUT_PATH)

    summary_fields = ["Student", "Coverage", "QueueTicketComponentScore", "KnowledgeAccuracyComponentScore",
                      "OverallStandardizedScore", "ConfidenceScore", "ConfidenceTier"]
    summary_rows = []
    for student in students:
        st = student_stats[student]
        row = {"Student": student}
        for k in summary_fields[1:]:
            v = st.get(k)
            row[k] = round(v, 1) if isinstance(v, float) else ("" if v is None else v)
        summary_rows.append(row)
    write_csv(OUT_SUMMARY, summary_fields, summary_rows)
    print(f"Wrote {OUT_PATH.name}")
    print(f"Wrote {OUT_SUMMARY.name}")


if __name__ == "__main__":
    main()
