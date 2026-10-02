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

The CLI also exposes a safe plan-first workflow, project-local Markdown agent
profiles and skills, and approval-gated local MCP tools. These are thin product
surfaces around the same runtime, not a second orchestration engine.

## Architecture walkthrough

```text
Goal
  |
  v
CLI goal/plan/chat -> load state and bounded context
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
  +----> project profiles, skills, local MCP tools
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
status, step and cost limits, failure counters, last action/error, bounded
hashes of recent successful tool calls, tool/token counters, recovery and
approval counters, and checkpoint count. The model
cannot change these values directly. The current statuses are `running`,
`paused`, `completed`, and `failed`.

`completed` means the model returned a non-empty final response. The generic
runtime cannot prove that every user goal was achieved; task-specific tests,
artifact checks, or a human review provide that verification.
The CLI and Streamlit therefore label this state “model finished; goal
unverified” rather than presenting it as a verified success. JSON keeps the
raw `status` and adds a derived `status_label` for the same distinction.
One-shot commands preserve any existing transcript and create a fresh session
ID for the next task; only `resume` continues paused/running work, and it rejects
completed or missing sessions. A zero CLI exit means
the model returned a final answer, not that an external oracle verified it.

Step limits are deterministic. Cost limits use LiteLLM estimates when available:
if one request crosses the threshold, Aetheris records the response, blocks any
associated tool calls, and pauses. That already-issued request cannot be undone,
so this is a soft task budget, not a hard financial ceiling; provider-side spend
limits are still needed for that.

Shell output is drained while only a small bounded byte prefix is retained, so
large stdout/stderr cannot grow the runtime's captured output without limit.
This does not limit the command's CPU, disk, or network use.

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

## Native web search

`web_search` is an explicit skill, separate from `run_command`. It uses the
single-backend DDGS adapter (DuckDuckGo by default) or the Brave Search API only
when selected explicitly, asks for approval, applies optional domain filters,
and stores the result as a normal tool observation. Titles and snippets are
untrusted excerpts; they are not proof that a page was opened and cannot change
policy or trigger commands. A Brave key alone does not select that API; the
backend must be chosen explicitly or through operator configuration. DDGS
requests have an HTTP timeout, but coroutine cancellation is not thread
cancellation.
The Streamlit surface exposes the same operation behind a button but keeps it
out of the hosted repository-agent tool set.

## Jev's role

Jev is an optional secondary watchdog. It receives typed runtime facts and
returns a narrow `continue` or `pause` decision. Aetheris consults it after
failures and every fifth step, not after every trivial action. Import failure or
request failure falls back to `HeuristicPolicy`, which pauses after three
consecutive tool failures. Jev does not choose tools or replace the worker
model.

The adapter can point at a local Jev-compatible Kev server with
`AETHERIS_DECISION_BASE_URL` and `AETHERIS_DECISION_MODEL=kev-latest`. Kev is
only a secondary signal; it is not a sandbox or a security proof. The adapter
fails closed: low-confidence or ambiguous responses pause the run instead of
silently allowing it.

## Project profiles, skills, and MCP

`aetheris plan` exposes a read-only inspection mode. `aetheris goal` runs that
mode first by default and prints the execute command after the plan is saved.
Project-local `.aetheris/agents/*.md` files describe a specialist role, while
`.aetheris/skills/*/SKILL.md` files describe a bounded workflow. Both are
untrusted configuration: they cannot add tools or weaken runtime policy.

`aetheris mcp add` stores a local command in `.aetheris/mcp.json`; `mcp test`
actually starts it and performs MCP initialization and tool discovery. In
execute mode, discovered tools become explicit skills named
`mcp__server__tool` and require confirmation for every call. Normal agent
startup only discovers them when `AETHERIS_ENABLE_MCP=1` is explicitly set;
`mcp test` is the direct opt-in smoke command. Plan mode never starts external
MCP processes. This prevents merely opening an untrusted repository from
launching its configured server command; it is still not OS-level isolation.

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
recovery, remember/recall, and an identical-tool-call loop. The scripted model uses the real `Agent` loop and
real skills. It measures status, steps, tool calls, failures, recovery,
approval pauses, tokens, latency, and checkpoints. Mock cost is intentionally
`null`; these results test runtime control flow, not model quality.

The latest measured run was 8/8 successful evaluator cases, 12 tool calls, 3
controlled tool failures, 2 recovery cases, 1 approval pause, 180 prompt
tokens, and 90 completion tokens. The repeat case reuses the prior observation
without re-executing the tool. Latency is machine-dependent. A separate multiprocessing
test verifies same-session exclusion and lock release after process termination.

The latest local unit suite includes 194 passing tests (2026-10-02). In earlier
supervised live coding trials, DeepSeek looped until the repeated-tool
guard paused it after 43 steps (~$0.0223 estimated), Gemini returned HTTP 429,
and OpenRouter proposed code with a missing dependency and holdout leakage;
the risky writes were denied. None produced a verified application. Kev-0.8B
returned `continue` for a benign watchdog state and low-confidence `pause` for
an injected instruction paired with repeated failures. These are narrow smoke
checks, not evidence that the judge is robust to attacks. A final bounded
DeepSeek task in a disposable workspace added `divide()` and two tests; Aetheris
reported 3 passing tests in 5 steps, and an independent pytest rerun also
passed 3 tests. This demonstrates one small task, not general coding reliability.
The subsequent supervised DeepSeek pipeline trial generated a seeded sklearn
classifier, local MLflow metrics, a Streamlit viewer, and linked wiki pages.
An independent check passed 9 tests, including actual Streamlit AppTest rendering.
The first draft incorrectly called feature translation pure covariate shift;
the reviewer challenged that claim and the model corrected code, UI, docs, and
wiki. Review hit the 40-step limit, resumed with 50, and finished at 45 steps
(44 tool calls, one failed/skipped call, about $0.046 estimated for the saved
review task). A transient provider TLS error also paused and resumed. These are
specific observed cases, not a general reliability benchmark or full MLOps platform.
These examples show why task completion and factual correctness are separate
evaluation dimensions. Chat-completion and Responses requests default
to a 4096-token output cap (`AETHERIS_MAX_COMPLETION_TOKENS`); this is not a
total task or account spend limit.
Each provider request also has a 120-second default wall-clock timeout
(`AETHERIS_MODEL_TIMEOUT_SECONDS`); a timeout records a readable error and
checkpoints the task for resume. `aetheris run` exits with code 2 when the task
pauses, so automation can distinguish a pause from a final model response.
The 24,000-character context budget measures the serialized messages array,
including tool arguments and runtime state; it is not token-exact and excludes
provider-specific tool schemas. Recent user turns are prioritized, and tool-call
groups are retained with their results or omitted together.

## Main design trade-offs

| Choice | Why it fits this project | What it gives up |
| --- | --- | --- |
| SQLite over Redis/Postgres | Local transactions, easy inspection, same-session OS locking | One machine; not distributed state or coordination |
| Markdown over a vector database | Reviewable knowledge with no service | Lexical search misses semantic matches |
| Custom runtime over LangGraph | The control flow stays visible in a few Python files | Fewer built-in integrations and less distributed orchestration |
| Deterministic limits over model decisions | Numeric policy stays enforceable | The model can still choose poor actions within the limits |
| Approval over a sandbox | Simple explicit user gate for a prototype | It does not contain malicious or accidental commands |

## Failure modes and honest answers

- A model request error pauses the task and preserves the session for resume.
- A provider request is bounded by a configurable wall-clock timeout; this does
  not bound approved shell runtime, which has a separate tool timeout.
- On POSIX, shell timeouts terminate the command process group and are covered
  by a child-process regression test. Windows descendant cleanup is not yet
  guaranteed.
- A malformed or empty model response pauses with a checkpoint. The next run needs a
  valid provider response.
- Unknown tools, invalid JSON arguments, and command failures become recorded
  tool failures. A denied approval pauses immediately; sibling tool calls from
  the same model response are recorded as not executed.
- Tool calls must include unique provider IDs; malformed IDs pause before any
  requested action is executed.
- A max-step or known cost threshold pauses before more work. Unknown provider
  pricing cannot produce a hard cost guarantee.
- Missing task state or corrupt SQLite JSON is an understandable startup error,
  but there is no repair tool for arbitrary database corruption.
- An OS-level lock rejects concurrent resumes of the same session across
  processes on a shared local filesystem. It is not a distributed lock for
  network filesystems or replicas with separate disks; SQLite files with
  multiple hard links are refused to avoid lock aliases.
- A crash while a tool is executing is recoverable as an ambiguous interrupted
  call, not as proof that the external side effect did not happen.
- Cancellation during a model request or tool execution checkpoints a paused
  task. On resume, a pending tool result is recorded before the new user
  instruction, preserving the provider tool-call protocol. The effect of a
  canceled write/shell/MCP call can still be uncertain; inspect before retry.
- The HTTP API is single-tenant and read-only because it has no remote approval
  flow. Network binds require a bearer token. A shared token does not provide
  per-user session ownership or multi-tenant isolation.
- The hosted Streamlit UI has no authentication or aggregate spend quota; a
  paid provider key must not be exposed through an unauthenticated public app.
- `search_code` intentionally uses literal matching and skips hidden,
  generated, symlinked, and files larger than 1 MB; it returns at most 200
  matches. This avoids arbitrary regex backtracking and accidental traversal
  outside the workspace; an approved shell search remains available for more
  expressive queries.
- Built-in tool arguments are parsed against their Pydantic schemas at runtime
  rather than relying on the model to honor JSON-schema bounds. Invalid
  arguments are returned as tool failures before approval or side effects.

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
5. `nexus/cli.py` — point out plan-first goals, status output, and the explicit
   MCP/profile commands.
6. `evals/run.py` — point out that the evaluation drives the real runtime with
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
consecutive failures. It also pauses when the task repeats a successful
tool+arguments signature, including after resume. Failed calls remain retryable;
successful potentially mutating actions clear old signatures. This catches
identical no-progress loops, not alternating semantically equivalent calls or
every model planning failure. These controls do not depend on the model obeying
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

No. When a priced response crosses the threshold, its tool calls are blocked
and the task pauses, but that response was already incurred. A provider-side
quota is needed for a hard cap.

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

### 21. What does the MCP integration trust?

Only the local process connection and its returned data. Tool descriptions,
annotations, and server instructions are untrusted. Aetheris exposes each tool
explicitly, requires confirmation, and never treats MCP metadata as policy.

### 22. What does plan mode guarantee?

It removes mutating and shell skills and does not start configured MCP servers.
It produces a proposal and an evidence inventory. It does not prove that the
later execute phase will succeed.

## Full repository walkthrough

Show the worktree before opening the core loop:

```text
pyproject.toml / poetry.lock  package and reproducible dependency metadata
nexus/                         installable runtime package
  cli.py                       terminal entry points, plan/goal, profiles, MCP
  project.py / mcp.py          local config and the small MCP stdio adapter
  core/                        loop, state, SQLite memory, policy, prompts
  skills/                      explicit tools and approval boundaries
  web/                         bounded GitHub and search adapters
evals/                         deterministic scripted-model scenarios
tests/                         focused runtime and protocol tests
streamlit_app.py              read-only hosted demo and explicit search UI
Dockerfile                    portable CLI image
Dockerfile.streamlit          portable web image
.aetheris/                     local wiki, plans, profiles, MCP config (ignored)
README.md / INTERVIEW.md      user and interview documentation
```

The separation is intentional: `core` controls state and policy, `skills`
contain side effects, `evals` exercise the real runtime, and `web` is only an
adapter. The Streamlit surface uses the same `Agent`, not a second fake loop.

## Portable and hosted demo

The local distribution is intentionally small:

```bash
python -m pip install -e ".[web]"
streamlit run streamlit_app.py
docker build -f Dockerfile.streamlit -t aetheris-web:local .
```

The hosted demo streams validation of public GitHub archives with compressed,
entry-count, and expanded size/file-count limits, rejects traversal, links,
and special files, uses an isolated temporary workspace, and exposes only
read-only tools. It demonstrates the runtime and protocol without pretending
to sandbox arbitrary shell execution.

Do not publish it with paid provider secrets unless access control, rate
limits, and aggregate spend limits are supplied externally; its task budget is
not a per-visitor/global quota.
For Community Cloud, `requirements.txt` and `streamlit_app.py` are at the
repository root. Only add secrets after the app is protected by the access,
rate, and spend controls above.

The CLI has a small local stdio MCP adapter and an explicit `mcp test` command.
The hosted demo keeps using the direct GitHub connector so it does not spawn
arbitrary project processes or introduce a second credential boundary. Tool
descriptions and server instructions remain untrusted input. POSIX cleanup
terminates the MCP process group; Windows cleanup currently targets the direct
server process only.

## ReAct, sub-agents, and knowledge

ReAct is a paradigm for interleaving a model's next-action reasoning with tool
calls and observations. It is useful here because the control loop is explicit;
it is not itself a reliability guarantee. Aetheris adds the missing runtime
control: budgets, approval, persistence, protocol IDs, checkpoints, and pause
conditions.

The `delegate_task` skill is a deliberately small sub-agent path. The parent
can delegate one bounded read-only research question; the child has its own
session/checkpoints, cannot delegate again, cannot edit files, and returns a
report with evidence and unresolved risks. This helps decompose a long task,
but it also introduces cost, latency, duplicate context, and aggregation risk.
The honest production question is not “how many agents can I spawn?” but “what
independent work justifies another bounded context?”

Child sessions inherit the parent provider/model by default. The operator can
set `AETHERIS_SUBAGENT_MODEL` to a smaller local model, with a shared
`AETHERIS_SUBAGENT_BUDGET_USD`, `AETHERIS_MAX_SUBAGENTS`, and a shorter child
context. The parent records child token/cost/failure counters. This is a
deterministic routing choice, not an automatic provider switch: silently
falling back can change privacy, quality, and billing semantics.

The Markdown wiki is an LLM-assisted knowledge base: the model proposes a
fact, source, and optional `[[page-topic]]` links; SQLite remains the execution
record. `aetheris wiki --graph` gives a lightweight Obsidian-like view. The
current lexical retrieval is appropriate for a small repository. I would add
embedding RAG only after measuring semantic misses or corpus growth, and would
keep provenance, freshness, and human review even after adding embeddings.

## API, n8n, and transcription

`aetheris serve` is a small authenticated HTTP adapter for webhooks and n8n.
The default mode is read-only and the Docker Compose example puts n8n and the
adapter on an internal network, keeps API state on a separate volume, and
mounts the workspace read-only. n8n owns triggers and external integrations;
Aetheris owns the agent loop and runtime state. This is simpler to defend than
embedding an orchestration engine inside the core.

The transcription command uses a hosted Audio API when explicitly configured;
it is optional, bounded to the documented file size, and never takes the API
key as a CLI argument. Audio privacy and consent are part of the design, not a
footnote.

For Codex-compatible API models, the runtime has an explicit Responses adapter
selected with `responses/<model-id>`. It normalizes Responses function calls
into the same internal tool-call/result protocol. This path is tested with a
fake Responses client but not with a live request on this machine because no
OpenAI API key is configured. That distinction is important: adapter coverage
is not model-quality evidence.

## Small local models: measured boundary

`AETHERIS_TOOL_PROFILE=local-coding` narrows the default tool set to directory,
read/search, edit/write, and command tools. It also states the workspace root,
requests relative paths, and discourages package installation unless asked.
This reduces irrelevant schemas; it is neither a sandbox nor a reliability
guarantee. On this Mac, Qwen 2.5 0.5B returned malformed tool-list text and the
runtime marked the turn completed despite zero tool calls or artifacts. Qwen
2.5 1.5B proposed an install against instructions, then repeated invalid
edits. Qwen2.5-Coder 3B on an isolated copy read the requested files, reread one
file, then returned an empty tool-call payload without adding the requested test
or running the test command. Human approval stopped unsafe 1.5B actions; the
3B run exposed a no-op completion. These results are why small local
models should be treated as an experimental lane, with acceptance checks and
human review—not an automatic fallback for important coding tasks.

A fresh Qwen2.5-Coder 3B ML-pipeline task repeated pytest before creating test
or source files, then paused after four tool failures across its initial run
and resumes. The disposable workspace contains only a README stub. This does
not support claiming that Aetheris can autonomously scaffold or validate an ML
project with this local model.

A separate CLI task asked the same 3B model to inspect the synthetic drift
pipeline. It reread a successful file and emitted a function-call payload as
plain text; before the repeated-call guard, Aetheris marked that run completed
without an answer. With the guard, the same pattern paused after step 3. On
resume, the persisted signature blocked another reread, but the model then
invented an unavailable `output` tool and paused after three consecutive
failures. This demonstrates improved loop containment, not a successful code
review or a model-quality improvement.

## Failure cases to show

Use the deterministic evaluator to show one transient tool failure, an
approval pause, a process interruption with pending tool-call recovery, and
the max-step boundary. Then show `aetheris status` and `aetheris wiki --graph`.
For the n8n path, show an invalid bearer token and a read-only task. Explain
that model errors pause, malformed responses pause, SQLite corruption is not
magically repaired, MCP discovery can fail without taking down the core runtime,
and an interrupted side effect is ambiguous rather than exactly-once. Also show
that hostile repository text cannot override the system prompt or bypass
approval.
