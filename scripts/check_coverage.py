#!/usr/bin/env python3
"""Enforce independent statement and branch coverage thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _percentage(totals: dict[str, Any], key: str) -> float:
    value = totals.get(key)
    if not isinstance(value, (int, float)):
        raise ValueError(f"coverage report is missing numeric field {key!r}")
    return float(value)


def check_coverage(
    report_path: Path, min_statements: float, min_branches: float
) -> tuple[float, float]:
    """Validate coverage JSON and return the measured percentages."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"unable to read coverage report {report_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid coverage JSON in {report_path}: {exc}") from exc

    totals = report.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("coverage report is missing a totals mapping")

    statements = _percentage(totals, "percent_statements_covered")
    branches = _percentage(totals, "percent_branches_covered")
    failures: list[str] = []
    if statements < min_statements:
        failures.append(f"statement coverage {statements:.2f}% is below {min_statements:.2f}%")
    if branches < min_branches:
        failures.append(f"branch coverage {branches:.2f}% is below {min_branches:.2f}%")
    if failures:
        raise ValueError("; ".join(failures))
    return statements, branches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="coverage.py JSON report")
    parser.add_argument("--min-statements", type=float, required=True)
    parser.add_argument("--min-branches", type=float, required=True)
    args = parser.parse_args()

    try:
        statements, branches = check_coverage(
            args.report,
            min_statements=args.min_statements,
            min_branches=args.min_branches,
        )
    except ValueError as exc:
        parser.error(str(exc))

    print(f"statement coverage: {statements:.2f}%")
    print(f"branch coverage: {branches:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
