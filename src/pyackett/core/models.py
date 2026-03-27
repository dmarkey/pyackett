"""Core data models for Pyackett."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ReleaseInfo:
    """Represents a single torrent search result."""

    title: str = ""
    guid: str = ""
    link: str | None = None
    details: str | None = None
    publish_date: datetime = field(default_factory=datetime.now)
    category: list[int] = field(default_factory=list)
    size: int | None = None
    files: int | None = None
    grabs: int | None = None
    description: str | None = None
    # Media IDs
    rage_id: int | None = None
    tvdb_id: int | None = None
    imdb: int | None = None
    tmdb: int | None = None
    tvmaze_id: int | None = None
    trakt_id: int | None = None
    douban_id: int | None = None
    # Collections
    genres: list[str] | None = None
    languages: list[str] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    year: int | None = None
    # Book fields
    author: str | None = None
    book_title: str | None = None
    publisher: str | None = None
    # Music fields
    artist: str | None = None
    album: str | None = None
    label: str | None = None
    track: str | None = None
    # Torrent stats
    seeders: int | None = None
    peers: int | None = None
    poster: str | None = None
    info_hash: str | None = None
    magnet_uri: str | None = None
    # Tracker requirements
    minimum_ratio: float | None = None
    minimum_seed_time: int | None = None
    download_volume_factor: float | None = None
    upload_volume_factor: float | None = None
    # Origin indexer info (not serialized to API)
    origin_id: str | None = None
    origin_name: str | None = None
    origin_type: str | None = None

    @property
    def gain(self) -> float | None:
        if self.seeders is not None and self.size is not None:
            return self.seeders * (self.size / 1024 / 1024 / 1024)
        return None


# Unicode dash characters (equivalent to \p{Pd})
_STANDARDIZE_DASHES = re.compile(r"[\u002D\u058A\u05BE\u1400\u1806\u2010-\u2015\u2E17\u2E1A\u2E3A\u2E3B\u2E40\u301C\u3030\u30A0\uFE31\uFE32\uFE58\uFE63\uFF0D]+")
_STANDARDIZE_QUOTES = re.compile(r"[\u0060\u00B4\u2018\u2019]")
_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -._()@/'[]+%")


@dataclass
class TorznabQuery:
    """Represents a Torznab search query."""

    query_type: str = "search"
    categories: list[int] = field(default_factory=list)
    extended: int = 0
    api_key: str = ""
    limit: int = 100
    offset: int = 0
    # Media IDs
    rage_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvmaze_id: int | None = None
    trakt_id: int | None = None
    douban_id: int | None = None
    # Search terms
    search_term: str | None = None
    season: int | None = None
    episode: str | None = None
    # Music
    album: str | None = None
    artist: str | None = None
    label: str | None = None
    track: str | None = None
    year: int | None = None
    genre: str | None = None
    # Book
    author: str | None = None
    title: str | None = None
    publisher: str | None = None
    # Flags
    is_test: bool = False
    cache: bool = True
    interactive_search: bool = False

    @property
    def imdb_id_short(self) -> str | None:
        if self.imdb_id:
            return self.imdb_id.lstrip("t")
        return None

    @property
    def is_search(self) -> bool:
        return self.query_type == "search"

    @property
    def is_tv_search(self) -> bool:
        return self.query_type == "tvsearch"

    @property
    def is_movie_search(self) -> bool:
        return self.query_type == "movie"

    @property
    def is_music_search(self) -> bool:
        return self.query_type == "music"

    @property
    def is_book_search(self) -> bool:
        return self.query_type == "book"

    @property
    def is_id_search(self) -> bool:
        return any([
            self.episode, self.season and self.season > 0,
            self.imdb_id, self.tvdb_id, self.rage_id,
            self.trakt_id, self.tvmaze_id, self.tmdb_id, self.douban_id,
            self.album, self.artist, self.label, self.genre, self.track,
            self.author, self.title, self.publisher, self.year,
        ])

    @property
    def is_rss_search(self) -> bool:
        return not self.search_term and not self.is_id_search

    @property
    def sanitized_search_term(self) -> str:
        term = self.search_term or ""
        term = _STANDARDIZE_DASHES.sub("-", term)
        term = _STANDARDIZE_QUOTES.sub("'", term)
        return "".join(c for c in term if c in _SAFE_CHARS)

    def get_episode_search_string(self) -> str:
        if not self.season or self.season == 0:
            return ""
        if not self.episode:
            return f"S{self.season:02d}"
        try:
            ep_num = int(self.episode)
            return f"S{self.season:02d}E{ep_num:02d}"
        except ValueError:
            return f"S{self.season:02d}E{self.episode}"

    def get_query_string(self) -> str:
        return f"{self.sanitized_search_term} {self.get_episode_search_string()}".strip()


@dataclass
class TorznabCapabilities:
    """Declares what an indexer supports."""

    search_modes: dict[str, list[str]] = field(default_factory=dict)
    categories: list[CategoryMapping] = field(default_factory=list)
    allow_raw_search: bool = False


@dataclass
class CategoryMapping:
    """Maps a site-specific category ID to a Torznab standard category."""

    site_id: str
    torznab_cat: str
    description: str = ""
    default: bool = False


@dataclass
class IndexerDefinition:
    """Parsed YAML indexer definition."""

    id: str = ""
    name: str = ""
    description: str = ""
    type: str = "public"
    language: str = "en-US"
    encoding: str = "UTF-8"
    request_delay: float | None = None
    links: list[str] = field(default_factory=list)
    legacy_links: list[str] = field(default_factory=list)
    follow_redirect: bool = False
    test_link_torrent: bool = True
    certificates: list[str] = field(default_factory=list)
    settings: list[dict[str, Any]] = field(default_factory=list)
    caps: dict[str, Any] = field(default_factory=dict)
    login: dict[str, Any] | None = None
    ratio: dict[str, Any] | None = None
    search: dict[str, Any] = field(default_factory=dict)
    download: dict[str, Any] | None = None

    @property
    def site_link(self) -> str:
        return self.links[0] if self.links else ""

    def get_capabilities(self) -> TorznabCapabilities:
        caps = TorznabCapabilities()
        caps_data = self.caps
        if not caps_data:
            return caps

        caps.allow_raw_search = caps_data.get("allowrawsearch", False)

        for mode_name, params in caps_data.get("modes", {}).items():
            caps.search_modes[mode_name] = params or []

        for cm in caps_data.get("categorymappings", []):
            caps.categories.append(CategoryMapping(
                site_id=str(cm["id"]),
                torznab_cat=cm["cat"],
                description=cm.get("desc", ""),
                default=cm.get("default", False),
            ))
        return caps
