"""Tests for XML-based parsing in the selector engine and cardigann engine."""

import pytest
from pyackett.engine.selectors import (
    extract_text,
    parse_xml,
    query_selector,
    query_selector_all,
)
from pyackett.engine.cardigann import CardigannIndexer
from pyackett.core.models import IndexerDefinition


# ---------------------------------------------------------------------------
# Sample XML content
# ---------------------------------------------------------------------------

RSS_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Tracker</title>
    <item>
      <title>Ubuntu 24.04 LTS Desktop amd64</title>
      <link>https://tracker.example.com/details/1001</link>
      <enclosure url="https://tracker.example.com/download/1001.torrent" length="4812345678" type="application/x-bittorrent"/>
      <pubDate>Mon, 01 Apr 2024 12:00:00 +0000</pubDate>
      <description>Ubuntu Desktop ISO</description>
      <category>Linux ISOs</category>
      <seeders>150</seeders>
      <leechers>25</leechers>
      <size>4812345678</size>
    </item>
    <item>
      <title>Fedora 40 Workstation x86_64</title>
      <link>https://tracker.example.com/details/1002</link>
      <enclosure url="https://tracker.example.com/download/1002.torrent" length="2048000000" type="application/x-bittorrent"/>
      <pubDate>Tue, 02 Apr 2024 14:30:00 +0000</pubDate>
      <description>Fedora Workstation ISO</description>
      <category>Linux ISOs</category>
      <seeders>80</seeders>
      <leechers>10</leechers>
      <size>2048000000</size>
    </item>
    <item>
      <title>Debian 12.5 netinst amd64</title>
      <link>https://tracker.example.com/details/1003</link>
      <enclosure url="https://tracker.example.com/download/1003.torrent" length="650000000" type="application/x-bittorrent"/>
      <pubDate>Wed, 03 Apr 2024 08:15:00 +0000</pubDate>
      <description>Debian netinstall ISO</description>
      <category>Linux ISOs</category>
      <seeders>200</seeders>
      <leechers>5</leechers>
      <size>650000000</size>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <entry>
    <title>Arch Linux 2024.04.01</title>
    <link href="https://tracker.example.com/details/2001"/>
    <id>urn:tracker:2001</id>
    <updated>2024-04-01T10:00:00Z</updated>
    <summary>Arch Linux ISO</summary>
    <content type="html">&lt;p&gt;Monthly Arch ISO&lt;/p&gt;</content>
  </entry>
  <entry>
    <title>openSUSE Tumbleweed</title>
    <link href="https://tracker.example.com/details/2002"/>
    <id>urn:tracker:2002</id>
    <updated>2024-04-02T11:00:00Z</updated>
    <summary>openSUSE rolling release</summary>
  </entry>
</feed>
"""

SIMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<results>
  <torrent id="3001" freeleech="true">
    <name>Big Buck Bunny 4K</name>
    <size>1073741824</size>
    <seeders>500</seeders>
    <leechers>42</leechers>
    <download_url>https://tracker.example.com/dl/3001</download_url>
    <details_url>https://tracker.example.com/t/3001</details_url>
    <category>Movies</category>
    <added>2024-04-01 09:00:00</added>
    <imdb>tt1254207</imdb>
  </torrent>
  <torrent id="3002" freeleech="false">
    <name>Sintel 1080p</name>
    <size>734003200</size>
    <seeders>250</seeders>
    <leechers>15</leechers>
    <download_url>https://tracker.example.com/dl/3002</download_url>
    <details_url>https://tracker.example.com/t/3002</details_url>
    <category>Movies</category>
    <added>2024-04-02 15:30:00</added>
    <imdb>tt1727587</imdb>
  </torrent>
</results>
"""

NAMESPACED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Torznab Feed</title>
    <item>
      <title>Test.Release.S01E01.720p</title>
      <guid>https://tracker.example.com/t/5001</guid>
      <link>https://tracker.example.com/dl/5001.torrent</link>
      <pubDate>Thu, 04 Apr 2024 20:00:00 +0000</pubDate>
      <torznab:attr name="seeders" value="100"/>
      <torznab:attr name="peers" value="120"/>
      <torznab:attr name="size" value="1500000000"/>
      <torznab:attr name="tvdbid" value="12345"/>
      <enclosure url="https://tracker.example.com/dl/5001.torrent" length="1500000000" type="application/x-bittorrent"/>
    </item>
    <item>
      <title>Test.Release.S01E02.720p</title>
      <guid>https://tracker.example.com/t/5002</guid>
      <link>https://tracker.example.com/dl/5002.torrent</link>
      <pubDate>Fri, 05 Apr 2024 20:00:00 +0000</pubDate>
      <torznab:attr name="seeders" value="80"/>
      <torznab:attr name="peers" value="95"/>
      <torznab:attr name="size" value="1450000000"/>
      <torznab:attr name="tvdbid" value="12345"/>
      <enclosure url="https://tracker.example.com/dl/5002.torrent" length="1450000000" type="application/x-bittorrent"/>
    </item>
  </channel>
</rss>
"""


# ---------------------------------------------------------------------------
# Helper: build a minimal CardigannIndexer for testing _parse_xml_results
# ---------------------------------------------------------------------------

def _make_indexer(search_block: dict) -> CardigannIndexer:
    """Create a CardigannIndexer with the given search block for unit tests."""
    defn = IndexerDefinition(
        id="test-xml",
        name="Test XML Indexer",
        links=["https://tracker.example.com/"],
        type="public",
        search=search_block,
    )
    return CardigannIndexer(definition=defn)


# ===========================================================================
# Selector-level tests (parse_xml + query functions)
# ===========================================================================


class TestParseXml:
    """Test that parse_xml correctly parses XML content."""

    def test_parse_rss(self):
        doc = parse_xml(RSS_FEED)
        assert doc is not None
        # The document should have a channel element
        channel = doc.find("channel")
        assert channel is not None

    def test_parse_atom(self):
        doc = parse_xml(ATOM_FEED)
        assert doc is not None
        title = doc.find("title")
        assert title is not None
        assert title.get_text() == "Test Atom Feed"

    def test_parse_simple_xml(self):
        doc = parse_xml(SIMPLE_XML)
        assert doc is not None
        torrents = doc.find_all("torrent")
        assert len(torrents) == 2

    def test_parse_preserves_attributes(self):
        doc = parse_xml(SIMPLE_XML)
        torrent = doc.find("torrent")
        assert torrent["id"] == "3001"
        assert torrent["freeleech"] == "true"

    def test_parse_empty_xml(self):
        doc = parse_xml('<?xml version="1.0"?><root/>')
        assert doc is not None
        assert doc.find("root") is not None


class TestXmlQuerySelector:
    """Test CSS selector queries on XML documents."""

    def test_select_rss_items(self):
        doc = parse_xml(RSS_FEED)
        items = query_selector_all(doc, "item")
        assert len(items) == 3

    def test_select_nested_element(self):
        doc = parse_xml(RSS_FEED)
        title = query_selector(doc, "item title")
        assert title is not None
        assert title.get_text() == "Ubuntu 24.04 LTS Desktop amd64"

    def test_select_all_titles(self):
        doc = parse_xml(RSS_FEED)
        titles = query_selector_all(doc, "item title")
        assert len(titles) == 3
        assert titles[1].get_text() == "Fedora 40 Workstation x86_64"

    def test_select_by_attribute(self):
        doc = parse_xml(SIMPLE_XML)
        torrent = query_selector(doc, 'torrent[id="3002"]')
        assert torrent is not None
        name = query_selector(torrent, "name")
        assert name.get_text() == "Sintel 1080p"

    def test_select_enclosure_attribute(self):
        doc = parse_xml(RSS_FEED)
        enc = query_selector(doc, "item enclosure")
        assert enc is not None
        assert enc.get("url") == "https://tracker.example.com/download/1001.torrent"
        assert enc.get("length") == "4812345678"

    def test_select_returns_none_for_missing(self):
        doc = parse_xml(RSS_FEED)
        result = query_selector(doc, "nonexistent")
        assert result is None

    def test_select_all_returns_empty_for_missing(self):
        doc = parse_xml(RSS_FEED)
        results = query_selector_all(doc, "nonexistent")
        assert results == []

    def test_select_with_child_combinator(self):
        doc = parse_xml(RSS_FEED)
        items = query_selector_all(doc, "channel > item")
        assert len(items) == 3

    def test_select_custom_elements(self):
        doc = parse_xml(SIMPLE_XML)
        torrents = query_selector_all(doc, "results > torrent")
        assert len(torrents) == 2

    def test_select_freeleech_attribute(self):
        doc = parse_xml(SIMPLE_XML)
        freeleech = query_selector_all(doc, 'torrent[freeleech="true"]')
        assert len(freeleech) == 1
        assert query_selector(freeleech[0], "name").get_text() == "Big Buck Bunny 4K"


class TestXmlExtractText:
    """Test text extraction from XML elements."""

    def test_extract_text_from_element(self):
        doc = parse_xml(RSS_FEED)
        item = query_selector(doc, "item")
        title = query_selector(item, "title")
        assert extract_text(title) == "Ubuntu 24.04 LTS Desktop amd64"

    def test_extract_text_with_selector_block(self):
        doc = parse_xml(RSS_FEED)
        item = query_selector(doc, "item")
        text = extract_text(item, {"selector": "title"})
        assert text == "Ubuntu 24.04 LTS Desktop amd64"

    def test_extract_attribute(self):
        doc = parse_xml(RSS_FEED)
        item = query_selector(doc, "item")
        url = extract_text(item, {"selector": "enclosure", "attribute": "url"})
        assert url == "https://tracker.example.com/download/1001.torrent"

    def test_extract_attribute_from_xml_element(self):
        doc = parse_xml(SIMPLE_XML)
        torrent = query_selector(doc, "torrent")
        tid = extract_text(torrent, {"selector": ":root", "attribute": "id"})
        assert tid == "3001"

    def test_extract_text_optional_missing(self):
        doc = parse_xml(RSS_FEED)
        item = query_selector(doc, "item")
        text = extract_text(item, {"selector": "nonexistent", "optional": True, "default": "N/A"})
        assert text == "N/A"

    def test_extract_text_with_default(self):
        doc = parse_xml(RSS_FEED)
        item = query_selector(doc, "item")
        text = extract_text(item, {"selector": "missing_element", "default": "0"})
        assert text == "0"


class TestXmlNamespaces:
    """Test XML namespace handling."""

    def test_namespaced_xml_parses(self):
        doc = parse_xml(NAMESPACED_XML)
        assert doc is not None

    def test_select_items_in_namespaced_rss(self):
        doc = parse_xml(NAMESPACED_XML)
        items = query_selector_all(doc, "item")
        assert len(items) == 2

    def test_select_titles_in_namespaced_rss(self):
        doc = parse_xml(NAMESPACED_XML)
        titles = query_selector_all(doc, "item title")
        assert len(titles) == 2
        assert titles[0].get_text() == "Test.Release.S01E01.720p"

    def test_extract_enclosure_from_namespaced(self):
        doc = parse_xml(NAMESPACED_XML)
        item = query_selector(doc, "item")
        enc = query_selector(item, "enclosure")
        assert enc is not None
        assert "5001.torrent" in enc.get("url", "")


class TestAtomFeed:
    """Test Atom feed parsing."""

    def test_parse_atom_entries(self):
        doc = parse_xml(ATOM_FEED)
        entries = query_selector_all(doc, "entry")
        assert len(entries) == 2

    def test_atom_entry_title(self):
        doc = parse_xml(ATOM_FEED)
        entry = query_selector(doc, "entry")
        title = query_selector(entry, "title")
        assert title.get_text() == "Arch Linux 2024.04.01"

    def test_atom_link_attribute(self):
        doc = parse_xml(ATOM_FEED)
        entry = query_selector(doc, "entry")
        link = query_selector(entry, "link")
        assert link is not None
        assert link.get("href") == "https://tracker.example.com/details/2001"

    def test_atom_updated_text(self):
        doc = parse_xml(ATOM_FEED)
        entry = query_selector(doc, "entry")
        updated = query_selector(entry, "updated")
        assert updated.get_text() == "2024-04-01T10:00:00Z"


# ===========================================================================
# Cardigann engine-level tests (_parse_xml_results)
# ===========================================================================


class TestParseXmlResults:
    """Test the cardigann engine's XML result parsing pipeline."""

    def test_rss_feed_parsing(self):
        """Parse an RSS feed like a real indexer would."""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "details": {"selector": "link"},
                "download": {"selector": "enclosure", "attribute": "url"},
                "size": {"selector": "size"},
                "seeders": {"selector": "seeders"},
                "leechers": {"selector": "leechers"},
                "date": {"selector": "pubDate"},
                "description": {"selector": "description"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(RSS_FEED, "item", search["fields"], variables)

        assert len(results) == 3

        # First result
        r = results[0]
        assert r.title == "Ubuntu 24.04 LTS Desktop amd64"
        assert r.link == "https://tracker.example.com/download/1001.torrent"
        assert r.details == "https://tracker.example.com/details/1001"
        assert r.seeders == 150
        assert r.peers == 175  # seeders + leechers
        assert r.size == 4812345678
        assert r.description == "Ubuntu Desktop ISO"

        # Second result
        r = results[1]
        assert r.title == "Fedora 40 Workstation x86_64"
        assert r.seeders == 80

        # Third result
        r = results[2]
        assert r.title == "Debian 12.5 netinst amd64"
        assert r.seeders == 200
        assert r.peers == 205

    def test_custom_xml_api(self):
        """Parse a custom XML API response with attribute extraction."""
        search = {
            "rows": {"selector": "torrent"},
            "fields": {
                "title": {"selector": "name"},
                "download": {"selector": "download_url"},
                "details": {"selector": "details_url"},
                "size": {"selector": "size"},
                "seeders": {"selector": "seeders"},
                "leechers": {"selector": "leechers"},
                "date": {"selector": "added"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(SIMPLE_XML, "torrent", search["fields"], variables)

        assert len(results) == 2
        assert results[0].title == "Big Buck Bunny 4K"
        assert results[0].size == 1073741824
        assert results[0].link == "https://tracker.example.com/dl/3001"
        assert results[0].seeders == 500
        assert results[0].peers == 542

        assert results[1].title == "Sintel 1080p"
        assert results[1].size == 734003200

    def test_attribute_based_extraction(self):
        """Extract data from XML attributes (like enclosure url/length)."""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "download": {"selector": "enclosure", "attribute": "url"},
                "size": {"selector": "enclosure", "attribute": "length"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(RSS_FEED, "item", search["fields"], variables)

        assert len(results) == 3
        assert results[0].link == "https://tracker.example.com/download/1001.torrent"
        assert results[0].size == 4812345678
        assert results[1].link == "https://tracker.example.com/download/1002.torrent"
        assert results[1].size == 2048000000

    def test_after_skips_rows(self):
        """The 'after' parameter should skip the first N rows."""
        search = {
            "rows": {"selector": "item", "after": 1},
            "fields": {
                "title": {"selector": "title"},
                "download": {"selector": "link"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(RSS_FEED, "item", search["fields"], variables, after=1)

        assert len(results) == 2
        assert results[0].title == "Fedora 40 Workstation x86_64"
        assert results[1].title == "Debian 12.5 netinst amd64"

    def test_empty_xml(self):
        """Empty XML with no matching rows returns no results."""
        empty = '<?xml version="1.0"?><rss><channel></channel></rss>'
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(empty, "item", search["fields"], variables)
        assert results == []

    def test_missing_optional_fields(self):
        """Optional fields with defaults should not break parsing."""
        xml = """\
<?xml version="1.0"?>
<results>
  <item>
    <title>Minimal Entry</title>
    <link>https://example.com/dl/1</link>
  </item>
</results>
"""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "download": {"selector": "link"},
                "seeders": {"selector": "seeders", "optional": True, "default": "0"},
                "leechers": {"selector": "leechers", "optional": True, "default": "0"},
                "size": {"selector": "size", "optional": True, "default": "0"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(xml, "item", search["fields"], variables)

        assert len(results) == 1
        assert results[0].title == "Minimal Entry"
        assert results[0].link == "https://example.com/dl/1"

    def test_text_field_template(self):
        """Fields with 'text' key use template expansion, not selectors."""
        search = {
            "rows": {"selector": "torrent"},
            "fields": {
                "title": {"selector": "name"},
                "download": {
                    "text": "https://tracker.example.com/api/download/{{ .Result.title }}"
                },
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        # Use the simple XML which has <name> elements
        xml = """\
<?xml version="1.0"?>
<results>
  <torrent>
    <name>TestFile</name>
  </torrent>
</results>
"""
        results = indexer._parse_xml_results(xml, "torrent", search["fields"], variables)
        assert len(results) == 1
        assert results[0].title == "TestFile"

    def test_rows_skipped_when_title_empty(self):
        """Rows with empty title should be skipped."""
        xml = """\
<?xml version="1.0"?>
<results>
  <item><title></title><link>https://example.com/1</link></item>
  <item><title>Good Entry</title><link>https://example.com/2</link></item>
  <item><title>   </title><link>https://example.com/3</link></item>
</results>
"""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "download": {"selector": "link"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(xml, "item", search["fields"], variables)
        assert len(results) == 1
        assert results[0].title == "Good Entry"

    def test_magnet_link_detection(self):
        """Magnet URIs should be stored in magnet_uri, not link."""
        xml = """\
<?xml version="1.0"?>
<results>
  <item>
    <title>Magnet Release</title>
    <magnet>magnet:?xt=urn:btih:abc123&amp;dn=Magnet+Release</magnet>
  </item>
</results>
"""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "download": {"selector": "magnet"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(xml, "item", search["fields"], variables)
        assert len(results) == 1
        assert results[0].magnet_uri is not None
        assert results[0].magnet_uri.startswith("magnet:")
        assert results[0].link is None


class TestParseXmlResultsWithFilters:
    """Test XML parsing with filter chains applied to fields."""

    def test_filter_on_field(self):
        """Filters should be applied to extracted values."""
        xml = """\
<?xml version="1.0"?>
<results>
  <item>
    <title>  Needs Trimming  </title>
    <link>https://example.com/dl/1</link>
    <size>1.5 GB</size>
  </item>
</results>
"""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {
                    "selector": "title",
                    "filters": [{"name": "trim"}],
                },
                "download": {"selector": "link"},
                "size": {"selector": "size"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(xml, "item", search["fields"], variables)
        assert len(results) == 1
        assert results[0].title == "Needs Trimming"
        assert results[0].size == 1073741824 * 1.5  # ~1.5 GB

    def test_prepend_filter(self):
        """Prepend filter should add prefix to extracted values."""
        xml = """\
<?xml version="1.0"?>
<results>
  <item>
    <title>Test Release</title>
    <path>/download/123</path>
  </item>
</results>
"""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "download": {
                    "selector": "path",
                    "filters": [{"name": "prepend", "args": "https://tracker.example.com"}],
                },
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_xml_results(xml, "item", search["fields"], variables)
        assert len(results) == 1
        assert results[0].link == "https://tracker.example.com/download/123"


class TestResponseTypeDetection:
    """Test that _parse_results routes correctly based on response type."""

    def test_xml_type_routes_to_xml_parser(self):
        """response_type='xml' should use parse_xml (lxml-xml parser)."""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "download": {"selector": "link"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_results(RSS_FEED, "xml", variables)
        assert len(results) == 3
        assert results[0].title == "Ubuntu 24.04 LTS Desktop amd64"

    def test_html_type_also_works_for_xml_content(self):
        """HTML parser should also handle XML-ish content (less strict)."""
        search = {
            "rows": {"selector": "item"},
            "fields": {
                "title": {"selector": "title"},
                "download": {"selector": "link"},
            },
        }
        indexer = _make_indexer(search)
        variables = indexer._build_variables(
            __import__("pyackett.core.models", fromlist=["TorznabQuery"]).TorznabQuery()
        )
        results = indexer._parse_results(RSS_FEED, "html", variables)
        assert len(results) == 3


class TestXmlVsHtmlDifferences:
    """Test differences between XML and HTML parsing modes."""

    def test_xml_is_case_sensitive(self):
        """XML parser is case-sensitive for element names."""
        xml = '<?xml version="1.0"?><Root><Item><Title>Test</Title></Item></Root>'
        doc = parse_xml(xml)
        # Case-sensitive: "Item" should work
        assert query_selector(doc, "Item") is not None
        # "item" (lowercase) should not match in XML mode
        assert query_selector(doc, "item") is None

    def test_xml_preserves_self_closing_tags(self):
        """XML parser handles self-closing tags properly."""
        xml = '<?xml version="1.0"?><root><empty/><data>text</data></root>'
        doc = parse_xml(xml)
        empty = query_selector(doc, "empty")
        assert empty is not None
        assert empty.get_text() == ""
        data = query_selector(doc, "data")
        assert data.get_text() == "text"

    def test_xml_cdata_handling(self):
        """XML parser should handle CDATA sections."""
        xml = """\
<?xml version="1.0"?>
<items>
  <item>
    <title><![CDATA[Release with <special> chars & more]]></title>
    <link>https://example.com/1</link>
  </item>
</items>
"""
        doc = parse_xml(xml)
        title = query_selector(doc, "item title")
        assert title is not None
        text = title.get_text()
        assert "Release with <special> chars & more" in text
