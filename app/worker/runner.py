"""Worker process — run with: python -m app.worker.runner"""
import logging
import time
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import BATCH_SIZE, WORKER_POLL_INTERVAL
from app.database import SessionLocal
from app.models.db import Job, Record
from app.worker.stages.racks import RackStage
from app.worker.stages.rack_infra import RackInfraStage
from app.worker.stages.patch_panels import PatchPanelStage
from app.worker.stages.network_devices import NetworkDeviceStage
from app.worker.stages.servers import ServerStage
from app.worker.stages.power_panels import PowerPanelStage
from app.worker.stages.power_feeds import PowerFeedStage
from app.worker.stages.cables import CableStage
from app.worker.stages.ip_assignment import IPAssignmentStage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

STAGE_MAP = {
    "racks": RackStage,
    "rack_infra": RackInfraStage,
    "patch_panels": PatchPanelStage,
    "network_devices": NetworkDeviceStage,
    "servers": ServerStage,
    "power_panels": PowerPanelStage,
    "power_feeds": PowerFeedStage,
    "cables": CableStage,
    "ip_assignment": IPAssignmentStage,
}


def claim_batch(session: Session, job_id) -> list[Record]:
    result = session.execute(
        select(Record)
        .where(Record.job_id == job_id, Record.status == "pending")
        .order_by(Record.row_number)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


def process_job(job: Job) -> None:
    stage_cls = STAGE_MAP.get(job.file_type)
    if not stage_cls:
        log.error(f"No stage registered for file_type='{job.file_type}', skipping job {job.id}")
        with SessionLocal() as s:
            j = s.get(Job, job.id)
            j.status = "failed"
            s.commit()
        return

    log.info(f"Starting job {job.id} ({job.file_type}), {job.total_records} records")
    stage = stage_cls(job.netbox_url, job.netbox_token)

    with SessionLocal() as s:
        j = s.get(Job, job.id)
        j.status = "running"
        j.started_at = datetime.utcnow()
        s.commit()

    processed = 0
    while True:
        with SessionLocal() as s:
            batch = claim_batch(s, job.id)
            if not batch:
                break
            for record in batch:
                stage.process(s, record)
                processed += 1
            s.commit()
        log.info(f"Job {job.id}: {processed}/{job.total_records} processed")

    with SessionLocal() as s:
        j = s.get(Job, job.id)
        j.status = "completed"
        j.completed_at = datetime.utcnow()
        s.commit()
        log.info(f"Job {job.id} completed — success={j.success_count} failed={j.failed_count} skipped={j.skipped_count}")


def run() -> None:
    log.info("Worker started")
    while True:
        with SessionLocal() as s:
            job = s.execute(
                select(Job).where(Job.status == "pending").limit(1)
            ).scalar_one_or_none()

        if job:
            try:
                process_job(job)
            except Exception as exc:
                log.exception(f"Unhandled error processing job {job.id}: {exc}")
                with SessionLocal() as s:
                    j = s.get(Job, job.id)
                    if j:
                        j.status = "failed"
                        s.commit()
        else:
            time.sleep(WORKER_POLL_INTERVAL)


if __name__ == "__main__":
    run()
