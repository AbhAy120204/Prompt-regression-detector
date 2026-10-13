"""Command-line entry point.

Usage:
  python -m mrds run                            Run eval, compare against current baseline.
  python -m mrds run prompts/v2.yaml            Run eval with a specific prompt.
  python -m mrds run prompts/v2.yaml --promote  Run eval AND make this run the new baseline.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Windows: suppress the "Event loop is closed" noise from asyncio + proactor transport.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m mrds <command>")
        print()
        print("Commands:")
        print("  run [prompt_path] [--promote]    Run eval against baseline.")
        print("                                   --promote = make this run the new baseline.")
        print("  build-golden                     (legacy) Generate initial baseline.")
        sys.exit(1)

    command = sys.argv[1]

    if command == "build-golden":
        from .build_golden import main as run_build
        run_build()

    elif command == "run":
        from .diff import run_diff
        from .pr_comment import generate as generate_comment
        from .report import render
        from .runner import execute_run
        from .scoring.judge import attach_judgements
        from .store import BASELINE_PATH, load_baseline, promote_to_baseline, save_run
        from .feature import PROJECT_ROOT

        # Parse arguments
        args = sys.argv[2:]
        promote = "--promote" in args
        report_url_arg = None
        filtered = []
        for i, a in enumerate(args):
            if a == "--promote":
                continue
            if a == "--report-url" and i + 1 < len(args):
                report_url_arg = args[i + 1]
                continue
            if i > 0 and args[i - 1] == "--report-url":
                continue
            filtered.append(a)
        args = filtered

        prompt_path = Path(args[0]) if args else PROJECT_ROOT / "prompts" / "v1_baseline.yaml"

        # Check if a baseline exists. If not, this is the first run.
        has_baseline = BASELINE_PATH.exists()

        print(f"Prompt:   {prompt_path}")
        if has_baseline:
            bl = load_baseline()
            print(f"Baseline: {bl.prompt_version} ({bl.model})")
        else:
            print("Baseline: none — this will be the first baseline.")
        print()

        print("Running eval…")
        run = asyncio.run(execute_run(prompt_path=prompt_path))
        print(f"Valid: {run.totals.cases_valid}/{run.totals.cases_total}  "
              f"| p50 latency: {run.totals.latency_p50_ms:.0f} ms")

        run_path = save_run(run)
        print(f"Run saved → {run_path.name}")

        # If no baseline exists, this run becomes the baseline automatically.
        if not has_baseline:
            promote_to_baseline(run_path)
            print()
            print(f"{'─'*50}")
            print(f"First run. Promoted to baseline automatically.")
            print(f"Baseline → {BASELINE_PATH}")
            print(f"{'─'*50}")
            return

        baseline = load_baseline()

        print("Attaching judge scores (advisory)…")
        asyncio.run(attach_judgements(baseline, run))

        # Re-save with judge scores attached
        save_run(run)

        diff = run_diff(run, baseline)

        report_path = render(run, diff, baseline)

        # Build the full report URL if a base URL was provided.
        full_report_url = None
        if report_url_arg:
            full_report_url = f"{report_url_arg.rstrip('/')}/{report_path.name}"

        # Generate markdown comment for CI.
        comment_md = generate_comment(
            diff, baseline, run, report_url=full_report_url
        )
        comment_path = PROJECT_ROOT / "pr_comment.md"
        comment_path.write_text(comment_md, encoding="utf-8")

        print()
        print(f"{'─'*50}")
        print(f"Comparing: {run.prompt_version} vs {baseline.prompt_version}")
        print(f"Result:    {diff.severity}")
        for line in diff.details:
            print(f"           {line}")
        print(f"Report   → {report_path}")
        print(f"Comment  → {comment_path}")

        # Promote this run to baseline if requested and it passed.
        if promote:
            if diff.severity.value == "CRITICAL":
                print()
                print("Cannot promote: eval result is CRITICAL.")
                print("Fix the regression first, then try again.")
            else:
                promote_to_baseline(run_path)
                print()
                print(f"Promoted to new baseline.")
                print(f"Next eval will compare against: {run.prompt_version}")

        print(f"{'─'*50}")

        # Exit with code 1 on CRITICAL so CI can pick it up.
        if diff.severity.value == "CRITICAL":
            sys.exit(1)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
