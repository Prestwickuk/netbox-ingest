import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)


class PowerPanelStage(BaseStage):
    REQUIRED_FIELDS = ["name", "site"]

    def process(self, session: Session, record: Record) -> None:
        data = record.raw_data
        # Duplicate check: name + site
        site = (
            self.client.nb.dcim.sites.get(name=data.get("site", ""))
            or self.client.nb.dcim.sites.get(slug=data.get("site", ""))
        )
        if site:
            existing = self.client.nb.dcim.power_panels.get(name=data.get("name", ""), site_id=site.id)
            if existing:
                url = f"{self.client.netbox_url}/dcim/power-panels/{existing.id}/"
                self.skip(session, record, existing.id, url)
                return
        super().process(session, record)

    def create(self, session: Session, record: Record) -> tuple[int, str]:
        data = record.raw_data

        site = (
            self.client.nb.dcim.sites.get(name=data["site"])
            or self.client.nb.dcim.sites.get(slug=data["site"])
        )
        if not site:
            raise ValueError(f"Site '{data['site']}' not found in NetBox")
        self.log_info(session, record, f"Resolved site: {site.name} (id={site.id})")

        payload: dict = {
            "name": data["name"],
            "site": site.id,
        }

        if data.get("location"):
            loc = self.client.nb.dcim.locations.get(site_id=site.id, name=data["location"])
            if loc:
                payload["location"] = loc.id
            else:
                self.log_info(session, record, f"Location '{data['location']}' not found, skipping")

        panel = self.client.nb.dcim.power_panels.create(**payload)
        self.log_info(session, record, f"Created power panel id={panel.id}")
        return panel.id, f"{self.client.netbox_url}/dcim/power-panels/{panel.id}/"
