"""Filter chain implementation for Cardigann YAML definitions.

Each filter transforms a string value. Filters are applied in sequence.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, parse_qs, urlparse

from dateutil import parser as dateutil_parser

from pyackett.engine.template import apply_template

# RFC 1123 with timezone
RFC1123Z = "%a, %d %b %Y %H:%M:%S %z"

# Unicode property escape replacements for Python's re module
# Python doesn't support \p{Category} — convert to character ranges
_UNICODE_PROPERTY_MAP = {
    r"\p{L}": r"a-zA-Z\u00C0-\u024F\u0400-\u04FF\u0500-\u052F",
    r"\p{IsCyrillic}": r"\u0400-\u04FF",
    r"\p{Cyrillic}": r"\u0400-\u04FF",
    r"\p{P}": r"!-/:-@\[-`{-~\u00A0-\u00BF\u2000-\u206F",
    r"\p{S}": r"$+<->^`|~\u00A2-\u00A9\u00AE-\u00B1\u2190-\u21FF\u2600-\u26FF",
    r"\p{N}": r"0-9",
    r"\p{Pd}": r"\u002D\u058A\u05BE\u1400\u1806\u2010-\u2015\u2E17\u2E1A\u301C\u3030\u30A0\uFE31\uFE32\uFE58\uFE63\uFF0D",
    r"\p{Nd}": r"0-9",
    r"\p{Lu}": r"A-Z\u00C0-\u00D6\u00D8-\u00DE\u0400-\u042F",
    r"\p{Ll}": r"a-z\u00E0-\u00F6\u00F8-\u00FF\u0430-\u044F",
}


def _fix_unicode_properties(pattern: str) -> str:
    """Convert \\p{Property} escapes to character ranges for Python's re module."""
    if r"\p{" not in pattern:
        return pattern
    for prop, replacement in _UNICODE_PROPERTY_MAP.items():
        # Handle both inside and outside character classes
        # Inside [...]: \p{X} -> replacement chars
        # Outside [...]: \p{X} -> [replacement]
        pattern = pattern.replace(prop, replacement)
    return pattern


def _parse_fuzzy_time(data: str) -> datetime:
    """Parse various date/time formats including relative times like '2 days ago'."""
    data = data.strip()
    if not data:
        return datetime.now(timezone.utc)

    # Try relative time patterns
    relative_patterns = [
        (r"(\d+)\s*second", 1),
        (r"(\d+)\s*min", 60),
        (r"(\d+)\s*hour", 3600),
        (r"(\d+)\s*day", 86400),
        (r"(\d+)\s*week", 604800),
        (r"(\d+)\s*month", 2592000),
        (r"(\d+)\s*year", 31536000),
    ]

    lower = data.lower()
    if "ago" in lower or "just now" in lower or "today" in lower:
        if "just now" in lower or "now" in lower:
            return datetime.now(timezone.utc)
        for pattern, multiplier in relative_patterns:
            m = re.search(pattern, lower)
            if m:
                from datetime import timedelta
                seconds = int(m.group(1)) * multiplier
                return datetime.now(timezone.utc) - timedelta(seconds=seconds)

    # Yesterday
    if "yesterday" in lower:
        from datetime import timedelta
        return datetime.now(timezone.utc) - timedelta(days=1)

    # Try dateutil parser as fallback
    try:
        return dateutil_parser.parse(data, fuzzy=True)
    except (ValueError, OverflowError):
        return datetime.now(timezone.utc)


def _parse_date_go_layout(data: str, layout: str) -> datetime:
    """Parse date using Go-style layout format.

    Go uses reference time: Mon Jan 2 15:04:05 MST 2006
    We map common Go format tokens to Python strftime.
    """
    # Go -> Python strftime mapping
    mapping = [
        ("yyyy", "%Y"), ("yy", "%y"),
        ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"),
        ("dd", "%d"), ("d", "%-d"),
        ("HH", "%H"), ("hh", "%I"), ("mm", "%M"), ("ss", "%S"),
        ("tt", "%p"), ("htt", "%I%p"),
        ("zzz", "%z"),
        ("EEEE", "%A"), ("EEE", "%a"),
    ]

    py_format = layout
    for go_tok, py_tok in mapping:
        py_format = py_format.replace(go_tok, py_tok)

    # Clean up the data
    data = data.strip()

    try:
        return datetime.strptime(data, py_format)
    except ValueError:
        # Fallback to fuzzy parsing
        return _parse_fuzzy_time(data)


def apply_filters(
    data: str,
    filters: list[dict[str, Any]] | None,
    variables: dict[str, Any] | None = None,
) -> str:
    """Apply a chain of filters to transform a string value.

    Args:
        data: The input string to transform.
        filters: List of filter dicts with 'name' and optional 'args'.
        variables: Template variables for filters that support template expansion.

    Returns:
        The transformed string.
    """
    if not filters:
        return data

    variables = variables or {}

    for filt in filters:
        name = filt.get("name", "")
        args = filt.get("args")

        match name:
            case "querystring":
                param = str(args)
                parsed = parse_qs(urlparse(data).query)
                data = parsed.get(param, [""])[0]

            case "timeparse" | "dateparse":
                layout = str(args)
                try:
                    dt = _parse_date_go_layout(data, layout)
                    data = dt.strftime(RFC1123Z)
                except Exception:
                    pass

            case "regexp":
                pattern = _fix_unicode_properties(str(args))
                m = re.search(pattern, data)
                data = m.group(1) if m and m.lastindex else ""

            case "re_replace":
                pattern = _fix_unicode_properties(str(args[0]))
                replacement = str(args[1])
                replacement = apply_template(replacement, variables)
                # Convert C#/Go-style $1 backreferences to Python \1
                replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)
                data = re.sub(pattern, replacement, data)

            case "split":
                sep = str(args[0])
                pos = int(args[1])
                parts = data.split(sep)
                if pos < 0:
                    pos += len(parts)
                data = parts[pos] if 0 <= pos < len(parts) else ""

            case "replace":
                old = str(args[0])
                new = str(args[1])
                new = apply_template(new, variables)
                data = data.replace(old, new)

            case "trim":
                if args:
                    data = data.strip(str(args))
                else:
                    data = data.strip()

            case "prepend":
                data = apply_template(str(args), variables) + data

            case "append":
                data = data + apply_template(str(args), variables)

            case "tolower":
                data = data.lower()

            case "toupper":
                data = data.upper()

            case "urldecode":
                data = unquote(data)

            case "urlencode":
                data = quote(data, safe="")

            case "htmldecode":
                data = html.unescape(data)

            case "htmlencode":
                data = html.escape(data)

            case "timeago" | "reltime":
                dt = _parse_fuzzy_time(data)
                data = dt.strftime(RFC1123Z)

            case "fuzzytime":
                dt = _parse_fuzzy_time(data)
                data = dt.strftime(RFC1123Z)

            case "validfilename":
                data = re.sub(r'[<>:"/\\|?*]', "_", data)

            case "diacritics":
                if str(args) == "replace":
                    nfkd = unicodedata.normalize("NFD", data)
                    data = "".join(
                        c for c in nfkd
                        if unicodedata.category(c) != "Mn"
                    )
                    data = unicodedata.normalize("NFC", data)

            case "jsonjoinarray":
                json_path = str(args[0])
                separator = str(args[1])
                obj = json.loads(data)
                # Simple JSON path: just key lookup
                parts = json_path.strip("$.").split(".")
                current = obj
                for part in parts:
                    if isinstance(current, dict):
                        current = current.get(part, [])
                items = current if isinstance(current, list) else [current]
                data = separator.join(str(i) for i in items)

            case "hexdump" | "strdump":
                # Debug filters - no-op in production
                pass

            case "validate":
                delimiters = set(",/ )(.;[]\"|:")
                valid_args = str(args).lower()
                valid_tokens = set(
                    t for t in re.split(r"[,/ \)(\.\;\[\]\"\|\:]+", valid_args) if t
                )
                data_tokens = set(
                    t for t in re.split(r"[,/ \)(\.\;\[\]\"\|\:]+", data.lower()) if t
                )
                data = ",".join(valid_tokens & data_tokens)

    return data
