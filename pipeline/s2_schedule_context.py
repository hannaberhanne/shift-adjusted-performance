#!/usr/bin/env python3
"""Stage 2: put every sampled action in context: the shift schedule, who else was on, and how slow the ticket was.

Inputs:   output/evidence_sample.csv, output/metrics_summary.csv, data/raw/schedule.csv
Outputs:  output/evidence_shift_slots.csv          schedule slots with staff, night and weekend flags
          output/evidence_incident_summary.csv     ticket timelines and slow-ticket flags (P75 / P90 of observed span)
          output/evidence_activity_enriched.csv    each action with its slot, team size, co-scheduled peers and timing
          output/evidence_student_time_context.csv per-technician night, weekend, slow-ticket and on-slot counts
          output/evidence_pair_overlap_slots.csv   how often each pair of technicians is scheduled together
"""

from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, RAW, percentile_rank, quantile, read_csv_rows  # noqa: E402
from schedule import load_schedule, slot_for  # noqa: E402

EVIDENCE_PATH = OUT / "evidence_sample.csv"
METRICS_PATH = OUT / "metrics_summary.csv"
SCHEDULE_PATH = RAW / "schedule.csv"

OUT_SHIFT_SLOTS = OUT / "evidence_shift_slots.csv"
OUT_INCIDENT_SUMMARY = OUT / "evidence_incident_summary.csv"
OUT_ACTIVITY_ENRICHED = OUT / "evidence_activity_enriched.csv"
OUT_STUDENT_TIME = OUT / "evidence_student_time_context.csv"
OUT_PAIR_OVERLAP = OUT / "evidence_pair_overlap_slots.csv"

DAY_ORDER = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def parse_activity_datetime(raw: str) -> datetime:
    return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M")


def main() -> None:
    evidence_rows = read_csv_rows(EVIDENCE_PATH)
    metric_rows = read_csv_rows(METRICS_PATH)
    slots = load_schedule(SCHEDULE_PATH)

    # Student roster from metrics + evidence.
    roster = sorted(
        {
            *(r["Student"].strip() for r in metric_rows if r.get("Student", "").strip()),
            *(r["Student"].strip() for r in evidence_rows if r.get("Student", "").strip()),
        }
    )

    # Parse datetimes once and preserve row order.
    parsed_rows = []
    for i, r in enumerate(evidence_rows):
        dt = parse_activity_datetime(r["DateTime"])
        parsed_rows.append((i, r, dt))

    # Incident-level timeline features.
    incident_rows: Dict[str, List[Tuple[int, Dict[str, str], datetime]]] = defaultdict(list)
    for i, r, dt in parsed_rows:
        incident_rows[r["Incident"]].append((i, r, dt))

    incident_summary = {}
    incident_spans = []
    for incident, rows_for_incident in incident_rows.items():
        ordered = sorted(rows_for_incident, key=lambda x: x[2])
        first_dt = ordered[0][2]
        last_dt = ordered[-1][2]
        span_minutes = int((last_dt - first_dt).total_seconds() // 60)
        students = sorted({x[1]["Student"].strip() for x in ordered if x[1]["Student"].strip()})
        weekend_touches = sum(1 for _, _, d in ordered if d.weekday() >= 5)
        night_touches = sum(1 for _, _, d in ordered if d.hour >= 18)
        incident_summary[incident] = {
            "Incident": incident,
            "FirstObserved": first_dt.strftime("%Y-%m-%d %H:%M"),
            "LastObserved": last_dt.strftime("%Y-%m-%d %H:%M"),
            "ObservedSpanMinutes": span_minutes,
            "ActionCount": len(ordered),
            "UniqueStudents": len(students),
            "Students": " | ".join(students),
            "WeekendTouches": weekend_touches,
            "NightTouches": night_touches,
            "HasWeekendTouch": int(weekend_touches > 0),
            "HasNightTouch": int(night_touches > 0),
        }
        incident_spans.append(span_minutes)

    sorted_spans = sorted(incident_spans)
    nonzero_spans = sorted(s for s in incident_spans if s > 0)
    p75 = quantile(sorted_spans, 0.75)
    p90 = quantile(sorted_spans, 0.90)
    p75_nz = quantile(nonzero_spans, 0.75) if nonzero_spans else 0.0
    p90_nz = quantile(nonzero_spans, 0.90) if nonzero_spans else 0.0

    for inc, summary in incident_summary.items():
        span = float(summary["ObservedSpanMinutes"])
        pr = percentile_rank(sorted_spans, span)
        pr_nz = percentile_rank(nonzero_spans, span) if span > 0 and nonzero_spans else 0.0
        summary["ObservedSpanPercentile"] = round(pr, 4)
        summary["ObservedSpanPercentileNonZero"] = round(pr_nz, 4)
        summary["SlowIncidentP75"] = int(span >= p75)
        summary["SlowIncidentP90"] = int(span >= p90)
        summary["SlowIncidentNonZeroP75"] = int(span > 0 and span >= p75_nz)
        summary["SlowIncidentNonZeroP90"] = int(span > 0 and span >= p90_nz)

    # Precompute per-incident prior-action deltas.
    incident_prior_delta = {}
    for incident, rows_for_incident in incident_rows.items():
        ordered = sorted(rows_for_incident, key=lambda x: x[2])
        prev_dt = None
        first_dt = ordered[0][2]
        for idx, _, dt in ordered:
            if prev_dt is None:
                delta_prev = ""
            else:
                delta_prev = int((dt - prev_dt).total_seconds() // 60)
            elapsed = int((dt - first_dt).total_seconds() // 60)
            incident_prior_delta[idx] = (delta_prev, elapsed)
            prev_dt = dt

    # Student+incident handling span.
    student_incident_dt: Dict[Tuple[str, str], List[datetime]] = defaultdict(list)
    for _, r, dt in parsed_rows:
        student_incident_dt[(r["Student"].strip(), r["Incident"])].append(dt)
    student_incident_span = {}
    for key, dts in student_incident_dt.items():
        dts_sorted = sorted(dts)
        span = int((dts_sorted[-1] - dts_sorted[0]).total_seconds() // 60)
        student_incident_span[key] = span

    # Build enriched activity rows and student context aggregates.
    student_context = {
        s: Counter(
            {
                "Events": 0,
                "WeekendEvents": 0,
                "NightEvents": 0,
                "OnScheduledSlotEvents": 0,
                "OffScheduledSlotEvents": 0,
                "SlowIncidentP75Events": 0,
                "SlowIncidentP90Events": 0,
            }
        )
        for s in roster
    }
    student_incident_sets = {s: set() for s in roster}
    student_slow75_sets = {s: set() for s in roster}
    student_slow90_sets = {s: set() for s in roster}
    student_slow_nz75_sets = {s: set() for s in roster}
    student_slow_nz90_sets = {s: set() for s in roster}

    enriched = []
    for idx, r, dt in parsed_rows:
        student = r["Student"].strip()
        incident = r["Incident"]
        day = dt.strftime("%A")
        slot = slot_for(slots, dt)
        scheduled_staff = list(slot.staff) if slot else []
        co_staff = [n for n in scheduled_staff if n != student]
        in_slot = int(student in scheduled_staff) if slot else 0

        incident_info = incident_summary[incident]
        delta_prev, elapsed = incident_prior_delta[idx]
        student_span = student_incident_span.get((student, incident), 0)

        row = dict(r)
        row.update(
            {
                "DateTimeParsed": dt.strftime("%Y-%m-%d %H:%M"),
                "DayOfWeek": day,
                "Hour24": dt.hour,
                "IsWeekend": int(dt.weekday() >= 5),
                "IsNightByClock": int(dt.hour >= 18),
                "ScheduleSlotLabel": slot.label if slot else "",
                "ScheduleSlotStart": slot.start.strftime("%H:%M") if slot else "",
                "ScheduleSlotEnd": slot.end.strftime("%H:%M") if slot else "",
                "IsNightByScheduleSlot": int(slot.is_night) if slot else "",
                "ScheduledTeamSize": len(scheduled_staff),
                "ScheduledTeam": " | ".join(scheduled_staff),
                "StudentScheduledInSlot": in_slot,
                "CoScheduledPeers": " | ".join(co_staff),
                "CoScheduledPeerCount": len(co_staff),
                "IncidentFirstObserved": incident_info["FirstObserved"],
                "IncidentLastObserved": incident_info["LastObserved"],
                "IncidentObservedSpanMinutes": incident_info["ObservedSpanMinutes"],
                "IncidentObservedSpanPercentile": incident_info["ObservedSpanPercentile"],
                "IncidentObservedSpanPercentileNonZero": incident_info["ObservedSpanPercentileNonZero"],
                "SlowIncidentP75": incident_info["SlowIncidentP75"],
                "SlowIncidentP90": incident_info["SlowIncidentP90"],
                "SlowIncidentNonZeroP75": incident_info["SlowIncidentNonZeroP75"],
                "SlowIncidentNonZeroP90": incident_info["SlowIncidentNonZeroP90"],
                "MinutesSincePreviousIncidentAction": delta_prev,
                "MinutesFromIncidentFirstObserved": elapsed,
                "StudentIncidentHandlingSpanMinutes": student_span,
            }
        )
        enriched.append(row)

        if student not in student_context:
            student_context[student] = Counter()
            student_incident_sets[student] = set()
            student_slow75_sets[student] = set()
            student_slow90_sets[student] = set()
            student_slow_nz75_sets[student] = set()
            student_slow_nz90_sets[student] = set()

        student_context[student]["Events"] += 1
        student_incident_sets[student].add(incident)
        if dt.weekday() >= 5:
            student_context[student]["WeekendEvents"] += 1
        if dt.hour >= 18:
            student_context[student]["NightEvents"] += 1
        if in_slot:
            student_context[student]["OnScheduledSlotEvents"] += 1
        else:
            student_context[student]["OffScheduledSlotEvents"] += 1
        if incident_info["SlowIncidentP75"]:
            student_context[student]["SlowIncidentP75Events"] += 1
            student_slow75_sets[student].add(incident)
        if incident_info["SlowIncidentP90"]:
            student_context[student]["SlowIncidentP90Events"] += 1
            student_slow90_sets[student].add(incident)
        if incident_info["SlowIncidentNonZeroP75"]:
            student_context[student]["SlowIncidentNonZeroP75Events"] += 1
            student_slow_nz75_sets[student].add(incident)
        if incident_info["SlowIncidentNonZeroP90"]:
            student_context[student]["SlowIncidentNonZeroP90Events"] += 1
            student_slow_nz90_sets[student].add(incident)

    # Pair overlap from schedule template (slot-level co-scheduling only).
    pair_overlap = Counter()
    student_slot_exposure = Counter()
    for s in slots:
        staff = sorted(set(s.staff))
        for name in staff:
            student_slot_exposure[name] += 1
        for i in range(len(staff)):
            for j in range(i + 1, len(staff)):
                pair_overlap[(staff[i], staff[j])] += 1

    # Write outputs.
    shift_fields = [
        "DayOfWeek",
        "SlotLabel",
        "SlotStart",
        "SlotEnd",
        "IsWeekendSlot",
        "IsNightSlot",
        "ScheduledTeamSize",
        "ScheduledTeam",
    ]
    with OUT_SHIFT_SLOTS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=shift_fields)
        w.writeheader()
        for s in sorted(slots, key=lambda x: (DAY_ORDER.index(x.day), x.start)):
            w.writerow(
                {
                    "DayOfWeek": s.day,
                    "SlotLabel": s.label,
                    "SlotStart": s.start.strftime("%H:%M"),
                    "SlotEnd": s.end.strftime("%H:%M"),
                    "IsWeekendSlot": int(s.is_weekend),
                    "IsNightSlot": int(s.is_night),
                    "ScheduledTeamSize": len(s.staff),
                    "ScheduledTeam": " | ".join(s.staff),
                }
            )

    incident_fields = [
        "Incident",
        "FirstObserved",
        "LastObserved",
        "ObservedSpanMinutes",
        "ObservedSpanPercentile",
        "ObservedSpanPercentileNonZero",
        "SlowIncidentP75",
        "SlowIncidentP90",
        "SlowIncidentNonZeroP75",
        "SlowIncidentNonZeroP90",
        "ActionCount",
        "UniqueStudents",
        "Students",
        "WeekendTouches",
        "NightTouches",
        "HasWeekendTouch",
        "HasNightTouch",
    ]
    with OUT_INCIDENT_SUMMARY.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=incident_fields)
        w.writeheader()
        for inc in sorted(incident_summary):
            w.writerow({k: incident_summary[inc][k] for k in incident_fields})

    enriched_fields = list(evidence_rows[0].keys()) + [
        "DateTimeParsed",
        "DayOfWeek",
        "Hour24",
        "IsWeekend",
        "IsNightByClock",
        "ScheduleSlotLabel",
        "ScheduleSlotStart",
        "ScheduleSlotEnd",
        "IsNightByScheduleSlot",
        "ScheduledTeamSize",
        "ScheduledTeam",
        "StudentScheduledInSlot",
        "CoScheduledPeers",
        "CoScheduledPeerCount",
        "IncidentFirstObserved",
        "IncidentLastObserved",
        "IncidentObservedSpanMinutes",
        "IncidentObservedSpanPercentile",
        "IncidentObservedSpanPercentileNonZero",
        "SlowIncidentP75",
        "SlowIncidentP90",
        "SlowIncidentNonZeroP75",
        "SlowIncidentNonZeroP90",
        "MinutesSincePreviousIncidentAction",
        "MinutesFromIncidentFirstObserved",
        "StudentIncidentHandlingSpanMinutes",
    ]
    with OUT_ACTIVITY_ENRICHED.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=enriched_fields)
        w.writeheader()
        w.writerows(enriched)

    student_fields = [
        "Student",
        "Events",
        "UniqueIncidents",
        "WeekendEvents",
        "NightEvents",
        "OnScheduledSlotEvents",
        "OffScheduledSlotEvents",
        "SlowIncidentP75Events",
        "SlowIncidentP90Events",
        "SlowIncidentNonZeroP75Events",
        "SlowIncidentNonZeroP90Events",
        "UniqueSlowP75Incidents",
        "UniqueSlowP90Incidents",
        "UniqueSlowNonZeroP75Incidents",
        "UniqueSlowNonZeroP90Incidents",
        "ScheduledSlotExposureCount",
    ]
    with OUT_STUDENT_TIME.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=student_fields)
        w.writeheader()
        for student in sorted(student_context):
            c = student_context[student]
            w.writerow(
                {
                    "Student": student,
                    "Events": c["Events"],
                    "UniqueIncidents": len(student_incident_sets.get(student, set())),
                    "WeekendEvents": c["WeekendEvents"],
                    "NightEvents": c["NightEvents"],
                    "OnScheduledSlotEvents": c["OnScheduledSlotEvents"],
                    "OffScheduledSlotEvents": c["OffScheduledSlotEvents"],
                    "SlowIncidentP75Events": c["SlowIncidentP75Events"],
                    "SlowIncidentP90Events": c["SlowIncidentP90Events"],
                    "SlowIncidentNonZeroP75Events": c["SlowIncidentNonZeroP75Events"],
                    "SlowIncidentNonZeroP90Events": c["SlowIncidentNonZeroP90Events"],
                    "UniqueSlowP75Incidents": len(student_slow75_sets.get(student, set())),
                    "UniqueSlowP90Incidents": len(student_slow90_sets.get(student, set())),
                    "UniqueSlowNonZeroP75Incidents": len(student_slow_nz75_sets.get(student, set())),
                    "UniqueSlowNonZeroP90Incidents": len(student_slow_nz90_sets.get(student, set())),
                    "ScheduledSlotExposureCount": student_slot_exposure.get(student, 0),
                }
            )

    pair_fields = [
        "StudentA",
        "StudentB",
        "CoScheduledSlotCount",
    ]
    with OUT_PAIR_OVERLAP.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=pair_fields)
        w.writeheader()
        for (a, b), n in sorted(pair_overlap.items(), key=lambda x: (-x[1], x[0][0], x[0][1])):
            w.writerow({"StudentA": a, "StudentB": b, "CoScheduledSlotCount": n})

    print("Wrote:")
    for p in [
        OUT_SHIFT_SLOTS,
        OUT_INCIDENT_SUMMARY,
        OUT_ACTIVITY_ENRICHED,
        OUT_STUDENT_TIME,
        OUT_PAIR_OVERLAP,
    ]:
        print(f"- {p.name}")
    print(
        "Slow-ticket span thresholds (minutes): "
        f"all-incidents P75={p75:.2f}, P90={p90:.2f}; "
        f"non-zero-span P75={p75_nz:.2f}, P90={p90_nz:.2f}"
    )


if __name__ == "__main__":
    main()
