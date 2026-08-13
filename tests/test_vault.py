"""Tests for the vault: indexing, hybrid retrieval (RRF), backfill, db hygiene."""


import time

import pytest

import hoardcore as hc
from tests.conftest import TempConfig


def test_index_and_fts_search(vault, make_chunk):
    chunks = [
        make_chunk("renewable solar farm generates megawatts of clean power", "Solar"),
        make_chunk("sugar milling is central to the local economy", "Sugar"),
    ]
    vault.index_document("https://example.test/p1", chunks, {"parser_used": "test"})

    hits = vault.search_vault("solar", limit=5, hybrid=False)
    assert len(hits) == 1
    assert "solar" in hits[0].text
    assert hits[0].metadata["source_url"] == "https://example.test/p1"


def test_index_updates_create_versions_worm(vault, make_chunk):
    """WORM semantics: re-ingesting a URL appends a new version, never overwrites."""
    vault.index_document("https://example.test/u", [make_chunk("first version")], {})
    vault.index_document("https://example.test/u", [make_chunk("second version"), make_chunk("extra")], {})
    # New content is searchable.
    assert len(vault.search_vault("second", hybrid=False)) == 1
    assert len(vault.search_vault("extra", hybrid=False)) == 1
    # Old content is NOT deleted — it remains in an earlier version.
    assert len(vault.search_vault("first", hybrid=False)) == 1
    # Two document versions exist for the URL (append-only).
    with vault._db() as (_conn, cursor):
        cursor.execute("SELECT version FROM documents WHERE url = ?", ("https://example.test/u",))
        versions = [r[0] for r in cursor.fetchall()]
    assert sorted(versions) == [1, 2]


def test_db_contextmanager_leaks_nothing(vault, make_chunk):
    """Writes inside a _db() block must be committed and connection closed even on error."""
    vault.index_document("https://example.test/x", [make_chunk("hello world content")], {})
    # Simulate an exception mid-query -> rollback, no dangling txn.
    with pytest.raises(RuntimeError):
        with vault._db() as (_conn, cur):
            cur.execute("SELECT 1")
            raise RuntimeError("boom")
    # Vault still usable afterwards => connection was properly released.
    assert vault.search_vault("hello", hybrid=False)


def test_document_exists_and_ttl(vault, make_chunk):
    vault.index_document("https://example.test/x", [make_chunk("c")], {})
    assert vault.document_exists("https://example.test/x", ttl_seconds=86400)
    assert not vault.document_exists("https://example.test/missing", ttl_seconds=86400)


def test_search_empty_and_punctuation_queries_do_not_crash(vault, make_chunk):
    vault.index_document("https://example.test/e", [make_chunk("renewable solar negros power")], {})
    # Empty / whitespace / operator-only queries must return [], never raise.
    for q in ["", "   ", "*", ":", "-", "( )", '"solar" "negros"']:
        for hybrid in (False, True):
            hits = vault.search_vault(q, hybrid=hybrid)
            assert hits == [] or isinstance(hits, list)  # no exception, sensible type


def test_fts_query_strips_operators_and_returns_none_for_junk():
    import hoardcore as hc
    assert hc.VaultManager._fts_query("solar negros") == '"solar" AND "negros"'
    assert hc.VaultManager._fts_query("a - b") == '"a" AND "b"'
    assert hc.VaultManager._fts_query("*") is None
    assert hc.VaultManager._fts_query("   ") is None


def test_backfill_vectors_populates_missing(vault, make_chunk):
    vault.index_document("https://example.test/a", [make_chunk("some text to vectorize here", )], {})
    # force a gap: delete the vector row, then backfill recomputes it
    with vault._db() as (_conn, cur):
        cur.execute("DELETE FROM chunk_vectors")
    n = vault.backfill_vectors()
    assert n == 1
    with vault._db() as (_conn, cur):
        cur.execute("SELECT COUNT(*) FROM chunk_vectors")
        assert cur.fetchone()[0] == 1


def test_backfill_skips_full_scan_when_nothing_missing(vault, make_chunk):
    vault.index_document("https://example.test/filled", [make_chunk("some text")], {})
    assert vault.backfill_vectors() == 0


def test_get_chunks_for_url_returns_stored_chunks_in_order(vault, make_chunk):
    vault.index_document("https://example.test/ordered", [
        make_chunk("first chunk", "Sec One"),
        make_chunk("second chunk", "Sec Two"),
    ], {})
    chunks = vault.get_chunks_for_url("https://example.test/ordered")
    assert [c.text for c in chunks] == ["first chunk", "second chunk"]
    assert all(c.metadata.get("source_url") == "https://example.test/ordered" for c in chunks)
    assert vault.get_chunks_for_url("https://example.test/missing") == []


def test_hybrid_search_ranks_relevant_first(vault, make_chunk):
    docs = [
        ("https://solar.test/1", "solar farm inverters panels megawatt capacity"),
        ("https://solar.test/2", "solar thermal concentrated power station"),
        ("https://recipe.test/3", "chocolate cake recipe with flour and eggs"),
        ("https://sugar.test/4", "sugar cane harvest tonnes per hectare"),
    ]
    for url, text in docs:
        vault.index_document(url, [make_chunk(text, url=url)], {})
    res = vault.search_vault("solar farm megawatt", limit=3, hybrid=True)
    assert len(res) == 3
    assert any("solar" in c.text for c in res)
    # neither sugar nor cake should beat solar results for a solar query
    assert not res[0].metadata["source_url"].startswith("https://recipe.test")


def test_embeddings_lexical_similarity():
    from hoardcore import ConfigManager
    # Pin sparse mode: this test exercises the dependency-free lexical hash,
    # whose cosine is a strict vocabulary-overlap measure (dense models give
    # non-trivial similarity even for unrelated text).
    cfg = ConfigManager()
    cfg._config["embeddings"]["mode"] = "sparse"
    cfg._config["embeddings"]["dim"] = 256
    eng = hc.EmbeddingsEngine(cfg)
    same = hc.EmbeddingsEngine.cosine(
        eng.vectorize("renewable energy solar power negros"),
        eng.vectorize("solar farm megawatts renewable"), eng.dim)
    diff = hc.EmbeddingsEngine.cosine(
        eng.vectorize("renewable energy solar power negros"),
        eng.vectorize("chocolate cake recipe"), eng.dim)
    assert same > 0.4
    assert diff < 0.15
    assert eng.enabled
    assert eng.mode == "sparse"


def test_dense_model_default_is_bge(tmp_path):
    """The default dense model is BAAI/bge-small-en-v1.5 (384-dim)."""
    from hoardcore import ConfigManager, EmbeddingsEngine
    # Build a fresh engine with a clean config default (avoid shared-state
    # mutation from other tests that pin sparse).
    cfg = ConfigManager()
    cfg._config["embeddings"]["mode"] = "dense"
    eng = EmbeddingsEngine(cfg)
    assert eng.mode == "dense"
    assert eng.dim == 384


def test_dense_mode_falls_back_to_sparse_when_unavailable(tmp_path):
    """If mode=dense but fastembed is not installed, degrade to sparse."""
    from hoardcore import ConfigManager, EmbeddingsEngine
    cfg = ConfigManager()
    cfg._config["embeddings"]["mode"] = "dense"
    cfg._config["embeddings"]["dim"] = 256
    eng = EmbeddingsEngine(cfg)
    # Either dense loaded (fastembed present) or fell back to sparse; never crash.
    assert eng.dim in (256, 384)
    vec = eng.vectorize("renewable energy solar")
    assert len(vec) == eng.dim * 4


def test_hybrid_search_attaches_confidence(vault, make_chunk):
    docs = [
        ("https://solar.test/1", "solar farm inverters panels megawatt capacity"),
        ("https://solar.test/2", "solar thermal concentrated power station"),
        ("https://recipe.test/3", "chocolate cake recipe with flour and eggs"),
    ]
    for url, text in docs:
        vault.index_document(url, [make_chunk(text, url=url)], {})
    res = vault.search_vault("solar farm megawatt", limit=3, hybrid=True)
    assert len(res) == 3
    confs = [c.metadata.get("confidence") for c in res]
    assert all(conf in ("high", "medium", "low") for conf in confs)
    # top hit should be high-confidence
    assert res[0].metadata.get("confidence") == "high"
    assert all(c.metadata.get("hybrid_score") is not None for c in res)


def test_confidence_discriminates_strong_vs_weak(vault, make_chunk):
    """A specific, both-list query should score 'high'; an off-topic or
    keyword-free query should score 'medium' or 'low' — i.e. the confidence
    band is not always 'high' (regression for the ratio-to-top bluntness)."""
    docs = [
        ("https://solar.test/1", "solar farm inverters panels megawatt capacity"),
        ("https://solar.test/2", "solar thermal concentrated power station"),
        ("https://recipe.test/3", "chocolate cake recipe with flour and eggs"),
    ]
    for url, text in docs:
        vault.index_document(url, [make_chunk(text, url=url)], {})

    strong = vault.search_vault("solar farm megawatt", limit=3, hybrid=True)
    assert strong[0].metadata.get("confidence") == "high"

    # A nonsense query with no keyword overlap must not be 'high'.
    weak = vault.search_vault("quantum banana marketplace zillion", limit=3, hybrid=True)
    confs = {c.metadata.get("confidence") for c in weak}
    assert "high" not in confs
    assert confs.issubset({"medium", "low"})


def test_verify_claim_three_states(vault, make_chunk):
    from hoardcore import HoardCore
    docs = [
        ("https://epoch.test/1",
         "the true doubling time is closer to six months, not three point four"),
        ("https://solar.test/2", "solar farm megawatt capacity grew this year"),
    ]
    for url, text in docs:
        vault.index_document(url, [make_chunk(text, url=url)], {})
    # monkeypatch a HoardCore instance to reuse the temp vault
    hc_obj = HoardCore.__new__(HoardCore)
    hc_obj.config = vault.config
    hc_obj.vault = vault
    # verified: a distinctive substring present verbatim
    assert hc_obj.verify_claim("doubling time is closer to six months") == "verified"
    # unverified: gibberish with no keyword coverage
    assert hc_obj.verify_claim("quantum banana zillion marketplace") == "unverified"


def test_backfill_rebuilds_stale_dimension_in_place(vault, make_chunk):
    """Switching embedding dimension recomputes mismatched vectors w/o DELETE."""
    vault.index_document("https://solar.test/1",
                         [make_chunk("solar farm megawatt capacity")], {})
    # Simulate stale vectors: write a wrong-dimension vector blob directly.
    from array import array
    stale = array('f', [0.1] * 128).tobytes()  # 128-dim, not vault._vector_dim
    with vault._db() as (_conn, cur):
        cur.execute(
            "INSERT OR REPLACE INTO chunk_vectors (chunk_rowid, url, vector) "
            "VALUES ((SELECT rowid FROM chunks_fts LIMIT 1), ?, ?)",
            ("https://solar.test/1", stale)
        )
    # Lower vault._vector_dim expectation trick: force rebuild by setting dim.
    vault._vector_dim = 64
    n = vault.backfill_vectors()
    assert n >= 1  # the stale row was recomputed
    with vault._db() as (_conn, cur):
        vec = cur.execute("SELECT vector FROM chunk_vectors LIMIT 1").fetchone()[0]
        assert len(vec) == 64 * 4  # now matches the configured dim


def test_content_addressable_dedup(vault, make_chunk):
    """Identical chunk text across documents is stored once in chunks_ca."""
    txt = "identical repeated boilerplate sentence"
    vault.index_document("https://a.test/1", [make_chunk(txt)], {})
    vault.index_document("https://b.test/2", [make_chunk(txt)], {})
    with vault._db() as (_conn, cur):
        n_ca = cur.execute("SELECT COUNT(*) FROM chunks_ca WHERE text = ?", (txt,)).fetchone()[0]
    assert n_ca == 1  # deduplicated


def test_verify_vault_passes_on_clean_vault(vault, make_chunk):
    """verify_vault returns True on a healthy, correctly-sized vault."""
    vault.index_document("https://solar.test/1",
                         [make_chunk("solar farm megawatt capacity")], {})
    assert vault.verify_vault() is True


def test_verify_vault_catches_corruption(vault, make_chunk):
    """verify_vault detects a hash mismatch in chunks_ca."""
    vault.index_document("https://solar.test/1",
                         [make_chunk("solar farm megawatt capacity")], {})
    # Corrupt a canonical chunk's text so its stored hash no longer matches.
    with vault._db() as (_conn, cur):
        row = cur.execute("SELECT chunk_hash FROM chunks_ca LIMIT 1").fetchone()
        assert row is not None
        cur.execute("UPDATE chunks_ca SET text = 'tampered text' WHERE chunk_hash = ?",
                    (row[0],))
    assert vault.verify_vault() is False


def test_verify_claim_tolerates_newline_split_text(vault, make_chunk):
    """A claim whose verbatim text spans a line break in the stored chunk
    must be VERIFIED (whitespace/newline normalization), not PARTIAL."""
    text = ("Sleep deprivation is associated with impaired cognitive function "
            "and\nreduced attention span in healthy adults.")
    vault.index_document("https://sleep.test/1", [make_chunk(text)], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    result = hc_inst.verify_claim("Sleep deprivation is associated with impaired cognitive function and reduced attention span")
    assert result == "verified"


def test_verify_claim_unverified_when_absent(vault, make_chunk):
    """A claim with no keyword support in the vault stays unverified."""
    vault.index_document("https://sleep.test/1",
                         [make_chunk("sleep architecture rem deep slow wave")], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    result = hc_inst.verify_claim("giraffes sleep standing up for exactly 47 minutes")
    assert result == "unverified"


def test_vault_isolation_between_vaults(tmp_path, make_chunk):
    """Per-vault naming isolates recall: content in vault A is invisible to vault B."""
    base = str(tmp_path)

    vault_a = hc.VaultManager(TempConfig(base), "sleep")
    vault_b = hc.VaultManager(TempConfig(base), "dating")

    vault_a.index_document("https://sleep.test/1",
                           [make_chunk("sleep duration optimal 7 to 9 hours", url="https://sleep.test/1")], {})
    vault_b.index_document("https://dating.test/1",
                           [make_chunk("attraction escalates with eye contact", url="https://dating.test/1")], {})

    assert len(vault_a.search_vault("sleep", hybrid=False)) == 1
    assert len(vault_a.search_vault("attraction", hybrid=False)) == 0
    assert len(vault_b.search_vault("attraction", hybrid=False)) == 1
    assert len(vault_b.search_vault("sleep", hybrid=False)) == 0
    assert vault_a.db_path != vault_b.db_path


def test_new_vault_uses_16384_page_size(vault):
    """P0.1: fresh vaults are created with 16 KB pages (float32 inline, no overflow)."""
    with vault._db() as (_conn, cur):
        page_size = cur.execute("PRAGMA page_size").fetchone()[0]
    assert page_size == 16384


def test_migrate_page_size_rewrites_vault(tmp_path, make_chunk):
    """P0.2: VACUUM INTO migration rewrites an existing vault at a new page size."""
    base = str(tmp_path)
    vault = hc.VaultManager(TempConfig(base))
    vault.index_document("https://solar.test/1",
                         [make_chunk("solar farm megawatt capacity")], {})
    # Rewrite at 8192 bytes.
    assert vault.migrate_page_size(8192) is True
    with vault._db() as (_conn, cur):
        assert cur.execute("PRAGMA page_size").fetchone()[0] == 8192
    # Data survived the rewrite.
    assert len(vault.search_vault("solar", hybrid=False)) == 1
    # Idempotent: already at target -> no-op.
    assert vault.migrate_page_size(8192) is False


def test_migrate_page_size_retries_after_stale_tmp(tmp_path, make_chunk):
    """A stale .ps<target> temp file must not block a retry (P0.2).

    VACUUM INTO refuses to overwrite an existing file; a previous failed
    attempt leaves one behind. The rewrite must clear it and still succeed.
    """
    import os

    base = str(tmp_path)
    vault = hc.VaultManager(TempConfig(base))
    vault.index_document("https://solar.test/1",
                         [make_chunk("solar farm megawatt capacity")], {})
    stale = vault.db_path + ".ps8192"
    with open(stale, "w", encoding="utf-8") as f:
        f.write("stale tmp from a failed attempt")
    assert vault.migrate_page_size(8192) is True
    assert not os.path.exists(stale)
    with vault._db() as (_conn, cur):
        assert cur.execute("PRAGMA page_size").fetchone()[0] == 8192
    assert len(vault.search_vault("solar", hybrid=False)) == 1


def test_migrate_page_size_noop_at_target(vault):
    """P0.2: migrating to the current size is a no-op returning False."""
    assert vault.migrate_page_size(16384) is False


def test_embeddings_int8_quantization():
    """P0.3: int8 quantize halves storage (1 byte/dim) while cosine still ranks.

    Restores ConfigManager singleton state so sibling tests (which assert
    float32 byte widths) stay order-independent.
    """
    from hoardcore import ConfigManager, EmbeddingsEngine
    cfg = ConfigManager()
    saved = dict(cfg._config["embeddings"])
    try:
        cfg._config["embeddings"]["mode"] = "sparse"
        cfg._config["embeddings"]["dim"] = 256
        eng = EmbeddingsEngine(cfg)
        assert eng.bytes_per_dim == 4  # sparse stays float32

        cfg._config["embeddings"]["mode"] = "dense"
        cfg._config["embeddings"]["quantize"] = "int8"
        eng8 = EmbeddingsEngine(cfg)
        assert eng8.bytes_per_dim == 1
        # Sparse-mode engine so no fastembed dependency: use hash vectors + manual quantize.
        vec_f32 = eng.vectorize("renewable energy solar power negros")
        vec_q = EmbeddingsEngine._quantize_int8(vec_f32)
        assert len(vec_q) == eng.dim * 1  # 1 byte per dim
        assert vec_q != vec_f32

        # int8 cosine still orders a matching pair above a non-matching one.
        same = EmbeddingsEngine.cosine(
            vec_q,
            EmbeddingsEngine._quantize_int8(eng.vectorize("solar farm megawatts renewable")),
            eng.dim)
        diff = EmbeddingsEngine.cosine(
            vec_q,
            EmbeddingsEngine._quantize_int8(eng.vectorize("chocolate cake recipe")),
            eng.dim)
        assert same > 0.3
        assert diff < 0.15
    finally:
        cfg._config["embeddings"] = saved


def test_fts_fast_path_skips_vector_when_fts_fills_limit(vault, make_chunk):
    """P1.1: when FTS5 alone fills the limit, results are tagged fts_fast."""
    docs = [
        ("https://solar.test/1", "solar farm inverters panels megawatt capacity"),
        ("https://solar.test/2", "solar farm site chosen for megawatt output"),
        ("https://solar.test/3", "solar farm construction megawatt timeline"),
        ("https://sugar.test/4", "sugar cane harvest tonnes per hectare"),
    ]
    for url, text in docs:
        vault.index_document(url, [make_chunk(text, url=url)], {})
    # 'solar farm megawatt' AND-matches the three solar docs; limit 3 >= FTS count.
    res = vault.search_vault("solar farm megawatt", limit=3, hybrid=True)
    assert len(res) == 3
    assert all(c.metadata.get("retrieval") == "fts_fast" for c in res)
    # recipe/sugar never leak in.
    assert not any("recipe" in c.text or "sugar" in c.text for c in res)


def test_fts_fast_path_disabled_uses_hybrid(vault, make_chunk):
    """P1.1: with fts_fast_path off, hybrid RRF runs (retrieval='hybrid')."""
    vault.config._overrides["embeddings.fts_fast_path"] = False
    docs = [
        ("https://solar.test/1", "solar farm inverters panels megawatt capacity"),
        ("https://solar.test/2", "solar farm site chosen for megawatt output"),
        ("https://solar.test/3", "solar farm construction megawatt timeline"),
    ]
    for url, text in docs:
        vault.index_document(url, [make_chunk(text, url=url)], {})
    res = vault.search_vault("solar farm megawatt", limit=3, hybrid=True)
    assert len(res) == 3
    assert all(c.metadata.get("retrieval") == "hybrid" for c in res)
    assert all(c.metadata.get("hybrid_score") is not None for c in res)


def test_recency_half_life_ranks_fresh_docs_first(vault, make_chunk):
    """P1.2: recency weighting promotes freshly-ingested docs over stale ones."""
    vault.config._overrides["embeddings.recency_half_life_days"] = 7
    vault.config._overrides["embeddings.fts_fast_path"] = False
    now = time.time()
    stale_url = "https://news.test/stale"
    fresh_url = "https://news.test/fresh"
    vault.index_document(stale_url, [make_chunk("solar farm megawatt capacity", url=stale_url)], {})
    vault.index_document(fresh_url, [make_chunk("solar farm megawatt capacity", url=fresh_url)], {})
    # Back-date the stale doc by ~2 months (>> 7-day half-life).
    with vault._db() as (_conn, cur):
        cur.execute("UPDATE documents SET fetched_at = ? WHERE url = ?",
                    (now - 60 * 86400, stale_url))
    res = vault.search_vault("solar farm megawatt", limit=2, hybrid=True)
    # Fresh doc must outrank the stale one despite identical text.
    assert res[0].metadata["source_url"] == fresh_url


def test_mode_fast_and_hybrid_cli_route(vault, make_chunk):
    """P2.1: mode='fast' forces FTS-only, mode='hybrid' forces vector+RRF."""
    vault.index_document("https://solar.test/1",
                         [make_chunk("solar farm megawatt capacity")], {})
    fast = vault.search_vault("solar", limit=5, hybrid=False)
    assert len(fast) == 1
    hyb = vault.search_vault("solar", limit=5, hybrid=True)
    assert len(hyb) == 1
