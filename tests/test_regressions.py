"""Regression tests for the v0.10.0 engine audit findings (B*/S*).

Every test targets a specific audited defect and must fail against the
pre-fix code; see CHANGELOG v0.10.0 for the finding IDs.
"""

import asyncio
import threading
import time

import pytest

import hoardcore as hc
from tests.conftest import TempConfig


def test_parallel_ingest_vectors_align_after_near_dedup(tmp_path):
    """B1: embeddings in the parallel pipeline were indexed against the
    ORIGINAL chunk list, but the writer re-filtered near-duplicates and
    re-enumerated the shorter list — so every vector after the first dropped
    chunk landed under the wrong rowid. Vectors must match their own text."""
    cfg = TempConfig(str(tmp_path), {
        "indexer.parallel": True,
        "indexer.near_dedup": True,
    })
    vault = hc.VaultManager(cfg)
    topics = [
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
    chunks = [
        hc.Chunk(text=topic, metadata={"source": "https://b1.test/1"})
        for topic in topics
    ]
    chunks[2] = chunks[1]  # exact duplicate -> dropped by near-dedup
    vault.ingest_chunks_parallel("https://b1.test/1", chunks, {})

    with vault._db() as (_conn, cur):
        rows = cur.execute(
            "SELECT c.rowid, c.text, v.vector FROM chunks_fts c "
            "LEFT JOIN chunk_vectors v ON v.chunk_rowid = c.rowid "
            "WHERE c.url = 'https://b1.test/1' ORDER BY c.rowid"
        ).fetchall()
    assert len(rows) == 9  # chunk[2] dropped by near-dedup
    assert all(vec is not None for _, _, vec in rows)
    for rowid, text, vec in rows:
        assert vec == vault.embeddings.vectorize(text), (
            f"rowid {rowid} stored a vector that does not match its own text")


def test_ttl_zero_means_never_expire(vault, make_chunk):
    """B3: cache.ttl_seconds = 0 is documented as 'never expire' but the
    code treated any positive age as expired (age < 0 is never true)."""
    vault.index_document("https://ttl.test/1",
                         [make_chunk("body", url="https://ttl.test/1")], {})
    assert vault.document_exists("https://ttl.test/1", ttl_seconds=0)
    assert vault.document_exists("https://ttl.test/1", ttl_seconds=-5)
    assert vault.document_exists("https://ttl.test/1", ttl_seconds=86400)


def test_near_dedup_high_threshold_falls_back_to_full_scan(tmp_path):
    """B6: the simhash bucket probe is only safe while its probe count fits
    SQLite's bound-variable limit. Above that (threshold >= 5) it must degrade
    to a full-table scan instead of raising 'too many SQL variables'."""
    vault = hc.VaultManager(TempConfig(str(tmp_path), {
        "indexer.near_dedup": True,
        "indexer.near_dedup_threshold": 5,
    }))
    stored = {0x123, 0x456, 0x789}
    with vault._db() as (_conn, cur):
        for i, sh in enumerate(sorted(stored)):
            cur.execute(
                "INSERT OR IGNORE INTO chunks_simhash "
                "(simhash, chunk_hash, url, text, first_seen, bucket) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sh, f"h{i}", "https://b6.test", "t", time.time(),
                 sh & hc._SIMHASH_BUCKET_MASK))
    assert len(hc._simhash_bucket_patterns(5)) > hc._MAX_SQL_VARIABLES
    with vault._db() as (_conn, cur):
        candidates = vault._near_duplicate_candidates(cur, 0x123, 5)
    assert set(candidates) == stored


def test_connection_pool_replaces_broken_connection(tmp_path):
    """S5: a broken checked-out connection was dropped without being closed
    (fd + WAL handle leak). It must be closed and replaced by a live one."""
    pool = hc.ConnectionPool(str(tmp_path / "v.db"), pool_size=1)
    bad = pool._pool.get()
    bad.close()
    pool._pool.put(bad)
    conn = pool.get()
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    pool.put(conn)
    pool.close_all()


def test_ingest_many_serves_cached_chunks(tmp_path, monkeypatch):
    """B4: _ingest_many silently returned zero chunks for an already-vaulted
    URL while scrape/crawl served the stored chunks. Cache hits must mirror
    _scrape_single."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()
    scraper.vault.index_document(
        "https://c.test/1",
        [hc.Chunk(text="vaulted content", metadata={"source": "https://c.test/1"})],
        {})

    async def fake_process(url, strategy, force_refresh):
        return [], {"cached": True}

    scraper._process_document = fake_process
    out = asyncio.run(scraper._ingest_many(["https://c.test/1"], "fast", False))
    assert len(out) == 1
    assert out[0]["text"] == "vaulted content"


def test_discovery_plugin_providers_run_as_fallback_tail(tmp_path):
    """B5: plugin discovery providers were registered but never consulted;
    search() only tried the hardcoded DuckDuckGo provider. They must form
    the tail of the fallback chain."""
    provider = hc.WebSearchProvider(TempConfig(str(tmp_path)), object())

    async def fail(url, strategy):
        return None

    provider._fetch_with_backoff = fail

    async def plugin(query, max_results):
        return [hc.SearchResult(title="plugin hit", url="https://plugin.test")]

    provider.plugin_providers = {"custom": plugin}
    res = asyncio.run(provider.search("q", max_results=5, strategy="fast"))
    assert [r.url for r in res] == ["https://plugin.test"]


def test_vector_matrix_cache_invalidated_on_ingest(tmp_path):
    """S1: the brute-force vector-scan matrix is cached keyed only on
    (row count, byte width). A rewrite-in-place that keeps the count (e.g.
    backfill_vectors recomputing a stale vector) could serve stale vectors.
    Any chunk_vectors write must drop the cache."""
    vault = hc.VaultManager(TempConfig(str(tmp_path)))
    for i, text in enumerate(["cache invalidation probe alpha",
                              "cache invalidation probe beta"]):
        vault.index_document(
            f"https://s1.test/{i}",
            [hc.Chunk(text=text, metadata={"source": f"https://s1.test/{i}"})],
            {})
    qvec = vault.embeddings.vectorize("probe query words")
    with vault._db() as (_conn, cur):
        vault._vector_scan(cur, qvec, 5, None)
    assert vault._vec_mat_cache.get("mat") is not None  # cache populated
    # Corrupt one vector so backfill rewrites rows in place (same count), then
    # confirm the stale matrix is dropped instead of being served.
    with vault._db() as (_conn, cur):
        cur.execute("UPDATE chunk_vectors SET vector = ? WHERE chunk_rowid = "
                    "(SELECT MIN(chunk_rowid) FROM chunk_vectors)", (b"\x00" * 4,))
    assert vault.backfill_vectors() == 1
    assert not vault._vec_mat_cache.get("mat")


def test_verify_claim_gets_recall_depth_wired(tmp_path, monkeypatch):
    """B2: `verify --recall` was documented but silently ignored on the CLI.
    verify_hint must receive the caller's recall depth instead of a hardcoded
    default."""
    captured = {}

    class _FakeHC:
        def __init__(self, vault_name=None):
            pass

        @property
        def vault(self):
            return type("V", (), {"root_dir": str(tmp_path)})()

        @property
        def artifacts_dir(self):
            return str(tmp_path)

        def organize_artifacts_by_day(self):
            return []

        @staticmethod
        def verify_claim(claim):
            return "unverified"

        @staticmethod
        def verify_hint(claim, recall=5):
            captured["recall"] = recall
            return None

    monkeypatch.setattr(hc, "HoardCore", _FakeHC)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(hc.main(
            ["_", "--action", "verify", "--claim", "x", "--hint", "--recall", "11"]))
    assert ei.value.code == 2
    assert captured["recall"] == 11


def test_parallel_ingest_large_batch_slow_embed_does_not_deadlock(tmp_path):
    """E2 gap found by live stress test (moon2026, 2026-08-17): with real
    embedding latency, a batch larger than the bounded queues deadlocks the
    parallel pipeline — the main thread blocks feeding work_q while the workers
    block on a full result_q. A deliberately slow vectorize() makes the race
      deterministic. All vectors must land against their own chunk text."""
    class SlowEmbed(hc.EmbeddingsEngine):
        def vectorize(self, text):
            time.sleep(0.05)  # simulate realistic (non-instant) embed latency
            return b"x" * 32

    cfg = TempConfig(str(tmp_path), {"indexer.parallel": True})
    vault = hc.VaultManager(cfg)
    vault.embeddings = SlowEmbed(cfg)
    n = 60  # > PIPELINE_QUEUE_SIZE (20) + WORKER_THREADS (4)
    chunks = [
        hc.Chunk(text=f"parallel slow embed item {i} megawatt solar", metadata={"source": "https://dl.test/1"})
        for i in range(n)
    ]

    # A guard thread fails the test if ingest_chunks_parallel hangs.
    done = threading.Event()

    def _watch():
        if not done.wait(30.0):
            raise AssertionError("parallel ingest deadlocked on a large batch")

    guard = threading.Thread(target=_watch, daemon=True)
    guard.start()
    try:
        vault.ingest_chunks_parallel("https://dl.test/1", chunks, {})
    finally:
        done.set()

    with vault._db() as (_conn, cur):
        rows = cur.execute(
            "SELECT c.rowid, c.text, v.vector FROM chunks_fts c "
            "LEFT JOIN chunk_vectors v ON v.chunk_rowid = c.rowid "
            "WHERE c.url = 'https://dl.test/1' ORDER BY c.rowid"
        ).fetchall()
    assert len(rows) == n
    assert all(vec == b"x" * 32 for _, _, vec in rows)


def _run_filter_low_case(tmp_path, monkeypatch, filter_low):
    root = tmp_path / f"fl-{filter_low}"
    cfg = TempConfig(str(root), {
        "research.filter_low": filter_low,
        "storage.artifacts_dir": str(root / "artifacts"),
    })
    monkeypatch.setattr(hc, "ConfigManager", lambda c=cfg: c)
    scraper = hc.HoardCore(["flt"])

    hi = hc.Chunk(text="anchor solid fact", metadata={
        "source_url": "https://a.test/1", "confidence": "high"})
    # Same source as the high hit: with filtering ON this duplicate-source
    # low hit is dropped; with filtering OFF it must survive.
    lo = hc.Chunk(text="weak tail claim", metadata={
        "source_url": "https://a.test/1", "confidence": "low"})
    monkeypatch.setattr(scraper, "_search_across_vaults",
                        lambda *a, **k: [hi, lo])
    out = asyncio.run(scraper.research("probe query", discover=0, recall=5))
    assert out is not None
    return open(out, encoding="utf-8").read()


def test_research_filter_low_config_is_honored(tmp_path, monkeypatch):
    """research.filter_low was documented but never read: low-confidence hits
    were dropped even when the key was set to false. The toggle must now be
    honored (false -> retain low hits; true/default -> drop duplicates)."""
    body_off = _run_filter_low_case(tmp_path, monkeypatch, False)
    assert "weak tail claim" in body_off          # retained
    assert "dropped" not in body_off              # no filtering note

    body_on = _run_filter_low_case(tmp_path, monkeypatch, True)
    assert "weak tail claim" not in body_on       # dropped
    assert "dropped" in body_on                   # honest note present


def test_binary_parser_disabled_by_config(tmp_path, monkeypatch):
    """parsers.enable_pdf=false must refuse PDF ingestion instead of parsing
    it anyway (the toggles existed but were never read before v0.15.1)."""
    cfg = TempConfig(str(tmp_path), {"parsers.enable_pdf": False,
                                     "crawler.parallel_workers": 2})
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()

    class FakeFetcher:
        async def fetch(self, url, strategy):
            return (None, b"%PDF-1.4 fake", "application/pdf", 200)

    scraper.fetcher = FakeFetcher()
    out = asyncio.run(scraper.fetch("https://x.test/doc.pdf",
                                    action="scrape", strategy="fast"))
    assert out and out[0]["metadata"].get("junk_reason") == "parser_disabled:pdf"
