import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.netbox.duplicate import check_rack
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

WIDTH_MAP = {"19": 19, "23": 23, "21": 21}


class RackStage(BaseStage):
    REQUIRED_FIELDS = ["name", "site", "u_height"]

    def process(self, session: Session, record: Record) -> None:
        data = record.raw_data
        existing_id = check_rack(self.client, data.get("name", ""), data.get("site", ""))
        if existing_id:
            url = f"{self.client.netbox_url}/dcim/racks/{existing_id}/"
            self.skip(session, record, existing_id, url)
            return
        super().process(session, record)

    def create(self, session: Session, record: Record) -> tuple[int, str]:
        data = record.raw_data

        # Resolve site (try name then slug)
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
            "u_height": int(data["u_height"]),
        }

        if data.get("location"):
            loc = self.client.nb.dcim.locations.get(site_id=site.id, name=data["location"])
            if loc:
                payload["location"] = loc.id
            else:
                self.log_info(session, record, f"Location '{data['location']}' not found, skipping")

        if data.get("rack_role"):
            role = self.client.nb.dcim.rack_roles.get(name=data["rack_role"])
            if role:
                payload["role"] = role.id
            else:
                self.log_info(session, record, f"Rack role '{data['rack_role']}' not found, skipping")

        if data.get("width_inches"):
            key = str(int(float(data["width_inches"])))
            if key in WIDTH_MAP:
                payload["width"] = WIDTH_MAP[key]

        for field in ("serial", "asset_tag", "comments"):
            if data.get(field):
                payload[field] = data[field]

        rack = self.client.nb.dcim.racks.create(**payload)
        self.log_info(session, record, f"Created rack id={rack.id}")
        return rack.id, f"{self.client.netbox_url}/dcim/racks/{rack.id}/"
