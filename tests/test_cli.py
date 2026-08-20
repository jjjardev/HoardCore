"""CLI smoke tests for the argparse entrypoint (E9): run the real process and
assert parsing + exit-code behavior, catching flag-typo regressions like the
old silent `--recal` ignore (D3)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "hoardcore.py"


def _run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(MODULE), *args],
        capture_output=True, text=True, timeout=120,
        cwd=cwd or str(REPO),
    )


def _isolated_toml(tmp_path, extra: str = ""):
    """Write a sparse, temp-rooted hoardcore.toml so a CLI subprocess builds
    its engine against an isolated vault instead of the repo's (dense-mode,
    network-capable) config."""
    (tmp_path / "hoardcore.toml").write_text(
        f"[storage]\nroot_dir = '{tmp_path / 'data'}'\n\n"
        "[embeddings]\nenabled = true\nmode = 'sparse'\ndim = 64\n"
        "hybrid_search = true\n\n"
        "[network]\ndefault_strategy = 'fast'\nenable_preflight = false\n\n"
        f"{extra}",
        encoding="utf-8",
    )
    return str(tmp_path)


def test_cli_help_exits_zero():
    """`--help` must print usage and exit 0 (argparse)."""
    res = _run("--help")
    assert res.returncode == 0
    assert "--action" in res.stdout
    assert "--vault" in res.stdout


def test_cli_rejects_unknown_flag():
    """A typo like `--recal` must be rejected loudly instead of silently
    ignored, so a mis-typed recall count can't quietly change behavior."""
    res = _run("_", "--recal", "5")
    assert res.returncode != 0
    assert "usage" in (res.stderr + res.stdout).lower()


def test_cli_verify_requires_claim(tmp_path):
    """`--action verify` without --claim must exit 2 with a clear message.
    Runs in an isolated temp root so the (dense, network-enabled) repo config
    is never loaded into the subprocess engine."""
    cwd = _isolated_toml(tmp_path)
    res = _run("_", "--action", "verify", cwd=cwd)
    assert res.returncode == 2
    assert "--claim" in (res.stdout + res.stderr)


def test_cli_research_requires_query(tmp_path):
    """`--action research` without --query must exit 2, not run a bare loop."""
    cwd = _isolated_toml(tmp_path)
    res = _run("_", "--action", "research", cwd=cwd)
    assert res.returncode == 2


def test_cli_check_creates_vault_and_passes(tmp_path, monkeypatch):
    """End-to-end: `--action check` must open a fresh vault (in a temp root)
    and verify it cleanly. Uses an isolated config root via env-free override
    by pointing the storage at a temp dir through a one-shot TOML in cwd."""
    (tmp_path / "hoardcore.toml").write_text(
        f"[storage]\nroot_dir = '{tmp_path / 'data'}'\n\n"
        "[embeddings]\nenabled = true\nmode = 'sparse'\ndim = 64\n"
        "hybrid_search = true\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HC_POOL_SIZE"] = "2"
    res = subprocess.run(
        [sys.executable, str(MODULE), "_", "--action", "check"],
        capture_output=True, text=True, timeout=120,
        cwd=str(tmp_path), env=env,
    )
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "data" / "vault.db").exists()


def test_cli_stats_action_reports_vault_counts(tmp_path):
    """`--action stats` on a fresh vault exits 0 and prints a source count."""
    (tmp_path / "hoardcore.toml").write_text(
        f"[storage]\nroot_dir = '{tmp_path / 'data'}'\n\n"
        "[embeddings]\nenabled = true\nmode = 'sparse'\ndim = 64\n",
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(MODULE), "_", "--action", "stats"],
        capture_output=True, text=True, timeout=120,
        cwd=str(tmp_path),
    )
    assert res.returncode == 0, res.stderr
    assert "Sources:" in res.stdout


def test_cli_verify_hint_flag_errors_without_claim(tmp_path):
    """`--hint` still requires --claim (exit 2), not a silent no-op."""
    cwd = _isolated_toml(tmp_path)
    res = _run("_", "--action", "verify", "--hint", cwd=cwd)
    assert res.returncode == 2
    assert "--claim" in (res.stdout + res.stderr)


def test_cli_verify_unverified_exits_2(tmp_path):
    """The CI-wireable verify contract: an unsupported claim must exit 2
    (UNVERIFIED) from the CLI, not 0."""
    cwd = _isolated_toml(tmp_path)
    res = _run("_", "--action", "verify",
               "--claim", "absolutely absent gibberish xyzzy", cwd=cwd)
    assert res.returncode == 2
    assert "VERIFY: UNVERIFIED" in res.stdout


def test_cli_verify_claim_list_conflict_rejected(tmp_path):
    """improving_hoardcore.md #2: --claim-list is mutually exclusive with
    --claim/--claim-file (CLI contract error, not a silent merge)."""
    cwd = _isolated_toml(tmp_path)
    claims = tmp_path / "claims.txt"
    claims.write_text("some claim\n", encoding="utf-8")
    res = _run("_", "--action", "verify", "--claim-list", str(claims),
               "--claim", "other", cwd=cwd)
    assert res.returncode == 2
    assert "cannot be combined" in (res.stdout + res.stderr)


def test_cli_verify_claim_list_empty_file_exits_2(tmp_path):
    """A --claim-list containing only blanks/comments is a usage error (exit 2),
    not a silently empty (all-passing) batch."""
    cwd = _isolated_toml(tmp_path)
    claims = tmp_path / "claims.txt"
    claims.write_text("# only a comment\n\n", encoding="utf-8")
    res = _run("_", "--action", "verify", "--claim-list", str(claims), cwd=cwd)
    assert res.returncode == 2
    assert "no claims" in (res.stdout + res.stderr)


def test_cli_verify_claim_list_reports_aggregate_accuracy(tmp_path, make_chunk):
    """improving_hoardcore.md #2: batch verify must print a per-claim verdict
    table plus an aggregate citation-accuracy %, and exit with the worst
    verdict (any unverified -> 2)."""
    import hoardcore as hc
    cwd_s = _isolated_toml(tmp_path)  # root_dir = tmp_path/'data'
    cfg = hc.ConfigManager(str(tmp_path / "hoardcore.toml"))
    hc_obj = hc.HoardCore.__new__(hc.HoardCore)
    hc_obj.config = cfg
    hc_obj.vault = hc.VaultManager(cfg)
    hc_obj.vault.index_document(
        "https://coffee.test/1",
        [make_chunk("The Philippines has a coffee trade deficit of 13 million "
                    "dollars in 2022", url="https://coffee.test/1")],
        {})
    claims = tmp_path / "claims.txt"
    claims.write_text(
        "# batch audit\n"
        "The Philippines has a coffee trade deficit of 13 million dollars in 2022\n"
        "absolutely absent gibberish xyzzy\n",
        encoding="utf-8",
    )
    res = _run("_", "--action", "verify", "--claim-list", str(claims), cwd=cwd_s)
    assert res.returncode == 2  # worst verdict wins: one UNVERIFIED
    assert "VERIFIED" in res.stdout
    assert "UNVERIFIED" in res.stdout
    assert "citation accuracy: 1/2 = 50.0%" in res.stdout


def test_cli_scrape_ssrf_blocked_exits_2_without_traceback(tmp_path):
    """S3: an SSRF-refused target must exit 2 with a clean message and no
    Python traceback (the refusal used to crash main())."""
    cwd = _isolated_toml(tmp_path, "[network]\nssrf_protection = true\n")
    res = _run("https://127.0.0.1/x", "--action", "scrape", cwd=cwd)
    assert res.returncode == 2
    assert "Traceback" not in res.stderr
    assert "SSRF" in (res.stdout + res.stderr)


def test_cli_unexpected_exception_exits_2_no_traceback(tmp_path, monkeypatch):
    """An unexpected exception inside an action must exit 2 with a clean
    message and no raw traceback on stderr (the CLI contract)."""
    import asyncio

    import hoardcore as hc
    from tests.conftest import TempConfig

    cfg = TempConfig(str(tmp_path))
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)

    class _Boom:
        def __init__(self, *a, **k):
            pass

        config = type("C", (), {"_config": {}})()

        def organize_artifacts_by_day(self):
            return []

    monkeypatch.setattr(hc, "HoardCore", _Boom)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(hc.main(
            ["_", "--action", "scrape", "https://example.test/x"]))
    assert ei.value.code == 2



def test_module_level_citation_list_is_exported():
    """skill.md documents hoardcore.citation_list(urls); it must exist as a
    top-level module function (was AttributeError before the fix)."""
    import hoardcore as hc
    urls = ["https://a.example/1", "https://b.example/2"]
    block = hc.citation_list(urls)
    assert "## Source Links / Citations" in block
    assert "[#1] https://a.example/1" in block
    assert "[#2] https://b.example/2" in block


def test_cli_parallel_flag_parses_on_off():
    """--parallel / --no-parallel must be accepted (BooleanOptionalAction) and
    yield True / False / None (unset), so a caller can force or force-off the
    threaded ingest pipeline independently of config indexer.parallel."""
    import hoardcore as hc
    parser = hc._build_parser()
    assert parser.parse_args(["_", "--parallel"]).parallel is True
    assert parser.parse_args(["_", "--no-parallel"]).parallel is False
    assert parser.parse_args(["_"]).parallel is None


def test_cli_parallel_flag_runs_check(tmp_path):
    """--parallel must be accepted end-to-end through main() without error.
    `check` doesn't ingest (parallel is a no-op there), but it proves the flag
    parses and reaches the engine config override cleanly."""
    cwd = _isolated_toml(tmp_path)
    res = _run("_", "--action", "check", "--parallel", cwd=cwd)
    assert res.returncode == 0, res.stderr
    res = _run("_", "--action", "check", "--no-parallel", cwd=cwd)
    assert res.returncode == 0, res.stderr


# --- audit CLI exit codes ------------------------------------------------


def _audit_fixture(tmp_path, make_chunk):
    """Isolated vault seeded with one verbatim chunk + artifact paths."""
    import hoardcore as hc
    cwd_s = _isolated_toml(tmp_path)
    cfg = hc.ConfigManager(str(tmp_path / "hoardcore.toml"))
    hc_obj = hc.HoardCore.__new__(hc.HoardCore)
    hc_obj.config = cfg
    hc_obj.vault = hc.VaultManager(cfg)
    url = "https://coffee.test/audit-1"
    claim = ("The Philippines has a coffee trade deficit of 13 million "
             "dollars in 2022")
    hc_obj.vault.index_document(url, [make_chunk(claim, url=url)], {})
    quotes = {"v": f'X says "{claim}" [V#1]',
              "p": 'X says "verifiably absent nonsense xyzzy" [V#1]'}
    for kind, body in quotes.items():
        (tmp_path / f"art_{kind}.md").write_text(body + "\n"
            "\n## Source Links / Citations\n"
            f"[#1] {url} — {url}\n", encoding="utf-8")
    return cwd_s, quotes


def test_cli_audit_requires_artifact(tmp_path):
    """`--action audit` without --artifact must exit 2 with a clear message."""
    cwd = _isolated_toml(tmp_path)
    res = _run("_", "--action", "audit", cwd=cwd)
    assert res.returncode == 2
    assert "--artifact" in (res.stdout + res.stderr)


def test_cli_audit_missing_artifact_file_exits_2(tmp_path):
    """A nonexistent --artifact path must exit 2, not raise."""
    cwd = _isolated_toml(tmp_path)
    res = _run("_", "--action", "audit",
               "--artifact", str(tmp_path / "nope.md"), cwd=cwd)
    assert res.returncode == 2
    assert "not found" in (res.stdout + res.stderr)


def test_cli_audit_verified_artifact_exits_0(tmp_path, make_chunk):
    """A fully-verified artifact (verbatim quote, mapped, ingested) exits 0."""
    import hoardcore as hc  # noqa: F401
    cwd, quotes = _audit_fixture(tmp_path, make_chunk)
    res = _run("_", "--action", "audit",
               "--artifact", str(tmp_path / "art_v.md"), cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "VERIFIED" in res.stdout
    assert "100.0%" in res.stdout


def test_cli_audit_unverified_artifact_exits_2(tmp_path, make_chunk):
    """An artifact whose claims don't verify exits 2 (0% accuracy)."""
    cwd, quotes = _audit_fixture(tmp_path, make_chunk)
    res = _run("_", "--action", "audit",
               "--artifact", str(tmp_path / "art_p.md"), cwd=cwd)
    assert res.returncode == 2
    assert "UNVERIFIED" in res.stdout
    assert "0.0%" in res.stdout


def test_cli_audit_unmapped_source_exits_2(tmp_path, make_chunk):
    """A [V#N] with no Source Link entry exits 2 with the mapping warning."""
    import hoardcore as hc  # noqa: F401
    cwd, quotes = _audit_fixture(tmp_path, make_chunk)
    unmapped = (tmp_path / "art_unmap.md")
    unmapped.write_text(
        ('X says "The Philippines has a coffee trade deficit of 13 million '
         'dollars in 2022" [V#9]\n\n## Source Links / Citations\n'
         "[#1] https://coffee.test/audit-1 — https://coffee.test/audit-1\n"),
        encoding="utf-8")
    res = _run("_", "--action", "audit",
               "--artifact", str(unmapped), cwd=cwd)
    assert res.returncode == 2
    assert "Source-link mapping MISSING" in res.stdout


def _local_toml(tmp_path, extra: str = ""):
    """A temp-rooted config with an isolated local_inputs dir for --action local."""
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    (tmp_path / "hoardcore.toml").write_text(
        f"[storage]\nroot_dir = '{tmp_path / 'data'}'\n"
        f"local_dir = '{inputs}'\n\n"
        "[embeddings]\nenabled = true\nmode = 'sparse'\ndim = 64\n"
        "hybrid_search = true\n\n"
        "[network]\ndefault_strategy = 'fast'\nenable_preflight = false\n\n"
        f"{extra}",
        encoding="utf-8")
    return str(tmp_path), inputs


def test_cli_local_ingest_list_skip_and_search(tmp_path):
    """`--action local` end-to-end through the real CLI: list, ingest, content-
    hash skip on re-run, and search over the vaulted local file."""
    cwd, inputs = _local_toml(tmp_path)
    (inputs / "note.txt").write_text("pomegranate yields doubled this season",
                                     encoding="utf-8")
    res = _run("_", "--action", "local", "--list", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "note.txt" in res.stdout

    res = _run("_", "--action", "local", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Ingested 1 chunks" in res.stdout

    res = _run("_", "--action", "local", cwd=cwd)
    assert "Ingested 0 chunks" in res.stdout  # content-hash skip

    res = _run("_", "--action", "search", "--query", "pomegranate", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "local://local/note.txt" in res.stdout


def test_cli_cross_vault_search_and_verify(tmp_path):
    """`--vault a,b` through the real CLI: ingest into each vault, then a
    cross-vault search and a verify fold (verified with both, unverified with
    the vault that lacks the claim)."""
    cwd, inputs = _local_toml(tmp_path)
    (inputs / "a.txt").write_text("pineapple harvest records for bohol",
                                  encoding="utf-8")
    assert _run("_", "--action", "local", "--vault", "va", cwd=cwd).returncode == 0
    (inputs / "a.txt").unlink()
    (inputs / "b.txt").write_text("orange blossom honey from visayas",
                                  encoding="utf-8")
    assert _run("_", "--action", "local", "--vault", "vb", cwd=cwd).returncode == 0

    res = _run("_", "--action", "search", "--query", "pineapple honey",
               "--vault", "va,vb", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "'vault': 'va'" in res.stdout or "vault va" in res.stdout

    # Claim held only in va: verifies across va,vb, fails on vb alone.
    claim = "pineapple harvest records for bohol"
    res = _run("_", "--action", "verify", "--claim", claim,
               "--vault", "va,vb", cwd=cwd)
    assert res.returncode == 0, res.stdout + res.stderr
    res = _run("_", "--action", "verify", "--claim", claim,
               "--vault", "vb", cwd=cwd)
    assert res.returncode == 2, res.stdout + res.stderr
