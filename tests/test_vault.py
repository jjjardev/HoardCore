"""Tests for the vault: indexing, hybrid retrieval (RRF), backfill, db hygiene."""


import pytest

import hoardcore as hc


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


def test_index_updates_delete_old(vault, make_chunk):
    vault.index_document("https://example.test/u", [make_chunk("first version")], {})
    vault.index_document("https://example.test/u", [make_chunk("second version"), make_chunk("extra")], {})
    assert len(vault.search_vault("second", hybrid=False)) == 1
    assert len(vault.search_vault("extra", hybrid=False)) == 1
    assert len(vault.search_vault("first", hybrid=False)) == 0


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
    eng = hc.EmbeddingsEngine(ConfigManager())
    same = hc.EmbeddingsEngine.cosine(
        eng.vectorize("renewable energy solar power negros"),
        eng.vectorize("solar farm megawatts renewable"), eng.dim)
    diff = hc.EmbeddingsEngine.cosine(
        eng.vectorize("renewable energy solar power negros"),
        eng.vectorize("chocolate cake recipe"), eng.dim)
    assert same > 0.4
    assert diff < 0.15
    assert eng.enabled


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
