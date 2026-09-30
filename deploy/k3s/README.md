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

This repository does not apply a cluster or include a Kubernetes manifest yet:
the local Docker/Streamlit path is easier to verify and is stronger for the
interview. K3s becomes justified when there is a real always-on workflow,
private networking requirement, or more than one worker.
