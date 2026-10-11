"""Numeric comparison against the baseline -- the thresholded axis.

Two rules govern everything here.

COUNTS, NOT PERCENTAGES. With 10 cases, "3 of 10 cases moved by 2 or more points" is a
statement you can defend. "30% degradation" implies a precision 10 samples cannot support.
Percentages appear nowhere in this module.

PAIRED COMPARISON ONLY. A case is compared only when BOTH the baseline and the current run
produced a valid ScoreCard for it. An unpaired case is excluded and counted, never treated
as a zero -- a missing value is not a score of zero, and averaging it in would mask the
contract violation that caused it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..contracts import DIMENSIONS
from ..store import RunRecord

COMPARED_FIELDS: tuple[str, ...] = (*DIMENSIONS, "overall_score")

# A case counts as "moved" at this delta or more. Set here rather than in config because
# it defines what the word means, not how strict the gate is.
MOVED_THRESHOLD = 2


class FieldDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    baseline_mean: float
    current_mean: float
    delta: float            # current - baseline; sign is meaningful
    mean_abs_delta: float   # per-case magnitude; what the gate reads
    exact_matches: int
    within_one: int
    moved: int              # cases that shifted by >= MOVED_THRESHOLD


class CaseComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    baseline_scores: dict[str, int]
    current_scores: dict[str, int]
    deltas: dict[str, int]
    baseline_pitch: str
    current_pitch: str
    pitch_changed: bool
    max_abs_delta: int
    direction: str  # "regressed" | "improved" | "unchanged"


class NumericComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases_compared: int
    cases_unpaired: list[str]     # present in baseline but not comparable now
    per_field: dict[str, FieldDelta]
    pitch_changes: list[tuple[str, str, str]]  # (case_id, from, to)
    cases: list[CaseComparison]

    def worst_mean_abs_delta(self) -> float:
        """The single number the numeric gate reads: the largest per-case average movement
        across all compared fields."""
        if not self.per_field:
            return 0.0
        return max(fd.mean_abs_delta for fd in self.per_field.values())


def compare(baseline: RunRecord, current: RunRecord) -> NumericComparison:
    base_by_id = {c.case_id: c for c in baseline.valid_cases()}
    curr_by_id = {c.case_id: c for c in current.valid_cases()}

    paired = [cid for cid in base_by_id if cid in curr_by_id]
    unpaired = sorted(cid for cid in base_by_id if cid not in curr_by_id)

    comparisons: list[CaseComparison] = []
    pitch_changes: list[tuple[str, str, str]] = []

    for case_id in sorted(paired):
        base_card = base_by_id[case_id].scorecard
        curr_card = curr_by_id[case_id].scorecard
        base_scores = base_card.scores()
        curr_scores = curr_card.scores()
        deltas = {f: curr_scores[f] - base_scores[f] for f in COMPARED_FIELDS}

        base_pitch = base_card.pitch_rating.value
        curr_pitch = curr_card.pitch_rating.value
        if base_pitch != curr_pitch:
            pitch_changes.append((case_id, base_pitch, curr_pitch))

        overall_delta = deltas["overall_score"]
        if overall_delta < 0:
            direction = "regressed"
        elif overall_delta > 0:
            direction = "improved"
        else:
            direction = "unchanged"

        comparisons.append(
            CaseComparison(
                case_id=case_id,
                baseline_scores=base_scores,
                current_scores=curr_scores,
                deltas=deltas,
                baseline_pitch=base_pitch,
                current_pitch=curr_pitch,
                pitch_changed=base_pitch != curr_pitch,
                max_abs_delta=max(abs(d) for d in deltas.values()),
                direction=direction,
            )
        )

    per_field: dict[str, FieldDelta] = {}
    for field in COMPARED_FIELDS:
        if not comparisons:
            per_field[field] = FieldDelta(
                field=field, baseline_mean=0.0, current_mean=0.0, delta=0.0,
                mean_abs_delta=0.0, exact_matches=0, within_one=0, moved=0,
            )
            continue
        base_vals = [c.baseline_scores[field] for c in comparisons]
        curr_vals = [c.current_scores[field] for c in comparisons]
        abs_deltas = [abs(c.deltas[field]) for c in comparisons]
        n = len(comparisons)
        per_field[field] = FieldDelta(
            field=field,
            baseline_mean=round(sum(base_vals) / n, 3),
            current_mean=round(sum(curr_vals) / n, 3),
            delta=round((sum(curr_vals) - sum(base_vals)) / n, 3),
            mean_abs_delta=round(sum(abs_deltas) / n, 3),
            exact_matches=sum(1 for d in abs_deltas if d == 0),
            within_one=sum(1 for d in abs_deltas if d <= 1),
            moved=sum(1 for d in abs_deltas if d >= MOVED_THRESHOLD),
        )

    return NumericComparison(
        cases_compared=len(comparisons),
        cases_unpaired=unpaired,
        per_field=per_field,
        pitch_changes=pitch_changes,
        cases=comparisons,
    )
