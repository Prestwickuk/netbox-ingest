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
