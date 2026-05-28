import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

STATUS_DEFAULT = "planned"

# Maps CSV termination_type → (pynetbox endpoint attr, NetBox object_type string)
TERMINATION_MAP = {
    "interface":    ("dcim.interfaces",    "dcim.interface"),
    "front_port":   ("dcim.front_ports",   "dcim.frontport"),
    "rear_port":    ("dcim.rear_ports",    "dcim.rearport"),
    "power_port":   ("dcim.power_ports",   "dcim.powerport"),
    "power_outlet": ("dcim.power_outlets", "dcim.poweroutlet"),
}


class CableStage(BaseStage):
    REQUIRED_FIELDS = [
        "a_device", "a_site", "a_termination_type", "a_termination_name",
        "b_device", "b_site", "b_termination_type", "b_termination_name",
    ]

    def process(self, session: Session, record: Record) -> None:
        data = record.raw_data

        # Duplicate check: is termination A already cabled?
        # Use .get() so a missing column doesn't KeyError out of the try and kill the job;
        # super().process() runs REQUIRED_FIELDS validation and fails the record cleanly.
        try:
            term_a = self._resolve_termination(
                data.get("a_device", ""), data.get("a_site", ""),
                data.get("a_termination_type", ""), data.get("a_termination_name", ""),
            )
            if term_a and getattr(term_a, "cable", None):
                existing_id = term_a.cable.id
                url = f"{self.client.netbox_url}/dcim/cables/{existing_id}/"
                self.skip(session, record, existing_id, url)
                return
        except ValueError:
            pass  # let create() produce the proper error message

        super().process(session, record)

    def create(self, session: Session, record: Record) -> tuple[int, str]:
        data = record.raw_data

        term_a = self._resolve_termination(
            data["a_device"], data["a_site"],
            data["a_termination_type"], data["a_termination_name"],
        )
        self.log_info(session, record,
            f"Resolved A: {data['a_device']} / {data['a_termination_name']} (id={term_a.id})")

        term_b = self._resolve_termination(
            data["b_device"], data["b_site"],
            data["b_termination_type"], data["b_termination_name"],
        )
        self.log_info(session, record,
            f"Resolved B: {data['b_device']} / {data['b_termination_name']} (id={term_b.id})")

        _, object_type_a = TERMINATION_MAP[data["a_termination_type"]]
        _, object_type_b = TERMINATION_MAP[data["b_termination_type"]]

        payload: dict = {
            "a_terminations": [{"object_type": object_type_a, "object_id": term_a.id}],
            "b_terminations": [{"object_type": object_type_b, "object_id": term_b.id}],
            "status": data.get("status") or STATUS_DEFAULT,
        }

        for field in ("label", "cable_type", "color"):
            if data.get(field):
                payload[field] = data[field]

        cable = self.client.nb.dcim.cables.create(**payload)
        self.log_info(session, record, f"Created cable id={cable.id}")
        return cable.id, f"{self.client.netbox_url}/dcim/cables/{cable.id}/"

    def _resolve_termination(self, device_name: str, site_name: str, term_type: str, term_name: str):
        if term_type not in TERMINATION_MAP:
            raise ValueError(
                f"Invalid termination type '{term_type}' — "
                f"valid options: {', '.join(TERMINATION_MAP)}"
            )

        site = (
            self.client.nb.dcim.sites.get(name=site_name)
            or self.client.nb.dcim.sites.get(slug=site_name)
        )
        if not site:
            raise ValueError(f"Site '{site_name}' not found in NetBox")

        device = self.client.nb.dcim.devices.get(name=device_name, site_id=site.id)
        if not device:
            raise ValueError(f"Device '{device_name}' not found in site '{site_name}'")

        endpoint_attr, _ = TERMINATION_MAP[term_type]
        # Traverse dotted endpoint path (e.g. "dcim.interfaces" → client.nb.dcim.interfaces)
        ep = self.client.nb
        for part in endpoint_attr.split("."):
            ep = getattr(ep, part)

        termination = ep.get(name=term_name, device_id=device.id)
        if not termination:
            raise ValueError(
                f"'{term_type}' named '{term_name}' not found on device '{device_name}'"
            )
        return termination
