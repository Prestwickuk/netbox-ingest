import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.netbox.duplicate import check_device
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

FACE_MAP = {"front": "front", "rear": "rear"}


class NetworkDeviceStage(BaseStage):
    REQUIRED_FIELDS = ["name", "site", "manufacturer", "device_type", "device_role", "status"]

    def process(self, session: Session, record: Record) -> None:
        data = record.raw_data
        existing_id = check_device(self.client, data.get("name", ""), data.get("site", ""))
        if existing_id:
            url = f"{self.client.netbox_url}/dcim/devices/{existing_id}/"
            self.skip(session, record, existing_id, url)
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

        # Manufacturer
        manufacturer = self.client.nb.dcim.manufacturers.get(name=data["manufacturer"])
        if not manufacturer:
            raise ValueError(f"Manufacturer '{data['manufacturer']}' not found in NetBox")

        # Device type
        device_type = self.client.nb.dcim.device_types.get(
            model=data["device_type"], manufacturer_id=manufacturer.id
        )
        if not device_type:
            raise ValueError(
                f"Device type '{data['device_type']}' for manufacturer "
                f"'{data['manufacturer']}' not found in NetBox"
            )
        self.log_info(session, record, f"Resolved device type: {device_type.model} (id={device_type.id})")

        # Device role
        role = (
            self.client.nb.dcim.device_roles.get(name=data["device_role"])
            or self.client.nb.dcim.device_roles.get(slug=data["device_role"])
        )
        if not role:
            raise ValueError(f"Device role '{data['device_role']}' not found in NetBox")

        payload: dict = {
            "name": data["name"],
            "site": site.id,
            "device_type": device_type.id,
            "role": role.id,
            "status": data["status"],
        }

        # Optional rack mounting
        if data.get("rack"):
            rack = self.client.nb.dcim.racks.get(name=data["rack"], site_id=site.id)
            if not rack:
                raise ValueError(f"Rack '{data['rack']}' not found in site '{data['site']}'")
            payload["rack"] = rack.id
            self.log_info(session, record, f"Resolved rack: {rack.name} (id={rack.id})")

            if data.get("position_u"):
                payload["position"] = int(data["position_u"])

            if data.get("face"):
                face = FACE_MAP.get(data["face"].lower())
                if face is None:
                    raise ValueError(f"Invalid face '{data['face']}' — must be 'front' or 'rear'")
                payload["face"] = face

        # Optional platform
        if data.get("platform"):
            platform = (
                self.client.nb.dcim.platforms.get(name=data["platform"])
                or self.client.nb.dcim.platforms.get(slug=data["platform"])
            )
            if platform:
                payload["platform"] = platform.id
            else:
                self.log_info(session, record, f"Platform '{data['platform']}' not found, skipping")

        for field in ("serial", "asset_tag"):
            if data.get(field):
                payload[field] = data[field]

        device = self.client.nb.dcim.devices.create(**payload)
        self.log_info(session, record, f"Created device id={device.id}")
        return device.id, f"{self.client.netbox_url}/dcim/devices/{device.id}/"
