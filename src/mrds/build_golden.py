"""Build the initial baseline.

This script runs once to create the first data/baseline.json.
After that, the baseline updates automatically through the normal flow:
  1. Push a new prompt in a PR.
  2. Run: python -m mrds run prompts/v2.yaml --promote
  3. The new run becomes the baseline for the next version.

You should not need this script again after the first run.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Load .env before any other project imports
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from .contracts import utc_now
from .feature import load_conversations, load_prompt, PROJECT_ROOT
from .gemini import GeminiClient
from .runner import execute_run
from .store import BASELINE_PATH, save_run, promote_to_baseline


PROMPT_PATH = PROJECT_ROOT / "prompts" / "v1_baseline.yaml"


async def build() -> None:
    if BASELINE_PATH.exists():
        print(f"ERROR: {BASELINE_PATH} already exists.")
        print("A baseline already exists. Use 'python -m mrds run --promote' instead.")
        sys.exit(1)

    print("Loading conversations...")
    conversations = load_conversations()
    print(f"Found {len(conversations)} conversations.")
    print()

    print(f"Calling Gemini on all {len(conversations)} cases...")
    print()

    run = await execute_run(
        prompt_path=PROMPT_PATH,
        concurrency=3,
        conversations=conversations,
    )

    # Print a result for every case.
    print(f"{'CASE':<12} {'STATUS':<8} {'PITCH':>7} {'OVERALL':>8}  SCORES")
    print("-" * 70)
    for case in run.cases:
        if case.transport_error:
            print(f"{case.case_id:<12} {'ERROR':<8}  {case.transport_error[:40]}")
            continue

        if not case.is_valid:
            v = case.violations[0] if case.violations else None
            vtype = v["type"] if v else "?"
            print(f"{case.case_id:<12} {'INVALID':<8}  violation={vtype}")
            continue

        sc = case.scorecard
        scores = sc.scores()
        dims = " ".join(f"{scores[d]:2d}" for d in
                        ["persuasiveness","politeness","objection_handling",
                         "clarity","discovery_quality"])
        print(
            f"{case.case_id:<12} {'OK':<8} {sc.pitch_rating.value:>7}"
            f" {sc.overall_score:>7}  [{dims}]"
        )

    print()
    print(f"Valid:   {run.totals.cases_valid} / {run.totals.cases_total}")
    print(f"Invalid: {run.totals.cases_invalid}")
    print(f"Errors:  {run.totals.cases_errored}")
    print()

    if run.totals.cases_valid < run.totals.cases_total:
        print("WARNING: Some cases failed. Fix the prompt or model before saving.")
        answer = input("Save as baseline anyway? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted. Baseline was NOT written.")
            sys.exit(1)

    # Save the run, then promote it to baseline.
    run_path = save_run(run)
    promote_to_baseline(run_path)
    print(f"Baseline created → {BASELINE_PATH}")
    print("Next eval will compare against this run.")


def main() -> None:
    asyncio.run(build())


if __name__ == "__main__":
    main()
