#!/usr/bin/env python3
"""Stage 1: tag every audited ticket action with an explicit rule and build per-technician metrics.

Inputs:   data/raw/audit_log.csv, data/raw/schedule.csv
Outputs:  output/evidence_log.csv       every action, tagged (rule, outcome, category)
          output/evidence_sample.csv    up to 20 actions per technician, spread over the window
          output/metrics_summary.csv    per-technician metrics, normalized per scheduled hour

Rules (see docs/method.md):
  - Valid follow-up = "Comment added" or "Responded -> Waiting on User".
  - Correct reroute to another team = positive. Assignment to the Front Desk Queue is counted separately.
  - Password-maintenance ticket resolved by the same technician within 15 minutes of first touch = positive.
  - Category or subcategory change = accuracy correction.
  - Reopened by the client = neutral (not held against the technician).
  - Comment edited after posting and very short comments are watched, not scored.
  - Rows that only exist in a partial (PDF) export are kept as context and never scored.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, RAW, read_csv_rows, write_csv  # noqa: E402
from schedule import load_schedule, scheduled_hours, slot_for  # noqa: E402

SHORT_COMMENT_CHARS = 15
QUICK_PASSWORD_MINUTES = 15
SAMPLE_PER_TECH = 20


def tag(action: str) -> tuple[str, str, str]:
    """Return (rule, outcome, category) for one audit action."""
    a = action.strip()
    if a == "Comment added":
        return "Follow-up valid (Comment added)", "good", "strength"
    if a == "State Responded -> Waiting on User":
        return "Follow-up valid (Waiting on User)", "good", "strength"
    if a == "State Assigned -> Responded":
        return "Initial handling response", "good", "strength"
    if a == "State Assigned -> Resolved":
        return "Resolved ticket", "good", "strength"
    if a == "Resolution code set":
        return "Closure documentation", "good", "strength"
    if a.startswith("Assignee") and a.endswith("-> Front Desk Queue"):
        return "Assigned to Front Desk Queue", "good", "strength"
    if a.startswith("Assignee"):
        return "Rerouted to correct department/person", "good", "strength"
    if a == "Category changed":
        return "Category correction needed", "issue", "issue"
    if a == "Subcategory changed":
        return "Subcategory correction needed", "issue", "issue"
    if a.startswith("State Resolved -> Reopened"):
        return "Reopened ticket (neutral per policy)", "neutral", "neutral"
    if a == "Comment edited":
        return "Comment edited after posting", "neutral", "watch"
    return "Unclassified action", "neutral", "neutral"


def main() -> None:
    audit = read_csv_rows(RAW / "audit_log.csv")
    slots = load_schedule(RAW / "schedule.csv")
    for r in audit:
        r["_dt"] = datetime.strptime(r["DateTime"], "%Y-%m-%d %H:%M")
    start = min(r["_dt"] for r in audit).date()
    end = max(r["_dt"] for r in audit).date()
    hours_by_tech = scheduled_hours(slots, start, end)

    techs = sorted({r["Technician"] for r in audit} | set(hours_by_tech))
    full_techs = {r["Technician"] for r in audit if r["Source"] == "Audit export"}

    # First touch per (tech, incident), for quick password resolutions.
    first_touch = {}
    for r in sorted(audit, key=lambda x: x["_dt"]):
        first_touch.setdefault((r["Technician"], r["Incident"]), r["_dt"])

    evidence = []
    for r in audit:
        if r["Source"] != "Audit export":
            rule, outcome, cat = "PDF-only partial context", "neutral", "neutral"
            note = "Included as partial context; full audit coverage unavailable"
        else:
            rule, outcome, cat = tag(r["Action"])
            note = ""
            if (rule == "Resolved ticket" and r["TicketCategory"] == "Password maintenance"
                    and r["_dt"] - first_touch[(r["Technician"], r["Incident"])] <= timedelta(minutes=QUICK_PASSWORD_MINUTES)):
                rule = "Password template quick resolution"
            if r["Action"] == "Comment added":
                note = f"Comment added '{r['Detail']}'"
        slot = slot_for(slots, r["_dt"])
        on_shift = int(bool(slot and r["Technician"] in slot.staff))
        evidence.append({
            "Student": r["Technician"], "Incident": r["Incident"], "DateTime": r["DateTime"], "Source": r["Source"],
            "QueueContext": "Front Desk Queue feed" if r["Source"] != "Audit export" else "All activity (queue may be implicit)",
            "ActionType": r["Action"], "RuleMatched": rule, "Outcome": outcome, "Category": cat, "Note": note,
            "_on_shift": on_shift, "_detail": r["Detail"],
        })

    # Metrics per technician (full audit coverage only).
    metrics = []
    by_tech = defaultdict(list)
    for e in evidence:
        by_tech[e["Student"]].append(e)
    for tech in techs:
        evs = [e for e in by_tech[tech] if e["Source"] == "Audit export"]
        hours = hours_by_tech.get(tech, 0.0)
        coverage = "Full" if tech in full_techs else "PDF-partial"
        rules = Counter(e["RuleMatched"] for e in evs)
        incidents = {e["Incident"] for e in evs}
        updates = len(evs)
        followup = rules["Follow-up valid (Comment added)"] + rules["Follow-up valid (Waiting on User)"]
        corrections = rules["Category correction needed"] + rules["Subcategory correction needed"]
        metrics.append({
            "Student": tech, "Coverage": coverage, "ScheduledHours": f"{hours:.1f}",
            "Updates": updates, "Incidents": len(incidents),
            "UpdatesPerHour": f"{(updates / hours if hours else 0):.2f}",
            "IncidentsPerHour": f"{(len(incidents) / hours if hours else 0):.2f}",
            "OnShiftPct": f"{(100 * sum(e['_on_shift'] for e in evs) / updates if updates else 0):.1f}",
            "FollowupValid": followup,
            "FollowupPerIncident": f"{(followup / len(incidents) if incidents else 0):.2f}",
            "RerouteOut": rules["Rerouted to correct department/person"],
            "ToFrontDesk": rules["Assigned to Front Desk Queue"],
            "AssignedToResolved": rules["Resolved ticket"] + rules["Password template quick resolution"],
            "AssignedToResponded": rules["Initial handling response"],
            "AccuracyCorrections": corrections,
            "PWQuickResolutions": rules["Password template quick resolution"],
            "ShortComments": sum(1 for e in evs if e["ActionType"] == "Comment added" and len(e["_detail"]) < SHORT_COMMENT_CHARS),
            "CommentUpdated": rules["Comment edited after posting"],
        })

    # Evidence sample: up to 20 actions per technician, spread evenly over their activity.
    sample = []
    for tech in techs:
        evs = sorted(by_tech[tech], key=lambda e: (e["DateTime"], e["Incident"]))
        if len(evs) > SAMPLE_PER_TECH:
            step = len(evs) / SAMPLE_PER_TECH
            evs = [evs[int(i * step)] for i in range(SAMPLE_PER_TECH)]
        sample.extend(evs)

    fields = ["Student", "Incident", "DateTime", "Source", "QueueContext", "ActionType", "RuleMatched", "Outcome", "Category", "Note"]
    write_csv(OUT / "evidence_log.csv", fields, sorted(evidence, key=lambda e: (e["Student"], e["DateTime"])))
    write_csv(OUT / "evidence_sample.csv", fields, sample)
    mfields = ["Student", "Coverage", "ScheduledHours", "Updates", "Incidents", "UpdatesPerHour", "IncidentsPerHour",
               "OnShiftPct", "FollowupValid", "FollowupPerIncident", "RerouteOut", "ToFrontDesk", "AssignedToResolved",
               "AssignedToResponded", "AccuracyCorrections", "PWQuickResolutions", "ShortComments", "CommentUpdated"]
    write_csv(OUT / "metrics_summary.csv", mfields, metrics)
    print(f"Stage 1: tagged {len(evidence)} actions; sample of {len(sample)}; metrics for {len(metrics)} technicians")


if __name__ == "__main__":
    main()
