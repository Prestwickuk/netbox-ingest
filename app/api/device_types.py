import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import devicetype_library
from app.database import get_db
from app.models.db import Job, NetBoxInstance, Record
from app.templates_config import templates
from app.worker.stages.device_types import parse_device_type_yaml

log = logging.getLogger(__name__)

router = APIRouter()


def _instances(db: Session) -> list[NetBoxInstance]:
    return db.execute(select(NetBoxInstance).order_by(NetBoxInstance.name)).scalars().all()


@router.get("/device-types", response_class=HTMLResponse)
def device_types_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("device_types.html", {
        "request": request,
        "instances": _instances(db),
    })


@router.get("/device-types/manufacturers", response_class=HTMLResponse)
def manufacturers_partial(request: Request):
    """HTMX partial: manufacturer dropdown built from the devicetype-library index."""
    try:
        index = devicetype_library.get_index()
    except Exception as exc:
        log.warning(f"Failed to load devicetype-library index: {exc}")
        return templates.TemplateResponse("partials/dtl_manufacturers.html", {
            "request": request,
            "error": f"Could not reach the device-type library on GitHub: {exc}",
        })
    return templates.TemplateResponse("partials/dtl_manufacturers.html", {
        "request": request,
        "manufacturers": [
            {"name": name, "count": len(models)} for name, models in index.items()
        ],
    })


@router.get("/device-types/models", response_class=HTMLResponse)
def models_partial(request: Request, manufacturer: str = ""):
    """HTMX partial: device-type model checkboxes for one manufacturer."""
    if not manufacturer:
        return HTMLResponse("")
    try:
        index = devicetype_library.get_index()
    except Exception as exc:
        return templates.TemplateResponse("partials/dtl_models.html", {
            "request": request,
            "manufacturer": manufacturer,
            "error": f"Could not reach the device-type library on GitHub: {exc}",
        })
    return templates.TemplateResponse("partials/dtl_models.html", {
        "request": request,
        "manufacturer": manufacturer,
        "models": index.get(manufacturer, []),
    })


@router.post("/device-types/import", response_class=HTMLResponse)
def import_device_types(
    request: Request,
    job_name: Annotated[str, Form()] = "",
    netbox_source: Annotated[str, Form()] = "custom",
    netbox_url: Annotated[str, Form()] = "",
    netbox_token: Annotated[str, Form()] = "",
    model_paths: Annotated[list[str], Form()] = [],
    yaml_files: Annotated[list[UploadFile], File()] = [],
    db: Session = Depends(get_db),
):
    def error_page(message: str):
        return templates.TemplateResponse("device_types.html", {
            "request": request,
            "instances": _instances(db),
            "error": message,
            "form": {"job_name": job_name, "netbox_url": netbox_url},
        })

    # Resolve NetBox credentials (same contract as /upload)
    if netbox_source != "custom":
        try:
            instance = db.get(NetBoxInstance, uuid.UUID(netbox_source))
        except ValueError:
            instance = None
        if not instance:
            return error_page("NetBox instance not found")
        resolved_url, resolved_token = instance.url, instance.token
    else:
        if not netbox_url or not netbox_token:
            return error_page("NetBox URL and token are required when using a custom connection")
        resolved_url, resolved_token = netbox_url.rstrip("/"), netbox_token

    # Collect device-type definitions: selected from the library and/or uploaded
    rows: list[dict] = []

    for path in model_paths:
        try:
            devicetype_library.validate_library_path(path)
            yaml_text = devicetype_library.fetch_device_type_yaml(path)
            data = parse_device_type_yaml(yaml_text)
        except Exception as exc:
            return error_page(f"Failed to fetch '{path}' from the device-type library: {exc}")
        rows.append({
            "name": f"{data['manufacturer']} {data['model']}",
            "source": "library",
            "path": path,
            "yaml_text": yaml_text,
        })

    for f in yaml_files:
        if not f.filename:
            continue
        yaml_text = f.file.read().decode("utf-8", errors="replace")
        try:
            data = parse_device_type_yaml(yaml_text)
        except ValueError as exc:
            return error_page(f"Invalid device-type YAML '{f.filename}': {exc}")
        rows.append({
            "name": f"{data['manufacturer']} {data['model']}",
            "source": "upload",
            "path": f.filename,
            "yaml_text": yaml_text,
        })

    if not rows:
        return error_page("Select at least one device type from the library or upload a YAML file")

    job = Job(
        id=uuid.uuid4(),
        name=job_name.strip() or f"Device type import ({len(rows)})",
        file_type="device_types",
        status="pending",
        total_records=len(rows),
        netbox_url=resolved_url,
        netbox_token=resolved_token,
    )
    db.add(job)
    db.flush()
    for i, row in enumerate(rows, start=1):
        db.add(Record(job_id=job.id, row_number=i, raw_data=row))
    db.commit()

    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
