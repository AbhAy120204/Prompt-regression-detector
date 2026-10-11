"""Reason quality -- the advisory axis.

An LLM judge is not deterministic, so it must never fail a build. Its output is recorded
and charted so that a collapse in reasoning quality is visible to a human reviewer, and
that is the whole of its authority. This is the design decision to lead with: CI gates are
built only on signals that give the same answer twice.

One judge call per case, not per dimension -- 10 calls per run rather than 50, for a signal
that is advisory anyway.

Free text is never diffed. The judge reads both versions and says whether the reasoning
changed; the report shows them side by side and lets the reader decide. No character-level
or semantic diff is attempted anywhere in this project.
"""

from __future__ import annotations

import asyncio
import json
import re

from ..gemini import GeminiClient, GeminiError
from ..store import CaseRecord, RunRecord

JUDGE_SYSTEM_PROMPT = """\
You compare two sets of evaluator notes written about the same sales conversation.

Rate how closely the SECOND set's reasoning aligns with the FIRST, on this scale:
  5 = same substance and emphasis, wording aside
  4 = same conclusions, minor differences in what is emphasised
  3 = partially overlapping; some points appear or disappear
  2 = substantially different reasoning about the same conversation
  1 = unrelated, empty, or degenerate (e.g. collapsed to a single generic sentence)

Judge the reasoning only. Do not judge whether the scores are correct.

Respond with a single raw JSON object and nothing else:
{"alignment": <int 1-5>, "note": "<one sentence, max 200 chars>"}
"""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _build_user_prompt(baseline: CaseRecord, current: CaseRecord) -> str:
    base_reasons = baseline.scorecard.reasons()
    curr_reasons = current.scorecard.reasons()
    first = "\n".join(f"- {dim}: {text}" for dim, text in base_reasons.items())
    second = "\n".join(f"- {dim}: {text}" for dim, text in curr_reasons.items())
    return f"FIRST SET:\n{first}\n\nSECOND SET:\n{second}"


def _parse(raw: str) -> tuple[int | None, str | None]:
    match = _FENCE_RE.match(raw)
    payload_text = match.group(1) if match else raw.strip()
    try:
        payload = json.loads(payload_text)
        alignment = int(payload["alignment"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # The judge is advisory, so an unreadable verdict is simply absent. It is never
        # allowed to fail a run, and it is never silently invented either.
        return None, "judge response unreadable"
    if not 1 <= alignment <= 5:
        return None, f"judge returned out-of-range alignment: {alignment}"
    note = payload.get("note")
    return alignment, (str(note)[:200] if note else None)


async def _judge_one(
    client: GeminiClient,
    baseline: CaseRecord,
    current: CaseRecord,
    semaphore: asyncio.Semaphore,
) -> None:
    try:
        async with semaphore:
            raw, _usage = await client.generate(
                system_prompt=JUDGE_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(baseline, current),
                temperature=0.0,
            )
    except GeminiError as exc:
        current.judge_alignment = None
        current.judge_note = f"judge unavailable: {exc}"
        return

    current.judge_alignment, current.judge_note = _parse(raw)


async def attach_judgements(
    baseline: RunRecord,
    current: RunRecord,
    model: str | None = None,
    concurrency: int = 5,
) -> None:
    """Annotate `current` in place with alignment scores. Only paired valid cases are
    judged -- there is nothing to compare when either side has no scorecard."""
    base_by_id = {c.case_id: c for c in baseline.valid_cases()}
    pairs = [
        (base_by_id[c.case_id], c)
        for c in current.valid_cases()
        if c.case_id in base_by_id
    ]
    if not pairs:
        return

    client = GeminiClient(model=model)
    semaphore = asyncio.Semaphore(concurrency)
    await asyncio.gather(
        *(_judge_one(client, base, curr, semaphore) for base, curr in pairs)
    )
