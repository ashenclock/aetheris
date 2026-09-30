# Aetheris interview notes

## 30-second version

Aetheris is a small local coding-agent runtime. The interesting part is the
control plane around the model: SQLite stores the task and message history,
Python enforces step and cost policies, tool calls keep their protocol IDs, and
the same task can resume after an interruption. The runtime also has approval
gates, a Markdown project wiki, an optional Jev watchdog, and an offline suite
that tests these behaviors without an API key.

## 60-second project overview

Aetheris started as a ReAct agent. I focused the long-horizon branch on the
failure modes that appear after the first successful tool call: growing context,
lost progress, tool errors, repeated loops, risky actions, and process
interruption. The model still proposes the next action, but Python remains
authoritative for task identity, budgets, counters, checkpoints, and pause
decisions. SQLite keeps the ordered history and state inspectable. Markdown is
enough for a first version of durable project knowledge because the corpus is
small and people can review it directly.

## Architecture walkthrough

```text
Goal
  |
  v
Agent.chat -> load state and bounded context
  |
  v
LiteLLM model -> final answer or assistant tool call with ID
  |
  v
Policy and approval -> allow, reject, or pause
  |
  v
Skill execution -> observation
  |
  v
SQLite checkpoint -> update state and append tool result
  |
  +----> Markdown wiki retrieval
  +----> optional Jev watchdog
```

Show the loop in `nexus/core/agent.py`, then show persistence in
`nexus/core/memory.py`. The important boundary is the checkpoint after a tool
interaction: the assistant call and its matching `tool` result use the same
`tool_call_id`, and the result is committed with the updated state.

## Core ReAct loop

1. `Agent.chat` records the user goal and loads or creates `TaskState`.
2. The request contains the system prompt, compact authoritative state, recent
   complete tool exchanges, and matching wiki excerpts.
3. LiteLLM returns either text or structured tool calls.
4. The runtime checks budgets, then applies the skill's approval requirement.
5. The skill returns an observation. The runtime records success or failure and
   checkpoints state plus the tool result.
6. The next model request sees the bounded context and the authoritative state.

## State model

`TaskState` is deliberately small. It contains the session ID, original goal,
status, step and cost limits, failure counters, last action/error, tool and
token counters, recovery and approval counters, and checkpoint count. The model
cannot change these values directly. The current statuses are `running`,
`paused`, `completed`, and `failed`.

Step limits always work. Cost limits work when the provider exposes a finite
LiteLLM cost estimate. A response can cross the configured cost budget before
the next checkpoint, so a provider-side spend limit would still be required for
a hard financial ceiling.

## Checkpoint and resume example

The short demo uses a seeded interrupted `read_file` call. On resume,
`SessionMemory.recover_pending_tool_calls` appends an explicit failed `tool`
message for the saved call ID, increments the recovery counters, checkpoints,
and lets the model reassess. A tool side effect may already have happened when
the process died, so the prototype does not promise exactly-once execution.

```bash
python evals/demo.py
python evals/run.py --task resume-interrupted-call
```

## Tool protocol

The persisted sequence is:

```text
assistant {tool_calls: [{id: "call-1", ...}]}
tool     {tool_call_id: "call-1", content: "..."}
assistant {next response}
```

A tool observation is never faked as a user message. The bounded-history
function drops incomplete old exchanges so the model receives valid pairs.

## Durable knowledge

`remember` writes a human-readable Markdown page under `.aetheris/wiki/`.
`recall` and the runtime use a small lexical scorer over page names and text.
That is a sensible V1 for a small local corpus: it is inspectable, has no
service dependency, and keeps the persistence model separate from transient
execution history. Embeddings would become useful after measuring a real recall
problem, not before.

## Jev's role

Jev is an optional secondary watchdog. It receives typed runtime facts and
returns a narrow `continue` or `pause` decision. Aetheris consults it after
failures and every fifth step, not after every trivial action. Import failure or
request failure falls back to `HeuristicPolicy`, which pauses after three
consecutive tool failures. Jev does not choose tools or replace the worker
model.

## Human approval and security

Approval is a user decision gate on `write_file`, `edit_file`, and
`run_command`. Policy decides when the runtime should pause. The skill decides
whether confirmation is required. Neither mechanism is a sandbox: shell
commands run with the current operating-system user's permissions and can
access the network. A production version needs OS or container isolation,
filesystem and network policy, identity-aware approvals, and audit logs.

## Offline evaluation

`evals/tasks.jsonl` covers symbol search, file reading, a deterministic edit
followed by a test, one transient failure, approval denial, interrupted-call
recovery, and remember/recall. The scripted model uses the real `Agent` loop and
real skills. It measures status, steps, tool calls, failures, recovery,
approval pauses, tokens, latency, and checkpoints. Mock cost is intentionally
`null`; these results test runtime control flow, not model quality.

The measured run on this checkout was 7/7 successful tasks, 12 tool calls, 5
tool failures, 2 recovery cases, 1 approval pause, 170 prompt tokens, and 85
completion tokens. Latency is machine-dependent.

## Main design trade-offs

| Choice | Why it fits this project | What it gives up |
| --- | --- | --- |
| SQLite over Redis/Postgres | Local transactions and easy inspection | One process and one local database file |
| Markdown over a vector database | Reviewable knowledge with no service | Lexical search misses semantic matches |
| Custom runtime over LangGraph | The control flow stays visible in a few Python files | Fewer built-in integrations and less distributed orchestration |
| Deterministic limits over model decisions | Numeric policy stays enforceable | The model can still choose poor actions within the limits |
| Approval over a sandbox | Simple explicit user gate for a prototype | It does not contain malicious or accidental commands |

## Failure modes and honest answers

- A model request error pauses the task and preserves the session for resume.
- A malformed model response pauses with a checkpoint. The next run needs a
  valid provider response.
- Unknown tools, invalid JSON arguments, command failures, and denied approvals
  become tool failures. Three consecutive failures pause the task.
- A max-step or known cost threshold pauses before more work. Unknown provider
  pricing cannot produce a hard cost guarantee.
- Missing task state or corrupt SQLite JSON is an understandable startup error,
  but there is no repair tool for arbitrary database corruption.
- A crash while a tool is executing is recoverable as an ambiguous interrupted
  call, not as proof that the external side effect did not happen.

## What I would change for production

I would isolate execution per task, move state and artifacts to a shared
transactional service, add provider-side spend limits, use idempotency keys for
side-effecting tools, add structured traces and approval identities, and build
model-backed evaluations from reviewed repositories. I would keep the explicit
runtime state machine even if a larger orchestration platform replaced the
SQLite implementation.

## Files to show

1. `nexus/core/agent.py` — point out the explicit model/tool/checkpoint loop,
   bounded context, and deterministic policy checks.
2. `nexus/core/state.py` — point out the authoritative counters and
   `should_pause` budget rule.
3. `nexus/core/memory.py` — point out atomic state plus tool-result
   persistence and interrupted-call recovery.
4. `nexus/core/policy.py` — point out the narrow Jev interface and deterministic
   fallback.
5. `evals/run.py` — point out that the evaluation drives the real runtime with
   scripted model responses and no external API.

## Likely interview questions

### 1. Why not LangGraph or another agent framework?

The goal is to make the runtime control flow inspectable. A framework becomes
worthwhile when the project needs branching workflows, distributed workers, or
many integrations. The current requirements fit a small explicit loop.

### 2. Why SQLite?

It provides transactional local persistence for one user, is easy to inspect,
and avoids operating another service. I would replace it with a shared database
when multiple workers or users need concurrent access.

### 3. What happens after a crash between tool request and result?

The assistant tool call is already persisted. Resume finds the unmatched call,
writes an explicit failed tool result with the same ID, updates counters, and
lets the model reassess. The side effect remains ambiguous.

### 4. How do you prevent infinite loops?

Python enforces `max_steps`, checks cost when available, and pauses after three
consecutive failures. Those limits do not depend on the model following its
instructions.

### 5. How do you bound context growth?

SQLite keeps the full history, while each model request gets the system prompt,
recent complete exchanges, clipped text, compact state, and a few lexical wiki
excerpts. Old history is not replayed forever.

### 6. How do you handle retries safely?

The prototype records failures and lets the model retry within the step limit.
For side-effecting production tools I would add idempotency keys, explicit
retry classes, and a tool-specific retry policy.

### 7. Does the cost budget guarantee no overspend?

No. It stops before the next action only when the provider reports a usable
estimate. One response can cross the threshold. A provider-side quota is needed
for a hard cap.

### 8. Is shell execution sandboxed?

No. Confirmation is an approval gate, not isolation. Shell commands run with
the current user's permissions. Production use needs a disposable container or
another isolated execution service.

### 9. Why use Markdown for memory?

The first corpus is small and project knowledge benefits from human review. A
lexical baseline is measurable and easy to replace later if semantic retrieval
proves necessary.

### 10. Why use Jev?

Only as an optional second opinion at selected checkpoints. It can recommend a
pause after an unusual or repeated failure, but the runtime remains usable when
Jev is missing or unavailable.

### 11. What does the offline evaluation prove?

It proves deterministic runtime behaviors on controlled tasks: tool protocol,
approval, retry, recovery, budgets, and knowledge persistence. It says nothing
about real-model coding quality or general task success.

### 12. How would you evaluate an agent in production?

Use reviewed repositories and task-specific assertions, then track success,
patch quality, tool errors, retries, cost, latency, unsafe-action rate, and
human takeover. Keep deterministic regression tasks beside model-based tests.

### 13. How would you scale to many concurrent tasks?

Move state to a shared database, enqueue task steps, run isolated workers, and
store artifacts by immutable task ID. The state transitions and tool protocol
would remain explicit.

### 14. What is idempotency here?

Repeating a step should not duplicate a side effect. Reads are naturally close
to idempotent; writes, installs, and git operations need operation keys or a
reconciliation step before retry.

### 15. How would you improve observability?

Emit structured events for model requests, tool calls, approvals, checkpoints,
budget decisions, and errors. Correlate them by session and tool-call ID, then
retain redacted inputs and outputs with access controls.

### 16. How would you handle malformed model output?

The runtime pauses and checkpoints the malformed-response error. A production
provider adapter could add schema validation and a bounded repair request, but
it should never silently interpret invalid tool arguments.

### 17. What is the biggest current security weakness?

Approved shell execution is still arbitrary local execution. The correct answer
is that Aetheris is not a sandbox and should run untrusted work in a separate
isolated environment.

### 18. Why keep the full history if it is not sent to the model?

It supports audit, recovery, and debugging. The model context is a derived
bounded view; the SQLite history is the durable record.

### 19. What happens if SQLite is corrupt?

Initialization or Pydantic validation fails visibly. The prototype does not
pretend to reconstruct arbitrary corruption; production needs backups,
migrations, integrity checks, and recovery procedures.

### 20. What would you simplify further?

I would keep the current explicit state, memory, policy, and skill boundaries.
I would avoid adding distributed orchestration or semantic memory until a
measured requirement justifies it.
