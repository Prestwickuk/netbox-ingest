import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

STATUS_DEFAULT = "planned"

# Maps feed_type CSV value → NetBox power feed fields
FEED_TYPE_MAP = {
    "32A-3P-230V": {"voltage": 230, "amperage": 32, "phase": "three-phase"},
    "63A-3P-230V": {"voltage": 230, "amperage": 63, "phase": "three-phase"},
}


class PowerFeedStage(BaseStage):
    REQUIRED_FIELDS = ["name", "site", "power_panel", "rack"]

    def process(self, session: Session, record: Record) -> None:
        data = record.raw_data
        # Duplicate check: name + power_panel
        site = (
            self.client.nb.dcim.sites.get(name=data.get("site", ""))
            or self.client.nb.dcim.sites.get(slug=data.get("site", ""))
        )
        if site:
            panel = self.client.nb.dcim.power_panels.get(
                name=data.get("power_panel", ""), site_id=site.id
            )
            if panel:
                existing = self.client.nb.dcim.power_feeds.get(
                    name=data.get("name", ""), power_panel_id=panel.id
                )
                if existing:
                    url = f"{self.client.netbox_url}/dcim/power-feeds/{existing.id}/"
                    self.skip(session, record, existing.id, url)
                    return
        super().process(session, record)

    def create(self, session: Session, record: Record) -> tuple[int, str]:
        data = record.raw_data

        # Site
        site = (
            self.client.nb.dcim.sites.get(name=data["site"])
            or self.client.nb.dcim.sites.get(slug=data["site"])
        )
        if not site:
            raise ValueError(f"Site '{data['site']}' not found in NetBox")
        self.log_info(session, record, f"Resolved site: {site.name} (id={site.id})")

        # Power panel
        panel = self.client.nb.dcim.power_panels.get(name=data["power_panel"], site_id=site.id)
        if not panel:
            raise ValueError(f"Power panel '{data['power_panel']}' not found in site '{data['site']}'")
        self.log_info(session, record, f"Resolved power panel: {panel.name} (id={panel.id})")

        # Rack
        rack = self.client.nb.dcim.racks.get(name=data["rack"], site_id=site.id)
        if not rack:
            raise ValueError(f"Rack '{data['rack']}' not found in site '{data['site']}'")
        self.log_info(session, record, f"Resolved rack: {rack.name} (id={rack.id})")

        payload: dict = {
            "name": data["name"],
            "power_panel": panel.id,
            "rack": rack.id,
            "status": data.get("status") or STATUS_DEFAULT,
        }

        # Feed type → electrical specs
        if data.get("feed_type"):
            specs = FEED_TYPE_MAP.get(data["feed_type"])
            if not specs:
                raise ValueError(
                    f"Unknown feed_type '{data['feed_type']}' — "
                    f"valid options: {', '.join(FEED_TYPE_MAP)}"
                )
            payload.update(specs)
            self.log_info(session, record, f"Applied feed type '{data['feed_type']}': {specs}")

        feed = self.client.nb.dcim.power_feeds.create(**payload)
        self.log_info(session, record, f"Created power feed id={feed.id}")
        return feed.id, f"{self.client.netbox_url}/dcim/power-feeds/{feed.id}/"
