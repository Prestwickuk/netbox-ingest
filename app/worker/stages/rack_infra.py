import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.netbox.duplicate import check_device
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

FACE_MAP = {"front": "front", "rear": "rear"}
STATUS_DEFAULT = "planned"


class RackInfraStage(BaseStage):
    REQUIRED_FIELDS = ["name", "site", "rack", "position_u", "face", "manufacturer", "device_type", "role"]

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

        # Rack (scoped to site)
        rack = self.client.nb.dcim.racks.get(name=data["rack"], site_id=site.id)
        if not rack:
            raise ValueError(f"Rack '{data['rack']}' not found in site '{data['site']}'")
        self.log_info(session, record, f"Resolved rack: {rack.name} (id={rack.id})")

        # Manufacturer
        manufacturer = self.client.nb.dcim.manufacturers.get(name=data["manufacturer"])
        if not manufacturer:
            raise ValueError(f"Manufacturer '{data['manufacturer']}' not found in NetBox")

        # Device type (must belong to this manufacturer)
        device_type = self.client.nb.dcim.device_types.get(
            model=data["device_type"], manufacturer_id=manufacturer.id
        )
        if not device_type:
            raise ValueError(
                f"Device type '{data['device_type']}' for manufacturer "
                f"'{data['manufacturer']}' not found in NetBox"
            )
        self.log_info(session, record, f"Resolved device type: {device_type.model} (id={device_type.id})")

        # Face
        face = FACE_MAP.get(data["face"].lower())
        if face is None:
            raise ValueError(f"Invalid face '{data['face']}' — must be 'front' or 'rear'")

        # Device role
        role = (
            self.client.nb.dcim.device_roles.get(name=data["role"])
            or self.client.nb.dcim.device_roles.get(slug=data["role"])
        )
        if not role:
            raise ValueError(f"Device role '{data['role']}' not found in NetBox")

        payload: dict = {
            "name": data["name"],
            "site": site.id,
            "rack": rack.id,
            "position": int(data["position_u"]),
            "face": face,
            "device_type": device_type.id,
            "role": role.id,
            "status": data.get("status") or STATUS_DEFAULT,
        }

        device = self.client.nb.dcim.devices.create(**payload)
        self.log_info(session, record, f"Created device id={device.id}")
        return device.id, f"{self.client.netbox_url}/dcim/devices/{device.id}/"
