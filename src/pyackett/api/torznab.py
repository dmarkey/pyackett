"""Torznab XML API response generation and query parsing."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from pyackett.core.models import ReleaseInfo, TorznabQuery

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
ATOM_NS = "http://www.w3.org/2005/Atom"

# Error codes per Torznab spec
ERROR_APIKEY = 100
ERROR_MISSING_PARAMETER = 200
ERROR_NOT_SUPPORTED = 201
ERROR_NOT_AVAILABLE = 203
ERROR_GENERAL = 900

_INVALID_XML_CHARS = re.compile(
    r"(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])"
    r"|[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F\uFEFF\uFFFE\uFFFF]"
)


def _clean_xml(text: str | None) -> str:
    """Remove invalid XML characters."""
    if text is None:
        return ""
    return _INVALID_XML_CHARS.sub("", text)


def _format_date(dt: datetime) -> str:
    """Format datetime as RFC 822."""
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z") or dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _torznab_attr(parent: Element, name: str, value: Any):
    """Add a <torznab:attr name="..." value="..."/> element."""
    if value is None:
        return
    el = SubElement(parent, f"{{{TORZNAB_NS}}}attr")
    el.set("name", name)
    el.set("value", str(value))


def results_to_xml(
    releases: list[ReleaseInfo],
    channel_title: str = "Pyackett",
    channel_link: str = "http://localhost:9117",
    self_link: str = "",
) -> str:
    """Serialize a list of ReleaseInfo objects to Torznab RSS XML."""
    rss = Element("rss")
    rss.set("version", "2.0")
    rss.set("xmlns:atom", ATOM_NS)
    rss.set("xmlns:torznab", TORZNAB_NS)

    channel = SubElement(rss, "channel")

    atom_link = SubElement(channel, f"{{{ATOM_NS}}}link")
    atom_link.set("href", self_link or channel_link)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    SubElement(channel, "title").text = channel_title
    SubElement(channel, "description").text = channel_title
    SubElement(channel, "link").text = channel_link
    SubElement(channel, "language").text = "en-US"
    SubElement(channel, "category").text = ""

    for r in releases:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = _clean_xml(r.title)
        SubElement(item, "guid").text = r.guid or r.link or r.magnet_uri or r.title

        if r.origin_id:
            idx_el = SubElement(item, "jackettindexer")
            idx_el.set("id", r.origin_id)
            idx_el.text = r.origin_name or r.origin_id

        if r.origin_type:
            SubElement(item, "type").text = r.origin_type

        if r.details:
            SubElement(item, "comments").text = r.details

        SubElement(item, "pubDate").text = _format_date(r.publish_date)

        if r.size is not None:
            SubElement(item, "size").text = str(r.size)
        if r.files is not None:
            SubElement(item, "files").text = str(r.files)
        if r.grabs is not None:
            SubElement(item, "grabs").text = str(r.grabs)

        SubElement(item, "description").text = _clean_xml(r.description)

        link_url = r.link or r.magnet_uri or ""
        SubElement(item, "link").text = link_url

        for cat_id in (r.category or []):
            SubElement(item, "category").text = str(cat_id)

        enclosure = SubElement(item, "enclosure")
        enclosure.set("url", link_url)
        if r.size is not None:
            enclosure.set("length", str(r.size))
        enclosure.set("type", "application/x-bittorrent")

        # Torznab attributes
        for cat_id in (r.category or []):
            _torznab_attr(item, "category", cat_id)

        _torznab_attr(item, "rageid", r.rage_id)
        _torznab_attr(item, "tvdbid", r.tvdb_id)
        if r.imdb is not None:
            _torznab_attr(item, "imdb", f"{r.imdb:07d}")
            _torznab_attr(item, "imdbid", f"tt{r.imdb:07d}")
        _torznab_attr(item, "tmdbid", r.tmdb)
        _torznab_attr(item, "tvmazeid", r.tvmaze_id)
        _torznab_attr(item, "traktid", r.trakt_id)
        _torznab_attr(item, "doubanid", r.douban_id)

        if r.genres:
            _torznab_attr(item, "genre", ", ".join(r.genres))
        for lang in (r.languages or []):
            _torznab_attr(item, "language", lang)
        for sub in (r.subs or []):
            _torznab_attr(item, "subs", sub)

        _torznab_attr(item, "year", r.year)
        _torznab_attr(item, "author", _clean_xml(r.author))
        _torznab_attr(item, "booktitle", _clean_xml(r.book_title))
        _torznab_attr(item, "publisher", _clean_xml(r.publisher))
        _torznab_attr(item, "artist", _clean_xml(r.artist))
        _torznab_attr(item, "album", _clean_xml(r.album))
        _torznab_attr(item, "label", _clean_xml(r.label))
        _torznab_attr(item, "track", _clean_xml(r.track))
        _torznab_attr(item, "seeders", r.seeders)
        _torznab_attr(item, "peers", r.peers)
        _torznab_attr(item, "coverurl", r.poster)
        _torznab_attr(item, "infohash", _clean_xml(r.info_hash))
        _torznab_attr(item, "magneturl", r.magnet_uri)
        _torznab_attr(item, "minimumratio", r.minimum_ratio)
        _torznab_attr(item, "minimumseedtime", r.minimum_seed_time)
        _torznab_attr(item, "downloadvolumefactor", r.download_volume_factor)
        _torznab_attr(item, "uploadvolumefactor", r.upload_volume_factor)

    xml_bytes = tostring(rss, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes


def error_xml(code: int, description: str) -> str:
    """Generate a Torznab error XML response."""
    rss = Element("error")
    rss.set("code", str(code))
    rss.set("description", description)
    xml_bytes = tostring(rss, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes


def caps_xml(
    indexer_id: str,
    indexer_name: str,
    categories: list[dict[str, Any]],
    search_modes: dict[str, list[str]],
) -> str:
    """Generate a Torznab capabilities XML response."""
    caps = Element("caps")

    server = SubElement(caps, "server")
    server.set("version", "0.1")
    server.set("title", indexer_name)

    limits = SubElement(caps, "limits")
    limits.set("max", "100")
    limits.set("default", "100")

    searching = SubElement(caps, "searching")
    mode_map = {
        "search": "search",
        "tv-search": "tv-search",
        "movie-search": "movie-search",
        "music-search": "music-search",
        "book-search": "book-search",
    }
    for mode_name, mode_tag in mode_map.items():
        el = SubElement(searching, mode_tag)
        if mode_name in search_modes:
            el.set("available", "yes")
            el.set("supportedParams", ",".join(search_modes[mode_name]))
        else:
            el.set("available", "no")
            el.set("supportedParams", "")

    cats_el = SubElement(caps, "categories")
    for cat in categories:
        cat_el = SubElement(cats_el, "category")
        cat_el.set("id", str(cat.get("id", "")))
        cat_el.set("name", cat.get("name", ""))

    xml_bytes = tostring(caps, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes


def parse_torznab_query(params: dict[str, str]) -> TorznabQuery:
    """Parse HTTP query parameters into a TorznabQuery."""
    query = TorznabQuery()

    query.query_type = params.get("t", "search")
    query.api_key = params.get("apikey", "") or params.get("passkey", "")
    query.search_term = params.get("q")
    query.extended = int(params.get("extended", "0") or "0")
    query.limit = int(params.get("limit", "100") or "100")
    query.offset = int(params.get("offset", "0") or "0")

    # Categories (can be comma-separated or multiple cat[] params)
    cat_str = params.get("cat", "")
    if cat_str:
        query.categories = [int(c) for c in cat_str.split(",") if c.strip().isdigit()]

    # Season / Episode
    season_str = params.get("season", "")
    if season_str:
        try:
            query.season = int(season_str)
        except ValueError:
            pass
    query.episode = params.get("ep")

    # Media IDs
    imdb = params.get("imdbid", "")
    if imdb:
        query.imdb_id = imdb if imdb.startswith("tt") else f"tt{imdb}"

    for field, param in [
        ("tmdb_id", "tmdbid"), ("tvdb_id", "tvdbid"), ("rage_id", "rid"),
        ("tvmaze_id", "tvmazeid"), ("trakt_id", "traktid"), ("douban_id", "doubanid"),
    ]:
        val = params.get(param, "")
        if val:
            try:
                setattr(query, field, int(val))
            except ValueError:
                pass

    # Music
    query.album = params.get("album")
    query.artist = params.get("artist")
    query.label = params.get("label")
    query.track = params.get("track")
    year_str = params.get("year", "")
    if year_str:
        try:
            query.year = int(year_str)
        except ValueError:
            pass
    query.genre = params.get("genre")

    # Book
    query.author = params.get("author")
    query.title = params.get("title")
    query.publisher = params.get("publisher")

    return query
