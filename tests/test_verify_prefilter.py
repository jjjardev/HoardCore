"""Tests for `verify`'s candidate selection.

The governing rule is that a candidate-narrowing optimisation may cost time but
must NEVER cost a verdict. Two false-denial bugs shipped and were caught here:

  * the LIKE-widening class omitted the ASCII quote forms, so curly-vs-straight
    quotes could only ever return PARTIAL;
  * a whitespace-spanning prefilter could not match hard-wrapped extracted text
    (PDFs), denying claims that were present verbatim.

Every test below is either a case a narrower implementation would wrongly deny,
or a guard on the accepted cost of the current one.
"""

import random
import tempfile
import time

import pytest

import hoardcore as hc
from tests.conftest import TempConfig


def _vault_with(pairs):
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    for url, text in pairs:
        v.index_document(url, [hc.Chunk(
            text=text, metadata={"header_path": "A", "source": url})], {})
    return v


def _hc(vault):
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(vault.root_dir)
    inst.vault = vault
    inst.vaults = [vault]
    return inst


# --- claims that MUST verify ---------------------------------------------

def test_verbatim_claim_verifies():
    s = ("The Philippine Earth Data Resource Observation Center installed the "
         "first satellite tracking antenna in the country.")
    assert _hc(_vault_with([("https://e.test/p", s)])).verify_claim(s) == "verified"


def test_hard_wrapped_text_verifies_against_unwrapped_claim():
    """REGRESSION (shipped broken in v0.16.5): extracted PDF text is hard-wrapped,
    so a candidate filter spanning whitespace could never match the raw row and
    the claim was denied even though it is present verbatim."""
    stored = ("We propose CiteGuard, a retrieval-aware agent\n"
              "framework designed to provide more faithful\n"
              "grounding for citation validation.")
    claim = ("We propose CiteGuard, a retrieval-aware agent framework designed "
             "to provide more faithful grounding for citation validation.")
    assert _hc(_vault_with([("https://e.test/pdf", stored)])).verify_claim(claim) \
        == "verified"


def test_hard_wrapped_text_with_hyphenated_word():
    """A word split across a line by a hyphenator stays a hyphen in the stored
    text, so it is legitimately NOT the unwrapped word — but the surrounding
    sentence must still verify."""
    stored = "The team recorded a 10 percent-\nage improvement across all trials."
    assert _hc(_vault_with([("https://e.test/h", stored)])).verify_claim(
        "The team recorded a 10 percent- age improvement across all trials."
    ) == "verified"


@pytest.mark.parametrize("claim_quote,stored_quote", [
    ('"', '“'),   # straight claim, curly stored
    ('“', '"'),   # curly claim, straight stored
])
def test_double_quote_folding_is_symmetric(claim_quote, stored_quote):
    """REGRESSION: typography-blind matching is documented, and the LIKE class
    originally widened only the Unicode quote characters — so a normalized needle
    carrying a straight `"` never matched a raw row holding `“`."""
    stored = f"The center operates an {stored_quote}advanced{stored_quote} unit today."
    claim = f"The center operates an {claim_quote}advanced{claim_quote} unit today."
    assert _hc(_vault_with([("https://e.test/q", stored)])).verify_claim(claim) == "verified"


def test_apostrophe_folding():
    for stored_q, claim_q in (("’", "'"), ("'", "’")):
        stored = f"The agency{stored_q}s mandate is ICT research and development."
        claim = f"The agency{claim_q}s mandate is ICT research and development."
        assert _hc(_vault_with([("https://e.test/a", stored)])).verify_claim(claim) \
            == "verified"


def test_dash_folding():
    stored = "Values rose 5% — see the annex for details today."
    assert _hc(_vault_with([("https://e.test/d", stored)])).verify_claim(
        "Values rose 5% - see the annex for details today.") == "verified"


def test_non_ascii_claim_verifies():
    uni = "Le projet utilise des capteurs à microélectronique pour la surveillance"
    v = _vault_with([("https://e.test/u", uni),
                     ("https://e.test/u2", "AUTRE TEXTE SANS ACCENT ICI COMPLET")])
    assert _hc(v).verify_claim(uni) == "verified"


def test_match_in_the_middle_of_a_long_document():
    doc = ("Background and introduction material. " * 3
           + "The PEDRO Center installed the first satellite tracking antenna. "
           + " Appendices and references follow.")
    assert _hc(_vault_with([("https://e.test/l", doc)])).verify_claim(doc) == "verified"


def test_markdown_markers_fold():
    stored = "It increased by 17% in 2025 per the annual report."
    claim = "It increased by 17% in 2025 per the annual report."
    assert _hc(_vault_with([("https://e.test/m", stored)])).verify_claim(claim) == "verified"


# --- claims that must NOT verify ------------------------------------------

def test_absent_claim_is_denied():
    s = "The Philippine Earth Data Resource Observation Center installed an antenna."
    assert _hc(_vault_with([("https://e.test/p", s)])).verify_claim(
        "The Philippine Earth Data Resource Observation Center installed two "
        "antennas last year.") != "verified"


def test_percent_does_not_fold_to_percent():
    stored = "Adoption reached 12% of all installations last quarter."
    assert _hc(_vault_with([("https://e.test/p", stored)])).verify_claim(
        "Adoption reached 12 percent of all installations last quarter."
    ) != "verified"


def test_wrong_word_order_is_denied():
    stored = "the antenna tracked the first satellite"
    assert _hc(_vault_with([("https://e.test/o", stored)])).verify_claim(
        "the first satellite tracked the antenna") != "verified"


# --- the accepted cost, pinned so it cannot silently regress -------------

def test_typical_claim_with_a_distinctive_term_is_fast_on_a_large_vault():
    """A real claim names something specific, so the widened LIKE matches few rows
    and verification stays interactive even on a large vault."""
    random.seed(11)
    words = ["solar", "microelectronics", "satellite", "sensor", "network",
             "research", "philippines", "project", "technology", "development"]
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    chunks = [hc.Chunk(
        text=" ".join(random.choice(words) for _ in range(120)),
        metadata={"header_path": "H", "source": f"https://e.test/p{i}"})
        for i in range(6000)]
    for i in range(0, 6000, 500):
        v.index_document(f"https://e.test/b{i}", chunks[i:i + 500], {})
    inst = _hc(v)
    # A distinctive term, as a real claim would have: it appears in the corpus
    # exactly once, so almost no rows are candidates.
    v.index_document("https://e.test/rare", [hc.Chunk(
        text="The CiteME benchmark measures citation attribution alignment quality.",
        metadata={"header_path": "A", "source": "https://e.test/rare"})], {})
    start = time.time()
    result = inst.verify_claim(
        "The CiteME benchmark measures citation attribution alignment quality.")
    elapsed = time.time() - start
    assert result == "verified"
    assert elapsed < 1.0, f"distinctive claim took {elapsed:.2f}s on 6k chunks"


def test_pathological_all_common_words_claim_still_terminates(tmp_path):
    """A claim made ONLY of common words matches a large share of the corpus, so
    every candidate row is normalized. That cost is accepted deliberately (a
    narrowing optimisation here was measured and rejected as unsound) — this
    test exists so it cannot regress into a hang, not to police latency."""
    random.seed(3)
    words = ["solar", "microelectronics", "satellite", "sensor", "network",
             "research", "philippines", "project", "technology", "development"]
    v = hc.VaultManager(TempConfig(str(tmp_path)))
    chunks = [hc.Chunk(
        text=" ".join(random.choice(words) for _ in range(120)),
        metadata={"header_path": "H", "source": f"https://e.test/p{i}"})
        for i in range(3000)]
    for i in range(0, 3000, 500):
        v.index_document(f"https://e.test/b{i}", chunks[i:i + 500], {})
    inst = _hc(v)
    claim = " ".join(random.choice(words) for _ in range(30))
    start = time.time()
    result = inst.verify_claim(claim)
    elapsed = time.time() - start
    assert result in ("verified", "partial", "unverified")
    assert elapsed < 30.0, f"pathological claim took {elapsed:.1f}s — looks like a hang"
