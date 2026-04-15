import logging
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.db import Job, Record, RecordLog
from app.netbox.client import NetBoxClient

log = logging.getLogger(__name__)


class BaseStage(ABC):
    REQUIRED_FIELDS: list[str] = []

    def __init__(self, netbox_url: str, netbox_token: str):
        self.client = NetBoxClient(netbox_url, netbox_token)

    def process(self, session: Session, record: Record) -> None:
        record.status = "processing"
        session.flush()

        try:
            missing = [f for f in self.REQUIRED_FIELDS if not record.raw_data.get(f)]
            if missing:
                raise ValueError(f"Missing required fields: {', '.join(missing)}")

            netbox_id, netbox_url = self.create(session, record)

            record.status = "success"
            record.netbox_id = netbox_id
            record.netbox_url = netbox_url
            record.processed_at = datetime.utcnow()

            session.execute(
                Job.__table__.update()
                .where(Job.id == record.job_id)
                .values(success_count=Job.success_count + 1)
            )

        except Exception as exc:
            log.warning(f"Record {record.row_number} failed: {exc}")
            record.status = "failed"
            record.error_message = str(exc)
            record.processed_at = datetime.utcnow()

            session.add(RecordLog(record_id=record.id, level="error", message=str(exc)))

            session.execute(
                Job.__table__.update()
                .where(Job.id == record.job_id)
                .values(failed_count=Job.failed_count + 1)
            )

    def skip(self, session: Session, record: Record, existing_id: int, existing_url: str) -> None:
        record.status = "skipped"
        record.netbox_id = existing_id
        record.netbox_url = existing_url
        record.processed_at = datetime.utcnow()

        msg = f"Duplicate: object already exists in NetBox (id={existing_id})"
        session.add(RecordLog(record_id=record.id, level="warning", message=msg))

        session.execute(
            Job.__table__.update()
            .where(Job.id == record.job_id)
            .values(skipped_count=Job.skipped_count + 1)
        )

    def log_info(self, session: Session, record: Record, message: str) -> None:
        session.add(RecordLog(record_id=record.id, level="info", message=message))

    @abstractmethod
    def create(self, session: Session, record: Record) -> tuple[int, str]:
        """Create the NetBox object. Returns (netbox_id, netbox_url)."""
