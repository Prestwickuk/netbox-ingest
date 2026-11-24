import unittest
import uuid
from unittest.mock import MagicMock, patch

from app.worker.stages.cables import CableStage


def _stage() -> CableStage:
    with patch("app.worker.stages.base.NetBoxClient"):
        stage = CableStage("https://netbox.example.com", "token")
    stage.client = MagicMock()
    stage.client.netbox_url = "https://netbox.example.com"
    nb = stage.client.nb
    nb.dcim.sites.get.return_value = MagicMock(id=1)
    nb.dcim.devices.get.return_value = MagicMock(id=10)
    nb.dcim.interfaces.get.side_effect = [MagicMock(id=100), MagicMock(id=200)]
    nb.dcim.cables.create.return_value = MagicMock(id=99)
    return stage


def _record(**overrides) -> MagicMock:
    record = MagicMock(id=uuid.uuid4())
    record.raw_data = {
        "a_device": "gpu-node-01",
        "a_site": "DC1",
        "a_termination_type": "interface",
        "a_termination_name": "eth0",
        "b_device": "leaf-01",
        "b_site": "DC1",
        "b_termination_type": "interface",
        "b_termination_name": "Ethernet1",
        **overrides,
    }
    return record


class CableStageCreateTests(unittest.TestCase):
    def test_creates_cable_with_default_status(self) -> None:
        stage = _stage()
        netbox_id, url = stage.create(MagicMock(), _record())

        self.assertEqual(netbox_id, 99)
        self.assertEqual(url, "https://netbox.example.com/dcim/cables/99/")
        payload = stage.client.nb.dcim.cables.create.call_args.kwargs
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["a_terminations"], [{"object_type": "dcim.interface", "object_id": 100}])
        self.assertEqual(payload["b_terminations"], [{"object_type": "dcim.interface", "object_id": 200}])

    def test_cable_type_column_is_sent_as_the_netbox_type_field(self) -> None:
        # NetBox's API field is `type`. A `cable_type` key is silently ignored by
        # the API, which used to leave every uploaded cable without a type.
        stage = _stage()
        stage.create(MagicMock(), _record(cable_type="cat6"))

        payload = stage.client.nb.dcim.cables.create.call_args.kwargs
        self.assertEqual(payload["type"], "cat6")
        self.assertNotIn("cable_type", payload)

    def test_label_and_color_passed_through(self) -> None:
        stage = _stage()
        stage.create(MagicMock(), _record(label="CAB-001", color="blue", status="connected"))

        payload = stage.client.nb.dcim.cables.create.call_args.kwargs
        self.assertEqual(payload["label"], "CAB-001")
        self.assertEqual(payload["color"], "blue")
        self.assertEqual(payload["status"], "connected")

    def test_blank_optional_columns_are_not_sent(self) -> None:
        stage = _stage()
        stage.create(MagicMock(), _record(label="", cable_type="", color=""))

        payload = stage.client.nb.dcim.cables.create.call_args.kwargs
        for key in ("label", "type", "cable_type", "color"):
            self.assertNotIn(key, payload)


if __name__ == "__main__":
    unittest.main()
