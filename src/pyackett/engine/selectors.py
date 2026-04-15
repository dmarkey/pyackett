"""Selector engine for extracting data from HTML and JSON responses."""

from __future__ import annotations

import json
import re
from typing import Any

import warnings

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from jsonpath_ng import parse as jsonpath_parse

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
warnings.filterwarnings("ignore", message=".*pseudo class ':contains' is deprecated.*")


def query_selector(element: Tag, selector: str) -> Tag | None:
    """CSS selector query with :root pseudo-selector support."""
    if not selector:
        return element

    # Handle :root pseudo-selector
    if selector.startswith(":root"):
        rest = selector[5:].strip()
        if not rest:
            return element
        # :root:contains("text") or :root > child
        if rest.startswith(":contains("):
            text = rest[10:].rstrip(")")
            text = text.strip("'\"")
            if text.lower() in (element.get_text() or "").lower():
                return element
            return None
        return element.select_one(rest)

    try:
        result = element.select_one(selector)
        if result:
            return result
    except Exception:
        pass

    # Fallback: make tbody optional (browsers auto-insert it, lxml doesn't)
    if "> tbody >" in selector or ">tbody>" in selector:
        try:
            relaxed = selector.replace("> tbody >", ">").replace(">tbody>", ">")
            result = element.select_one(relaxed)
            if result:
                return result
        except Exception:
            pass

    return None


def query_selector_all(element: Tag, selector: str) -> list[Tag]:
    """CSS selector query returning all matches."""
    if not selector:
        return [element]

    # Handle :root
    if selector.startswith(":root"):
        return [element]

    try:
        results = element.select(selector)
        if results:
            return results
    except Exception:
        pass

    # Fallback: browsers auto-insert <tbody> in tables, but lxml/bs4 don't.
    # Try with tbody made optional in the selector.
    if "> tbody >" in selector or ">tbody>" in selector:
        try:
            relaxed = selector.replace("> tbody >", ">").replace(">tbody>", ">")
            results = element.select(relaxed)
            if results:
                return results
        except Exception:
            pass
        try:
            relaxed = selector.replace("> tbody >", " ").replace(">tbody>", " ")
            results = element.select(relaxed)
            if results:
                return results
        except Exception:
            pass

    return []


def extract_text(element: Tag | None, selector_block: dict[str, Any] | None = None) -> str:
    """Extract text content from an element according to a selector block.

    A selector_block may contain:
      - selector: CSS selector
      - attribute: HTML attribute to extract instead of text
      - remove: CSS selector for elements to remove before extraction
      - text: template text (returned as-is, no selector needed)
    """
    if selector_block is None:
        if element is None:
            return ""
        return element.get_text(strip=True)

    # If "text" key is set, it's a template - return as-is for template processing
    if "text" in selector_block and selector_block["text"] is not None:
        return str(selector_block["text"])

    if element is None:
        return selector_block.get("default", "")

    # Apply CSS selector
    sel = selector_block.get("selector", "")
    target = element
    if sel:
        target = query_selector(element, sel)

    if target is None:
        if selector_block.get("optional", False):
            return selector_block.get("default", "")
        return selector_block.get("default", "")

    # Remove elements before extraction
    remove_sel = selector_block.get("remove")
    if remove_sel:
        for el in target.select(remove_sel):
            el.decompose()

    # Extract attribute or text
    attr = selector_block.get("attribute")
    if attr:
        return target.get(attr, "") or ""
    return target.get_text(strip=True)


def extract_from_json(data: Any, selector: str) -> Any:
    """Extract a value from JSON data using a selector.

    Supports:
      - Simple key names: "fieldName"
      - JSONPath: "$.store.book[0].title"
      - Pseudo-CSS selectors: ":has(key)", ":contains(value)", ":not(key)"
    """
    if not selector:
        return data

    # Dollar sign = it's a JSONPath expression for a list of objects
    if selector == "$":
        return data if isinstance(data, list) else [data]

    # Simple key lookup on dict (check top-level and one level nested)
    if isinstance(data, dict):
        if selector in data:
            return data[selector]
        # Search one level deep (common for Elasticsearch _source pattern)
        for v in data.values():
            if isinstance(v, dict) and selector in v:
                return v[selector]

    # JSONPath
    if "$" in selector or "[" in selector:
        try:
            expr = jsonpath_parse(selector)
            matches = [m.value for m in expr.find(data)]
            if len(matches) == 1:
                return matches[0]
            return matches
        except Exception:
            pass

    # Dotted path
    if "." in selector and not selector.startswith("$"):
        parts = selector.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    return None


def extract_rows_from_json(data: Any, selector: str) -> list[dict]:
    """Extract rows from JSON data for iteration."""
    result = extract_from_json(data, selector)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return [result]
    return []


def parse_html(content: str) -> BeautifulSoup:
    """Parse HTML content into a BeautifulSoup tree."""
    return BeautifulSoup(content, "lxml")


def parse_xml(content: str) -> BeautifulSoup:
    """Parse XML content into a BeautifulSoup tree."""
    return BeautifulSoup(content, "lxml-xml")


def parse_json(content: str) -> Any:
    """Parse JSON content."""
    return json.loads(content)
