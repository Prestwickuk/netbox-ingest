import unittest

from app.parsers.csv_parser import parse_csv
from app.parsers.json_parser import parse_json


class ParserTests(unittest.TestCase):
    def test_csv_normalizes_bom_headers_and_values(self) -> None:
        content = "\ufeff Name , Site , U_HEIGHT \n rack-1 , eu-1 , 42 \n".encode()

        rows = parse_csv(content, "racks")

        self.assertEqual(
            rows,
            [{"name": "rack-1", "site": "eu-1", "u_height": "42"}],
        )

    def test_csv_rejects_missing_required_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "u_height"):
            parse_csv(b"name,site\nrack-1,eu-1\n", "racks")

    def test_json_normalizes_keys_and_preserves_values(self) -> None:
        content = b'[{" Name ": "rack-1", "SITE": "eu-1", "U_HEIGHT": 42}]'

        rows = parse_json(content, "racks")

        self.assertEqual(
            rows,
            [{"name": "rack-1", "site": "eu-1", "u_height": 42}],
        )


if __name__ == "__main__":
    unittest.main()
