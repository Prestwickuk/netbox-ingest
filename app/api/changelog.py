from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templates_config import templates

router = APIRouter()


@router.get("/changelog", response_class=HTMLResponse)
def changelog(request: Request):
    return templates.TemplateResponse("changelog.html", {
        "request": request,
        "changelog": CHANGELOG,
    })


CHANGELOG = [
    {
        "version": "1.0.9",
        "date": "2026-05-29",
        "sections": {
            "Added": [
                "Cables stage now supports `power_feed` terminations, closing the panel → feed → PDU input gap. The full upstream power chain (power_panel → power_feed → PDU.power_port → PDU.power_outlet → server.power_port) can now be modelled end-to-end. For `power_feed` terminations, the `a_device` / `b_device` column holds the power panel name since feeds belong to panels, not devices.",
            ],
            "Docs": [
                "README clarifies that PDUs are ingested via the `rack_infra` stage with `role` set to your PDU role — NetBox auto-creates the outlets from the device-type's outlet templates.",
            ],
        },
    },
    {
        "version": "1.0.8",
        "date": "2026-05-29",
        "sections": {
            "Added": [
                "Cables stage now supports `console_port` and `console_server_port` terminations, enabling out-of-band console patching (server console port → console server port, or via a patch panel) that NetBox already models but HAROLD previously rejected as 'Invalid termination type'.",
            ],
        },
    },
    {
        "version": "1.0.7",
        "date": "2026-05-28",
        "sections": {
            "Fixed": [
                "Alembic migrations failing with ModuleNotFoundError: No module named 'app' when run via `docker compose exec app alembic upgrade head`. Alembic is installed as a console script which doesn't add the cwd to sys.path the way uvicorn does. migrations/env.py now prepends the project root to sys.path before importing app.models.db.",
            ],
        },
    },
    {
        "version": "1.0.6",
        "date": "2026-05-28",
        "sections": {
            "Added": [
                "Job.error_message column + Alembic migration 0002. When a job fails before any record is processed (e.g. invalid token, NetBox unreachable, malformed CSV that crashes the stage), the exception text is now persisted to the DB and shown as a red banner on the job detail page. Previously the only record of the failure was in worker stdout.",
                "Upload-time required-header validation for cables, power_panels, power_feeds, and ip_assignment. Missing columns are now caught at upload, not at processing time.",
            ],
            "Fixed": [
                "Cables stage: a missing 'a_device' (or other required) column in the CSV raised KeyError in the dedup check, bypassed the per-record error handler, and killed the entire job. The dedup check now uses .get() defensively so the record fails cleanly via REQUIRED_FIELDS validation.",
                "NetBoxClient now strips 'Token ' / 'Bearer ' prefixes from the supplied token before passing it to pynetbox, which builds the Authorization header itself. Saving a token with a prefix no longer results in 403 'Invalid authorization header'.",
            ],
        },
    },
    {
        "version": "1.0.5",
        "date": "2026-05-27",
        "sections": {
            "Fixed": [
                "Logo image 404 on Linux deployments: nav template referenced /static/HAROLD-LOGO.PNG (uppercase) but the file on disk is HAROLD-LOGO.png. Worked on macOS (case-insensitive filesystem) but failed on Linux containers (case-sensitive). Template now matches the file casing.",
            ],
        },
    },
    {
        "version": "1.0.4",
        "date": "2026-04-16",
        "sections": {
            "Fixed": [
                "IP assignment: filter out network and broadcast addresses from NetBox available-ips response (NetBox 3.7 returns the network address as the first result, causing 'network ID' rejection on assignment)",
            ],
        },
    },
    {
        "version": "1.0.3",
        "date": "2026-04-16",
        "sections": {
            "Fixed": [
                "IP assignment: bypass pynetbox DetailEndpoint for available-ips lookup entirely; use raw HTTP session to GET /api/ipam/prefixes/{id}/available-ips/ directly, eliminating incorrect address allocation",
            ],
        },
    },
    {
        "version": "1.0.2",
        "date": "2026-04-16",
        "sections": {
            "Fixed": [
                "IP assignment: pynetbox available_ips.create() was sending the network address (10.0.0.0) instead of the first usable host. Now uses available_ips.list() to get the next IP and creates it explicitly via ip_addresses.create()",
            ],
        },
    },
    {
        "version": "1.0.1",
        "date": "2026-04-16",
        "sections": {
            "Added": [
                "Example CSV templates for all nine ingestion stages",
                "Dynamic 'Download example CSV' link on the upload form — updates when file type changes",
            ],
        },
    },
    {
        "version": "1.0.0",
        "date": "2026-04-16",
        "sections": {
            "Added": [
                "Nine ingestion stages: racks, rack_infra, patch_panels, network_devices, servers, power_panels, power_feeds, cables, ip_assignment",
                "FS fibre enclosure support with full NetBox module bay and cassette installation",
                "Prefix-based IP allocation across up to five interfaces per device, with primary IP designation",
                "Unified cable stage covering device-to-device, device-to-patch-panel, patch-panel-to-patch-panel, and PSU-to-PDU connections",
                "Power feed electrical spec lookup by feed type (32A-3P-230V, 63A-3P-230V)",
                "Duplicate detection on all stages — existing objects are skipped, not failed",
                "Per-record log trail with info, warning, and error levels",
                "Live job progress via Server-Sent Events (SSE)",
                "Retry failed records without re-uploading the file",
                "Saved NetBox instances with default selection",
                "Per-job batch size and API rate limit override",
                "Kubernetes manifests with Kustomize overlays for local and production",
                "Bundled PostgreSQL StatefulSet for zero-dependency deployment",
                "Worker HPA scaling 1–5 replicas based on CPU utilisation",
                "Alembic database migrations",
            ],
        },
    },
]
