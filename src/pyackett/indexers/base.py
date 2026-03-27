"""Base class for native Python indexer implementations."""

from __future__ import annotations

import logging
from typing import Any

from pyackett.core.http import HttpClient
from pyackett.core.models import ReleaseInfo, TorznabCapabilities, TorznabQuery

logger = logging.getLogger("pyackett.indexers")


class NativeIndexer:
    """Base class for indexers that need custom Python logic.

    Subclass this for trackers that can't be handled by YAML definitions
    (e.g. complex auth, dynamic column layouts, API-based search).
    """

    id: str = ""
    name: str = ""
    description: str = ""
    site_link: str = ""
    indexer_type: str = "private"
    language: str = "en-US"

    def __init__(self, config: dict[str, Any] | None = None, client: HttpClient | None = None):
        self.config = config or {}
        self.client = client
        self.is_configured = False
        self.cookies: dict[str, str] = {}

    @property
    def definition(self):
        """Compatibility shim so NativeIndexer works with the manager."""
        return self

    @property
    def request_delay(self):
        return None

    def get_capabilities(self) -> TorznabCapabilities:
        return TorznabCapabilities()

    async def login(self, config: dict[str, Any] | None = None) -> bool:
        if config:
            self.config.update(config)
        self.is_configured = True
        return True

    async def search(self, query: TorznabQuery) -> list[ReleaseInfo]:
        raise NotImplementedError

    async def _get(self, url: str, **kwargs):
        """HTTP GET with cookies."""
        headers = kwargs.pop("headers", {})
        if self.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            headers.setdefault("Cookie", cookie_str)
        if self.config.get("useragent"):
            headers["User-Agent"] = self.config["useragent"]
        return await self.client.get(url, headers=headers, **kwargs)
