#!/usr/bin/env python3
"""Assert that __version__, pyproject version, and (on a tag) the git tag agree.

Prevents the version-drift defect where pyproject.toml / __version__ were left
at 0.14.0 while the release tag advanced to v0.14.2. Runs in CI: it always
compares __version__ against the pyproject version; when GITHUB_REF points at a
`v*` tag (a release push), it also requires both to match the tag.

Exit 0 on agreement, 1 on any mismatch.
"""

import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def module_version() -> str:
    text = (ROOT / "hoardcore.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("could not parse __version__ in hoardcore.py")
    return m.group(1)


def pyproject_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]


def readme_badge_version() -> str:
    """Parse the README version badge; empty string when absent."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"badge/version-([\d.]+)-", text)
    return m.group(1) if m else ""


def tag_version(ref: str | None) -> str | None:
    if not ref:
        return None
    m = re.match(r"^refs/tags/v(\d+\.\d+\.\d+)$", ref)
    return m.group(1) if m else None


def main() -> int:
    mv = module_version()
    pv = pyproject_version()
    bv = readme_badge_version()
    tv = tag_version(os.environ.get("GITHUB_REF"))
    ok = True
    if mv != pv:
        print(f"VERSION MISMATCH: __version__={mv!r} != pyproject version={pv!r}",
              file=sys.stderr)
        ok = False
    if bv and bv != mv:
        # The badge has drifted twice (0.14.3, 0.14.4); it is checked so the
        # README can never silently disagree with the shipped version again.
        print(f"VERSION MISMATCH: README badge={bv!r} != __version__={mv!r}",
              file=sys.stderr)
        ok = False
    if tv is not None:
        for label, value in (("__version__", mv), ("pyproject", pv)):
            if value != tv:
                print(f"VERSION MISMATCH: {label}={value!r} != git tag v{tv!r}",
                      file=sys.stderr)
                ok = False
    if ok:
        print(f"version OK: {mv} (pyproject matches; badge matches; tag {tv or 'n/a'})")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
