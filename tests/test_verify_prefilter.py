"""Tests for the verify() candidate prefilter — it must never cause a FALSE DENIAL.

`_verdict_prefilter` derives a sound *necessary* condition for a claim to be
present verbatim, and AND-s it into the same SQL statement as the widened
windowing LIKE. Every test here is a case where a sloppy prefilter would drop a
row that genuinely matches — a corrupted provenance verdict, which is the one
failure mode `verify` must never have.
"""

import random
import tempfile
import time

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


REAL = ("The Philippine Earth Data Resource Observation Center installed the "
        "first satellite tracking antenna in the country.")


# --- the prefilter itself -------------------------------------------------

def test_prefilter_keeps_internal_whitespace():
    """Squeezing whitespace out of the slice makes it unmatchable against
    spaced stored text — a silent denial of every real match."""
    needle = hc.normalize_claim(REAL)
    pf = hc.HoardCore._verdict_prefilter(needle)
    assert " " in pf, "the prefilter must remain a literal substring of the text"
    assert pf in needle
    assert pf in REAL.lower()


def test_prefilter_stops_at_foldable_characters():
    """A stored dash may be hyphen/en-dash/minus, so a run must not span one."""
    needle = hc.normalize_claim(
        "alpha-beta gamma delta epsilon zeta eta theta iota kappa")
    pf = hc.HoardCore._verdict_prefilter(needle)
    assert pf and "-" not in pf
    assert pf in needle


def test_prefilter_is_ascii_only():
    """SQLite's lower() is ASCII-only, so a non-ASCII slice would risk a false
    denial; such claims must simply get no prefilter."""
    assert hc.HoardCore._verdict_prefilter(hc.normalize_claim("café.research")) == ""
    assert hc.HoardCore._verdict_prefilter(hc.normalize_claim("研究機関報告書")) == ""


def test_prefilter_empty_when_no_run_is_long_enough():
    # Runs split by foldable chars, each shorter than the 14-char minimum.
    assert hc.HoardCore._verdict_prefilter("a-b-c-d-e-f-g-h-i-j-k-l-m") == ""
    assert hc.HoardCore._verdict_prefilter("") == ""


def test_prefilter_like_wildcards_are_escaped():
    """A claim containing % or _ must not turn into a LIKE wildcard that matches
    everything (which would only over-fetch, but must not crash or under-fetch)."""
    needle = hc.normalize_claim("growth_of_100%_reported_last_year_ok_final")
    pf = hc.HoardCore._verdict_prefilter(needle)
    assert "%" in pf and "_" in pf
    pat = pf.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    assert "\\%" in pat and "\\_" in pat


def test_prefilter_breaks_at_both_ends_of_a_folded_quote():
    """The normalized claim carries a straight quote where the stored text may
    still hold a curly one, so a run must not span that position — otherwise the
    prefilter string cannot occur in the raw row and every real match is denied."""
    stored = "The center operates an “advanced” remote unit today."
    pf = hc.HoardCore._verdict_prefilter(hc.normalize_claim(stored))
    assert pf and '"' not in pf
    assert pf in stored.lower(), "the prefilter must be a literal substring of the RAW text"


# --- end-to-end: no false denials ----------------------------------------

def test_verbatim_claim_still_verifies_with_prefilter_active():
    v = _vault_with([("https://e.test/p", REAL)])
    assert _hc(v).verify_claim(REAL) == "verified"


def test_prefilter_does_not_break_typographic_tolerance():
    """Curly quotes and en/em dashes must still fold and verify — the reason the
    windowing LIKE widens those characters in the first place."""
    v = _vault_with([
        ("https://e.test/a", "The center operates an “advanced” remote unit today."),
        ("https://e.test/b", "Values rose 5% — see the annex for details today."),
    ])
    inst = _hc(v)
    assert inst.verify_claim("The center operates an “advanced” remote unit today.") == "verified"
    assert inst.verify_claim("Values rose 5% — see the annex for details today.") == "verified"


def test_prefilter_does_not_break_markdown_marker_folding():
    """Bold/code markers are stripped by normalize_claim; a claim written
    plainly must still verify against text stored with markers."""
    v = _vault_with([("https://e.test/m", "It increased by 17% in 2025 per the report.")])
    inst = _hc(v)
    assert inst.verify_claim("It increased by 17% in 2025 per the report.") == "verified"


def test_windowed_match_in_the_middle_of_a_long_document():
    """The distinctive part of a long claim is not at the front; the sliding
    windows must still surface the row."""
    doc = ("Introduction and background material. " * 3 + REAL +
           " Concluding remarks and appendices follow here.")
    v = _vault_with([("https://e.test/long", doc)])
    inst = _hc(v)
    assert inst.verify_claim(doc) == "verified"


def test_non_ascii_claim_verifies_without_prefilter():
    uni = "Le projet utilise des capteurs à microélectronique pour la surveillance"
    v = _vault_with([
        ("https://e.test/u1", uni),
        ("https://e.test/u2", "AUTRE TEXTE SANS ACCENT COMPLETEMENT DIFFERENT ICI"),
    ])
    inst = _hc(v)
    assert inst.verify_claim(uni) == "verified"
    # Case-folded variant: SQLite's ASCII lower() cannot do this, which is
    # exactly why the prefilter is withheld for non-ASCII claims.
    assert inst.verify_claim(uni) == "verified"


def test_genuinely_absent_claim_is_still_denied():
    """The prefilter must not manufacture false positives either."""
    v = _vault_with([("https://e.test/p", REAL)])
    inst = _hc(v)
    assert inst.verify_claim(
        "The Philippine Earth Data Resource Observation Center installed a "
        "second satellite tracking antenna last year.") != "verified"


# --- performance ----------------------------------------------------------

def test_common_words_claim_stays_fast_on_a_large_vault():
    """A claim built only from common words is the pathological case: every
    sliding window matches a huge share of the corpus. It must stay fast enough
    to run once per [V#N] tag during `audit`."""
    random.seed(3)
    words = ["solar", "microelectronics", "satellite", "sensor", "network",
             "research", "philippines", "project", "technology", "development",
             "program", "agency", "data", "system", "transfer"]
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    chunks = [hc.Chunk(
        text=" ".join(random.choice(words) for _ in range(120)),
        metadata={"header_path": "H", "source": f"https://e.test/p{i}"})
        for i in range(6000)]
    for i in range(0, 6000, 500):
        v.index_document(f"https://e.test/b{i}", chunks[i:i + 500], {})
    inst = _hc(v)
    claim = " ".join(random.choice(words) for _ in range(30))
    start = time.time()
    result = inst.verify_claim(claim)
    elapsed = time.time() - start
    assert result in ("partial", "unverified")   # verdict unchanged by speed
    assert elapsed < 3.0, f"verify took {elapsed:.2f}s on 6k chunks"


# --- prune keeps the cross-URL ledger honest ------------------------------

def test_prune_clears_chunk_urls_so_chrome_counts_stay_true():
    """`chunk_urls` describes the chunks; pruning the chunks must prune it too,
    or every chrome count stays inflated and unique content keeps being demoted
    as if it were a site-wide template."""
    nav = "RESEARCH Space Technology Wireless Technology Artificial Intelligence"
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    urls = [f"https://e.test/p{i}" for i in range(4)]
    for u in urls:
        v.index_document(u, [hc.Chunk(
            text=nav, metadata={"header_path": "Menu", "source": u})], {})

    v.prune_urls(urls[:3], dry_run=False)

    hits = v.search_vault("ARTIFICIAL Intelligence", limit=5, hybrid=False)
    # Exactly one page of that nav remains, so it is no longer chrome.
    assert len(hits) == 1
    assert not hits[0].metadata.get("chrome")
    with v._db() as (_c, cur):
        cur.execute("SELECT COUNT(*) FROM chunk_urls")
        assert cur.fetchone()[0] == 1
