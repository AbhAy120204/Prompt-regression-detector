"""Contract adherence -- the hard gate.

This is the only scorer whose verdict can fail a build, because it is the only one that is
fully deterministic: either the payload satisfies `ScoreCard` or it does not.

WHY THERE IS NO COERCION
A model that returns `"8"` where the contract says `8` has broken the contract. Coercing
that back to an int here would make the eval harness the one place in the system where the
defect is invisible -- while every downstream consumer of this output still crashes on it.
So a violating case is recorded, excluded from numeric scoring, and reported verbatim.

Stripping a ```json markdown fence is NOT coercion. The fence is transport packaging around
the payload, not part of the payload. Everything inside the fence is validated untouched.
"""

from __future__ import annotations

import json
import re
from enum import Enum

from pydantic import ValidationError

from ..contracts import ScoreCard

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class ViolationType(str, Enum):
    UNPARSEABLE = "UNPARSEABLE"
    MISSING_FIELD = "MISSING_FIELD"
    WRONG_TYPE = "WRONG_TYPE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    BAD_ENUM = "BAD_ENUM"
    EXTRA_FIELD = "EXTRA_FIELD"


# Pydantic v2 error codes -> our taxonomy. Anything unmapped is reported as WRONG_TYPE,
# which is the conservative default: it fails the case rather than passing it silently.
_ERROR_MAP: dict[str, ViolationType] = {
    "missing": ViolationType.MISSING_FIELD,
    "extra_forbidden": ViolationType.EXTRA_FIELD,
    "enum": ViolationType.BAD_ENUM,
    "greater_than_equal": ViolationType.OUT_OF_RANGE,
    "less_than_equal": ViolationType.OUT_OF_RANGE,
    "greater_than": ViolationType.OUT_OF_RANGE,
    "less_than": ViolationType.OUT_OF_RANGE,
    "string_too_short": ViolationType.OUT_OF_RANGE,
    "string_too_long": ViolationType.OUT_OF_RANGE,
}


class Violation:
    __slots__ = ("type", "field_path", "detail")

    def __init__(self, type: ViolationType, field_path: str, detail: str) -> None:
        self.type = type
        self.field_path = field_path
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type.value, "field_path": self.field_path, "detail": self.detail}

    def __repr__(self) -> str:
        return f"<{self.type.value} at {self.field_path or '<root>'}: {self.detail}>"


class ContractVerdict:
    """Either a valid ScoreCard, or the list of reasons it is not one. Never both."""

    __slots__ = ("case_id", "scorecard", "violations", "raw_text")

    def __init__(
        self,
        case_id: str,
        scorecard: ScoreCard | None,
        violations: list[Violation],
        raw_text: str,
    ) -> None:
        self.case_id = case_id
        self.scorecard = scorecard
        self.violations = violations
        self.raw_text = raw_text

    @property
    def is_valid(self) -> bool:
        return self.scorecard is not None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "is_valid": self.is_valid,
            "violations": [v.to_dict() for v in self.violations],
            "scorecard": self.scorecard.model_dump(mode="json") if self.scorecard else None,
            # Raw text is kept only when something went wrong -- that is when it is worth
            # reading, and keeping it always would bloat every run file.
            "raw_text": None if self.is_valid else self.raw_text,
        }


def _unwrap_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text.strip()


def _field_path(loc: tuple) -> str:
    return ".".join(str(part) for part in loc)


def validate(case_id: str, raw_text: str) -> ContractVerdict:
    """Validate one model output against the contract. Pure and deterministic."""
    candidate = _unwrap_fence(raw_text)

    if not candidate:
        return ContractVerdict(
            case_id,
            None,
            [Violation(ViolationType.UNPARSEABLE, "", "empty response")],
            raw_text,
        )

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return ContractVerdict(
            case_id,
            None,
            [Violation(ViolationType.UNPARSEABLE, "", f"invalid JSON: {exc.msg} at pos {exc.pos}")],
            raw_text,
        )

    if not isinstance(payload, dict):
        return ContractVerdict(
            case_id,
            None,
            [Violation(
                ViolationType.UNPARSEABLE, "",
                f"expected a JSON object, got {type(payload).__name__}",
            )],
            raw_text,
        )

    try:
        return ContractVerdict(case_id, ScoreCard.model_validate(payload), [], raw_text)
    except ValidationError as exc:
        violations = [
            Violation(
                _ERROR_MAP.get(err["type"], ViolationType.WRONG_TYPE),
                _field_path(err["loc"]),
                err["msg"],
            )
            for err in exc.errors()
        ]
        return ContractVerdict(case_id, None, violations, raw_text)
