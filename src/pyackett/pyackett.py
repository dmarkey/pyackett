"""Main Pyackett class - the public API for library usage."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pyackett.core.cache import ResultCache
from pyackett.core.manager import IndexerManager
from pyackett.core.models import ReleaseInfo, TorznabQuery

logger = logging.getLogger("pyackett")


class Pyackett:
    """Main entry point for using Pyackett as a Python library.

    Example usage::

        from pyackett import Pyackett

        pk = Pyackett()
        pk.load_definitions("/path/to/definitions")

        # Configure an indexer
        await pk.configure_indexer("1337x", {})

        # Search
        results = await pk.search("breaking bad", categories=[5000])
        for r in results:
            print(r.title, r.seeders, r.magnet_uri)

        # Or run the web server
        pk.serve(port=9117)
    """

    def __init__(
        self,
        config_dir: str | Path | None = None,
        definitions_dir: str | Path | None = None,
        proxy: str | None = None,
        timeout: float = 30.0,
        connect_timeout: float = 5.0,
    ):
        """
        Args:
            config_dir: Directory for persistent config/cache.
            definitions_dir: Local directory containing YAML definitions.
            proxy: Proxy URL (socks5://host:port, http://host:port, etc.).
            timeout: Total request timeout in seconds (default: 30).
            connect_timeout: Connection establishment timeout in seconds (default: 5).
        """
        self._config_dir = Path(config_dir) if config_dir else Path.home() / ".config" / "pyackett"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._definitions_dir = Path(definitions_dir) if definitions_dir else None
        self._proxy = proxy

        from pyackett.core.http import create_http_client
        self._client = create_http_client(proxy=proxy, timeout=timeout, connect_timeout=connect_timeout)

        # Load persisted CF cookies so we don't need to re-solve after restart
        self._client._cf_cache_path = self._config_dir / "cf_cookies.json"
        self._client.load_cf_cache(self._client._cf_cache_path)

        self._manager = IndexerManager(
            definitions_dir=self._definitions_dir,
            config_dir=self._config_dir,
            client=self._client,
        )
        self._cache = ResultCache()

    @property
    def manager(self) -> IndexerManager:
        """Access the underlying IndexerManager."""
        return self._manager

    def load_definitions(self, directory: str | Path | None = None):
        """Load YAML indexer definitions from a directory.

        If no directory is specified, uses the bundled definitions or the
        directory passed to the constructor.
        """
        if directory:
            self._manager.load_definitions(Path(directory))
        else:
            self._manager.load_definitions()

    def load_definitions_from_github(
        self,
        source: str = "jackett",
        branch: str = "master",
        force_update: bool = False,
    ):
        """Download and load YAML definitions from GitHub.

        Downloads the Jackett (or Prowlarr) definition files from GitHub
        and caches them locally in the config directory.

        Args:
            source: "jackett" or "prowlarr".
            branch: Git branch to download from.
            force_update: If True, re-download even if cached.
        """
        from pyackett.core.definitions_fetcher import (
            fetch_definitions_if_needed,
            update_definitions,
        )

        if force_update:
            defs_dir = update_definitions(self._config_dir, source=source, branch=branch)
        else:
            defs_dir = fetch_definitions_if_needed(self._config_dir, source=source, branch=branch)

        self._manager.load_definitions(defs_dir)

    async def configure_indexer(self, indexer_id: str, config: dict[str, Any] | None = None) -> bool:
        """Configure an indexer with settings/credentials.

        For public indexers, an empty dict is usually sufficient.
        For private indexers, provide credentials as required by the definition.

        Returns True if configuration (and login) succeeded.
        """
        return await self._manager.configure_indexer(indexer_id, config or {})

    def remove_indexer(self, indexer_id: str):
        """Remove an indexer configuration."""
        self._manager.remove_indexer(indexer_id)

    async def search(
        self,
        query: str | TorznabQuery,
        categories: list[int] | None = None,
        indexer_ids: list[str] | None = None,
        limit: int = 100,
    ) -> list[ReleaseInfo]:
        """Search across configured indexers.

        Args:
            query: Search string or a TorznabQuery object.
            categories: Optional Torznab category IDs to filter by.
            indexer_ids: Optional list of indexer IDs to search. Searches all if None.
            limit: Maximum number of results.

        Returns:
            List of ReleaseInfo results.
        """
        if isinstance(query, str):
            tq = TorznabQuery(
                search_term=query,
                categories=categories or [],
                limit=limit,
            )
        else:
            tq = query

        return await self._manager.search(tq, indexer_ids=indexer_ids)

    async def resolve_download(self, indexer_id: str, details_url: str) -> str | None:
        """Resolve a download link by visiting the details page.

        For indexers like 1337x where the search result 'link' points to
        a details page, this fetches the page and extracts the actual
        .torrent URL or magnet URI using the definition's download selectors.

        Returns a magnet URI, .torrent URL, or None.
        """
        indexer = self._manager.get_indexer(indexer_id)
        if indexer and hasattr(indexer, 'resolve_download'):
            return await indexer.resolve_download(details_url)
        return None

    def search_sync(
        self,
        query: str | TorznabQuery,
        categories: list[int] | None = None,
        indexer_ids: list[str] | None = None,
        limit: int = 100,
    ) -> list[ReleaseInfo]:
        """Synchronous search wrapper for non-async code."""
        return asyncio.run(self.search(query, categories, indexer_ids, limit))

    def list_available(self) -> list[dict[str, str]]:
        """List all available indexer definitions."""
        return self._manager.list_available()

    def list_configured(self) -> list[dict[str, str]]:
        """List configured indexers."""
        return self._manager.list_configured()

    def serve(self, host: str = "0.0.0.0", port: int = 9117, api_key: str | None = None):
        """Start the Torznab-compatible web server (blocking).

        Args:
            host: Bind address.
            port: Port number.
            api_key: API key for authentication. Auto-generated if not provided.
        """
        import uvicorn
        from pyackett.server.app import create_app

        app = create_app(
            manager=self._manager,
            api_key=api_key,
            config_dir=self._config_dir,
        )
        logger.info(f"Starting Pyackett server on {host}:{port}")
        uvicorn.run(app, host=host, port=port, log_level="info")

    def create_app(self, api_key: str | None = None):
        """Create the FastAPI app without running it (for ASGI deployment)."""
        from pyackett.server.app import create_app
        return create_app(
            manager=self._manager,
            api_key=api_key,
            config_dir=self._config_dir,
        )

    async def close(self):
        """Close the HTTP client and save state."""
        if self._client._cf_cache_path:
            self._client.save_cf_cache(self._client._cf_cache_path)
        await self._client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
