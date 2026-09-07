"""Client for the netbox-community devicetype-library GitHub repository.

Builds a browsable index (manufacturer -> device type models) from a single
recursive git-tree API call, cached in memory, and fetches individual
device-type YAML definitions from raw.githubusercontent.com.
"""
import logging
import os
import re
import threading
import time

import requests

log = logging.getLogger(__name__)

DTL_REPO = os.environ.get("DEVICETYPE_LIBRARY_REPO", "netbox-community/devicetype-library")
DTL_BRANCH = os.environ.get("DEVICETYPE_LIBRARY_BRANCH", "master")
DTL_CACHE_TTL = int(os.environ.get("DEVICETYPE_LIBRARY_CACHE_TTL", "3600"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

TREE_URL = "https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"

_PATH_RE = re.compile(r"^device-types/(?!\.)[^/]+/(?!\.)[^/]+\.ya?ml$")

_cache_lock = threading.Lock()
_cache: dict = {"fetched_at": 0.0, "index": None}


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["Accept"] = "application/vnd.github+json"
    if GITHUB_TOKEN:
        session.headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return session


def slugify(value: str) -> str:
    """Match the devicetype-library slug convention: lowercase, dash-separated."""
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


def model_display_name(path: str) -> str:
    """Derive a readable model name from a repo file path."""
    filename = path.rsplit("/", 1)[-1]
    return re.sub(r"\.ya?ml$", "", filename)


def build_index(tree_entries: list[dict]) -> dict[str, list[dict]]:
    """Group device-type YAML paths from a git tree into {manufacturer: [models]}."""
    index: dict[str, list[dict]] = {}
    for entry in tree_entries:
        path = entry.get("path", "")
        if entry.get("type") != "blob" or not _PATH_RE.match(path):
            continue
        manufacturer = path.split("/")[1]
        index.setdefault(manufacturer, []).append({
            "model": model_display_name(path),
            "path": path,
        })
    for models in index.values():
        models.sort(key=lambda m: m["model"].lower())
    return dict(sorted(index.items(), key=lambda kv: kv[0].lower()))


def get_index(force_refresh: bool = False) -> dict[str, list[dict]]:
    """Return the cached library index, refreshing from GitHub when stale."""
    with _cache_lock:
        age = time.time() - _cache["fetched_at"]
        if _cache["index"] is not None and age < DTL_CACHE_TTL and not force_refresh:
            return _cache["index"]

        url = TREE_URL.format(repo=DTL_REPO, branch=DTL_BRANCH)
        log.info(f"Refreshing devicetype-library index from {url}")
        resp = _session().get(url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("truncated"):
            log.warning("devicetype-library git tree response was truncated; index may be incomplete")

        _cache["index"] = build_index(payload.get("tree", []))
        _cache["fetched_at"] = time.time()
        return _cache["index"]


def validate_library_path(path: str) -> str:
    """Reject anything that is not a device-type YAML path inside the library."""
    if not _PATH_RE.match(path):
        raise ValueError(f"Invalid device-type library path: {path!r}")
    return path


def fetch_device_type_yaml(path: str) -> str:
    """Fetch the raw YAML text of one device-type definition."""
    validate_library_path(path)
    url = RAW_URL.format(repo=DTL_REPO, branch=DTL_BRANCH, path=path)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text
