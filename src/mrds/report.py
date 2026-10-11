"""Render the HTML diff report.

Takes a run, its diff result, and the baseline cases,
and writes a single self-contained HTML file to reports/.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .diff import DiffResult
from .store import RunRecord, load_baseline

TEMPLATES_DIR = Path(__file__).parent / "templates"
REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def _baseline_cases_dict(baseline: RunRecord) -> dict:
    """Return baseline valid cases as a plain dict keyed by case_id.
    The template reads scorecard fields from these, so we convert to plain dicts.
    """
    result = {}
    for case in baseline.valid_cases():
        sc = case.scorecard
        if sc is None:
            continue
        dim_data = {}
        for dim in ["persuasiveness","politeness","objection_handling","clarity","discovery_quality"]:
            dim_obj = getattr(sc, dim)
            dim_data[dim] = {"score": dim_obj.score, "reason": dim_obj.reason}
        result[case.case_id] = {
            "scorecard": dim_data,
            "pitch_rating": sc.pitch_rating.value,
            "overall_score": sc.overall_score,
        }
    return result


def render(
    run: RunRecord,
    diff: DiffResult,
    baseline: RunRecord | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Render the report and return the path to the written HTML file."""
    if baseline is None:
        baseline = load_baseline()

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )

    template = env.get_template("report.html")

    html = template.render(
        run=run,
        diff=diff,
        baseline_cases=_baseline_cases_dict(baseline),
    )

    out_dir = output_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run.run_id}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
