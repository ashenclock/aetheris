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
aetheris run "Inspect this repository and fix the failing tests" \
  --model ollama/llama3 --db aetheris_memory.db \
  --session repair-tests --max-steps 30 --cost-budget 0.50
```

For a user-level command, install the project as a tool:

```bash
uv tool install .
aetheris --help
```

The editable install is useful for development. `uv tool install .` is the
smallest npm-like workflow for a standalone local command.

For hands-on experiments that create a web project, inspect SQLite checkpoints,
resume a paused task, or build a local MLflow pipeline, see
[`GUIDE.md`](GUIDE.md).

The same command can be written as:

```bash
python -m nexus.cli run "Inspect this repository and fix the failing tests" \
  --model ollama/llama3 --db aetheris_memory.db \
  --session repair-tests --max-steps 30 --cost-budget 0.50
```

The interactive CLI can reopen the same session with `/resume repair-tests`. Use `/status` to inspect the persisted state and run counters.

## Streamlit GitHub demo

The repository also includes a small hosted-demo surface in
`streamlit_app.py`. It downloads a bounded archive of a public GitHub
repository into a temporary workspace, starts Aetheris with the same runtime,
and exposes only read-only tools (`list_directory`, `read_file`, `search_code`,
and `recall`). The UI shows the answer, authoritative task state, checkpoint
counters, and the persisted tool protocol. It does not expose shell, file
writes, edits, or Git pushes.

Install and run it locally:

```bash
python -m pip install -e ".[web]"
streamlit run streamlit_app.py
```

The sidebar also runs the deterministic offline reliability demo without an
API key. For live analysis, configure the provider key outside Git and set
`AETHERIS_MODEL` if the default model is not available:

```bash
export OPENAI_API_KEY="..."
export AETHERIS_MODEL="openai/gpt-4o-mini"
```

Supported LiteLLM provider examples are `openai/gpt-4o-mini`,
`deepseek/deepseek-chat`, `gemini/gemini-2.5-flash`,
`openrouter/openai/gpt-4o-mini`, `anthropic/claude-3-5-haiku-latest`, and
`ollama/llama3`. Run `aetheris providers` to inspect which credentials are
present locally. Discovery is offline and never selects a provider silently.
For bounded live smoke tests, use `python evals/providers.py --live`; without
`--live` the command only prints the test plan. Missing keys are reported as
skipped rather than treated as successful tests.

For Streamlit Community Cloud, deploy `streamlit_app.py` from the repository's
GitHub branch, keep `requirements.txt` at the repository root, and add the
provider key in Streamlit Secrets. Never commit `.streamlit/secrets.toml`.

This demo uses a direct GitHub archive connector rather than a local MCP
process. Aetheris can connect to project-local stdio MCP servers for the CLI,
but the hosted read-only surface keeps that process and credential boundary
out of a public Streamlit deployment.

## Native web search

`web_search` is a separate, approval-gated skill; it is not a shell shortcut
around `curl`. It returns at most eight title/URL/snippet records, supports
domain filters, and marks the result as external untrusted data. It does not
claim that a snippet was verified or execute instructions found in one.

The default backend is the optional `ddgs` package and needs no API key. Set
`AETHERIS_SEARCH_BACKEND` to select a DDGS backend, or set
`BRAVE_SEARCH_API_KEY` to use the Brave Search API instead. The direct CLI
command is useful for a transparent demo:

```bash
uv sync --extra search
aetheris search "Python asyncio documentation" --domain python.org --max-results 3
```

The command asks for approval before making the external request. The
Streamlit demo exposes the same bounded search behind an explicit button, but
keeps it separate from the hosted read-only repository agent so search queries
and external snippets do not silently enter the model context.

## API transcription and CLI surface

The optional transcription path uses the hosted OpenAI Audio API; it does not
require a local speech model. The API path accepts bounded audio files and the
CLI never accepts a key as an argument:

```bash
python -m pip install -e ".[transcription]"
export OPENAI_API_KEY="..."
aetheris transcribe interview.wav --model gpt-transcribe --language en \
  --output interview.txt
```

The same capability is available in the Streamlit sidebar. Treat uploaded
audio as sensitive: do not send participant or private interview recordings to
an external provider without authorization, retention review, and consent.

Useful CLI commands are:

```bash
aetheris doctor --workspace .
aetheris providers
aetheris providers --json
aetheris search "Python asyncio documentation" --domain python.org
aetheris chat --workspace . --session repair-tests
aetheris run "Inspect the test layout" --json
aetheris status --db aetheris_memory.db --session repair-tests
aetheris skills
aetheris wiki --workspace . --graph
aetheris serve --read-only --api-token "$AETHERIS_API_TOKEN"
aetheris plan "Review the repository and propose a safe change" --workspace .
aetheris goal "Repair the failing tests" --plan-first --workspace .
aetheris skill create reviewer --workspace .
aetheris agent create security --workspace .
aetheris mcp add local-demo --command "python server.py" --workspace .
aetheris mcp test --workspace .
```

`plan` uses a read-only tool set and saves a proposal with `--output`.
`goal` defaults to plan-first and prints the execute command after the plan.
Project agents and skills are Markdown configuration, so they remain reviewable
and cannot bypass runtime approvals. `mcp test` performs a real initialize and
tools/list exchange. Discovered MCP tools remain approval-gated, and plan mode
does not start MCP servers.

For an OpenAI Codex-compatible Responses model, use the explicit adapter
prefix, for example `AETHERIS_MODEL=responses/codex-mini-latest`. The adapter
translates the persisted tool-call protocol into Responses function-call items
and normalizes the response back into Aetheris's runtime format. A live call
still requires an OpenAI API key and provider access; the local offline suite
does not claim to test model quality.

`delegate_task` is a bounded read-only sub-agent skill. It is intentionally
limited to one child level, a small step budget, and repository inspection. A
child report is an observation in the parent loop, not an independent source
of authority. This makes delegation useful for long-horizon decomposition
without hiding the control flow in an autonomous swarm.

Child sessions use the same provider/model as the parent by default. For a
cheap local research lane, set `AETHERIS_SUBAGENT_MODEL` in `.env`, for example
`ollama/llama3.2:3b`. `AETHERIS_SUBAGENT_BUDGET_USD` is a conservative total
reservation shared by children, and `AETHERIS_MAX_SUBAGENTS` limits the number
of child sessions. If a provider does not report cost, the reservation is
treated as spent instead of silently allowing more calls. Parent state exposes
child sessions, failures, token counts, and estimated cost separately.

## Memory, wiki, and retrieval

The runtime separates three things:

1. SQLite history and checkpoints are the execution record.
2. `.aetheris/wiki/*.md` is a human-readable knowledge base for durable facts,
   decisions, and conventions.
3. The model context is a bounded derived view made from recent protocol-valid
   messages plus lexical wiki matches.

The `remember` tool can record an evidence source and `[[page-topic]]` links.
It is an approval-gated curated-memory write: the model can propose an update,
but a human confirms it before the Markdown page changes.
`aetheris wiki --graph` and the Streamlit knowledge panel render those links in
an Obsidian-like view. This is an LLM-assisted wiki, not an LLM-owned truth
store: the model proposes content, while a human can inspect and edit the
Markdown. A lexical baseline is the right V1 for a small corpus. Embedding RAG
would make sense after measuring missed semantic matches, a growing corpus, or
cross-repository retrieval; it would not fix bad provenance or stale facts.

ReAct is a control-loop paradigm: reason about the next action, call a tool,
observe its result, and repeat. Aetheris uses that loop, but the runtime—not
the model—owns budgets, approvals, persistence, and recovery.

## Durable project knowledge

`remember` writes Markdown pages under `.aetheris/wiki/`; `recall` and the runtime's lexical retrieval read them in later tasks. The files are human-readable and generated knowledge is ignored by Git by default. SQLite is used for ordered events and checkpoints; Markdown is used for stable project facts and decisions. A vector database would add another service before this small corpus needs semantic retrieval.

## Policy and safety

The default watchdog pauses after three consecutive failed actions. It is consulted after failures and periodically, not after every successful tool call. Jev is an optional, narrow `continue` or `pause for review` signal. If Jev cannot be imported or called, the deterministic policy takes over. Jev does not generate code or replace the main model.

Install the optional decision client with `uv sync --extra decision`. A local
Jev-compatible judge such as Kev can be selected with
`AETHERIS_DECISION_POLICY=jev`, `AETHERIS_DECISION_BASE_URL=http://127.0.0.1:8009`,
and `AETHERIS_DECISION_MODEL=kev-latest`. The runtime fails closed: it only
continues when the judge explicitly selects `continue` above the confidence
threshold; uncertainty becomes a human-review pause. Deterministic approval
gates and runtime budgets remain authoritative.

`write_file`, `edit_file`, and shell execution require an interactive approval. Approval is a user decision gate; it is not an OS sandbox. Shell commands run with the current process user's permissions. The repository does not claim protection against malicious commands. Use a disposable container or a separate OS account for untrusted repositories.

`read_file` also asks for approval before reading common sensitive paths such as
`.env`, private keys, `.ssh`, `.aws`, or `.git`. This is a narrow credential
boundary, not a guarantee that arbitrary secrets are detected; do not run an
untrusted agent with valuable credentials in its environment.

## Offline evaluation

The checked-in task set covers symbol search, file reading, a small edit followed by a unit test, one controlled tool failure and retry, an approval denial, recovery from an interrupted tool call, and durable knowledge recall.

```bash
python evals/run.py
python -m pytest -q
```

For a short deterministic interview demo, run:

```bash
python evals/demo.py
```

The focused runner can execute one scenario without API keys:

```bash
python evals/run.py --task resume-interrupted-call
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
pyproject.toml            # Package metadata, dependencies, and aetheris CLI
poetry.lock               # Reproducible dependency resolution
nexus/                    # Runtime package installed by the CLI and Docker image
  cli.py                  # CLI, plan/goal modes, profiles, and MCP commands
  project.py              # local agents, skills, and MCP configuration
  mcp.py                  # small JSONL stdio MCP client
  core/                   # Agent loop, state, memory, knowledge, policy, tracking
  skills/                 # Explicit tools plus approval-gated MCP wrappers
  web/                    # Bounded GitHub and DDGS/Brave search adapters
evals/                    # Offline scripted-model scenarios and demo
tests/                    # Runtime, protocol, persistence, and policy tests
Dockerfile                # Small non-root portable image
Dockerfile.streamlit      # Optional non-root Streamlit web image
.dockerignore             # Excludes state, caches, tests, and Git metadata
requirements.txt          # Streamlit Community Cloud dependencies
streamlit_app.py          # Read-only GitHub exploration and explicit search UI
nexus/api.py               # Small authenticated adapter for n8n/webhooks
nexus/transcription.py     # Optional hosted audio transcription adapter
README.md                 # Architecture, usage, limitations, and trade-offs
INTERVIEW.md              # Interview walkthrough and repository-specific Q&A
.aetheris/                # Local profiles, plans, MCP config, and wiki (ignored)
```

The folders have different responsibilities: `nexus/core` owns control and
state, `nexus/skills` owns side effects, `evals` exercises the real runtime,
and `tests` checks boundaries in isolation. The Docker image copies the
installable runtime and offline evaluator, not local databases or generated
wiki state.

## Portable Docker run

Build the image and run the no-key demo:

```bash
docker build -t aetheris:local .
docker run --rm --entrypoint python aetheris:local /opt/aetheris/evals/demo.py
```

Run a real task with a workspace and a named state volume:

```bash
docker run --rm -it \
  -v "$PWD:/workspace" \
  -v aetheris-state:/state \
  aetheris:local run "Inspect the repository" \
  --model ollama/llama3 --db /state/aetheris_memory.db
```

The image runs as a non-root user, but this is still not a sandbox. The shell
skill can access whatever the mounted workspace and container networking expose.
Use a separate container policy or disable sensitive skills for untrusted work.

Build the web image instead:

```bash
docker build -f Dockerfile.streamlit -t aetheris-web:local .
docker run --rm -p 8501:8501 aetheris-web:local
```

The web image is portable, but deployment still needs a provider secret. A
public URL is useful for demonstrating the read-only path; it is not evidence
that arbitrary shell execution is safe.

For workflow orchestration, see `deploy/n8n/`. It keeps n8n outside Aetheris
and calls the authenticated `aetheris serve` adapter over a private Docker
network. The default compose stack uses a read-only workspace and a separate
SQLite volume. `deploy/k3s/README.md` describes the later K3s/CI-CD path and
why multiple replicas require shared state first.

## Limitations

- Aetheris is a single-process, local-first prototype. SQLite and Markdown are not shared across workers.
- A hard cost ceiling cannot be guaranteed: providers may not expose a usable estimate, and one model response can cross the threshold before execution is paused. The step ceiling is enforced regardless.
- Approval does not isolate files, processes, or network access. There is no container sandbox.
- Offline evaluation checks control flow with scripted responses. It does not measure real-model task success, code quality, or comparative performance.
- A long run still depends on the model's ability to choose useful actions. Checkpoints preserve progress; they do not guarantee completion.
- A malformed or empty model response pauses the task with a checkpoint, but recovery still depends on a later valid response.
- MCP support currently targets local stdio servers and JSONL messages. It does
  not provide remote transport, server isolation, or permission grants based on
  MCP metadata.
- Project agent profiles and skills guide the model but are not a second policy
  engine. Runtime checks and approvals remain authoritative.

## License

MIT. See `LICENSE`.
