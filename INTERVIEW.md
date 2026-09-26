# Aetheris interview notes

## 60-second overview

Aetheris is a small coding agent built around a ReAct loop. I focused this branch on what happens after the first tool call: the task state and message history are durable, every tool result is paired with its call ID, and a task can resume after an interruption. Python enforces budgets and approval gates. A Markdown wiki keeps reusable project decisions outside the transient conversation. The offline evaluation runs the real loop with scripted model responses, so I can test recovery and pause behavior without API keys.

## Walkthrough

1. `nexus/core/agent.py` builds the request from the system prompt, compact task state, recent history, and matching wiki pages.
2. The model returns either a final answer or a structured tool call.
3. A sensitive skill asks for approval. The runtime executes the skill, records success or failure, and checkpoints the state.
4. On resume, SQLite restores the same task. Any call that was saved without a result gets an explicit interruption result so the tool protocol remains valid.
5. `evals/run.py` exercises this control flow with deterministic fixtures and reports steps, calls, failures, token usage, latency, recovery, pauses, and checkpoints.

## Why these choices

- **SQLite:** local transactions make task state, message history, and checkpoints easy to inspect and restore. It fits a single-user prototype without introducing another service.
- **Bounded context:** the runtime keeps recent messages and complete tool exchanges, clips old text by a fixed rule, and adds compact authoritative state on every call. The full history stays in SQLite.
- **Markdown wiki:** project decisions are readable and editable without an embedding service. Retrieval is lexical; semantic search can be added behind the same concept if the notes grow.
- **Deterministic budgets:** Python owns step ceilings, tracked usage, approval, and status changes. A model cannot waive a numeric limit.
- **Jev:** optional watchdog only. The worker LLM still reasons and selects tools. Jev is consulted after failures and periodically, and the local heuristic remains the fallback.
- **Approval:** the approval prompt is a user gate. It does not sandbox shell execution; commands inherit the operating system user's permissions.

## Evaluation and limits

The seven offline scenarios cover search, read, edit plus test, retry after one controlled failure, denial and pause, interrupted-call recovery, and remember/recall. Responses are scripted; the suite validates control flow rather than model competence. Mock token counts are illustrative inputs, cost is unavailable in mock mode, and latency varies by machine.

SQLite is single-process and local. A production version would need an isolated execution environment, a shared durable store, stronger identity and audit controls, provider-side spend limits, retained artifact management, and model-backed evaluations with human-reviewed outcomes.

## Trade-offs and failure modes

| Topic | Choice or failure mode | Response |
| --- | --- | --- |
| Interrupted tool | The assistant call exists but the process stopped before the tool result was written | Resume records a failed interrupted result and asks the model to reassess |
| Transient provider error | The LLM request fails before an action | Pause the task and preserve its state for the same session ID |
| Repeated tool errors | The model keeps issuing failed actions | The deterministic watchdog pauses after three consecutive failures |
| Context growth | Old tool output accumulates in SQLite | Keep recent complete exchanges and bounded text in model requests |
| Unavailable cost estimate | The provider response has no known price | Mark cost as unavailable; keep enforcing the step budget |
| Local execution | Approval does not contain malicious shell commands | Run untrusted work in a real OS/container sandbox outside this prototype |

## Likely questions

**Why not use LangGraph or a larger agent framework?**  
The project is meant to make the control flow easy to inspect. The core needs are a loop, durable state, tool events, budgets, and a pause decision. A framework becomes useful when branching workflows or distributed execution justify its extra concepts.

**What happens if the process dies between a tool call and its result?**  
The assistant call has already been persisted. On resume, the runtime finds tool-call IDs without results, appends a tool message describing the interruption, updates failure counters, and checkpoints. That preserves protocol validity and avoids silently repeating an action whose completion is unknown.

**Does the cost budget guarantee a hard spend cap?**  
Only when LiteLLM can estimate the provider response price. The runtime records whether the estimate is available, and the README states that the step limit remains enforced when it is not. A real hard cap also needs a provider-side limit.

**Is shell execution safe?**  
It requires confirmation and has a timeout, but runs as the current operating-system user. That is an approval gate, not a sandbox. Production use needs a disposable container or another isolated execution service.

**What would you change for production?**  
Use isolated workers with per-task filesystem and network policies, shared transactional storage, durable artifact references, auditable approval identities, hard provider spend limits, and evaluation tasks based on reviewed repositories rather than scripted responses.
