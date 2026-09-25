# Aetheris

Aetheris is a small, local-first coding agent focused on three things: **tool use, resumability, and inspectable long-term memory**.

It deliberately avoids a large agent framework. The core loop is plain Python and LiteLLM, while durable state lives in SQLite and durable project knowledge lives in Markdown.

## Why this version is different

A short ReAct loop is easy to demo. Long-running agents fail for different reasons: context drift, repeated tool errors, runaway cost, and losing progress after interruption. Aetheris addresses those failure modes with a minimal runtime:

- **Durable task state** — goal, status, step count, failure count, and budgets are persisted.
- **Checkpoint after every tool step** — an interrupted session can be resumed without reconstructing the run from scratch.
- **Real tool messages** — tool calls and results use the standard assistant/tool protocol instead of being disguised as user messages.
- **Budget gates** — a run pauses when its step or cost budget is exhausted.
- **Human approval** — sensitive skills can require confirmation.
- **Markdown knowledge wiki** — `remember` and `recall` keep durable project facts separate from transient chat history.
- **Optional Jev watchdog** — Jev can make the narrow `continue` vs `pause-for-review` decision while the main LLM remains responsible for reasoning and generation.

## Architecture

```text
user goal
   |
   v
Agent loop -----> LiteLLM model
   |                  |
   |                  v
   |              tool call
   |                  |
   v                  v
TaskState <----- skill executor
   |                  |
   +---- checkpoint --+
   |
   +---- SQLite session history
   |
   +---- Markdown knowledge wiki
   |
   +---- policy gate (heuristic or optional Jev)
```

The design borrows the useful ideas from larger agent runtimes without copying their complexity: explicit state, action/observation history, resumability, safety gates, and knowledge that survives a single context window.

## Quick start

```bash
poetry install
poetry run python -m nexus.cli chat --model ollama/llama3
```

Run one autonomous task with explicit limits:

```bash
poetry run python -m nexus.cli run \
  "Inspect this repository and fix the failing tests" \
  --model ollama/llama3 \
  --session repair-tests \
  --max-steps 30 \
  --cost-budget 0.50
```

Resume later by reopening the same session in chat mode:

```text
/resume repair-tests
```

## Optional Jev policy gate

The default policy is deterministic and pauses after repeated failures. To use Jev as a separate decision layer:

```bash
poetry run pip install "typesafe-sdk>=0.7.1,<0.8"
export TYPESAFE_API_KEY=...
export AETHERIS_DECISION_POLICY=jev
```

Jev is intentionally not the worker model. It only answers the narrow question: should this run continue or pause for human review? To keep the control plane cheap, the policy is consulted after failures and periodically rather than after every successful tool call.

## Durable knowledge

The `remember` skill writes Markdown pages under `.aetheris/wiki/`. The `recall` skill searches those pages in later sessions. This keeps stable project knowledge readable by both humans and agents and avoids hiding all memory inside an embedding database. Generated wiki pages are ignored by Git by default to reduce the chance of committing private context.

## Project layout

```text
nexus/
  core/
    agent.py       # reasoning/action loop
    memory.py      # SQLite messages, state, checkpoints
    state.py       # typed task state
    policy.py      # heuristic and optional Jev gate
    knowledge.py   # Markdown long-term knowledge
    tracker.py     # token and cost accounting
  skills/          # dynamically discovered tools
  cli.py           # minimal interactive and one-shot CLI
```

## Design principles

- Keep deterministic control flow in code.
- Give the model tools, not hidden side effects.
- Persist enough state to recover from interruption.
- Separate transient conversation history from durable knowledge.
- Put hard limits around autonomous execution.
- Prefer a clean pause over uncontrolled retries.

## License

MIT. See `LICENSE`.
