"""Compare a new run against the frozen baseline.

This module answers: what changed, and how bad is it?

It uses three inputs:
  - contract.py result  → did cases break the schema?
  - numeric.py result   → did scores move?
  - thresholds.yaml     → is the movement PASS, WARN, or CRITICAL?

The three severity levels:
  PASS     = no problems.
  WARN     = something shifted but not enough to block a merge.
  CRITICAL = block the merge.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from .scoring.contract import validate, ViolationType
from .scoring.numeric import compare as numeric_compare, NumericComparison
from .store import RunRecord, load_baseline


class Severity(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class DiffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity

    # Contract axis (hard gate)
    total_violations: int
    violation_summary: list[dict]   # [{type, count, cases: [case_id]}]

    # Numeric axis (thresholded)
    cases_compared: int
    cases_excluded: int             # excluded because they broke the contract
    numeric: NumericComparison

    # Human-readable summary lines for the PR comment and report header
    headline: str
    details: list[str]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THRESHOLDS = PROJECT_ROOT / "config" / "thresholds.yaml"


def _load_thresholds(path: Path | None = None) -> dict:
    return yaml.safe_load((path or DEFAULT_THRESHOLDS).read_text(encoding="utf-8"))


def _violation_summary(current: RunRecord) -> tuple[int, list[dict]]:
    """Count and group contract violations across all cases in this run."""
    total = 0
    by_type: dict[str, list[str]] = {}
    for case in current.cases:
        for v in case.violations:
            total += 1
            vtype = v["type"]
            by_type.setdefault(vtype, []).append(case.case_id)
    summary = [
        {"type": vtype, "count": len(cases), "cases": cases}
        for vtype, cases in sorted(by_type.items())
    ]
    return total, summary


def _decide_severity(
    total_violations: int,
    numeric: NumericComparison,
    thresholds: dict,
) -> tuple[Severity, list[str]]:
    details: list[str] = []
    severity = Severity.PASS

    # --- Contract gate (hard) ---
    max_v = thresholds["contract"]["max_violations"]
    if total_violations > max_v:
        severity = Severity.CRITICAL
        details.append(
            f"CRITICAL: {total_violations} contract violation(s) — schema breakage blocks merge"
        )

    # --- Numeric gate (thresholded) ---
    crit_dim = thresholds["numeric"]["critical_dimension_delta"]
    warn_dim = thresholds["numeric"]["warn_dimension_delta"]
    crit_enum = thresholds["numeric"]["critical_enum_changes"]
    warn_enum = thresholds["numeric"]["warn_enum_changes"]

    worst = numeric.worst_mean_abs_delta()
    if worst >= crit_dim:
        if severity != Severity.CRITICAL:
            severity = Severity.CRITICAL
        details.append(
            f"CRITICAL: worst mean score delta = {worst:.2f} pts "
            f"(threshold {crit_dim})"
        )
    elif worst >= warn_dim:
        if severity == Severity.PASS:
            severity = Severity.WARN
        details.append(
            f"WARN: worst mean score delta = {worst:.2f} pts "
            f"(threshold {warn_dim})"
        )

    n_enum = len(numeric.pitch_changes)
    if n_enum >= crit_enum:
        if severity != Severity.CRITICAL:
            severity = Severity.CRITICAL
        details.append(
            f"CRITICAL: {n_enum} pitch_rating changes (threshold {crit_enum})"
        )
    elif n_enum >= warn_enum:
        if severity == Severity.PASS:
            severity = Severity.WARN
        details.append(
            f"WARN: {n_enum} pitch_rating changes (threshold {warn_enum})"
        )

    if not details:
        details.append("PASS: all metrics within thresholds")

    return severity, details


def _headline(severity: Severity, total_violations: int, numeric: NumericComparison) -> str:
    v_part = (
        f"{total_violations} contract violation(s)"
        if total_violations
        else "0 contract violations"
    )
    n = numeric.cases_compared
    regressed = sum(1 for c in numeric.cases if c.direction == "regressed")
    n_part = f"{regressed} of {n} cases regressed" if n else "no cases compared"
    return f"{severity.value}: {v_part}, {n_part}"


def run_diff(
    current: RunRecord,
    baseline: RunRecord | None = None,
    thresholds_path: Path | None = None,
) -> DiffResult:
    """Compare `current` against `baseline` (defaults to the frozen golden baseline)."""
    if baseline is None:
        baseline = load_baseline()

    thresholds = _load_thresholds(thresholds_path)
    total_violations, violation_summary = _violation_summary(current)
    numeric = numeric_compare(baseline, current)
    severity, details = _decide_severity(total_violations, numeric, thresholds)

    return DiffResult(
        severity=severity,
        total_violations=total_violations,
        violation_summary=violation_summary,
        cases_compared=numeric.cases_compared,
        cases_excluded=len(numeric.cases_unpaired),
        numeric=numeric,
        headline=_headline(severity, total_violations, numeric),
        details=details,
    )
