"""Tests for the network fetch chain and discovery providers (no real I/O)."""

import asyncio
import os

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


def test_default_strategy_is_aggressive():
    """network.default_strategy must default to aggressive so every fetch
    escalates through the full aiohttp -> curl_cffi -> FlareSolverr chain."""
    cfg = hc.ConfigManager()
    assert cfg.get("network.default_strategy", "") == "aggressive"


def test_hoardcore_defaults_fetch_to_aggressive(tmp_path, monkeypatch):
    """HoardCore.fetch with no strategy uses network.default_strategy."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    captured = {}

    async def fake_scrape(url, strategy, force_refresh):
        captured["strategy"] = strategy
        return []

    scraper._scrape_single = fake_scrape
    asyncio.run(scraper.fetch("https://example.test/x", action="scrape"))
    assert captured["strategy"] == cfg.get("network.default_strategy")


def test_hoardcore_scopes_vault_to_subdir(tmp_path, monkeypatch):
    """HoardCore(vault_name='sleep') must point the vault at root_dir/sleep."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore(vault_name="sleep")
    assert scraper.vault.root_dir == os.path.join(str(tmp_path), "sleep")
    assert os.path.isdir(scraper.vault.root_dir)
    assert scraper.vault.db_path == os.path.join(str(tmp_path), "sleep", "vault.db")


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


def test_research_forwards_strategy_to_discovery(tmp_path, monkeypatch):
    """`research` must pass the explicit --strategy to the discovery/ingest step
    instead of silently falling back to the config default. Regression test for
    the bug where `research --strategy aggressive` still fetched with `balanced`."""
    cfg = TempConfig(str(tmp_path))  # default network.default_strategy = "fast"
    # Point HoardCore at an isolated temp config, not the real hoardcore.toml.
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    captured = {}

    async def fake_discover(query, max_results, strategy, force_refresh):
        captured["strategy"] = strategy
        return []

    scraper._discover_and_ingest = fake_discover
    # Avoid touching the real vault; empty recall short-circuits research.
    monkeypatch.setattr(scraper.vault, "search_vault",
                        lambda query, limit=None, hybrid=None: [])

    # Explicit strategy is honored...
    asyncio.run(scraper.research("q", discover=2, recall=4, strategy="aggressive"))
    assert captured["strategy"] == "aggressive"

    # ...and when omitted, the config default is used.
    asyncio.run(scraper.research("q", discover=2, recall=4))
    assert captured["strategy"] == "fast"


def test_scrape_returns_cached_chunks_on_cache_hit(tmp_path, monkeypatch):
    """`scrape` of an already-vaulted URL must serve the stored chunks instead
    of returning an empty result (no network touched, no re-index)."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    url = "https://example.test/cached-doc"
    scraper.vault.index_document(
        url,
        [hc.Chunk(text="cached body sentence here", metadata={"source": url})],
        {},
    )

    chunks = asyncio.run(scraper._scrape_single(url, "fast", force_refresh=False))
    assert chunks
    assert "cached body" in chunks[0].text
    assert chunks[0].metadata["source_url"] == url


def test_research_emits_citations_block(tmp_path, monkeypatch):
    """Regression: the grounding-context file must close with the Source
    Links / Citations block. It was previously written after the file was
    closed (`f.write` outside the `with open` block), so `research` raised
    ValueError and the citations were lost from every grounding file."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()

    async def fake_discover(query, max_results, strategy, force_refresh):
        return []

    scraper._discover_and_ingest = fake_discover
    url = "https://example.test/source-a"
    scraper.vault.index_document(
        url,
        [hc.Chunk(text="relevant content about solar negros energy", metadata={"source": url})],
        {},
    )

    out = os.path.join(str(tmp_path), "grounding.md")
    path = asyncio.run(scraper.research("solar negros", out_path=out, discover=2, recall=4))
    assert path is not None
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "## Source Links / Citations" in content
    assert f"[#1] {url} — {url}" in content


def test_research_answer_first_skips_discovery(tmp_path, monkeypatch):
    """Adaptive-RAG routing: an existing high-confidence memory hit must
    bypass live DISCOVER entirely; the grounding file flags it."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    hit = hc.Chunk(text="cached high-confidence answer body",
                   metadata={"confidence": "high",
                             "source_url": "https://memory.test/x"})
    scraper.vault.search_vault = lambda *a, **k: [hit]

    async def boom(*args, **kwargs):
        raise AssertionError("live DISCOVER must be skipped on a high-confidence hit")

    scraper._discover_and_ingest = boom
    out = os.path.join(str(tmp_path), "grounding.md")
    path = asyncio.run(scraper.research("proxy auth may be needed",
                                        out_path=out, discover=2, recall=3))
    assert path is not None
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "Answer-first recall" in content
    assert "cached high-confidence answer body" in content


def test_research_answer_first_disabled_runs_discovery(tmp_path, monkeypatch):
    """--no-answer-first forces live DISCOVER even when memory has a hit."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    hit = hc.Chunk(text="cached high-confidence answer body",
                   metadata={"confidence": "high",
                             "source_url": "https://memory.test/x"})
    scraper.vault.search_vault = lambda *a, **k: [hit]
    captured = {}

    async def fake_discover(query, max_results, strategy, force_refresh):
        captured["ran"] = True
        return []

    scraper._discover_and_ingest = fake_discover
    out = os.path.join(str(tmp_path), "grounding.md")
    path = asyncio.run(scraper.research("proxy auth may be needed",
                                        out_path=out, discover=2, recall=3,
                                        answer_first=False))
    assert path is not None
    assert captured.get("ran") is True
