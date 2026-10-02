# Aetheris quick guide

In a GitHub web snapshot, `/write_enable` adds workspace-scoped write, edit and
wiki tools with per-action approval. It never enables shell or MCP, nor access
to parent paths. `/write_disable` returns to read-only tools; `/permissions`
shows the current mode. Snapshot edits are temporary and are not pushed to GitHub.
For example, ask for `iris_lr/iris_lr.py`, not `../iris_lr/iris_lr.py`.

## Start a chat

From the repository, activate the installed environment:

```bash
source .venv/bin/activate
aetheris chat --model deepseek/deepseek-chat --workspace . --session chat
```

The CLI loads the ignored `.env`. Configure the relevant provider key there,
never in a prompt. `aetheris providers` lists configuration without exposing
credentials. For Ollama, use an installed model reported by `ollama list`.
The model may take time; activity displays tool requests, results, provider
waits, and model-authored plans—not private chain-of-thought.

## Chat controls (terminal and web)

| Command | Meaning |
| --- | --- |
| `/` or `/help` | List commands; terminal also autocompletes |
| `/status` | Saved state, budgets, failures |
| `/resume` | List saved sessions |
| `/resume NAME` | Continue the same task |
| `/new NAME` | Select a session |
| `/model PROVIDER/MODEL` | Switch in a fresh session |
| `/provider` | Inspect configured providers |
| `/permissions` | Inspect approval mode |
| `/permissions session` | Auto-approve ordinary tools for this chat |
| `/permissions ask` | Restore per-action approval |
| `/wiki` | Durable Markdown facts and their links |
| `/trace` | Recent tool-call IDs and checkpoints |
| `/skills`, `/mcp` | Inspect local extensions |
| `/exit` | Exit without deleting saved state |

Session approval includes shell, file writes, and wiki writes. It is **not a
sandbox**; use only in a disposable trusted workspace. Sensitive reads and MCP
still need explicit approval. Approval grants are not saved across restarts.

A paused task keeps its original goal and usage. Increase the step allowance
before resuming if needed; resume does not silently replenish the cost budget.

## Streamlit

```bash
AETHERIS_WEB_LOCAL=1 streamlit run streamlit_app.py
```

Open http://localhost:8501. Click **Open local workspace** for CLI-like coding.
The configured root defaults to the working directory; set
`AETHERIS_WEB_WORKSPACE` to constrain folder selection. Local mode loads `.env`.
Shell/edit tools pause for **Approve once** or **Reject**, unless explicitly
enabled with `/permissions session`.

Without `AETHERIS_WEB_LOCAL=1`, the UI only downloads public GitHub archives
and explores them read-only. This is a snapshot, not a clone or pull: local
uncommitted changes and Git metadata are absent. The runtime itself comes from
the installed local application, not the downloaded repository.

The UI displays activity as it happens, saved state, a checkpoint timeline,
and the wiki graph. `[[topic]]` links connect Markdown pages. Ask the agent to
remember a decision with its source; wiki writes use the normal approval gate.
The wiki is not an automatically verified source of truth.

## One practical task

Use a separate workspace and ask:

> Build a seeded scikit-learn classification pipeline using synthetic data.
> Split before fitting preprocessing. Log held-out metrics in local MLflow.
> Add separate feature-drift and concept-drift simulations, tests, and a small
> Streamlit results viewer. Keep files here; do not push or publish anything.
> Run the tests and report measured results and limitations.

Review proposed installations and commands. Do not confuse a model's final
answer with verified success: inspect files, test output, state, and metrics.

## Deterministic verification

```bash
python -m pytest -q
python evals/run.py
python evals/demo.py
```

These run without paid API calls. They verify runtime behavior, not model
quality. Live provider smoke tests are explicit:
`python evals/providers.py --live`.

Other interfaces are discoverable with `aetheris --help`. Optional search,
transcription, MCP, API, and deployment are separate adapters—not prerequisites
for chat. See `README.md` and `deploy/k3s/README.md` for their boundaries.
