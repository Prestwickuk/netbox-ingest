import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.netbox.duplicate import check_device
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

FACE_MAP = {"front": "front", "rear": "rear"}


class ServerStage(BaseStage):
    REQUIRED_FIELDS = ["name", "site", "manufacturer", "device_type", "device_role", "status"]

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

        # Manufacturer
        manufacturer = self.client.nb.dcim.manufacturers.get(name=data["manufacturer"])
        if not manufacturer:
            raise ValueError(f"Manufacturer '{data['manufacturer']}' not found in NetBox")

        # Device type
        device_type = self.client.nb.dcim.device_types.get(
            model=data["device_type"], manufacturer_id=manufacturer.id
        )
        if not device_type:
            raise ValueError(
                f"Device type '{data['device_type']}' for manufacturer "
                f"'{data['manufacturer']}' not found in NetBox"
            )
        self.log_info(session, record, f"Resolved device type: {device_type.model} (id={device_type.id})")

        # Device role
        role = (
            self.client.nb.dcim.device_roles.get(name=data["device_role"])
            or self.client.nb.dcim.device_roles.get(slug=data["device_role"])
        )
        if not role:
            raise ValueError(f"Device role '{data['device_role']}' not found in NetBox")

        payload: dict = {
            "name": data["name"],
            "site": site.id,
            "device_type": device_type.id,
            "role": role.id,
            "status": data["status"],
        }

        # Optional rack mounting
        if data.get("rack"):
            rack = self.client.nb.dcim.racks.get(name=data["rack"], site_id=site.id)
            if not rack:
                raise ValueError(f"Rack '{data['rack']}' not found in site '{data['site']}'")
            payload["rack"] = rack.id
            self.log_info(session, record, f"Resolved rack: {rack.name} (id={rack.id})")

            if data.get("position_u"):
                payload["position"] = int(data["position_u"])

            if data.get("face"):
                face = FACE_MAP.get(data["face"].lower())
                if face is None:
                    raise ValueError(f"Invalid face '{data['face']}' — must be 'front' or 'rear'")
                payload["face"] = face

        # Optional platform
        if data.get("platform"):
            platform = (
                self.client.nb.dcim.platforms.get(name=data["platform"])
                or self.client.nb.dcim.platforms.get(slug=data["platform"])
            )
            if platform:
                payload["platform"] = platform.id
            else:
                self.log_info(session, record, f"Platform '{data['platform']}' not found, skipping")

        # Optional tenant
        if data.get("tenant"):
            tenant = (
                self.client.nb.tenancy.tenants.get(name=data["tenant"])
                or self.client.nb.tenancy.tenants.get(slug=data["tenant"])
            )
            if tenant:
                payload["tenant"] = tenant.id
            else:
                self.log_info(session, record, f"Tenant '{data['tenant']}' not found, skipping")

        for field in ("serial", "asset_tag"):
            if data.get(field):
                payload[field] = data[field]

        device = self.client.nb.dcim.devices.create(**payload)
        self.log_info(session, record, f"Created device id={device.id}")

        # Configure Optional Boot MAC Address and Boot Interface
        boot_mac = data.get("boot_mac", "").strip()
        boot_interface_name = data.get("boot_interface", "eth0").strip()
        if boot_mac:
            self._configure_interface(session, record, device.id, boot_interface_name, mac_address=boot_mac)

        # Configure Optional BMC details
        bmc_ip = data.get("bmc_ip", "").strip()
        bmc_interface_name = data.get("bmc_interface", "bmc").strip()
        bmc_username = data.get("bmc_username", "").strip()
        bmc_password = data.get("bmc_password", "").strip()

        if bmc_ip:
            # Create/update BMC interface
            bmc_interface = self._configure_interface(
                session, record, device.id, bmc_interface_name, interface_type="other"
            )
            # Allocate and assign IP to BMC interface
            ip_obj = self._configure_ip(session, record, bmc_interface, bmc_ip)
            
            # Set primary IP
            device.update({"primary_ip4": ip_obj.id})
            self.log_info(session, record, f"Set device primary_ip4 to BMC IP (id={ip_obj.id})")

        if bmc_username or bmc_password:
            # Update device local_context_data with BMC details
            local_context = device.local_context_data or {}
            if not isinstance(local_context, dict):
                local_context = {}
            
            local_context["bmc"] = {
                "username": bmc_username,
                "password": bmc_password,
                "ip": bmc_ip.split("/")[0] if bmc_ip else "",
            }
            device.update({"local_context_data": local_context})
            self.log_info(session, record, f"Stored BMC credentials in local_context_data for device id={device.id}")

        return device.id, f"{self.client.netbox_url}/dcim/devices/{device.id}/"

    def _configure_interface(self, session, record, device_id, name, mac_address=None, interface_type="1000base-t"):
        interface = self.client.nb.dcim.interfaces.get(device_id=device_id, name=name)
        if interface:
            payload = {}
            if mac_address and (interface.mac_address or "").lower() != mac_address.lower():
                payload["mac_address"] = mac_address.lower()
            if payload:
                interface.update(payload)
                self.log_info(session, record, f"Updated existing interface '{name}' on device id={device_id}")
        else:
            payload = {
                "device": device_id,
                "name": name,
                "type": interface_type,
            }
            if mac_address:
                payload["mac_address"] = mac_address.lower()
            interface = self.client.nb.dcim.interfaces.create(**payload)
            self.log_info(session, record, f"Created interface '{name}' on device id={device_id}")
        return interface

    def _configure_ip(self, session, record, interface, ip_with_mask):
        ip_str = ip_with_mask.strip()
        if "/" not in ip_str:
            ip_str = f"{ip_str}/32"

        # Check if IP address exists
        existing_ips = list(self.client.nb.ipam.ip_addresses.filter(address=ip_str))
        if existing_ips:
            ip = existing_ips[0]
            if ip.assigned_object_id != interface.id or ip.assigned_object_type != "dcim.interface":
                ip.update({
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": interface.id,
                })
                self.log_info(session, record, f"Re-assigned existing IP {ip.address} to interface '{interface.name}'")
        else:
            ip = self.client.nb.ipam.ip_addresses.create(
                address=ip_str,
                status="active",
                assigned_object_type="dcim.interface",
                assigned_object_id=interface.id,
            )
            self.log_info(session, record, f"Created and assigned IP {ip.address} to interface '{interface.name}'")
        return ip
