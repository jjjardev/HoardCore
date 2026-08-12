# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-08-12

### Changed
- **Repositioned: HoardCore is a research toolkit and memory protocol for AI
  agents — not an agent harness.** The tool provides the `DISCOVER → INGEST →
  RECALL → EMIT` loop, hybrid retrieval, and provenance tagging, but it does not
  host an LLM or manage context; that is the job of an agent harness (e.g.,
  OpenCode, Claude Code, or any other), which hosts the agent that reads
  `skill.md` and executes HoardCore via its CLI. Removed all "agent harness"
  / "OpenCode harness" phrasing across `README.md`, `AGENTS.md`, `skill.md`,
  `pyproject.toml`, `hoardcore.py`, and `Makefile`, replacing it with "research
  toolkit for AI agents," "agent-driven research layer," and "protocol your
  agent follows." OpenCode is now described as one example of a harness, not the
  only one. Technical details unchanged.

## [0.3.1] - 2026-08-11

### Fixed
- **`research` lost the Source Links / Citations block (and could raise).** The
  citation block was written via `f.write(...)` *after* the `with open(...)`
  block had closed the file, so every grounding-context file was missing its
  source-links section and the tail of `research` could raise
  `ValueError: I/O operation on closed file`. Moved the write inside the block.
  Added a regression test (`test_research_emits_citations_block`).

### Changed
- **Dropped the "offline / key-free / no API key / no torch" branding.** HoardCore
  is a web-touching harness (DuckDuckGo/Mojeek discovery, Cloudflare-aware
  fetch) and may be driven against cloud APIs, so the marketing language claimed
  more than the tool does. Updated `README.md`, `skill.md`, `AGENTS.md`,
  `Makefile`, `pyproject.toml`, and `hoardcore.py` docstrings/comments. Kept the
  technically-true "retrieval needs no ML model / no embeddings model" facts.

## [0.3.0] - 2026-08-10

### Rebrand: HoardCore-RAG (HCRAG) → HoardCore (HCH)
- Renamed from **HoardCore-RAG** to **HoardCore** — an *Agent Harness for
  Retrieval & Deep Research*, not a RAG library. The HCRAG acronym is retired;
  the harness is now branded **HoardCore / HCH** everywhere.
- Branding updated across `README.md` (fully rewritten in the pahebatcher
  template format, Design Decisions section removed), `skill.md`, `AGENTS.md`,
  `Makefile`, `hoardcore.toml`, `pyproject.toml` (package `name = "hoardcore"`,
  v0.3.0), the CLI banner, and the module docstring.
- Added `CHANGELOG` release discipline: **v0.3.0** marks the rebrand and is
  shipped as a git tag + GitHub release.

## [0.2.2] - 2026-08-09

### Fixed
- **Sitemap crawler was effectively dead.** In `parse_sitemap` the `<loc>` XML
  extraction sat *after* a `return []` inside the non-200 branch, so a live
  sitemap that responded 200 fell through and returned `None` — crashing
  `discover_urls` with `TypeError` and silently degrading `--action crawl` to a
  single scrape. Rewrote it with a namespace-aware `_extract_locs()` (lxml,
  regex fallback) plus dedupe.
- **`scrape` returned an empty result on a cache hit.** `_scrape_single` now
  serves the already-vaulted chunks via the new `VaultManager.get_chunks_for_url()`
  instead of an empty list.
- **Full-vault O(n) scan on every CLI run.** `backfill_vectors()` now short-
  circuits with a cheap count-equality check when nothing is missing.
- Unrecognized CLI flags are now warned about instead of silently ignored.
- Stale `v0.1` version strings removed; the CLI banner and config template now
  use a module-level `__version__`.

### Quality
- New `tests/test_crawler.py` suite (sitemap, robots, discovery — no network
  I/O) plus cache-hit and backfill regression tests. Suite is now **37 tests**.

## [0.2.1] - 2026-08-07

### Changed
- DeepResearch is now the **default research mode**. The bounded Hardcore
  Research Loop (with `[V]`/`[E]`/`[H]` provenance and adversarial audit)
  triggers on any open-ended research request; the former `autoresearch`
  trigger word is redundant and may be omitted. Feature renamed `autoresearch`
  → **DeepResearch** (depth presets are now `research`, `research deep`,
  `research exhaustive`, and the `x N` cap).

## [0.2.0] - 2026-08-07

### Added
- Autoresearch ("Hardcore Research Loop"): a bounded agent-research trigger in
  `skill.md` with provenance-tagged `[V]`/`[E]`/`[H]` grounding, adversarial
  audit, termination conditions (saturation, source quota, diminishing returns,
  pass budget, user interrupt), and depth presets (`standard`/`deep`/`exhaustive`)
  with conversational deepening ("go deeper" / "that's enough").
- PDF OCR fallback: scanned / image-only PDF pages are auto-OCRed when
  `rapidocr_onnxruntime` is installed (pip-only, local ONNX, no system deps).
  Embedded-text pages are still extracted by PyMuPDF (unchanged); only pages
  with no extractable text get OCR'd, and metadata reports
  `parser: pymupdf+ocr` plus an `ocr_pages` count. Degrades gracefully to a
  `(ocr: no text extracted)` note when the OCR engine is unavailable.
  Optional extra: `pip install .[ocr]`. Config key `parsers.enable_pdf_ocr`
  (default `true`).

### Fixed
- `research` action now honors the CLI `--strategy` flag (e.g.
  `--strategy aggressive`) for its `DISCOVER -> INGEST` step. Previously the
  strategy was silently ignored and the config default was used, so
  `research --strategy aggressive` still fetched with the `balanced` chain.

## [0.1.0] - 2026-08-07

### Added
- Single-file ingestion engine (`hoardcore.py`): scrape, crawl, search, discover,
  and the agentic `research` action (`DISCOVER -> INGEST -> RECALL -> EMIT`).
- Persistent SQLite vault (FTS5 + lexical vectors) with hybrid RRF retrieval and
  safe empty/punctuation-only query handling.
- Key-free web discovery (DuckDuckGo HTML with Mojeek fallback) through the
  resilient fetch chain (aiohttp -> curl_cffi -> optional FlareSolverr).
- Semantic chunking, junk-filtering, and DB-hygiene context manager
  (commit / rollback / close).
- Provenance-tagged artifacts (`[V]`/`[E]`/`[H]`) with path-traversal-safe
  `write_artifact()`.
- Agent integration: `AGENTS.md` trigger + `skill.md` operating manual,
  tested against the OpenCode harness.
- License: MIT.

### Quality
- pytest suite (21 tests): vault, network/fetch chain, junk detection.
- CI via GitHub Actions (lint with `ruff`, tests on Python 3.11 & 3.12).