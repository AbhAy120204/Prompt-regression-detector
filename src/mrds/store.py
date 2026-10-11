"""Run persistence: one JSON file per run under runs/.

There is no database. At 10 cases and a handful of runs a schema and a migration path
would be pure overhead, while plain JSON is readable, diffable in git, and trivially
consumed by the drift calculation (which just globs this directory in filename order).

Run ids are prefixed with a sortable UTC timestamp so lexical order is chronological order.

BASELINE FLOW
  data/baseline.json is the current approved baseline.
  When a new prompt is pushed in a PR, the eval compares against this file.
  When the PR merges and eval passed, the new run replaces baseline.json.
  So v2 is always compared against v1, v3 against v2, and so on.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from pydantic import BaseModel, ConfigDict

from .contracts import DIMENSIONS, ScoreCard, Usage
from .feature import PROJECT_ROOT

RUNS_DIR = PROJECT_ROOT / "runs"
BASELINE_PATH = PROJECT_ROOT / "data" / "baseline.json"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class CaseRecord(BaseModel):
    """One case's complete result: what came back, and whether it honoured the contract."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    domain: str
    n_turns: int
    is_valid: bool
    violations: list[dict[str, str]] = []
    scorecard: ScoreCard | None = None
    raw_text: str | None = None  # retained only for invalid cases
    usage: Usage
    transport_error: str | None = None
    judge_alignment: int | None = None  # advisory, filled in by scoring.judge
    judge_note: str | None = None


class RunTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases_total: int
    cases_valid: int
    cases_invalid: int
    cases_errored: int
    latency_p50_ms: float
    latency_p95_ms: float
    input_tokens: int
    output_tokens: int


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    prompt_version: str
    prompt_path: str
    model: str
    temperature: float
    cases: list[CaseRecord]
    totals: RunTotals

    def valid_cases(self) -> list[CaseRecord]:
        return [c for c in self.cases if c.is_valid and c.scorecard is not None]

    def mean_scores(self) -> dict[str, float]:
        """Mean of each numeric field over valid cases only. Empty when nothing is valid."""
        valid = self.valid_cases()
        if not valid:
            return {}
        fields = (*DIMENSIONS, "overall_score")
        return {
            field: round(sum(c.scorecard.scores()[field] for c in valid) / len(valid), 3)
            for field in fields
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered) + 0.5) - 1))
    return round(ordered[index], 1)


def build_totals(cases: list[CaseRecord]) -> RunTotals:
    latencies = [c.usage.latency_ms for c in cases if c.transport_error is None]
    return RunTotals(
        cases_total=len(cases),
        cases_valid=sum(1 for c in cases if c.is_valid),
        cases_invalid=sum(1 for c in cases if not c.is_valid and c.transport_error is None),
        cases_errored=sum(1 for c in cases if c.transport_error is not None),
        latency_p50_ms=round(median(latencies), 1) if latencies else 0.0,
        latency_p95_ms=_percentile(latencies, 95),
        input_tokens=sum(c.usage.input_tokens for c in cases),
        output_tokens=sum(c.usage.output_tokens for c in cases),
    )


def make_run_id(prompt_version: str, model: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _UNSAFE.sub("-", f"{prompt_version}_{model}").strip("-")
    return f"{stamp}_{slug}"


def save_run(record: RunRecord, runs_dir: Path | None = None) -> Path:
    target_dir = runs_dir or RUNS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{record.run_id}.json"
    path.write_text(
        json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_run(path: str | Path) -> RunRecord:
    return RunRecord.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_baseline(path: Path | None = None) -> RunRecord:
    """Load the current approved baseline.

    This is the run from the previous prompt version.
    When v2 is being tested, this file holds v1's run.
    When v3 is being tested, this file holds v2's run.
    """
    target = path or BASELINE_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"No baseline at {target}. "
            f"Run the first prompt version to create it: python -m mrds run --promote"
        )
    return load_run(target)


def promote_to_baseline(run_path: Path, baseline_path: Path | None = None) -> Path:
    """Copy a run file to become the new baseline.

    Call this after a prompt version merges and its eval passed.
    The old baseline is overwritten. The run file in runs/ stays untouched.
    """
    target = baseline_path or BASELINE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(run_path), str(target))
    return target
