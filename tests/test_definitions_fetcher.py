"""Tests for the GitHub definitions fetcher."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pyackett.core.definitions_fetcher import (
    fetch_definitions_if_needed,
    update_definitions,
)


def _write_marker(defs_dir: Path, sha: str = "abc123", hours_ago: float = 0):
    """Helper to write a .fetched marker."""
    fetched_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    (defs_dir / ".fetched").write_text(json.dumps({
        "sha": sha,
        "count": 1,
        "fetched_at": fetched_at.isoformat(),
    }))


class TestFetchDefinitionsIfNeeded:
    def test_downloads_when_no_definitions(self, tmp_path):
        """No definitions on disk -> download."""
        with patch("pyackett.core.definitions_fetcher.fetch_definitions", return_value=5) as mock_fetch, \
             patch("pyackett.core.definitions_fetcher._get_remote_sha", return_value="sha123"):
            result = fetch_definitions_if_needed(tmp_path, source="jackett")
            mock_fetch.assert_called_once()
            assert "jackett" in str(result)

    def test_skips_when_recently_checked(self, tmp_path):
        """Have definitions, checked < 12h ago -> skip."""
        defs_dir = tmp_path / "definitions" / "jackett"
        defs_dir.mkdir(parents=True)
        (defs_dir / "test.yml").write_text("id: test")
        _write_marker(defs_dir, sha="abc123", hours_ago=1)

        with patch("pyackett.core.definitions_fetcher.fetch_definitions") as mock_fetch, \
             patch("pyackett.core.definitions_fetcher._get_remote_sha") as mock_sha:
            result = fetch_definitions_if_needed(tmp_path, source="jackett")
            mock_fetch.assert_not_called()
            mock_sha.assert_not_called()  # shouldn't even check
            assert result == defs_dir

    def test_skips_when_sha_matches(self, tmp_path):
        """Have definitions, checked > 12h ago, same SHA -> skip."""
        defs_dir = tmp_path / "definitions" / "jackett"
        defs_dir.mkdir(parents=True)
        (defs_dir / "test.yml").write_text("id: test")
        _write_marker(defs_dir, sha="abc123", hours_ago=24)

        with patch("pyackett.core.definitions_fetcher.fetch_definitions") as mock_fetch, \
             patch("pyackett.core.definitions_fetcher._get_remote_sha", return_value="abc123"):
            result = fetch_definitions_if_needed(tmp_path, source="jackett")
            mock_fetch.assert_not_called()
            assert result == defs_dir

    def test_updates_when_sha_differs(self, tmp_path):
        """Have definitions, checked > 12h ago, different SHA -> re-download."""
        defs_dir = tmp_path / "definitions" / "jackett"
        defs_dir.mkdir(parents=True)
        (defs_dir / "test.yml").write_text("id: test")
        _write_marker(defs_dir, sha="old_sha", hours_ago=24)

        with patch("pyackett.core.definitions_fetcher.fetch_definitions", return_value=550) as mock_fetch, \
             patch("pyackett.core.definitions_fetcher._get_remote_sha", return_value="new_sha"):
            result = fetch_definitions_if_needed(tmp_path, source="jackett")
            mock_fetch.assert_called_once()

    def test_skips_when_github_unreachable(self, tmp_path):
        """Have definitions, can't reach GitHub -> use cached."""
        defs_dir = tmp_path / "definitions" / "jackett"
        defs_dir.mkdir(parents=True)
        (defs_dir / "test.yml").write_text("id: test")
        _write_marker(defs_dir, sha="abc123", hours_ago=24)

        with patch("pyackett.core.definitions_fetcher.fetch_definitions") as mock_fetch, \
             patch("pyackett.core.definitions_fetcher._get_remote_sha", return_value=None):
            result = fetch_definitions_if_needed(tmp_path, source="jackett")
            mock_fetch.assert_not_called()
            assert result == defs_dir


class TestUpdateDefinitions:
    def test_clears_and_redownloads(self, tmp_path):
        """Force update should clear existing and re-download."""
        defs_dir = tmp_path / "definitions" / "jackett"
        defs_dir.mkdir(parents=True)
        (defs_dir / "old.yml").write_text("id: old")
        _write_marker(defs_dir, sha="old")

        with patch("pyackett.core.definitions_fetcher.fetch_definitions", return_value=10) as mock_fetch, \
             patch("pyackett.core.definitions_fetcher._get_remote_sha", return_value="new"):
            result = update_definitions(tmp_path, source="jackett")
            mock_fetch.assert_called_once()
            assert not (defs_dir / "old.yml").exists()
