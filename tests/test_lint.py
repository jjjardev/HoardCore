"""Tests for the static artifact linter (--action lint)."""

import hoardcore as hc

LINES_OK = [
    "# Report",
    "",
    'He said "the economy grew by 5.1 percent in 2024" [V#1] and stopped.',
    "Analysis continues here [E].",
    "",
    "## Source Links / Citations",
    "",
    "[#1] https://example.test/a",
]

LINES_TABLE = [
    "# Report",
    '| Did X happen? | No | basis [V#1] |',
    "Prose with an unclosed quote [V#1]",
    "Analysis line with tag [E] then [V#1]",
    "- bullet riding [V#1]",
    "",
    "## Source Links / Citations",
    "[#1] https://example.test/a",
]


def _write(tmp_path, lines):
    p = tmp_path / "artifact.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def test_lint_clean_artifact(tmp_path):
    inst = object.__new__(hc.HoardCore)
    report = inst.lint_artifact(_write(tmp_path, LINES_OK))
    assert report["counts"]["error"] == 0
    assert report["counts"].get("warning", 0) == 0


def test_lint_flags_table_tag_and_unmapped(tmp_path):
    inst = object.__new__(hc.HoardCore)
    lines = list(LINES_TABLE)
    lines[1] = "| Did X happen? | No | basis [V#7] |"
    report = inst.lint_artifact(_write(tmp_path, lines))
    types = {f["type"] for f in report["findings"]}
    assert "tag_in_table" in types and "unmapped_tag" in types
    assert report["counts"]["error"] >= 2


def test_lint_warnings_and_strict_escalation(tmp_path):
    inst = object.__new__(hc.HoardCore)
    path = _write(tmp_path, [
        "# R",
        "Paraphrase first [E], then a quote \"some verbatim words here ok\" [V#1]",
        "Short \"tiny\" [V#1]",
        "",
        "## Source Links / Citations",
        "[#1] https://example.test/a",
    ])
    soft = inst.lint_artifact(path, strict=False)
    hard = inst.lint_artifact(path, strict=True)
    assert soft["counts"].get("warning", 0) > 0 and soft["counts"]["error"] == 0
    assert hard["counts"]["error"] >= 1
