"""Tests for core models."""

from pyackett.core.models import ReleaseInfo, TorznabQuery


class TestTorznabQuery:
    def test_defaults(self):
        q = TorznabQuery()
        assert q.query_type == "search"
        assert q.categories == []
        assert q.is_search
        assert not q.is_tv_search

    def test_sanitized_search_term(self):
        q = TorznabQuery(search_term="hello <world> |test|")
        assert "<" not in q.sanitized_search_term
        assert ">" not in q.sanitized_search_term
        assert "|" not in q.sanitized_search_term

    def test_episode_search_string(self):
        q = TorznabQuery(season=1, episode="5")
        assert q.get_episode_search_string() == "S01E05"

    def test_episode_search_season_only(self):
        q = TorznabQuery(season=2)
        assert q.get_episode_search_string() == "S02"

    def test_is_rss_search(self):
        q = TorznabQuery()
        assert q.is_rss_search

    def test_is_id_search(self):
        q = TorznabQuery(imdb_id="tt1234567")
        assert q.is_id_search
        assert not q.is_rss_search

    def test_get_query_string(self):
        q = TorznabQuery(search_term="test", season=1, episode="3")
        assert q.get_query_string() == "test S01E03"


class TestReleaseInfo:
    def test_gain(self):
        r = ReleaseInfo(seeders=10, size=1024**3)  # 1 GB
        assert r.gain == 10.0

    def test_gain_none(self):
        r = ReleaseInfo()
        assert r.gain is None
