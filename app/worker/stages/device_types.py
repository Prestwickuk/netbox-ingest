import logging
import re

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

# Module-type fields — NetBox module types carry no slug, u_height or
# full-depth flag; they are identified by manufacturer + model alone.
# The YAML's profile name and attribute_data are handled separately: the
# profile is resolved by name at create time, and the profile attributes go
# through the API field 'attributes' — the REST serializer does not expose
# the YAML/ORM name 'attribute_data' at all, and unknown fields are
# silently dropped rather than rejected.
MODULE_TYPE_FIELDS = [
    "model",
    "part_number",
    "airflow",
    "weight",
    "weight_unit",
    "description",
    "comments",
]

KIND_DEVICE_TYPE = "device_type"
KIND_MODULE_TYPE = "module_type"

# YAML keys that only ever appear on device-type definitions (the library's
# module-type schema has none of these); used to autodetect uploaded files.
DEVICE_ONLY_KEYS = (
    "slug", "u_height", "is_full_depth", "subdevice_role",
    "front_image", "rear_image", "device-bays", "inventory-items",
)

# Component templates NetBox supports on module types (device bays and
# inventory items exist only on device types).
MODULE_COMPONENT_KEYS = {
    "console-ports", "console-server-ports", "power-ports", "power-outlets",
    "interfaces", "front-ports", "rear-ports", "module-bays",
}

# Per-kind wiring: which NetBox endpoint owns the type object, the FK field
# and filter param its templates use, and how result URLs are built.
OWNERS = {
    KIND_DEVICE_TYPE: {
        "endpoint": "device_types",
        "fk": "device_type",
        "filter": "device_type_id",
        "url_path": "device-types",
        "fields": DEVICE_TYPE_FIELDS,
        "label": "device type",
    },
    KIND_MODULE_TYPE: {
        "endpoint": "module_types",
        "fk": "module_type",
        "filter": "module_type_id",
        "url_path": "module-types",
        "fields": MODULE_TYPE_FIELDS,
        "label": "module type",
    },
}

# Component template specs, in creation order. Power ports must precede power
# outlets and rear ports must precede front ports, because outlets/front ports
# reference them by name.
# (yaml_key, pynetbox dcim endpoint, passthrough fields, name-reference fields)
COMPONENT_SPECS = [
    ("console-ports", "console_port_templates", ["name", "label", "type", "description"], {}),
    ("console-server-ports", "console_server_port_templates", ["name", "label", "type", "description"], {}),
    ("power-ports", "power_port_templates", ["name", "label", "type", "maximum_draw", "allocated_draw", "description"], {}),
    ("rear-ports", "rear_port_templates", ["name", "label", "type", "color", "positions", "description"], {}),
    ("front-ports", "front_port_templates", ["name", "label", "type", "color", "positions", "description"], {}),
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


def detect_definition_kind(data: dict) -> str:
    """Classify a parsed library YAML as a device type or a module type.

    Library device-type definitions always carry a slug (their schema requires
    it) and usually u_height; module-type definitions carry none of the
    device-only keys.
    """
    if any(data.get(key) is not None for key in DEVICE_ONLY_KEYS):
        return KIND_DEVICE_TYPE
    return KIND_MODULE_TYPE


def build_type_payload(data: dict, manufacturer_id: int, kind: str = KIND_DEVICE_TYPE) -> dict:
    payload: dict = {"manufacturer": manufacturer_id}
    for field in OWNERS[kind]["fields"]:
        if data.get(field) is not None:
            payload[field] = data[field]
    if kind == KIND_DEVICE_TYPE and not payload.get("slug"):
        payload["slug"] = slugify(f"{data['manufacturer']} {data['model']}")
    return payload


def build_device_type_payload(data: dict, manufacturer_id: int) -> dict:
    return build_type_payload(data, manufacturer_id, KIND_DEVICE_TYPE)


def build_component_payloads(data: dict, kind: str = KIND_DEVICE_TYPE) -> list[tuple[str, str, list[dict], dict]]:
    """Build per-endpoint payload lists from the YAML, leaving name refs unresolved.

    Returns [(yaml_key, endpoint, payloads, ref_fields)] in creation order.
    """
    if kind == KIND_MODULE_TYPE:
        unsupported = sorted(
            yaml_key for yaml_key, _, _, _ in COMPONENT_SPECS
            if data.get(yaml_key) and yaml_key not in MODULE_COMPONENT_KEYS
        )
        if unsupported:
            raise ValueError(
                f"Module types do not support these component sections: {', '.join(unsupported)}"
            )
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


def build_port_mappings(data: dict) -> dict[str, list[dict]]:
    """Collect front-to-rear port mappings keyed by front port name.

    Reads the top-level 'port-mappings' list (the devicetype-library format
    matching NetBox 4.5's PortMapping model) and, for backwards compatibility
    with pre-4.5 YAML, inline 'rear_port'/'rear_port_position' fields on
    front-ports entries. Rear ports stay as names; the stage resolves them to
    template ids at create time.
    """
    front_names = {i.get("name") for i in (data.get("front-ports") or []) if isinstance(i, dict)}
    mappings: dict[str, list[dict]] = {}

    def add(source: str, front_port, front_position, rear_port, rear_position) -> None:
        if front_port not in front_names:
            raise ValueError(f"{source} references front port '{front_port}' which is not defined in the YAML")
        mappings.setdefault(front_port, []).append({
            "position": int(front_position),
            "rear_port": rear_port,
            "rear_port_position": int(rear_position),
        })

    for item in data.get("front-ports") or []:
        if isinstance(item, dict) and item.get("rear_port"):
            add("'front-ports' entry", item.get("name"), 1,
                item["rear_port"], item.get("rear_port_position") or 1)

    entries = data.get("port-mappings") or []
    if not isinstance(entries, list):
        raise ValueError("Invalid device-type YAML: 'port-mappings' must be a list")
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("front_port") or not entry.get("rear_port"):
            raise ValueError("Invalid 'port-mappings' entry: 'front_port' and 'rear_port' are required")
        add("'port-mappings' entry", entry["front_port"], entry.get("front_port_position") or 1,
            entry["rear_port"], entry.get("rear_port_position") or 1)

    return mappings


class DeviceTypeStage(BaseStage):
    REQUIRED_FIELDS = ["yaml_text"]

    # NetBox 4.5 replaced FrontPort(Template).rear_port/rear_port_position with
    # PortMapping, exposed as a 'rear_ports' list on front ports. Detected once
    # per job so older instances still get the legacy inline format.
    _supports_port_mappings: bool | None = None
    _netbox_version: str = "unknown"

    @staticmethod
    def _record_kind(record: Record, data: dict) -> str:
        """The record's declared kind, set at import time.

        Records queued before module-type support carry no kind and were
        always device types — including custom YAML without slug/u_height —
        so a missing kind means device type. Never autodetect here: retrying
        an old record must resume the existing device type, not create a
        module type. New imports always persist their kind.
        """
        kind = (record.raw_data or {}).get("kind")
        return kind if kind in OWNERS else KIND_DEVICE_TYPE

    def process(self, session: Session, record: Record) -> None:
        existing = None
        complete = False
        kind = KIND_DEVICE_TYPE
        try:
            data = parse_device_type_yaml(record.raw_data.get("yaml_text", ""))
            kind = self._record_kind(record, data)
            existing = self._find_existing(data, kind)
            complete = existing is not None and not self._missing_components(existing, data, kind)
        except Exception:
            # Fall through to super().process(): create() hits the same error
            # inside the per-record handler, so the record fails instead of the job.
            pass

        if complete:
            url = f"{self.client.netbox_url}/dcim/{OWNERS[kind]['url_path']}/{existing.id}/"
            self.skip(session, record, existing.id, url)
            return
        super().process(session, record)

    def create(self, session: Session, record: Record) -> tuple[int, str]:
        data = parse_device_type_yaml(record.raw_data["yaml_text"])
        kind = self._record_kind(record, data)
        owner = OWNERS[kind]

        manufacturer = self._ensure_manufacturer(session, record, data["manufacturer"])

        owner_api = getattr(self.client.nb.dcim, owner["endpoint"])
        profile_id = self._resolve_module_profile(session, record, data) if kind == KIND_MODULE_TYPE else None
        type_obj = self._find_existing(data, kind, manufacturer_id=manufacturer.id)
        if type_obj:
            self.log_info(session, record, f"{owner['label'].capitalize()} already exists (id={type_obj.id}), creating missing templates only")
            if kind == KIND_MODULE_TYPE:
                self._apply_module_profile(session, record, type_obj, data, profile_id)
        else:
            payload = build_type_payload(data, manufacturer.id, kind)
            if profile_id is not None:
                payload["profile"] = profile_id
            if kind == KIND_MODULE_TYPE and data.get("attribute_data"):
                payload["attributes"] = data["attribute_data"]
            type_obj = owner_api.create(**payload)
            self.log_info(session, record, f"Created {owner['label']} '{data['model']}' (id={type_obj.id})")

        # Track created/existing template ids so name refs (port mappings,
        # outlet->power port) resolve without extra lookups.
        template_ids: dict[str, dict[str, int]] = {}
        port_mappings = build_port_mappings(data)

        for yaml_key, endpoint, payloads, ref_fields in build_component_payloads(data, kind):
            api = getattr(self.client.nb.dcim, endpoint)
            existing_objs = {t.name: t for t in api.filter(**{owner["filter"]: type_obj.id})}
            template_ids[endpoint] = {name: t.id for name, t in existing_objs.items()}

            to_create = []
            for payload in payloads:
                if payload["name"] in existing_objs:
                    continue
                resolved = dict(payload, **{owner["fk"]: type_obj.id})
                for ref_field, ref_endpoint in ref_fields.items():
                    if ref_field not in resolved:
                        continue
                    resolved[ref_field] = self._resolve_ref(
                        session, record, yaml_key, ref_field, ref_endpoint,
                        resolved[ref_field], template_ids,
                    )
                if endpoint == "front_port_templates":
                    self._attach_rear_port_mappings(session, record, resolved, port_mappings, template_ids)
                to_create.append(resolved)

            if to_create:
                created = api.create(to_create)
                for obj in created:
                    template_ids[endpoint][obj.name] = obj.id
                self.log_info(session, record, f"Created {len(to_create)} {yaml_key} template(s)")
            skipped = len(payloads) - len(to_create)
            if skipped:
                self.log_info(session, record, f"Skipped {skipped} existing {yaml_key} template(s)")
            if endpoint == "front_port_templates":
                self._repair_missing_mappings(
                    session, record, payloads, existing_objs, port_mappings, template_ids)

        return type_obj.id, f"{self.client.netbox_url}/dcim/{owner['url_path']}/{type_obj.id}/"

    def _find_existing(self, data: dict, kind: str = KIND_DEVICE_TYPE, manufacturer_id: int | None = None):
        """Look up the device/module type scoped to its manufacturer.

        NetBox slugs and models are only unique per manufacturer, so an
        unscoped lookup could match (and later mutate) another manufacturer's
        type. Module types have no slug, so they match on model alone.
        """
        if manufacturer_id is None:
            manufacturer = self.client.nb.dcim.manufacturers.get(name=data["manufacturer"])
            if not manufacturer:
                return None
            manufacturer_id = manufacturer.id
        owner_api = getattr(self.client.nb.dcim, OWNERS[kind]["endpoint"])
        if kind == KIND_MODULE_TYPE:
            return owner_api.get(model=data["model"], manufacturer_id=manufacturer_id)
        slug = data.get("slug") or slugify(f"{data['manufacturer']} {data['model']}")
        return (
            owner_api.get(slug=slug, manufacturer_id=manufacturer_id)
            or owner_api.get(model=data["model"], manufacturer_id=manufacturer_id)
        )

    def _resolve_module_profile(self, session: Session, record: Record, data: dict):
        """Resolve the YAML's module-type profile name to a NetBox profile id.

        A profile name NetBox does not know is logged and skipped rather than
        failing the record — the module type still imports without it.
        """
        name = data.get("profile")
        if not name:
            return None
        profile = self.client.nb.dcim.module_type_profiles.get(name=name)
        if not profile:
            self.log_info(session, record,
                          f"Module type profile '{name}' not found in NetBox; importing without a profile")
            return None
        return profile.id

    @staticmethod
    def _existing_attributes(type_obj) -> dict:
        """The module type's stored profile attributes as a plain dict.

        NetBox's REST serializer names the field 'attributes' (the YAML and
        ORM call it attribute_data — the API drops that name entirely);
        pynetbox may hand back a Record rather than a dict."""
        existing = getattr(type_obj, "attributes", None) or getattr(type_obj, "attribute_data", None) or {}
        return existing if isinstance(existing, dict) else dict(existing)

    def _apply_module_profile(self, session: Session, record: Record, type_obj,
                              data: dict, profile_id) -> None:
        """Backfill a missing profile and missing attribute keys on an
        existing module type (imports made before profile support left both
        unset). Values already present are never overwritten. Writes go to
        the API's 'attributes' field — 'attribute_data' is read-only."""
        changed = []
        if profile_id is not None and not getattr(type_obj, "profile", None):
            type_obj.profile = profile_id
            changed.append("profile")
        wanted = data.get("attribute_data") or {}
        existing = self._existing_attributes(type_obj)
        missing = {k: v for k, v in wanted.items() if k not in existing}
        if missing:
            type_obj.attributes = {**existing, **missing}
            changed.append("attributes")
        if changed:
            type_obj.save()
            self.log_info(session, record,
                          f"Updated existing module type with missing {' and '.join(changed)}")

    def _module_profile_missing(self, type_obj, data: dict) -> bool:
        """True when the YAML defines a profile or attributes the existing
        module type lacks (and, for the profile, NetBox can actually resolve)."""
        wanted = data.get("attribute_data") or {}
        if any(k not in self._existing_attributes(type_obj) for k in wanted):
            return True
        if data.get("profile") and not getattr(type_obj, "profile", None):
            return self.client.nb.dcim.module_type_profiles.get(name=data["profile"]) is not None
        return False

    def _missing_components(self, type_obj, data: dict, kind: str = KIND_DEVICE_TYPE) -> bool:
        """True if any template named in the YAML does not exist on the device or
        module type yet, an existing front port lacks the rear-port mappings the
        YAML defines (left empty by imports made before port-mapping support), or
        an existing module type lacks the YAML's profile or attribute_data."""
        if kind == KIND_MODULE_TYPE and self._module_profile_missing(type_obj, data):
            return True
        port_mappings = build_port_mappings(data)
        for _, endpoint, payloads, _ in build_component_payloads(data, kind):
            api = getattr(self.client.nb.dcim, endpoint)
            existing = {t.name: t for t in api.filter(**{OWNERS[kind]["filter"]: type_obj.id})}
            if any(p["name"] not in existing for p in payloads):
                return True
            if endpoint == "front_port_templates" and port_mappings and self._netbox_supports_port_mappings():
                for p in payloads:
                    if port_mappings.get(p["name"]) and not getattr(existing[p["name"]], "rear_ports", None):
                        return True
        return False

    def _resolve_mappings(self, session: Session, record: Record,
                          mappings: list[dict], template_ids: dict) -> list[dict]:
        return [
            {
                "position": m["position"],
                "rear_port": self._resolve_ref(
                    session, record, "port-mappings", "rear_port", "rear_port_templates",
                    m["rear_port"], template_ids,
                ),
                "rear_port_position": m["rear_port_position"],
            }
            for m in mappings
        ]

    def _attach_rear_port_mappings(self, session: Session, record: Record, payload: dict,
                                   port_mappings: dict, template_ids: dict) -> None:
        mappings = port_mappings.get(payload["name"])
        if not mappings:
            return
        resolved = self._resolve_mappings(session, record, mappings, template_ids)
        if self._netbox_supports_port_mappings():
            payload["rear_ports"] = resolved
            return
        # Pre-4.5 NetBox models exactly one position-1 mapping inline on the
        # front port; anything richer would import with silently different
        # connectivity, so reject it instead.
        if (len(resolved) > 1
                or resolved[0]["position"] != 1
                or int(payload.get("positions") or 1) > 1):
            raise ValueError(
                f"Front port '{payload['name']}' uses multiple mappings or front-port "
                f"positions, which NetBox {self._netbox_version} cannot model (4.5+ required)"
            )
        payload.pop("positions", None)  # front port templates gained 'positions' in 4.5
        payload["rear_port"] = resolved[0]["rear_port"]
        payload["rear_port_position"] = resolved[0]["rear_port_position"]

    def _repair_missing_mappings(self, session: Session, record: Record, payloads: list[dict],
                                 existing_objs: dict, port_mappings: dict, template_ids: dict) -> None:
        """Fill in rear-port mappings on existing front port templates that have
        none — HAROLD 1.1.0 sent the pre-4.5 inline fields, which NetBox 4.5+
        silently dropped, so re-importing repairs those device types. Templates
        that already have any mapping are left untouched."""
        if not self._netbox_supports_port_mappings():
            return  # pre-4.5 NetBox cannot create a front port without a mapping
        repaired = 0
        for payload in payloads:
            template = existing_objs.get(payload["name"])
            if template is None or getattr(template, "rear_ports", None):
                continue
            mappings = port_mappings.get(payload["name"])
            if not mappings:
                continue
            template.rear_ports = self._resolve_mappings(session, record, mappings, template_ids)
            template.save()
            repaired += 1
        if repaired:
            self.log_info(session, record,
                          f"Added missing rear-port mappings to {repaired} existing front-ports template(s)")

    def _netbox_supports_port_mappings(self) -> bool:
        if self._supports_port_mappings is None:
            self._netbox_version = self.client.test_connection()
            match = re.match(r"(\d+)\.(\d+)", self._netbox_version or "")
            # Unparseable versions are assumed current (4.5+)
            self._supports_port_mappings = (
                (int(match.group(1)), int(match.group(2))) >= (4, 5) if match else True
            )
        return self._supports_port_mappings

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
