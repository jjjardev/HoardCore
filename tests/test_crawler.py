"""Tests for the crawler: robots/sitemap parsing and URL discovery (no network I/O)."""

import asyncio

import hoardcore as hc
from tests.conftest import TempConfig


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._body


class _FakeSession:
    """aiohttp.ClientSession stand-in whose get() returns a canned response."""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, timeout=None, **kwargs):
        return self._resp


def _crawler(tmp_path):
    return hc.CrawlerPlanner(TempConfig(str(tmp_path)))


def test_extract_locs_namespaced_sitemap():
    xml = """
      <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://example.com/</loc></url>
        <url><loc>https://example.com/about</loc></url>
      </urlset>
    """
    assert hc.CrawlerPlanner._extract_locs(xml) == [
        "https://example.com/",
        "https://example.com/about",
    ]


def test_extract_locs_non_namespaced():
    xml = "<urlset><url><loc>https://old-style.test/a</loc></url></urlset>"
    assert hc.CrawlerPlanner._extract_locs(xml) == ["https://old-style.test/a"]


def test_extract_locs_falls_back_to_regex_on_malformed_xml():
    # Not valid XML (two roots), so lxml fails -> regex fallback still finds <loc>.
    xml = "<loc>https://a.test/1</loc> garbage <loc>https://a.test/2</loc>"
    assert hc.CrawlerPlanner._extract_locs(xml) == [
        "https://a.test/1",
        "https://a.test/2",
    ]


def test_parse_sitemap_empty_for_non_200(monkeypatch):
    import hoardcore

    def fake_session(*args, **kwargs):
        return _FakeSession(_FakeResp(404, ""))

    monkeypatch.setattr(hoardcore.aiohttp, "ClientSession", fake_session)
    planner = hc.CrawlerPlanner(TempConfig("/tmp/hc-crawler-404"))
    out = asyncio.run(planner.parse_sitemap("https://example.test/nope.xml"))
    assert out == []


def test_parse_sitemap_extracts_and_dedupes(monkeypatch):
    import hoardcore

    xml = """
      <urlset>
        <url><loc>https://a.test/p1</loc></url>
        <url><loc>https://a.test/p1</loc></url>
        <url><loc>https://a.test/p2</loc></url>
      </urlset>
    """

    def fake_session(*args, **kwargs):
        return _FakeSession(_FakeResp(200, xml))

    monkeypatch.setattr(hoardcore.aiohttp, "ClientSession", fake_session)
    planner = hc.CrawlerPlanner(TempConfig("/tmp/parse-dedupe"))
    out = asyncio.run(planner.parse_sitemap("https://a.test/sitemap.xml"))
    assert out == ["https://a.test/p1", "https://a.test/p2"]


def test_parse_sitemap_never_returns_none_on_network_error(monkeypatch):
    """Regression: a failed/HTTP-error sitemap must never yield None (which
    previously crashed `discover_urls` with TypeError on a 200 response)."""
    import hoardcore

    class _BoomSession(_FakeSession):
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise RuntimeError("connect refused")

        async def __aexit__(self, *exc):
            return False

        def get(self, url, timeout=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(hoardcore.aiohttp, "ClientSession", _BoomSession)
    planner = hc.CrawlerPlanner(TempConfig("/tmp/parse-boom"))
    out = asyncio.run(planner.parse_sitemap("https://x.test/sitemap.xml"))
    assert out == []


def test_discover_urls_combines_and_dedupes(monkeypatch):
    """Multiple sitemaps are combined and de-duplicated."""
    planner = _crawler("/tmp/crawl-dedup")
    captured = {}

    async def fake_robots(domain):
        captured["domain"] = domain
        return ["https://site.test/sitemap1.xml", "https://site.test/sitemap2.xml"]

    async def fake_parse(sitemap_url):
        if "sitemap1" in sitemap_url:
            return ["https://site.test/a", "https://site.test/b"]
        return ["https://site.test/b", "https://site.test/c"]

    planner.get_robots_urls = fake_robots
    planner.parse_sitemap = fake_parse
    urls = asyncio.run(planner.discover_urls("https://site.test/"))
    assert urls == ["https://site.test/a", "https://site.test/b", "https://site.test/c"]


def test_get_robots_urls_parses_sitemap_directives(monkeypatch):
    robots = "User-agent: *\nDisallow:\nSitemap: https://a.test/bigmap.xml\n"

    def fake_session(*args, **kwargs):
        return _FakeSession(_FakeResp(200, robots))

    import hoardcore

    monkeypatch.setattr(hoardcore.aiohttp, "ClientSession", fake_session)
    planner = hc.CrawlerPlanner(TempConfig("/tmp/robots", {"crawler.respect_robots": True}))
    out = asyncio.run(planner.get_robots_urls("https://a.test"))
    assert out == ["https://a.test/bigmap.xml"]


def test_get_robots_urls_falls_back_to_default_only_on_failure(monkeypatch):
    import hoardcore

    def fake_session(*args, **kwargs):
        return _FakeSession(_FakeResp(404, ""))

    monkeypatch.setattr(hoardcore.aiohttp, "ClientSession", fake_session)
    planner = hc.CrawlerPlanner(TempConfig("/tmp/robots404", {"crawler.respect_robots": True}))
    out = asyncio.run(planner.get_robots_urls("https://a.test"))
    assert out == ["https://a.test/sitemap.xml"]


def _rules_planner(rules_body: str):
    planner = hc.CrawlerPlanner(TempConfig("/tmp/robots-rules", {"crawler.respect_robots": True}))
    # Parse rules directly (bypasses the network fetch).
    import re as _re
    rules = []
    for raw in rules_body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key not in ("disallow", "allow") or not value:
            continue
        anchored = value.endswith("$")
        pat = value[:-1] if anchored else value
        rx_text = ".*".join(_re.escape(p) for p in pat.split("*"))
        rules.append((key == "allow", value,
                      _re.compile("^" + rx_text + ("$" if anchored else ""))))
    planner._robots_rules["site.test"] = rules
    return planner


def test_allowed_blocks_disallowed_prefix():
    planner = _rules_planner("User-agent: *\nDisallow: /private/\n")
    assert not planner.allowed("https://site.test/private/x")
    assert planner.allowed("https://site.test/public/x")


def test_allowed_longest_match_and_allow_wins_ties():
    planner = _rules_planner("Disallow: /section\nAllow: /section/ok\n")
    assert not planner.allowed("https://site.test/section/other")
    assert planner.allowed("https://site.test/section/ok")


def test_allowed_wildcard_and_dollar_anchor():
    planner = _rules_planner("Disallow: *.pdf$\nAllow: /open/*.pdf$\n")
    assert not planner.allowed("https://site.test/files/secret.pdf")
    assert planner.allowed("https://site.test/open/free.pdf")
    assert planner.allowed("https://site.test/files/secret.pdfx")


def test_allowed_no_rules_or_disabled_means_allow():
    planner = _rules_planner("")
    assert planner.allowed("https://site.test/anything")
    off = hc.CrawlerPlanner(TempConfig("/tmp/robots-off", {"crawler.respect_robots": False}))
    assert off.allowed("https://elsewhere.test/private/")
