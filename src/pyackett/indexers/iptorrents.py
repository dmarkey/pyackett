"""IPTorrents native indexer implementation.

IPTorrents is a private tracker using cookie-based authentication
and HTML table scraping. Cannot be expressed as a YAML definition
due to dynamic column detection and complex search query building.
"""

from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from bs4 import Tag

from pyackett.core.categories import CATEGORIES
from pyackett.core.models import (
    CategoryMapping,
    ReleaseInfo,
    TorznabCapabilities,
    TorznabQuery,
)
from pyackett.engine.filters import _parse_fuzzy_time
from pyackett.engine.selectors import parse_html
from pyackett.indexers.base import NativeIndexer

logger = logging.getLogger("pyackett.indexers.iptorrents")

# IPT category ID -> (Torznab cat string, description)
_CATEGORY_MAP = {
    72: ("Movies", "Movies"),
    87: ("Movies/3D", "Movie/3D"),
    77: ("Movies/SD", "Movie/480p"),
    101: ("Movies/UHD", "Movie/4K"),
    89: ("Movies/BluRay", "Movie/BD-R"),
    90: ("Movies/HD", "Movie/BD-Rip"),
    96: ("Movies/SD", "Movie/Cam"),
    6: ("Movies/DVD", "Movie/DVD-R"),
    48: ("Movies/HD", "Movie/HD/Bluray"),
    54: ("Movies", "Movie/Kids"),
    62: ("Movies/SD", "Movie/MP4"),
    38: ("Movies/Foreign", "Movie/Non-English"),
    68: ("Movies", "Movie/Packs"),
    20: ("Movies/WEB-DL", "Movie/Web-DL"),
    100: ("Movies/HD", "Movie/x265"),
    7: ("Movies/SD", "Movie/Xvid"),
    73: ("TV", "TV"),
    26: ("TV/Documentary", "TV/Documentaries"),
    55: ("TV/Sport", "Sports"),
    78: ("TV/SD", "TV/480p"),
    23: ("TV/HD", "TV/BD"),
    24: ("TV/SD", "TV/DVD-R"),
    25: ("TV/SD", "TV/DVD-Rip"),
    66: ("TV/SD", "TV/Mobile"),
    82: ("TV/Foreign", "TV/Non-English"),
    65: ("TV", "TV/Packs"),
    83: ("TV/Foreign", "TV/Packs/Non-English"),
    79: ("TV/SD", "TV/SD/x264"),
    22: ("TV/WEB-DL", "TV/Web-DL"),
    5: ("TV/HD", "TV/x264"),
    99: ("TV/HD", "TV/x265"),
    4: ("TV/SD", "TV/Xvid"),
    74: ("Console", "Games"),
    2: ("Console/Other", "Games/Mixed"),
    47: ("Console/Other", "Games/Nintendo"),
    43: ("PC/Games", "Games/PC-ISO"),
    45: ("PC/Games", "Games/PC-Rip"),
    71: ("Console/PS4", "Games/Playstation"),
    50: ("Console/Wii", "Games/Wii"),
    44: ("Console/XBox", "Games/Xbox"),
    75: ("Audio", "Music"),
    3: ("Audio/MP3", "Music/Audio"),
    80: ("Audio/Lossless", "Music/Flac"),
    93: ("Audio", "Music/Packs"),
    37: ("Audio/Video", "Music/Video"),
    21: ("Audio/Other", "Podcast"),
    76: ("Other", "Miscellaneous"),
    60: ("TV/Anime", "Anime"),
    1: ("PC/0day", "Appz"),
    86: ("PC/0day", "Appz/Non-English"),
    64: ("Audio/Audiobook", "AudioBook"),
    35: ("Books", "Books"),
    102: ("Books", "Books/Non-English"),
    94: ("Books/Comics", "Comics"),
    95: ("Books/Other", "Educational"),
    98: ("Other", "Fonts"),
    69: ("PC/Mac", "Mac"),
    92: ("Books/Mags", "Magazines / Newspapers"),
    58: ("PC/Mobile-Other", "Mobile"),
    36: ("Other", "Pics/Wallpapers"),
    88: ("XXX", "XXX"),
    85: ("XXX/Other", "XXX/Magazines"),
    8: ("XXX", "XXX/Movie"),
    81: ("XXX", "XXX/Movie/0Day"),
    91: ("XXX/Pack", "XXX/Packs"),
    84: ("XXX/ImageSet", "XXX/Pics/Wallpapers"),
}

# Build reverse mapping: torznab cat ID -> list of IPT cat IDs
_TORZNAB_TO_IPT: dict[int, list[int]] = {}
_IPT_TO_TORZNAB: dict[int, list[int]] = {}
for ipt_id, (cat_str, _desc) in _CATEGORY_MAP.items():
    tz_id = CATEGORIES.get(cat_str)
    if tz_id:
        _TORZNAB_TO_IPT.setdefault(tz_id, []).append(ipt_id)
        _IPT_TO_TORZNAB.setdefault(ipt_id, []).append(tz_id)

_CLEAN_TITLE_RE1 = re.compile(r"[\u0000-\u0008\u000A-\u001F\u0100-\uFFFF]")
_CLEAN_TITLE_RE2 = re.compile(r"[\(\[\{]REQ(UEST(ED)?)?[\)\]\}]", re.IGNORECASE)
_CLEAN_TITLE_RE3 = re.compile(r"^\[[a-z0-9 ._-]+\][-._ ](?P<title>.*-[a-z0-9]+)$", re.IGNORECASE)


def _clean_title(title: str) -> str:
    title = _CLEAN_TITLE_RE1.sub("", title)
    title = _CLEAN_TITLE_RE2.sub("", title)
    m = _CLEAN_TITLE_RE3.match(title)
    if m:
        title = m.group("title")
    return title.strip(" -:")


def _parse_size(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    multipliers = {
        "b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4,
        "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
    }
    m = re.match(r"([\d.,]+)\s*([a-zA-Z]+)", text)
    if m:
        try:
            num = float(m.group(1).replace(",", ""))
            return int(num * multipliers.get(m.group(2).lower(), 1))
        except ValueError:
            pass
    return None


def _safe_int(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text.replace(",", "").strip())
    except (ValueError, TypeError):
        return None


class IPTorrents(NativeIndexer):
    id = "iptorrents"
    name = "IPTorrents"
    description = "IPTorrents is a Private site. Always a step ahead."
    site_link = "https://iptorrents.com/"
    indexer_type = "private"

    # Exposed as settings in the web UI
    settings = [
        {"name": "cookie", "type": "text", "label": "Cookie",
         "default": ""},
        {"name": "useragent", "type": "text", "label": "User-Agent (must match your browser)",
         "default": ""},
        {"name": "sort", "type": "select", "label": "Sort", "default": "time",
         "options": {"time": "created", "size": "size", "seeders": "seeders", "name": "title"}},
        {"name": "freeleech", "type": "checkbox", "label": "Freeleech only", "default": False},
        {"name": "info", "type": "info", "label": "Setup",
         "default": "In IPTorrents Settings: set Torrents per page to 100, tick Show files count, set category column to Icons."},
    ]

    def get_capabilities(self) -> TorznabCapabilities:
        caps = TorznabCapabilities()
        caps.search_modes = {
            "search": ["q"],
            "tv-search": ["q", "season", "ep", "imdbid", "genre"],
            "movie-search": ["q", "imdbid", "genre"],
            "music-search": ["q"],
            "book-search": ["q"],
        }
        caps.categories = [
            CategoryMapping(site_id=str(ipt_id), torznab_cat=cat_str, description=desc)
            for ipt_id, (cat_str, desc) in _CATEGORY_MAP.items()
        ]
        return caps

    async def login(self, config: dict[str, Any] | None = None) -> bool:
        if config:
            self.config.update(config)
        cookie = self.config.get("cookie", "")
        if not cookie:
            logger.error("IPTorrents requires a cookie to authenticate")
            return False
        # Parse cookie string into dict
        for part in cookie.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                self.cookies[k.strip()] = v.strip()
        # Test login
        try:
            resp = await self._get(self.site_link + "t")
            if "/lout.php" not in resp.text:
                logger.error("IPTorrents cookie is invalid or expired")
                return False
        except Exception as e:
            logger.error(f"IPTorrents login test failed: {e}")
            return False
        self.is_configured = True
        return True

    async def search(self, query: TorznabQuery) -> list[ReleaseInfo]:
        # Build query params
        params: dict[str, str] = {}

        # Category params — IPT uses ?72=&5=&22= format (key=empty value)
        ipt_cats = set()
        for tz_cat in (query.categories or []):
            if tz_cat in _TORZNAB_TO_IPT:
                ipt_cats.update(_TORZNAB_TO_IPT[tz_cat])
            # Also check parent category
            parent = (tz_cat // 1000) * 1000
            if parent in _TORZNAB_TO_IPT:
                ipt_cats.update(_TORZNAB_TO_IPT[parent])
        for cat_id in ipt_cats:
            params[str(cat_id)] = ""

        if self.config.get("freeleech"):
            params["free"] = "on"

        # Build search string (IPT uses sphinx boolean syntax)
        search_parts = []
        if query.imdb_id:
            search_parts.append(f"+({query.imdb_id})")
            params["qf"] = "all"  # search in description for IMDB
        elif query.genre:
            search_parts.append(f"+({query.genre})")

        search_term = query.get_query_string()
        if search_term:
            if query.season and not query.episode:
                search_term += "*"  # season pack wildcard
            search_parts.append(f"+({search_term})")

        if search_parts:
            params["q"] = " ".join(search_parts)

        params["o"] = self.config.get("sort", "time")

        if query.limit > 0 and query.offset > 0:
            page = query.offset // query.limit + 1
            params["p"] = str(page)

        # Build URL — IPT uses ?key=&key= format, not standard urlencode
        param_parts = []
        for k, v in params.items():
            if v:
                param_parts.append(f"{k}={v}")
            else:
                param_parts.append(k)
        url = self.site_link + "t?" + "&".join(param_parts)

        resp = await self._get(url, params=None)

        if "/lout.php" not in resp.text:
            logger.error("IPTorrents: not logged in, cookie may have expired")
            return []

        return self._parse_results(resp.text, query)

    def _parse_results(self, html: str, query: TorznabQuery) -> list[ReleaseInfo]:
        doc = parse_html(html)
        results = []

        # Find header columns to determine dynamic indices
        headers = []
        for th in doc.select("table#torrents > thead > tr > th"):
            headers.append(th.get_text(strip=True))

        size_idx = _find_col(headers, "Sort by size", 5)
        files_idx = _find_col(headers, "Sort by files")

        rows = doc.select("table#torrents > tbody > tr")
        if not rows:
            # tbody fallback
            rows = doc.select("table#torrents tr")

        for row in rows:
            try:
                release = self._parse_row(row, query, headers, size_idx, files_idx)
                if release:
                    results.append(release)
            except Exception as e:
                logger.debug(f"IPT row parse error: {e}")
                continue

        return results

    def _parse_row(
        self, row: Tag, query: TorznabQuery,
        headers: list[str], size_idx: int, files_idx: int,
    ) -> ReleaseInfo | None:
        title_link = row.select_one("a.hv")
        if not title_link:
            return None

        title = _clean_title(title_link.get_text())
        if not title:
            return None

        # Details URL
        href = title_link.get("href", "")
        details = self.site_link + href.lstrip("/")

        # Download link
        dl_link = row.select_one("a[href^='/download.php/']")
        link = None
        if dl_link:
            link = self.site_link + dl_link.get("href", "").lstrip("/")

        # Description, date, tags
        sub_div = row.select_one("div.sub")
        description = ""
        publish_date = datetime.now(timezone.utc)
        if sub_div:
            sub_text = sub_div.get_text()
            parts = sub_text.split("|")
            date_part = parts[-1].strip()
            by_split = date_part.split(" by ")
            try:
                publish_date = _parse_fuzzy_time(by_split[0].strip())
            except Exception:
                pass
            if len(parts) > 1:
                description = "Tags: " + parts[0].strip()
            if len(by_split) > 1:
                description += " Uploaded by: " + by_split[-1].strip()

        # Category from icon link
        cat_icon = row.select_one("td:nth-of-type(1) a[href^='?']")
        category = []
        if cat_icon:
            cat_href = cat_icon.get("href", "")
            cat_id_str = cat_href.lstrip("?").split("&")[0] if cat_href else ""
            try:
                cat_id = int(cat_id_str)
                category = _IPT_TO_TORZNAB.get(cat_id, [])
            except ValueError:
                pass

        # Size
        cells = row.find_all("td")
        size = None
        if size_idx < len(cells):
            size = _parse_size(cells[size_idx].get_text())

        # Files
        files = None
        if files_idx >= 0 and files_idx < len(cells):
            files = _safe_int(cells[files_idx].get_text().replace("Go to files", ""))

        # Dynamic column indices for stats
        n_cols = len(cells)
        col = 7 if n_cols == 10 else 6
        grabs_idx = _find_col(headers, "Sort by snatches", col)
        seeders_idx = _find_col(headers, "Sort by seeders", col + 1 if grabs_idx >= 0 else col)
        leechers_idx = _find_col(headers, "Sort by leechers", seeders_idx + 1 if seeders_idx >= 0 else col + 1)

        grabs = _safe_int(cells[grabs_idx].get_text()) if 0 <= grabs_idx < n_cols else None
        seeders = _safe_int(cells[seeders_idx].get_text()) if 0 <= seeders_idx < n_cols else None
        leechers = _safe_int(cells[leechers_idx].get_text()) if 0 <= leechers_idx < n_cols else None

        is_free = row.select_one("span.free") is not None

        return ReleaseInfo(
            title=title,
            guid=details,
            link=link,
            details=details,
            publish_date=publish_date,
            category=category,
            description=description,
            size=size,
            files=files,
            grabs=grabs,
            seeders=seeders,
            peers=(seeders or 0) + (leechers or 0),
            download_volume_factor=0.0 if is_free else 1.0,
            upload_volume_factor=1.0,
            minimum_ratio=1.0,
            minimum_seed_time=1209600,  # 14 days
            origin_id=self.id,
            origin_name=self.name,
            origin_type=self.indexer_type,
        )


def _find_col(headers: list[str], name: str, default: int = -1) -> int:
    for i, h in enumerate(headers):
        if h == name:
            return i
    return default
