#!/usr/bin/env python3
"""Write a synthetic service desk dataset so the whole pipeline can run.

Every technician, ticket and comment here is made up. The shapes follow a real
student service desk: a shift schedule, an audit log of ticket actions, and a
couple of technicians whose activity only shows up in a partial export.

Outputs:
  data/raw/schedule.csv    one row per day and hourly slot, with who is scheduled
  data/raw/audit_log.csv   one row per ticket action
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SEED = 26
WINDOW_START = date(2026, 1, 22)
WINDOW_END = date(2026, 2, 23)
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_HOURS = range(8, 23)   # 8am to 11pm
WEEKEND_HOURS = range(10, 18)  # 10am to 6pm

# Weekly shift blocks: (day, start hour, end hour).
BLOCKS = {
    "Tech 01": [("Monday", 18, 23), ("Wednesday", 18, 23), ("Saturday", 10, 18), ("Sunday", 10, 14)],
    "Tech 02": [("Monday", 12, 17), ("Tuesday", 12, 17), ("Thursday", 12, 17)],
    "Tech 03": [("Tuesday", 8, 12), ("Wednesday", 8, 12), ("Friday", 8, 13)],
    "Tech 04": [("Monday", 8, 12), ("Thursday", 8, 12), ("Friday", 13, 17)],
    "Tech 05": [("Tuesday", 17, 23), ("Thursday", 17, 23), ("Sunday", 14, 18)],
    "Tech 06": [("Monday", 12, 17), ("Wednesday", 12, 17), ("Friday", 12, 17)],
    "Tech 07": [("Tuesday", 12, 17), ("Wednesday", 12, 17), ("Thursday", 12, 16)],
    "Tech 08": [("Monday", 8, 12), ("Tuesday", 8, 12), ("Wednesday", 8, 12)],
    "Tech 09": [("Friday", 17, 23), ("Saturday", 10, 18)],
    "Tech 10": [("Monday", 17, 22), ("Wednesday", 17, 22), ("Thursday", 8, 12)],
    "Tech 11": [("Tuesday", 8, 12), ("Thursday", 12, 17), ("Friday", 8, 12)],
    "Tech 12": [("Monday", 12, 16), ("Wednesday", 8, 12), ("Friday", 12, 16)],
    "Tech 13": [("Thursday", 17, 23), ("Sunday", 10, 18)],
    "Tech 14": [("Tuesday", 12, 17), ("Friday", 8, 12)],
    "Tech 15": [("Monday", 17, 22), ("Saturday", 10, 14)],
}
PARTIAL = {"Tech 14", "Tech 15"}

# Per-technician behavior: touches per scheduled hour, follow-up rate, correction
# rate, share of activity during their own shifts, short-comment rate.
PROFILE = {
    "Tech 01": (1.3, 0.75, 0.02, 0.97, 0.05),
    "Tech 02": (2.4, 0.70, 0.05, 0.96, 0.04),
    "Tech 03": (2.0, 0.55, 0.10, 0.99, 0.10),
    "Tech 04": (1.6, 0.80, 0.01, 0.95, 0.20),
    "Tech 05": (1.1, 0.60, 0.04, 0.98, 0.05),
    "Tech 06": (1.4, 0.45, 0.06, 0.90, 0.05),
    "Tech 07": (1.2, 0.65, 0.03, 0.88, 0.05),
    "Tech 08": (0.9, 0.50, 0.08, 0.99, 0.08),
    "Tech 09": (0.6, 0.55, 0.02, 0.99, 0.05),
    "Tech 10": (0.5, 0.40, 0.12, 0.72, 0.05),
    "Tech 11": (0.4, 0.35, 0.15, 0.99, 0.10),
    "Tech 12": (0.3, 0.20, 0.20, 0.99, 0.05),
    "Tech 13": (0.35, 0.60, 0.05, 0.99, 0.05),
}
TEAMS = ["Academic Solutions", "Technical Support", "Network Services", "Classroom Media Services",
         "Asset Management", "Enterprise Solutions", "Service Desk"]
CATEGORIES = ["Password maintenance"] * 3 + ["Account access", "Wi-Fi", "Printing", "Classroom AV",
                                             "Software", "Hardware", "Canvas", "Email"]
COMMENTS = ["Called the user and walked through the reset steps.",
            "Left a voicemail and sent an email with next steps.",
            "Confirmed the issue is resolved with the user.",
            "Checked the device in AD; account is active.",
            "Asked the user for a screenshot of the error.",
            "Reassigned with troubleshooting notes attached.",
            "Following up: waiting on the user to test again."]
SHORT = ["ok", "done", "called", "fixed"]


def dates_in_window():
    d = WINDOW_START
    while d <= WINDOW_END:
        yield d
        d += timedelta(days=1)


def slot_label(h: int) -> str:
    def fmt(x: int) -> str:
        suffix = "am" if x < 12 else "pm"
        hr = x % 12 or 12
        return f"{hr}:00{suffix}"
    return f"{fmt(h)}-{fmt(h + 1)}".replace(":00am-", ":00-").replace(":00pm-", ":00-") if (h < 11 or h >= 12) else f"{fmt(h)}-{fmt(h + 1)}"


def main() -> None:
    rng = random.Random(SEED)
    RAW.mkdir(parents=True, exist_ok=True)

    # Schedule: who is on each hourly slot.
    staff = {(d, h): [] for d in DAYS for h in (WEEKEND_HOURS if d in ("Saturday", "Sunday") else WEEKDAY_HOURS)}
    for tech, blocks in BLOCKS.items():
        for day, a, b in blocks:
            for h in range(a, b):
                if (day, h) in staff:
                    staff[(day, h)].append(tech)
    with (RAW / "schedule.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Day", "Slot", "Staff"])
        for d in DAYS:
            for h in (WEEKEND_HOURS if d in ("Saturday", "Sunday") else WEEKDAY_HOURS):
                w.writerow([d, slot_label(h), " | ".join(staff[(d, h)])])

    # Audit log.
    rows = []
    incident_no = 240100
    open_incidents = []  # (number, category, created)

    def new_incident(when):
        nonlocal incident_no
        incident_no += rng.randint(1, 4)
        inc = (f"#{incident_no}", rng.choice(CATEGORIES), when)
        open_incidents.append(inc)
        return inc

    def add(tech, when, inc, action, detail="", source="Audit export"):
        rows.append({"Incident": inc[0], "DateTime": when.strftime("%Y-%m-%d %H:%M"), "Technician": tech,
                     "Action": action, "Detail": detail, "TicketCategory": inc[1], "Source": source})

    on_hours = {t: [(day, h) for day, a, b in bl for h in range(a, b)] for t, bl in BLOCKS.items()}
    for d in dates_in_window():
        day = d.strftime("%A")
        for tech, (rate, fu, corr, onshift, short) in PROFILE.items():
            my_hours = [h for dd, h in on_hours[tech] if dd == day]
            for h in my_hours:
                n = sum(1 for _ in range(6) if rng.random() < rate / 6)
                for _ in range(n):
                    if rng.random() > onshift:  # some activity happens off the schedule
                        hh = rng.choice([7, 23] + list(range(8, 23)))
                        when = datetime(d.year, d.month, d.day, hh, rng.randint(0, 59))
                    else:
                        when = datetime(d.year, d.month, d.day, h, rng.randint(0, 59))
                    reuse = open_incidents and rng.random() < 0.35
                    inc = rng.choice(open_incidents[-40:]) if reuse else new_incident(when)
                    if inc[2] > when:
                        inc = new_incident(when)
                    t = when
                    step = lambda: t + timedelta(minutes=rng.randint(1, 6))
                    if not reuse and rng.random() < 0.15:
                        add(tech, t, inc, "State Assigned -> Responded"); t = step()
                    if rng.random() < corr:
                        add(tech, t, inc, rng.choice(["Category changed", "Subcategory changed"])); t = step()
                    if rng.random() < fu:
                        text = rng.choice(SHORT) if rng.random() < short else rng.choice(COMMENTS)
                        add(tech, t, inc, "Comment added", text); t = step()
                        if rng.random() < 0.08:
                            add(tech, t, inc, "Comment edited"); t = step()
                    roll = rng.random()
                    if roll < 0.50:
                        target = "Front Desk Queue" if rng.random() < 0.25 else rng.choice(TEAMS)
                        add(tech, t, inc, f"Assignee -1 -> {target}"); t = step()
                        if inc in open_incidents:
                            open_incidents.remove(inc)
                    elif roll < 0.72:
                        if rng.random() < 0.25:
                            add(tech, t, inc, "State Responded -> Waiting on User"); t = step()
                        else:
                            add(tech, t, inc, "State Assigned -> Resolved"); t = step()
                            add(tech, t, inc, "Resolution code set"); t = step()
                            if inc in open_incidents:
                                open_incidents.remove(inc)
                            if rng.random() < 0.03:
                                add(tech, t + timedelta(days=1), inc, "State Resolved -> Reopened by Client")

    # Partial coverage: only a few rows from a queue PDF export.
    for tech in sorted(PARTIAL):
        for _ in range(rng.randint(4, 7)):
            d = rng.choice(list(dates_in_window()))
            when = datetime(d.year, d.month, d.day, rng.choice(list(range(10, 17))), rng.randint(0, 59))
            inc = new_incident(when)
            add(tech, when, inc, rng.choice(["State change", "Comment added", "Assignment"]), "", "Queue PDF")

    rows.sort(key=lambda r: (r["DateTime"], r["Incident"]))
    with (RAW / "audit_log.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} synthetic audit rows for {len(BLOCKS)} technicians to {RAW}")


if __name__ == "__main__":
    main()
