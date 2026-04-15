# HAROLD — README Notes

Running notes for eventual README.md. Not for public consumption yet.

---

## Container Registry

HAROLD images are pushed to **GHCR (GitHub Container Registry)**:

- Registry: `ghcr.io/Prestwickuk/harold`
- Auth is handled via the `gh` CLI (already authenticated)
- Image can be made private; grant cluster pull access via a `imagePullSecret`

### Build & push

```bash
docker build -t ghcr.io/Prestwickuk/harold:latest .
docker push ghcr.io/Prestwickuk/harold:latest
```

To tag a release:

```bash
docker build -t ghcr.io/Prestwickuk/harold:v1.0.0 .
docker push ghcr.io/Prestwickuk/harold:v1.0.0
```

---

## Kubernetes Deployment

Manifests live in `k8s/` using a **Kustomize overlay structure**:

```
k8s/
  base/           # core manifests (app, worker, postgres, services)
  overlays/
    local/        # port-forward only, no ingress
    production/   # patches in real hostname + any env-specific config
```

### Install (production)

```bash
kubectl apply -k k8s/overlays/production
```

### Install (local / dev)

```bash
kubectl apply -k k8s/overlays/local
```

### Configuring the ingress hostname

Edit `k8s/overlays/production/kustomization.yaml` and replace `harold.example.com` with your real hostname before applying.

### Switching to an external PostgreSQL instance

1. Delete or skip `k8s/base/postgres.yaml` from the base `kustomization.yaml`
2. Update `DATABASE_URL` in `k8s/base/secret.yaml` to point at the external instance
3. Apply as normal

### SSE and ingress timeouts

The ingress is pre-configured with 1-hour proxy read/send timeouts to support the SSE live progress stream. If your ingress controller uses different annotation keys, update `k8s/base/ingress.yaml` accordingly.

---

## Database

HAROLD bundles a **PostgreSQL StatefulSet** in the base manifests — no external DB required to get started.

The app connects via `DATABASE_URL`, which is stored in a Kubernetes Secret (`harold-db-secret`). To switch to an external PostgreSQL instance:

1. Delete or skip applying `k8s/base/postgres.yaml`
2. Update the `DATABASE_URL` value in `k8s/base/secret.yaml` (or your overlay) to point at the external instance
3. Apply as normal

---

## NetBox URL & Token

These are provided **per-job at runtime** via the upload form — no cluster-level configuration needed.

---

## Ingress

The ingress hostname is configured per overlay. Set it in:

```
k8s/overlays/production/kustomization.yaml
```

Default placeholder: `harold.example.com`
