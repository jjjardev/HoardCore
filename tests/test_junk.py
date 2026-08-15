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


def test_junk_login_consent_shells():
    """Realistic extractions from login/consent-walled pages observed during a
    live research hunt (LinkedIn, Facebook, Instagram, FamilySearch). Each is
    login chrome with no content and must be refused."""
    linkedin = ("Agree & Join LinkedIn\nBy clicking Continue to join or sign in, "
                "you agree to LinkedIn's User Agreement, Privacy Policy, and Cookie Policy. "
                "Already on Linkedin? Sign in or New to Linkedin? Join now")
    facebook = ("Facebook\nEmail o Telepono\nPassword\nNakalimutan ang account?\n"
                "Mag-sign Up\nMag-log In\nMeta Pay\nPatakaran sa Privacy\nLog ng Aktibidad")
    instagram = ("AfrikaansالعربيةČeštinaDanskDeutschEnglishEspañolFrançais日本語한국어"
                 "FilipinoTürkçe© 2026 Instagram from Meta")
    familysearch = ("Your web browser is not fully supported. Please update to the "
                    "latest version to enjoy all that FamilySearch has to offer.")

    for shell, score in ((linkedin, 0.001), (facebook, 0.006),
                         (instagram, 0.004), (familysearch, 0.01)):
        assert hc.HoardCore._detect_junk(shell, None, {}, score) is not None, shell


def test_not_junk_low_quality_flat_list_with_prose():
    """A real low-quality directory page (many short lines plus one prose
    line) must NOT be refused by the flat-list shell heuristic: prose present
    means content, even if most rows are short."""
    text = "\n".join([f"Member {i}" for i in range(60)] + [
        "Member profile for Jose Rizal from Silay City, Negros Occidental, "
        "a prominent Filipino figure whose family records are being traced."])
    assert not hc.HoardCore._detect_junk(text, None, {}, 0.05)


def test_junk_low_quality_flat_language_picker():
    """A pure list with zero prose (the signature of an out-of-session
    language picker) is refused once it clears the short-boilerplate bar."""
    text = "\n".join([f"Lang{i}" for i in range(40)])
    reason = hc.HoardCore._detect_junk(text, None, {}, 0.05)
    assert reason is not None
    assert "flat_list" in reason


def test_not_junk_findagrave_citation():
    """Long mixed UI + real memorial citation: the genuine citation portion
    must not be over-blocked by the shell heuristics."""
    text = ("Memorial transferred successfully.\n"
            "Find a Grave, database and images, memorial page for Nestor O Mana-Ay "
            "(14 Dec 1939-9 Mar 2013), Find a Grave Memorial ID 176603528, citing "
            "Silay City Cemeterio Municipal, Silay, Negros Occidental Province, "
            "Western Visayas, Philippines; Maintained by David Hopper.")
    assert not hc.HoardCore._detect_junk(text, None, {}, 0.5)
