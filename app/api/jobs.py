import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models.db import Job, Record

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    jobs = db.execute(
        select(Job).order_by(Job.created_at.desc())
    ).scalars().all()
    return templates.TemplateResponse("dashboard.html", {"request": request, "jobs": jobs})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    job_id: uuid.UUID,
    status_filter: str = Query(default="all"),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    q = select(Record).where(Record.job_id == job_id).order_by(Record.row_number)
    if status_filter != "all":
        q = q.where(Record.status == status_filter)
    q = q.options(selectinload(Record.logs))

    records = db.execute(q).scalars().all()
    return templates.TemplateResponse("job_detail.html", {
        "request": request,
        "job": job,
        "records": records,
        "status_filter": status_filter,
    })


@router.get("/jobs/{job_id}/progress", response_class=HTMLResponse)
def job_progress(request: Request, job_id: uuid.UUID, db: Session = Depends(get_db)):
    """HTMX partial — returns just the progress bar + stats block."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return templates.TemplateResponse("partials/progress.html", {"request": request, "job": job})


@router.post("/jobs/{job_id}/cancel", response_class=HTMLResponse)
def cancel_job(request: Request, job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status in ("pending", "running"):
        job.status = "cancelled"
        # Mark all pending records as skipped
        db.execute(
            Record.__table__.update()
            .where(Record.job_id == job_id, Record.status == "pending")
            .values(status="skipped")
        )
        db.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
