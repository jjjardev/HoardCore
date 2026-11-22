"""Offline tests for the SIEGE Protocol (`--action solve`).

No network: discovery is stubbed with a canned hunt that ingests front-stamped
chunks exactly as the real pipeline would. The centerpiece is the dossier
audit-cleanliness proof — the generated Attack Dossier must pass `lint
--strict` and `audit` by construction, because a solver cannot gate a
grand-challenge answer against an evidence file that itself fails the gates.
"""

import asyncio
import json

import hoardcore as hc
from tests.conftest import TempConfig


def _scraper(tmp_path, monkeypatch, overrides=None):
    cfg = TempConfig(str(tmp_path), overrides or {})
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    return hc.HoardCore()


def _stub_hunt(scraper, front_texts: dict[str, list[str]]):
    """Replace _discover_and_ingest with a canned hunt: each front's texts are
    ingested one-URL-per-source into the primary vault, front-stamped like the
    real pipeline does via extra_meta."""
    async def fake_discover(query, max_results, strategy, force_refresh,
                            extra_meta=None):
        front = (extra_meta or {}).get("front", "?")
        texts = front_texts.get(front, [])
        ledger: dict[str, dict] = {}
        for i, text in enumerate(texts):
            url = f"https://{front.lower()}.test/source-{i}"
            # Mirror the real pipeline: the front stamp rides on the document
            # metadata AND every chunk (see _process_document).
            scraper.vault.index_document(
                url,
                [hc.Chunk(text=text, metadata={"header_path": "R",
                                               "front": front,
                                               "front_query": query})],
                {"front": front, "front_query": query})
            ledger[url] = {"status": "ingested", "chunks": 1}
        scraper.last_ingest_report = {
            "urls": ledger,
            "summary": {"ingested": len(texts), "cached": 0,
                        "skipped_junk": 0, "failed": 0},
        }
        return []
    scraper._discover_and_ingest = fake_discover  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Front decomposition
# ---------------------------------------------------------------------------

def test_expand_fronts_explicit_semicolons(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    fronts = s._expand_fronts("cure X", "mechanisms of X; barriers to X; benchmarks for X")
    assert fronts == ["mechanisms of X", "barriers to X", "benchmarks for X"]


def test_expand_fronts_default_comes_from_facets(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    fronts = s._expand_fronts("how to build fusion power", None)
    assert len(fronts) == 5  # five configured facets
    assert all(fronts[0].startswith("how to build fusion power — ") for _ in [0])
    assert any("barrier" in f for f in fronts)


# ---------------------------------------------------------------------------
# Deterministic battlefield mapping
# ---------------------------------------------------------------------------

def test_detect_barriers_flags_barrier_claims(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    chunk = hc.Chunk(
        text="Despite decades of work, there is no known method to reverse "
             "protein aggregation in vivo at scale.", metadata={})
    out = s._detect_barriers([chunk])
    assert out and out[0]["pattern"] == "no known"
    assert "no known method" in out[0]["snippet"]


def test_detect_tensions_requires_shared_vocab_and_same_unit(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    # Shared distinctive tokens (pancreatic, tumor, response) + same unit (%),
    # different values -> a tension candidate.
    a = hc.Chunk(text="The pancreatic tumor trial reported a 40% response rate "
                      "with the gemcitabine nanoliposomal regimen.",
                 metadata={})
    b = hc.Chunk(text="The pancreatic tumor trial reported a 25% response rate "
                      "under the gemcitabine nanoliposomal protocol.",
                 metadata={})
    c = hc.Chunk(text="Completely unrelated text about mycorrhizal fungi networks.",
                 metadata={})
    out = s._detect_tensions({"F1": [a], "F2": [b, c]})
    assert len(out) == 1
    assert out[0]["fronts"] == ("F1", "F2")
    assert out[0]["conflicts"][0][0] == "%"
    # The unrelated chunk must never join a tension pair.
    assert all("mycorrhizal" not in (t["chunk_a"].text + t["chunk_b"].text)
               for t in out)


def test_bridge_candidates_find_cross_front_chunks(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, {
        "solve.bridge_threshold": 0.25,
    })
    vault = s.vault
    f1 = "quantum annealing qubit coherence decoherence schedules"
    f2 = "protein folding lattice amino acid potentials"
    bridge = "quantum annealing qubit folding lattice protein potentials"
    # Mirror the real pipeline: the front stamp rides on chunk metadata too
    # (_process_document applies extra_meta to every chunk).
    vault.index_document("https://f1.test/1",
                         [hc.Chunk(text=f1, metadata={"header_path": "R",
                                                      "front": "F1"})],
                         {"front": "F1"})
    vault.index_document("https://f2.test/1",
                         [hc.Chunk(text=f2, metadata={"header_path": "R",
                                                      "front": "F2"})],
                         {"front": "F2"})
    vault.index_document("https://f1.test/2",
                         [hc.Chunk(text=bridge, metadata={"header_path": "R",
                                                          "front": "F1"})],
                         {"front": "F1"})
    out = s._bridge_candidates(["F1", "F2"], threshold=0.25, top_k=4)
    assert out, "the cross-front chunk must surface as a bridge candidate"
    top = out[0]
    assert set(top["fronts"]) == {"F1", "F2"}
    assert top["url"] == "https://f1.test/2"


# ---------------------------------------------------------------------------
# End-to-end (offline): hunt -> dossier -> gates
# ---------------------------------------------------------------------------

def _solve(tmp_path, monkeypatch, overrides=None, texts=None, passes=1):
    overrides = dict(overrides or {})
    overrides.setdefault("solve.passes", passes)
    overrides.setdefault("solve.saturation_min_sources", 2)
    overrides.setdefault("solve.bridge_threshold", 0.25)
    s = _scraper(tmp_path, monkeypatch, overrides)
    texts = texts or {
        "F1": [
            "Pancreatic tumor stroma physically blocks drug delivery; the "
            "gemcitabine nanoliposomal regimen reported a 40% response rate "
            "in the phase 3 metastatic cohort.",
            "Tumor stroma depletion in mouse models doubled intratumoral drug "
            "concentration within seventy-two hours of treatment.",
        ],
        "F2": [
            "The pancreatic tumor trial reported a 25% response rate under "
            "the gemcitabine nanoliposomal protocol in the refractory cohort.",
            "Standard of care remains FOLFIRINOX for the metastatic pancreatic "
            "tumor population with acceptable performance status.",
        ],
        "F3": [
            "There is no known method to clear desmoplastic stroma safely in "
            "humans; an open problem in oncology drug delivery.",
            "Early trials were halted; the approach remains unproven in "
            "human pancreatic tumor patients to date.",
        ],
    }
    _stub_hunt(s, texts)
    report = asyncio.run(s.solve_task(
        "How would you actually build a cure for pancreatic cancer by 2035?",
        fronts_arg="F1 stroma; F2 standard of care; F3 barriers",
        passes=passes, discover=2, recall=4, strategy="fast",
        out_path="dossier_test.md"))
    return s, report


def test_solve_end_to_end_emits_front_stamped_dossier(tmp_path, monkeypatch):
    s, report = _solve(tmp_path, monkeypatch)
    assert report["exit"] == 0  # every front got 2 sources (saturation 2)
    assert report["passes_run"] == 1
    assert report["barriers"] >= 1
    assert report["tensions"] >= 1  # 40% vs 25% across F1/F2
    path = report["dossier_path"]
    content = open(path, encoding="utf-8").read()
    assert content.startswith("# Attack Dossier —")
    assert "## Claimed barriers" in content
    assert "## Cross-front tensions" in content
    assert "## Bridge candidates" in content
    assert "## Source Links / Citations" in content
    # Front stamps reached the vault and ride on recall.
    hits = s._search_across_vaults("pancreatic tumor", limit=5, hybrid=False)
    fronts = {h.metadata.get("front") for h in hits}
    assert {"F1", "F2"} <= fronts


def test_dossier_passes_lint_and_audit_by_construction(tmp_path, monkeypatch):
    """The centerpiece: the generated Attack Dossier must clear the exact
    gates a solver's final artifact is held to — lint --strict and audit."""
    s, report = _solve(tmp_path, monkeypatch)
    path = report["dossier_path"]
    lint = s.lint_artifact(path, strict=True)
    assert lint["counts"]["error"] == 0, lint["findings"][:3]
    audit = s.audit_artifact(path)
    assert audit["total"] > 0, "the dossier must carry [V#N] evidence"
    assert audit["counts"]["unverified"] == 0, audit["claims"][:3]
    assert not audit["unmapped"], audit["unmapped"]
    assert not audit["not_ingested"], audit["not_ingested"]
    assert audit["accuracy"] == 1.0


def test_solve_exit_one_when_fronts_stay_unsaturated(tmp_path, monkeypatch):
    s, report = _solve(tmp_path, monkeypatch,
                       overrides={"solve.saturation_min_sources": 3})
    assert report["exit"] == 1  # only 2 sources/front against a saturation of 3
    content = open(report["dossier_path"], encoding="utf-8").read()
    # The budget section must say so honestly.
    assert "distinct sources: 2" in content


def test_solve_answer_first_skips_satisfied_fronts(tmp_path, monkeypatch):
    s, first = _solve(tmp_path, monkeypatch)
    # Second siege on the same vault: every front is already satisfied, so the
    # answer-first gate must skip all hunts (no new ingest ledger activity).
    hunts = {"n": 0}
    original = s._discover_and_ingest

    async def counting(*a, **k):
        hunts["n"] += 1
        return await original(*a, **k)

    s._discover_and_ingest = counting  # type: ignore[method-assign]
    second = asyncio.run(s.solve_task(
        "How would you actually build a cure for pancreatic cancer by 2035?",
        fronts_arg="F1 stroma; F2 standard of care; F3 barriers",
        passes=1, discover=2, recall=4, strategy="fast",
        out_path="dossier_second.md"))
    assert second["exit"] == 0
    assert hunts["n"] == 0


def test_solve_json_report_shape(tmp_path, monkeypatch):
    s, report = _solve(tmp_path, monkeypatch)
    payload = json.dumps(report)  # must be JSON-serializable
    assert json.loads(payload)["dossier_path"] == report["dossier_path"]


# --- SIEGE stress-test regressions ------------------------------------------

def test_dossier_source_links_follow_numeric_tag_order(tmp_path, monkeypatch):
    """Source links must stay bound to their [V#N] number past #9.

    The old emitter sorted URL strings ("[V#10]" < "[V#2]"), so dossiers with
    more than nine sources silently pointed every later tag at the wrong URL.
    """
    import re

    s = _scraper(tmp_path, monkeypatch)
    fronts = [("F1", "query one"), ("F2", "query two")]
    chunks = []
    for i in range(12):
        url = f"https://source{i}.test/doc"
        chunks.append(hc.Chunk(
            text=f"This is a complete sentence about source number {i} and its evidence.",
            metadata={"source_url": url}))
    report = {
        "passes_run": 1, "passes_budget": 2, "sources_per_front": 12,
        "fronts": {"F1": {"hunted": [1], "sources": 12},
                   "F2": {"hunted": [], "sources": 0}},
    }
    path, cite = s._write_dossier("q", fronts, {"F1": chunks, "F2": []},
                                  [], [], [], report,
                                  str(tmp_path / "dossier_test.md"))
    md = open(path, encoding="utf-8").read()
    assert all(isinstance(n, int) for n in cite.values())
    for url, n in cite.items():
        assert re.search(rf"\[#{n}\] {re.escape(url)}", md), (n, url)


def test_dossier_quote_requires_a_complete_sentence():
    q = hc.HoardCore._dossier_quote
    assert q("## Transportation & Logistics INVESTMENT") is None
    assert q("ption for firms seeking scalability without compromising talent access") is None
    sentence = "This is a complete sentence about renewable energy investment."
    assert q("Intro. " + sentence) == sentence


def test_tension_threshold_is_configurable_and_stricter_by_default(tmp_path, monkeypatch):
    a = hc.Chunk(text="alpha 10 billion units.", metadata={})
    b = hc.Chunk(text="alpha 20 billion units.", metadata={})
    strict = _scraper(tmp_path, monkeypatch, {"solve.tension_min_shared": 5})
    assert strict._detect_tensions({"F1": [a], "F2": [b]}) == []
    loose = _scraper(tmp_path, monkeypatch, {"solve.tension_min_shared": 3})
    assert loose._detect_tensions({"F1": [a], "F2": [b]})


def test_dossier_quotes_returns_multiple_complete_sentences():
    text = ("Solar power now supplies a meaningful share of the island grid. "
            "Battery storage costs have fallen sharply in the last five years. "
            "Local technicians can maintain the systems without outside help. "
            "## References and unrelated layout text")
    quotes = hc.HoardCore._dossier_quotes(text, limit=3)
    assert len(quotes) == 3
    assert all(q.endswith(".") and q[0].isupper() for q in quotes)
    assert all("#" not in q for q in quotes)
    assert any("Battery storage" in q for q in quotes)


def test_dossier_keeps_multiple_quotes_from_one_informative_chunk(tmp_path, monkeypatch):
    """Evidence yield regression: one rich chunk must not collapse to one line."""
    s = _scraper(tmp_path, monkeypatch)
    text = ("Solar power now supplies a meaningful share of the island grid and its cost keeps falling. "
            "Battery storage prices have dropped sharply in the last five years across the region. "
            "Local technicians can maintain the systems without any outside help or extra cost.")
    chunk = hc.Chunk(text=text, metadata={"source_url": "https://energy.test/case"})
    fronts = [("F1", "energy case")]
    report = {"passes_run": 1, "passes_budget": 1, "sources_per_front": 1,
              "fronts": {"F1": {"hunted": [1], "sources": 1}}}
    path, cite = s._write_dossier("q", fronts, {"F1": [chunk]}, [], [], [],
                                  report, str(tmp_path / "rich_dossier.md"))
    md = open(path, encoding="utf-8").read()
    quote_lines = [line for line in md.splitlines()
                   if line.startswith('"') and "[V#" in line]
    assert len(quote_lines) >= 3
    assert cite == {"https://energy.test/case": 1}


# --- Topical relevance gate --------------------------------------------------

def test_topic_anchors_ignore_generic_question_words(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    anchors = s._topic_anchors("How could Bacolod become dengue-free by 2035?")
    assert {"bacolod", "dengue"} <= anchors
    assert "how" not in anchors and "could" not in anchors


def test_relevance_filter_drops_offtopic_front_vocabulary(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    anchors = {"dengue", "bacolod"}
    on = hc.Chunk(text=("Dengue cases in Bacolod require clinical triage and "
                        "case management during the outbreak."), metadata={})
    off = hc.Chunk(text=("CCHF is endemic in Africa; a clinical trial of ribavirin "
                         "evaluates vaccine and case fatality outcomes."), metadata={})
    kept, stats = s._filter_front_relevant(
        [on, off], "clinical management vaccines case fatality", anchors)
    assert on in kept
    assert off not in kept
    assert stats == {"raw": 2, "kept": 1, "dropped": 1,
                     "fallback": False, "open": False,
                     "entities_required": False}


def test_relevance_fallback_keeps_best_weak_match(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, {"solve.front_overlap_min": 3})
    weak = hc.Chunk(text="A dengue report was published.", metadata={})
    kept, stats = s._filter_front_relevant(
        [weak], "clinical vaccines case fatality", {"dengue"})
    assert kept == [weak]
    assert stats["fallback"] is True
    assert stats["kept"] == 1


def test_relevance_never_resurrects_offtopic_chunk(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    off = hc.Chunk(text="CCHF vaccine trial case fatality outcomes.", metadata={})
    kept, stats = s._filter_front_relevant(
        [off], "clinical vaccines case fatality", {"dengue"})
    assert kept == []
    assert stats["open"] is True
    assert stats["fallback"] is False


# --- Entity-specific relevance ----------------------------------------------

def test_entity_anchors_extract_place_not_generic_words(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    ents = s._entity_anchors("How could Bacolod City eliminate Grade 3 failure in the Philippines?")
    assert {"bacolod", "philippines"} <= ents
    assert "city" not in ents and "grade" not in ents and "how" not in ents


def test_entity_gate_drops_generic_topic_only_evidence(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    local = hc.Chunk(text="Bacolod reading intervention improved Grade 3 fluency.",
                     metadata={})
    generic = hc.Chunk(text="Reading instruction in Grade 3 schools improves fluency.",
                       metadata={})
    kept, stats = s._filter_front_relevant(
        [local, generic], "early literacy reading fluency",
        {"reading", "fluency"}, {"bacolod"})
    assert local in kept
    assert generic not in kept
    assert stats["entities_required"] is True
    assert stats["kept"] == 1 and stats["dropped"] == 1


def test_entity_gate_override_from_config(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, {"solve.required_anchors": "DepEd,Philippines"})
    ents = s._entity_anchors("Any question at all")
    assert ents == {"deped", "philippines"}
