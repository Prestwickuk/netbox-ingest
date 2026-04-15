import logging
from app.netbox.client import NetBoxClient

log = logging.getLogger(__name__)


def check_rack(client: NetBoxClient, name: str, site_name: str) -> int | None:
    """Return existing rack ID if a rack with this name+site exists, else None."""
    site = client.nb.dcim.sites.get(name=site_name) or client.nb.dcim.sites.get(slug=site_name)
    if not site:
        return None
    rack = client.nb.dcim.racks.get(name=name, site_id=site.id)
    if rack:
        log.debug(f"Duplicate rack found: {name} in {site_name} (id={rack.id})")
        return rack.id
    return None


def check_device(client: NetBoxClient, name: str, site_name: str) -> int | None:
    """Return existing device ID if a device with this name+site exists, else None."""
    site = client.nb.dcim.sites.get(name=site_name) or client.nb.dcim.sites.get(slug=site_name)
    if not site:
        return None
    device = client.nb.dcim.devices.get(name=name, site_id=site.id)
    if device:
        log.debug(f"Duplicate device found: {name} in {site_name} (id={device.id})")
        return device.id
    return None
