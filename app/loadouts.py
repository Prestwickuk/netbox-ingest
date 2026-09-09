"""Loadout serialization and expansion.

A loadout maps a device type's module bays to module types. It is stored and
exported by manufacturer/model/bay NAMES so it stays portable across NetBox
instances; the modules stage resolves the names at apply time.
"""
from typing import Any

import yaml


def loadout_to_yaml(loadout) -> str:
    """Serialize one loadout to the interchange YAML format."""
    doc = {
        "name": loadout.name,
        "manufacturer": loadout.manufacturer,
        "device_type": loadout.device_type,
        "bays": {
            bay: {"manufacturer": fit["manufacturer"], "module_type": fit["module_type"]}
            for bay, fit in sorted(loadout.bays.items())
        },
    }
    return yaml.safe_dump(doc, sort_keys=False, width=1000)


def loadouts_from_yaml(text: str) -> list[dict[str, Any]]:
    """Parse one or more loadout YAML documents into loadout dicts.

    Accepts a single mapping, a YAML list of mappings, or multiple documents
    separated by '---'. Raises ValueError with a per-loadout reason on any
    malformed entry.
    """
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}")

    entries: list[dict] = []
    for doc in docs:
        entries.extend(doc if isinstance(doc, list) else [doc])

    loadouts = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each loadout must be a mapping")
        missing = [f for f in ("name", "manufacturer", "device_type", "bays") if not entry.get(f)]
        if missing:
            raise ValueError(
                f"Loadout '{entry.get('name', '?')}' is missing required fields: {', '.join(missing)}")
        bays = entry["bays"]
        if not isinstance(bays, dict) or not bays:
            raise ValueError(f"Loadout '{entry['name']}': 'bays' must be a non-empty mapping")
        parsed_bays = {}
        for bay, fit in bays.items():
            if not isinstance(fit, dict) or not fit.get("module_type") or not fit.get("manufacturer"):
                raise ValueError(
                    f"Loadout '{entry['name']}', bay '{bay}': each bay needs "
                    "'manufacturer' and 'module_type'")
            parsed_bays[str(bay)] = {
                "manufacturer": str(fit["manufacturer"]),
                "module_type": str(fit["module_type"]),
            }
        loadouts.append({
            "name": str(entry["name"]),
            "manufacturer": str(entry["manufacturer"]),
            "device_type": str(entry["device_type"]),
            "bays": parsed_bays,
        })
    return loadouts


def expand_loadout_rows(bays: dict, devices: list[dict], status: str = "active") -> list[dict[str, Any]]:
    """Expand a loadout's bay map over target devices into modules-stage rows.

    devices: [{"device": name, "site": site_name}, ...]. Returns one row per
    device x bay, in device order then bay order, ready to store as Record
    raw_data for a 'modules' job.
    """
    rows = []
    for target in devices:
        for bay, fit in sorted(bays.items()):
            rows.append({
                "device": target["device"],
                "site": target["site"],
                "module_bay": bay,
                "module_type": fit["module_type"],
                "manufacturer": fit["manufacturer"],
                "status": status,
            })
    return rows
