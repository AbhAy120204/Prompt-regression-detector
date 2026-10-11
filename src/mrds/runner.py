"""The eval runner: fan out over every case, validate each result, assemble a RunRecord.

The runner's job stops at "what did the model say, and was it a valid ScoreCard". It makes
no pass/fail judgement -- that belongs to diff.py and thresholds. Keeping those separate is
what lets the same run be re-diffed against a different baseline later without re-calling
the API.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .contracts import Conversation, PromptConfig, utc_now
from .feature import load_conversations, load_prompt, score_conversation
from .gemini import GeminiClient
from .scoring.contract import validate
from .store import CaseRecord, RunRecord, build_totals, make_run_id

DEFAULT_CONCURRENCY = 5


async def _run_one(
    client: GeminiClient,
    prompt: PromptConfig,
    conversation: Conversation,
    semaphore: asyncio.Semaphore,
) -> CaseRecord:
    async with semaphore:
        output = await score_conversation(client, prompt, conversation)

    verdict = validate(conversation.case_id, output.raw_text)
    return CaseRecord(
        case_id=conversation.case_id,
        domain=conversation.domain,
        n_turns=conversation.n_turns,
        is_valid=verdict.is_valid,
        violations=[v.to_dict() for v in verdict.violations],
        scorecard=verdict.scorecard,
        raw_text=None if verdict.is_valid else output.raw_text,
        usage=output.usage,
        transport_error=output.transport_error,
    )


async def execute_run(
    prompt_path: str | Path,
    model: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    conversations: list[Conversation] | None = None,
) -> RunRecord:
    prompt = load_prompt(prompt_path)
    cases = conversations if conversations is not None else load_conversations()
    # Model comes from the prompt config. The prompt file IS the feature configuration.
    # Changing the model means editing the YAML, which triggers CI.
    client = GeminiClient(model=model or prompt.model)
    semaphore = asyncio.Semaphore(concurrency)

    results = await asyncio.gather(
        *(_run_one(client, prompt, conv, semaphore) for conv in cases)
    )
    # Preserve input order so two runs always list cases identically; gather already does
    # this, but asserting it here means a future change to the fan-out cannot silently
    # scramble the side-by-side report.
    by_id = {record.case_id: record for record in results}
    ordered = [by_id[conv.case_id] for conv in cases]

    return RunRecord(
        run_id=make_run_id(prompt.version, client.model),
        created_at=utc_now(),
        prompt_version=prompt.version,
        prompt_path=str(Path(prompt_path).as_posix()),
        model=client.model,
        temperature=prompt.temperature,
        cases=ordered,
        totals=build_totals(ordered),
    )
