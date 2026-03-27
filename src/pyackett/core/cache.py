"""Simple TTL cache for search results."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cachetools import TTLCache

from pyackett.core.models import ReleaseInfo, TorznabQuery


class ResultCache:
    """Caches search results per indexer per query."""

    def __init__(self, maxsize: int = 1000, ttl: int = 3600):
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)

    @staticmethod
    def _make_key(indexer_id: str, query: TorznabQuery) -> str:
        """Create a deterministic cache key from indexer + query."""
        parts = {
            "indexer": indexer_id,
            "type": query.query_type,
            "q": query.search_term or "",
            "cats": sorted(query.categories),
            "season": query.season,
            "ep": query.episode,
            "imdb": query.imdb_id,
            "tmdb": query.tmdb_id,
            "tvdb": query.tvdb_id,
            "limit": query.limit,
            "offset": query.offset,
        }
        raw = json.dumps(parts, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, indexer_id: str, query: TorznabQuery) -> list[ReleaseInfo] | None:
        """Get cached results, or None if not cached."""
        key = self._make_key(indexer_id, query)
        return self._cache.get(key)

    def put(self, indexer_id: str, query: TorznabQuery, results: list[ReleaseInfo]):
        """Cache results."""
        key = self._make_key(indexer_id, query)
        self._cache[key] = results

    def clear(self, indexer_id: str | None = None):
        """Clear cache. If indexer_id given, only clear that indexer's entries."""
        if indexer_id is None:
            self._cache.clear()
        else:
            # TTLCache doesn't support prefix deletion, so clear all
            # (acceptable since reconfiguring an indexer is rare)
            self._cache.clear()
