import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import Job, NetBoxInstance, Record
from app.parsers.csv_parser import parse_csv
from app.parsers.json_parser import parse_json
from app.templates_config import templates

router = APIRouter()

VALID_FILE_TYPES = [
    "racks",
    "rack_infra",
    "patch_panels",
    "network_devices",
    "servers",
    "power_panels",
    "power_feeds",
    "cables",
    "ip_assignment",
]


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    instances = db.execute(
        select(NetBoxInstance).order_by(NetBoxInstance.name)
    ).scalars().all()
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "file_types": VALID_FILE_TYPES,
        "instances": instances,
    })


@router.post("/upload", response_class=HTMLResponse)
async def upload_file(
    request: Request,
    job_name: Annotated[str, Form()],
    file_type: Annotated[str, Form()],
    file: UploadFile,
    netbox_source: Annotated[str, Form()] = "custom",  # "custom" or a NetBoxInstance UUID
    netbox_url: Annotated[str, Form()] = "",
    netbox_token: Annotated[str, Form()] = "",
    batch_size: Annotated[str, Form()] = "",
    rate_limit: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    if file_type not in VALID_FILE_TYPES:
        raise HTTPException(400, f"Invalid file_type: {file_type}")

    # Resolve NetBox credentials
    if netbox_source != "custom":
        try:
            instance_id = uuid.UUID(netbox_source)
            instance = db.get(NetBoxInstance, instance_id)
            if not instance:
                raise ValueError(f"NetBox instance not found")
            resolved_url = instance.url
            resolved_token = instance.token
        except (ValueError, AttributeError) as e:
            instances = db.execute(select(NetBoxInstance).order_by(NetBoxInstance.name)).scalars().all()
            return templates.TemplateResponse("upload.html", {
                "request": request,
                "file_types": VALID_FILE_TYPES,
                "instances": instances,
                "error": str(e),
                "form": {"job_name": job_name, "file_type": file_type},
            })
    else:
        if not netbox_url or not netbox_token:
            instances = db.execute(select(NetBoxInstance).order_by(NetBoxInstance.name)).scalars().all()
            return templates.TemplateResponse("upload.html", {
                "request": request,
                "file_types": VALID_FILE_TYPES,
                "instances": instances,
                "error": "NetBox URL and token are required when using a custom connection",
                "form": {"job_name": job_name, "file_type": file_type},
            })
        resolved_url = netbox_url.rstrip("/")
        resolved_token = netbox_token

    content = await file.read()
    filename = file.filename or ""

    try:
        if filename.endswith(".json"):
            rows = parse_json(content, file_type)
        elif filename.endswith(".csv"):
            rows = parse_csv(content, file_type)
        else:
            raise ValueError("File must be .csv or .json")
    except ValueError as e:
        instances = db.execute(select(NetBoxInstance).order_by(NetBoxInstance.name)).scalars().all()
        return templates.TemplateResponse("upload.html", {
            "request": request,
            "file_types": VALID_FILE_TYPES,
            "instances": instances,
            "error": str(e),
            "form": {"job_name": job_name, "file_type": file_type, "netbox_url": netbox_url},
        })

    job = Job(
        id=uuid.uuid4(),
        name=job_name,
        file_type=file_type,
        status="pending",
        total_records=len(rows),
        netbox_url=resolved_url,
        netbox_token=resolved_token,
        batch_size=int(batch_size) if batch_size.strip() else None,
        rate_limit=int(rate_limit) if rate_limit.strip() else None,
    )
    db.add(job)
    db.flush()

    for i, row in enumerate(rows, start=1):
        db.add(Record(job_id=job.id, row_number=i, raw_data=row))

    db.commit()
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
