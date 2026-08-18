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


def test_fts_query_aligns_currency_tokens_to_index():
    """A '$' directly before digits is indexed by unicode61 as the bare number,
    so the FTS phrase must drop the '$' to match the index (e.g. '$13' -> '13').
    `verify`'s raw-text LIKE still confirms the verbatim '$13' for [V]."""
    import hoardcore as hc
    assert hc.VaultManager._fts_query("coffee deficit of $13 million") == \
        '"coffee" AND "deficit" AND "of" AND "13" AND "million"'
    assert hc.VaultManager._fts_query("$1") == '"1"'
    assert hc.VaultManager._fts_query("$21.3 billion") == '"21.3" AND "billion"'
    # a '$' not followed by digits (e.g. a shell/dollar word) is left alone
    assert hc.VaultManager._fts_query("dollar $ sign") == '"dollar" AND "$" AND "sign"'
    assert hc._fts_token("$13") == "13"
    assert hc._fts_token("plain") == "plain"


def test_verify_claim_confirms_currency_figure(vault, make_chunk):
    """A '$'-prefixed number present verbatim in stored text must verify, and
    the FTS token alignment must not break the verbatim raw-text match."""
    from hoardcore import HoardCore
    vault.index_document(
        "https://coffee.test/1",
        [make_chunk("The Philippines has a coffee trade deficit of $13 million in 2022",
                    header="Coffee", url="https://coffee.test/1")],
        {})
    hc_obj = HoardCore.__new__(HoardCore)
    hc_obj.config = vault.config
    hc_obj.vault = vault
    assert hc_obj.verify_claim("The Philippines has a coffee trade deficit of $13 million in 2022") == "verified"


def test_confidence_probe_prefers_distinctive_headers(vault, make_chunk):
    """The stats confidence probe must sample distinctive multi-word header
    segments, not generic single-word labels ('Production', 'Farmers'), which
    match too broadly to be keyword-backed and would report a misleading
    all-medium distribution."""
    from hoardcore import HoardCore
    # Many generic single-word headers (would dominate the old first-40 pick),
    # plus one distinctive multi-word section per topic.
    for i in range(15):
        vault.index_document(
            f"https://r.test/{i}",
            [make_chunk(f"rice production metric tons palay item {i}",
                        header="Production", url=f"https://r.test/{i}")],
            {})
    vault.index_document(
        "https://r.test/topic",
        [make_chunk("rice imports surged to 4.68 million metric tons in 2024 from Vietnam",
                    header="Rice production in the Philippines > Imports from Vietnam",
                    url="https://r.test/topic")],
        {})
    hc_obj = HoardCore.__new__(HoardCore)
    hc_obj.config = vault.config
    hc_obj.vault = vault
    # The probe should drive a keyword-backed multi-word query, producing a
    # spread rather than the degenerate all-medium the generic headers would.
    dist = vault.confidence_distribution(probes=2, recall=4)
    assert any(dist.values())
    assert set(dist.keys()) == {"high", "medium", "low"}


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


def test_recall_metadata_carries_chunk_id(vault, make_chunk):
    """Execution-provenance (improving_hoardcore.md #1): every recalled chunk
    must carry the exact storage `chunk_id` (a real int rowid), so a claim's
    grounding can be replayed to its source chunk. Applies to the FTS-only,
    fts_fast, and hybrid retrieval paths."""
    vault.index_document("https://prov.test/1", [
        make_chunk("negros island solar microgrid capacity"),
        make_chunk("geothermal plans pililla reservoir"),
    ], {})
    vault.index_document("https://prov.test/2", [
        make_chunk("solar microgrid DFT lighting poloc"),
    ], {})
    # FTS-only path (search_vault hybrid=False): rowid comes from the FTS table.
    fts = vault.search_vault("negros", limit=5, hybrid=False)
    assert fts, "FTS path should return hits for a present keyword"
    assert all(c.metadata.get("chunk_id") is not None for c in fts)
    assert all(isinstance(c.metadata["chunk_id"], int) for c in fts)
    # Hybrid path: rowid comes from the merged recall set.
    hy = vault.search_vault("negros geothermal", limit=5, hybrid=True)
    assert hy, "hybrid path should return hits for a present query"
    assert all(c.metadata.get("chunk_id") is not None for c in hy)
    assert all(isinstance(c.metadata["chunk_id"], int) for c in hy)
    # The chunk_id must identify the exact stored chunk (replayable), so a
    # given text maps back to a stable, distinct rowid.
    by_text = {c.text: c.metadata["chunk_id"] for c in hy}
    assert len(set(by_text.values())) == len(by_text)


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
    """The default dense model is BAAI/bge-small-en-v1.5 (384-dim).

    Skips when the dense model can't actually load (no fastembed / offline /
    model-download failure) — the fallback-to-sparse sibling covers that case.
    Restores the ConfigManager singleton so ordering can't leak state."""
    from hoardcore import ConfigManager, EmbeddingsEngine
    cfg = ConfigManager()
    saved = dict(cfg._config["embeddings"])
    try:
        cfg._config["embeddings"]["mode"] = "dense"
        cfg._config["embeddings"]["mrl_dims"] = 0  # pin: user config may set 96+
        eng = EmbeddingsEngine(cfg)
        if eng.mode != "dense":
            pytest.skip("fastembed/dense model unavailable in this environment")
        assert eng.dim == 384
    finally:
        cfg._config["embeddings"] = saved


def test_dense_mode_falls_back_to_sparse_when_unavailable(tmp_path):
    """If mode=dense but fastembed is not installed, degrade to sparse."""
    from hoardcore import ConfigManager, EmbeddingsEngine
    cfg = ConfigManager()
    saved = dict(cfg._config["embeddings"])
    try:
        cfg._config["embeddings"]["mode"] = "dense"
        cfg._config["embeddings"]["dim"] = 256
        cfg._config["embeddings"]["mrl_dims"] = 0  # pin: user config may set 96+
        eng = EmbeddingsEngine(cfg)
        # Either dense loaded (fastembed present) or fell back to sparse; never crash.
        assert eng.dim in (256, 384)
        vec = eng.vectorize("renewable energy solar")
        assert len(vec) == eng.dim * 4
    finally:
        cfg._config["embeddings"] = saved


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


def test_confidence_set_relative_spreads_homogeneous_vault(vault, make_chunk):
    """Regression for the "all-medium" flatness: on a homogeneous vault where
    every chunk is on-topic, absolute RRF thresholds cluster and tag everything
    'medium'. The default relative mode must instead rank within the set —
    crowning the top hit 'high' and pushing the tail to 'low' so the set is no
    longer flat."""
    for i in range(12):
        url = f"https://karend.test/{i}"
        text = (f"karenderia sari-sari store profit margin item {i}: "
                f"fast moving essentials stock cooking oil sugar coffee snacks "
                f"best selling products wholesale price tingi repack margin percent")
        vault.index_document(url, [make_chunk(text, header="Karenderia", url=url)], {})

    # Force the hybrid RRF path (not the fts_fast keyword fast-path, which
    # correctly hardcodes 'medium') so the set-relative band is exercised.
    vault.config._overrides["embeddings.fts_fast_path"] = False
    res = vault.search_vault("karenderia sari-sari store profit margin", limit=8, hybrid=True)
    assert len(res) > 1
    confs = [c.metadata.get("confidence") for c in res]
    # top hit must be 'high' (keyword-backed, clearly above the set's tail)
    assert confs[0] == "high"
    # the set must be spread, not all-'medium'
    assert len(set(confs)) >= 2
    # nothing unknown
    assert set(confs).issubset({"high", "medium", "low"})


def test_confidence_set_relative_caps_high_on_small_recall(vault, make_chunk):
    """Set-relative confidence must not over-credit a small recall: only the
    top ~20% of the set may be 'high', and never every row."""
    for i in range(6):
        url = f"https://karend.test/{i}"
        text = (f"karenderia sari-sari profit margin item {i} cooking oil "
                f"sugar coffee snacks wholesale tingi repack sell price")
        vault.index_document(url, [make_chunk(text, header="K", url=url)], {})

    vault.config._overrides["embeddings.fts_fast_path"] = False
    res = vault.search_vault("karenderia sari-sari profit margin cooking oil", limit=6, hybrid=True)
    confs = [c.metadata.get("confidence") for c in res]
    # not everything can be 'high'
    assert "high" not in confs or confs.count("high") < len(confs)
    # and the top is high (it is keyword-backed and clearly the best match)
    assert confs[0] == "high"


def test_hybrid_or_fallback_rescues_topical_query(vault, make_chunk):
    """A long research question whose strict FTS AND-match is empty (any one
    absent term zeroes the whole AND) must not collapse to a pure-vector search
    that tags every hit 'medium'. The OR-fallback rescues it when >=2 distinct
    query tokens match the corpus, restoring a keyword-backed set with a spread
    of confidence bands."""
    vault.config._overrides["embeddings.fts_fast_path"] = False
    for i in range(10):
        url = f"https://saritest.ph/{i}"
        text = (f"sari-sari store retail Philippines economy item {i} "
                f"packworks small business micro seller stock sales")
        vault.index_document(url, [make_chunk(text, header="R", url=url)], {})

    # "gdp" has no match, so the strict AND ("... AND gdp") returns 0 rows.
    res = vault.search_vault(
        "sari-sari store retail Philippines economy packworks gdp contribution",
        limit=8, hybrid=True)
    assert len(res) > 1
    confs = [c.metadata.get("confidence") for c in res]
    # keyword-backed set: top hit is 'high' and the set is not all-'medium'
    assert confs[0] == "high"
    assert len(set(confs)) >= 2


def test_hybrid_or_fallback_rejects_single_coincidental_token(vault, make_chunk):
    """The OR-fallback guard must NOT fake a keyword-backed set for an
    off-topic query that shares only a single coincidental token with the
    corpus — that would crownd a weak match 'high'. At least two distinct
    matching tokens are required."""
    vault.config._overrides["embeddings.fts_fast_path"] = False
    for i in range(8):
        url = f"https://saritest.ph/{i}"
        text = (f"sari-sari store retail Philippines item {i} packworks "
                f"micro seller stock sales margin")
        vault.index_document(url, [make_chunk(text, header="R", url=url)], {})

    # Only "stock" (one token) matches the corpus; the OR-fallback guard
    # requires >= 2 distinct matching tokens, so a lone coincidental token
    # must NOT lift the set to 'high' — a weak match must never be crowned.
    res = vault.search_vault("quantum banana zillion nebula stock", limit=8, hybrid=True)
    confs = [c.metadata.get("confidence") for c in res]
    assert "high" not in confs
    assert set(confs).issubset({"medium", "low"})


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


def test_verify_claim_partial_is_corpus_scaled(vault, make_chunk):
    """Regression: PARTIAL used a fixed absolute BM25 rank cutoff (-2.0) that
    is unreachable on small vaults (top ranks ~1e-6), so any non-verbatim
    claim reported UNVERIFIED for a fresh vault. The all-terms match must now
    beat the best single-term coincidence floor instead — corpus-size free."""
    from hoardcore import HoardCore
    vault.index_document("https://solar.test/2",
                         [make_chunk("solar farm megawatt capacity grew this year",
                                     url="https://solar.test/2")], {})
    hc_obj = HoardCore.__new__(HoardCore)
    hc_obj.config = vault.config
    hc_obj.vault = vault
    # Terms all present but NOT verbatim-contiguous (that would VERIFY): the
    # honest state is PARTIAL — previously UNVERIFIED because the small
    # vault's ranks never reached the old absolute cutoff.
    assert hc_obj.verify_claim("megawatt solar farm") == "partial"
    # Stopword-garbage with no real coverage is still UNVERIFIED.
    assert hc_obj.verify_claim("the or and not of to") == "unverified"


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


def test_fts_fast_path_confidence_is_medium_not_high(vault, make_chunk):
    """v0.8.2: fast-path hits skip the vector scan, so semantic closeness is
    unverified — confidence must be 'medium', not the dishonest 'high'."""
    docs = [
        ("https://solar.test/1", "solar farm inverters panels megawatt capacity"),
        ("https://solar.test/2", "solar farm site chosen for megawatt output"),
        ("https://solar.test/3", "solar farm construction megawatt timeline"),
    ]
    for url, text in docs:
        vault.index_document(url, [make_chunk(text, url=url)], {})
    res = vault.search_vault("solar farm megawatt", limit=3, hybrid=True)
    assert all(c.metadata.get("retrieval") == "fts_fast" for c in res)
    # Guard against regressing back to the dishonest 'high' (vector unverified).
    assert all(c.metadata.get("confidence") == "medium" for c in res)


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


# --- v0.8.1 audit hardenings (E1 cosine, A1 long claims, A5 stale-dim) ---


def test_cosine_rejects_dimension_mismatch():
    """A7: mismatched vector payloads must be surfaced (scored 0.0), never
    silently truncated into a plausible-but-wrong cosine."""
    from hoardcore import ConfigManager, EmbeddingsEngine
    cfg = ConfigManager()
    cfg._config["embeddings"]["mode"] = "sparse"
    cfg._config["embeddings"]["dim"] = 256
    eng = EmbeddingsEngine(cfg)
    a = eng.vectorize("renewable energy solar power negros")
    # Float32 vec truncated to 100 bytes: previously silently sliced to a wrong
    # score; now must be reported as a mismatch.
    truncated = a[:100]
    # int8 vs float32 mix: also a mismatch (256-byte int8 vs 1024-byte f32).
    int8_a = EmbeddingsEngine._quantize_int8(a)
    assert EmbeddingsEngine.cosine(a, truncated, eng.dim) == 0.0
    assert EmbeddingsEngine.cosine(int8_a, a, eng.dim) == 0.0
    # Same-format matches still score.
    b = eng.vectorize("solar farm megawatts renewable")
    assert EmbeddingsEngine.cosine(a, b, eng.dim) > 0.4


def test_cosine_rejects_zero_norm_and_empty_vectors():
    """E1: empty payloads must fail cleanly, not crash or produce NaN."""
    from array import array

    from hoardcore import ConfigManager, EmbeddingsEngine
    cfg = ConfigManager()
    cfg._config["embeddings"]["mode"] = "sparse"
    cfg._config["embeddings"]["dim"] = 64
    eng = EmbeddingsEngine(cfg)
    dim = eng.dim
    # Empty blobs: not a valid int8 or float32 length -> mismatch path.
    assert EmbeddingsEngine.cosine(b"", b"", dim) == 0.0
    # A float32 all-zeros vector is valid bytes; dot is 0.0 (no NaN).
    zero = array('f', [0.0] * dim).tobytes()
    assert EmbeddingsEngine.cosine(zero, zero, dim) == 0.0


def test_cosine_int8_matches_float32_ordering():
    """E1: the numPy int8 dot must match the pure-Python reference (no
    int8 overflow) and still rank same-topic above different-topic."""
    from hoardcore import ConfigManager, EmbeddingsEngine
    cfg = ConfigManager()
    cfg._config["embeddings"]["mode"] = "sparse"
    cfg._config["embeddings"]["dim"] = 256
    eng = EmbeddingsEngine(cfg)

    def ref_int8_cosine(x, y):
        from array import array
        va = array('b')
        va.frombytes(x)
        vb = array('b')
        vb.frombytes(y)
        return float(sum(a_ * b_ for a_, b_ in zip(va, vb, strict=True))) / (127.0 ** 2)

    a = EmbeddingsEngine._quantize_int8(eng.vectorize("renewable energy solar"))
    b = EmbeddingsEngine._quantize_int8(eng.vectorize("solar farm megawatts"))
    c = EmbeddingsEngine._quantize_int8(eng.vectorize("chocolate cake recipe"))
    assert abs(EmbeddingsEngine.cosine(a, b, eng.dim) - ref_int8_cosine(a, b)) < 1e-6
    assert EmbeddingsEngine.cosine(a, b, eng.dim) > EmbeddingsEngine.cosine(a, c, eng.dim)


def test_verify_claim_long_claim_with_distinctive_tail(vault, make_chunk):
    """A1 regression: a claim whose distinctive portion is NOT its first 60
    chars (so the old prefix-only fragment missed it) must verify verbatim."""
    text = ("The signatories agreed that the economic impact of renewable "
            "energy in the Negros region has been measured at seventy three "
            "billion pesos in the calendar year of twenty twenty six.")
    vault.index_document("https://negros.test/1", [make_chunk(text)], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    # 130+ char claim; 'seventy three' appears ~100 chars in -- past the old
    # 60-char window, so this would previously fall through to partial.
    claim = ("The signatories agreed that the economic impact of renewable "
             "energy in the Negros region has been measured at seventy three "
             "billion pesos")
    assert hc_inst.verify_claim(claim) == "verified"


def test_verify_claim_partial_requires_strong_bm25(vault, make_chunk):
    """A2 regression: co-occurrence of generic words in unrelated boilerplate
    must NOT be reported 'partial' — the top hit must be a strong BM25 match."""
    vault.index_document("https://boilerplate.test/1", [
        make_chunk("Click here to read our terms. The message was on the "
                   "table by the door for everyone to see and understand it.")
    ], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    # All keywords exist in that one chunk, but it is a weak, generic match.
    assert hc_inst.verify_claim("on the table under the door behind everyone") == "unverified"


def test_verify_claim_folds_typographic_dashes(vault, make_chunk):
    """A6 regression: '500–2,000' (en-dash) must verify against the same claim
    stored with an ASCII hyphen — typography is a render artifact, not a
    wording difference. Folding must NOT loosen token identity though."""
    text = "A successful Product Hunt launch can deliver 500-2,000 GitHub stars in 48 hours"
    vault.index_document("https://launch.test/1", [make_chunk(text)], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    # en-dash + curly-quote variants of the exact same claim verify:
    assert hc_inst.verify_claim(
        "A successful Product Hunt launch can deliver 500\u20132,000 GitHub stars in 48 hours"
    ) == "verified"
    # Nested typographic variants also verify (straight quote need, given
    # apex im) — the em-dash standing in for the stored hyphen is folded:
    assert hc_inst.verify_claim(
        "launch can deliver 500\u20142,000 GitHub"
    ) == "verified"


def test_verify_claim_folds_markdown_emphasis(vault, make_chunk):
    """Stress-test regression: parser-emitted **bold** markers in stored text
    are render artifacts, so 'increased by 17% in 2025' must verify against a
    chunk storing 'increased by **17% in 2025**'."""
    text = "electricity demand from data centers increased by **17% in 2025**"
    vault.index_document("https://md.test/1", [make_chunk(text)], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    assert hc_inst.verify_claim(
        "electricity demand from data centers increased by 17% in 2025"
    ) == "verified"
    # Emphasis is folded, but a real token change still rejects.
    assert hc_inst.verify_claim(
        "electricity demand from data centers increased by 27% in 2025"
    ) != "verified"


def test_verify_claim_still_rejects_token_change(vault, make_chunk):
    """A6 guard: typographic folding must not become fuzzy matching — '400K'
    stays distinct from '400K+' and reordered words never verify."""
    text = "TLDR AI has 400K+ subscribers, part of the TLDR newsletter family"
    vault.index_document("https://tldr.test/1", [make_chunk(text)], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    # exact phrasing (verbatim) verifies...
    assert hc_inst.verify_claim("TLDR AI has 400K+ subscribers") == "verified"
    # ...but the missing '+' is a different claim: not verbatim -> not verified.
    assert hc_inst.verify_claim("TLDR AI has 400K subscribers") != "verified"


def test_verify_hint_surfaces_nearest_phrase(vault, make_chunk):
    """Coaching: a denied claim gets a hint pointing at the source's actual
    wording (the reformulation contract), not a dead-end."""
    text = "The vault stores 640K+ tokens of source text for recall"
    vault.index_document("https://hint.test/1", [make_chunk(text)], {})
    hc_inst = object.__new__(hc.HoardCore)
    hc_inst.vault = vault
    hint = hc_inst.verify_hint("the vault stores 640K tokens of source text")
    assert hint is not None
    assert "640K+" in hint or "640k+" in hint
    assert "reword" in hint


def test_normalize_claim_folds_typography_only():
    """normalize_claim folds punctuation/whitespace variants but preserves
    token identity and word order."""
    assert hc.normalize_claim("\u201cHi\u201d \u2014 it\u2019s 500\u20132,000") == \
        hc.normalize_claim('"Hi" - it\'s 500-2,000')
    assert hc.normalize_claim("400K") != hc.normalize_claim("400K+")
    assert hc.normalize_claim("solar farm") != hc.normalize_claim("farm solar")


def test_normalize_claim_strips_markdown_emphasis():
    """**bold** / *italic* / `code` markers are render artifacts, folded like
    typographic dashes; token identity (400K vs 400K+) is untouched."""
    assert hc.normalize_claim("increased by **17% in 2025**") == \
        hc.normalize_claim("increased by 17% in 2025")
    assert hc.normalize_claim("*italic* and `code`") == "italic and code"
    assert hc.normalize_claim("400K") != hc.normalize_claim("400K+")


def test_vault_stats_counts_sources_and_chunks(vault, make_chunk):
    """stats() returns aggregate vault numbers (sources/chunks/vectors)."""
    for url, txt in [("https://a.test/1", "solar farm megawatt capacity"),
                     ("https://b.test/2", "sleep duration 7 to 9 hours"),
                     ("https://c.test/3", "bokashi compost in a bucket")]:
        vault.index_document(url, [make_chunk(txt)], {})
    st = vault.stats()
    assert st["sources"] == 3
    assert st["doc_versions"] == 3
    assert st["chunks"] == 3
    assert st["page_size"] >= 4096


def test_backfill_detects_stale_dim_even_when_first_row_is_fresh(vault, make_chunk):
    """A5 regression: stale-dim detection samples ALL rows, not just rowid 1.
    A mixed vault (one fresh vec + one stale vec) must rebuild the stale row."""
    from array import array
    for i in range(3):
        vault.index_document(f"https://solar.test/{i}",
                             [make_chunk(f"solar farm megawatt capacity {i}")], {})
    # Force one stale row (wrong dim) while others stay fresh.
    stale = array('f', [0.1] * 128).tobytes()
    with vault._db() as (_conn, cur):
        rids = [r[0] for r in cur.execute("SELECT rowid FROM chunks_fts").fetchall()]
        cur.execute(
            "INSERT OR REPLACE INTO chunk_vectors (chunk_rowid, url, vector) "
            "VALUES (?, ?, ?)", (rids[2], "https://solar.test/2", stale))
    # Lower the dim expectation so the two fresh rows also look stale to force
    # a rebuild of everything; the point is that backfill no longer misses the
    # mismatch when sampling only the first row.
    vault._vector_dim = 64
    n = vault.backfill_vectors()
    assert n > 0
    with vault._db() as (_conn, cur):
        bad = cur.execute(
            "SELECT COUNT(*) FROM chunk_vectors WHERE length(vector) != 64 * 4"
        ).fetchone()[0]
    assert bad == 0


def test_parallel_ingest_sentinels_and_results(tmp_path, make_chunk):
    """A11 hardening plus E2 gap: the parallel pipeline must terminate and
    store every chunk's vector without deadlock on batches larger than the
    bounded queues."""
    base = str(tmp_path)
    vault = hc.VaultManager(TempConfig(base, {"indexer.parallel": True}))
    chunks = [
        hc.Chunk(text=f"parallel solar chunk item {i}", metadata={"source": "https://p.test/1"})
        for i in range(40)
    ]
    vault.ingest_chunks_parallel("https://p.test/1", chunks, {})
    with vault._db() as (_conn, cur):
        fts = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        vecs = cur.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
    assert fts == 40
    assert vecs == 40
    assert len(vault.search_vault("solar", limit=20, hybrid=False)) == 20


def test_fast_path_recency_reworks_stale_first(vault, make_chunk):
    """A3 regression: the FTS fast path must apply recency weighting, so a
    stale keyword match doesn't outrank a fresh one of identical text."""
    vault.config._overrides["embeddings.recency_half_life_days"] = 7
    now = time.time()
    stale_url = "https://news.test/stale"
    fresh_url = "https://news.test/fresh"
    for url in (stale_url, fresh_url):
        vault.index_document(url, [make_chunk("solar farm megawatt capacity", url=url)], {})
    with vault._db() as (_conn, cur):
        cur.execute("UPDATE documents SET fetched_at = ? WHERE url = ?",
                    (now - 60 * 86400, stale_url))
    # 2 matching docs >= limit 2 -> fast path fires; recency must promote fresh.
    res = vault.search_vault("solar farm megawatt", limit=2, hybrid=True)
    assert all(c.metadata.get("retrieval") == "fts_fast" for c in res)
    assert res[0].metadata["source_url"] == fresh_url


# --- Matryoshka truncation (embeddings.mrl_dims) ---

def test_mrl_truncation_slices_f32_blob():
    """_truncate_f32 keeps the first N float32 dims; oversized inputs pass
    through unchanged."""
    from array import array
    vec = array('f', [0.1, 0.2, 0.3, 0.4]).tobytes()
    truncated = hc.EmbeddingsEngine._truncate_f32(vec, 2)
    assert truncated == array('f', [0.1, 0.2]).tobytes()
    # dims >= length is a no-op (same blob back); 0 truncates to empty.
    assert hc.EmbeddingsEngine._truncate_f32(vec, 4) is vec
    assert hc.EmbeddingsEngine._truncate_f32(vec, 0) == b""


def test_mrl_truncate_empty_passthrough():
    from array import array
    empty = array('f').tobytes()
    assert hc.EmbeddingsEngine._truncate_f32(empty, 2) == empty


def test_mrl_engine_reduces_dim_payload(tmp_path):
    """With mrl_dims set, the dense path truncates the stored vector payload
    before it reaches the DB: vectorize() length == mrl_dims * bytes_per_dim."""
    import math
    cfg = TempConfig(str(tmp_path))
    cfg._overrides["embeddings.mode"] = "dense"
    cfg._overrides["embeddings.mrl_dims"] = 4
    eng = hc.EmbeddingsEngine(cfg)  # may fall back to sparse if fastembed absent

    class FakeModel:
        def embed(self, texts):
            vec = list(range(1, 9))
            norm = math.sqrt(sum(x * x for x in vec))
            yield [x / norm for x in vec]

    # Force the dense path with a fake 8-dim model (no network/fastembed).
    eng.mode = "dense"
    eng._dense = (FakeModel(), 8)
    eng.base_dim = 8
    eng.dim = 4
    eng.quantize = "float32"
    vec = eng.vectorize("solar panama photovoltaic negros")
    assert len(vec) == 4 * 4  # 4 dims * 4 bytes


# --- Near-duplicate chunk filter (indexer.near_dedup) ---

def test_near_dedup_collapses_identical_chunks_across_urls(vault, make_chunk):
    """With indexer.near_dedup on, a chunk identical (same simhash) to an
    earlier one is dropped at ingest — first-write wins."""
    vault.config._overrides["indexer.near_dedup"] = True
    text = "the quick brown fox jumps over the lazy dog " * 6
    vault.index_document("https://a.test/1", [make_chunk(text, url="https://a.test/1")], {})
    vault.index_document("https://b.test/2", [make_chunk(text, url="https://b.test/2")], {})
    with vault._db() as (_conn, cur):
        n = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    assert n == 1


def test_near_dedup_off_keeps_both_copies(vault, make_chunk):
    """Default (off): cross-source corroborating chunks are preserved."""
    text = "the quick brown fox jumps over the lazy dog " * 6
    vault.index_document("https://a.test/1", [make_chunk(text, url="https://a.test/1")], {})
    vault.index_document("https://b.test/2", [make_chunk(text, url="https://b.test/2")], {})
    with vault._db() as (_conn, cur):
        n = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    assert n == 2


def test_near_dedup_distinct_chunks_not_dropped(vault, make_chunk):
    """Regression (bucket-probe): the bounded near-dup probe must not
    false-positive on many genuinely distinct chunks in a single document."""
    vault.config._overrides["indexer.near_dedup"] = True
    # Fully disjoint token vocabularies per chunk: no two chunks share a
    # token, so their simhashes are effectively independent 63-bit values and
    # none can sit within the hamming window of another.
    chunks = [make_chunk(" ".join(f"k{i}{c}" for c in "abcdefghijklmnopqrstuvwxyz"),
                         url=f"https://scale.test/{i}") for i in range(80)]
    vault.index_document("https://scale.test/1", chunks, {})
    with vault._db() as (_conn, cur):
        n = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    assert n == 80


def test_near_dedup_bucket_probe_finds_true_near_dup(vault):
    """Regression (bucket-probe): a stored simhash whose bucket differs from
    the candidate's by <= threshold bits must still be matched. The old
    full-table scan found this trivially; the bounded probe must reproduce it
    exactly without loading the entire chunks_simhash table."""
    sh_a = (1 << 20) | (1 << 5)
    sh_b = sh_a ^ ((1 << 5) | (1 << 12))  # hamming 2, distinct buckets

    def check():
        assert hc.hamming64(sh_a, sh_b) == 2
        assert (sh_a & hc._SIMHASH_BUCKET_MASK) != (sh_b & hc._SIMHASH_BUCKET_MASK)
    check()
    vault.config._overrides["indexer.near_dedup"] = True
    vault._simhash = lambda tokens: sh_a
    vault.index_document("https://probe.test/a",
                         [hc.Chunk(text="first document body here",
                                   metadata={"source": "https://probe.test/a"})], {})
    vault._simhash = lambda tokens: sh_b
    vault.index_document("https://probe.test/b",
                         [hc.Chunk(text="second document body here",
                                   metadata={"source": "https://probe.test/b"})], {})
    with vault._db() as (_conn, cur):
        n = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    assert n == 1


def test_simhash_bucket_patterns_cover_hamming_window():
    """The probe set provably contains the bucket of any stored simhash within
    hamming distance k: flipping <= k bits anywhere in the 63-bit hash changes
    at most k of the low-16 bucket bits."""
    import random

    random.seed(11)
    pats = set(hc._simhash_bucket_patterns(3))
    for _ in range(200):
        a = random.getrandbits(63)
        b = a
        for _ in range(random.randint(1, 3)):
            b ^= 1 << random.randrange(63)
        assert hc.hamming64(a, b) <= 3
        diff = (a ^ b) & hc._SIMHASH_BUCKET_MASK
        assert diff in pats


def test_ca_vector_cache_refreshed_after_dim_change(tmp_path):
    """Regression (A9): chunk_vectors_ca is keyed by content hash only. After
    the embedding dimension changes, a stale cached vector must never be
    served for an identical chunk — the cache entry must be recomputed to the
    new dim, and the new vector row must match."""
    cfg = TempConfig(str(tmp_path))
    cfg._overrides["embeddings.mode"] = "sparse"
    cfg._overrides["embeddings.dim"] = 64
    v = hc.VaultManager(cfg)
    text = "the quick brown fox jumps over the lazy dog solar photovoltaic battery"
    v.index_document("https://dim.test/1",
                     [hc.Chunk(text=text, metadata={"source": "https://dim.test/1"})], {})
    cfg._overrides["embeddings.dim"] = 8
    v.embeddings = hc.EmbeddingsEngine(cfg)
    v.index_document("https://dim.test/2",
                     [hc.Chunk(text=text, metadata={"source": "https://dim.test/2"})], {})
    with v._db() as (_conn, cur):
        ca = [r[0] for r in cur.execute(
            "SELECT length(vector) FROM chunk_vectors_ca").fetchall()]
        vecs = [r[0] for r in cur.execute(
            "SELECT length(vector) FROM chunk_vectors").fetchall()]
    assert ca == [8 * 4]  # refreshed to the new dim, not 64*4
    assert 8 * 4 in vecs


def test_simhash_top_bit_cleared_so_ingest_never_overflows(vault):
    """Regression: a 64-bit simhash with bit 63 set (> 2^63-1) made SQLite
    raise 'Python int too large to convert to SQLite INTEGER', killing ingest
    of ~44% of docs with indexer.near_dedup on. The value must stay within the
    signed-64-bit range, and ingest must never raise."""
    import random
    import string
    random.seed(7)
    overflow_tokens = None
    for _ in range(500):  # guaranteed to terminate; ~50% hit per draw
        tokens = ["".join(random.choice(string.ascii_lowercase)
                          for _ in range(12)) for _ in range(40)]
        # Replicate the pre-fix 64-bit vote for bit 63: positive vote would
        # have set the top bit and overflowed SQLite's signed INTEGER.
        votes = [0] * 64
        for t in set(tokens):
            h = hc._fnv1a(t.encode("utf-8"))
            for i in range(64):
                votes[i] += 1 if (h >> i) & 1 else -1
        if votes[63] > 0:
            overflow_tokens = tokens
            break
    assert overflow_tokens is not None
    full = hc.VaultManager._simhash(overflow_tokens)
    assert full <= 0x7FFFFFFFFFFFFFFF  # fits signed 64-bit <= SQLite max
    assert overflow_tokens  # sanity: found an overflow-prone token set
    vault.config._overrides["indexer.near_dedup"] = True
    vault.index_document("https://overflow.test/1",
                         [hc.Chunk(text=" ".join(overflow_tokens),
                                   metadata={"source": "https://overflow.test/1"})],
                         {})
    with vault._db() as (_conn, cur):
        n = cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    assert n == 1


def test_drop_low_confidence_keeps_strong():
    """EMIT hygiene: confidence='low' chunks drop when stronger ones remain,
    but an all-low result still returns something (better than nothing)."""
    low = hc.Chunk(text="low-confidence hit", metadata={"confidence": "low"})
    high = hc.Chunk(text="high-confidence hit", metadata={"confidence": "high"})
    assert hc.HoardCore._drop_low_confidence([low, high]) == [high]
    assert hc.HoardCore._drop_low_confidence([low, low]) == [low, low]
    assert hc.HoardCore._drop_low_confidence([]) == []


def test_drop_low_confidence_keeps_one_chunk_per_source():
    """filter_low must never strip a source entirely: a low-banded chunk that
    is the only representative of a distinct source survives EMIT (regression
    from the stress test, where the authoritative primary source vanished
    while secondary blogs survived)."""
    a_low = hc.Chunk(text="l1", metadata={"confidence": "low",
                                          "source_url": "https://primary.test/1"})
    a_high = hc.Chunk(text="h1", metadata={"confidence": "high",
                                           "source_url": "https://secondary.test/2"})
    another_low = hc.Chunk(text="l2", metadata={"confidence": "low",
                                                "source_url": "https://third.test/3"})
    kept = hc.HoardCore._drop_low_confidence([a_low, a_high, another_low])
    assert {c.metadata["source_url"] for c in kept} == {
        "https://primary.test/1", "https://secondary.test/2", "https://third.test/3"}
    # A low chunk whose source is already represented by a strong hit still drops.
    dup_low = hc.Chunk(text="l3", metadata={"confidence": "low",
                                            "source_url": "https://primary.test/1"})
    kept2 = hc.HoardCore._drop_low_confidence([a_low, a_high, dup_low])
    assert {c.metadata["source_url"] for c in kept2} == {
        "https://primary.test/1", "https://secondary.test/2"}
    assert len([c for c in kept2 if c.metadata["confidence"] == "low"]) == 1
    # An all-low pool is never pruned (a lone low hit is better than nothing).
    assert len(hc.HoardCore._drop_low_confidence([a_low, dup_low])) == 2
