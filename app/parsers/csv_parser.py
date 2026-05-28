import csv
import io
from typing import Any

# Required headers per file type
REQUIRED_HEADERS: dict[str, list[str]] = {
    "racks": ["name", "site", "u_height"],
    "rack_infra": ["name", "rack", "position_u", "face", "manufacturer", "device_type"],
    "patch_panels": ["name", "rack", "position_u", "face", "manufacturer", "device_type"],
    "network_devices": ["name", "site", "manufacturer", "device_type", "device_role", "status"],
    "servers": ["name", "site", "manufacturer", "device_type", "device_role", "status"],
    "power_panels": ["name", "site"],
    "power_feeds": ["name", "site", "power_panel", "rack"],
    "cables": [
        "a_device", "a_site", "a_termination_type", "a_termination_name",
        "b_device", "b_site", "b_termination_type", "b_termination_name",
    ],
    "ip_assignment": ["device", "site", "prefix"],
}


def parse_csv(content: bytes, file_type: str) -> list[dict[str, Any]]:
    """Parse CSV bytes into a list of row dicts. Strips whitespace from values."""
    text = content.decode("utf-8-sig")  # handle BOM if present
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise ValueError("CSV file appears to be empty")

    # Normalise header names
    headers = [h.strip().lower() for h in reader.fieldnames]
    required = REQUIRED_HEADERS.get(file_type, [])
    missing = [r for r in required if r not in headers]
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

    rows = []
    for row in reader:
        cleaned = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items()}
        rows.append(cleaned)

    if not rows:
        raise ValueError("CSV file contains no data rows")

    return rows
