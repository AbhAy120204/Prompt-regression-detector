"""Generate the markdown PR comment.

This is what appears on the PR itself. It shows enough detail that a reviewer
can decide whether to merge without downloading anything. The full HTML report
link is at the bottom for anyone who wants the side-by-side reasoning panels.
"""

from __future__ import annotations

from .diff import DiffResult, Severity
from .store import RunRecord


def _severity_badge(severity: Severity) -> str:
    if severity == Severity.CRITICAL:
        return "🔴 CRITICAL — merge blocked"
    elif severity == Severity.WARN:
        return "🟡 WARN — review recommended"
    return "🟢 PASS"


def _contract_section(diff: DiffResult) -> str:
    if diff.total_violations == 0:
        return "✅ **Contract: no violations.** All cases returned valid JSON.\n"

    lines = [
        f"🚨 **Contract: {diff.total_violations} violation(s) found.**",
        f"Cases with violations are excluded from score comparison.",
        "",
        "| Violation type | Count | Affected cases |",
        "|---|---|---|",
    ]
    for v in diff.violation_summary:
        cases_str = ", ".join(f"`{c}`" for c in v["cases"])
        lines.append(f"| `{v['type']}` | {v['count']} | {cases_str} |")
    lines.append("")
    return "\n".join(lines)


def _scores_section(diff: DiffResult, baseline: RunRecord, current: RunRecord) -> str:
    n = diff.numeric
    if n.cases_compared == 0:
        return "*No valid paired cases to compare scores.*\n"

    lines = [
        f"**Score comparison** ({n.cases_compared} of {current.totals.cases_total} cases)",
        "",
        "| Dimension | Baseline | Current | Delta | Moved ≥2 |",
        "|---|:---:|:---:|:---:|:---:|",
    ]

    for fname, fd in n.per_field.items():
        delta_str = f"{fd.delta:+.1f}" if fd.delta != 0 else "0.0"
        # Colour the delta with emoji
        if fd.delta < -0.5:
            delta_str = f"📉 {delta_str}"
        elif fd.delta > 0.5:
            delta_str = f"📈 {delta_str}"
        lines.append(
            f"| {fname} | {fd.baseline_mean:.1f} | {fd.current_mean:.1f} "
            f"| {delta_str} | {fd.moved} |"
        )
    lines.append("")
    return "\n".join(lines)


def _pitch_changes_section(diff: DiffResult) -> str:
    changes = diff.numeric.pitch_changes
    if not changes:
        return ""

    lines = [
        f"**pitch_rating changes** ({len(changes)})",
        "",
        "| Case | Baseline | Current |",
        "|---|---|---|",
    ]
    for case_id, frm, to in changes:
        lines.append(f"| `{case_id}` | {frm} | **{to}** |")
    lines.append("")
    return "\n".join(lines)


def _regressed_cases_section(diff: DiffResult) -> str:
    regressed = [c for c in diff.numeric.cases if c.direction == "regressed"]
    if not regressed:
        return ""

    # Sort worst first
    regressed.sort(key=lambda c: c.max_abs_delta, reverse=True)

    lines = [
        f"**Regressed cases** ({len(regressed)})",
        "",
        "| Case | Max delta | Dimensions affected |",
        "|---|:---:|---|",
    ]
    for c in regressed:
        affected = [
            f"{dim} ({c.deltas[dim]:+d})"
            for dim in c.deltas
            if c.deltas[dim] < 0
        ]
        lines.append(
            f"| `{c.case_id}` | {c.max_abs_delta} | {', '.join(affected)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _meta_section(baseline: RunRecord, current: RunRecord) -> str:
    return (
        f"| | Baseline | Current |\n"
        f"|---|---|---|\n"
        f"| **Prompt** | `{baseline.prompt_version}` | `{current.prompt_version}` |\n"
        f"| **Model** | `{baseline.model}` | `{current.model}` |\n"
        f"| **Temperature** | {baseline.temperature} | {current.temperature} |\n"
        f"| **Valid cases** | {baseline.totals.cases_valid}/{baseline.totals.cases_total}"
        f" | {current.totals.cases_valid}/{current.totals.cases_total} |\n"
        f"| **Latency p50** | — | {current.totals.latency_p50_ms:.0f} ms |\n"
    )


def generate(
    diff: DiffResult,
    baseline: RunRecord,
    current: RunRecord,
    report_url: str | None = None,
) -> str:
    """Build the full PR comment as a markdown string."""
    sections = [
        f"## Eval Result: {_severity_badge(diff.severity)}",
        "",
        _meta_section(baseline, current),
        "---",
        "",
        _contract_section(diff),
        _scores_section(diff, baseline, current),
        _pitch_changes_section(diff),
        _regressed_cases_section(diff),
    ]

    # Decision details
    for line in diff.details:
        sections.append(f"> {line}")
    sections.append("")

    # Link to full report
    if report_url:
        sections.append(
            f"📄 [View full HTML report]({report_url}) — "
            f"side-by-side reasoning, per-case scores, raw payloads"
        )
    else:
        sections.append(
            "📄 Download the HTML report artifact for side-by-side reasoning details."
        )

    return "\n".join(sections)
