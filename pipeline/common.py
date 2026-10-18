"""Shared helpers for the performance model pipeline."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "output"


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def to_float(v: Optional[object]) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v: Optional[object]) -> Optional[int]:
    f = to_float(v)
    return None if f is None else int(round(f))


def safe_div(num: float, den: float) -> float:
    return 0.0 if den == 0 else num / den


def ratio_or_none(num: float, den: float) -> Optional[float]:
    return None if den == 0 else num / den


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def percentile_rank(sorted_values: Sequence[float], value: float) -> float:
    """Share of values <= value."""
    if not sorted_values:
        return 0.0
    lo, hi = 0, len(sorted_values)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(sorted_values)


def percentrank_inc(sorted_vals: Sequence[float], x: float) -> float:
    """Same result as Excel PERCENTRANK.INC, with linear interpolation."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return 1.0
    if x <= sorted_vals[0]:
        return 0.0
    if x >= sorted_vals[-1]:
        return 1.0
    for i in range(n - 1):
        lo, hi = sorted_vals[i], sorted_vals[i + 1]
        if lo <= x <= hi:
            if hi == lo:
                return i / (n - 1)
            return (i + (x - lo) / (hi - lo)) / (n - 1)
    return 1.0


def split_names(s: str) -> List[str]:
    s = (s or "").strip()
    return [p.strip() for p in s.split("|") if p.strip()] if s else []


def is_full(coverage: str) -> bool:
    return (coverage or "").strip().lower() == "full"
