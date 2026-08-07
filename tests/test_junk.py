"""Tests for the junk detection heuristic."""

import hoardcore as hc


def test_junk_empty():
    assert hc.HoardCore._detect_junk("", None, False, 1.0) == "empty_extraction"
    assert hc.HoardCore._detect_junk("[No extractable content found]", None, {}, 1.0) == "empty_extraction"


def test_junk_boilerplate_block_pages():
    for text in [
        "        Please click here if you are not redirected in a moment",
        "Access denied. Please do not have permission to view this page.",
        "The page you are looking for was not found. 404",
    ]:
        assert hc.HoardCore._detect_junk(text, None, {}, 0.0) is not None


def test_not_junk_real_content():
    text = ("Negros is an island in the Philippines. Its economy is driven by "
            "sugar milling, renewable energy, tourism and agriculture. "
            "Official data from the PSA shows significant growth across "
            "multiple sectors this year.")
    assert not hc.HoardCore._detect_junk(text, None, {}, 0.9)


def test_junk_very_short_low_quality():
    assert hc.HoardCore._detect_junk("tiny", None, {}, 0.0) == "near_empty_extraction"
    # A short-but-real snippet above the quality bar is not junk.
    assert not hc.HoardCore._detect_junk("Solar farm expansion planned.", None, {}, 0.9)