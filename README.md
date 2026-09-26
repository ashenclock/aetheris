# Aetheris

Aetheris is a small, local-first coding agent. It started as a ReAct loop; this branch redesigns the runtime around the parts that fail on longer tasks: durable state, interruption recovery, bounded context, explicit approval, and repeatable evaluation.

The control flow stays in Python. LiteLLM supplies model calls, SQLite stores task history, and a Markdown wiki holds project knowledge that should survive a session.

## Runtime

```text
goal + recent context + relevant wiki pages
                   |
                   v
             LLM reasoning
                   |
                   v
       assistant tool call (typed ID)
                   |
                   v
          approved skill execution
                   |
                   v
      tool result + durable checkpoint
                   |
             next model step
```

The model chooses semantic actions. Python enforces step limits, records outcomes, checks estimated cost when the provider exposes it, pauses after repeated failures, requests approval for sensitive tools, and persists checkpoints. The model is never asked to decide whether a numeric limit has been exceeded.

## Long-running tasks

Each task has a durable state record: goal, status, step and cost budgets, consecutive failures, last action and error, token totals, tool failures, recovered interruptions, approval pauses, and checkpoint count.

- SQLite updates task state and checkpoint rows in one transaction.
- A tool call is stored as an assistant message. Its result is stored as a `tool` message with the matching `tool_call_id`.
- Tool outcomes and their state checkpoint are committed together. If the process stops while a tool is executing, resume writes an explicit interrupted result and lets the model reassess; a side effect may already have occurred, so exactly-once execution is not guaranteed.
- Transient model-request errors pause the task; resuming the same session keeps the original goal and usage totals.
- The request context keeps the system instruction, recent messages, and complete tool-call/result pairs. Old text is clipped deterministically; task state remains authoritative.
- Relevant wiki pages are retrieved lexically and added as short excerpts. Older run history remains in SQLite and is not all replayed to the model.

Run and resume with the same database and session ID:

```bash
python -m pip install -e .
python -m nexus.cli run "Inspect this repository and fix the failing tests" \
  --model ollama/llama3 --db aetheris_memory.db \
  --session repair-tests --max-steps 30 --cost-budget 0.50
```

The interactive CLI can reopen the same session with `/resume repair-tests`. Use `/status` to inspect the persisted state and run counters.

## Durable project knowledge

`remember` writes Markdown pages under `.aetheris/wiki/`; `recall` and the runtime's lexical retrieval read them in later tasks. The files are human-readable and generated knowledge is ignored by Git by default. SQLite is used for ordered events and checkpoints; Markdown is used for stable project facts and decisions. A vector database would add another service before this small corpus needs semantic retrieval.

## Policy and safety

The default watchdog pauses after three consecutive failed actions. It is consulted after failures and periodically, not after every successful tool call. Jev is an optional, narrow `continue` or `pause for review` signal. If Jev cannot be imported or called, the deterministic policy takes over. Jev does not generate code or replace the main model.

`write_file`, `edit_file`, and shell execution require an interactive approval. Approval is a user decision gate; it is not an OS sandbox. Shell commands run with the current process user's permissions. The repository does not claim protection against malicious commands. Use a disposable container or a separate OS account for untrusted repositories.

## Offline evaluation

The checked-in task set covers symbol search, file reading, a small edit followed by a unit test, one controlled tool failure and retry, an approval denial, recovery from an interrupted tool call, and durable knowledge recall.

```bash
python evals/run.py
python -m pytest -q
```

The runner uses a scripted local model and a test-only tool that fails once. It makes no API calls. It reports task success, steps, tool calls and failures, recovery, prompt/completion tokens, estimated cost when available, latency, review pauses, and checkpoint counts. Token usage is fixed by the mock responses; cost is intentionally `null`, and latency is machine-dependent. These checks demonstrate runtime behavior, not model quality or a benchmark score.

## Design choices

| Choice | Reason | Cost or limitation |
| --- | --- | --- |
| SQLite checkpoints | One local file, transactions, and enough durability for a single-user agent | Not a multi-worker or distributed store |
| Markdown wiki | Reviewable, editable, and easy to keep with project knowledge | Lexical retrieval misses semantic matches |
| Deterministic budgets | The runtime can enforce numeric limits without model interpretation | Cost limits apply only when LiteLLM can estimate provider cost; one model response can cross the threshold before the next tool is blocked; the step budget still applies |
| Optional Jev watchdog | Adds a narrow review signal without changing the worker model | Adds latency and a service dependency when enabled |

## Project layout

```text
nexus/core/agent.py       # ReAct loop, bounded context, policy checks
nexus/core/state.py      # Typed task state and counters
nexus/core/memory.py     # SQLite history, atomic checkpoints, recovery
nexus/core/knowledge.py  # Markdown wiki and lexical retrieval
nexus/core/policy.py     # Deterministic and optional Jev watchdog
nexus/skills/             # Small file, search, shell, and knowledge tools
evals/tasks.jsonl        # Reproducible offline scenarios
evals/run.py             # Scripted-model evaluation runner
tests/                   # Runtime, protocol, persistence, and policy tests
```

## Limitations

- Aetheris is a single-process, local-first prototype. SQLite and Markdown are not shared across workers.
- A hard cost ceiling cannot be guaranteed: providers may not expose a usable estimate, and one model response can cross the threshold before execution is paused. The step ceiling is enforced regardless.
- Approval does not isolate files, processes, or network access. There is no container sandbox.
- Offline evaluation checks control flow with scripted responses. It does not measure real-model task success, code quality, or comparative performance.
- A long run still depends on the model's ability to choose useful actions. Checkpoints preserve progress; they do not guarantee completion.

## License

MIT. See `LICENSE`.
