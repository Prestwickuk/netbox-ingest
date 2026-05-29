# HAROLD

**Hardware Asset Rack Orchestration & Loading Daemon**

HAROLD is a bulk ingestion service for [NetBox](https://netboxlabs.com/), built for infrastructure teams that need to onboard large numbers of physical assets — racks, servers, network devices, patch panels, power infrastructure, cabling, and IP addresses — quickly and reliably.

---

## What it does

HAROLD accepts CSV or JSON files and ingests them into NetBox via the API, handling object resolution, duplicate detection, and per-record error reporting along the way. It is not a one-shot import script: it is a persistent service with a job queue, a live progress dashboard, and the ability to retry failures without re-running a full import.

### Supported ingestion types

| Type | What it creates in NetBox |
|---|---|
| `racks` | Racks, with site/location/role resolution |
| `rack_infra` | Rack-mounted infrastructure devices (blanking panels, cable managers) |
| `patch_panels` | Patch panels and FS fibre enclosures, including cassette module installation |
| `network_devices` | Switches, routers, and other network devices |
| `servers` | Compute servers, with tenant assignment |
| `power_panels` | Site-level power panels |
| `power_feeds` | Power feeds from panel to rack, with electrical spec lookup by feed type |
| `cables` | Direct device connections, device-to-patch-panel, patch-panel-to-patch-panel, and PSU-to-PDU |
| `ip_assignment` | IP allocation from IPAM prefixes across up to five interfaces per device, with primary IP designation |

### Key features

- **Duplicate detection** — every stage checks for existing objects before creating, skipping duplicates rather than failing
- **Per-record logging** — each record carries its own log trail; failures include the exact error from the NetBox API
- **Live progress** — real-time job progress via Server-Sent Events (SSE branch) or HTMX polling (main branch)
- **Retry failed records** — re-queue only the records that failed without re-uploading the file
- **Saved NetBox instances** — store named NetBox credentials and select them at upload time; set a default instance for your most-used environment
- **Per-job tuning** — override batch size and API rate limit per job to stay within NetBox API constraints
- **Kubernetes-native** — ships with Kustomize manifests, a bundled PostgreSQL StatefulSet, and a worker HPA (scales 1–5 replicas under CPU load)

### Why HAROLD instead of a script?

- **Scale** — tested against jobs with 10,000+ records; the `SKIP LOCKED` worker queue lets multiple worker replicas process batches in parallel safely
- **Visibility** — you can see exactly which records succeeded, failed, or were skipped, and why
- **Safety** — duplicate detection means imports are idempotent; running the same file twice will skip existing objects, not create duplicates
- **Operability** — jobs are persisted in PostgreSQL; a worker crash does not lose progress

---

## Prerequisites

- Docker (for local dev) or a Kubernetes cluster (for production)
- A running NetBox v4.3+ instance
- Python 3.12+ (only needed if running outside Docker)

---

## Local development

### 1. Start the stack

```bash
docker compose up --build
```

This starts three containers: `db` (PostgreSQL on port 5433), `app` (FastAPI on port 8000), and `worker`.

### 2. Run database migrations

```bash
docker compose exec app alembic upgrade head
```

### 3. Open the UI

```
http://localhost:8000
```

---

## Kubernetes deployment

Manifests use a Kustomize overlay structure:

```
k8s/
  base/             # core manifests: app, worker, postgres, ingress, HPA
  overlays/
    local/          # no ingress — use kubectl port-forward
    production/     # patches in your real hostname
```

### Build and push the image

```bash
docker build -t ghcr.io/Prestwickuk/harold:latest .
docker push ghcr.io/Prestwickuk/harold:latest
```

To tag a release:

```bash
docker build -t ghcr.io/Prestwickuk/harold:v1.0.0 .
docker push ghcr.io/Prestwickuk/harold:v1.0.0
```

### Deploy (production)

1. Set your hostname in `k8s/overlays/production/kustomization.yaml` — replace `harold.example.com`
2. Apply:

```bash
kubectl apply -k k8s/overlays/production
```

3. Run migrations:

```bash
kubectl exec -n harold deploy/harold-app -- alembic upgrade head
```

### Deploy (local cluster, no ingress)

```bash
kubectl apply -k k8s/overlays/local
kubectl port-forward -n harold svc/harold-app 8000:80
```

### Using an external PostgreSQL instance

1. Remove `postgres.yaml` from `k8s/base/kustomization.yaml`
2. Update `DATABASE_URL` in `k8s/base/secret.yaml` to point at your external instance
3. Apply as normal

> **Note:** The `harold-db-secret` in `k8s/base/secret.yaml` contains credentials in plaintext. For production, replace this with your cluster's secret management solution (Sealed Secrets, External Secrets Operator, Vault, etc.).

---

## Configuration

All configuration is via environment variables. Docker Compose and the K8s manifests set sensible defaults.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://netbox_ingest:netbox_ingest@localhost:5433/netbox_ingest` | PostgreSQL connection string |
| `BATCH_SIZE` | `50` | Records claimed per worker iteration (overridable per job) |
| `WORKER_POLL_INTERVAL` | `5` | Seconds the worker sleeps when no jobs are pending |
| `RATE_LIMIT` | `0` (unlimited) | Global max NetBox API calls per second per worker (overridable per job) |

---

## Using HAROLD

### Setting up NetBox instances

Before running imports, save your NetBox connection details under **NetBox Instances** in the nav. You can save multiple instances (e.g. dev, staging, production) and mark one as the default — it will be pre-selected on the upload form.

### Running an import

1. Go to **New Import**
2. Select the file type matching your CSV
3. Choose a saved NetBox instance or enter a custom URL and token
4. Optionally set a batch size or rate limit under **Advanced options**
5. Upload your file and click **Upload & Queue**

The job detail page shows live progress and per-record status as the worker processes records.

### CSV schemas

All fields marked `*` are required.

**racks**
`name*, site*, u_height*, location, rack_role, width_inches, serial, asset_tag, comments`

**rack_infra**
`name*, site*, rack*, position_u*, face*, manufacturer*, device_type*, role*, status`

**patch_panels**
`name*, site*, rack*, position_u*, face*, manufacturer*, device_type*, role*, status, module_bay_count, cassette_manufacturer, cassette_device_type`

> For FS fibre enclosures: populate `cassette_manufacturer`, `cassette_device_type`, and `module_bay_count` (minimum 1). Cassette ports are created automatically from the NetBox module type template.

**network_devices**
`name*, site*, manufacturer*, device_type*, device_role*, status*, rack, position_u, face, platform, serial, asset_tag`

**servers**
`name*, site*, manufacturer*, device_type*, device_role*, status*, rack, position_u, face, platform, serial, asset_tag, tenant`

**power_panels**
`name*, site*, location`

**power_feeds**
`name*, site*, power_panel*, rack*, status, feed_type`

Supported `feed_type` values and their electrical specs:

| feed_type | Voltage | Amperage | Phase |
|---|---|---|---|
| `32A-3P-230V` | 230V | 32A | Three-phase |
| `63A-3P-230V` | 230V | 63A | Three-phase |

**cables**
`a_device*, a_site*, a_termination_type*, a_termination_name*, b_device*, b_site*, b_termination_type*, b_termination_name*, label, cable_type, color, status`

Valid `termination_type` values: `interface`, `front_port`, `rear_port`, `power_port`, `power_outlet`, `console_port`, `console_server_port`

**ip_assignment**
`device*, site*, prefix*, vrf, iface_1_name, iface_2_name, iface_3_name, iface_4_name, iface_5_name, primary_mgmt_iface, primary_data_iface`

- Up to five interfaces per device row
- `primary_mgmt_iface` sets the device's `primary_ip4` in NetBox (must match one of the populated `iface_N_name` values)
- `primary_data_iface` is assigned an IP but does not set the NetBox device primary
- IPs are allocated as next-available from the specified prefix

### Retrying failures

On the job detail page, a **Retry failed (N)** button appears when a completed job has failed records. Clicking it resets those records to pending and requeues the job — no need to re-upload the file.

---

## Troubleshooting

**Worker is not picking up jobs**
Check that the worker process is running: `docker compose ps` or `kubectl get pods -n harold`. The worker polls for pending jobs every `WORKER_POLL_INTERVAL` seconds.

**Records failing with "Device type not found"**
HAROLD does not create device types automatically. Pre-populate them in NetBox manually or via the [NetBox community device type library](https://github.com/netbox-community/devicetype-library) before running an import.

**Records failing with "Site not found"**
Sites are resolved by name or slug. Make sure the value in your CSV matches exactly (case-insensitive for slug lookup, case-sensitive for name).

**Fibre enclosure records failing with "no module bays defined"**
The enclosure's device type in NetBox must have module bay templates configured. Add them to the device type before importing.

**"No available IPs in prefix"**
The specified prefix has been exhausted. Expand the prefix in NetBox IPAM or use a different prefix.

**SSE progress stream drops after ~60 seconds**
Your ingress proxy is cutting the connection. The bundled ingress manifest sets 1-hour timeouts via `nginx.ingress.kubernetes.io/proxy-read-timeout`. If you use a different ingress controller, apply the equivalent timeout annotation.

**Alembic migration fails on existing database**
If the app created tables via `create_all` before migrations were introduced, stamp the DB first then upgrade:
```bash
alembic stamp head
alembic upgrade head
```
If you see `column jobs.batch_size does not exist`, the columns were not added by `create_all` (it only creates tables, never alters them). Add them manually:
```bash
docker compose exec db psql -U netbox_ingest -d netbox_ingest -c \
  "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS batch_size INTEGER; \
   ALTER TABLE jobs ADD COLUMN IF NOT EXISTS rate_limit INTEGER;"
```
Then restart the app and worker containers.

---

## Architecture

```
Browser  ──►  FastAPI app  ──►  PostgreSQL
                                    ▲
              Worker(s)  ───────────┘
                  │
                  └──►  NetBox API
```

- **App** — serves the UI and REST endpoints; writes jobs and records to PostgreSQL
- **Worker** — claims batches of pending records using `SELECT ... FOR UPDATE SKIP LOCKED`, processes them through the appropriate stage, and writes results back to PostgreSQL
- **PostgreSQL** — job queue, record store, and log store
- Multiple worker replicas are safe: `SKIP LOCKED` ensures each record is processed by exactly one worker

---

## Branches

| Branch | Description |
|---|---|
| `main` | Stable — uses HTMX polling (every 2s) for job progress |
| `sse` | Live progress via Server-Sent Events — single persistent connection per viewer |
