# K3s deployment decision

K3s is a possible hosting target, not the default interview deployment. The
smallest sensible topology would be:

```text
Ingress / private VPN
        |
        v
n8n Deployment ----> Aetheris API Deployment (read-only by default)
                         |
                         +--> one stateful SQLite volume
                         +--> read-only workspace volume
```

Before using K3s, replace local SQLite with a shared transactional database or
keep exactly one Aetheris replica with a persistent volume. Adding replicas
without changing the state model would create split-brain task state. The
agent worker also needs a per-task isolated workspace and a network policy that
denies arbitrary egress unless a reviewed tool needs it.

## CI/CD path

1. GitHub Actions runs tests, offline evaluation, package checks, and a Docker
   build on pull requests.
2. A version tag builds and publishes the API image to GHCR.
3. A K3s deployment pulls an immutable image tag, applies a Secret for the API
   token/provider key, and rolls out one worker.
4. A smoke job checks `/healthz`, submits a read-only task, and reads its state.
5. Roll back the image tag if the smoke task or migration check fails.

## Private Streamlit demo on an existing K3d cluster

`streamlit.yaml` is a minimal Kubernetes manifest for an ephemeral, read-only
web demo: one replica, a non-root container, no service-account token, writable
scratch space, and a ClusterIP Service. It does not enable coding mode or
create a public URL. Public archives, wiki and SQLite state are temporary;
pod replacement loses those sessions. This is packaging, not a secure
multi-user coding service or a Kubernetes execution sandbox.

From the repository, with Docker and the selected local cluster running:

```bash
docker build -f Dockerfile.streamlit -t aetheris-web:local .
kubectl config current-context
kubectl get nodes
k3d image import aetheris-web:local -c aetheris-demo
kubectl create namespace aetheris-demo
kubectl -n aetheris-demo create secret generic aetheris-provider --from-env-file=.env
kubectl -n aetheris-demo apply -f deploy/k3s/streamlit.yaml
kubectl -n aetheris-demo rollout status deployment/aetheris-web --timeout=120s
kubectl -n aetheris-demo port-forward service/aetheris-web 8501:8501
```

Open http://localhost:8501. Use `aetheris-demo` only if `k3d cluster list` confirms
that name and select context `k3d-aetheris-demo` explicitly. If the namespace/Secret exists, inspect it before changing it rather than
blindly rerunning create commands. The env file must be uncommitted and contain
only approved server settings/credentials in kubectl env-file syntax. Explicit
manifest settings keep local coding disabled even if the Secret has that flag.
For a non-K3d cluster, publish a reviewed image to your own registry and change
the image reference/pull policy deliberately. No push is part of these commands.

Status on 2026-10-02: Docker Desktop was started and a dedicated local K3d
cluster `aetheris-demo` was created. Its single node is Ready. The manifest
passed Kubernetes server-side dry-run validation. The initial image build is
completed successfully. Image import and rollout succeeded; the pod is Running
and Ready with zero restarts. HTTP health checks returned `ok` both inside the
pod and through localhost port-forward. Streamlit AppTest initialized the page
inside the pod without exceptions, under UID 1000 and the read-only-root
manifest. A DeepSeek-only Secret was subsequently configured locally without
printing or committing its value. Live Streamlit AppTest inside the pod loaded
a public GitHub archive, exercised slash commands, greeted the user and
completed repository inspection: three tool calls, zero failures, nine activity
events, 6478 prompt tokens and 433 completion tokens, estimated $0.002049048
for the inspection. This is framework-level UI evidence, not a native-browser
visual check. Sustained memory requirements are not load-tested.

The current private test is available at http://127.0.0.1:8502 while this runs:

```bash
kubectl --context k3d-aetheris-demo -n aetheris-demo port-forward \
  --address 127.0.0.1 service/aetheris-web 8502:8501
```
There is no automatic CD pipeline or public deployment. Keep the local CLI/UI
demo as the interview default. Add authentication, aggregate spending limits
and network policy before any shared hosting.
