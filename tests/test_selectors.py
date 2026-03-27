"""Tests for the selector engine."""

from pyackett.engine.selectors import (
    extract_from_json,
    extract_rows_from_json,
    parse_html,
    query_selector,
    query_selector_all,
)


class TestTbodyFallback:
    def test_tbody_selector_without_tbody_in_html(self):
        html = '<table class="t1"><tr><td>row1</td></tr></table>'
        doc = parse_html(html)
        rows = query_selector_all(doc, ".t1 > tbody > tr")
        assert len(rows) == 1

    def test_tbody_selector_with_tbody_in_html(self):
        html = '<table class="t1"><tbody><tr><td>row1</td></tr></tbody></table>'
        doc = parse_html(html)
        rows = query_selector_all(doc, ".t1 > tbody > tr")
        assert len(rows) == 1

    def test_single_query_selector_tbody_fallback(self):
        html = '<table class="t1"><tr><td>cell</td></tr></table>'
        doc = parse_html(html)
        el = query_selector(doc, ".t1 > tbody > tr > td")
        assert el is not None
        assert el.get_text() == "cell"


class TestNestedJsonLookup:
    def test_direct_key(self):
        assert extract_from_json({"name": "test"}, "name") == "test"

    def test_nested_in_source(self):
        """Elasticsearch-style _source nesting."""
        data = {"_source": {"name": "test", "size": 100}}
        assert extract_from_json(data, "name") == "test"
        assert extract_from_json(data, "size") == 100

    def test_direct_key_takes_precedence(self):
        data = {"name": "top", "_source": {"name": "nested"}}
        assert extract_from_json(data, "name") == "top"

    def test_missing_key(self):
        assert extract_from_json({"a": 1}, "b") is None

    def test_dollar_selector(self):
        data = [{"a": 1}, {"a": 2}]
        rows = extract_rows_from_json(data, "$")
        assert len(rows) == 2

    def test_rows_from_key(self):
        data = {"items": [{"n": "a"}, {"n": "b"}]}
        rows = extract_rows_from_json(data, "items")
        assert len(rows) == 2


class TestRootSelector:
    def test_root_contains_match(self):
        html = '<html><body>Hello world</body></html>'
        doc = parse_html(html)
        el = query_selector(doc, ":root:contains(Hello)")
        assert el is not None

    def test_root_contains_no_match(self):
        html = '<html><body>Hello world</body></html>'
        doc = parse_html(html)
        el = query_selector(doc, ":root:contains(Missing)")
        assert el is None


class TestHasSelector:
    def test_has_basic(self):
        html = '<table><tr><td><a href="/torrent/1">Link</a></td></tr><tr><td>No link</td></tr></table>'
        doc = parse_html(html)
        rows = query_selector_all(doc, 'tr:has(a[href^="/torrent/"])')
        assert len(rows) == 1
