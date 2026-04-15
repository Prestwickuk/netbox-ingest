import json
from typing import Any
from app.parsers.csv_parser import REQUIRED_HEADERS


def parse_json(content: bytes, file_type: str) -> list[dict[str, Any]]:
    """Parse JSON bytes (array of objects) into a list of row dicts."""
    try:
        data = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    if not isinstance(data, list):
        raise ValueError("JSON must be an array of objects")
    if not data:
        raise ValueError("JSON array is empty")
    if not isinstance(data[0], dict):
        raise ValueError("JSON array elements must be objects")

    # Normalise keys to lowercase
    rows = [{k.strip().lower(): v for k, v in row.items()} for row in data]

    required = REQUIRED_HEADERS.get(file_type, [])
    sample_keys = set(rows[0].keys())
    missing = [r for r in required if r not in sample_keys]
    if missing:
        raise ValueError(f"JSON objects are missing required keys: {', '.join(missing)}")

    return rows
