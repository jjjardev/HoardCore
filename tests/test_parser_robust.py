"""Parser crash-resistance: feed garbage/random bytes to every document parser
and assert it never raises (returns text + metadata instead).

The parsers are the highest-risk input surface (untrusted web content). CI runs
base deps only, so binary parsers degrade to `{"parser": "failed", ...}` here —
the point is that no input can crash the process, in any environment.
"""

import asyncio
import random

import hoardcore as hc


def _garbage(rng: random.Random) -> bytes:
    return bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))


def test_binary_parsers_never_crash_on_garbage():
    rng = random.Random(0)
    for _ in range(25):
        blob = _garbage(rng)
        for parse in (hc.DocumentParser.parse_pdf,
                      hc.DocumentParser.parse_docx,
                      hc.DocumentParser.parse_epub):
            text, meta = asyncio.run(parse(blob))
            assert isinstance(text, str)
            assert isinstance(meta, dict)


def test_clean_html_never_crashes_on_garbage():
    rng = random.Random(1)
    for _ in range(25):
        garbage = _garbage(rng).decode("latin-1")
        text, meta = asyncio.run(hc.DocumentParser.clean_html(
            garbage, "https://robust.test/page"))
        assert isinstance(text, str)
        assert isinstance(meta, dict)


def test_parse_text_never_crashes_on_garbage():
    rng = random.Random(2)
    for _ in range(25):
        text, meta = asyncio.run(hc.DocumentParser.parse_text(
            _garbage(rng).decode("latin-1"), "https://robust.test/txt"))
        assert isinstance(text, str)
        assert isinstance(meta, dict)
