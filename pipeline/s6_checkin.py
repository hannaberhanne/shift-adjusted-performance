#!/usr/bin/env python3
"""Stage 6: turn the metrics into a semester check-in: a seven-domain rubric workbook and a meeting packet.

Each technician is rated against role expectations, not against each other. Domains the ticket data
cannot speak to (phones, classroom media) are marked Insufficient Data instead of being guessed, and
domains that only have a proxy are labelled "(Proxy)" so nobody mistakes them for direct evidence.

Inputs:   output/metrics_summary.csv, output/evidence_log.csv, data/raw/schedule.csv
Outputs:  output/checkin_workbook.xlsx    README, Standardized_Metric, Domain_Rubric, Prefilled_Metrics,
                                          Supervisor_Input, Domain_Ratings, Final_Summary, Evidence_Log
          output/checkin_packet.md        one section per technician with findings, cited incidents and risks
          output/checkin_ratings.csv      the Domain_Ratings sheet as CSV, for diffing between runs
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, RAW, is_full, read_csv_rows, write_csv  # noqa: E402
from common import to_float as _to_float  # noqa: E402
from schedule import load_schedule, slot_for  # noqa: E402

METRICS_PATH = OUT / "metrics_summary.csv"
EVIDENCE_PATH = OUT / "evidence_log.csv"
SCHEDULE_PATH = RAW / "schedule.csv"

OUT_WORKBOOK = OUT / "checkin_workbook.xlsx"
OUT_PACKET = OUT / "checkin_packet.md"
OUT_RATINGS = OUT / "checkin_ratings.csv"

MEETS = "Meets Expectations"
SUPPORT = "Needs Support"
IMMEDIATE = "Needs Immediate Improvement"
NO_DATA = "Insufficient Data"

DOMAINS = [
    "Queue & Ticket Handling",
    "Accuracy & Knowledge",
    "Phones & Calls",
    "Front Desk & Printers",
    "Classroom/Media Readiness",
    "Reliability & Consistency",
    "Communication Responsiveness",
]

# Thresholds, kept together so the rubric sheet and the code cannot drift apart.
QUEUE_MEETS = dict(iph=0.60, fpi=0.50, onshift=90.0, handled=5, reroutes=8)
QUEUE_SUPPORT = dict(iph=0.25, fpi=0.30, onshift=80.0)
ACCURACY_MEETS_MAX = 4.0      # corrections per 100 incidents
ACCURACY_SUPPORT_MAX = 10.0
FRONT_DESK_MEETS = 5
RELIABILITY_MEETS = 95.0
RELIABILITY_SUPPORT = 85.0
COMMS_MEETS = dict(onshift=95.0, fpi=0.50)
COMMS_SUPPORT = dict(onshift=85.0, fpi=0.30)

# Final summary and packet risk rules.
LOW_THROUGHPUT_UPDATES_PER_HOUR = 1.0
LOW_ONSHIFT_PCT = 85.0
LOW_FOLLOWUP_PER_INCIDENT = 0.30
REPEATED_CORRECTIONS = 5
SHORT_COMMENT_WATCH = 5
COMMENT_EDIT_WATCH = 3
SHORT_COMMENT_CHARS = 15
EXAMPLES = 6

FOLLOWUP_RULES = {"Follow-up valid (Comment added)", "Follow-up valid (Waiting on User)"}
CORRECTION_RULES = {"Category correction needed", "Subcategory correction needed"}


def f(value: object) -> float:
    return _to_float(value) or 0.0


def correction_rate(m: Dict[str, str]) -> float:
    incidents = f(m["Incidents"])
    return f(m["AccuracyCorrections"]) / incidents * 100.0 if incidents else 0.0


def handled(m: Dict[str, str]) -> int:
    return int(f(m["AssignedToResolved"]) + f(m["AssignedToResponded"]))


# ---------------------------------------------------------------- domain ratings

def rate(m: Dict[str, str]) -> Dict[str, str]:
    if not is_full(m["Coverage"]) or f(m["Incidents"]) == 0:
        return {**{d: NO_DATA for d in DOMAINS}, "Pattern Flags Present?": "Data gap",
                "Overall Check-In Outcome": NO_DATA, "Evidence References (Incident IDs / Logs)": evidence_ref(m)}

    iph, fpi, on = f(m["IncidentsPerHour"]), f(m["FollowupPerIncident"]), f(m["OnShiftPct"])
    corr = correction_rate(m)

    if (iph >= QUEUE_MEETS["iph"] and fpi >= QUEUE_MEETS["fpi"] and on >= QUEUE_MEETS["onshift"]
            and (handled(m) >= QUEUE_MEETS["handled"] or f(m["RerouteOut"]) >= QUEUE_MEETS["reroutes"])):
        queue = MEETS
    elif iph >= QUEUE_SUPPORT["iph"] and fpi >= QUEUE_SUPPORT["fpi"] and on >= QUEUE_SUPPORT["onshift"]:
        queue = SUPPORT
    else:
        queue = IMMEDIATE

    accuracy = MEETS if corr <= ACCURACY_MEETS_MAX else SUPPORT if corr <= ACCURACY_SUPPORT_MAX else IMMEDIATE

    to_fd = f(m["ToFrontDesk"])
    front_desk = (MEETS if to_fd >= FRONT_DESK_MEETS else SUPPORT if to_fd >= 1 else IMMEDIATE) + " (Proxy)"

    if f(m["Updates"]) == 0:
        reliability = NO_DATA
    else:
        reliability = (MEETS if on >= RELIABILITY_MEETS else SUPPORT if on >= RELIABILITY_SUPPORT else IMMEDIATE) + " (Proxy)"

    if on >= COMMS_MEETS["onshift"] and fpi >= COMMS_MEETS["fpi"]:
        comms = MEETS
    elif on >= COMMS_SUPPORT["onshift"] and fpi >= COMMS_SUPPORT["fpi"]:
        comms = SUPPORT
    else:
        comms = IMMEDIATE

    flags = []
    if queue == IMMEDIATE:
        flags.append("Queue risk")
    if accuracy == IMMEDIATE:
        flags.append("Accuracy risk")
    if reliability.startswith(IMMEDIATE):
        flags.append("Reliability risk")
    if comms == IMMEDIATE:
        flags.append("Responsiveness risk")

    core = [queue, accuracy, comms]
    if sum(1 for r in core if r == IMMEDIATE) >= 2:
        outcome = "Performance Concern"
    elif sum(1 for r in core if r in (SUPPORT, IMMEDIATE)) >= 2:
        outcome = "Needs Support Plan"
    else:
        outcome = "On Track"

    return {
        "Queue & Ticket Handling": queue,
        "Accuracy & Knowledge": accuracy,
        "Phones & Calls": NO_DATA,
        "Front Desk & Printers": front_desk,
        "Classroom/Media Readiness": NO_DATA,
        "Reliability & Consistency": reliability,
        "Communication Responsiveness": comms,
        "Pattern Flags Present?": "; ".join(flags) if flags else "None",
        "Overall Check-In Outcome": outcome,
        "Evidence References (Incident IDs / Logs)": evidence_ref(m),
    }


def evidence_ref(m: Dict[str, str]) -> str:
    return (
        f"Coverage={m['Coverage']}; iph={f(m['IncidentsPerHour']):.2f}; fpi={f(m['FollowupPerIncident']):.2f}; "
        f"on={f(m['OnShiftPct']):.1f}%; handled={handled(m)}; reroutes={int(f(m['RerouteOut']))}; "
        f"correction_rate={correction_rate(m):.2f}/100; to_frontdesk={int(f(m['ToFrontDesk']))}"
    )


def final_summary(m: Dict[str, str]) -> Dict[str, str]:
    if not is_full(m["Coverage"]):
        return {
            "Strengths (objective)": "Partial data only.",
            "Concrete Issues Observed": "Cannot evaluate full role fulfillment without complete audit attribution.",
            "Patterns Noted": "Data gap pattern.",
            "Improvement Actions": "Request complete audit export and re-run.",
            "Clear Expectations Going Forward": "Use contextual coaching only this cycle.",
            "Questions/Notes": "",
        }
    issues, patterns = [], []
    corrections = int(f(m["AccuracyCorrections"]))
    if f(m["OnShiftPct"]) < LOW_ONSHIFT_PCT:
        issues.append("low on-shift activity alignment")
        patterns.append("responsiveness while scheduled")
    if f(m["FollowupPerIncident"]) < LOW_FOLLOWUP_PER_INCIDENT:
        issues.append("low follow-up frequency")
        patterns.append("follow-up process inconsistency")
    if corrections >= REPEATED_CORRECTIONS:
        issues.append("repeated accuracy corrections")
        patterns.append(f"accuracy correction pattern ({corrections})")
    if f(m["UpdatesPerHour"]) < LOW_THROUGHPUT_UPDATES_PER_HOUR:
        issues.append("low ticket throughput for scheduled hours")
    return {
        "Strengths (objective)": (
            f"Follow-up valid: {int(f(m['FollowupValid']))}; reroutes out: {int(f(m['RerouteOut']))}; "
            f"handled transitions(A->Responded/Resolved): {handled(m)}."
        ),
        "Concrete Issues Observed": "; ".join(issues) if issues else "No major objective issue flagged in incident metrics.",
        "Patterns Noted": "; ".join(patterns) if patterns else "No repeating pattern observed from incident metrics.",
        "Improvement Actions": "Coach to close the specific gap(s) above using incident-level examples.",
        "Clear Expectations Going Forward": "Meet the Meets Expectations anchors in Domain_Rubric consistently next check-in period.",
        "Questions/Notes": "",
    }


# ---------------------------------------------------------------- packet

def first_unique(incidents: Sequence[str], limit: int = EXAMPLES) -> List[str]:
    seen: List[str] = []
    for inc in incidents:
        if inc and inc not in seen:
            seen.append(inc)
        if len(seen) >= limit:
            break
    return seen


def refs(incidents: Sequence[str], limit: int = EXAMPLES) -> str:
    found = first_unique(incidents, limit)
    return ", ".join(found) if found else "None"


def comment_text(note: str) -> str:
    prefix = "Comment added '"
    return note[len(prefix):-1] if note.startswith(prefix) and note.endswith("'") else note


def packet_section(m: Dict[str, str], events: List[Dict[str, str]], on_shift: Dict[int, int]) -> List[str]:
    name = m["Student"]
    lines = [
        f"## {name}",
        "",
        f"- Coverage: **{m['Coverage']}**",
        f"- Scheduled hours: **{f(m['ScheduledHours']):.1f}** | Updates: **{int(f(m['Updates']))}** | Incidents: **{int(f(m['Incidents']))}**",
        f"- Normalized: **{f(m['UpdatesPerHour']):.2f} updates/hr**, **{f(m['IncidentsPerHour']):.2f} incidents/hr**",
        f"- On-shift alignment: **{f(m['OnShiftPct']):.1f}%**",
    ]
    if not is_full(m["Coverage"]):
        pdf = [e for e in events if e["RuleMatched"] == "PDF-only partial context"]
        states = sum(1 for e in pdf if e["ActionType"].startswith("State"))
        comments = sum(1 for e in pdf if e["ActionType"].startswith("Comment"))
        assigns = sum(1 for e in pdf if e["ActionType"].startswith("Assign"))
        sample = sorted({e["Incident"] for e in pdf}, reverse=True)[:5]
        lines += [
            f"- PDF context events: {len(pdf)} (state changes: {states}, comments: {comments}, assignments: {assigns})",
            f"- Sample incidents from PDF: {', '.join(sample) if sample else 'None found'}",
            "",
            "### Findings",
            "- Full role-fulfillment evaluation is blocked by missing audit attribution. Use as contextual notes only.",
            "",
            "### Supervisor Assist Focus",
            "- Request complete audit export for fair review; avoid peer ranking from partial data.",
            "- Reinforce consistent ticket documentation in systems used for audits.",
        ]
    else:
        def by_rule(rules) -> List[Dict[str, str]]:
            return [e for e in events if e["RuleMatched"] in rules]

        followups = by_rule(FOLLOWUP_RULES)
        morning = sum(1 for e in followups if datetime.strptime(e["DateTime"], "%Y-%m-%d %H:%M").hour < 12)
        reroutes = by_rule({"Rerouted to correct department/person"})
        front_desk = by_rule({"Assigned to Front Desk Queue"})
        quick_pw = by_rule({"Password template quick resolution"})
        corrections = by_rule(CORRECTION_RULES)

        lines += [
            "",
            "### Findings",
            f"- Follow-up execution: {len(followups)} valid follow-up actions; morning follow-up actions: {morning}. "
            f"Example incidents: {refs([e['Incident'] for e in followups])}.",
            f"- Cross-team routing: {len(reroutes)} reroutes to non-front-desk targets (positive when appropriate). "
            f"Example incidents: {refs([e['Incident'] for e in reroutes])}.",
            f"- Front desk queue specificity: {len(front_desk)} assignment events to Front Desk Queue. "
            f"Example incidents: {refs([e['Incident'] for e in front_desk])}.",
            f"- Password-maintenance quick resolutions: {len(quick_pw)}. "
            f"Incident refs: {refs([e['Incident'] for e in quick_pw], limit=10_000)}.",
        ]
        if corrections:
            lines.append(
                f"- Accuracy correction events: {len(corrections)} (category/subcategory changes). "
                f"Incident refs: {refs([e['Incident'] for e in corrections])}."
            )
        else:
            lines.append("- Accuracy correction events: none captured in this period.")

        risks = []
        if len(corrections) >= REPEATED_CORRECTIONS:
            risks.append(f"- Repeated accuracy corrections ({len(corrections)}). Incidents: {refs([e['Incident'] for e in corrections])}")
        if f(m["OnShiftPct"]) < LOW_ONSHIFT_PCT:
            off = [e["Incident"] for e in events if e["Source"] == "Audit export" and not on_shift[id(e)]]
            risks.append(f"- On-shift responsiveness pattern ({f(m['OnShiftPct']):.1f}% in-slot). Off-shift incident examples: {refs(off)}")
        short = [e for e in events if e["ActionType"] == "Comment added" and len(comment_text(e["Note"])) < SHORT_COMMENT_CHARS]
        if len(short) >= SHORT_COMMENT_WATCH:
            risks.append(f"- Comment QA watch: {len(short)} short comments detected. Incidents: {refs([e['Incident'] for e in short])}")
        edits = by_rule({"Comment edited after posting"})
        if len(edits) >= COMMENT_EDIT_WATCH:
            risks.append(f"- Comment edit frequency: {len(edits)} updates after posting. Incidents: {refs([e['Incident'] for e in edits])}")

        lines += ["", "### Role Fulfillment Risk Check"]
        lines += risks or ["- No major risk pattern flagged from available data."]
        lines += [
            "",
            "### Supervisor Assist Focus",
            "- Keep coaching on accurate first-pass categorization and subcategory selection before routing.",
            "- Reinforce documenting follow-up actions explicitly in comments/status changes.",
            "- Continue troubleshooting attempt + correct reroute behavior for non-front-desk teams.",
        ]
    lines += [
        "",
        "### Media Services Feedback",
        "- Pending input.",
        "",
        "### Service Desk Feedback",
        "- Pending input.",
        "",
    ]
    return lines


def write_packet(metrics: List[Dict[str, str]], events_by: Dict[str, List[Dict[str, str]]],
                 on_shift: Dict[int, int], window: str) -> None:
    lines = [
        "# Student Employee Semester Check-In Packet",
        "",
        f"Analysis window: **{window}**.",
        "No SLA scoring used. Focus is role fulfillment, risks, and coaching actions for student employees.",
        "Applied rules: valid follow-up = `Comment added` or `Responded -> Waiting on User`; reopened tickets = neutral; "
        "correct reroutes = positive; password-maintenance template + resolve = positive.",
        "",
        "## Team Productivity Comparison (Normalized)",
        "",
        "| Technician | Coverage | Updates/hr | Incidents/hr | On-shift % | Follow-up valid | Accuracy corrections |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m in metrics:
        lines.append(
            f"| {m['Student']} | {m['Coverage']} | {f(m['UpdatesPerHour']):.2f} | {f(m['IncidentsPerHour']):.2f} | "
            f"{f(m['OnShiftPct']):.1f}% | {int(f(m['FollowupValid']))} | {int(f(m['AccuracyCorrections']))} |"
        )
    top_u = sorted(metrics, key=lambda m: -f(m["UpdatesPerHour"]))[:5]
    top_i = sorted(metrics, key=lambda m: -f(m["IncidentsPerHour"]))[:5]
    lines += [
        "",
        "Top updates/hr: " + ", ".join(f"{m['Student']} ({f(m['UpdatesPerHour']):.2f})" for m in top_u),
        "Top incidents/hr: " + ", ".join(f"{m['Student']} ({f(m['IncidentsPerHour']):.2f})" for m in top_i),
        "",
    ]
    for m in metrics:
        lines += packet_section(m, events_by.get(m["Student"], []), on_shift)
    OUT_PACKET.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------- workbook

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
WRAP = Alignment(wrap_text=True, vertical="top")


def add_sheet(wb: Workbook, title: str, header: Sequence[str], rows: Sequence[Sequence[object]], widths: Sequence[int]) -> None:
    ws = wb.create_sheet(title)
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    for c in ws[1]:
        c.fill, c.font, c.alignment = HEADER_FILL, HEADER_FONT, WRAP
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = WRAP
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w
    ws.freeze_panes = "A2"


STANDARDIZED_METRIC = [
    ("Queue & Ticket Handling", "IncidentsPerHour, FollowupPerIncident, OnShiftPct, RerouteOut, AssignedToResolved+AssignedToResponded",
     f"IncidentsPerHour >= {QUEUE_MEETS['iph']:.2f} AND FollowupPerIncident >= {QUEUE_MEETS['fpi']:.2f} AND OnShiftPct >= {QUEUE_MEETS['onshift']:.0f} "
     f"AND (HandledTransitions >= {QUEUE_MEETS['handled']} OR RerouteOut >= {QUEUE_MEETS['reroutes']})",
     f"IncidentsPerHour >= {QUEUE_SUPPORT['iph']:.2f} AND FollowupPerIncident >= {QUEUE_SUPPORT['fpi']:.2f} AND OnShiftPct >= {QUEUE_SUPPORT['onshift']:.0f}",
     "Anything below Needs Support threshold", "Coverage != Full OR Incidents = 0"),
    ("Accuracy & Knowledge", "AccuracyCorrections per 100 incidents (correction rate)",
     f"CorrectionRate <= {ACCURACY_MEETS_MAX:.1f}", f"CorrectionRate > {ACCURACY_MEETS_MAX:.1f} and <= {ACCURACY_SUPPORT_MAX:.1f}",
     f"CorrectionRate > {ACCURACY_SUPPORT_MAX:.1f}", "Coverage != Full OR Incidents = 0"),
    ("Phones & Calls", "No phone/call log fields in current dataset", "N/A from current data", "N/A from current data",
     "N/A from current data", "Always Insufficient Data until phone logs are added"),
    ("Front Desk & Printers", "ToFrontDesk (queue events) as limited proxy only", f"ToFrontDesk >= {FRONT_DESK_MEETS} (proxy only)",
     f"ToFrontDesk 1-{FRONT_DESK_MEETS - 1} (proxy only)", "ToFrontDesk = 0 (proxy only)",
     "If you require walk-in/printer observations, mark Insufficient Data"),
    ("Classroom/Media Readiness", "No run completion/preparedness logs in current dataset", "N/A from current data",
     "N/A from current data", "N/A from current data", "Always Insufficient Data until media run records are added"),
    ("Reliability & Consistency", "OnShiftPct as responsiveness proxy only (not attendance)", f"OnShiftPct >= {RELIABILITY_MEETS:.0f}",
     f"OnShiftPct >= {RELIABILITY_SUPPORT:.0f} and < {RELIABILITY_MEETS:.0f}", f"OnShiftPct < {RELIABILITY_SUPPORT:.0f}",
     "Coverage != Full OR Updates = 0"),
    ("Communication Responsiveness", "OnShiftPct + FollowupPerIncident (shift-time communication proxy)",
     f"OnShiftPct >= {COMMS_MEETS['onshift']:.0f} AND FollowupPerIncident >= {COMMS_MEETS['fpi']:.2f}",
     f"OnShiftPct >= {COMMS_SUPPORT['onshift']:.0f} AND FollowupPerIncident >= {COMMS_SUPPORT['fpi']:.2f}",
     "Anything below Needs Support threshold", "Coverage != Full OR Incidents = 0"),
]

DOMAIN_RUBRIC = [
    ("Queue & Ticket Handling", "Tickets touched/resolved/rerouted, follow-up compliance, idle tickets, queue ownership",
     "Consistent follow-up process; routing usually correct; no recurring idle tickets", "Occasional missed follow-ups or routing errors; needs reminders",
     "Repeated missed follow-ups, stale tickets, or queue neglect", "Incident IDs + queue report notes"),
    ("Accuracy & Knowledge", "Category/subcategory corrections, repeat misinfo, escalation appropriateness",
     "Low correction frequency; correct info/escalation", "Some repeat corrections or uncertain escalations",
     "Repeated incorrect guidance or unresolved repeat mistakes", "Incident IDs + corrected examples"),
    ("Phones & Calls", "Call logs, answer rate, missed rings, logging completeness", "Takes calls when assigned and logs clearly",
     "Inconsistent call ownership/logging", "Avoids calls or repeatedly misses logging", "Call log records + dates"),
    ("Front Desk & Printers", "Walk-in support notes, printer issue handling, escalation quality", "Engaged support and proper escalation behavior",
     "Some avoidant behavior or delayed engagement", "Repeated avoidance or unnecessary deflection", "Front desk observations + incident IDs"),
    ("Classroom / Media Readiness", "Run completion, preparedness, punctuality, follow-through", "Prepared and reliable on runs",
     "Some misses/prep gaps", "Repeated misses or unpreparedness", "Media Services lead notes + run records"),
    ("Reliability & Consistency", "Lateness, call-outs, notice timing, schedule stability", "Generally on time; communicates early",
     "Some late/short-notice issues", "Chronic lateness/call-out patterns", "Attendance records + timestamps"),
    ("Communication Responsiveness", "Student-employee chat responsiveness during shift/escalations", "Responsive while on shift and during escalations",
     "Occasional response delays", "Repeated non-response while clocked in", "Chat logs + escalation examples"),
]

SUPERVISOR_INPUT = ["Student", "Attendance/Lateness Notes", "Call-outs & Notice", "Chat Responsiveness", "Phones/Call Logging",
                    "Front Desk Observation Notes", "Media Services Notes", "Service Desk Notes", "Lead Notes", "Manager Notes"]
RATING_FIELDS = ["Student"] + DOMAINS + ["Pattern Flags Present?", "Overall Check-In Outcome", "Evidence References (Incident IDs / Logs)"]
SUMMARY_FIELDS = ["Student", "Strengths (objective)", "Concrete Issues Observed", "Patterns Noted", "Improvement Actions",
                  "Clear Expectations Going Forward", "Questions/Notes"]
METRIC_FIELDS = ["Student", "Coverage", "ScheduledHours", "Updates", "Incidents", "UpdatesPerHour", "IncidentsPerHour", "OnShiftPct",
                 "FollowupValid", "FollowupPerIncident", "RerouteOut", "ToFrontDesk", "AssignedToResolved", "AssignedToResponded",
                 "AccuracyCorrections", "PWQuickResolutions", "ShortComments", "CommentUpdated"]
EVIDENCE_FIELDS = ["Student", "Incident", "DateTime", "Source", "QueueContext", "ActionType", "RuleMatched", "Outcome", "Category", "Note"]


def write_workbook(metrics, ratings, summaries, evidence) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        ("Objective Semester Check-In Workbook", None),
        ("Purpose", "Evaluate student employees objectively across all required domains. Incident activity is only one input."),
        ("How to use", "1) Review prefilled incident metrics in Prefilled_Metrics.\n"
                       "2) Add missing inputs in Supervisor_Input (attendance/chat/phones/media/service desk).\n"
                       "3) Rate each domain using anchors in Domain_Rubric and enter ratings in Domain_Ratings.\n"
                       "4) Use Final_Summary for meeting-ready output."),
        ("Rating scale", f"{MEETS} | {SUPPORT} | {IMMEDIATE} | {NO_DATA}"),
        ("Important", "Do NOT compare students against each other. Compare each student against role expectations and evidence."),
    ]:
        ws.append(list(row))
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 120
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=1).font = Font(bold=True)
        ws.cell(row=r, column=2).alignment = WRAP

    add_sheet(wb, "Standardized_Metric", ["Domain", "Available Data Used", MEETS, SUPPORT, IMMEDIATE, "Insufficient Data Trigger"],
              STANDARDIZED_METRIC, [26, 38, 34, 30, 30, 34])
    add_sheet(wb, "Domain_Rubric", ["Domain", "Objective Evidence Inputs", MEETS, SUPPORT, IMMEDIATE, "Required Evidence Citation"],
              DOMAIN_RUBRIC, [28, 32, 28, 28, 28, 28])
    add_sheet(wb, "Prefilled_Metrics", METRIC_FIELDS, [[m[k] for k in METRIC_FIELDS] for m in metrics],
              [24, 12, 14] + [10] * (len(METRIC_FIELDS) - 3))
    add_sheet(wb, "Supervisor_Input", SUPERVISOR_INPUT, [[m["Student"]] + [None] * (len(SUPERVISOR_INPUT) - 1) for m in metrics],
              [24] + [24] * (len(SUPERVISOR_INPUT) - 1))
    add_sheet(wb, "Domain_Ratings", RATING_FIELDS, [[r[k] for k in RATING_FIELDS] for r in ratings],
              [24] + [20] * len(DOMAINS) + [22, 22, 60])
    add_sheet(wb, "Final_Summary", SUMMARY_FIELDS, [[s[k] for k in SUMMARY_FIELDS] for s in summaries], [24, 34, 34, 28, 28, 28, 24])
    add_sheet(wb, "Evidence_Log", EVIDENCE_FIELDS, [[e[k] for k in EVIDENCE_FIELDS] for e in evidence],
              [24, 12, 18, 18, 30, 34, 34, 10, 10, 50])
    wb.save(OUT_WORKBOOK)


def main() -> None:
    metrics = sorted(read_csv_rows(METRICS_PATH), key=lambda m: m["Student"])
    evidence = read_csv_rows(EVIDENCE_PATH)
    slots = load_schedule(SCHEDULE_PATH)

    on_shift: Dict[int, int] = {}
    events_by: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for e in sorted(evidence, key=lambda e: (e["DateTime"], e["Incident"])):
        slot = slot_for(slots, datetime.strptime(e["DateTime"], "%Y-%m-%d %H:%M"))
        on_shift[id(e)] = int(bool(slot and e["Student"] in slot.staff))
        events_by[e["Student"]].append(e)

    dates = sorted(e["DateTime"][:10] for e in evidence)
    window = f"{dates[0]} to {dates[-1]}" if dates else "n/a"

    ratings = [{"Student": m["Student"], **rate(m)} for m in metrics]
    summaries = [{"Student": m["Student"], **final_summary(m)} for m in metrics]

    write_workbook(metrics, ratings, summaries, evidence)
    write_packet(metrics, events_by, on_shift, window)
    write_csv(OUT_RATINGS, RATING_FIELDS, ratings)

    counts = defaultdict(int)
    for r in ratings:
        counts[r["Overall Check-In Outcome"]] += 1
    print("Check-in outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for p in (OUT_WORKBOOK, OUT_PACKET, OUT_RATINGS):
        print(f"- {p.name}")


if __name__ == "__main__":
    main()
