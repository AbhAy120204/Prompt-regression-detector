"""The output contract.

This module is the single source of schema truth. The rubric prompt's JSON instructions,
the contract validator, and the report all derive from the models here. If the contract
changes, it changes here first.

Validation is deliberately strict: `strict=True` means a model returning `"8"` instead of
`8` is a WRONG_TYPE violation, not a value to be quietly coerced. See
`mrds.scoring.contract` for why.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# The five rubric dimensions, in report display order. Everything that iterates dimensions
# reads this tuple rather than hardcoding names.
DIMENSIONS: tuple[str, ...] = (
    "persuasiveness",
    "politeness",
    "objection_handling",
    "clarity",
    "discovery_quality",
)

SCORE_MIN = 1
SCORE_MAX = 10


class PitchRating(str, Enum):
    """The categorical verdict. A model that invents a label outside this set is a
    BAD_ENUM violation -- exactly the failure mode a provider swap tends to introduce."""

    POOR = "poor"
    BAD = "bad"
    AVERAGE = "average"
    GOOD = "good"
    GREAT = "great"


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    score: int = Field(ge=SCORE_MIN, le=SCORE_MAX)
    reason: str = Field(min_length=10, max_length=400)


class ScoreCard(BaseModel):
    """What the feature under test must return for one conversation.

    7 required fields, 6 numeric values, 1 enum. That surface is what regressions show up on.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    persuasiveness: DimensionScore
    politeness: DimensionScore
    objection_handling: DimensionScore
    clarity: DimensionScore
    discovery_quality: DimensionScore
    # strict=False is required here and nowhere else: under model-wide strict mode pydantic
    # demands an actual PitchRating instance and rejects the plain string "good", which
    # would make every valid payload a false violation. Relaxing it to value lookup keeps
    # "good" valid and still reports "excellent" as BAD_ENUM.
    pitch_rating: PitchRating = Field(strict=False)
    overall_score: int = Field(ge=SCORE_MIN, le=SCORE_MAX)

    def scores(self) -> dict[str, int]:
        """The numeric values that get diffed, flattened."""
        flat = {dim: getattr(self, dim).score for dim in DIMENSIONS}
        flat["overall_score"] = self.overall_score
        return flat

    def reasons(self) -> dict[str, str]:
        """The free-text values that are never diffed, only shown side by side."""
        return {dim: getattr(self, dim).reason for dim in DIMENSIONS}


# --- Inputs -----------------------------------------------------------------------------


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str  # "customer" | "salesman"
    text: str


class Conversation(BaseModel):
    """One test case input. Loaded from the static data/conversations.json."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    domain: str
    n_turns: int
    source_row: int
    turns: list[Turn]

    def as_transcript(self) -> str:
        """Render for the user prompt. Stable formatting matters: a change here would
        look like a model regression."""
        return "\n".join(f"{t.role.capitalize()}: {t.text}" for t in self.turns)


class PromptConfig(BaseModel):
    """A versioned rubric prompt -- the artifact under CI."""

    model_config = ConfigDict(extra="forbid")

    version: str
    created_at: str
    description: str
    system_prompt: str
    model: str = "gemini-2.5-flash"
    temperature: float = 0.0


# --- Run records ------------------------------------------------------------------------


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0


class CaseOutput(BaseModel):
    """The raw result of one API call, before any scoring. `raw_text` is always kept so a
    contract violation can be shown verbatim in the report."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    raw_text: str
    usage: Usage
    transport_error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
