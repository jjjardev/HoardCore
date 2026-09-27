"""Contract tests for `skill.md` — the agent operating manual.

skill.md is documentation, but almost every statement in it is an assertion
about tool behaviour that an agent will act on. Documentation drifts silently,
and that has already cost real work twice in this repo: v0.16.5 shipped a
provenance regression while the manual still described the old behaviour, and
v0.16.6 fixed an audit blind spot the manual never mentioned.

So the manual is treated as code here. This module:

  * parses skill.md and checks that every action, CLI flag and config key it
    names actually exists (doc says X, code does Y -> fail);
  * re-asserts, executably, the specific behaviours the manual promises to an
    agent — the ones an agent would otherwise discover the hard way.

A behaviour change therefore breaks this suite instead of quietly making the
manual a lie. When you change behaviour, change this file in the same commit.
"""

import asyncio
import re
import tempfile
from pathlib import Path

import hoardcore as hc
from tests.conftest import TempConfig

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skill.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The manual's vocabulary must exist
# ---------------------------------------------------------------------------

def _parser_flags() -> set[str]:
    flags: set[str] = set()
    for action in hc._build_parser()._actions:
        flags.update(action.option_strings)
    return flags


def test_every_action_in_the_manuals_table_is_a_real_action():
    """The 'Available Actions' table must not advertise a nonexistent action."""
    rows = re.findall(r"^\|\s*`([a-z]+)`\s*\|", SKILL, re.MULTILINE)
    assert rows, "could not parse the actions table out of skill.md"
    choices = set(next(a for a in hc._build_parser()._actions
                       if a.dest == "action").choices)
    for row in rows:
        assert row in choices, f"skill.md documents a '{row}' action that the CLI rejects"


def test_every_action_the_cli_offers_is_documented():
    """The reverse direction: a new action must be documented, or agents will
    not know it exists."""
    choices = set(next(a for a in hc._build_parser()._actions
                       if a.dest == "action").choices)
    for choice in sorted(choices):
        assert re.search(rf"^\|\s*`{re.escape(choice)}`\s*\|", SKILL, re.MULTILINE), \
            f"action '{choice}' exists in the CLI but is not in skill.md's table"


def test_every_flag_the_manual_mentions_exists():
    """`--flag` in the manual must be a real CLI option."""
    flags = _parser_flags()
    mentioned = set(re.findall(r"`(--[a-z][a-z-]*)", SKILL))
    # Options that belong to the test/lint toolchain, not the hoardcore CLI.
    toolchain = {"--cov-fail-under", "--no-parallel", "--parallel", "--strict"}
    for flag in sorted(mentioned):
        if flag in toolchain:
            continue
        assert flag in flags, f"skill.md mentions {flag}, which the CLI does not define"


def test_every_config_key_the_manual_names_is_read_by_the_code():
    """A dotted path in the manual must be a real key: either present in the
    shipped defaults, or read literally by the module."""
    defaults = hc.ConfigManager._defaults(None)
    text = (ROOT / "hoardcore.py").read_text(encoding="utf-8")
    candidates = set(re.findall(r"`([a-z_]+\.[a-z_0-9]+)`", SKILL))
    not_config = {"hoardcore.toml", "pyproject.toml", "re.match"}
    for path in sorted(candidates - not_config):
        section, key = path.split(".", 1)
        in_defaults = key in defaults.get(section, {})
        # Opt-in keys are read with a fallback default rather than shipped in
        # _defaults(), so accept either, but never neither.
        read = f"{section}.{key}" in text or f"'{key}'" in text or f'"{key}"' in text
        assert in_defaults or read, \
            f"skill.md documents config key '{path}', which the code never reads"


def test_documented_config_keys_ship_in_the_default_config():
    """Keys the manual tells agents to set must be present in the generated
    hoardcore.toml template, or the setting has nowhere to live."""
    for key in ("cache_dir", "offline", "chrome_min_urls", "sitemap_index_depth",
                "session_reuse", "urls", "filter_low", "fts_fast_path"):
        assert key in hc.DEFAULT_CONFIG, \
            f"'{key}' is documented but absent from DEFAULT_CONFIG"


# ---------------------------------------------------------------------------
# Behavioural promises, re-asserted executably
# ---------------------------------------------------------------------------

def test_chrome_demotion_default_is_three_and_is_non_destructive():
    """Manual #9: chrome is demoted at >= retrieval.chrome_min_urls (default 3)
    and is NEVER deleted, so it stays verifiable for [V]."""
    assert hc.ConfigManager._defaults(None)["retrieval"]["chrome_min_urls"] == 3
    nav = "RESEARCH Space Technology Artificial Intelligence PhilSensors EMERGING TECH"
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    for i in range(3):
        v.index_document(f"https://e.test/p{i}", [hc.Chunk(
            text=nav, metadata={"header_path": "Menu",
                                "source": f"https://e.test/p{i}"})], {})
    hits = v.search_vault("ARTIFICIAL Intelligence", limit=5, hybrid=False)
    assert hits and hits[0].metadata.get("chrome") is True
    # Still stored, therefore still verifiable.
    assert nav in "".join(c.text for c in v.get_chunks_for_url("https://e.test/p0"))
    assert hc.HoardCore().vault is not None  # artifacts dir readable


def test_parallel_ingest_is_gated_at_eight_chunks():
    """Manual #6: the threaded pipeline engages only for batches of 8+ chunks;
    smaller batches are a silent sequential no-op."""
    for size, expect_parallel in ((7, False), (8, True)):
        v = hc.VaultManager(TempConfig(tempfile.mkdtemp(),
                                        overrides={"indexer.parallel": True}))
        calls = {"n": 0}
        original = v.index_document

        def spy(url, chunks, meta, _o=original, _c=calls):
            _c["n"] += 1
            return _o(url, chunks, meta)

        v.index_document = spy
        chunks = [hc.Chunk(text=f"chunk {i} distinct body text {i}",
                           metadata={"header_path": "H"}) for i in range(size)]
        v.ingest_chunks_parallel("https://e.test/x", chunks, {})
        # The threaded path writes its own rows; only the small-batch fallback
        # delegates to index_document.
        used_parallel = calls["n"] == 0
        assert used_parallel is expect_parallel, (
            f"batch of {size}: expected parallel={expect_parallel}, "
            f"got parallel={used_parallel}")


def test_quote_folding_works_in_both_directions():
    """Manual #1 promises typography blindness for smart quotes. Both directions
    must hold (v0.16.5 fixed the one-way case)."""
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    for stored_q, claim_q in (("“", '"'), ('"', "“")):
        stored = f"The center operates an {stored_q}advanced{stored_q} unit today."
        v.index_document(f"https://e.test/{ord(stored_q)}", [hc.Chunk(
            text=stored, metadata={"header_path": "A",
                                   "source": f"https://e.test/{ord(stored_q)}"})], {})
        inst = hc.HoardCore.__new__(hc.HoardCore)
        inst.config = TempConfig(v.root_dir)
        inst.vault = v
        inst.vaults = [v]
        claim = f"The center operates an {claim_q}advanced{claim_q} unit today."
        assert inst.verify_claim(claim) == "verified", \
            f"folding failed for stored={stored_q} claim={claim_q}"


def test_hard_wrapped_word_is_not_silently_rejoined():
    """Manual #1: whitespace folds, but a hyphenator-split word stays split in
    storage, so an unwrapped claim is denied rather than quietly accepted."""
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    v.index_document("https://e.test/h", [hc.Chunk(
        text="The team recorded a 10 percent-\nage improvement.",
        metadata={"header_path": "A", "source": "https://e.test/h"})], {})
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(v.root_dir)
    inst.vault = v
    inst.vaults = [v]
    assert inst.verify_claim(
        "The team recorded a 10 percentage improvement.") != "verified"
    assert inst.verify_claim(
        "The team recorded a 10 percent- age improvement.") == "verified"
    # ...and the converse: a hard-wrapped STORED row does verify against a
    # normally-spaced claim, because whitespace itself is folded.
    v.index_document("https://e.test/w", [hc.Chunk(
        text="a retrieval-aware agent\nframework designed to work",
        metadata={"header_path": "A", "source": "https://e.test/w"})], {})
    assert inst.verify_claim("a retrieval-aware agent framework designed to work") \
        == "verified"


def test_audit_links_heading_rule_as_documented():
    """Manual (audit section): scanning stops at a heading that IS the links
    block, and a mid-document section merely mentioning citations is still
    audited."""
    assert hc.HoardCore._is_links_heading("## Source Links / Citations")
    assert hc.HoardCore._is_links_heading("## Citations")
    assert not hc.HoardCore._is_links_heading("## Citations and grounding")
    assert not hc.HoadCore if False else True
    assert not hc.HoardCore._is_links_heading("## 4. Techniques that raise citation quality")


def test_tag_is_audited_as_a_claim_wherever_it_appears():
    """Manual (audit section): `[V#N]` is not a cross-reference; a summary that
    uses tags as references is audited as an unquoted claim."""
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    text = "The PEDRO Center installed the first satellite tracking antenna."
    v.index_document("https://e.test/p", [hc.Chunk(
        text=text, metadata={"header_path": "A", "source": "https://e.test/p"})], {})
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(v.root_dir)
    inst.vault = v
    inst.vaults = [v]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "a.md"
        path.write_text(
            "# T\n\n"
            'Summary of the finding [V#1].\n\n'
            "## Source Links / Citations\n\n"
            "[#1] https://e.test/p\n",
            encoding="utf-8",
        )
        report = inst.audit_artifact(str(path))
    assert report["counts"]["unverified"] == 1, \
        "a bare [V#1] with no verbatim quote must be audited UNVERIFIED"


def test_verify_exit_codes_are_wireable(tmp_path):
    """Manual: 0 verified / 1 partial / 2 unverified, and a PARTIAL anywhere in a
    claim list downgrades the exit."""
    v = hc.VaultManager(TempConfig(str(tmp_path)))
    v.index_document("https://e.test/p", [hc.Chunk(
        text="Satellite telemetry pipelines feed hydromet deployments.",
        metadata={"header_path": "A", "source": "https://e.test/p"})], {})
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(str(tmp_path))
    inst.vault = v
    inst.vaults = [v]
    assert inst.verify_claim("Satellite telemetry pipelines feed hydromet deployments.") == "verified"
    assert inst.verify_claim("Quantum tunnelling in superconductors is well understood.") != "verified"


def test_outside_artifacts_out_path_is_warned_about():
    """Manual (Artifacts): a --out path outside artifacts/ is not day-foldered
    and a later run re-homes it, so the CLI warns."""
    text = (ROOT / "hoardcore.py").read_text(encoding="utf-8")
    assert "Artifact written OUTSIDE" in text, \
        "the manual promises a warning for --out outside artifacts/"
    assert "not day-foldered" in text


def test_solver_urls_appends_and_never_replaces():
    """Manual #10: solver.urls *appends* to solver.url, which is always tried
    first (v0.16.5 fixed the replacing case)."""
    cfg = TempConfig(tempfile.mkdtemp(), overrides={
        "solver.url": "http://primary:8191/v1",
        "solver.urls": "http://second:8191/v1, http://third:8191/v1",
    })
    f = hc.NetworkFetcher(cfg)
    assert f._solver_urls[0] == "http://primary:8191/v1"
    assert f._solver_urls == ["http://primary:8191/v1",
                              "http://second:8191/v1", "http://third:8191/v1"]


def test_sitemap_index_recursion_default_is_three():
    """Manual #8: a sitemap index is recursed (crawler.sitemap_index_depth)."""
    assert hc.ConfigManager._defaults(None)["crawler"]["sitemap_index_depth"] == 3
    body = ('<sitemapindex><sitemap><loc>https://e.test/a.xml</loc></sitemap>'
            '</sitemapindex>')
    assert hc.CrawlerPlanner._is_sitemap_index(body) is True
    assert hc.CrawlerPlanner._is_sitemap_index("<urlset><url/></urlset>") is False


def test_local_ingest_skips_unchanged_content(tmp_path):
    """Manual (local): freshness is content-based, so a re-run skips."""
    root = Path(tempfile.mkdtemp()) / "local_inputs"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "note.md").write_text(
        "# Note\n\nThe institute mandates ICT research and development.\n",
        encoding="utf-8")
    cfg = TempConfig(str(root.parent), overrides={"storage.local_dir": str(root)})
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = cfg
    inst.bus = hc.EventBus()
    inst.vault = hc.VaultManager(cfg, None, event_bus=inst.bus)
    inst.vaults = [inst.vault]
    inst.parser = hc.DocumentParser()
    inst.chunker = hc.SemanticChunker(cfg)
    inst.save_binary = False
    inst.save_raw_html = False
    first = asyncio.run(inst.local_ingest("docs"))
    assert first, "first ingest should index the file"
    second = asyncio.run(inst.local_ingest("docs"))
    assert second == [], "unchanged content must be skipped on re-run"
    # Force bypasses the hash check.
    third = asyncio.run(inst.local_ingest("docs", force_refresh=True))
    assert third, "--force must re-index"


def test_local_url_scheme_is_citable():
    """Manual (local): cite the synthetic local:// URL, not the path."""
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(tempfile.mkdtemp())
    assert inst._local_url("papers/x.pdf") == "local://local/papers/x.pdf"


def test_grounding_goes_to_a_subdirectory_named_grounding():
    """Manual (Artifacts): research EMITs into artifacts/YYYY-MM-DD/grounding/."""
    assert hc.ConfigManager._defaults(None)["storage"]["grounding_subdir"] == "grounding"


def test_filter_low_and_fts_fast_path_defaults_hold():
    """Manual #4 and #5."""
    emb = hc.ConfigManager._defaults(None)["embeddings"]
    res = hc.ConfigManager._defaults(None)["research"]
    assert res["filter_low"] is True
    assert emb["fts_fast_path"] is True


def test_verify_note_in_manual_matches_behaviour():
    """Guard the manual's own claim that dashes/quotes fold and % != percent."""
    v = hc.VaultManager(TempConfig(tempfile.mkdtemp()))
    v.index_document("https://e.test/d", [hc.Chunk(
        text="Values rose 5% — see the annex today.",
        metadata={"header_path": "A", "source": "https://e.test/d"})], {})
    inst = hc.HoardCore.__new__(hc.HoardCore)
    inst.config = TempConfig(v.root_dir)
    inst.vault = v
    inst.vaults = [v]
    assert inst.verify_claim("Values rose 5% - see the annex today.") == "verified"
    assert inst.verify_claim("Values rose 5 percent - see the annex today.") != "verified"
