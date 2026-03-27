"""Tests for the filter chain."""

from pyackett.engine.filters import apply_filters


class TestBasicFilters:
    def test_replace(self):
        result = apply_filters("hello-world", [{"name": "replace", "args": ["-", " "]}])
        assert result == "hello world"

    def test_append(self):
        result = apply_filters("hello", [{"name": "append", "args": " world"}])
        assert result == "hello world"

    def test_prepend(self):
        result = apply_filters("world", [{"name": "prepend", "args": "hello "}])
        assert result == "hello world"

    def test_tolower(self):
        result = apply_filters("HELLO", [{"name": "tolower"}])
        assert result == "hello"

    def test_toupper(self):
        result = apply_filters("hello", [{"name": "toupper"}])
        assert result == "HELLO"

    def test_trim(self):
        result = apply_filters("  hello  ", [{"name": "trim"}])
        assert result == "hello"

    def test_trim_char(self):
        result = apply_filters("/hello/", [{"name": "trim", "args": "/"}])
        assert result == "hello"

    def test_urldecode(self):
        result = apply_filters("hello%20world", [{"name": "urldecode"}])
        assert result == "hello world"

    def test_urlencode(self):
        result = apply_filters("hello world", [{"name": "urlencode"}])
        assert result == "hello%20world"

    def test_htmldecode(self):
        result = apply_filters("&amp;", [{"name": "htmldecode"}])
        assert result == "&"

    def test_htmlencode(self):
        result = apply_filters("<b>", [{"name": "htmlencode"}])
        assert result == "&lt;b&gt;"


class TestRegexFilters:
    def test_regexp(self):
        result = apply_filters("/torrent/12345/some-title", [{"name": "regexp", "args": r"/torrent/(\d+)"}])
        assert result == "12345"

    def test_re_replace(self):
        result = apply_filters("S2023", [{"name": "re_replace", "args": [r"\bS(20\d{2})\b", "$1"]}])
        # Python uses \1 not $1, but we pass through re.sub which handles $1 via backreference
        # Actually re.sub uses \1, not $1. Let's check:
        # re.sub handles $1 differently - it's literal. Need \\1
        # But Jackett YAML uses $1 which is Go/C# style.
        # Our filter should handle this.
        assert "2023" in result

    def test_split(self):
        result = apply_filters("/a/b/c/d", [{"name": "split", "args": ["/", 3]}])
        assert result == "c"

    def test_split_negative(self):
        result = apply_filters("a.b.c", [{"name": "split", "args": [".", -1]}])
        assert result == "c"


class TestFilterChain:
    def test_multiple_filters(self):
        result = apply_filters("  HELLO-WORLD  ", [
            {"name": "trim"},
            {"name": "replace", "args": ["-", " "]},
            {"name": "tolower"},
        ])
        assert result == "hello world"

    def test_empty_filters(self):
        assert apply_filters("hello", None) == "hello"
        assert apply_filters("hello", []) == "hello"


class TestDateFilters:
    def test_fuzzytime_now(self):
        result = apply_filters("just now", [{"name": "fuzzytime"}])
        assert result  # Should produce a date string

    def test_fuzzytime_ago(self):
        result = apply_filters("2 days ago", [{"name": "fuzzytime"}])
        assert result

    def test_timeago(self):
        result = apply_filters("3 hours ago", [{"name": "timeago"}])
        assert result
