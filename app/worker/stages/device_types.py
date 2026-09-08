import logging

import yaml
from sqlalchemy.orm import Session

from app.devicetype_library import slugify
from app.models.db import Record
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

# Device-type fields copied straight from the library YAML into the NetBox payload
DEVICE_TYPE_FIELDS = [
    "model",
    "slug",
    "part_number",
    "u_height",
    "is_full_depth",
    "airflow",
    "weight",
    "weight_unit",
    "subdevice_role",
    "description",
    "comments",
]

# Component template specs, in creation order. Power ports must precede power
# outlets and rear ports must precede front ports, because outlets/front ports
# reference them by name.
# (yaml_key, pynetbox dcim endpoint, passthrough fields, name-reference fields)
COMPONENT_SPECS = [
    ("console-ports", "console_port_templates", ["name", "label", "type", "description"], {}),
    ("console-server-ports", "console_server_port_templates", ["name", "label", "type", "description"], {}),
    ("power-ports", "power_port_templates", ["name", "label", "type", "maximum_draw", "allocated_draw", "description"], {}),
    ("rear-ports", "rear_port_templates", ["name", "label", "type", "color", "positions", "description"], {}),
    ("front-ports", "front_port_templates", ["name", "label", "type", "color", "rear_port_position", "description"], {"rear_port": "rear_port_templates"}),
    ("power-outlets", "power_outlet_templates", ["name", "label", "type", "feed_leg", "description"], {"power_port": "power_port_templates"}),
    ("interfaces", "interface_templates", ["name", "label", "type", "mgmt_only", "enabled", "poe_mode", "poe_type", "description"], {}),
    ("module-bays", "module_bay_templates", ["name", "label", "position", "description"], {}),
    ("device-bays", "device_bay_templates", ["name", "label", "description"], {}),
    ("inventory-items", "inventory_item_templates", ["name", "label", "part_id", "description"], {"manufacturer": "manufacturers"}),
]


def parse_device_type_yaml(yaml_text: str) -> dict:
    """Parse and minimally validate a devicetype-library YAML definition."""
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}")
    if not isinstance(data, dict):
        raise ValueError("Invalid device-type YAML: expected a mapping at the top level")
    missing = [f for f in ("manufacturer", "model") if not data.get(f)]
    if missing:
        raise ValueError(f"Device-type YAML missing required fields: {', '.join(missing)}")
    return data


def build_device_type_payload(data: dict, manufacturer_id: int) -> dict:
    payload: dict = {"manufacturer": manufacturer_id}
    for field in DEVICE_TYPE_FIELDS:
        if data.get(field) is not None:
            payload[field] = data[field]
    if not payload.get("slug"):
        payload["slug"] = slugify(f"{data['manufacturer']} {data['model']}")
    return payload


def build_component_payloads(data: dict) -> list[tuple[str, str, list[dict], dict]]:
    """Build per-endpoint payload lists from the YAML, leaving name refs unresolved.

    Returns [(yaml_key, endpoint, payloads, ref_fields)] in creation order.
    """
    result = []
    for yaml_key, endpoint, fields, ref_fields in COMPONENT_SPECS:
        items = data.get(yaml_key) or []
        if not isinstance(items, list):
            raise ValueError(f"Invalid device-type YAML: '{yaml_key}' must be a list")
        payloads = []
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError(f"Invalid entry in '{yaml_key}': every item needs a 'name'")
            payload = {f: item[f] for f in fields if item.get(f) is not None}
            for ref_field in ref_fields:
                if item.get(ref_field) is not None:
                    payload[ref_field] = item[ref_field]
            payloads.append(payload)
        if payloads:
            result.append((yaml_key, endpoint, payloads, ref_fields))
    return result


class DeviceTypeStage(BaseStage):
    REQUIRED_FIELDS = ["yaml_text"]

    def process(self, session: Session, record: Record) -> None:
        existing = None
        complete = False
        try:
            data = parse_device_type_yaml(record.raw_data.get("yaml_text", ""))
            existing = self._find_existing(data)
            complete = existing is not None and not self._missing_components(existing, data)
        except Exception:
            # Fall through to super().process(): create() hits the same error
            # inside the per-record handler, so the record fails instead of the job.
            pass

        if complete:
            url = f"{self.client.netbox_url}/dcim/device-types/{existing.id}/"
            self.skip(session, record, existing.id, url)
            return
        super().process(session, record)

    def create(self, session: Session, record: Record) -> tuple[int, str]:
        data = parse_device_type_yaml(record.raw_data["yaml_text"])

        manufacturer = self._ensure_manufacturer(session, record, data["manufacturer"])

        device_type = self._find_existing(data, manufacturer_id=manufacturer.id)
        if device_type:
            self.log_info(session, record, f"Device type already exists (id={device_type.id}), creating missing templates only")
        else:
            payload = build_device_type_payload(data, manufacturer.id)
            device_type = self.client.nb.dcim.device_types.create(**payload)
            self.log_info(session, record, f"Created device type '{data['model']}' (id={device_type.id})")

        # Track created/existing template ids so name refs (front->rear port,
        # outlet->power port) resolve without extra lookups.
        template_ids: dict[str, dict[str, int]] = {}

        for yaml_key, endpoint, payloads, ref_fields in build_component_payloads(data):
            api = getattr(self.client.nb.dcim, endpoint)
            existing_templates = {t.name: t.id for t in api.filter(devicetype_id=device_type.id)}
            template_ids[endpoint] = dict(existing_templates)

            to_create = []
            for payload in payloads:
                if payload["name"] in existing_templates:
                    continue
                resolved = dict(payload, device_type=device_type.id)
                for ref_field, ref_endpoint in ref_fields.items():
                    if ref_field not in resolved:
                        continue
                    resolved[ref_field] = self._resolve_ref(
                        session, record, yaml_key, ref_field, ref_endpoint,
                        resolved[ref_field], template_ids,
                    )
                to_create.append(resolved)

            if to_create:
                created = api.create(to_create)
                for obj in created:
                    template_ids[endpoint][obj.name] = obj.id
                self.log_info(session, record, f"Created {len(to_create)} {yaml_key} template(s)")
            skipped = len(payloads) - len(to_create)
            if skipped:
                self.log_info(session, record, f"Skipped {skipped} existing {yaml_key} template(s)")

        return device_type.id, f"{self.client.netbox_url}/dcim/device-types/{device_type.id}/"

    def _find_existing(self, data: dict, manufacturer_id: int | None = None):
        """Look up the device type scoped to its manufacturer.

        NetBox slugs and models are only unique per manufacturer, so an
        unscoped slug lookup could match (and later mutate) another
        manufacturer's device type.
        """
        if manufacturer_id is None:
            manufacturer = self.client.nb.dcim.manufacturers.get(name=data["manufacturer"])
            if not manufacturer:
                return None
            manufacturer_id = manufacturer.id
        slug = data.get("slug") or slugify(f"{data['manufacturer']} {data['model']}")
        return (
            self.client.nb.dcim.device_types.get(slug=slug, manufacturer_id=manufacturer_id)
            or self.client.nb.dcim.device_types.get(model=data["model"], manufacturer_id=manufacturer_id)
        )

    def _missing_components(self, device_type, data: dict) -> bool:
        """True if any template named in the YAML does not exist on the device type yet."""
        for _, endpoint, payloads, _ in build_component_payloads(data):
            api = getattr(self.client.nb.dcim, endpoint)
            existing = {t.name for t in api.filter(devicetype_id=device_type.id)}
            if any(p["name"] not in existing for p in payloads):
                return True
        return False

    def _ensure_manufacturer(self, session: Session, record: Record, name: str):
        manufacturer = self.client.nb.dcim.manufacturers.get(name=name)
        if not manufacturer:
            manufacturer = self.client.nb.dcim.manufacturers.create(name=name, slug=slugify(name))
            self.log_info(session, record, f"Created manufacturer '{name}' (id={manufacturer.id})")
        return manufacturer

    def _resolve_ref(self, session: Session, record: Record, yaml_key: str,
                     ref_field: str, ref_endpoint: str, ref_name: str,
                     template_ids: dict) -> int:
        if ref_endpoint == "manufacturers":
            return self._ensure_manufacturer(session, record, ref_name).id
        ref_id = template_ids.get(ref_endpoint, {}).get(ref_name)
        if ref_id is None:
            raise ValueError(
                f"'{yaml_key}' entry references {ref_field} '{ref_name}' which is not defined in the YAML"
            )
        return ref_id
