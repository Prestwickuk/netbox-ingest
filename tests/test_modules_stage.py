import unittest
import uuid
from unittest.mock import MagicMock, patch

from app.worker.stages.modules import ModuleStage


def _stage() -> ModuleStage:
    with patch("app.worker.stages.base.NetBoxClient"):
        stage = ModuleStage("https://netbox.example.com", "token")
    stage.client = MagicMock()
    stage.client.netbox_url = "https://netbox.example.com"
    nb = stage.client.nb
    nb.dcim.sites.get.return_value = MagicMock(id=1, name="DC1")
    device = MagicMock(id=10)
    device.name = "gpu-node-01"
    nb.dcim.devices.get.return_value = device
    bay = MagicMock(id=20, installed_module=None)
    bay.name = "DPU1"
    nb.dcim.module_bays.get.return_value = bay
    mt = MagicMock(id=30)
    mt.model = "BlueField-3 DPU B3220"
    nb.dcim.module_types.get.return_value = mt
    nb.dcim.modules.create.return_value = MagicMock(id=99)
    return stage


def _record(**overrides) -> MagicMock:
    record = MagicMock(id=uuid.uuid4())
    record.raw_data = {
        "device": "gpu-node-01",
        "site": "DC1",
        "module_bay": "DPU1",
        "module_type": "BlueField-3 DPU B3220",
        **overrides,
    }
    return record


class ModuleStageCreateTests(unittest.TestCase):
    def test_installs_module_with_default_status(self) -> None:
        stage = _stage()
        netbox_id, url = stage.create(MagicMock(), _record())

        self.assertEqual(netbox_id, 99)
        self.assertEqual(url, "https://netbox.example.com/dcim/modules/99/")
        payload = stage.client.nb.dcim.modules.create.call_args.kwargs
        self.assertEqual(payload, {"device": 10, "module_bay": 20, "module_type": 30, "status": "active"})

    def test_optional_fields_passed_through(self) -> None:
        stage = _stage()
        stage.create(MagicMock(), _record(status="planned", serial="MT234", asset_tag="A1"))

        payload = stage.client.nb.dcim.modules.create.call_args.kwargs
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["serial"], "MT234")
        self.assertEqual(payload["asset_tag"], "A1")

    def test_module_type_scoped_to_manufacturer_when_given(self) -> None:
        stage = _stage()
        stage.client.nb.dcim.manufacturers.get.return_value = MagicMock(id=7)

        stage.create(MagicMock(), _record(manufacturer="Nvidia"))

        stage.client.nb.dcim.module_types.get.assert_called_once_with(
            model="BlueField-3 DPU B3220", manufacturer_id=7)

    def test_missing_bay_raises_with_bay_name(self) -> None:
        stage = _stage()
        stage.client.nb.dcim.module_bays.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            stage.create(MagicMock(), _record(module_bay="DPU9"))
        self.assertIn("DPU9", str(ctx.exception))

    def test_unknown_device_raises(self) -> None:
        stage = _stage()
        stage.client.nb.dcim.devices.get.return_value = None
        with self.assertRaises(ValueError) as ctx:
            stage.create(MagicMock(), _record())
        self.assertIn("gpu-node-01", str(ctx.exception))


class ModuleStageProcessTests(unittest.TestCase):
    def test_populated_bay_is_skipped(self) -> None:
        stage = _stage()
        stage.client.nb.dcim.module_bays.get.return_value = MagicMock(
            id=20, installed_module=MagicMock(id=55))

        record = _record()
        stage.process(MagicMock(), record)

        self.assertEqual(record.status, "skipped")
        self.assertEqual(record.netbox_id, 55)
        stage.client.nb.dcim.modules.create.assert_not_called()

    def test_empty_bay_proceeds_to_create(self) -> None:
        stage = _stage()
        record = _record()
        stage.process(MagicMock(), record)

        self.assertEqual(record.status, "success")
        self.assertEqual(record.netbox_id, 99)

    def test_precheck_error_fails_record_not_job(self) -> None:
        stage = _stage()
        stage.client.nb.dcim.sites.get.side_effect = RuntimeError("NetBox API unreachable")

        record = _record()
        stage.process(MagicMock(), record)  # must not raise

        self.assertEqual(record.status, "failed")
        self.assertIn("unreachable", record.error_message)

    def test_missing_required_field_fails_record(self) -> None:
        stage = _stage()
        record = _record()
        del record.raw_data["module_type"]

        stage.process(MagicMock(), record)

        self.assertEqual(record.status, "failed")
        self.assertIn("module_type", record.error_message)


if __name__ == "__main__":
    unittest.main()
