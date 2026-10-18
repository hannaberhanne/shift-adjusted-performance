"""Shift schedule parsing shared by stages 1 and 2."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from common import read_csv_rows, split_names


@dataclass(frozen=True)
class ShiftSlot:
    day: str
    label: str
    start: time
    end: time
    staff: Tuple[str, ...]

    @property
    def is_weekend(self) -> bool:
        return self.day in {"Saturday", "Sunday"}

    @property
    def is_night(self) -> bool:
        return self.start.hour >= 18

    @property
    def hours(self) -> float:
        return (datetime.combine(date.min, self.end) - datetime.combine(date.min, self.start)).seconds / 3600


def parse_ampm_token(token: str) -> time:
    token = token.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", token)
    if not m:
        raise ValueError(f"Unrecognized time token: {token!r}")
    hour, minute = int(m.group(1)), int(m.group(2) or "00")
    if hour == 12:
        hour = 0
    if m.group(3) == "pm":
        hour += 12
    return time(hour=hour, minute=minute)


def parse_time_range(label: str) -> Tuple[time, time]:
    cleaned = label.strip().lower().replace(" ", "")
    if "-" not in cleaned:
        raise ValueError(f"Malformed time range: {label!r}")
    left, right = cleaned.split("-", 1)
    if not left.endswith(("am", "pm")) and right.endswith(("am", "pm")):
        left = f"{left}{right[-2:]}"
    if left.endswith(("am", "pm")) and not right.endswith(("am", "pm")):
        right = f"{right}{left[-2:]}"
    return parse_ampm_token(left), parse_ampm_token(right)


def load_schedule(path: Path) -> List[ShiftSlot]:
    slots = []
    for r in read_csv_rows(path):
        start, end = parse_time_range(r["Slot"])
        names = []
        for n in split_names(r.get("Staff", "")):
            if n not in names:
                names.append(n)
        slots.append(ShiftSlot(day=r["Day"], label=r["Slot"], start=start, end=end, staff=tuple(names)))
    return slots


def slot_for(slots: Sequence[ShiftSlot], dt: datetime) -> Optional[ShiftSlot]:
    day, tod = dt.strftime("%A"), dt.time()
    matches = [s for s in slots if s.day == day and s.start <= tod < s.end]
    if not matches:
        return None
    return sorted(matches, key=lambda s: (len(s.staff), s.label), reverse=True)[0]


def scheduled_hours(slots: Sequence[ShiftSlot], start: date, end: date) -> Dict[str, float]:
    """Scheduled hours per person across the analysis window."""
    per_day: Dict[str, Dict[str, float]] = {}
    for s in slots:
        for name in s.staff:
            per_day.setdefault(name, {}).setdefault(s.day, 0.0)
            per_day[name][s.day] += s.hours
    totals: Dict[str, float] = {}
    d = start
    while d <= end:
        day = d.strftime("%A")
        for name, days in per_day.items():
            totals[name] = totals.get(name, 0.0) + days.get(day, 0.0)
        d += timedelta(days=1)
    return totals
