import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import Job, Record
from app.parsers.csv_parser import parse_csv
from app.parsers.json_parser import parse_json

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

VALID_FILE_TYPES = ["racks", "rack_infra", "patch_panels", "network_devices", "servers"]


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "file_types": VALID_FILE_TYPES,
    })


@router.post("/upload", response_class=HTMLResponse)
async def upload_file(
    request: Request,
    job_name: Annotated[str, Form()],
    file_type: Annotated[str, Form()],
    netbox_url: Annotated[str, Form()],
    netbox_token: Annotated[str, Form()],
    file: UploadFile,
    db: Session = Depends(get_db),
):
    if file_type not in VALID_FILE_TYPES:
        raise HTTPException(400, f"Invalid file_type: {file_type}")

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
        return templates.TemplateResponse("upload.html", {
            "request": request,
            "file_types": VALID_FILE_TYPES,
            "error": str(e),
            "form": {"job_name": job_name, "file_type": file_type, "netbox_url": netbox_url},
        })

    job = Job(
        id=uuid.uuid4(),
        name=job_name,
        file_type=file_type,
        status="pending",
        total_records=len(rows),
        netbox_url=netbox_url.rstrip("/"),
        netbox_token=netbox_token,
    )
    db.add(job)
    db.flush()

    for i, row in enumerate(rows, start=1):
        db.add(Record(job_id=job.id, row_number=i, raw_data=row))

    db.commit()

    # Redirect to job detail page
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
