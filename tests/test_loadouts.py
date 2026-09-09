import unittest
from types import SimpleNamespace

from app.loadouts import expand_loadout_rows, loadout_to_yaml, loadouts_from_yaml

BAYS = {
    "DPU1": {"manufacturer": "Nvidia", "module_type": "BlueField-3 DPU B3220"},
    "NIC1": {"manufacturer": "Nvidia", "module_type": "ConnectX-7 MCX75310AAS-NEAT"},
}


class YamlRoundTripTests(unittest.TestCase):
    def test_export_then_import_round_trips(self) -> None:
        loadout = SimpleNamespace(
            name="A126GS standard", manufacturer="Supermicro",
            device_type="AS -A126GS-TNBR", bays=BAYS)
        text = loadout_to_yaml(loadout)
        parsed = loadouts_from_yaml(text)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "A126GS standard")
        self.assertEqual(parsed[0]["device_type"], "AS -A126GS-TNBR")
        self.assertEqual(parsed[0]["bays"], BAYS)

    def test_import_accepts_multiple_documents(self) -> None:
        one = loadout_to_yaml(SimpleNamespace(
            name="a", manufacturer="M", device_type="T", bays=BAYS))
        two = loadout_to_yaml(SimpleNamespace(
            name="b", manufacturer="M", device_type="T", bays=BAYS))
        parsed = loadouts_from_yaml(one + "---\n" + two)
        self.assertEqual([p["name"] for p in parsed], ["a", "b"])

    def test_import_accepts_a_yaml_list(self) -> None:
        text = """\
- name: a
  manufacturer: M
  device_type: T
  bays:
    DPU1: {manufacturer: Nvidia, module_type: B3220}
- name: b
  manufacturer: M
  device_type: T
  bays:
    DPU1: {manufacturer: Nvidia, module_type: B3220}
"""
        self.assertEqual([p["name"] for p in loadouts_from_yaml(text)], ["a", "b"])

    def test_import_rejects_missing_fields(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            loadouts_from_yaml("name: x\nmanufacturer: M\nbays: {}\n")
        self.assertIn("device_type", str(ctx.exception))

    def test_import_rejects_bay_without_module_type(self) -> None:
        text = """\
name: x
manufacturer: M
device_type: T
bays:
  DPU1: {manufacturer: Nvidia}
"""
        with self.assertRaises(ValueError) as ctx:
            loadouts_from_yaml(text)
        self.assertIn("DPU1", str(ctx.exception))

    def test_import_rejects_invalid_yaml(self) -> None:
        with self.assertRaises(ValueError):
            loadouts_from_yaml("name: [unclosed")


class ExpandLoadoutRowsTests(unittest.TestCase):
    def test_expands_device_cross_bay(self) -> None:
        devices = [
            {"device": "gpu-01", "site": "DC1"},
            {"device": "gpu-02", "site": "DC1"},
        ]
        rows = expand_loadout_rows(BAYS, devices, status="planned")

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], {
            "device": "gpu-01", "site": "DC1", "module_bay": "DPU1",
            "module_type": "BlueField-3 DPU B3220", "manufacturer": "Nvidia",
            "status": "planned",
        })
        # device order preserved, bays sorted within a device
        self.assertEqual([(r["device"], r["module_bay"]) for r in rows],
                         [("gpu-01", "DPU1"), ("gpu-01", "NIC1"),
                          ("gpu-02", "DPU1"), ("gpu-02", "NIC1")])

    def test_default_status_is_active(self) -> None:
        rows = expand_loadout_rows(BAYS, [{"device": "d", "site": "s"}])
        self.assertTrue(all(r["status"] == "active" for r in rows))

    def test_no_devices_yields_no_rows(self) -> None:
        self.assertEqual(expand_loadout_rows(BAYS, []), [])


if __name__ == "__main__":
    unittest.main()
