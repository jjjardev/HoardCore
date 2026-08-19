"""Tests for cross-vault read (`--vault a,b,c`).

Covers the fusion/dedup semantics of `_search_across_vaults`, the verify/
verify-hint folds across named vaults, the audit INGESTED check across vaults,
and backward compatibility of a single vault name.
"""

import asyncio
import os

import hoardcore as hc
from tests.conftest import TempConfig


def _scraper(tmp_path, monkeypatch, names=("va", "vb")):
    """HoardCore whose vaults are isolated under tmp_path."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    return hc.HoardCore(list(names))


def test_multiple_names_build_primary_and_companions(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, monkeypatch)
    assert scraper.vault_name == "va"
    assert scraper.vault.vault_name == "va"
    assert len(scraper.vaults) == 2
    assert [v.vault_name for v in scraper.vaults] == ["va", "vb"]
    # Distinct top-level roots: primary at <root>/va, companion at <root>/vb.
    assert os.path.basename(scraper.vaults[0].root_dir) == "va"
    assert os.path.basename(scraper.vaults[1].root_dir) == "vb"


def test_single_name_backward_compat(tmp_path, monkeypatch):
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore("solo")
    assert scraper.vault_name == "solo"
    assert scraper.vault.vault_name == "solo"
    assert len(scraper.vaults) == 1
    assert scraper.vaults[0] is scraper.vault


def test_comma_list_string_splits_names(tmp_path, monkeypatch):
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore("career, negros_ai_jobs")
    assert len(scraper.vaults) == 2
    assert [v.vault_name for v in scraper.vaults] == ["career", "negros_ai_jobs"]


def test_search_fuses_across_vaults_and_dedups(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, monkeypatch)
    primary, companion = scraper.vaults
    # One chunk unique to the primary, one unique to the companion, and one
    # with identical text in BOTH vaults.
    primary.index_document(
        "https://mv.test/a",
        [hc.Chunk(text="a supernova explosion reshaped the nebula",
                  metadata={"header_path": "A", "source": "https://mv.test/a"})],
        {})
    companion.index_document(
        "https://mv.test/b",
        [hc.Chunk(text="a magneto storm rattled the instruments",
                  metadata={"header_path": "B", "source": "https://mv.test/b"})],
        {})
    shared = "the shadow echo pulsed across every vault"
    for vault in (primary, companion):
        vault.index_document(
            "https://mv.test/shared",
            [hc.Chunk(text=shared, metadata={"header_path": "S", "source": "https://mv.test/shared"})],
            {})

    # An identical chunk in BOTH vaults dedups to a single hit while scoring
    # once per vault.
    res = scraper._search_across_vaults("shadow echo pulsed", limit=8, hybrid=False)
    assert [c.text for c in res] == [shared]
    assert all("vault" in c.metadata for c in res)
    # Each vault's unique chunk is still discoverable through the fold.
    got_a = scraper._search_across_vaults("supernova", limit=8, hybrid=False)
    got_b = scraper._search_across_vaults("magneto storm", limit=8, hybrid=False)
    assert [c.text for c in got_a] == ["a supernova explosion reshaped the nebula"]
    assert [c.text for c in got_b] == ["a magneto storm rattled the instruments"]


def test_search_single_vault_short_circuits(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, monkeypatch, names=("va",))
    scraper.vault.index_document(
        "https://mv.test/solo",
        [hc.Chunk(text="lonely vault solo treasure",
                  metadata={"header_path": "L", "source": "https://mv.test/solo"})],
        {})
    res = scraper._search_across_vaults("lonely solo treasure", limit=5, hybrid=False)
    assert [c.text for c in res] == ["lonely vault solo treasure"]


def test_verify_claim_folds_across_vaults(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, monkeypatch)
    companion = scraper.vaults[1]
    companion.index_document(
        "https://mv.test/vonly",
        [hc.Chunk(text="the marmoset population tripled after the monsoon",
                  metadata={"header_path": "V", "source": "https://mv.test/vonly"})],
        {})
    # Claim that exists verbatim ONLY in the secondary vault still verifies.
    assert scraper.verify_claim("the marmoset population tripled after the monsoon") == "verified"
    # Gibberish nowhere in any vault stays UNVERIFIED.
    assert scraper.verify_claim("quantum banana zillion marketplace") == "unverified"


def test_verify_claim_partial_folds(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, monkeypatch)
    primary = scraper.vaults[0]
    primary.index_document(
        "https://mv.test/p1",
        [hc.Chunk(text="farm solar megawatt capacity grew this season",
                  metadata={"header_path": "P", "source": "https://mv.test/p1"})],
        {})
    # All terms present but not verbatim-contiguous -> PARTIAL (single-vault
    # semantics preserved through the fold).
    assert scraper.verify_claim("solar farm megawatt") == "partial"


def test_verify_hint_folds_across_vaults(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path, monkeypatch)
    companion = scraper.vaults[1]
    phrase = "the walrus colony harvests kelp in deep winter"
    companion.index_document(
        "https://mv.test/hint",
        [hc.Chunk(text=phrase,
                  metadata={"header_path": "H", "source": "https://mv.test/hint"})],
        {})
    hint = scraper.verify_hint("walrus harvest kelp during winter", recall=5)
    assert hint is not None
    assert "walrus" in hint and "kelp" in hint


def test_audit_ingested_any_vault(tmp_path, monkeypatch):
    """A [V#N] whose Source Link URL has chunks only in a secondary vault must
    still pass the INGESTED link of the audit chain."""
    scraper = _scraper(tmp_path, monkeypatch)
    url = "https://mv.test/companion-only"
    quote = "the auk colony numbers quadrupled in a single breeding season"
    scraper.vaults[1].index_document(
        url,
        [hc.Chunk(text=f"Study found {quote}",
                  metadata={"header_path": "Auk", "source": url})],
        {})
    artifact = os.path.join(str(tmp_path), "audit.md")
    with open(artifact, "w", encoding="utf-8") as fh:
        fh.write(f'The report says "{quote}" [V#1].\n\n'
                 "## Source Links / Citations\n\n"
                 f"[#1] {url} — {url}\n")
    out = scraper.audit_artifact(artifact)
    assert out["claims"][0]["verdict"] == "verified"
    assert out["unmapped"] == []
    assert out["not_ingested"] == []
    assert out["accuracy"] == 1.0


def test_fetch_search_action_routes_through_fusion(tmp_path, monkeypatch):
    """The search action must go through `_search_across_vaults` so companion
    vaults contribute recall."""
    scraper = _scraper(tmp_path, monkeypatch)
    scraper.vaults[1].index_document(
        "https://mv.test/only-second",
        [hc.Chunk(text="only the second vault knows about quark gluon plasmas",
                  metadata={"header_path": "Q", "source": "https://mv.test/only-second"})],
        {})
    res = asyncio.run(scraper.fetch("https://mv.test/only-second", action="search",
                                    query="quark gluon plasma", max_results=5))
    texts = [c["text"] for c in res]
    assert any("quark gluon" in t for t in texts)
