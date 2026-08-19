"""Tests for local directory ingestion (`--action local` from storage.local_dir).

Covers: txt/md/html/docx/pdf/epub indexing under synthetic `local://local/`
URLs, content-hash freshness (skip-if-unchanged, re-index on change, --force),
path-traversal refusal, unsupported-extension handling, and the read-only scan.
"""

import asyncio
import io
import os
import zipfile

import hoardcore as hc
from tests.conftest import TempConfig


def _scraper(tmp_path, monkeypatch):
    """HoardCore with an isolated root_dir and a dedicated local_inputs dir."""
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    cfg = TempConfig(str(tmp_path), overrides={
        "storage.local_dir": str(inputs),
        "storage.root_dir": str(tmp_path / "data"),
    })
    monkeypatch.setattr(hc, "ConfigManager", lambda: cfg)
    return hc.HoardCore(), inputs


def _write(inputs, name, data, mode="wb"):
    path = os.path.join(str(inputs), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as fh:
        fh.write(data)
    return path


def _docx_bytes():
    import docx
    d = docx.Document()
    d.add_paragraph("quarterly harvest report covering pineapple and mango")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pdf_bytes():
    import fitz
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "mango exports rose sharply in may")
    return pdf.tobytes()


def _epub_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" "
            'media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
            '<metadata><dc:title '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">T</dc:title></metadata>'
            '<manifest><item id="c1" href="chap1.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>')
        zf.writestr(
            "OEBPS/chap1.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>coconut oil distillation notes for the refinery</p></body></html>")
    return buf.getvalue()


def test_txt_ingest_searchable_with_local_url(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "note.txt", "pomegranate yields doubled this season", mode="w")
    results = asyncio.run(scraper.local_ingest("note.txt"))
    assert len(results) == 1
    chunks = scraper.vault.search_vault("pomegranate", hybrid=False)
    assert len(chunks) == 1
    assert chunks[0].metadata["source_url"] == "local://local/note.txt"


def test_markdown_and_html_ingest(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "notes.md",
           "# Field diary\n\nlychee orchards expanded to the hills", mode="w")
    _write(inputs, "page.html",
           "<html><body><article><h1>Report</h1>"
           "<p>guava juice export figures</p></article></body></html>",
           mode="w")
    results = asyncio.run(scraper.local_ingest())
    assert len(results) >= 2
    assert scraper.vault.search_vault("lychee", hybrid=False)
    assert scraper.vault.search_vault("guava", hybrid=False)


def test_content_hash_skips_unchanged_and_reindexes_on_change(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "log.txt", "baseline observatory reading alpha", mode="w")
    first = asyncio.run(scraper.local_ingest("log.txt"))
    assert len(first) == 1
    # Unchanged bytes -> skipped as cached, no new chunks.
    again = asyncio.run(scraper.local_ingest("log.txt"))
    assert again == []
    # Changed bytes -> new WORM version indexed.
    _write(inputs, "log.txt", "baseline observatory reading beta", mode="w")
    changed = asyncio.run(scraper.local_ingest("log.txt"))
    assert len(changed) == 1
    assert any("beta" in c["text"] for c in changed)
    with scraper.vault._db() as (_conn, cur):
        cur.execute("SELECT version FROM documents WHERE url = ?",
                    ("local://local/log.txt",))
        versions = [r[0] for r in cur.fetchall()]
    assert sorted(versions) == [1, 2]


def test_force_reindexes_unchanged_file(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "f.txt", "force me through", mode="w")
    asyncio.run(scraper.local_ingest("f.txt"))
    forced = asyncio.run(scraper.local_ingest("f.txt", force_refresh=True))
    assert len(forced) == 1


def test_path_traversal_refused(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "ok.txt", "safe", mode="w")
    out = asyncio.run(scraper.local_ingest("../outside.txt"))
    assert out[0]["metadata"]["error"].startswith("LOCAL_PATH_REFUSED")
    scan = scraper.scan_local("../outside")
    assert scan[0]["error"].startswith("LOCAL_PATH_REFUSED")


def test_nonexistent_path_reports_error(tmp_path, monkeypatch):
    scraper, _inputs = _scraper(tmp_path, monkeypatch)
    out = asyncio.run(scraper.local_ingest("missing.txt"))
    assert out[0]["metadata"]["error"].startswith("LOCAL_PATH_NOT_FOUND")


def test_unsupported_extension_refused(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "notes.py", "print(1)", mode="w")
    chunks, meta = asyncio.run(scraper._process_local("notes.py"))
    assert chunks == []
    assert meta["error"].startswith("LOCAL_UNSUPPORTED_EXT")
    # Scan only lists supported extensions.
    _write(inputs, "real.py", "print(1)", mode="w")
    _write(inputs, "real.txt", "hi", mode="w")
    files = [e["path"] for e in scraper.scan_local()]
    assert "real.txt" in files
    assert "real.py" not in files


def test_scan_lists_files_with_sizes(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "a.txt", "alpha", mode="w")
    _write(inputs, "sub/b.md", "# beta", mode="w")
    entries = scraper.scan_local()
    paths = {e["path"] for e in entries}
    assert paths == {"a.txt", os.path.join("sub", "b.md")}
    assert all(e["bytes"] > 0 for e in entries)
    assert all("modified" in e for e in entries)


def test_docx_pdf_epub_ingest(tmp_path, monkeypatch):
    scraper, inputs = _scraper(tmp_path, monkeypatch)
    _write(inputs, "docs/doc.docx", _docx_bytes())
    _write(inputs, "docs/doc.pdf", _pdf_bytes())
    _write(inputs, "docs/doc.epub", _epub_bytes())
    results = asyncio.run(scraper.local_ingest("docs"))
    assert len(results) >= 3
    assert scraper.vault.search_vault("pineapple", hybrid=False)
    assert scraper.vault.search_vault("mango", hybrid=False)
    assert scraper.vault.search_vault("coconut", hybrid=False)
