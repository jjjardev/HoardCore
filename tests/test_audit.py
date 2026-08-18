"""Tests for the audit action -- the execution-provenance gate.

Covers the three-link evidence chain for every [V#N] tag in an artifact:
VERBATIM (claim verifies against the vault), MAPPED (N -> Source Link),
INGESTED (cited URL has vault chunks), plus the dedupe rule and bare-[V]
(non-mapping) path.
"""

import os

import pytest

import hoardcore as hc
from tests.conftest import TempConfig


def _make_artifact(tmp_path, name: str = "artifact.md", body: str = "") -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _build_scraper(tmp_path, monkeypatch):
    """A HoardCore whose config is isolated into tmp_path."""
    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    scraper = hc.HoardCore()
    return scraper


# --- fixtures with an already-ingested vault -----------------------------

_VERBATIM_CLAIM = (
    "According to Financial Models Lab, the minimum budget to launch an indoor "
    "vertical farming facility in 2026 is projected at $36 million to grow "
    "lettuce."
)
_QUOTE = "the minimum budget to launch an indoor vertical farming facility in 2026 is projected at $36 million"
_URL = "https://foodlore.test/economics-vertical-farming"


@pytest.fixture()
def scraper(tmp_path, monkeypatch):
    """HoardCore with one ingested URL whose chunk holds the verbatim claim."""
    scraper = _build_scraper(tmp_path, monkeypatch)
    scraper.vault.index_document(
        _URL,
        [hc.Chunk(text=_VERBATIM_CLAIM,
                  metadata={"header_path": "startup costs", "source": _URL})],
        {},
    )
    return scraper


def _source_links(*pairs):
    lines = ["## Source Links / Citations", ""]
    for n, url in pairs:
        lines.append(f"[#{n}] {url} — {url}")
    return "\n".join(lines)


def _quote_body(quote, n):
    """One prose line carrying a [V#N] tag whose quoted span is the claim."""
    return (f'The FoodLore report says "{quote}" [V#{n}].\n\n'
            + _source_links((n, _URL)))


# --- VERBATIM -----------------------------------------------------------------


def test_audit_verified_quote_passes_all_links(scraper, tmp_path):
    """A quoted claim that is verbatim in the vault + a Source Link + an
    ingested URL must land VERIFIED with accuracy 1.0."""
    path = _make_artifact(
        tmp_path, body=_quote_body(_QUOTE, 1))
    out = scraper.audit_artifact(path)
    assert out["total"] == 1
    c = out["claims"][0]
    assert c["verdict"] == "verified"
    assert c["quote"] is True
    assert c["n"] == "1"
    assert out["counts"] == {"verified": 1, "partial": 0, "unverified": 0}
    assert out["accuracy"] == 1.0
    assert out["unmapped"] == []
    assert out["not_ingested"] == []


def test_audit_paraphrase_is_unverified(scraper, tmp_path):
    """Paraphrased prose (not verbatim in the vault) must be UNVERIFIED even
    though it maps to a real source."""
    paraphrase = "starting a vertical farm next year reportedly needs tens of millions"
    path = _make_artifact(
        tmp_path, body=_quote_body(paraphrase, 1))
    out = scraper.audit_artifact(path)
    assert out["claims"][0]["verdict"] == "unverified"
    assert out["counts"]["unverified"] == 1
    assert out["accuracy"] == 0.0
    assert out["unmapped"] == []  # mapping still passes
    assert out["not_ingested"] == []


def test_audit_non_quoted_line_uses_whole_line(scraper, tmp_path):
    """Without a distinctive quote, the whole cleaned line is the claim; a
    line that does not even paraphrase the source stays UNVERIFIED."""
    body = ("Chopped and reworded claims like this one are not in the vault "
            f"[V#1].\n\n{_source_links((1, _URL))}")
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert out["claims"][0]["verdict"] == "unverified"


# --- MAPPED -------------------------------------------------------------------


def test_audit_unmapped_tag_reported(scraper, tmp_path):
    """A [V#N] whose N has no Source Link entry must be reported as unmapped
    and the INGESTED check skipped."""
    body = (f'The FoodLore report says "{_QUOTE}" [V#9].\n\n'
            + _source_links((1, _URL)))
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert out["unmapped"] == ["9"]
    assert out["not_ingested"] == []  # gated on unmapped==[]
    assert out["claims"][0]["verdict"] == "verified"  # verbatim still passes


def test_audit_bare_v_not_mapping_checked(scraper, tmp_path):
    """A bare [V] (no N) is verified but never mapping-checked."""
    body = f'The FoodLore report says "{_QUOTE}" [V].\n\n{_source_links((1, _URL))}'
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert len(out["claims"]) == 1
    c = out["claims"][0]
    assert c["verdict"] == "verified"
    assert c["n"] is None
    assert out["used_n"] == []
    assert out["unmapped"] == []
    assert out["not_ingested"] == []


# --- INGESTED -----------------------------------------------------------------


def test_audit_not_ingested_url_reported(scraper, tmp_path):
    """[V#N] whose Source Link URL has no chunks in the vault -> not_ingested."""
    ghost_url = "https://neveringested.test/page"
    body = (f'The FoodLore report says "{_QUOTE}" [V#2].\n\n'
            + _source_links((1, _URL), (2, ghost_url)))
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert out["used_n"] == ["2"]
    assert ("2", ghost_url) in out["not_ingested"]
    assert out["claims"][0]["verdict"] == "verified"


# --- dedupe -------------------------------------------------------------------


def test_audit_dedupes_repeated_same_tag(scraper, tmp_path):
    """Two identical [V#3] tags on one line count once."""
    body = (f'The FoodLore report says "{_QUOTE}" [V#3] and insists again '
            f'"{_QUOTE}" [V#3].\n\n{_source_links((3, _URL))}')
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert len(out["claims"]) == 1
    assert out["total"] == 1
    assert out["claims"][0]["n"] == "3"


def test_audit_keeps_distinct_tags_on_one_line(scraper, tmp_path):
    """[V#1] and [V#2] on the same line are two chain links, not one."""
    body = (f'The FoodLore report says "{_QUOTE}" [V#1]; the same page stats it '
            f'again [V#2].\n\n{_source_links((1, _URL), (2, _URL))}')
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert len(out["claims"]) == 2
    ns = {c["n"] for c in out["claims"]}
    assert ns == {"1", "2"}


def test_audit_skips_source_links_block(scraper, tmp_path):
    """Lines under the Source Links heading are never treated as claims."""
    body = (f'The FoodLore report says "{_QUOTE}" [V#1].\n\n'
            + _source_links((1, _URL)))
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert out["total"] == 1
    assert out["claims"][0]["text"] != "[#1]"


def test_audit_empty_vault_reports_not_ingested(tmp_path, monkeypatch):
    """An audit against a vault with zero chunks still verifies the claim
    (as UNVERIFIED — nothing verbatim) and flags the cited URL as not
    ingested."""
    scraper = _build_scraper(tmp_path, monkeypatch)
    body = _quote_body("capy and iguana farming economics", 1)
    out = scraper.audit_artifact(_make_artifact(tmp_path, body=body))
    assert out["total"] == 1
    assert out["claims"][0]["verdict"] == "unverified"
    assert out["unmapped"] == []
    assert ("1", _URL) in out["not_ingested"]
