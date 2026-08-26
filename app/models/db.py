import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, JSON, Index, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NetBoxInstance(Base):
    __tablename__ = "netbox_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    _token: Mapped[str] = mapped_column("token", Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def token(self) -> str:
        from app.security.tokens import decrypt_token

        return decrypt_token(self._token)

    @token.setter
    def token(self, value: str) -> None:
        from app.security.tokens import encrypt_token

        self._token = encrypt_token(value)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending | running | completed | failed | cancelled
    total_records: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    netbox_url: Mapped[str] = mapped_column(String(500))
    _netbox_token: Mapped[str] = mapped_column("netbox_token", Text)
    batch_size: Mapped[int | None] = mapped_column(Integer, nullable=True)   # overrides env BATCH_SIZE if set
    rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)   # max records/sec per worker (0/None = unlimited)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)   # populated when job fails before/outside record processing
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    records: Mapped[list["Record"]] = relationship("Record", back_populates="job", cascade="all, delete-orphan")

    @property
    def netbox_token(self) -> str:
        from app.security.tokens import decrypt_token

        return decrypt_token(self._netbox_token)

    @netbox_token.setter
    def netbox_token(self, value: str) -> None:
        from app.security.tokens import encrypt_token

        self._netbox_token = encrypt_token(value)

    @property
    def processed_count(self) -> int:
        return self.success_count + self.failed_count + self.skipped_count

    @property
    def progress_pct(self) -> int:
        if not self.total_records:
            return 0
        return int(self.processed_count / self.total_records * 100)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        Index("ix_records_job_status", "job_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending | processing | success | failed | skipped
    raw_data: Mapped[dict] = mapped_column(JSON)
    netbox_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    netbox_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped["Job"] = relationship("Job", back_populates="records")
    logs: Mapped[list["RecordLog"]] = relationship("RecordLog", back_populates="record", cascade="all, delete-orphan", order_by="RecordLog.created_at")

    @property
    def display_name(self) -> str:
        return self.raw_data.get("name", f"Row {self.row_number}")


class RecordLog(Base):
    __tablename__ = "record_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("records.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(20))  # info | warning | error
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    record: Mapped["Record"] = relationship("Record", back_populates="logs")
