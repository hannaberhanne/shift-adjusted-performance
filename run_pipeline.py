#!/usr/bin/env python3
"""Run every stage in order. Use --sample to regenerate the synthetic data first."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGES = [
    "s1_tag_evidence",
    "s2_schedule_context",
    "s3_benchmark",
    "s4_standardize",
    "s5_scorecard",
    "s6_checkin",
    "s7_raw_packet",
]


def main() -> None:
    sys.path.insert(0, str(ROOT / "pipeline"))
    if "--sample" in sys.argv:
        print("== make_sample_data")
        runpy.run_path(str(ROOT / "scripts" / "make_sample_data.py"), run_name="__main__")
    for stage in STAGES:
        print(f"== {stage}")
        runpy.run_path(str(ROOT / "pipeline" / f"{stage}.py"), run_name="__main__")
    print("Done. Outputs are in output/.")


if __name__ == "__main__":
    main()
