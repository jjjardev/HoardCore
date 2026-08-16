# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.3] - 2026-08-15

### Fixed
- **Flat `medium` confidence on homogeneous vaults.** Hybrid-retrieval
  confidence bands no longer key off absolute RRF fused scores (which cluster
  into one band on same-topic vaults and tagged every hit `medium`). The new
  default (`embeddings.conf_mode = "relative"`) ranks *within* the returned
  recall set: top hit(s) clearly above the set's own tail are `high`, the tail
  hugging the coincidence floor is `low`, the middle is `medium`. Only a
  **keyword-backed** set (a genuine FTS match near the top) can crown `high`;
  a pure-vector/off-topic set never does — mirroring `verify`'s
  corpus-scaled coincidence-floor logic. `conf_mode = "absolute"` restores the
  legacy `conf_high_abs`/`conf_low_abs` thresholds.

### Added
- **`--action stats` confidence probe.** `stats` now reports the configured
  `conf_mode` plus a sampled `high`/`medium`/`low` distribution across the
  vault's own header-phrase probes, so retrieval flatness is visible and
  diagnosable instead of silent.

### Notes
- Regression coverage: homogeneous-vault recall now produces a spread (not
  all-`medium`), and a small recall can only crown the top ~20% `high`. 111
  tests green.

## [0.9.2] - 2026-08-15

### Added
- **Typography-blind exact-phrasing verification.** `verify` now folds
  *typographic* noise only — en/em/figure/horizontal-bar/minus dashes, smart
  quotes, NBSP, and NFKC full-width variants — so a typesetter's dash never
  flips a verdict. Token identity (`400K` vs `400K+`) and word order are still
  enforced, preserving the exact-phrasing `[V]` contract.
- **`verify --hint` coaching.** On a `PARTIAL`/`UNVERIFIED` denial, prints the
  nearest stored phrase (fuzzy overlap vs. the normalized claim) with a
  "reword your claim to match the source text" nudge — turning a dead-end
  denial into the reformulation loop the exact-phrasing gate is designed for.
- **`--action stats`.** One-command vault summary: distinct sources, document
  versions, chunks, vectors, embedding mode/dim, schema version, page size,
  and DB size — the numbers a promotion/maintenance pass needs.
- **Module-level artifact helpers.** `hoardcore.citation_list(urls)`,
  `hoardcore.write_artifact(...)`, and `hoardcore.organize_artifacts_by_day()`
  now exist at module level exactly as `skill.md` documents them (previously
  only `HoardCore.citation_list` existed as an instance/static method, so the
  documented call raised `AttributeError`).

### Fixed
- **`verify` false negatives from typography** — e.g. `500–2,000` (en-dash) vs
  `500-2,000` (hyphen) previously reported `UNVERIFIED`; now `VERIFIED` (A6).
- **Doc/code drift on artifacts helpers** — the skill-documented module-level
  `citation_list`/`write_artifact`/`organize_artifacts_by_day` imports work.

## [0.9.1] - 2026-08-15

### Fixed
- **Docs accuracy audit.** Corrected false/outdated claims in `README.md` and
  added missing v0.9.0 behavior to `skill.md`:
  - `verify` `PARTIAL` semantics: now documented as "measurably beats the
    vault's coincidence floor by a corpus-scaled relative margin" (not `rank < -2.0`).
  - Test count corrected to 101 passing tests.
  - `ConfigManager` singleton: now scoped to the default config path only;
    non-default paths build fresh instances. Removed the stale "shared class
    attribute" wording.
  - Architecture section: vector scan corrected to a cached numpy
    matrix-vector product with `argpartition` for top-k.
  - Dimension migration clarified to the embed-fingerprint (`embed_fp`) model
    with batch in-place backfill; `[parsers]` docs gained `enable_pdf_ocr`
    (default true) which the 0.9.0 changelog claimed but the config table lacked.
  - Makefile/tree listing updated (`bench` target; `bench_hoardcore_full.py`
    matmul benchmark), and `Contributing` dropped already-shipped items
    (CI/CD, config versioning, brute-force bench wording) in favor of the real
    open areas.
  - `skill.md`: documented `embeddings.reranker_model`, the `filter_low`
    EMIT filter, and schema-version/embed-fingerprint guarantees surfaced by
    `--action check`.
- **Repo description** (GitHub `About`): kept factually faithful while
  sharpening the differentiator — citations machine-verified by CLI, not
  prompt-enforced.

## [0.9.0] - 2026-08-15

### Added
- **Entry-point plugin system.** `PluginManager` discovers third-party
  extensions via `importlib.metadata` entry points across four groups:
  `hoardcore.parsers`, `hoardcore.fetchers`, `hoardcore.providers`, and
  `hoardcore.chunkers`. Parser plugins are keyed by content type; fetcher and
  provider plugins append to the strategy/discovery fallback chains; chunker
  plugins are selected by `chunking.strategy = "plugin.<name>"` (any plugin
  failure falls back to the built-in pipeline). Gated by `plugins.enabled`.
- **Lifecycle event bus.** `EventBus` publishes `document.ingested`,
  `chunk.embedded`, `discovery.completed`, and `search.completed`; handlers
  register via `bus.on('event', fn)` or `@bus.on('event')`, and an exception in
  one handler never blocks the triggering path.
- **SSRF protection** (`network.ssrf_protection`, default `true`). Fetch
  targets are resolved and checked that every address is public before use,
  non-http(s) and block-listed hosts (cloud-metadata, DNS-special names) are
  refused, and redirect chains are re-validated after each hop (aiohttp
  `allow_redirects=False` manual loop; final-URL check for curl/FlareSolverr).
  Disable only on trusted isolated networks.
- **Schema versioning.** Vaults record `PRAGMA user_version` so future schema
  migrations run deterministically instead of by ad-hoc DDL.
- **Embedding fingerprinting.** `EmbeddingsEngine.fingerprint()` folds the model
  name/dim/quantize into a cache key; `chunk_vectors` gains an `embed_fp`
  column so stale cross-model vectors are never served and backfill knows
  exactly which rows to rebuild.
- **Reranker hook** (`embeddings.reranker_model`). An optional cross-encoder
  re-ranks the final recalled set via fastembed's `TextCrossEncoder`, loaded
  lazily, cached per instance, and degrading to input order on any failure.
- **CJK-aware chunking.** Token estimates count CJK characters as ~1 token each
  and `overlap_tokens` now drives a sliding-window tail overlap so
  boundary-split sentences stay in context.
- **Numpy matrix-vector scan with cache.** `_vector_scan` loads the whole
  vector table into one contiguous float32 buffer and dots it with the query
  (`argpartition` for top-k), caching the matrix keyed on row count/width for
  unchanged vaults; falls back to per-row cosine when numpy is absent.
- **Backfill as batch transactions** (1000 rows per commit) with stale-row
  cleanup when the embedding dimension changes.

### Changed
- **`clean_html` is trafilatura-first.** `trafilatura` is the default extractor;
  `readability-lxml` is used only when trafilatura yields < 100 characters.
- **`parse_binary` error surface.** Unknown binary types report the real decode
  exception under `metadata.error` instead of a silent generic message.
- **`verify_vault` N+1 eliminated.** Two `GROUP BY` passes replace one query per
  chunk row.
- **Config manager is instance-scoped** for non-default paths, fixing state
  bleed between separately-constructed managers.
- **Version bumped to 0.9.0** (pyproject, module `__version__`, config banner).

### Fixed
- **Discovery N+1 on the search path** (config `discovery.*` honored per
  provider attempt).

### Not implemented (deliberately deferred)
- **`sqlite-vec` (vec0) was not adopted.** The
  libsql `vec0` virtual table is a *brute-force exact scan*, not an ANN index —
  its advertised gains are storage/compression wins, not asymptotic search
  speed — and the production-grade HNSW variant is still marked alpha. For the
  single-user CLI threat model, the in-process numpy matrix–vector scan (plus
  the count-keyed cache and FTS fast path) already delivers the same or better
  recall at the vault sizes HoardCore targets, without a new native dependency
  or a second columnar copy of the vectors. Revisit when a stable HNSW
  (e.g. DiskANN) ships in a SQLite-compatible form.
- **Multi-user / multi-server mode was NOT built.** HoardCore remains a
  single-user, single-writer CLI tool: the SQLite vault is not wired for
  concurrent writers, and the WebSearchProvider/EventBus are in-process only.
  Nothing in this release introduces a server surface; `network.ssrf_protection`
  hardens the *fetching* side in case the module is ever embedded in a server
  context, but the vault + CLI remain explicitly single-tenant by design.

## [0.8.4] - 2026-08-15

### Added
- **Adaptive answer-first routing for `research`.** The agentic loop now queries
  the existing vault *before* touching the web (`research.answer_first`, default
  true): a high-confidence memory hit for a repeat question skips live DISCOVER
  entirely, and the grounding file is flagged "Answer-first recall". New
  `--no-answer-first` forces fresh DISCOVER regardless. When no high-confidence
  answer exists, the loop is unchanged (`DISCOVER -> INGEST -> RECALL -> EMIT`).
- **Low-confidence filtering at EMIT** (`research.filter_low`, default true).
  Confidence-`low` chunks are dropped from a recall set whenever stronger
  (non-low) chunks remain; a lone low hit is still returned rather than nothing.
- **Matryoshka dimension truncation surfaced in config** (`embeddings.mrl_dims`,
  default 0 = full model dimension). Dense vectors can be stored at a truncated
  dimension; the existing dimension-migration backfill rebuilds stale rows.
- **Near-duplicate chunk filter made configurable** (`indexer.near_dedup`,
  default false, and `indexer.near_dedup_threshold`). Optionally drops simhash
  near-duplicate chunks at ingest; off by default to preserve cross-source
  corroborating text as evidence.
- **New `[research]` TOML section** and config plumbing (`DEFAULT_CONFIG`,
  `_defaults`, `hoardcore.toml`) for the above.
- **Tests** covering answer-first routing (skip path and forced discovery),
  low-confidence filtering, MRL truncation, near-dedup (on/off), and simhash
  SQLite safety.

### Changed
- **README and `skill.md` updated.** The `research` action reference, the
  Hardcore Research Loop, and the CLI/config tables now document answer-first
  routing, `--no-answer-first`, `mrl_dims`, `near_dedup`, and `filter_low`.
- **Two dense-mode tests hardened** to pin `mrl_dims=0` so they no longer depend
  on the machine's `hoardcore.toml`.

### Fixed
- **Simhash overflow broke ingest with near-dedup enabled.** `_simhash()`
  produced a full unsigned 64-bit value; when bit 63 was set it exceeded
  SQLite's signed INTEGER maximum, raising "Python int too large to convert to
  SQLite INTEGER" and failing to ingest ~44% of documents. The top bit is now
  forced clear so values always fit; hamming-distance comparisons are
  unaffected because every value is masked identically.

## [0.8.3] - 2026-08-14

### Added
- **Regression test for fast-path confidence (v0.8.2 test gap).** New
  `test_fts_fast_path_confidence_is_medium_not_high` asserts every FTS-only
  fast-path hit is tagged `confidence='medium'` (vector scan skipped, so
  semantic closeness is unverified) — closing the gap that would have let the
  dishonest `'high'` silently return. Test-only release; no behavior change.

## [0.8.2] - 2026-08-14

### Changed
- **Honest int8 positioning.** The v0.8.1 numpy rewrite upcasts int8 payloads
  to int32 (required: int16 overflows in just 2–3 dims at 127*127=16129). This
  makes the float32 path the fast scan path and int8 the *storage* optimization
  (~4x smaller vault). Corrected the stale v0.8.0 claim "~3.5x faster scans" and
  the v0.8.1 "50–100x" estimate to the measured ~5–85x.
- **Fast-path confidence no longer hardcoded `'high'`.** FTS-only fast-path hits
  skip the vector scan, so semantic closeness is unverified; they are now tagged
  `confidence='medium'` to match the banding discipline used by hybrid recall.
- **`_decay` closure hoisted** out of the inner conditional in the recency
  rework, so it is defined once per call instead of per-branch (A3 robustness).

## [0.8.1] - 2026-08-14

### Added
- **Third-party codebase audit-driven patch** (v0.8.1). Implements the ten
  highest-ROI fixes from the audit (part H1) plus their directly-coupled tests:
- **NumPy cosine rewrite.** `EmbeddingsEngine.cosine` now uses `numpy.dot`
  (with a pure-Python fallback when numpy is absent), giving a ~5–85x
  measured speedup on the brute-force vector scan. int8 payloads are upcast
  to int32 (int16 would overflow in just 2–3 dims) before the dot product to
  avoid silent int8 overflow; int8's value is 4x smaller on-disk storage,
  not scan speed — float32 is the faster scan path.
- **Strict vector dimension checks in cosine.** Mismatched payload lengths
  (mixed int8/float32, corrupted blobs, stale embedding formats) are logged and
  scored 0.0 instead of silently truncated into a plausible-but-wrong cosine
  (A7/A12).
- **`--action check` speedup:** new `idx_documents_url_fetched` index makes
  recency lookups O(log N) instead of full scans (B7).

### Fixed
- **`verify_claim` long-claim verbatim detection (A1).** The 60-char prefix
  fragment is replaced by a sliding 60-char window across the whole normalized
  claim, so a claim whose distinctive portion is not its first 60 chars still
  verifies.
- **`verify_claim` "partial" false positives (A2).** "Partial" now requires the
  top FTS5 hit to be a strong BM25 match (rank < -2.0); co-occurrence of
  generic words in unrelated boilerplate is no longer reported as partial.
- **`verify_claim` LIMIT-100 false negatives (A8).** The verbatim LIKE
  candidate query no longer truncates candidates to the first 100 rows.
- **`backfill_vectors` stale-dim detection (A5).** The dimension probe now
  counts ALL wrong-length rows rather than sampling one — an interrupted
  earlier backfill can no longer leave stale vectors silently in place.
- **FTS fast-path recency weighting (A3).** When `fts_fast_path` fires, recency
  weighting (`recency_half_life_days`) is applied uniformly; a 2-year-old
  keyword match no longer ranks above a fresh page of identical text.
- **Hybrid search with punctuation-only queries (A6).** When the query has no
  FTS tokens, the vector scan still runs instead of returning `[]`.
- **Parallel ingest pipeline deadlock + sentinel race (A11, E2).** Work-queue
  sentinels replace the old result-queue / `join()` dance, and results are
  consumed concurrently so batches larger than the bounded queues can't hang.
- **Fail-closed network preflight (C1).** A preflight error (DNS/TLS/5xx) now
  aborts the fetch instead of proceeding by default.
- **BLAKE2b-64 filename suffixes (C2, C6).** Replaces the 24-bit MD5 suffix
  (50% collision odds near ~4k URLs) and sanitizes illegal filename characters.

### Changed
- **CLI migrated to argparse (D3).** `--help`, type validation, loud rejection
  of unknown/typo'd flags (`--recal` -> error, was silently ignored), no more
  80-line manual `sys.argv` loop. Legacy positional-URL calls are unchanged.
- **`EmbeddingsEngine._vectorize_dense` falls back to pure Python when numpy
  is unavailable** instead of failing the import.
- **README** documents the realistic test count (75) and corrects the "zero
  global mutable state" claim (ConfigManager is a process singleton).

### Tests
- New direct coverage for: cosine dim-mismatch/int8-ordering/zero-norm (E1),
  long-claim verbatim verification (E3), stale-dim mid-vault detection, the
  parallel ingest pipeline (E2), fast-path recency reordering (A3), and a
  subprocess CLI smoke suite (E9).

## [0.8.0] - 2026-08-13

### Added
- **16 KB SQLite page size for new vaults (`storage.page_size`, default 16384).**
  A 384-dim float32 vector fits inline in one page with no overflow, making
  vector scans ~1.7x faster than the 4 KB default. Existing vaults are
  untouched; `--action check --migrate` rewrites them via `VACUUM INTO`
  (data-preserving, idempotent).
- **`--action search --mode fast|hybrid`.** CLI ergonomics to force FTS-only
  (`fast`) or guaranteed vector+RRF fusion (`hybrid`) on search, independent of
  config.
- **`[embeddings] quantize = "int8"`.** Dense vectors can be stored as signed
  8-bit instead of float32 — ~4x smaller vault storage with a tiny recall
  cost. Cosine handles both formats; `verify_vault`/backfill track the
  expected byte width.
- **`[embeddings] fts_fast_path` (default true).** Strong-signal fast path: when
  FTS5's all-term AND match alone fills the requested result set, the vector
  scan is skipped and hits are tagged `retrieval='fts_fast'`. Set `false` to
  always fuse.
- **`[embeddings] recency_half_life_days` (default 0 = off).** Optional recency
  weighting in RRF (`rrf *= 0.5 ** (age_days/half_life)`) promotes freshly
  ingested sources over stale ones.
- **Benchmark harness** (`tools/bench_vector.py`, `make bench`): sweeps page
  size × float32/int8 to measure brute-force cosine latency as the vault grows —
  the data behind future HNSW/sqlite-vec scaling decisions.

### Changed
- Version bump to `0.8.0`.
- Dropped the "HCH" moniker from all branding, docs, and env vars
  (`HCH_POOL_SIZE` → `HC_POOL_SIZE`, `HCH_WORKERS` → `HC_WORKERS`) — the
  toolkit is referred to simply as **HoardCore**.

## [0.7.0] - 2026-08-13

### Added
- **Per-topic vaults (`--vault NAME`).** A new CLI flag scopes the whole
  session to `hoardcore_data/NAME/`, isolating recall per topic/domain so
  cross-topic "fetch poison" is impossible. Vault name is sanitized against
  path traversal. Default (no flag) keeps the flat `hoardcore_data/vault.db`.

### Changed
- **`network.default_strategy` now defaults to `aggressive`.** Every fetch
  escalates the full aiohttp → curl_cffi → FlareSolverr chain, fixing web
  discovery failing on anti-bot search pages (DuckDuckGo) under the old
  `balanced` default.
- **`verify_claim` verbatim check is whitespace/newline tolerant.** A claim
  whose exact text spans a line break in the stored chunk now returns
  `verified` instead of a false `partial` (SQL pre-filter widens whitespace,
  exact confirmation in Python).

### Fixed
- `documents.parser_used` no longer always reads `"unknown"`; it now falls back
  to the real parser name stored in metadata.
- Parallel ingestion now writes `content_hash` consistently with the sequential
  path (needed for vault integrity checks).

## [0.6.0] - 2026-08-13

### Added
- **Embedding model upgraded to `BAAI/bge-small-en-v1.5`** (384-dim, ~62 MTEB,
  0.067 GB ONNX) — a stronger default than the previous `all-MiniLM-L6-v2`.
- **Content-addressed chunk storage.** Chunks are hashed with BLAKE2b-256 into a
  `chunks_ca` canonical table; identical chunk text across documents is stored
  once and embedded only once (`chunk_vectors_ca` cache), enabling cross-document
  deduplication.
- **WORM (write-once-read-many) document semantics.** Re-ingesting the same URL
  appends a new `version` row (`UNIQUE(url, version)`) instead of overwriting the
  previous one — the vault is now append-only. Existing vaults are auto-migrated.
- **SQLite connection pool** (`ConnectionPool`, 8 connections) with WAL + memory
  mmap + page-cache tuning, replacing the open-a-new-connection-per-query path.
- **`--action check`** — a three-phase vault integrity check (document chunk
  counts, content-hash verification, vector-dimension verification), CI-wireable
  via exit code.
- **Optional parallel ingestion** (`[indexer] parallel = true`) — a threaded
  reader→embed→write pipeline for large batches, gated off by default.

### Changed
- Vector storage now deduplicates across documents via the content-addressable
  hash, so re-ingesting similar pages no longer grows storage linearly.

## [0.5.0] - 2026-08-13

### Added
- **Dense retrieval is now the default (`[embeddings] mode = "dense"`).** Uses an
  ONNX-quantized sentence-transformers model (`all-MiniLM-L6-v2`, 384-dim) via
  `fastembed` on `onnxruntime` (no PyTorch, no GPU). `fastembed` moved into the
  core dependency set (Makefile + `pyproject.toml`), so semantic retrieval works
  out of the box.
- **Resumable dimension-migration.** `backfill_vectors` now recomputes
  mismatched-dimension rows in place (no destructive DELETE-all), so switching
  `sparse` <-> `dense` is interrupt-safe.
- **`--action verify` programmatic provenance audit.** `hoardcore.py _ --action
  verify --claim "<claim>"` confirms a claim against the vault: returns
  `VERIFIED` (verbatim match), `PARTIAL` (real keyword coverage, not verbatim),
  or `UNVERIFIED` (no vault support), with CI-wireable exit codes 0/1/2. Makes
  the `[V]` honor-system tag machine-checkable.

### Changed
- **Dense ONNX semantic retrieval is now the default experience.** HoardCore keeps
  its single-file, no-PyTorch/no-GPU, easy-setup ethos while making dense
  retrieval the standard path; the sparse FNV-1a hash remains only as an
  automatic fallback when `fastembed` is unavailable. README, config comments,
  and `Makefile` updated to drop the previous "zero-dependency" framing.
- `hoardcore.toml` documents `embeddings.mode` (default `dense`), `dense_model`,
  `conf_high_abs`, `conf_low_abs`.

### Fixed
- **Retrieval-confidence bands now actually discriminate.** The previous
  implementation tagged hits `high`/`medium`/`low` by their score *ratio to the
  top hit* — but RRF scores cluster tightly, so broad/weak queries still read as
  all-`high` (bottom hit ≈ 0.9× top). Confidence now uses a combination of
  (a) whether a hit matched **both** the FTS5 keyword list and the vector list,
  and (b) the **absolute** fused score (`conf_high_abs` / `conf_low_abs`),
  calibrated against the observed RRF scale (strong result sets top out near
  0.032; weak vector-only sets near 0.016). Specific queries now score `high`;
  vague/off-topic queries score `medium`/`low`.

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

### Rebrand: HoardCore-RAG (HCRAG) → HoardCore
- Renamed from **HoardCore-RAG** to **HoardCore** — an *Agent Harness for
  Retrieval & Deep Research*, not a RAG library. The HCRAG acronym is retired;
  the harness is now branded simply **HoardCore** everywhere.
- Branding updated across `README.md` (fully rewritten in a template format,
  Design Decisions section removed), `skill.md`, `AGENTS.md`,
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