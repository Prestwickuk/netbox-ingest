import unittest
from unittest.mock import Mock

from app.netbox.client import NetBoxClient


class NetBoxClientAuthTests(unittest.TestCase):
    def _authorization_header(self, token: str) -> str:
        client = NetBoxClient("https://netbox.example.com", token)
        response = Mock(ok=True)
        response.json.return_value = {"netbox-version": "4.6.5"}
        client.nb.http_session.get = Mock(return_value=response)

        self.assertEqual(client.test_connection(), "4.6.5")
        return client.nb.http_session.get.call_args.kwargs["headers"]["authorization"]

    def test_v1_token_uses_token_scheme(self) -> None:
        self.assertEqual(
            self._authorization_header("Token legacy-token"),
            "Token legacy-token",
        )

    def test_v2_token_uses_bearer_scheme(self) -> None:
        self.assertEqual(
            self._authorization_header("Bearer nbt_key.secret"),
            "Bearer nbt_key.secret",
        )


if __name__ == "__main__":
    unittest.main()
