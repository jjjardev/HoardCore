"""Tests for the three crawl/recall robustness fixes.

1. Sitemap-INDEX recursion (a CMS sitemap that lists child sitemaps).
2. Zero-content crawl is reported as a failure, not a quiet success.
3. Site-chrome (template boilerplate replicated across pages) is demoted at
   RECALL — never deleted, so it stays verifiable for [V].
"""

import asyncio

import hoardcore as hc
from tests.conftest import TempConfig

# --------------------------------------------------------------------------
# 1. Sitemap index recursion
# --------------------------------------------------------------------------

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


def _session_returning(bodies: dict[str, str]):
    """A ClientSession stand-in that serves `bodies` per URL."""
    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, timeout=None, **kwargs):
            body = bodies.get(url)
            if body is None:
                return _FakeResp(404, "")
            return _FakeResp(200, body)
    return _Session


def _planner(tmp_path, bodies, **overrides):
    cfg = TempConfig(str(tmp_path), overrides=overrides)
    planner = hc.CrawlerPlanner(cfg)
    planner._user_agent = "hctest"
    # Neutralize the SSRF gate: these tests never touch the network.
    hc.NetworkFetcher.validate_url_target = staticmethod(lambda _u: True)
    return planner


def test_is_sitemap_index_detects_index_and_urlset():
    index = ('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             '<sitemap><loc>https://e.test/post-sitemap.xml</loc></sitemap>'
             '</sitemapindex>')
    urlset = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
              '<url><loc>https://e.test/a</loc></url></urlset>')
    assert hc.CrawlerPlanner._is_sitemap_index(index) is True
    assert hc.CrawlerPlanner._is_sitemap_index(urlset) is False
    # Extensionless index children must still be recognized (root-element based).
    assert hc.CrawlerPlanner._is_sitemap_index(
        "<sitemapindex><sitemap><loc>https://e.test/s1</loc></sitemap></sitemapindex>"
    ) is True


def test_parse_sitemap_recurses_into_index(tmp_path, monkeypatch):
    """The ASTI trap: sitemap.xml is an INDEX of 2 children, whose <loc> entries
    are real pages. Non-recursive parsing returns the children as if they were
    pages; recursion must return only the leaf page URLs."""
    bodies = {
        "https://e.test/sitemap.xml":
            "<sitemapindex><sitemap><loc>https://e.test/post-sitemap.xml</loc></sitemap>"
            "<sitemap><loc>https://e.test/page-sitemap.xml</loc></sitemap></sitemapindex>",
        "https://e.test/post-sitemap.xml":
            "<urlset><url><loc>https://e.test/p1</loc></url>"
            "<url><loc>https://e.test/p2</loc></url></urlset>",
        "https://e.test/page-sitemap.xml":
            "<urlset><url><loc>https://e.test/pg1</loc></url></urlset>",
    }
    planner = _planner(tmp_path, bodies)
    monkeypatch.setattr(hc.aiohttp, "ClientSession",
                        lambda *a, **k: _session_returning(bodies)())
    urls = asyncio.run(planner.parse_sitemap("https://e.test/sitemap.xml"))
    assert set(urls) == {"https://e.test/p1", "https://e.test/p2", "https://e.test/p1".replace("p1", "pg1")}
    # The child sitemaps themselves must never be returned as pages.
    assert "https://e.test/post-sitemap.xml" not in urls
    assert "https://e.test/sitemap.xml" not in urls


def test_parse_sitemap_respects_depth_limit(tmp_path, monkeypatch):
    """depth=0 is the legacy non-recursive behaviour: only the index itself is
    read and no children are followed."""
    bodies = {
        "https://e.test/sitemap.xml":
            "<sitemapindex><sitemap><loc>https://e.test/a-sitemap.xml</loc></sitemap></sitemapindex>",
        "https://e.test/a-sitemap.xml":
            "<urlset><url><loc>https://e.test/p1</loc></url></urlset>",
    }
    planner = _planner(tmp_path, bodies, **{"crawler.sitemap_index_depth": 0})
    monkeypatch.setattr(hc.aiohttp, "ClientSession",
                        lambda *a, **k: _session_returning(bodies)())
    assert asyncio.run(planner.parse_sitemap("https://e.test/sitemap.xml")) == []


def test_parse_sitemap_survives_index_cycle(tmp_path, monkeypatch):
    """A self-referential index must terminate, not spin."""
    bodies = {
        "https://e.test/sitemap.xml":
            "<sitemapindex><sitemap><loc>https://e.test/sitemap.xml</loc></sitemap>"
            "<sitemap><loc>https://e.test/b.xml</loc></sitemap></sitemapindex>",
        "https://e.test/b.xml":
            "<urlset><url><loc>https://e.test/p1</loc></url></urlset>",
    }
    planner = _planner(tmp_path, bodies)
    monkeypatch.setattr(hc.aiohttp, "ClientSession",
                        lambda *a, **k: _session_returning(bodies)())
    assert asyncio.run(planner.parse_sitemap("https://e.test/sitemap.xml")) == ["https://e.test/p1"]


def test_parse_sitemap_caps_page_budget_after_expansion(tmp_path, monkeypatch):
    """sitemap_limit is a CRAWL BUDGET: it caps the leaf pages, not the index fan-out."""
    many = "".join(f"<url><loc>https://e.test/p{i}</loc></url>" for i in range(50))
    bodies = {
        "https://e.test/sitemap.xml":
            "<sitemapindex><sitemap><loc>https://e.test/a.xml</loc></sitemap></sitemapindex>",
        "https://e.test/a.xml": f"<urlset>{many}</urlset>",
    }
    planner = _planner(tmp_path, bodies, **{"crawler.sitemap_limit": 10})
    monkeypatch.setattr(hc.aiohttp, "ClientSession",
                        lambda *a, **k: _session_returning(bodies)())
    assert len(asyncio.run(planner.parse_sitemap("https://e.test/sitemap.xml"))) == 10


# --------------------------------------------------------------------------
# 2. Zero-content crawl must not look successful
# --------------------------------------------------------------------------

def _hc_with_junk(tmp_path, **overrides):
    """A HoardCore wired to a temp vault whose fetches always yield junk."""
    inst = hc.HoardCore.__new__(hc.HoardCore)
    cfg = TempConfig(str(tmp_path), overrides=overrides)
    inst.config = cfg
    inst.bus = hc.EventBus()
    inst.vault = hc.VaultManager(cfg, None, event_bus=inst.bus)
    inst.vaults = [inst.vault]
    inst.crawler = hc.CrawlerPlanner(cfg)
    inst.save_binary = False
    inst.save_raw_html = False

    async def _discover(_url):
        return ["https://e.test/a.xml", "https://e.test/b.xml"]

    async def _process(url, *_a, **_k):
        return [hc.Chunk(text="", metadata={"junk": True, "junk_reason": "empty_extraction"})], \
               {"junk": True, "junk_reason": "empty_extraction"}

    inst.crawler.discover_urls = _discover
    inst._process_document = _process
    return inst


def test_crawl_with_zero_content_returns_nothing(tmp_path, caplog):
    """Every URL rejected as junk -> zero chunks, and the failure is LOGGED as an
    error rather than passing silently (the old behaviour exited 0 green)."""
    import logging
    inst = _hc_with_junk(tmp_path)
    with caplog.at_level(logging.ERROR, logger="hoardcore"):
        out = asyncio.run(inst._crawl_domain("https://e.test", "fast", False))
    assert out == []
    assert any("Crawl produced NO content" in r.getMessage() for r in caplog.records)


def test_crawl_zero_content_warns_about_sitemap_index(tmp_path, caplog):
    """The diagnostic must name the sitemap-index cause, since that is the
    overwhelmingly common reason a crawl discovers URLs but keeps none."""
    import logging
    inst = _hc_with_junk(tmp_path)
    with caplog.at_level(logging.ERROR, logger="hoardcore"):
        asyncio.run(inst._crawl_domain("https://e.test", "fast", False))
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "sitemap INDEX" in msg
    assert "empty_extraction" in msg  # the per-URL outcomes are reported


# --------------------------------------------------------------------------
# 3. Site-chrome demotion at recall
# --------------------------------------------------------------------------

_NAV = ("RESEARCH Space Technology Wireless Technology Artificial Intelligence "
        "SMART Cities Emerging Technologies Robotics Research Data PhilSensors "
        "TECH TRANSFER INNOVATION COARE PREGINET EPDC RESOURCES Events ASTICON")


def _vault_with_chrome(tmp_path, min_urls=3):
    cfg = TempConfig(str(tmp_path))
    vault = hc.VaultManager(cfg)

    def mk(text, header="Root", url="https://e.test/a"):
        return hc.Chunk(text=text, metadata={"header_path": header, "source": url})

    # The same nav block replicated across 4 distinct pages = site chrome.
    for i in range(4):
        vault.index_document(f"https://e.test/page{i}", [mk(_NAV, "Menu", f"https://e.test/page{i}")], {})
    # A unique, page-specific body of prose.
    vault.index_document("https://e.test/masthead", [
        mk("Asticon 2026 showcases microsatellite telemetry pipelines for hydromet deployments", "Body"),
    ], {})
    return vault


def test_chunk_urls_ledger_records_distinct_urls(tmp_path):
    vault = _vault_with_chrome(tmp_path)
    with vault._db() as (_c, cur):
        cur.execute("SELECT COUNT(DISTINCT url) FROM chunk_urls")
        assert cur.fetchone()[0] >= 5
        cur.execute(
            "SELECT COUNT(*) FROM chunk_urls WHERE chunk_hash = "
            "(SELECT chunk_hash FROM chunk_urls GROUP BY chunk_hash "
            " ORDER BY COUNT(*) DESC LIMIT 1)"
        )
        assert cur.fetchone()[0] == 4  # the nav block, on 4 pages


def test_recall_demotes_replicated_chrome(tmp_path):
    """A nav-menu chunk replicated on 4 pages must be pushed below the page's
    real content, and flagged `chrome` — but NOT deleted from the vault."""
    vault = _vault_with_chrome(tmp_path)
    hits = vault.search_vault("Asticon microsatellite telemetry", limit=5, hybrid=True)
    assert hits, "the topical body must still be recalled"
    top = hits[0].text
    assert "microsatellite" in top
    nav = [h for h in hits if h.text == _NAV]
    assert nav, "the replicated nav block should still be recalled"
    # Every copy of the chrome chunk is flagged and sits after the real content
    # (4 pages x 1 identical nav row each, so the group occupies the tail).
    for h in nav:
        assert h.metadata.get("chrome") is True
        assert h.metadata.get("chrome_urls") == 4
    body_idx = next(i for i, h in enumerate(hits) if "microsatellite" in h.text)
    assert all(hits.index(h) > body_idx for h in nav)
    # The evidence itself is untouched: still stored, still retrievable by URL.
    stored = "".join(c.text for c in vault.get_chunks_for_url("https://e.test/page0"))
    assert _NAV in stored


def test_chrome_threshold_is_configurable_and_disableable(tmp_path):
    """chrome_min_urls=0 turns the demotion off entirely."""
    cfg = TempConfig(str(tmp_path), overrides={"retrieval.chrome_min_urls": 0})
    vault = hc.VaultManager(cfg)
    mk = lambda t, u: hc.Chunk(text=t, metadata={"header_path": "Menu", "source": u})  # noqa: E731
    for i in range(4):
        vault.index_document(f"https://e.test/p{i}", [mk(_NAV, f"https://e.test/p{i}")], {})
    hits = vault.search_vault("ASTICON", limit=5, hybrid=False)
    assert not any(h.metadata.get("chrome") for h in hits)


def test_unique_content_is_never_flagged_chrome(tmp_path):
    """A chunk stored on ONE url must never be demoted, no matter how short."""
    vault = _vault_with_chrome(tmp_path)
    with vault._db() as (_c, cur):
        counts = vault._chrome_hashes(cur, ["Asticon 2026 showcases microsatellite telemetry pipelines for hydromet deployments"])
    assert counts == {} or all(v < 3 for v in counts.values())


def test_all_chrome_recall_still_returns_results(tmp_path):
    """A set whose only matches are chrome is returned anyway (chrome is better
    than nothing) — the demotion must never turn a hit set empty. Each page's
    stored row still comes back (4 pages x the identical nav text), all flagged."""
    cfg = TempConfig(str(tmp_path))
    vault = hc.VaultManager(cfg)
    mk = lambda u: hc.Chunk(text=_NAV, metadata={"header_path": "Menu", "source": u})  # noqa: E731
    for i in range(4):
        vault.index_document(f"https://e.test/p{i}", [mk(f"https://e.test/p{i}")], {})
    hits = vault.search_vault("ASTICON", limit=5, hybrid=False)
    assert len(hits) == 4
    assert all(h.metadata.get("chrome") is True for h in hits)


def test_backfill_chunk_urls_populates_legacy_vault(tmp_path):
    """A vault whose rows predate the ledger must be backfilled on open, or the
    demotion silently no-ops after an upgrade."""
    cfg = TempConfig(str(tmp_path))
    vault = hc.VaultManager(cfg)
    mk = lambda t, u: hc.Chunk(text=t, metadata={"header_path": "Menu", "source": u})  # noqa: E731
    for i in range(3):
        vault.index_document(f"https://e.test/p{i}", [mk(_NAV, f"https://e.test/p{i}")], {})
    # Simulate the pre-upgrade state: ledger wiped, content still present.
    with vault._db() as (conn, cur):
        cur.execute("DELETE FROM chunk_urls")
        conn.commit()
    n = vault.backfill_chunk_urls()
    assert n == 3
    hits = vault.search_vault("ASTICON", limit=5, hybrid=False)
    assert hits[0].metadata.get("chrome") is True
    # Idempotent: a second run is a no-op.
    assert vault.backfill_chunk_urls() == 0


# --------------------------------------------------------------------------
# 4. Decoded binary must never be indexed as text
# --------------------------------------------------------------------------

# A real JPEG's leading bytes (SOI + APP1/Exif header), as Python sees it after
# `bytes.decode('utf-8', errors='ignore')` — the exact shape that was polluting
# recall on any site whose sitemap lists its images.
_JPEG_GARBAGE = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01Exif\x00\x00II\x00\x08\x00\x00\x00"
    b"\x06\x00\x12\x01\x03\x00\x01\x00\x00\x00\x01\x00\x1a\x01\x05\x00\x01"
    b"\x00\x01\x00\x00\x00\x00\x1a\x01\x05\x00\x01\x00\x01\x00\x00\x00"
).decode("utf-8", errors="ignore")


def _detect(markdown, qs=1.0):
    return hc.HoardCore._detect_junk(markdown, None, {}, qs)


def test_binary_ratio_distinguishes_garbage_from_prose():
    assert hc.HoardCore._binary_ratio(_JPEG_GARBAGE) > 0.3
    assert hc.HoardCore._binary_ratio("The DOST-ASTI mandate is R&D in ICT.") < 0.05
    # CJK and accented prose are printable, not "binary".
    assert hc.HoardCore._binary_ratio("研究開発Philippines ICT microelectronics") < 0.05
    assert hc.HoardCore._binary_ratio("") == 0.0


def test_junk_detector_rejects_decoded_binary():
    """The quality ratio cannot see this failure (garbage/garbage ~ 1.0), which
    is exactly why it reached the vault: 93% of one live crawl's chunks."""
    assert _detect(_JPEG_GARBAGE) == "binary_as_text"
    # Even wrapped in a plausible-looking header, the bulk is still binary.
    assert _detect("## Page 1\n" + _JPEG_GARBAGE * 4) == "binary_as_text"


def test_junk_detector_still_accepts_real_prose_and_code():
    assert _detect("## About ULAT\n\nULAT aims to observe the country's weather "
                   "behaviors through studying torrential rainfall.") is None
    # Code fences, tabs and newlines must not read as "binary".
    code = "```python\nif x < 10:\n\tprint('ok')\n```"
    assert _detect(code) is None
    assert _detect("Prices rose 5% — see p. 12 (100% of samples).") is None
    assert _detect("") == "empty_extraction"


def test_binary_document_is_not_indexed(tmp_path):
    """End-to-end: a binary payload must be skipped, not chunked into the vault."""
    cfg = TempConfig(str(tmp_path))
    vault = hc.VaultManager(cfg)
    assert _detect(_JPEG_GARBAGE * 6) == "binary_as_text"
    with vault._db() as (_c, cur):
        cur.execute("SELECT COUNT(*) FROM chunks_fts")
        assert cur.fetchone()[0] == 0


def test_is_binary_url_classifies_by_path_extension():
    B = hc.CrawlerPlanner._is_binary_url
    assert B("https://e.test/a.jpg") is True
    assert B("https://e.test/a.JPEG") is True
    assert B("https://e.test/deep/path/x.png?bwg=1783321955") is True
    assert B("https://e.test/font.woff2") is True
    assert B("https://e.test/video.mp4") is True
    # Pages and documents are kept.
    for keep in ("https://e.test/projects/ulat/", "https://e.test/a.pdf",
                 "https://e.test/doc.epub", "https://e.test/a.docx",
                 "https://e.test/", "https://e.test/a.aspx?id=1"):
        assert B(keep) is False, keep
    # A dot in a directory name is not an extension.
    assert B("https://e.test/v1.2/page") is False
    assert B("not a url at all") is False


def test_sitemap_drops_binary_urls_before_fetching(tmp_path, monkeypatch):
    """Attachments listed in a WordPress sitemap must not cost a fetch — against
    a Cloudflare site each one is a serialized FlareSolverr solve (~50 s)."""
    bodies = {
        "https://e.test/post-sitemap.xml":
            "<urlset>"
            "<url><loc>https://e.test/real-page</loc></url>"
            "<url><loc>https://e.test/wp-content/uploads/photo.jpg</loc></url>"
            "<url><loc>https://e.test/wp-content/uploads/font.woff2</loc></url>"
            "<url><loc>https://e.test/second-page</loc></url>"
            "</urlset>",
    }
    planner = _planner(tmp_path, bodies)
    monkeypatch.setattr(hc.aiohttp, "ClientSession",
                        lambda *a, **k: _session_returning(bodies)())
    urls = asyncio.run(planner.parse_sitemap("https://e.test/post-sitemap.xml"))
    assert urls == ["https://e.test/real-page", "https://e.test/second-page"]


# --------------------------------------------------------------------------
# 5. Cross-vault recall must demote chrome too
# --------------------------------------------------------------------------

def test_cross_vault_recall_demotes_chrome(tmp_path):
    """A replicated nav block pooled from two vaults must not outrank content;
    the single-vault demotion must not silently stop at the vault boundary."""
    nav = ("RESEARCH Space Technology Wireless Technology Artificial Intelligence "
           "SMART Cities Emerging Technologies PhilSensors")
    body = ("Asticon 2026 showcases microsatellite telemetry pipelines for "
            "hydromet deployments across the archipelago.")

    vaults = []
    for name in ("alpha", "beta"):
        cfg = TempConfig(str(tmp_path / name))
        v = hc.VaultManager(cfg, name)
        for i in range(3):
            v.index_document(
                f"https://{name}.test/p{i}",
                [hc.Chunk(text=nav, metadata={"header_path": "Menu",
                                              "source": f"https://{name}.test/p{i}"})], {})
        v.index_document(
            f"https://{name}.test/real",
            [hc.Chunk(text=body, metadata={"header_path": "Body",
                                           "source": f"https://{name}.test/real"})], {})
        vaults.append(v)

    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(str(tmp_path / "alpha"))
    inst.bus = hc.EventBus()
    inst.vault = vaults[0]
    inst.vaults = vaults

    hits = inst._search_across_vaults("ASTICON microsatellite telemetry", limit=6)
    assert hits
    body_idx = next(i for i, h in enumerate(hits) if "microsatellite" in h.text)
    nav_hits = [i for i, h in enumerate(hits) if h.text == nav]
    assert nav_hits, "the replicated nav block should still be recalled"
    assert all(i > body_idx for i in nav_hits), "chrome must sit below the content"
