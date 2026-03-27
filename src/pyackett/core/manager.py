"""IndexerManager - loads, configures, and manages all indexers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from typing import TYPE_CHECKING

from pyackett.core.models import IndexerDefinition, ReleaseInfo, TorznabQuery
from pyackett.engine.cardigann import CardigannIndexer, load_all_definitions

if TYPE_CHECKING:
    from pyackett.core.http import HttpClient

logger = logging.getLogger("pyackett.manager")


class IndexerManager:
    """Manages all loaded indexer instances."""

    def __init__(
        self,
        definitions_dir: Path | None = None,
        config_dir: Path | None = None,
        client: Any | None = None,
    ):
        self._definitions: dict[str, IndexerDefinition] = {}
        self._indexers: dict[str, CardigannIndexer] = {}
        self._definitions_dir = definitions_dir
        self._config_dir = config_dir or Path.home() / ".config" / "pyackett"
        self._client = client
        self._config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def definitions(self) -> dict[str, IndexerDefinition]:
        return self._definitions

    @property
    def configured_indexers(self) -> dict[str, CardigannIndexer]:
        return {k: v for k, v in self._indexers.items() if v.is_configured}

    @property
    def all_indexers(self) -> dict[str, CardigannIndexer]:
        return self._indexers

    def load_definitions(self, directory: Path | None = None):
        """Load all YAML definitions from a directory."""
        directory = directory or self._definitions_dir
        if directory is None:
            # Try bundled definitions
            bundled = Path(__file__).parent.parent / "definitions"
            if bundled.exists():
                directory = bundled
            else:
                logger.warning("No definitions directory specified or found")
                return

        self._definitions = load_all_definitions(directory)
        logger.info(f"Loaded {len(self._definitions)} indexer definitions")

        # Create indexer instances for all YAML definitions
        for defn_id, defn in self._definitions.items():
            if defn_id not in self._indexers:
                saved_config = self._load_indexer_config(defn_id)
                self._indexers[defn_id] = CardigannIndexer(
                    definition=defn,
                    config=saved_config,
                    client=self._client,
                )
                if saved_config:
                    self._indexers[defn_id].is_configured = True

        # Register native Python indexers
        self._load_native_indexers()

    def get_indexer(self, indexer_id: str) -> CardigannIndexer | None:
        """Get an indexer by ID."""
        return self._indexers.get(indexer_id)

    async def configure_indexer(self, indexer_id: str, config: dict[str, Any]) -> bool:
        """Configure an indexer with credentials/settings and attempt login."""
        indexer = self._indexers.get(indexer_id)
        if not indexer:
            # Check if we have the definition
            defn = self._definitions.get(indexer_id)
            if not defn:
                logger.error(f"Unknown indexer: {indexer_id}")
                return False
            indexer = CardigannIndexer(definition=defn, config=config, client=self._client)
            self._indexers[indexer_id] = indexer

        success = await indexer.login(config)
        if success:
            self._save_indexer_config(indexer_id, indexer.config)
        return success

    def remove_indexer(self, indexer_id: str):
        """Remove an indexer configuration."""
        if indexer_id in self._indexers:
            self._indexers[indexer_id].is_configured = False
            self._indexers[indexer_id].config = {}
            config_path = self._config_dir / "indexers" / f"{indexer_id}.json"
            if config_path.exists():
                config_path.unlink()

    async def search(
        self,
        query: TorznabQuery,
        indexer_ids: list[str] | None = None,
    ) -> list[ReleaseInfo]:
        """Search across configured indexers.

        Args:
            query: The search query.
            indexer_ids: Optional list of specific indexer IDs to search.
                         If None, searches all configured indexers.

        Returns:
            Combined list of results from all searched indexers.
        """
        targets = {}
        if indexer_ids:
            for iid in indexer_ids:
                idx = self._indexers.get(iid)
                if idx and idx.is_configured:
                    targets[iid] = idx
        else:
            targets = self.configured_indexers

        if not targets:
            logger.warning("No configured indexers to search")
            return []

        all_results: list[ReleaseInfo] = []

        async def _search_one(indexer_id: str, indexer):
            try:
                results = await indexer.search(query)
                logger.info(f"Indexer {indexer_id}: {len(results)} results")
                return results
            except Exception as e:
                logger.error(f"Search failed for {indexer_id}: {e}")
                return []

        import asyncio
        tasks = [_search_one(iid, idx) for iid, idx in targets.items()]
        results_lists = await asyncio.gather(*tasks)
        for results in results_lists:
            all_results.extend(results)

        return all_results

    def _load_native_indexers(self):
        """Register native Python indexer implementations."""
        from pyackett.indexers.iptorrents import IPTorrents

        for cls in [IPTorrents]:
            if cls.id not in self._indexers:
                saved_config = self._load_indexer_config(cls.id)
                idx = cls(config=saved_config, client=self._client)
                if saved_config:
                    idx.is_configured = True
                self._indexers[cls.id] = idx
                # Add to definitions list for discoverability
                if cls.id not in self._definitions:
                    self._definitions[cls.id] = idx

    def _load_indexer_config(self, indexer_id: str) -> dict[str, Any] | None:
        """Load saved indexer configuration."""
        config_path = self._config_dir / "indexers" / f"{indexer_id}.json"
        if config_path.exists():
            try:
                return json.loads(config_path.read_text())
            except Exception:
                pass
        return None

    def _save_indexer_config(self, indexer_id: str, config: dict[str, Any]):
        """Save indexer configuration to disk."""
        indexers_dir = self._config_dir / "indexers"
        indexers_dir.mkdir(parents=True, exist_ok=True)
        config_path = indexers_dir / f"{indexer_id}.json"
        config_path.write_text(json.dumps(config, indent=2))

    def list_available(self) -> list[dict[str, str]]:
        """List all available indexers (YAML + native)."""
        result = []
        for defn in self._definitions.values():
            result.append({
                "id": getattr(defn, "id", ""),
                "name": getattr(defn, "name", ""),
                "description": getattr(defn, "description", ""),
                "type": getattr(defn, "type", getattr(defn, "indexer_type", "")),
                "language": getattr(defn, "language", "en-US"),
                "site_link": getattr(defn, "site_link", ""),
                "configured": getattr(defn, "id", "") in self.configured_indexers,
                "settings": getattr(defn, "settings", []),
            })
        return result

    def list_configured(self) -> list[dict[str, str]]:
        """List configured indexers."""
        return [
            {
                "id": idx.id,
                "name": idx.name,
                "type": idx.indexer_type,
                "site_link": idx.site_link,
            }
            for idx in self.configured_indexers.values()
        ]
