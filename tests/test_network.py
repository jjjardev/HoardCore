"""Tests for the network fetch chain and discovery providers (no real I/O)."""

import asyncio

import pytest

import hoardcore as hc
from tests.conftest import TempConfig


def _fetcher(tmp_path, overrides=None):
    cfg = TempConfig(str(tmp_path), overrides)
    return hc.NetworkFetcher(cfg)


def test_fast_uses_only_aiohttp(tmp_path):
    f = _fetcher(tmp_path)
    f._fetch_aiohttp = lambda u: _stub(("html", None, "text/html"))
    out = asyncio.run(f.fetch("http://x/", "fast"))
    assert out == ("html", None, "text/html")


async def _stub(value):
    return value


def test_balanced_falls_back_to_curl_when_aiohttp_fails(tmp_path):
    f = _fetcher(tmp_path)
    # aiohttp fails
    f._fetch_aiohttp = lambda u: _stub((None, None, ""))
    f._fetch_curl_cffi = lambda u: _stub(("curlbody", None, "text/html"))
    out = asyncio.run(f.fetch("http://x/", "balanced"))
    assert out[0] == "curlbody"


def test_aggressive_uses_flaresolverr_as_last_resort(tmp_path):
    f = _fetcher(tmp_path)
    f._fetch_aiohttp = lambda u: _stub((None, None, ""))
    f._fetch_curl_cffi = lambda u: _stub((None, None, ""))
    f._fetch_flaresolverr = lambda u: _stub(("solved", None, "text/html"))
    out = asyncio.run(f.fetch("http://x/", "aggressive"))
    assert out[0] == "solved"


def test_chain_raises_when_all_fail(tmp_path):
    f = _fetcher(tmp_path)
    f._fetch_aiohttp = lambda u: _stub((None, None, ""))
    f._fetch_curl_cffi = lambda u: _stub((None, None, ""))
    f._fetch_flaresolverr = lambda u: _stub((None, None, ""))
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(f.fetch("http://x/", "aggressive"))
    assert str(ei.value) == "FETCH_FAILED"


# --- parser unit tests ---

def test_parse_duckduckgo_maps_uddg_links():
    html = ('<a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=xx">'
            '<b>Example</b> Title</a>')
    res = hc.WebSearchProvider._parse_duckduckgo(html, 5)
    assert len(res) == 1
    assert res[0].url == "https://example.com/page"
    assert res[0].title == "Example Title"


def test_parse_mojeek_links():
    html = '<a class="ob" href="https://mojeek-result.net">Mojeek Hit</a>'
    res = hc.WebSearchProvider._parse_mojeek(html, 5)
    assert len(res) == 1
    assert res[0].url == "https://mojeek-result.net"
    assert "Mojeek" in res[0].title


def test_search_falls_back_across_providers(tmp_path):
    """Provider 1 returns nothing -> provider 2 (mojeek) is used."""
    cfg = TempConfig(str(tmp_path), {"discovery.max_retries": 0})

    class FakeFetcher:
        def __init__(self):
            self.url = None
        async def fetch(self, url, strategy):
            self.url = url
            if "duckduckgo" in url:
                return (None, None, "")
            return ('<a class="ob" href="https://fallback.test/x">F</a>', None, "text/html")

    p = hc.WebSearchProvider(cfg, FakeFetcher())
    res = asyncio.run(p.search("what is x", max_results=5))
    assert len(res) == 1
    assert res[0].url == "https://fallback.test/x"


def test_discovery_empty_query_returns_nothing(tmp_path):
    cfg = TempConfig(str(tmp_path))

    class FakeFetcher:
        async def fetch(self, url, strategy):
            return (None, None, "")

    p = hc.WebSearchProvider(cfg, FakeFetcher())
    res = asyncio.run(p.search("   ", max_results=5))
    assert res == []