import logging
from sqlalchemy.orm import Session

from app.models.db import Record
from app.worker.stages.base import BaseStage

log = logging.getLogger(__name__)

# Up to 5 interface columns supported
IFACE_COLUMNS = ["iface_1_name", "iface_2_name", "iface_3_name", "iface_4_name", "iface_5_name"]


class IPAssignmentStage(BaseStage):
    REQUIRED_FIELDS = ["device", "site", "prefix"]

    def process(self, session: Session, record: Record) -> None:
        data = record.raw_data

        # Validate at least one interface is specified
        populated = [col for col in IFACE_COLUMNS if data.get(col)]
        if not populated:
            from app.models.db import Job, RecordLog
            from datetime import datetime
            record.status = "failed"
            record.error_message = "At least one interface column (iface_1_name … iface_5_name) must be provided"
            record.processed_at = datetime.utcnow()
            session.add(RecordLog(record_id=record.id, level="error", message=record.error_message))
            session.execute(
                Job.__table__.update()
                .where(Job.id == record.job_id)
                .values(failed_count=Job.failed_count + 1)
            )
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

        # Device
        device = self.client.nb.dcim.devices.get(name=data["device"], site_id=site.id)
        if not device:
            raise ValueError(f"Device '{data['device']}' not found in site '{data['site']}'")
        self.log_info(session, record, f"Resolved device: {device.name} (id={device.id})")

        # Prefix (optionally scoped to VRF)
        prefix_filters = {"prefix": data["prefix"]}
        if data.get("vrf"):
            vrf = self.client.nb.ipam.vrfs.get(name=data["vrf"])
            if not vrf:
                raise ValueError(f"VRF '{data['vrf']}' not found in NetBox")
            prefix_filters["vrf_id"] = vrf.id
            self.log_info(session, record, f"Resolved VRF: {vrf.name} (id={vrf.id})")

        prefix = self.client.nb.ipam.prefixes.get(**prefix_filters)
        if not prefix:
            raise ValueError(f"Prefix '{data['prefix']}' not found in NetBox IPAM")
        self.log_info(session, record, f"Resolved prefix: {prefix.prefix} (id={prefix.id})")

        primary_mgmt_iface = data.get("primary_mgmt_iface", "").strip()
        primary_ip_id = None
        first_ip_id = None

        # Allocate an IP from the prefix for each populated interface column
        for col in IFACE_COLUMNS:
            iface_name = data.get(col, "").strip()
            if not iface_name:
                continue

            interface = self.client.nb.dcim.interfaces.get(name=iface_name, device_id=device.id)
            if not interface:
                raise ValueError(f"Interface '{iface_name}' not found on device '{data['device']}'")

            # Skip if interface already has IPs assigned
            existing_ips = list(self.client.nb.ipam.ip_addresses.filter(
                assigned_object_type="dcim.interface",
                assigned_object_id=interface.id,
            ))
            if existing_ips:
                self.log_info(session, record,
                    f"Interface '{iface_name}' already has IP(s) assigned, skipping")
                if first_ip_id is None:
                    first_ip_id = existing_ips[0].id
                if iface_name == primary_mgmt_iface and primary_ip_id is None:
                    primary_ip_id = existing_ips[0].id
                continue

            # Find next available IP from prefix
            available = list(self.client.nb.ipam.prefixes.get(id=prefix.id).available_ips.list(limit=1))
            if not available:
                raise ValueError(f"No available IPs remaining in prefix '{data['prefix']}'")

            # Create IP and assign to interface in one call
            ip = self.client.nb.ipam.ip_addresses.create(
                address=available[0].address,
                status="active",
                assigned_object_type="dcim.interface",
                assigned_object_id=interface.id,
            )
            self.log_info(session, record,
                f"Allocated {ip.address} → interface '{iface_name}' (ip id={ip.id})")

            if first_ip_id is None:
                first_ip_id = ip.id
            if iface_name == primary_mgmt_iface and primary_ip_id is None:
                primary_ip_id = ip.id

        if not first_ip_id:
            raise ValueError("No IPs were allocated — all interfaces already had IPs assigned")

        # Set device primary_ip4 to the designated management interface IP
        if primary_ip_id:
            device.update({"primary_ip4": primary_ip_id})
            self.log_info(session, record, f"Set device primary_ip4 (ip id={primary_ip_id})")
        else:
            self.log_info(session, record,
                f"primary_mgmt_iface '{primary_mgmt_iface}' not found in interface columns — "
                "primary_ip4 not updated")

        result_id = primary_ip_id or first_ip_id
        return result_id, f"{self.client.netbox_url}/ipam/ip-addresses/{result_id}/"
