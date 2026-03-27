"""Tests for the GitHub definitions fetcher."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from pyackett.core.definitions_fetcher import (
    fetch_definitions_if_missing,
    update_definitions,
)


class TestFetchDefinitionsIfMissing:
    def test_uses_cache_when_present(self, tmp_path):
        """If definitions already exist, don't re-download."""
        defs_dir = tmp_path / "definitions" / "jackett"
        defs_dir.mkdir(parents=True)
        (defs_dir / "test.yml").write_text("id: test")
        (defs_dir / ".fetched").write_text("fetched 1 definitions")

        with patch("pyackett.core.definitions_fetcher.fetch_definitions") as mock_fetch:
            result = fetch_definitions_if_missing(tmp_path, source="jackett")
            mock_fetch.assert_not_called()
            assert result == defs_dir

    def test_downloads_when_missing(self, tmp_path):
        """If no definitions exist, download them."""
        with patch("pyackett.core.definitions_fetcher.fetch_definitions", return_value=5) as mock_fetch:
            result = fetch_definitions_if_missing(tmp_path, source="jackett")
            mock_fetch.assert_called_once()
            assert "jackett" in str(result)


class TestUpdateDefinitions:
    def test_clears_and_redownloads(self, tmp_path):
        """Force update should clear existing and re-download."""
        defs_dir = tmp_path / "definitions" / "jackett"
        defs_dir.mkdir(parents=True)
        (defs_dir / "old.yml").write_text("id: old")
        (defs_dir / ".fetched").write_text("old")

        with patch("pyackett.core.definitions_fetcher.fetch_definitions", return_value=10) as mock_fetch:
            result = update_definitions(tmp_path, source="jackett")
            mock_fetch.assert_called_once()
            # Old file should be gone
            assert not (defs_dir / "old.yml").exists()
