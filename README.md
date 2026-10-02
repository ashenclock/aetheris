# Aetheris

A minimal, local-first coding-agent runtime where the important control flow
is understandable. Python owns limits, approvals and state; the model proposes
the next action. LiteLLM connects providers, SQLite records execution, and
Markdown stores durable project knowledge.

## Why this exists

Long tasks expose more than model quality: growing context, repeated actions,
tool failures, interruptions, lost progress, unsafe commands and uncontrolled
spend. Aetheris makes these boundaries explicit rather than building a generic
workflow framework.

## Start here

Install Python 3.12 or 3.13 and the optional web dependencies:

```bash
python -m pip install -e ".[web]"
aetheris chat --model deepseek/deepseek-chat --workspace . --session chat
```

Configure the provider key in the ignored `.env`, not in a prompt or Git.
`aetheris providers` reports configuration without displaying credentials.
An explicit model argument wins over `AETHERIS_MODEL`. Ollama requires a
separately installed model; check `ollama list` rather than assuming llama3 exists.
For a standalone user-level command, use `uv tool install .`.

Type a normal message to chat. Type `/` or `/help` for controls; terminal input
also autocompletes. Unknown slash commands never become model prompts.

- `/status`: authoritative state and budgets.
- `/resume`: list sessions; `/resume NAME`: continue/select one.
- `/wiki`: durable knowledge; `/trace`: tool IDs and checkpoints.
- `/permissions`: inspect mode; `/permissions session`: auto-approve ordinary
  tools for this chat; `/permissions ask`: restore individual approval.
- `/model PROVIDER/MODEL`: switch in a fresh session.
- `/new NAME`, `/provider`, `/skills`, `/mcp`, `/exit`: local controls.

See the short [hands-on guide](GUIDE.md) for one practical ML task.
All entry points and optional flags are discoverable with `aetheris --help`.

## Architecture and ReAct loop

```text
CLI / Streamlit -> load task -> bounded context + wiki excerpts
                                      |
                                      v
                                 model request
                                      |
                          final text or tool call
                                      |
                              policy / approval
                                      |
                               skill execution
                                      |
                         tool result + SQLite checkpoint
                                      |
                               continue / pause
```

ReAct is a paradigm: choose an action, use a tool, observe the outcome and
repeat. It is not a reliability guarantee. The model cannot authorize itself,
change numeric counters or erase a runtime budget.

| Location | Responsibility |
| --- | --- |
| nexus/chat.py | Shared slash-command controller; no model requests |
| nexus/cli.py, streamlit_app.py | Terminal and native Streamlit views |
| nexus/core/agent.py | Model/tool loop and recovery boundaries |
| nexus/core/state.py, memory.py | Authoritative counters, SQLite and session locks |
| nexus/core/context.py, knowledge.py | Bounded protocol context and lexical wiki retrieval |
| nexus/core/events.py, policy.py | Redacted activity and deterministic/optional watchdog |
| nexus/skills/ | Explicit side-effecting tools and typed arguments |
| nexus/web/ | GitHub/search connectors and the web runtime adapter |
| nexus/project.py, mcp.py | Local configuration and opt-in stdio MCP |
| evals/, tests/ | Scripted-runtime scenarios and focused regressions |

## State, protocol and resume

Task state includes the original goal, status, steps, usage, failure counters,
last action/error and checkpoints. State plus a tool outcome are committed in
one transaction. A tool observation is never a user message:

```text
assistant tool_call(id=call-1) -> tool result(tool_call_id=call-1) -> assistant
```

Resume uses the same database and session. For example, reopen chat with the
same `--db` and use `/resume repair-tests`. Increasing `--max-steps` extends the
saved allowance; cost is not silently replenished. One-shot run/plan/goal
preserve existing tasks by returning a fresh session ID; copy that ID for
status/resume. Interactive chat intentionally preserves conversation history.

A persisted web approval remains pending until explicitly approved/rejected.
An unmatched call after an execution crash gets an interrupted tool result;
the model reassesses. A side effect might already have happened: this is not
exactly-once execution. A local per-session lock rejects concurrent resumes;
SQLite is not a distributed worker store.

## Context and knowledge

SQLite retains history for recovery and audit. The request receives recent
complete tool exchanges, compact state and lexical Markdown matches—not the
entire historical transcript. The default serialized-message limit is 24,000
characters (`AETHERIS_CONTEXT_CHARS`, minimum 8,192), not a token-exact limit
and not a cap on provider tool schemas.

`remember` proposes sourced facts in `.aetheris/wiki/*.md`; `recall` retrieves
them. Writes require approval unless session approval was deliberately enabled.
`[[topic]]` links appear in the wiki graph. Facts are not automatically
verified or refreshed: the live pipeline test required correcting a stale
wiki claim. Markdown plus lexical search is a reviewable V1 for a small corpus;
embedding RAG would need evidence of retrieval misses first.

## Streamlit: two explicit modes

```bash
AETHERIS_WEB_LOCAL=1 streamlit run streamlit_app.py
```

Open http://localhost:8501 and choose **Open local workspace** for CLI-like
coding. Local mode loads `.env`; `AETHERIS_WEB_WORKSPACE` constrains folder
selection. It uses the workspace's aetheris_memory.db, so point the CLI's
`--db` at that same file when launching from another folder.

Without local mode, the UI downloads a bounded public GitHub archive and only
exposes inspect/list/read/search/recall. It is a snapshot, not clone/pull:
there is no Git metadata or local uncommitted work. Downloads reject traversal,
links and special files and cap compressed/extracted size and file counts.

Both modes show slash controls, activity, task state, a checkpoint timeline,
and the wiki graph. Activity is model-authored plans, tool requests/results and
provider waits—not private chain-of-thought. Approval buttons authorize one
pending call; they do not create a permanent permission grant.

## Safety and limits

Approval is a human gate, policy is a runtime decision, and a sandbox is OS
isolation. **Aetheris has no execution sandbox.** Approved shell commands run
with the process user's filesystem/network permissions. Sensitive reads and
MCP still require explicit approval in session-approval mode. A filename check
does not detect every secret; redaction is best-effort, not data-loss prevention.

- Steps are deterministically bounded. Repeated identical successful calls
  reuse the matching observation and consume a step; unrelated siblings
  continue. Successful mutations invalidate the recent-read cache.
- Three consecutive failures pause the task. Model errors and malformed output
  pause with checkpoints; arbitrary SQLite corruption is not repairable here.
- Cost is estimated when provider pricing is available. A response can itself
  cross the threshold; this is not a hard billing quota.
- Model requests default to 120 seconds and 4096 output tokens, configurable
  via AETHERIS_MODEL_TIMEOUT_SECONDS and AETHERIS_MAX_COMPLETION_TOKENS.
- Shell output is bounded while pipes are drained. POSIX pipelines use
  pipefail and timeouts terminate process groups; Windows descendant cleanup
  and equivalent pipeline semantics are not guaranteed.
- A final model response means **goal unverified**. Check artifacts/tests rather
  than interpreting exit code 0 or completed status as proof of task success.

## Optional adapters

Search uses bounded, untrusted snippets and explicit approval. DDGS defaults
to DuckDuckGo; Brave is selected explicitly, never just because a key exists.
The sidebar search does not silently insert results into model context.

Transcription uses an external Audio API, not a local model. It requires a
configured provider, valid audio and consent; optional adapter tests are not
evidence of a live transcription on this machine.

Project profiles/skills are reviewable Markdown guidance, not permissions.
Workspace MCP processes are disabled until AETHERIS_ENABLE_MCP=1; plan mode
does not start them. MCP support is local stdio/JSONL, not remote transport or
server isolation. The Responses adapter is selected with responses/MODEL.

Read-only sub-agents inherit the parent model unless explicitly configured,
cannot delegate recursively, and have a shared reservation/count budget.
Unknown pricing spends the reservation conservatively. Jev/compatible judges
are optional continue/pause watchdogs at selected checkpoints; deterministic
policy remains the fallback. Neither delegation nor a judge proves safety.

The HTTP API is single-tenant and read-only, with no remote approval queue.
Non-loopback binding requires a token. See deploy/n8n/ for the external adapter.

## Verify

```bash
python -m pytest -q
python evals/run.py
python evals/demo.py
```

Offline scenarios drive the real runtime with scripted models and explicit
artifact/observation assertions. They cover reading/search, edit-and-test,
transient failure, denied approval, interruption recovery, wiki and repeats.
Reported mock tokens are fixture values; cost is null. This is runtime
regression evidence, not model-quality benchmarking. Live provider checks
require the explicit `python evals/providers.py --live` command.
See [TESTING.md](TESTING.md) and [INTERVIEW.md](INTERVIEW.md) for limitations.

## Portability and deployment

Dockerfile packages the CLI; Dockerfile.streamlit packages the web view.
Build the latter with `docker build -f Dockerfile.streamlit -t aetheris-web:local .`.
Run privately with port 8501 and provider secrets injected at runtime.
Secrets, state and caches are excluded from the build context.

Docker packaging does not automatically isolate arbitrary coding tools.
Streamlit has no built-in authentication or aggregate spend quota: never
expose a paid provider key on an unauthenticated public app. Use private access,
TLS, rate limits and provider-side spending limits. Hosted snapshots are
temporary, not durable shared workspaces. Kubernetes needs one replica unless
state and workspace ownership are redesigned; see deploy/k3s/README.md.

## License

MIT. See LICENSE.
