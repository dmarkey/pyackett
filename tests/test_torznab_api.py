"""Tests for Torznab XML generation and query parsing."""

from datetime import datetime, timezone

from pyackett.api.torznab import (
    caps_xml,
    error_xml,
    parse_torznab_query,
    results_to_xml,
)
from pyackett.core.models import ReleaseInfo


class TestQueryParsing:
    def test_basic_search(self):
        q = parse_torznab_query({"t": "search", "q": "test", "apikey": "abc123"})
        assert q.query_type == "search"
        assert q.search_term == "test"
        assert q.api_key == "abc123"

    def test_tv_search(self):
        q = parse_torznab_query({
            "t": "tvsearch", "q": "breaking bad", "season": "5", "ep": "1",
            "cat": "5000,5040", "apikey": "key",
        })
        assert q.is_tv_search
        assert q.season == 5
        assert q.episode == "1"
        assert q.categories == [5000, 5040]

    def test_imdb_search(self):
        q = parse_torznab_query({"t": "movie", "imdbid": "tt0903747"})
        assert q.imdb_id == "tt0903747"

    def test_imdb_without_prefix(self):
        q = parse_torznab_query({"t": "movie", "imdbid": "0903747"})
        assert q.imdb_id == "tt0903747"


class TestXmlGeneration:
    def test_empty_results(self):
        xml = results_to_xml([])
        assert "<?xml" in xml
        assert "<rss" in xml
        assert "torznab" in xml

    def test_result_item(self):
        r = ReleaseInfo(
            title="Test Release",
            guid="http://example.com/1",
            link="http://example.com/dl/1",
            seeders=10,
            peers=15,
            size=1024000,
            publish_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            category=[2000, 2040],
            origin_id="test",
            origin_name="TestTracker",
        )
        xml = results_to_xml([r])
        assert "Test Release" in xml
        assert "http://example.com/dl/1" in xml
        assert 'name="seeders"' in xml
        assert 'value="10"' in xml

    def test_error_xml(self):
        xml = error_xml(100, "Invalid API key")
        assert "<?xml" in xml
        assert 'code="100"' in xml

    def test_caps_xml(self):
        xml = caps_xml("test", "TestTracker", [{"id": 2000, "name": "Movies"}], {"search": ["q"]})
        assert "<?xml" in xml
        assert "TestTracker" in xml
        assert "search" in xml
