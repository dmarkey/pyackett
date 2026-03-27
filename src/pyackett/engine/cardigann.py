"""Cardigann engine - interprets YAML indexer definitions at runtime.

This is the core engine that makes 550+ YAML definitions work without custom code.
It handles:
  - Loading and parsing YAML definitions
  - Template expansion for search URLs, inputs, and selectors
  - HTML/JSON response parsing with CSS selectors and JSONPath
  - Filter chain execution
  - Login flow orchestration
  - Result extraction into ReleaseInfo objects
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import Tag
from ruamel.yaml import YAML

from pyackett.core.categories import CATEGORIES, resolve_category
from pyackett.core.models import (
    CategoryMapping,
    IndexerDefinition,
    ReleaseInfo,
    TorznabQuery,
)
from pyackett.engine.filters import apply_filters
from pyackett.engine.selectors import (
    extract_from_json,
    extract_rows_from_json,
    extract_text,
    parse_html,
    parse_json,
    query_selector,
    query_selector_all,
)
from pyackett.engine.template import apply_template

logger = logging.getLogger("pyackett.cardigann")

yaml = YAML()
yaml.preserve_quotes = True


def load_definition(path: Path) -> IndexerDefinition:
    """Load and parse a YAML indexer definition file."""
    with open(path) as f:
        raw = yaml.load(f)

    defn = IndexerDefinition(
        id=raw.get("id", path.stem),
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        type=raw.get("type", "public"),
        language=raw.get("language", "en-US"),
        encoding=raw.get("encoding", "UTF-8"),
        request_delay=raw.get("requestDelay"),
        links=raw.get("links", []),
        legacy_links=raw.get("legacylinks", []),
        follow_redirect=raw.get("followredirect", False),
        test_link_torrent=raw.get("testlinktorrent", True),
        certificates=raw.get("certificates", []),
        settings=raw.get("settings", []),
        caps=raw.get("caps", {}),
        login=raw.get("login"),
        ratio=raw.get("ratio"),
        search=raw.get("search", {}),
        download=raw.get("download"),
    )
    return defn


def load_all_definitions(directory: Path) -> dict[str, IndexerDefinition]:
    """Load all YAML definitions from a directory."""
    definitions = {}
    if not directory.exists():
        return definitions
    for yml_path in sorted(directory.glob("*.yml")):
        try:
            defn = load_definition(yml_path)
            definitions[defn.id] = defn
        except Exception as e:
            logger.warning(f"Failed to load definition {yml_path.name}: {e}")
    return definitions


class CardigannIndexer:
    """Runtime indexer powered by a YAML definition."""

    def __init__(
        self,
        definition: IndexerDefinition,
        config: dict[str, Any] | None = None,
        client: Any | None = None,
    ):
        self.definition = definition
        self.config = config or {}
        self.client = client
        self.cookies: dict[str, str] = {}
        self.is_configured = False

        # Build category mappings
        self._site_to_torznab: dict[str, list[int]] = {}
        self._torznab_to_site: dict[int, list[str]] = {}
        self._default_categories: list[str] = []
        self._build_category_maps()

        # Apply default settings
        self._apply_defaults()

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def site_link(self) -> str:
        return self.definition.site_link

    @property
    def indexer_type(self) -> str:
        return self.definition.type

    def _apply_defaults(self):
        """Apply default values from settings definitions."""
        for setting in self.definition.settings:
            name = setting.get("name", "")
            if name and name not in self.config:
                if "default" in setting:
                    self.config[name] = setting["default"]
                elif "defaults" in setting:
                    self.config[name] = setting["defaults"]

    def _build_category_maps(self):
        """Build bidirectional category mappings from the definition."""
        caps = self.definition.caps
        if not caps:
            return

        for cm in caps.get("categorymappings", []):
            site_id = str(cm["id"])
            cat_name = cm["cat"]
            cat_id = resolve_category(cat_name)
            if cat_id is None:
                continue

            self._site_to_torznab.setdefault(site_id, []).append(cat_id)
            self._torznab_to_site.setdefault(cat_id, []).append(site_id)

            if cm.get("default", False):
                self._default_categories.append(site_id)

    def _map_categories(self, torznab_cats: list[int]) -> list[str]:
        """Map Torznab category IDs to site-specific category IDs."""
        if not torznab_cats:
            return []
        site_cats = set()
        for cat in torznab_cats:
            if cat in self._torznab_to_site:
                site_cats.update(self._torznab_to_site[cat])
            # Also check parent category
            parent = (cat // 1000) * 1000
            if parent in self._torznab_to_site:
                site_cats.update(self._torznab_to_site[parent])
        return sorted(site_cats)

    def _map_site_category(self, site_id: str) -> list[int]:
        """Map a site category ID to Torznab category IDs."""
        return self._site_to_torznab.get(str(site_id), [])

    def _build_variables(self, query: TorznabQuery) -> dict[str, Any]:
        """Build the template variable dict for a search query."""
        site_cats = self._map_categories(query.categories)

        variables: dict[str, Any] = {
            ".Config.sitelink": self.site_link,
            ".True": ".True",
            ".False": "",
            ".Keywords": query.get_query_string(),
            ".Query.Q": query.sanitized_search_term,
            ".Query.Keywords": query.get_query_string(),
            ".Query.SearchTerm": query.sanitized_search_term,
            ".Query.IMDBID": query.imdb_id or "",
            ".Query.IMDBIDShort": query.imdb_id_short or "",
            ".Query.TMDBID": str(query.tmdb_id) if query.tmdb_id else "",
            ".Query.TVDBID": str(query.tvdb_id) if query.tvdb_id else "",
            ".Query.TVMazeID": str(query.tvmaze_id) if query.tvmaze_id else "",
            ".Query.TraktID": str(query.trakt_id) if query.trakt_id else "",
            ".Query.DoubanID": str(query.douban_id) if query.douban_id else "",
            ".Query.Season": str(query.season) if query.season else "",
            ".Query.Episode": query.episode or "",
            ".Query.Album": query.album or "",
            ".Query.Artist": query.artist or "",
            ".Query.Label": query.label or "",
            ".Query.Track": query.track or "",
            ".Query.Year": str(query.year) if query.year else "",
            ".Query.Genre": query.genre or "",
            ".Query.Author": query.author or "",
            ".Query.Title": query.title or "",
            ".Query.Publisher": query.publisher or "",
            ".Query.Limit": str(query.limit),
            ".Query.Offset": str(query.offset),
            ".Query.Extended": str(query.extended),
            ".Categories": site_cats,
        }

        # Add config values
        for key, value in self.config.items():
            variables[f".Config.{key}"] = value

        return variables

    def _apply_keywords_filters(self, keywords: str, variables: dict[str, Any]) -> str:
        """Apply keyword filters from the search definition."""
        kw_filters = self.definition.search.get("keywordsfilters")
        if kw_filters:
            keywords = apply_filters(keywords, kw_filters, variables)
        return keywords

    async def _ensure_client(self):
        """Get or create the HTTP client."""
        if self.client is None:
            from pyackett.core.http import create_http_client
            self.client = create_http_client()
        return self.client

    async def _request(
        self,
        url: str,
        method: str = "GET",
        data: dict | None = None,
        headers: dict | None = None,
        params: dict | None = None,
    ):
        """Make an HTTP request with cookies and optional delay."""
        # Honor requestDelay from definition
        if self.definition.request_delay:
            import asyncio
            await asyncio.sleep(self.definition.request_delay)

        client = await self._ensure_client()

        req_headers = dict(headers or {})
        if self.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            req_headers.setdefault("Cookie", cookie_str)

        # Use configured user-agent if set
        ua = self.config.get("useragent") or self.config.get("user-agent")
        if ua:
            req_headers.setdefault("User-Agent", ua)

        # Add search headers from definition
        search_headers = self.definition.search.get("headers", {})
        for key, values in search_headers.items():
            if isinstance(values, list):
                val = values[0] if values else ""
            else:
                val = str(values)
            # Template-expand header values
            req_headers[key] = apply_template(val, self._build_variables(TorznabQuery()))

        if method.upper() == "POST":
            return await client.post(
                url, data=data, headers=req_headers, params=params,
                follow_redirects=self.definition.follow_redirect,
            )
        return await client.get(
            url, headers=req_headers, params=params,
            follow_redirects=self.definition.follow_redirect,
        )

    async def login(self, config: dict[str, Any] | None = None) -> bool:
        """Perform login using the definition's login block."""
        if config:
            self.config.update(config)
            self._apply_defaults()

        login_block = self.definition.login
        if not login_block:
            self.is_configured = True
            return True

        method = login_block.get("method", "form")
        variables = self._build_variables(TorznabQuery())

        # Update variables with fresh config
        for key, value in self.config.items():
            variables[f".Config.{key}"] = value

        path = apply_template(login_block.get("path", ""), variables)
        if not path.startswith("http"):
            path = self.site_link.rstrip("/") + "/" + path.lstrip("/")

        # Build headers
        req_headers: dict[str, str] = {}
        login_headers = login_block.get("headers", {})
        for key, values in login_headers.items():
            if isinstance(values, list):
                val = values[0] if values else ""
            else:
                val = str(values)
            req_headers[key] = apply_template(val, variables)

        search_headers = self.definition.search.get("headers", {})
        for key, values in search_headers.items():
            if isinstance(values, list):
                val = values[0] if values else ""
            else:
                val = str(values)
            req_headers[key] = apply_template(val, variables)

        try:
            if method == "get":
                resp = await self._request(path, method="GET", headers=req_headers)
            elif method == "header":
                resp = await self._request(path, method="GET", headers=req_headers)
            elif method == "cookie":
                # Cookie auth — either via named cookies or a full cookie string
                cookie_names = login_block.get("cookies", [])
                if cookie_names:
                    for name in cookie_names:
                        if name in self.config:
                            self.cookies[name] = self.config[name]
                # Also check inputs for a full cookie string
                login_inputs = login_block.get("inputs", {})
                for key, value in login_inputs.items():
                    expanded = apply_template(str(value), variables)
                    if expanded:
                        # Parse "uid=123; pass=abc" into individual cookies
                        for part in expanded.split(";"):
                            part = part.strip()
                            if "=" in part:
                                k, v = part.split("=", 1)
                                self.cookies[k.strip()] = v.strip()
                # Test the login
                test = login_block.get("test", {})
                test_path = test.get("path", "")
                if test_path:
                    if not test_path.startswith("http"):
                        test_path = self.site_link.rstrip("/") + "/" + test_path.lstrip("/")
                    resp = await self._request(test_path)
                else:
                    self.is_configured = True
                    return True
            elif method in ("form", "post"):
                inputs = login_block.get("inputs", {})
                form_data = {}
                for key, value in inputs.items():
                    form_data[key] = apply_template(str(value), variables)
                resp = await self._request(path, method="POST", data=form_data, headers=req_headers)
            else:
                logger.warning(f"Unknown login method: {method}")
                self.is_configured = True
                return True

            # Store cookies from response
            if hasattr(resp, 'cookies'):
                for name, value in resp.cookies.items():
                    self.cookies[name] = value

            # Check for errors
            error_blocks = login_block.get("error", [])
            if error_blocks and resp.text:
                for err in error_blocks:
                    err_sel = err.get("selector", "")
                    if err_sel and ":root:contains(" in err_sel:
                        check_text = err_sel.split(":contains(")[1].rstrip(")")
                        check_text = check_text.strip("'\"")
                        if check_text.lower() in resp.text.lower():
                            msg = err.get("message", {})
                            if isinstance(msg, dict):
                                msg_text = apply_template(msg.get("text", "Login failed"), variables)
                            else:
                                msg_text = "Login failed"
                            logger.error(f"Login error for {self.id}: {msg_text}")
                            return False

            if resp.status_code < 400:
                self.is_configured = True
                return True
            else:
                logger.error(f"Login failed for {self.id}: HTTP {resp.status_code}")
                return False

        except Exception as e:
            logger.error(f"Login error for {self.id}: {e}")
            return False

    async def search(self, query: TorznabQuery) -> list[ReleaseInfo]:
        """Execute a search query and return results."""
        variables = self._build_variables(query)
        search_block = self.definition.search
        if not search_block:
            return []

        # Apply keyword filters
        keywords = query.get_query_string()
        keywords = self._apply_keywords_filters(keywords, variables)
        variables[".Keywords"] = keywords

        # Update headers with per-search variables
        req_headers: dict[str, str] = {}
        for key, values in search_block.get("headers", {}).items():
            if isinstance(values, list):
                val = values[0] if values else ""
            else:
                val = str(values)
            req_headers[key] = apply_template(val, variables)

        all_results: list[ReleaseInfo] = []

        # Get search paths
        paths = search_block.get("paths", [])
        if not paths and "path" in search_block:
            paths = [{"path": search_block["path"]}]

        for path_block in paths:
            if isinstance(path_block, str):
                path_block = {"path": path_block}

            # Check category restrictions
            path_cats = path_block.get("categories", [])
            if path_cats and query.categories:
                site_cats = self._map_categories(query.categories)
                if site_cats and not any(c in path_cats for c in site_cats):
                    continue

            # Build URL
            path = apply_template(path_block.get("path", ""), variables)
            if not path.startswith("http"):
                path = self.site_link.rstrip("/") + "/" + path.lstrip("/")

            # Build query inputs
            inputs = {}
            if path_block.get("inheritinputs", True):
                for key, value in search_block.get("inputs", {}).items():
                    expanded = apply_template(str(value), variables)
                    inputs[key] = expanded

            # Path-specific inputs override
            for key, value in path_block.get("inputs", {}).items():
                expanded = apply_template(str(value), variables)
                inputs[key] = expanded

            # Handle $raw input (appended to URL directly)
            raw_input = inputs.pop("$raw", None)

            method = path_block.get("method", search_block.get("method", "GET")).upper()

            try:
                url = path
                if raw_input:
                    separator = "&" if "?" in url else "?"
                    url = url + separator + raw_input.rstrip("&")

                if method == "POST":
                    resp = await self._request(url, method="POST", data=inputs, headers=req_headers)
                else:
                    resp = await self._request(url, method="GET", params=inputs if inputs else None, headers=req_headers)

                if resp.status_code >= 400:
                    logger.warning(f"Search request failed for {self.id}: HTTP {resp.status_code}")
                    continue

                # Determine response type
                response_type = "html"
                response_block = path_block.get("response", {})
                if response_block and response_block.get("type") == "json":
                    response_type = "json"
                elif resp.headers.get("content-type", "").startswith("application/json"):
                    response_type = "json"

                # Apply preprocessing filters
                content = resp.text
                preproc = search_block.get("preprocessingfilters")
                if preproc:
                    content = apply_filters(content, preproc, variables)

                # Parse results
                results = self._parse_results(content, response_type, variables)
                all_results.extend(results)

            except Exception as e:
                logger.error(f"Search error for {self.id} path {path}: {e}")
                continue

        return all_results

    def _parse_results(
        self, content: str, response_type: str, variables: dict[str, Any]
    ) -> list[ReleaseInfo]:
        """Parse response content into ReleaseInfo objects."""
        search_block = self.definition.search
        rows_block = search_block.get("rows", {})
        fields = search_block.get("fields", {})

        if not rows_block or not fields:
            return []

        results: list[ReleaseInfo] = []
        row_selector = rows_block.get("selector", "")
        after = rows_block.get("after", 0)

        if response_type == "json":
            return self._parse_json_results(content, row_selector, fields, variables, after)
        else:
            return self._parse_html_results(content, row_selector, fields, variables, after)

    def _parse_html_results(
        self,
        content: str,
        row_selector: str,
        fields: dict[str, Any],
        variables: dict[str, Any],
        after: int = 0,
    ) -> list[ReleaseInfo]:
        """Parse HTML response into results."""
        row_selector = apply_template(row_selector, variables)
        doc = parse_html(content)
        rows = query_selector_all(doc, row_selector)

        if after > 0:
            rows = rows[after:]

        results = []
        for row in rows:
            release = self._extract_release_from_html(row, fields, variables)
            if release and release.title:
                results.append(release)

        return results

    def _parse_json_results(
        self,
        content: str,
        row_selector: str,
        fields: dict[str, Any],
        variables: dict[str, Any],
        after: int = 0,
    ) -> list[ReleaseInfo]:
        """Parse JSON response into results."""
        try:
            data = parse_json(content)
        except Exception as e:
            logger.error(f"JSON parse error: {e}")
            return []

        row_selector = apply_template(row_selector, variables)
        rows = extract_rows_from_json(data, row_selector)

        if after > 0:
            rows = rows[after:]

        results = []
        for row in rows:
            release = self._extract_release_from_json(row, fields, variables)
            if release and release.title:
                results.append(release)

        return results

    def _extract_release_from_html(
        self, row: Tag, fields: dict[str, Any], variables: dict[str, Any]
    ) -> ReleaseInfo | None:
        """Extract a ReleaseInfo from an HTML row element."""
        field_values: dict[str, str] = {}
        row_variables = dict(variables)

        # Process fields in order (some depend on earlier fields via .Result.xxx)
        for field_name, field_block in fields.items():
            if isinstance(field_block, str):
                field_block = {"text": field_block}
            if field_block is None:
                field_block = {}

            # Check for template text first
            text_template = field_block.get("text")
            if text_template is not None:
                value = apply_template(str(text_template), row_variables)
            else:
                value = extract_text(row, field_block)

            # Apply filters
            filt = field_block.get("filters")
            if filt:
                value = apply_filters(value, filt, row_variables)

            # Apply case mapping
            case_map = field_block.get("case")
            if case_map and isinstance(case_map, dict):
                value = case_map.get(value, case_map.get(value.strip(), value))

            field_values[field_name] = value
            row_variables[f".Result.{field_name}"] = value

        return self._build_release(field_values)

    def _extract_release_from_json(
        self, row: dict, fields: dict[str, Any], variables: dict[str, Any]
    ) -> ReleaseInfo | None:
        """Extract a ReleaseInfo from a JSON row object."""
        field_values: dict[str, str] = {}
        row_variables = dict(variables)

        for field_name, field_block in fields.items():
            if isinstance(field_block, str):
                field_block = {"selector": field_block}
            if field_block is None:
                field_block = {}

            text_template = field_block.get("text")
            if text_template is not None:
                value = apply_template(str(text_template), row_variables)
            else:
                selector = field_block.get("selector", "")
                raw_value = extract_from_json(row, selector)
                value = str(raw_value) if raw_value is not None else field_block.get("default", "")

            filt = field_block.get("filters")
            if filt:
                value = apply_filters(value, filt, row_variables)

            case_map = field_block.get("case")
            if case_map and isinstance(case_map, dict):
                value = case_map.get(value, case_map.get(value.strip(), value))

            field_values[field_name] = value
            row_variables[f".Result.{field_name}"] = value

        return self._build_release(field_values)

    def _build_release(self, fields: dict[str, str]) -> ReleaseInfo | None:
        """Build a ReleaseInfo from extracted field values."""
        title = fields.get("title", "").strip()
        if not title:
            return None

        release = ReleaseInfo(
            title=title,
            origin_id=self.id,
            origin_name=self.name,
            origin_type=self.indexer_type,
        )

        # Details / comments URL
        details = fields.get("details", "")
        if details:
            if not details.startswith("http"):
                details = self.site_link.rstrip("/") + "/" + details.lstrip("/")
            release.details = details
            release.guid = details

        # Download link
        download = fields.get("download", "")
        if download:
            if not download.startswith("http") and not download.startswith("magnet:"):
                download = self.site_link.rstrip("/") + "/" + download.lstrip("/")
            if download.startswith("magnet:"):
                release.magnet_uri = download
            else:
                release.link = download

        if not release.guid:
            release.guid = release.link or release.magnet_uri or title

        # Magnet URI
        magnet = fields.get("magnet") or fields.get("magneturi", "")
        if magnet:
            release.magnet_uri = magnet

        # Info hash
        release.info_hash = fields.get("infohash") or None

        # Generate magnet URI from info_hash if no download link exists
        if not release.link and not release.magnet_uri and release.info_hash:
            from urllib.parse import quote
            release.magnet_uri = (
                f"magnet:?xt=urn:btih:{release.info_hash}"
                f"&dn={quote(release.title)}"
            )

        if not release.guid:
            release.guid = release.link or release.magnet_uri or title

        # Description
        release.description = fields.get("description") or None

        # Size
        size_str = fields.get("size", "")
        if size_str:
            release.size = self._parse_size(size_str)

        # Seeders / Leechers
        release.seeders = _safe_int(fields.get("seeders"))
        leechers = _safe_int(fields.get("leechers"))
        if release.seeders is not None and leechers is not None:
            release.peers = release.seeders + leechers
        elif leechers is not None:
            release.peers = leechers

        # Date
        date_str = fields.get("date", "")
        if date_str and date_str.lower() != "now":
            from pyackett.engine.filters import _parse_fuzzy_time
            try:
                release.publish_date = _parse_fuzzy_time(date_str)
            except Exception:
                release.publish_date = datetime.now(timezone.utc)
        else:
            release.publish_date = datetime.now(timezone.utc)

        # Category
        cat_str = fields.get("category", "")
        if cat_str:
            torznab_cats = self._map_site_category(cat_str)
            release.category = torznab_cats

        # Grabs / Files
        release.grabs = _safe_int(fields.get("grabs"))
        release.files = _safe_int(fields.get("files"))

        # Volume factors
        release.download_volume_factor = _safe_float(fields.get("downloadvolumefactor"))
        release.upload_volume_factor = _safe_float(fields.get("uploadvolumefactor"))

        # Tracker requirements
        release.minimum_ratio = _safe_float(fields.get("minimumratio"))
        release.minimum_seed_time = _safe_int(fields.get("minimumseedtime"))

        # Media IDs
        imdb_str = fields.get("imdb") or fields.get("imdbid", "")
        if imdb_str:
            imdb_str = imdb_str.lstrip("t")
            release.imdb = _safe_int(imdb_str)

        release.tmdb = _safe_int(fields.get("tmdbid"))
        release.tvdb_id = _safe_int(fields.get("tvdbid"))
        release.rage_id = _safe_int(fields.get("rageid"))
        release.tvmaze_id = _safe_int(fields.get("tvmazeid"))
        release.trakt_id = _safe_int(fields.get("traktid"))
        release.douban_id = _safe_int(fields.get("doubanid"))
        release.year = _safe_int(fields.get("year"))

        # Genres
        genre_str = fields.get("genre", "")
        if genre_str:
            release.genres = [g.strip() for g in genre_str.split(",") if g.strip()]

        # Poster
        poster = fields.get("poster", "")
        if poster:
            if not poster.startswith("http"):
                poster = self.site_link.rstrip("/") + "/" + poster.lstrip("/")
            release.poster = poster

        # Media-specific fields
        release.author = fields.get("author") or None
        release.book_title = fields.get("booktitle") or None
        release.publisher = fields.get("publisher") or None
        release.artist = fields.get("artist") or None
        release.album = fields.get("album") or None
        release.label = fields.get("label") or None
        release.track = fields.get("track") or None

        return release

    @staticmethod
    def _parse_size(size_str: str) -> int | None:
        """Parse a human-readable size string into bytes."""
        size_str = size_str.strip()
        if not size_str:
            return None

        # Already numeric
        try:
            return int(float(size_str))
        except ValueError:
            pass

        multipliers = {
            "b": 1, "byte": 1, "bytes": 1,
            "kb": 1024, "kib": 1024,
            "mb": 1024**2, "mib": 1024**2,
            "gb": 1024**3, "gib": 1024**3,
            "tb": 1024**4, "tib": 1024**4,
        }

        m = re.match(r"([\d.,]+)\s*([a-zA-Z]+)", size_str)
        if m:
            try:
                num = float(m.group(1).replace(",", ""))
                unit = m.group(2).lower()
                mult = multipliers.get(unit, 1)
                return int(num * mult)
            except ValueError:
                pass
        return None


def _safe_int(value: str | None) -> int | None:
    """Safely convert a string to int."""
    if not value:
        return None
    try:
        # Handle comma-separated numbers
        return int(float(value.replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _safe_float(value: str | None) -> float | None:
    """Safely convert a string to float."""
    if not value:
        return None
    try:
        return float(value.replace(",", "").strip())
    except (ValueError, TypeError):
        return None
