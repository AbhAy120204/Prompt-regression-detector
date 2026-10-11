# Model Regression Detection System (MRDS)

A CI gate for LLM-powered features. It detects quality regressions when a team changes
a production prompt, swaps the model, or updates parameters — before those changes reach users.

---

## The problem

A team runs a prompt in production. The prompt, the model, and the parameters together
form the **feature configuration**. The feature works. Users are happy.

Then someone changes the prompt wording. Or the team upgrades from `gemini-flash` to
`gemini-pro`. Or they lower the temperature. The feature still runs. No errors in the logs.

But the outputs are quietly worse. Scores drift. Required JSON fields go missing.
The team finds out from user complaints, days later.

There is no gate that catches this before merge.

**MRDS is that gate.**

---

## How it works

The feature configuration (system prompt + model + parameters) runs against a fixed set
of inputs. The outputs are compared to a **baseline** — what the previous approved
configuration produced on the same inputs.

The baseline moves forward with the prompt. When v1 is approved, its outputs become
the baseline. When v2 is pushed in a PR, the eval compares v2's outputs against v1's.
When v2 merges, v2's outputs become the new baseline. v3 is compared against v2. And so on.

```
v1 approved  →  v1 outputs become the baseline
v2 pushed    →  eval compares v2 against v1
v2 merges    →  v2 outputs become the new baseline
v3 pushed    →  eval compares v3 against v2
```

The comparison catches two kinds of regression:

| Regression type | What it looks like | Gate behaviour |
|---|---|---|
| **Contract violation** | Model drops a required JSON field, returns wrong types, breaks the schema | **Hard block.** Any violation fails the build. |
| **Score drift** | Numeric outputs shift beyond thresholds | **WARN or CRITICAL** based on how far they moved. |

A third signal — reasoning quality — is tracked by an LLM judge. It is **advisory only**
and never blocks a merge. A non-deterministic signal cannot be a gate.

---

## Project structure

```
prompts/                Feature configurations (versioned YAML).
                        Changing these files triggers the CI gate.

data/
  conversations.json    Fixed eval inputs (the "test suite").
  baseline.json         Outputs from the last approved configuration.
                        Updated automatically when a prompt merges.

config/
  thresholds.yaml       WARN and CRITICAL limits.

runs/                   One JSON file per eval run (gitignored).
reports/                One HTML report per eval run (gitignored).
src/mrds/               Source code.
.github/workflows/      CI workflow.
```

---

## Setup

**1. Create a virtual environment and install:**
```bash
python -m venv myenv
myenv\Scripts\activate          # Windows
pip install -e .
pip install "pydantic==2.13.4" "google-genai==2.9.0"
```

**2. Add your API key:**
```
# .env
GEMINI_API_KEY=your_key_here
```

---

## Running an eval

```bash
# Compare a candidate prompt against the current baseline
python -m mrds run prompts/v2_candidate.yaml

# If it passed and you want it to become the new baseline
python -m mrds run prompts/v2_candidate.yaml --promote
```

This does four things:
1. Runs the feature configuration against all eval inputs.
2. Validates each output against the contract schema.
3. Compares scores to the baseline (previous version's outputs).
4. Writes an HTML report to `reports/`.

Exit code `0` = PASS or WARN. Exit code `1` = CRITICAL (merge blocked in CI).

The `--promote` flag makes this run the new baseline. Use it after you confirm
the change is intentional (a prompt improvement, not a regression).

In CI, promotion happens automatically when a PR merges.

---

## Catching regressions — two scenarios

**Scenario 1 — Schema break (contract violation):**

Someone edits the prompt and removes a scoring dimension from the JSON instructions.
The model stops returning that field.

→ CRITICAL. The report shows `MISSING_FIELD` violations, lists which cases broke,
and displays the raw payload so you can see exactly what went wrong.

**Scenario 2 — Score drift, schema intact:**

Someone tightens the rubric wording. The model scores more harshly across the board.
Every output is still valid JSON. But scores dropped by 2+ points.

→ WARN or CRITICAL depending on the magnitude. The report shows per-dimension deltas,
highlights regressed cases, and puts baseline vs current reasoning side by side.

**These two scenarios look different in the report on purpose.**
A schema break and a behaviour shift are different problems with different fixes.

---

## How to change thresholds

Edit `config/thresholds.yaml`. The comments explain each value.

The current thresholds are set above the empirical noise floor:
- We ran the same prompt 3 times at temperature 0.
- Temperature 0 means the model returns the same answer every time.
- Run-to-run variance was 0. Any non-zero delta is real signal.
- A 1-point buffer before WARN allows minor rephrasing without noise.

---

## Architecture decisions

**Why not use structured output mode?**

The Gemini SDK can enforce a response schema server-side. That would make malformed JSON
impossible. It would also silence the contract gate — the primary signal this system exists
to detect. We ask for JSON in the prompt and validate what comes back, keeping all six
violation types reachable.

**Why strict validation with no coercion?**

A model that returns `"8"` (string) where the contract says `8` (integer) has broken
the contract. Coercing it back hides the defect here while every downstream consumer
still crashes on it. A violating case is excluded from numeric scoring and shown in
the report.

**Why count violations, not average them?**

"2 of 10 cases dropped `objection_handling.reason`" tells you what to fix.
An average of that (0.2) tells you nothing.

**Why is the LLM judge advisory only?**

An LLM judge is not deterministic. Running it twice on the same input can give different
answers. A CI gate built on a non-deterministic signal would block merges randomly.
The judge output is shown in the report for human review. It never fails a build.

**Why plain JSON files instead of a database?**

At this scale, a database adds setup friction with no benefit. One JSON file per run is
readable, diffable in git, and trivially consumed by drift detection.

---

## Baseline provenance

The baseline was generated by running the feature configuration on a fixed set of
10 sales conversations. It represents what the feature produced under the last
approved configuration — the same role that production logs serve in a live system.

The baseline is Gemini-generated, not human-labelled. This makes MRDS a
**behavioural-change detector**, not an accuracy measure. Scores can be wrong but stable.
That is the correct tool for a CI gate: you are not asking "are these scores right?" but
"did this change make things worse?"
