"""Tests for the prune action (transport-error cleanup)."""

import hoardcore as hc
from tests.conftest import TempConfig


def _vault(tmp_path):
    cfg = TempConfig(str(tmp_path))
    return hc.VaultManager(cfg)


def test_find_and_prune_transport_error_sources(tmp_path):
    vault = _vault(tmp_path)
    good = "Real provincial statistics with substantive prose about the economy."
    bad = ("This page isn’t working\nIf the problem continues, contact the "
           "site owner.\nHTTP ERROR 429\nReload")
    for url, text in [("https://good.test/1", good),
                      ("https://bad.test/429", bad)]:
        vault.index_document(url, [hc.Chunk(text=text, metadata={
            "header_path": "", "source": url})],
            {"quality_score": 1.0, "parser_used": "test"})

    found = vault.find_transport_error_sources()
    assert found == {"https://bad.test/429": 1}

    report = vault.prune_urls(list(found), dry_run=True)
    assert report[0]["total"] >= 2 and report[0]["deleted"] == 0
    assert vault.get_chunks_for_url("https://bad.test/429")

    report = vault.prune_urls(list(found), dry_run=False)
    assert report[0]["deleted"] == report[0]["total"] > 0
    assert not vault.get_chunks_for_url("https://bad.test/429")
    # Good source untouched; documents row gone for the pruned one.
    assert vault.get_chunks_for_url("https://good.test/1")
    with vault._db() as (_c, cur):
        cur.execute("SELECT COUNT(*) FROM documents WHERE url LIKE '%bad.test%'")
        assert cur.fetchone()[0] == 0


def test_explicit_url_prune_is_targeted(tmp_path):
    vault = _vault(tmp_path)
    for i in range(2):
        vault.index_document(f"https://x.test/{i}",
                             [hc.Chunk(text=f"filler {i}", metadata={
                                 "header_path": "",
                                 "source": f"https://x.test/{i}"})],
                             {"quality_score": 1.0, "parser_used": "t"})
    report = vault.prune_urls(["https://x.test/0"], dry_run=False)
    assert report[0]["deleted"] > 0
    assert not vault.get_chunks_for_url("https://x.test/0")
    assert vault.get_chunks_for_url("https://x.test/1")
