# Aetheris hands-on guide

This guide is for testing the runtime with real tasks while keeping the
workspace, model credentials, and side effects explicit.

## 1. Choose a model

The offline evaluator needs no model or API key:

```bash
python evals/demo.py
python evals/run.py --output /tmp/aetheris-evaluation.json
```

For a local Ollama model, start Ollama separately and pull a model that is
available on the machine:

```bash
ollama serve
ollama pull llama3
```

For the hosted OpenAI Responses adapter, keep the key in the uncommitted
`.env` file:

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY and AETHERIS_MODEL.
# Example: AETHERIS_MODEL=responses/codex-mini-latest
```

The CLI loads `.env`. An explicit `--model` argument always takes precedence.
Never put a key in a task prompt, repository file, slide, or git commit.

## 2. Observe a task from another terminal

Use a dedicated database and session for every experiment:

```bash
STATE_DB="$PWD/.aetheris-web.sqlite3"
WORKSPACE="$PWD/.demo-workspaces/web"
mkdir -p "$WORKSPACE"
```

In terminal A, run the task:

```bash
aetheris run "Inspect the empty workspace and explain what you would build. Do not edit files yet." \
  --workspace "$WORKSPACE" \
  --db "$STATE_DB" \
  --session web-demo \
  --max-steps 8 \
  --cost-budget 0.25
```

In terminal B, inspect the authoritative state while it runs:

```bash
aetheris status --db "$STATE_DB" --session web-demo --json
```

To poll every two seconds on macOS or Linux:

```bash
while true; do
  clear
  date
  aetheris status --db "$STATE_DB" --session web-demo
  sleep 2
done
```

The status shows the session, status, step budget, tool calls, failures,
checkpoints, last action, and last error. A paused task is not automatically
lost; use the same database and session to resume it.

For a lower-level protocol trace, SQLite is intentionally inspectable:

```bash
sqlite3 "$STATE_DB" \
  "select id,role,name,tool_call_id,substr(content,1,120) from messages where session_id='web-demo' order by id;"
sqlite3 "$STATE_DB" \
  "select id,timestamp,reason from checkpoints where session_id='web-demo' order by id;"
```

## 3. Build a small web project

Use `chat` when you want to see the agent's steps and approve file edits or
shell commands interactively. Use a disposable workspace and never point a
first experiment at an important repository:

```bash
WORKSPACE="$PWD/.demo-workspaces/web"
STATE_DB="$PWD/.aetheris-web.sqlite3"
mkdir -p "$WORKSPACE"

aetheris chat \
  --workspace "$WORKSPACE" \
  --db "$STATE_DB" \
  --session web-demo \
  --max-steps 30 \
  --cost-budget 0.50
```

Paste this task into the chat:

```text
Build a minimal static web project in the current workspace.

Constraints:
- First inspect the workspace and propose a short plan.
- Use plain HTML, CSS, and JavaScript; do not add a framework.
- Create a responsive landing page with one small interactive feature.
- Keep every file inside the workspace.
- Do not install packages, access private data, push git commits, or deploy to a public service.
- After editing, run a deterministic local check and explain the result.
- Before any shell command, show the exact command and wait for approval.
```

When Aetheris asks for approval, review the exact file or command. Approve
small writes and harmless checks; reject package installation, destructive
commands, credentials, or unexplained network access.

After the task:

```bash
aetheris status --db "$STATE_DB" --session web-demo
find "$WORKSPACE" -maxdepth 2 -type f -print
python -m http.server 8000 --directory "$WORKSPACE"
```

Open <http://127.0.0.1:8000>. If the task pauses or stops at its step limit,
continue it without changing the database or session:

```bash
aetheris resume web-demo \
  "Review the current files, run the local check, and fix only the smallest confirmed issue." \
  --workspace "$WORKSPACE" \
  --db "$STATE_DB" \
  --max-steps 15
```

## 4. Add a local container build

Only after reviewing the generated files, ask for a container artifact:

```text
Add a minimal Dockerfile for this static site using a small non-root web server.
Do not build or run it yet. Explain the base image, exposed port, and security assumptions.
```

Then approve the build explicitly:

```text
Run a local Docker build and a short container smoke test. Do not push the image,
publish it, or change files outside the current workspace.
```

Docker is a packaging boundary, not automatically a complete sandbox. For a
public agent, use isolated workers, restricted credentials, network policy, and
an approval identity in addition to a non-root image.

## 5. Build a small MLflow pipeline

Use a separate workspace and database:

```bash
ML_WORKSPACE="$PWD/.demo-workspaces/mlflow"
ML_DB="$PWD/.aetheris-mlflow.sqlite3"
mkdir -p "$ML_WORKSPACE"
aetheris chat \
  --workspace "$ML_WORKSPACE" \
  --db "$ML_DB" \
  --session mlflow-demo \
  --max-steps 40 \
  --cost-budget 0.75
```

Use this prompt. It intentionally separates implementation from installation
and network access:

```text
Create a small, reproducible binary-classification pipeline in this workspace.

Requirements:
- Use scikit-learn and MLflow.
- Download one public tabular dataset only after showing the exact URL or
  sklearn dataset loader and asking for approval.
- Split by the dataset's normal sample units, fit a baseline model, and report
  accuracy, macro F1, and ROC AUC where valid.
- Log parameters, metrics, the model, and a README to a local ./mlruns store.
- Write src/train.py, requirements.txt, and a short README with a seed.
- Do not use credentials, private medical data, cloud tracking, or a public
  deployment.
- Do not run pip install yet. First create the files and explain the commands
  needed to install and execute them.
```

Review the generated code and requirements. If the dependencies are acceptable,
approve the installation explicitly:

```text
Install only the packages listed in requirements.txt into an isolated virtual
environment, then run the training script. Do not modify the system Python.
```

After the run, inspect both Aetheris and MLflow state:

```bash
aetheris status --db "$ML_DB" --session mlflow-demo --json
find "$ML_WORKSPACE" -maxdepth 3 -type f -print
mlflow ui --backend-store-uri "$ML_WORKSPACE/mlruns" --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000> only after confirming the tracking directory is
local and contains no sensitive data. For the interview, show the experiment
directory, the training script, the recorded metrics, and the Aetheris
checkpoints; do not present a metric as meaningful without describing the
dataset and split.

## 6. Failure experiments

These are useful demonstrations because they make the runtime behavior visible:

1. Set `--max-steps 1` and observe a deterministic stop.
2. Give an invalid model name and inspect the paused state and `last_error`.
3. Interrupt a run during a tool request, then call `aetheris resume` with the
   same database and session. Inspect `recovered_interrupted_calls`.
4. Ask for a destructive command and reject the approval. Confirm that the
   command was not executed and that the failure is persisted.
5. Run the offline evaluator and compare its JSON result with the SQLite
   checkpoints.

The important distinction is that checkpoints preserve runtime state; they do
not make an already-started external side effect exactly once, and they do not
prove that the model made a good engineering decision.
