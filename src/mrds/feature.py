"""The feature under test, plus loading of the static inputs.

`score_conversation` is the single call the whole eval pipeline exercises. It returns the
model's raw text untouched -- scoring happens downstream so that the feature itself has no
opinion about whether its own output is valid.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .contracts import CaseOutput, Conversation, PromptConfig, Usage
from .gemini import GeminiClient, GeminiError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONVERSATIONS_PATH = PROJECT_ROOT / "data" / "conversations.json"


def load_conversations(path: Path | None = None) -> list[Conversation]:
    raw = json.loads((path or CONVERSATIONS_PATH).read_text(encoding="utf-8"))
    return [Conversation.model_validate(item) for item in raw]


def load_prompt(path: str | Path) -> PromptConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return PromptConfig.model_validate(data)


async def score_conversation(
    client: GeminiClient,
    prompt: PromptConfig,
    conversation: Conversation,
) -> CaseOutput:
    """Run one conversation through the scorer.

    A transport failure is recorded on the CaseOutput rather than raised, so one dead API
    call cannot abort a whole run -- the run reports it as an errored case instead.
    """
    try:
        raw_text, usage = await client.generate(
            system_prompt=prompt.system_prompt,
            user_prompt=conversation.as_transcript(),
            temperature=prompt.temperature,
        )
    except GeminiError as exc:
        return CaseOutput(
            case_id=conversation.case_id,
            raw_text="",
            usage=Usage(latency_ms=0.0),
            transport_error=str(exc),
        )

    return CaseOutput(case_id=conversation.case_id, raw_text=raw_text, usage=usage)
