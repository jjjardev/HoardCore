"""CLI smoke tests for the argparse entrypoint (E9): run the real process and
assert parsing + exit-code behavior, catching flag-typo regressions like the
old silent `--recal` ignore (D3)."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "hoardcore.py"


def _run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(MODULE), *args],
        capture_output=True, text=True, timeout=120,
        cwd=cwd or str(REPO),
    )


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


def test_cli_verify_requires_claim():
    """`--action verify` without --claim must exit 2 with a clear message."""
    res = _run("_", "--action", "verify")
    assert res.returncode == 2
    assert "--claim" in (res.stdout + res.stderr)


def test_cli_research_requires_query():
    """`--action research` without --query must exit 2, not run a bare loop."""
    res = _run("_", "--action", "research")
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
