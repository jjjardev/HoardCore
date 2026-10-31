"""Regression tests for the v0.16.5 audit fix round.

Every test targets a specific finding from the four-agent audit of v0.16.4
(P0/P1 correctness, P2 defects, performance quick wins) and must fail against
the pre-fix code. Finding IDs match the audit report.
"""

import array
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import hoardcore as hc
from tests.conftest import TempConfig

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "hoardcore.py"


# ---------------------------------------------------------------------------
# P0-1: verify/audit false-denied non-ASCII verbatim claims
# ---------------------------------------------------------------------------

def test_verify_non_ascii_case_folds(tmp_path):
    """P0-1: the SQL lower()/LIKE prefilter folds ASCII only, so a claim
    identical to stored text with accented/Cyrillic letters came back
    PARTIAL. The FTS tokenizer folds all Unicode; an honest verbatim claim
    must verify regardless of script."""
    vault = hc.VaultManager(TempConfig(str(tmp_path)))
    texts = [
        "Éxito total en la prueba de campo.",
        "Привет мир из проекта hoardcore.",
        "Ångström enheden måles omhyggeligt.",
    ]
    for i, text in enumerate(texts):
        vault.index_document(f"https://nonascii.test/{i}",
                             [hc.Chunk(text=text, metadata={"header_path": "R"})], {})
    scraper = hc.HoardCore.__new__(hc.HoardCore)
    scraper.vaults = [vault]
    for text in texts:
        assert scraper.verify_claim(text) == "verified", text


def test_verify_still_enforces_word_order(tmp_path):
    """P0-1 fix must not loosen the strict side: reordered tokens stay
    UNVERIFIED/PARTIAL, never 'verified'."""
    vault = hc.VaultManager(TempConfig(str(tmp_path)))
    vault.index_document("https://order.test/1",
                         [hc.Chunk(text="the vault stores evidence verbatim",
                                   metadata={"header_path": "R"})], {})
    scraper = hc.HoardCore.__new__(hc.HoardCore)
    scraper.vaults = [vault]
    assert scraper.verify_claim("verbatim stores evidence the vault") != "verified"


# ---------------------------------------------------------------------------
# P1-1: max_per_source honored on every retrieval path
# ---------------------------------------------------------------------------

def _diversity_vault(tmp_path):
    vault = hc.VaultManager(TempConfig(str(tmp_path)))
    for suffix in ["alpha", "beta", "gamma"]:
        vault.index_document(
            "https://rich.test/page",
            [hc.Chunk(text=f"solar panel yield report {suffix}",
                      metadata={"header_path": "R"})], {})
    vault.index_document(
        "https://other.test/page",
        [hc.Chunk(text="solar panel yield report delta",
                  metadata={"header_path": "R"})], {})
    return vault


def test_fts_fast_path_respects_max_per_source(tmp_path):
    """P1-1: with fts_fast_path on (default), the strong-keyword case used to
    return fts_rows[:limit] ignoring the cap — one rich page crowded out the
    rest exactly when FTS was strongest."""
    vault = _diversity_vault(tmp_path)
    hits = vault.search_vault("solar panel yield report", limit=2,
                              hybrid=True, max_per_source=1)
    assert len(hits) == 2
    sources = {h.metadata["source_url"] for h in hits}
    assert sources == {"https://rich.test/page", "https://other.test/page"}
    assert all(h.metadata.get("retrieval") == "fts_fast" for h in hits)


def test_plain_fts_respects_max_per_source_and_tags_keyword(tmp_path):
    """P1-1: the non-hybrid path never applied the cap and never set
    keyword_hit (which also made multi-vault fast-mode confidence unable to
    reach 'high')."""
    vault = _diversity_vault(tmp_path)
    hits = vault.search_vault("solar panel yield report", limit=2,
                              hybrid=False, max_per_source=1)
    assert len(hits) == 2
    sources = {h.metadata["source_url"] for h in hits}
    assert sources == {"https://rich.test/page", "https://other.test/page"}
    assert all(h.metadata.get("keyword_hit") is True for h in hits)
    assert all(h.metadata.get("retrieval") == "fts" for h in hits)
    uncapped = vault.search_vault("solar panel yield report", limit=10,
                                  hybrid=False)
    assert len(uncapped) == 4  # cap off = old behavior intact


def test_fts_fast_recency_never_discards_relevance(tmp_path):
    """P2: the fast-path recency reordering used (decay, rowid) as its key —
    bm25 rank had zero weight, so equal-decay chunks came back reversed and a
    fresher page always beat a better match. Decay must use rank as the
    tie-breaker."""
    vault = hc.VaultManager(TempConfig(str(tmp_path), {
        "embeddings.recency_half_life_days": 30,
    }))
    vault.index_document("https://rec.test/a",
                         [hc.Chunk(text="yield report alpha",
                                   metadata={"header_path": "R"})], {})
    vault.index_document("https://rec.test/b",
                         [hc.Chunk(text="yield report beta",
                                   metadata={"header_path": "R"})], {})
    # Pin identical fetched_at so decay is exactly equal for both docs.
    with vault._db() as (_conn, cur):
        cur.execute("UPDATE documents SET fetched_at = ?", (1000000.0,))
        rows = cur.execute(
            "SELECT url FROM chunks_fts WHERE chunks_fts MATCH "
            "'\"yield\" AND \"report\"' ORDER BY rank, rowid"
        ).fetchall()
    expected_first = rows[0][0]
    hits = vault.search_vault("yield report", limit=2, hybrid=True)
    assert [h.metadata["source_url"] for h in hits] == [r[0] for r in rows]
    assert hits[0].metadata["source_url"] == expected_first
    assert all(h.metadata["retrieval"] == "fts_fast" for h in hits)


# ---------------------------------------------------------------------------
# P2: OR-fallback topicality guard counts DISTINCT tokens
# ---------------------------------------------------------------------------

def test_or_fallback_requires_distinct_matching_tokens(tmp_path):
    """P2: 'zzz marketplace marketplace marketplace' used to pass the >=2
    gate on raw occurrences and crown a false-high on one coincidental word."""
    vault = hc.VaultManager(TempConfig(str(tmp_path)))
    vault.index_document("https://m.test/1",
                         [hc.Chunk(text="marketplace rules for vendors",
                                   metadata={"header_path": "R"})], {})
    hits = vault.search_vault("zzz marketplace marketplace marketplace",
                              limit=3, hybrid=True)
    for h in hits:
        assert h.metadata.get("keyword_hit") is not True
        assert h.metadata.get("confidence") != "high"


# ---------------------------------------------------------------------------
# P1-2: challenge-page escalation is not retried
# ---------------------------------------------------------------------------

def test_search_blocked_is_never_retried():
    """P1-2: SEARCH_BLOCKED used to be swallowed by the generic retry handler
    and re-sent max_retries times against the blocked provider."""
    cfg = TempConfig("/tmp/search-blocked", {
        "discovery.max_retries": 2,
        "discovery.backoff_seconds": 0.01,
    })
    calls = {"n": 0}

    class FakeFetcher:
        async def fetch(self, url, strategy):
            calls["n"] += 1
            return ('<html><head><title>Captcha</title></head>'
                    '<body>prove you are human</body></html>', None, "text/html")

    p = hc.WebSearchProvider(cfg, FakeFetcher())
    res = asyncio.run(p.search("what is x", max_results=5))
    assert res == []
    assert calls["n"] == 1  # hard block: no retry storm


# ---------------------------------------------------------------------------
# P2: FlareSolverr cookie payload shape / SSRF default / shared session
# ---------------------------------------------------------------------------

def test_flaresolverr_cookies_sent_as_list():
    """P2: FlareSolverr's DTO wants cookies as a list of {name, value}
    objects; the parsed dict shape made cookie-authenticated solves fail."""
    cfg = TempConfig("/tmp/solver-cookies", {
        "solver.enabled": True,
        "auth.cookie_string": "cf_clearance=abc; session=xyz",
    })
    captured: dict[str, object] = {}

    class _Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def json(self):
            return {"status": "ok", "solution": {
                "status": 200, "url": "https://t.test/x",
                "headers": {"Content-Type": "text/html"},
                "response": "<html>ok</html>",
            }}

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json=None, timeout=None):  # noqa: A002
            captured.update(json or {})
            return _Resp()

    monkeypatch_target = hc.aiohttp.ClientSession
    hc.aiohttp.ClientSession = _Session
    try:
        f = hc.NetworkFetcher(cfg)
        text, _bin, _ctype, status = asyncio.run(
            f._fetch_flaresolverr("https://t.test/x"))
    finally:
        hc.aiohttp.ClientSession = monkeypatch_target
    assert status == 200
    assert captured["cookies"] == [
        {"name": "cf_clearance", "value": "abc"},
        {"name": "session", "value": "xyz"},
    ]


def test_networkfetcher_ssrf_default_matches_config():
    """P2: the literal default used to be False while DEFAULT_CONFIG says
    True — a directly-constructed fetcher silently disabled the guard."""
    f = hc.NetworkFetcher(TempConfig("/tmp/ssrf-default"))
    assert f._ssrf_protected is True


def test_shared_session_reused_within_loop_and_swapped_across(monkeypatch):
    """Perf A3: every fetch used to open a new TCPConnector with
    force_close=True. A shared session must survive across fetches in one
    loop and be rebuilt (old connector closed) when the loop changes."""

    class _Resp:
        status = 200
        headers = {"Content-Type": "text/html"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def text(self):
            return "hello"

    class _CountingSession:
        constructed = 0

        def __init__(self, *a, **k):
            type(self).constructed += 1
            self.closed = False
            self.connector = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def detach(self):
            pass

        def get(self, url, **kwargs):
            return _Resp()

    monkeypatch.setattr(hc.NetworkFetcher, "validate_url_target",
                        staticmethod(lambda url: True))
    monkeypatch.setattr(hc.aiohttp, "ClientSession", _CountingSession)
    f = hc.NetworkFetcher(TempConfig("/tmp/shared-session"))

    async def _two_fetches():
        await f.fetch("http://x.test/1", "fast")
        await f.fetch("http://x.test/2", "fast")

    asyncio.run(_two_fetches())
    assert _CountingSession.constructed == 1  # reused within one loop
    asyncio.run(f.fetch("http://x.test/3", "fast"))
    assert _CountingSession.constructed == 2  # new loop -> fresh session


def test_robots_txt_fetched_once_per_crawl(monkeypatch):
    """Perf A3: robots.txt used to be downloaded twice per crawl (sitemap
    scan + rules parse). One fetch must serve both."""
    robots = ("User-agent: *\nDisallow: /private/\n"
              "Sitemap: https://s.test/map.xml\n")
    gets = {"n": 0}

    class _Resp:
        status = 200

        def __init__(self, body):
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def text(self):
            return self._body

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, timeout=None, **kwargs):
            gets["n"] += 1
            return _Resp(robots)

    monkeypatch.setattr(hc.aiohttp, "ClientSession", _Session)
    planner = hc.CrawlerPlanner(TempConfig("/tmp/robots-once", {
        "crawler.respect_robots": True,
    }))
    sitemaps = asyncio.run(planner.get_robots_urls("https://s.test"))
    assert sitemaps == ["https://s.test/map.xml"]
    asyncio.run(planner.fetch_robots_rules("https://s.test/page"))
    assert gets["n"] == 1
    assert not planner.allowed("https://s.test/private/x")
    assert planner.allowed("https://s.test/public/x")


# ---------------------------------------------------------------------------
# P2: parallel pipeline CA-cache parity + simhash transaction safety
# ---------------------------------------------------------------------------

def _parallel_texts():
    base = [
        "zebra quartz blizzard hammock jigsaw kiwi mantra nimbus",
        "ocean forest mountain river glacier canyon delta summit",
        "python rust golang haskell lisp erlang prolog julia",
        "basketball football cricket tennis hockey rugby soccer",
        "amethyst granite basalt onyx topaz jade pearl amber",
        "android linux macos windows unix bsd haiku rhel",
        "cello violin viola piano harp flute oboe clarinet",
        "mercury venus earth mars jupiter saturn uranus neptune",
        "saffron turmeric cumin ginger fennel clove anise nutmeg",
        "tokyo oslo lima kabul yaren reykjavik praha bern",
    ]
    chunks = [hc.Chunk(text=t, metadata={"source": "https://ca.test/1"})
              for t in base]
    chunks[3] = chunks[0]  # exact duplicate -> one CA entry, one vector
    return chunks


def test_parallel_ingest_populates_ca_cache_and_reuses_it(tmp_path):
    """P2: the parallel pipeline used to bypass chunk_vectors_ca entirely —
    duplicate text was re-embedded and the cache never populated. Second
    ingest of identical content must be served fully from the cache."""
    cfg = TempConfig(str(tmp_path), {"indexer.parallel": True})
    vault = hc.VaultManager(cfg)
    chunks = _parallel_texts()
    vault.ingest_chunks_parallel("https://ca.test/1", chunks, {})

    distinct = {c.text for c in chunks}
    with vault._db() as (_conn, cur):
        ca_hashes = {r[0] for r in cur.execute("SELECT chunk_hash FROM chunks_ca")}
        vec_hashes = {r[0] for r in cur.execute(
            "SELECT chunk_hash FROM chunk_vectors_ca")}
    assert len(ca_hashes) == len(distinct)
    assert ca_hashes == vec_hashes  # cache populated for every stored chunk

    calls = {"n": 0}

    def _no_embed(texts):
        calls["n"] += 1
        return [None] * len(texts)

    vault.embeddings.vectorize_batch = _no_embed  # type: ignore[method-assign]
    vault.ingest_chunks_parallel("https://ca.test/2", chunks, {})
    assert calls["n"] == 0  # fully cache-served
    hits = vault.search_vault("zebra quartz blizzard", limit=5, hybrid=False)
    assert {h.metadata["source_url"] for h in hits} >= {
        "https://ca.test/1", "https://ca.test/2"}


def test_parallel_ingest_leaves_no_ghost_simhash_rows(tmp_path):
    """P2: the near-dedup filter committed simhash rows in its own
    transaction before the writer; a crash between them left ghost rows that
    filtered every future re-ingest to zero. Simhash rows must reference
    stored chunks only."""
    cfg = TempConfig(str(tmp_path), {
        "indexer.parallel": True,
        "indexer.near_dedup": True,
    })
    vault = hc.VaultManager(cfg)
    vault.ingest_chunks_parallel("https://ghost.test/1", _parallel_texts(), {})
    with vault._db() as (_conn, cur):
        simhash_hashes = {r[0] for r in cur.execute(
            "SELECT chunk_hash FROM chunks_simhash")}
        stored_hashes = {r[0] for r in cur.execute(
            "SELECT chunk_hash FROM chunks_ca")}
    assert simhash_hashes
    assert simhash_hashes <= stored_hashes  # no ghosts


# ---------------------------------------------------------------------------
# P2: chunker / junk / routing
# ---------------------------------------------------------------------------

def test_whitespace_only_document_indexes_no_empty_chunk():
    """P2: '   \\n\\t ' used to fall through and index a zero-length chunk."""
    chunker = hc.SemanticChunker(TempConfig("/tmp/ws-chunk"))
    chunks = asyncio.run(chunker.chunk("   \n\n  \t ", "https://ws.test/1", {}))
    assert len(chunks) == 1
    assert chunks[0].text == "[Empty content]"


def test_junk_detector_single_marker_short_page_passes():
    """P2: one boilerplate substring used to refuse any <600-char page; a
    real short how-to that merely says 'sign in' must survive, while a
    multi-marker shell stays refused."""
    real = ("How to sign in to your new vault quickly. "
            "First open the settings page and choose the account tab. "
            "Then pick the sync menu and confirm the folder. "
            "Finally restart the app so the change takes effect today.")
    assert len(real) < 600
    assert hc.HoardCore._detect_junk(real, None, {}, 0.5) is None
    shell = ("Please click here to continue. Sign in to see this content. "
             "Forgot your password?")
    reason = hc.HoardCore._detect_junk(shell, None, {}, 0.5)
    assert reason is not None and reason.startswith("boilerplate:")


def test_xhtml_content_type_routes_through_html_cleaner(tmp_path, monkeypatch):
    """P2: application/xhtml+xml used to bypass clean_html and index raw
    markup via binary_as_text."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()

    async def _fake_fetch(url, strategy):
        return (None,
                b"<html><body><p>Hello wonderful world of testing</p></body></html>",
                "application/xhtml+xml", 200)

    scraper.fetcher.fetch = _fake_fetch  # type: ignore[method-assign]
    chunks, meta = asyncio.run(
        scraper._process_document("https://xhtml.test/1", "fast", True))
    assert meta.get("source_type") == "html"
    assert chunks
    assert all("<p>" not in c.text for c in chunks)


def test_normalize_claim_keeps_glued_markers_as_content():
    """normalize_claim must not glue tokens around in-word markers: '2*3'
    stays '2*3', while emphasis at token boundaries still strips."""
    assert hc.normalize_claim("2*3") == "2*3"
    assert hc.normalize_claim("increased by **17% in 2025**") == \
        "increased by 17% in 2025"
    assert hc.normalize_claim("…2025**.") == "...2025."
    assert hc.normalize_claim("`code` block") == "code block"


# ---------------------------------------------------------------------------
# P2: retry sweep reports one error per URL
# ---------------------------------------------------------------------------

def test_ingest_many_reports_one_error_per_url(tmp_path, monkeypatch):
    """P2: a URL failing twice used to yield duplicate error chunks, and a
    retry that succeeded left the stale error dict beside the real chunks."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()

    async def _always_fail(url, strategy, force):
        return ([hc.Chunk(text=f"Network error: {url}",
                          metadata={"source": url, "error": True})],
                {"error": "FETCH_FAILED"})

    scraper._process_document = _always_fail  # type: ignore[method-assign]
    results = asyncio.run(scraper._ingest_many(
        ["https://fail-a.test/1", "https://fail-b.test/2"], "fast", False))
    errors = [r for r in results if r.get("metadata", {}).get("error")]
    assert len(errors) == 2  # one per URL, not one per attempt
    assert {e["metadata"]["source"] for e in errors} == {
        "https://fail-a.test/1", "https://fail-b.test/2"}


# ---------------------------------------------------------------------------
# P2: recall of zero chunks is honored on every path
# ---------------------------------------------------------------------------

def test_recall_zero_returns_empty_everywhere(tmp_path):
    vault = _diversity_vault(tmp_path)
    assert vault.search_vault("solar panel yield", limit=0, hybrid=True) == []
    assert vault.search_vault("solar panel yield", limit=0, hybrid=False) == []


# ---------------------------------------------------------------------------
# Perf: corroboration stamping + vector-scan cache key
# ---------------------------------------------------------------------------

def test_corroboration_counts_distinct_sources(tmp_path):
    vault = hc.VaultManager(TempConfig(str(tmp_path)))
    text = "shared verbatim sentence used by two sources entirely"
    for i in range(2):
        vault.index_document(f"https://corr.test/{i}",
                             [hc.Chunk(text=text, metadata={"header_path": "R"})], {})
    hits = vault.search_vault("shared verbatim sentence", limit=2, hybrid=True)
    assert hits
    assert all(h.metadata.get("corroborated") == 2 for h in hits)


def test_vector_scan_cache_survives_equal_size_swap(tmp_path):
    """The matrix cache keyed on (count, total bytes) alone could serve a
    stale matrix after a 1:1 same-size vector replacement; MAX(rowid) is part
    of the key now, so the key must advance with the newest rowid."""
    vault = hc.VaultManager(TempConfig(str(tmp_path), {
        "embeddings.fts_fast_path": False,  # force the vector-scan path
    }))
    vault.index_document("https://swap.test/1",
                         [hc.Chunk(text="alpha beta gamma delta sweep",
                                   metadata={"header_path": "R"})], {})
    hits1 = vault.search_vault("alpha beta gamma", limit=1, hybrid=True)
    assert hits1
    assert vault._vec_mat_cache.get("mat") is not None
    assert vault._vec_mat_cache.get("max_rid") == 1
    # WORM: re-ingest appends a new version row (new rowid) -> the cache is
    # dropped and the next scan rebuilds under the advanced key.
    vault.index_document("https://swap.test/1",
                         [hc.Chunk(text="alpha beta gamma delta sweep v2",
                                   metadata={"header_path": "R"})], {})
    assert vault._vec_mat_cache.get("mat") is None  # invalidated on ingest
    hits2 = vault.search_vault("alpha beta gamma", limit=1, hybrid=True)
    assert hits2
    assert vault._vec_mat_cache.get("max_rid") == 2
    assert vault._vec_mat_cache.get("count") == 2


# ---------------------------------------------------------------------------
# Perf: lazy dense model + numpy int8
# ---------------------------------------------------------------------------

def test_dense_mode_stays_lazy_for_known_models(tmp_path):
    """Perf A1: the dense ONNX model used to load in EmbeddingsEngine.__init__
    (5-6s on every CLI action, N times for N vaults). Known models must stay
    lazy with the correct dim assumed up front."""
    engine = hc.EmbeddingsEngine(TempConfig(str(tmp_path), {
        "embeddings.mode": "dense",
    }))
    assert engine.mode == "dense"
    assert engine.dim == 384  # BAAI/bge-small-en-v1.5, from the known table
    assert engine.base_dim == 384
    assert engine._dense is None  # not loaded yet
    fp1 = engine.fingerprint()
    engine2 = hc.EmbeddingsEngine(TempConfig(str(tmp_path) + "-2", {
        "embeddings.mode": "dense",
    }))
    assert engine2.fingerprint() == fp1  # stable across processes/instances


def test_int8_quantizer_matches_python_reference():
    """Perf A7: the numpy quantizer must be bit-identical to the original
    pure-Python loop (round-half-to-even, clamp to [-127, 127])."""
    vec = array.array('f', [0.5, -0.5, 0.004, 1.0, -1.0, 0.0, 0.999, -0.999,
                            0.0025, -0.0025])
    out = hc.EmbeddingsEngine._quantize_int8(vec.tobytes())
    got = array.array('b')
    got.frombytes(out)
    expected = array.array('b', (
        max(-127, min(127, round(v * 127))) for v in vec))
    assert list(got) == list(expected)


def test_config_manager_instances_are_isolated(tmp_path):
    """Coverage gap: the real ConfigManager TOML load/defaults-merge was never
    exercised (conftest substitutes TempConfig). Two non-default paths must be
    independent and merge defaults under the file's values."""
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text("[storage]\nroot_dir = 'va'\n\n[cache]\nttl_seconds = 5\n",
                 encoding="utf-8")
    b.write_text("[storage]\nroot_dir = 'vb'\n", encoding="utf-8")
    ca = hc.ConfigManager(config_path=str(a))
    cb = hc.ConfigManager(config_path=str(b))
    assert ca.get("storage.root_dir") == "va"
    assert ca.get("cache.ttl_seconds") == 5          # from file
    assert ca.get("cache.ttl_seconds") != cb.get("cache.ttl_seconds")  # default
    assert cb.get("storage.root_dir") == "vb"
    assert ca.get("network.ssrf_protection") is True  # defaults merged
    assert ca is not cb


def test_migrate_page_size_roundtrip(tmp_path):
    """Coverage gap: the 16 KB migration path had no round-trip test."""
    vault = hc.VaultManager(TempConfig(str(tmp_path), {"storage.page_size": 4096}))
    vault.index_document("https://mig.test/1",
                         [hc.Chunk(text="migration roundtrip payload",
                                   metadata={"header_path": "R"})], {})
    assert vault.migrate_page_size(16384) is True
    with vault._db() as (_conn, cur):
        assert cur.execute("PRAGMA page_size").fetchone()[0] == 16384
    hits = vault.search_vault("migration roundtrip", limit=3, hybrid=False)
    assert hits and hits[0].metadata["source_url"] == "https://mig.test/1"
    assert vault.migrate_page_size(16384) is False  # idempotent


# ---------------------------------------------------------------------------
# CLI: --json, export, structured exit codes
# ---------------------------------------------------------------------------

def _run_cli(*args, cwd):
    return subprocess.run(
        [sys.executable, str(MODULE), *args],
        capture_output=True, text=True, timeout=180, cwd=str(cwd),
    )


def _isolated_root(tmp_path, extra: str = "") -> Path:
    (tmp_path / "hoardcore.toml").write_text(
        f"[storage]\nroot_dir = '{tmp_path / 'data'}'\n\n"
        "[embeddings]\nenabled = true\nmode = 'sparse'\ndim = 64\n"
        "hybrid_search = true\n\n"
        "[network]\ndefault_strategy = 'fast'\nenable_preflight = false\n\n"
        f"{extra}",
        encoding="utf-8",
    )
    return tmp_path


def test_cli_stats_and_verify_emit_json(tmp_path):
    root = _isolated_root(tmp_path)
    res = _run_cli("_", "--action", "stats", "--json", cwd=root)
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert isinstance(payload, list) and payload[0]["chunks"] == 0

    res = _run_cli("_", "--action", "verify", "--json",
                   "--claim", "nothing at all", cwd=root)
    assert res.returncode == 2
    payload = json.loads(res.stdout)
    assert payload["verdict"] == "unverified"


def test_cli_export_writes_jsonl(tmp_path):
    root = _isolated_root(tmp_path)
    seed = (
        "import hoardcore as hc\n"
        f"cfg = hc.ConfigManager(config_path={str(root / 'hoardcore.toml')!r})\n"
        "v = hc.VaultManager(cfg)\n"
        "v.index_document('https://export.test/1', [hc.Chunk("
        "text='exportable unique chunk payload', metadata={'header_path': 'R'})], {})\n"
    )
    seeded = subprocess.run(
        [sys.executable, "-c", seed], cwd=str(root),
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert seeded.returncode == 0, seeded.stderr

    out = root / "vault.jsonl"
    res = _run_cli("_", "--action", "export", "--out", str(out), cwd=root)
    assert res.returncode == 0, res.stderr
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["text"] == "exportable unique chunk payload"
    assert rec["metadata"]["source_url"] == "https://export.test/1"
    assert rec["metadata"]["chunk_id"] >= 1


def test_cli_ingest_all_failed_exits_one(tmp_path):
    root = _isolated_root(tmp_path)
    res = _run_cli("_", "--action", "ingest", "--strategy", "fast",
                   "--urls", "https://nx-a7x9.test/1,https://nx-a7x9.test/2",
                   cwd=root)
    assert res.returncode == 1
    assert "INGEST LEDGER" in res.stdout


def test_cli_search_json_empty_result_exits_zero(tmp_path):
    root = _isolated_root(tmp_path)
    res = _run_cli("_", "--action", "search", "--json",
                   "--query", "anything", cwd=root)
    assert res.returncode == 0  # empty search is a legal answer
    payload = json.loads(res.stdout)
    assert payload["action"] == "search"
    assert payload["chunks"] == []
