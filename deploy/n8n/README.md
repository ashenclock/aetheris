# n8n integration

This optional stack keeps orchestration outside the Aetheris runtime:

```text
n8n webhook / schedule / approval
             |
             | private Docker network + bearer token
             v
Aetheris HTTP adapter -> Agent -> bounded read-only workspace
                       -> SQLite state volume
```

The default API mode is read-only. The mounted workspace is `:ro`, Aetheris
is non-root, the API is not published to the host, and the API requires
`AETHERIS_API_TOKEN`. This is a useful demo boundary, not a complete sandbox.

## Start locally

From the repository root:

```bash
cp .env.example .env
# Set AETHERIS_API_TOKEN and N8N_ENCRYPTION_KEY in .env.
docker compose --env-file .env -f deploy/n8n/docker-compose.yml up --build
```

Open n8n at `http://localhost:5678`. In an HTTP Request node, call:

Create an n8n Header Auth credential containing the bearer token, then use an
HTTP Request node:

```text
POST http://aetheris-api:8787/v1/tasks
Authorization: Bearer <the n8n Header Auth credential>
Content-Type: application/json

{"session":"n8n-demo","goal":"Inspect the repository and summarize the test layout","max_steps":6}
```

Keeping the token in an n8n credential is preferable to exposing it in a
workflow expression or exporting it into a Code node.

Before exposing the stack beyond localhost, replace `n8nio/n8n:latest` with a
reviewed version or digest, enable HTTPS, remove `N8N_SECURE_COOKIE=false`, and
run `n8n audit`. The internal Docker network limits reachability, but it is not
a substitute for authentication, patching, or a sandbox.

Use `GET http://aetheris-api:8787/v1/tasks/n8n-demo` for the persisted state.
The health endpoint is `GET http://aetheris-api:8787/healthz` and is public only
inside the Docker network.

Do not enable `--allow-write` in a public workflow. If write actions are ever
needed, use an isolated worker, explicit approval, idempotency keys, and a
separate disposable workspace.

## Workflow idea for the interview

1. Webhook receives a repository URL and a question.
2. n8n validates the allow-list and records a run ID.
3. HTTP Request calls Aetheris in read-only mode.
4. n8n sends the structured answer to Slack/email or stores it.
5. A second HTTP Request reads state/checkpoints for observability.

This is a sensible integration demonstration because n8n orchestrates
triggers and external systems; Aetheris remains responsible for the model/tool
loop, state, limits, and failure recovery.
