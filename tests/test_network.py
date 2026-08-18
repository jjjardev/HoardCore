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
    f._fetch_aiohttp = lambda u: _stub(("html", None, "text/html", 200))
    out = asyncio.run(f.fetch("http://x/", "fast"))
    assert out == ("html", None, "text/html", 200)


def test_fetch_pads_3_tuple_strategy_results(tmp_path):
    """fetch() must tolerate a strategy returning the legacy 3-tuple (e.g.
    test doubles) by padding status=None, not crashing the chain."""
    f = _fetcher(tmp_path)
    f._fetch_aiohttp = lambda u: _stub(("html", None, "text/html"))
    out = asyncio.run(f.fetch("http://x/", "fast"))
    assert out == ("html", None, "text/html", None)


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


def test_default_strategy_is_aggressive(tmp_path):
    """network.default_strategy must default to aggressive so every fetch
    escalates through the full aiohttp -> curl_cffi -> FlareSolverr chain.
    Loaded from an isolated empty config so the user's (or CI's) local
    hoardcore.toml can never flip this test."""
    (tmp_path / "empty.toml").write_text("", encoding="utf-8")
    cfg = hc.ConfigManager(str(tmp_path / "empty.toml"))
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


def test_fetch_scrape_honors_urls_list(tmp_path, monkeypatch):
    """Regression (the `_` bug): `--urls` on action=scrape must route to the
    batch ingest, never fetch the placeholder positional `_`."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    ingested, single_scraped = [], []

    async def fake_ingest_many(urls, strategy, force_refresh):
        ingested.extend(urls)
        return [{"text": "ok", "metadata": {"source": u}} for u in urls]

    async def fake_scrape(url, strategy, force_refresh):
        single_scraped.append(url)
        return []

    scraper._ingest_many = fake_ingest_many
    scraper._scrape_single = fake_scrape
    out = asyncio.run(scraper.fetch(
        "_", action="scrape", urls=["https://a.test/1", "https://b.test/2"]))
    assert ingested == ["https://a.test/1", "https://b.test/2"]
    assert single_scraped == []  # "_" was never sent to the fetcher
    assert out == [{"text": "ok", "metadata": {"source": "https://a.test/1"}},
                   {"text": "ok", "metadata": {"source": "https://b.test/2"}}]


def test_fetch_crawl_honors_urls_list(tmp_path, monkeypatch):
    """`--urls` on action=crawl ingests the explicit list instead of
    sitemap discovery on the placeholder URL."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    ingested, crawled = [], []

    async def fake_ingest_many(urls, strategy, force_refresh):
        ingested.extend(urls)
        return [{"text": "ok", "metadata": {"source": u}} for u in urls]

    async def fake_crawl(url, strategy, force_refresh):
        crawled.append(url)
        return []

    scraper._ingest_many = fake_ingest_many
    scraper._crawl_domain = fake_crawl
    asyncio.run(scraper.fetch("_", action="crawl", urls=["https://c.test/9"]))
    assert ingested == ["https://c.test/9"]
    assert crawled == []


def test_fetch_scrape_without_urls_still_uses_positional(tmp_path, monkeypatch):
    """action=scrape with no --urls keeps scraping the single positional URL
    (the documented path, unchanged)."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    single_scraped = []

    async def fake_scrape(url, strategy, force_refresh):
        single_scraped.append(url)
        return [hc.Chunk(text="ok", metadata={"source": url})]

    scraper._scrape_single = fake_scrape
    out = asyncio.run(scraper.fetch("https://example.test/x", action="scrape"))
    assert single_scraped == ["https://example.test/x"]
    assert out == [{"text": "ok", "metadata": {"source": "https://example.test/x"}}]


def test_hoardcore_scopes_vault_to_subdir(tmp_path, monkeypatch):
    """HoardCore(vault_name='sleep') must point the vault at root_dir/sleep."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore(vault_name="sleep")
    assert scraper.vault.root_dir == os.path.join(str(tmp_path), "sleep")
    assert os.path.isdir(scraper.vault.root_dir)
    assert scraper.vault.db_path == os.path.join(str(tmp_path), "sleep", "vault.db")


def test_cli_discover_zero_is_recall_only(tmp_path, monkeypatch):
    """Regression (the `discover or 5` bug): `--discover 0` on the CLI must
    reach research() as 0 (recall-only), not be rewritten to 5."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    seen = {}

    class _FakeHC:
        def __init__(self, vault_name=None):
            pass

        @property
        def vault(self):
            return type("V", (), {"root_dir": str(tmp_path)})

        @property
        def artifacts_dir(self):
            return str(tmp_path)

        def organize_artifacts_by_day(self):
            return []

        async def research(self, question, out_path=None, discover=5, recall=6,
                           strategy=None, answer_first=None, keep_low=False):
            seen.update(question=question, discover=discover, recall=recall)
            return str(tmp_path / "out.md")

    monkeypatch.setattr(hc, "HoardCore", _FakeHC)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(hc.main(
            ["_", "--action", "research", "--query", "q",
             "--discover", "0", "--recall", "4"]))
    assert ei.value.code == 0
    assert seen["discover"] == 0  # not rewritten to 5
    assert seen["recall"] == 4


def test_fetch_search_honors_max_results(tmp_path, monkeypatch):
    """`--limit`/max_results must cap chunks returned for action=search
    (it was ignored, always using indexer.search_limit)."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    captured = {}

    def fake_search(query, limit, domain=None, hybrid=None):
        captured["limit"] = limit
        return [hc.Chunk(text="c", metadata={"source": "https://s.test"})]

    scraper.vault.search_vault = fake_search
    asyncio.run(scraper.fetch("https://s.test", action="search", query="q", max_results=7))
    assert captured["limit"] == 7
    # No --limit: falls back to the configured search_limit (20).
    asyncio.run(scraper.fetch("https://s.test", action="search", query="q"))
    assert captured["limit"] == cfg.get("indexer.search_limit", 20)


def test_discover_and_ingest_honors_limit(tmp_path, monkeypatch):
    """`--limit N` on discover must ingest the top N results (not always
    discovery.top_rank), with a search pool at least as large."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()
    results = [hc.SearchResult(title=f"t{i}", url=f"https://d.test/{i}")
               for i in range(8)]
    captured = {}

    async def fake_search(query, max_results, strategy):
        captured["pool"] = max_results
        return results

    async def fake_ingest_many(urls, strategy, force_refresh):
        captured["targets"] = urls
        return []

    scraper.discovery.search = fake_search
    scraper._ingest_many = fake_ingest_many
    asyncio.run(scraper._discover_and_ingest("q", 3, "fast", False))
    assert captured["targets"] == [u.url for u in results[:3]]
    assert captured["pool"] >= 3  # never shrinks below the search pool default
    # No --limit: falls back to discovery.top_rank (6).
    asyncio.run(scraper._discover_and_ingest("q", 0, "fast", False))
    assert captured["targets"] == [u.url for u in results[:cfg.get("discovery.top_rank", 6)]]


def test_crawl_serves_vaulted_chunks_on_cache_hit(tmp_path, monkeypatch):
    """Regression: re-crawling an already-vaulted site must return the stored
    chunks (mirroring _scrape_single), not silently report zero content."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    scraper = hc.HoardCore()

    async def fake_discover(url):
        return ["https://s.test/1", "https://s.test/2"]

    scraper.crawler.discover_urls = fake_discover

    async def fake_process(url, strategy, force_refresh):
        return [], {"cached": True, "url": url}

    def fake_get_chunks(url):
        return [hc.Chunk(text="cached", metadata={"source": url})]

    scraper._process_document = fake_process
    scraper.vault.get_chunks_for_url = fake_get_chunks
    chunks = asyncio.run(scraper._crawl_domain("https://s.test", "fast", False))
    assert [c.text for c in chunks] == ["cached", "cached"]
    assert [c.metadata["source"] for c in chunks] == ["https://s.test/1", "https://s.test/2"]


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


def test_parse_duckduckgo_filters_ad_tracking_links():
    """Regression: DuckDuckGo's ad tracker (/y.js) and Bing's /aclick
    redirector were ingested as false 'sources'. Ad/tracking URLs must be
    dropped at parse time, not fetched+indexed."""
    yjs = ("https://html.duckduckgo.com/y.js?ad_domain=top10.com&ad_provider=bingv7aa"
           "&ad_type=txad&u2=https%3A%2F%2Fwww.bing.com%2Faclick%3Fld%3D123")
    bing = "https://www.bing.com/aclick?ld=e8FFWqWt2SuSI0&u=aHR0cHM6Ly9leGFtcGxlLmNvbQ"
    ads = "https://www.googlesyndication.com/pagead/conversion/?ad_type=txad"
    html = (f'<a class="result__a" href="{yjs}"><b>Ad</b></a>'
            f'<a class="result__a" href="{bing}"><b>Ad2</b></a>'
            f'<a class="result__a" href="{ads}"><b>Ad3</b></a>'
            '<a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=xx">'
            '<b>Real</b> Result</a>')
    res = hc.WebSearchProvider._parse_duckduckgo(html, 5)
    assert len(res) == 1
    assert res[0].url == "https://example.com/page"


def test_is_ad_tracking_url():
    good = ("https://example.com/report"
            "?gclid=123&utm_source=news&utm_medium=email")
    for bad in (
        "https://duckduckgo.com/y.js?ad_domain=top10.com&ad_type=txad",
        "https://www.bing.com/aclick?ld=123&u=abc",
        "https://ad.doubleclick.net/ddm/trackclk/N123",
        "https://pagead2.googlesyndication.com/pagead/aclk?sac=1",
        "https://tag.adservice.google.com.au/",
        "https://s.amazon-adsystem.com/aaaaaaaa/amzn-adsystem",
    ):
        assert hc.is_ad_tracking_url(bad), bad
    assert not hc.is_ad_tracking_url(good)
    assert not hc.is_ad_tracking_url("")
    assert not hc.is_ad_tracking_url("not a url")


def test_process_document_refuses_http_error_status(tmp_path, monkeypatch):
    """Regression (soft-404): a body delivered with a 4xx/5xx status is an
    error page, not content. It must be refused (junk) rather than indexed."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()

    async def fake_fetch(url, strategy):
        return ("<html><body>404 not found. Sorry.</body></html>",
                None, "text/html", 404)

    scraper.fetcher.fetch = fake_fetch
    out = asyncio.run(scraper._process_document(
        "https://example.test/ghost-page", "fast", False))
    assert out[1].get("junk_reason") == "http_error_status=404"
    # 200 responses are still fetched/parsed normally (no status refusal).
    async def ok_fetch(url, strategy):
        return ("<html><body>This page details real solar farm capacity.</body></html>",
                None, "text/html", 200)
    scraper.fetcher.fetch = ok_fetch
    out2 = asyncio.run(scraper._process_document(
        "https://example.test/real", "fast", False))
    assert not out2[1].get("junk")


def test_research_discover_zero_is_recall_only(tmp_path, monkeypatch):
    """Regression: `--discover 0` must mean "never touch the web", not fall
    back to the config default and run live discovery anyway."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()
    ran = {"discover": False}

    async def boom(query, max_results, strategy, force_refresh):
        ran["discover"] = True
        raise AssertionError("DISCOVER must be skipped when discover=0")

    scraper._discover_and_ingest = boom
    hit = hc.Chunk(text="recall-only answer body",
                   metadata={"confidence": "high",
                             "source_url": "https://memory.test/x"})
    scraper.vault.search_vault = lambda *a, **k: [hit]
    out = os.path.join(str(tmp_path), "grounding.md")
    path = asyncio.run(scraper.research("question", out_path=out,
                                        discover=0, recall=3,
                                        answer_first=False))
    assert ran["discover"] is False  # no web hunt
    assert path is not None
    with open(path, encoding="utf-8") as f:
        assert "recall-only answer body" in f.read()


def test_research_reports_filtered_low_confidence(tmp_path, monkeypatch):
    """The grounding file must be transparent when `filter_low` drops
    low-confidence hits: it should report the raw count dropped and list the
    filtered sources, so a reduced chunk count reads as an intentional filter
    rather than an under-filled recall."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()
    scraper._discover_and_ingest = lambda *a, **k: None

    low = hc.Chunk(
        text="low relevance body",
        metadata={"confidence": "low", "source_url": "https://low.test/1"})
    high = hc.Chunk(
        text="high relevance body",
        metadata={"confidence": "high", "source_url": "https://high.test/1"})
    # Same source as the low chunk, so that source is already represented by a
    # strong hit and the low chunk is genuinely redundant (filter_low now keeps
    # one chunk per distinct source, so it must be excluded on duplicate grounds).
    high_from_low_source = hc.Chunk(
        text="strong body from low.test",
        metadata={"confidence": "high", "source_url": "https://low.test/1"})
    scraper.vault.search_vault = lambda *a, **k: [high, high_from_low_source, low]

    out = os.path.join(str(tmp_path), "grounding.md")
    path = asyncio.run(scraper.research("q", out_path=out, discover=0, recall=3,
                                        answer_first=False))
    assert path is not None
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # the low hit is excluded from retrieved sources, but its filtering is noted
    assert "low relevance body" not in content
    assert "filter_low" in content and "low-confidence hit(s)" in content
    assert "https://low.test/1" in content  # listed as a filtered source


def test_research_keep_low_retains_low_confidence(tmp_path, monkeypatch):
    """`keep_low=True` must bypass filter_low and retain low-confidence hits in
    the grounding file (the opt-in for exhaustive/deep hunts)."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()
    scraper._discover_and_ingest = lambda *a, **k: None

    low = hc.Chunk(
        text="low relevance body",
        metadata={"confidence": "low", "source_url": "https://low.test/1"})
    high = hc.Chunk(
        text="high relevance body",
        metadata={"confidence": "high", "source_url": "https://high.test/1"})
    scraper.vault.search_vault = lambda *a, **k: [high, low]

    out = os.path.join(str(tmp_path), "grounding.md")
    path = asyncio.run(scraper.research("q", out_path=out, discover=0, recall=3,
                                        answer_first=False, keep_low=True))
    assert path is not None
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # the low hit is retained as evidence (not filtered)
    assert "low relevance body" in content
    assert "filter_low" not in content
    assert "https://low.test/1" in content


def test_process_document_skips_ad_tracking_url(tmp_path, monkeypatch):
    """Even if an ad URL reaches the pipeline, it must be refused before any
    fetch or index happens (the crawler-level regression)."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()
    hit = {"fetched": False}

    async def boom(*a, **k):
        hit["fetched"] = True
        return ()

    scraper.fetcher.fetch = boom
    out = asyncio.run(scraper._process_document(
        "https://html.duckduckgo.com/y.js?ad_domain=top10.com&ad_type=txad",
        "fast", False))
    assert hit["fetched"] is False
    assert out[1].get("junk_reason") == "ad_tracking_url"


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
                        lambda query, limit=None, hybrid=None, **kwargs: [])

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


def test_default_grounding_context_is_suffixed_not_clobbered(tmp_path, monkeypatch):
    """Regression: two research runs in one day shared the default
    artifacts/YYYY-MM-DD/grounding_context.md, so the second silently wiped
    the first. The default name must uniquify (grounding_context_N.md)."""
    cfg = TempConfig(str(tmp_path))
    cfg._overrides["storage.artifacts_dir"] = os.path.join(str(tmp_path), "arts")
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()
    first = scraper.resolve_artifact_out(None)
    assert first.endswith(os.path.join("grounding", "grounding_context.md"))
    # First run writes it; second run must pick a fresh name.
    os.makedirs(os.path.dirname(first), exist_ok=True)
    with open(first, "w", encoding="utf-8") as f:
        f.write("# first")
    second = scraper.resolve_artifact_out(None)
    assert second.endswith(os.path.join("grounding", "grounding_context_2.md"))
    assert second != first
    with open(second, "w", encoding="utf-8") as f:
        f.write("# second")
    with open(first, encoding="utf-8") as f:
        assert f.read() == "# first"

    # Explicit --out paths are never rewritten or renamed.
    explicit = os.path.join(str(tmp_path), "mine.md")
    assert scraper.resolve_artifact_out(explicit) == explicit


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


def test_aggressive_picks_curl_when_aiohttp_soft404s(tmp_path):
    """Anti-bot disguise: aiohttp returns a 404 body, curl_cffi returns real
    content with 200. Concurrency must pick the 200 (the anti-bot 404 is not
    the truth), so the 404-disguised page is rescued instead of junk-filtered."""
    f = _fetcher(tmp_path)
    f._fetch_aiohttp = lambda u: _stub(("404 not found", None, "text/html", 404))
    f._fetch_curl_cffi = lambda u: _stub(("real content", None, "text/html", 200))
    f._fetch_flaresolverr = lambda u: _stub((None, None, "", None))
    out = asyncio.run(f.fetch("http://x/", "aggressive"))
    assert out[0] == "real content"
    assert out[3] == 200


def test_aggressive_runs_aiohttp_and_curl_concurrently(tmp_path):
    """Latency fix: aiohttp + curl_cffi must both be invoked (concurrently) for
    balanced/aggressive; the first leg with content wins. Verifies no leg is
    skipped, so we never regress the curl_cffi fallback."""
    f = _fetcher(tmp_path)
    calls = {"aio": 0, "curl": 0}

    async def _aio(u):
        calls["aio"] += 1
        return None, None, "", None

    async def _curl(u):
        calls["curl"] += 1
        return "curl content", None, "text/html", 200

    f._fetch_aiohttp = _aio
    f._fetch_curl_cffi = _curl
    f._fetch_flaresolverr = lambda u: _stub((None, None, "", None))
    out = asyncio.run(f.fetch("http://x/", "aggressive"))
    assert out[0] == "curl content"
    assert calls["aio"] == 1 and calls["curl"] == 1
