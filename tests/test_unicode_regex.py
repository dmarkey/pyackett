"""Tests for Unicode property regex conversion."""

import re
from pyackett.engine.filters import _fix_unicode_properties, apply_filters


class TestFixUnicodeProperties:
    def test_no_properties(self):
        assert _fix_unicode_properties(r"hello \d+") == r"hello \d+"

    def test_cyrillic(self):
        result = _fix_unicode_properties(r"[\p{IsCyrillic}]+")
        assert r"\p{" not in result
        # Should match Cyrillic characters
        compiled = re.compile(result)
        assert compiled.search("Привет")

    def test_punctuation(self):
        result = _fix_unicode_properties(r"\p{P}")
        assert r"\p{" not in result

    def test_multiple_properties(self):
        result = _fix_unicode_properties(r"[\p{IsCyrillic}\p{P}]+")
        assert r"\p{" not in result
        compiled = re.compile(result)
        assert compiled.search("Привет!")

    def test_re_replace_with_unicode_property(self):
        """Test that re_replace filter handles \\p{} patterns."""
        result = apply_filters(
            "Hello Мир World",
            [{"name": "re_replace", "args": [r"[\p{IsCyrillic}]+", "RU"]}],
        )
        assert "RU" in result
        assert "Мир" not in result
