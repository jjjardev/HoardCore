"""Tests for verify --hint candidate retrieval (claim-aware rescue)."""

import hoardcore as hc
from tests.conftest import TempConfig


def _make_hoardcore(tmp_path, chunks):
    hc_inst = hc.HoardCore.__new__(hc.HoardCore)
    cfg = TempConfig(str(tmp_path))
    hc_inst.config = cfg
    hc_inst.bus = hc.EventBus()
    vault = hc.VaultManager(cfg, None, event_bus=hc_inst.bus)
    for url, text in chunks:
        vault.index_document(url, [hc.Chunk(text=text, metadata={
            "header_path": "", "source": url})],
            {"quality_score": 1.0, "parser_used": "test"})
    hc_inst.vault = vault
    hc_inst.vaults = [vault]
    return hc_inst


def test_hint_or_rescue_finds_topical_chunk_when_and_fails(tmp_path):
    """Long claims with one absent token produce zero AND hits; the hint must
    still surface the topical chunk via the OR-relaxed pass instead of the
    arbitrary oldest-row fallback."""
    hc_inst = _make_hoardcore(tmp_path, [
        ("https://a.test/1", "## career opportunity:\nApply online today."),
        ("https://b.test/2",
         "Zylun Philippines, Inc. is seeking a skilled Data Engineer to "
         "build scalable data platforms for high-impact analytics projects."),
    ])
    # AND-impossible claim: contains tokens from doc B plus absent words.
    claim = ("Zylun Philippines is seeking a skilled Data Engineer to build "
             "scalable quantum blockchain platforms for orbital projects")
    hint = hc_inst.verify_hint(claim, recall=5)
    assert hint is not None
    assert "data platforms" in hint.lower()


def test_hint_still_none_on_empty_vault(tmp_path):
    hc_inst = _make_hoardcore(tmp_path, [])
    assert hc_inst.verify_hint("anything at all here", recall=5) is None


def test_hint_prefers_best_overlap_across_pool(tmp_path):
    hc_inst = _make_hoardcore(tmp_path, [
        ("https://a.test/1", "Totally unrelated filler text about boats."),
        ("https://b.test/2",
         "The economy of Negros Occidental grew by 6.9 percent in 2023 "
         "according to the provincial statistics office infographic."),
    ])
    claim = "The economy of Negros Occidental grew by 7 percent in 2023"
    hint = hc_inst.verify_hint(claim, recall=5)
    assert "6.9 percent" in hint
