import logging

from sqlalchemy.orm import Session

from app.models.db import Record
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

STATUS_DEFAULT = "active"


class ModuleStage(BaseStage):
    """Install modules (DPUs, NICs, cassettes, PSUs) into device module bays.

    Each record names a device, one of its module bays, and the module type to
    install. NetBox instantiates the module type's {module}-templated
    components (interfaces, ports) on the device automatically. A bay that
    already holds a module is skipped, so re-applying a fleet CSV is safe and
    only fills bays that are still empty.
    """

    REQUIRED_FIELDS = ["device", "site", "module_bay", "module_type"]

    def process(self, session: Session, record: Record) -> None:
        installed = None
        try:
            data = record.raw_data
            device = self._resolve_device(data)
            bay = self._resolve_bay(device, data["module_bay"])
            installed = getattr(bay, "installed_module", None)
        except Exception:
            # Fall through to super().process(): create() hits the same error
            # inside the per-record handler, so the record fails instead of the job.
            pass

        if installed:
            url = f"{self.client.netbox_url}/dcim/modules/{installed.id}/"
            self.skip(session, record, installed.id, url)
            return
        super().process(session, record)

    def create(self, session: Session, record: Record) -> tuple[int, str]:
        data = record.raw_data

        device = self._resolve_device(data)
        self.log_info(session, record, f"Resolved device: {device.name} (id={device.id})")

        bay = self._resolve_bay(device, data["module_bay"])
        self.log_info(session, record, f"Resolved module bay: {bay.name} (id={bay.id})")

        module_type = self._resolve_module_type(data)
        self.log_info(session, record, f"Resolved module type: {module_type.model} (id={module_type.id})")

        payload: dict = {
            "device": device.id,
            "module_bay": bay.id,
            "module_type": module_type.id,
            "status": data.get("status") or STATUS_DEFAULT,
        }
        for optional in ("serial", "asset_tag", "description"):
            if data.get(optional):
                payload[optional] = data[optional]

        module = self.client.nb.dcim.modules.create(**payload)
        self.log_info(
            session, record,
            f"Installed '{module_type.model}' in bay '{bay.name}' of {device.name} (module id={module.id})",
        )
        return module.id, f"{self.client.netbox_url}/dcim/modules/{module.id}/"

    def _resolve_device(self, data: dict):
        site = (
            self.client.nb.dcim.sites.get(name=data["site"])
            or self.client.nb.dcim.sites.get(slug=data["site"])
        )
        if not site:
            raise ValueError(f"Site '{data['site']}' not found in NetBox")
        device = self.client.nb.dcim.devices.get(name=data["device"], site_id=site.id)
        if not device:
            raise ValueError(f"Device '{data['device']}' not found in site '{data['site']}'")
        return device

    def _resolve_bay(self, device, bay_name: str):
        bay = self.client.nb.dcim.module_bays.get(device_id=device.id, name=bay_name)
        if not bay:
            raise ValueError(
                f"Module bay '{bay_name}' not found on device '{device.name}' — "
                "check the bay name against the device type's module bays"
            )
        return bay

    def _resolve_module_type(self, data: dict):
        lookup: dict = {"model": data["module_type"]}
        if data.get("manufacturer"):
            manufacturer = self.client.nb.dcim.manufacturers.get(name=data["manufacturer"])
            if not manufacturer:
                raise ValueError(f"Manufacturer '{data['manufacturer']}' not found in NetBox")
            lookup["manufacturer_id"] = manufacturer.id
        module_type = self.client.nb.dcim.module_types.get(**lookup)
        if not module_type:
            scope = f" for manufacturer '{data['manufacturer']}'" if data.get("manufacturer") else ""
            raise ValueError(f"Module type '{data['module_type']}'{scope} not found in NetBox")
        return module_type
