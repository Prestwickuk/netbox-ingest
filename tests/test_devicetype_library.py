import unittest

from app.devicetype_library import (
    build_index,
    model_display_name,
    slugify,
    validate_library_path,
)


class SlugifyTests(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(slugify("Dell PowerEdge R6615"), "dell-poweredge-r6615")

    def test_collapses_special_characters(self) -> None:
        self.assertEqual(slugify("FS.com  S5860-20SQ!"), "fs-com-s5860-20sq")

    def test_strips_leading_and_trailing_dashes(self) -> None:
        self.assertEqual(slugify(" (Cisco) "), "cisco")


class ModelDisplayNameTests(unittest.TestCase):
    def test_strips_directory_and_extension(self) -> None:
        self.assertEqual(model_display_name("device-types/Dell/PowerEdge-R6615.yaml"), "PowerEdge-R6615")
        self.assertEqual(model_display_name("device-types/Dell/DKMMLED185-207.yml"), "DKMMLED185-207")


class BuildIndexTests(unittest.TestCase):
    TREE = [
        {"path": "device-types/Dell/PowerEdge-R6615.yaml", "type": "blob"},
        {"path": "device-types/Dell/Aaa-First.yaml", "type": "blob"},
        {"path": "device-types/APC/AP8941.yml", "type": "blob"},
        {"path": "device-types/Dell", "type": "tree"},
        {"path": "module-types/Cisco/Some-Module.yaml", "type": "blob"},
        {"path": "elevation-images/Dell/front.png", "type": "blob"},
        {"path": "device-types/Dell/notes.txt", "type": "blob"},
    ]

    def test_only_device_type_yaml_blobs_are_indexed(self) -> None:
        index = build_index(self.TREE)
        self.assertEqual(list(index.keys()), ["APC", "Dell"])
        self.assertEqual([m["model"] for m in index["Dell"]], ["Aaa-First", "PowerEdge-R6615"])
        self.assertEqual(index["APC"][0]["path"], "device-types/APC/AP8941.yml")


class ValidateLibraryPathTests(unittest.TestCase):
    def test_accepts_device_type_paths(self) -> None:
        self.assertEqual(
            validate_library_path("device-types/Dell/PowerEdge-R6615.yaml"),
            "device-types/Dell/PowerEdge-R6615.yaml",
        )

    def test_rejects_other_paths(self) -> None:
        for bad in (
            "module-types/Cisco/Some-Module.yaml",
            "device-types/../secrets.yaml",
            "device-types/Dell/nested/file.yaml",
            "device-types/Dell/readme.txt",
            "/etc/passwd",
        ):
            with self.assertRaises(ValueError, msg=bad):
                validate_library_path(bad)


if __name__ == "__main__":
    unittest.main()
