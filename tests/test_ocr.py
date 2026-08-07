"""Tests for the optional PDF OCR fallback in DocumentParser.

These are deterministic mocks so they never need rapidocr_onnxruntime (or even
PyMuPDF) installed — which is also the CI environment (base package only). They
verify the wiring: blank pages are OCR'd and flagged in metadata, and OCR
degrades gracefully (no crash) when the engine is unavailable.
"""

import asyncio

import hoardcore as hc


class _FakePage:
    def __init__(self, n: int):
        self.n = n

    def get_text(self):
        return ""  # scanned / image-only page -> no embedded text


class _FakeDoc:
    page_count = 2

    def load_page(self, i):
        return _FakePage(i)

    def close(self):
        pass


class _FakeFitz:
    @staticmethod
    def open(stream=None, filetype=None):
        return _FakeDoc()


def _monkeypatch_scanned(monkeypatch):
    monkeypatch.setattr(hc, "FITZ_AVAILABLE", True)
    monkeypatch.setattr(hc.DocumentParser, "_fitz", _FakeFitz)
    monkeypatch.setattr(hc, "_BINARY_IMPORTED", True)


def test_pdf_ocr_fallback_extracts_and_marks_pages(monkeypatch):
    _monkeypatch_scanned(monkeypatch)

    def fake_ocr(page, dpi=200):
        return f"READING PAGE {page.n}"

    monkeypatch.setattr(hc.DocumentParser, "_ocr_page", staticmethod(fake_ocr))

    text, meta = asyncio.run(hc.DocumentParser.parse_pdf(b"fake-pdf-bytes"))
    assert "READING PAGE 0" in text
    assert "READING PAGE 1" in text
    assert meta["parser"] == "pymupdf+ocr"
    assert meta["ocr_pages"] == 2


def test_pdf_ocr_flag_without_engine_degrades_gracefully(monkeypatch):
    _monkeypatch_scanned(monkeypatch)
    monkeypatch.setattr(hc, "RAPIDOCR_AVAILABLE", False)

    text, meta = asyncio.run(hc.DocumentParser.parse_pdf(b"fake-pdf-bytes"))
    assert "(ocr: no text extracted)" in text
    assert meta["parser"] == "pymupdf"  # not upgraded, no crash


def test_ocr_page_returns_empty_without_engine(monkeypatch):
    monkeypatch.setattr(hc, "RAPIDOCR_AVAILABLE", False)
    page = object()  # an uncooperative object would raise if touched
    assert hc.DocumentParser._ocr_page(page) == ""
