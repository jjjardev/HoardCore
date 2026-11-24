"""Tests for the agent-facing output-contract parser."""

from pathlib import Path

import hoardcore as hc


def test_parse_complete_one_shot_brief():
    brief = hc.parse_output_brief(
        "ARTIFACT: decision memo; AUDIENCE: founder; DECISION: choose vendor; "
        "FORMAT: two pages, subject line, kill criteria; GATES: strict")
    assert brief == {
        "ARTIFACT": "decision memo",
        "AUDIENCE": "founder",
        "DECISION": "choose vendor",
        "FORMAT": "two pages, subject line, kill criteria",
        "GATES": "strict",
    }
    assert hc.output_brief_missing(brief) == []


def test_parse_aliases_and_missing_fields():
    brief = hc.parse_output_brief(
        "TYPE: dossier\nFOR: junior engineer\nFORMAT: five sections")
    assert brief["ARTIFACT"] == "dossier"
    assert brief["AUDIENCE"] == "junior engineer"
    assert hc.output_brief_missing(brief) == ["DECISION"]


def test_unknown_fields_are_ignored():
    brief = hc.parse_output_brief("ARTIFACT: method; MOOD: urgent")
    assert brief == {"ARTIFACT": "method"}
    assert hc.output_brief_missing(brief) == ["AUDIENCE", "DECISION"]


def test_skill_documents_output_contract(tmp_path):
    text = (Path(__file__).resolve().parents[1] / "skill.md").read_text(encoding="utf-8")
    assert "Output contract" in text
    assert "parse_output_brief" in text
    assert "forced-choice" in text
