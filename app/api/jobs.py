import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db, SessionLocal
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


@router.get("/jobs/{job_id}/stream")
async def job_stream(job_id: uuid.UUID):
    """SSE endpoint — pushes progress updates once per second until the job finishes."""
    async def event_generator():
        DONE_STATUSES = {"completed", "failed", "cancelled"}
        while True:
            with SessionLocal() as db:
                job = db.get(Job, job_id)
            if not job:
                break

            payload = json.dumps({
                "status": job.status,
                "progress_pct": job.progress_pct,
                "processed_count": job.processed_count,
                "total_records": job.total_records or 0,
                "success_count": job.success_count,
                "failed_count": job.failed_count,
                "skipped_count": job.skipped_count,
            })
            yield f"data: {payload}\n\n"

            if job.status in DONE_STATUSES:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/jobs/{job_id}/cancel", response_class=HTMLResponse)
def cancel_job(request: Request, job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status in ("pending", "running"):
        job.status = "cancelled"
        db.execute(
            Record.__table__.update()
            .where(Record.job_id == job_id, Record.status == "pending")
            .values(status="skipped")
        )
        db.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/retry", response_class=HTMLResponse)
def retry_job(request: Request, job_id: uuid.UUID, db: Session = Depends(get_db)):
    """Reset all failed records to pending and requeue the job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in ("completed", "failed", "cancelled"):
        raise HTTPException(400, "Only completed, failed, or cancelled jobs can be retried")

    failed_count = db.execute(
        select(Record).where(Record.job_id == job_id, Record.status == "failed")
    ).scalars().all()

    if not failed_count:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    db.execute(
        Record.__table__.update()
        .where(Record.job_id == job_id, Record.status == "failed")
        .values(status="pending", error_message=None, processed_at=None)
    )
    job.status = "pending"
    job.failed_count = 0
    job.completed_at = None
    db.commit()

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)
