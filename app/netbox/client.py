import logging
import time

import pynetbox
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


class NetBoxClient:
    def __init__(self, url: str, token: str):
        self.netbox_url = url.rstrip("/")
        self.nb = pynetbox.api(self.netbox_url, token=token)

        # Retry on 429 and 5xx with exponential backoff
        retry = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PATCH"],
        )
        session = requests.Session()
        session.mount("http://", HTTPAdapter(max_retries=retry))
        session.mount("https://", HTTPAdapter(max_retries=retry))
        self.nb.http_session = session

    def test_connection(self) -> str:
        """Return NetBox version string or raise on failure."""
        status = self.nb.status()
        return status.get("netbox-version", "unknown")
