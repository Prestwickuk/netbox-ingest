import unittest
import uuid
from unittest.mock import MagicMock, patch

from app.worker.stages.device_types import (
    DeviceTypeStage,
    build_component_payloads,
    build_device_type_payload,
    parse_device_type_yaml,
)

SAMPLE_YAML = """\
manufacturer: Dell
model: PowerEdge R6615
slug: dell-poweredge-r6615
u_height: 1
is_full_depth: true
airflow: front-to-rear
weight: 16.75
weight_unit: kg
console-ports:
  - name: Rear Serial
    type: de-9
interfaces:
  - name: Gig-E 1
    type: 1000base-t
  - name: iDRAC
    type: 1000base-t
    mgmt_only: true
power-ports:
  - name: PSU1
    type: iec-60320-c14
    maximum_draw: 750
power-outlets:
  - name: Outlet 1
    type: iec-60320-c13
    power_port: PSU1
"""


class ParseDeviceTypeYamlTests(unittest.TestCase):
    def test_parses_valid_yaml(self) -> None:
        data = parse_device_type_yaml(SAMPLE_YAML)
        self.assertEqual(data["model"], "PowerEdge R6615")

    def test_rejects_missing_required_fields(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_device_type_yaml("model: Foo\n")
        self.assertIn("manufacturer", str(ctx.exception))

    def test_rejects_non_mapping_yaml(self) -> None:
        with self.assertRaises(ValueError):
            parse_device_type_yaml("- just\n- a list\n")

    def test_rejects_invalid_yaml(self) -> None:
        with self.assertRaises(ValueError):
            parse_device_type_yaml("model: [unclosed\n  bracket: {")


class BuildDeviceTypePayloadTests(unittest.TestCase):
    def test_maps_known_fields(self) -> None:
        payload = build_device_type_payload(parse_device_type_yaml(SAMPLE_YAML), manufacturer_id=7)
        self.assertEqual(payload["manufacturer"], 7)
        self.assertEqual(payload["slug"], "dell-poweredge-r6615")
        self.assertEqual(payload["u_height"], 1)
        self.assertEqual(payload["weight"], 16.75)
        self.assertNotIn("console-ports", payload)

    def test_generates_slug_when_missing(self) -> None:
        payload = build_device_type_payload({"manufacturer": "Dell", "model": "Foo Bar"}, 1)
        self.assertEqual(payload["slug"], "dell-foo-bar")


class BuildComponentPayloadsTests(unittest.TestCase):
    def test_orders_power_ports_before_outlets_and_keeps_refs(self) -> None:
        components = build_component_payloads(parse_device_type_yaml(SAMPLE_YAML))
        endpoints = [endpoint for _, endpoint, _, _ in components]
        self.assertLess(
            endpoints.index("power_port_templates"),
            endpoints.index("power_outlet_templates"),
        )
        by_endpoint = {endpoint: payloads for _, endpoint, payloads, _ in components}
        self.assertEqual(by_endpoint["power_outlet_templates"][0]["power_port"], "PSU1")
        self.assertEqual(by_endpoint["interface_templates"][1]["mgmt_only"], True)

    def test_rejects_entries_without_name(self) -> None:
        with self.assertRaises(ValueError):
            build_component_payloads({"interfaces": [{"type": "1000base-t"}]})


def _stage_with_mock_client() -> DeviceTypeStage:
    with patch("app.worker.stages.base.NetBoxClient"):
        stage = DeviceTypeStage("https://netbox.example.com", "token")
    stage.client = MagicMock()
    stage.client.netbox_url = "https://netbox.example.com"
    return stage


def _mock_all_templates_existing(nb) -> None:
    """Make every template referenced by SAMPLE_YAML already exist in NetBox."""
    def existing_filter_for(names):
        existing = []
        for i, n in enumerate(names):
            obj = MagicMock(id=200 + i)
            obj.name = n
            existing.append(obj)
        return existing

    nb.dcim.console_port_templates.filter.return_value = existing_filter_for(["Rear Serial"])
    nb.dcim.interface_templates.filter.return_value = existing_filter_for(["Gig-E 1", "iDRAC"])
    nb.dcim.power_port_templates.filter.return_value = existing_filter_for(["PSU1"])
    nb.dcim.power_outlet_templates.filter.return_value = existing_filter_for(["Outlet 1"])


class FindExistingTests(unittest.TestCase):
    def test_returns_none_when_manufacturer_absent(self) -> None:
        stage = _stage_with_mock_client()
        nb = stage.client.nb
        nb.dcim.manufacturers.get.return_value = None

        self.assertIsNone(stage._find_existing({"manufacturer": "Dell", "model": "X"}))
        nb.dcim.device_types.get.assert_not_called()

    def test_slug_lookup_is_scoped_to_manufacturer(self) -> None:
        stage = _stage_with_mock_client()
        nb = stage.client.nb
        nb.dcim.manufacturers.get.return_value = MagicMock(id=7)
        nb.dcim.device_types.get.return_value = MagicMock(id=42)

        result = stage._find_existing(parse_device_type_yaml(SAMPLE_YAML))

        self.assertEqual(result.id, 42)
        nb.dcim.device_types.get.assert_called_once_with(
            slug="dell-poweredge-r6615", manufacturer_id=7
        )


class DeviceTypeStageProcessTests(unittest.TestCase):
    def test_skips_complete_duplicate(self) -> None:
        stage = _stage_with_mock_client()
        nb = stage.client.nb
        nb.dcim.manufacturers.get.return_value = MagicMock(id=7)
        nb.dcim.device_types.get.return_value = MagicMock(id=42)
        _mock_all_templates_existing(nb)

        record = MagicMock(id=uuid.uuid4())
        record.raw_data = {"yaml_text": SAMPLE_YAML}
        stage.process(MagicMock(), record)

        self.assertEqual(record.status, "skipped")
        self.assertEqual(record.netbox_id, 42)

    def test_component_check_failure_fails_the_record_not_the_job(self) -> None:
        """An API error during the duplicate pre-check must surface as a failed
        record (retryable via Retry), never escape process() and kill the job."""
        stage = _stage_with_mock_client()
        nb = stage.client.nb
        nb.dcim.manufacturers.get.return_value = MagicMock(id=7)
        nb.dcim.device_types.get.return_value = MagicMock(id=42)
        nb.dcim.console_port_templates.filter.side_effect = RuntimeError("NetBox API unreachable")

        record = MagicMock(id=uuid.uuid4())
        record.raw_data = {"yaml_text": SAMPLE_YAML}
        stage.process(MagicMock(), record)  # must not raise

        self.assertEqual(record.status, "failed")
        self.assertIn("unreachable", record.error_message)


class DeviceTypeStageCreateTests(unittest.TestCase):
    def _stage_with_mock_client(self) -> DeviceTypeStage:
        return _stage_with_mock_client()

    def test_create_resolves_power_port_reference(self) -> None:
        stage = self._stage_with_mock_client()
        nb = stage.client.nb

        nb.dcim.device_types.get.return_value = None
        nb.dcim.manufacturers.get.return_value = MagicMock(id=7)
        nb.dcim.device_types.create.return_value = MagicMock(id=42)

        # No pre-existing templates; bulk create echoes back objects with ids
        for endpoint in ("console_port_templates", "interface_templates",
                         "power_port_templates", "power_outlet_templates"):
            api = getattr(nb.dcim, endpoint)
            api.filter.return_value = []

        def bulk_create(payloads):
            created = []
            for i, p in enumerate(payloads):
                obj = MagicMock(id=100 + i)
                obj.name = p["name"]  # name= in the constructor names the mock itself
                created.append(obj)
            return created

        nb.dcim.power_port_templates.create.side_effect = bulk_create
        nb.dcim.power_outlet_templates.create.side_effect = bulk_create
        nb.dcim.console_port_templates.create.side_effect = bulk_create
        nb.dcim.interface_templates.create.side_effect = bulk_create

        record = MagicMock(id=uuid.uuid4())
        record.raw_data = {"yaml_text": SAMPLE_YAML}
        netbox_id, url = stage.create(MagicMock(), record)

        self.assertEqual(netbox_id, 42)
        self.assertEqual(url, "https://netbox.example.com/dcim/device-types/42/")

        outlet_payloads = nb.dcim.power_outlet_templates.create.call_args.args[0]
        self.assertEqual(outlet_payloads[0]["power_port"], 100)  # PSU1's created id
        self.assertEqual(outlet_payloads[0]["device_type"], 42)

    def test_create_skips_existing_templates(self) -> None:
        stage = self._stage_with_mock_client()
        nb = stage.client.nb

        existing_dt = MagicMock(id=42)
        nb.dcim.device_types.get.return_value = existing_dt
        nb.dcim.manufacturers.get.return_value = MagicMock(id=7)

        _mock_all_templates_existing(nb)

        record = MagicMock(id=uuid.uuid4())
        record.raw_data = {"yaml_text": SAMPLE_YAML}
        netbox_id, _ = stage.create(MagicMock(), record)

        self.assertEqual(netbox_id, 42)
        nb.dcim.device_types.create.assert_not_called()
        nb.dcim.interface_templates.create.assert_not_called()
        nb.dcim.power_outlet_templates.create.assert_not_called()

    def test_unresolvable_reference_raises(self) -> None:
        stage = self._stage_with_mock_client()
        nb = stage.client.nb
        nb.dcim.device_types.get.return_value = None
        nb.dcim.manufacturers.get.return_value = MagicMock(id=7)
        nb.dcim.device_types.create.return_value = MagicMock(id=42)
        nb.dcim.power_outlet_templates.filter.return_value = []

        record = MagicMock(id=uuid.uuid4())
        record.raw_data = {"yaml_text": (
            "manufacturer: X\nmodel: Y\n"
            "power-outlets:\n  - name: Outlet 1\n    power_port: MissingPSU\n"
        )}
        with self.assertRaises(ValueError) as ctx:
            stage.create(MagicMock(), record)
        self.assertIn("MissingPSU", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
