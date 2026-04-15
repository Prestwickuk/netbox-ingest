import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://netbox_ingest:netbox_ingest@localhost:5433/netbox_ingest",
)

BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))
WORKER_POLL_INTERVAL = int(os.environ.get("WORKER_POLL_INTERVAL", "5"))
