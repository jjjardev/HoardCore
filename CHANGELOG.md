# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## HoardCore v0.12.2

### Fixed
- **`audit` joins quotes wrapped across physical lines.** The gate previously
  scanned artifacts one physical line at a time, so a double-quoted passage a
  markdown editor wrapped over two lines had no complete `"…"` span on either
  line and fell back to whole-line prose — condemning naturally-written
  artifacts to `UNVERIFIED` (observed live: the Morocco synthesis scored 10%
  purely from ~80-col wrapping; re-formatted single-line quotes scored 100%).
  `audit_artifact` now groups consecutive non-blank lines into logical units
  (continuing a unit while a quote is still open via `_unclosed_quotes`, which
  tracks straight and curly quotes) before claim extraction.
- **`audit` attributes each `[V#N]` to its own quote.** On a line carrying
  several tags, every tag previously inherited the line's *longest* quote, so a
  paraphrased claim could hide behind a co-tagged verbatim quote. Each tag now
  audits the double-quoted passage ending nearest before it
  (`_claim_text_for_tag`); with no preceding distinctive quote it falls back to
  the whole line. Post-fix the Morocco report splits into 20 per-quote claims
  (was 11 under longest-quote collapsing) and the ERCOT stress-test report
  audits 16/16 = 100% with quotes deliberately wrapped across lines.

### Docs
- **`skill.md`:** new "Quote handling (read before writing artifacts)" block
  under the `audit` action — quotes may span physical lines, each tag is
  attributed to its own nearest preceding quote, and the whole-line fallback is
  strict (paraphrased lines usually `UNVERIFIED`).
- **`README.md`:** the `audit` VERBATIM bullet documents wrapped-quote joining
  and per-tag attribution.

### Tests
- `tests/test_audit.py` grows 10 → 17: wrapped quotes across 2 and 3 physical
  lines verify; a wrapped paraphrase stays `UNVERIFIED`; `_logical_lines` and
  `_unclosed_quotes` unit tests; per-tag attribution (a paraphrase can no
  longer hide behind another tag's verbatim quote); nearest-quote precedence.
- Live stress test: fresh `ercot_bess` vault (2026 ERCOT battery storage) ran
  research → check (90 checks PASS) → audit 100%, exercising the fixed path
  end-to-end.

## HoardCore v0.12.1

### Added
- **CLI exception safety net.** `main()` is now a thin wrapper around the
  renamed `_main_impl()`: `KeyboardInterrupt` exits `130`, `SystemExit` is
  re-raised, and any unexpected exception exits `2` with a clean one-line
  message instead of a raw traceback — so the CLI contract (exit codes 0/1/2)
  holds even for unforeseen action bugs.
- **`audit` test coverage (10 unit + 5 CLI tests).** Verbatim-quote pass,
  paraphrase `UNVERIFIED`, whole-line claims, unmapped tag, bare `[V]` not
  mapping-checked, not-ingested URL, tag dedupe, skip source-links block, empty
  vault, `--artifact`/missing-file exit codes, and the safety net above. The
  audit feature itself shipped in v0.12.0; this release proves it.
- **CI quality gates.** `.github/workflows/ci.yml` now runs bandit (security),
  pyright (type check), ruff, and pytest with `--cov-fail-under=66`. Local
  equivalents via `make lint / audit / typecheck / coverage / check`. Test
  deps (`pytest-cov`, `bandit`, `pyright`, `ruff`) live in the `test` extra.

### Fixed
- **Type check clean (40 pyright findings → 0).** Real defects surfaced and
  fixed: `resolve_artifact_out` crashed with `artifacts_by_day=false` and no
  `out_path` (now falls back to a suffixed default name); the probe-query
  builder sliced a `set` (`sorted(seen)`) when no candidates survived; aiohttp
  `timeout=` kwargs are now explicit `ClientTimeout` objects. The rest were
  type-honesty fixes: lazy `_np`/`curl_requests`/binary-parser imports are
  now `None`-typed and guarded, `strategy` resolution is `str()`-coerced in
  `research`/`fetch`, `_normalize_fetch` takes/returns `tuple[Any, ...]`,
  `_try_plugin_fetchers` no longer lies about returning `None`, `yarl.URL`
  is used for redirect joins, and `import lxml.etree` resolves cleanly.
- **Bandit clean.** The 9 `B608` findings were false positives (all `?`
  placeholder-bound); each f-string SQL is now either assigned to a `sql`
  variable with `# nosec B608` or annotated inline, and `curl_requests` gets
  an explicit `None` fallback import instead of a bare `assert`.
- **`_vectorize_dense` no longer crashes on a `None` embedding backend** and
  `embed_jobs` skips rows whose `lastrowid` is `None`.

## HoardCore v0.12.0

### Added
- **`audit` action — the execution-provenance gate.** `--action audit --artifact
  PATH` audits a synthesis artifact's `[V#N]` evidence chain, closing the gap the
  v0.11.1 stress test exposed: a claim-level `verify` alone never proves a tag
  maps to a listed, ingested source. For every `[V#N]` tag it checks three links:
  **VERBATIM** (the claim sentence — or its longest inline double-quoted passage —
  verifies against the vault), **MAPPED** (`N` appears in the artifact's Source
  Links / Citations block as `[#N] <url>`), and **INGESTED** (the cited URL has
  chunks in the vault). A bare `[V]` (no `#N`) is verified but not
  mapping-checked. Reports a per-claim table plus citation-accuracy %, then
  exits `0` verified / `1` partial / `2` (any unverified, unmapped, or
  not-ingested link). Strictness mirrors `verify`: only verbatim-quoted passages
  pass; paraphrased prose is `UNVERIFIED`.
- **Grounding contexts get their own subfolder.** `storage.grounding_subdir`
  (default `grounding`) routes research EMITs to
  `artifacts/YYYY-MM-DD/grounding/grounding_context[_N].md`, so the working
  instrument no longer pollutes the day folder of finished syntheses/audits.
  The `_N` no-clobber suffixing applies inside the subfolder.

### Fixed
- **`audit_artifact` double-counted multi-tag lines.** A line carrying two
  `[V#N]` tags (e.g. two `[V#3]` claims in one paragraph) emitted two identical
  claim rows, skewing `total` and accuracy. Emissions are now deduped by
  (normalized claim, source tag) pair: one line citing `[V#3]` twice counts one
  link, while `[V#1]`/`[V#5]` on the same line stay separate links.

### Docs
- **`README.md`:** added the `audit` action (example, flags, exit codes) and
  the `--artifact PATH` row; documented `grounding_subdir` in the `[storage]`
  config table and the Artifacts section.
- **`skill.md`:** added `audit` to the action table and a full
  "audit — execution-provenance gate" section; noted the grounding subfolder in
  the Artifacts section.

### Tests
- `test_default_grounding_context_is_suffixed_not_clobbered` updated for the
  `grounding/` subfolder path.

## HoardCore v0.11.1

### Added
- **`verify --claim-list FILE` batch audit.** Verify a file of claims (one per
  line; `#`/blank lines skipped) in one run and get a per-claim verdict table
  plus an aggregate **citation-accuracy %** (VERIFIED ÷ total). Exit code is
  the worst verdict (`0` all verified / `1` any partial / `2` any unverified),
  so a CI job can enforce "zero hallucinated citations" as a measured number.
  Mutually exclusive with `--claim`/`--claim-file`.
- **Execution-provenance in recall.** Every recalled chunk now carries its
  exact storage `chunk_id` (`source_url`, `chunk_id`, and `retrieval` mode) in
  its metadata across the FTS-only, `fts_fast`, and hybrid paths, and the
  research grounding artifact lists each chunk's id plus the `--discover` /
  `--recall` run budget — so a `[V]` claim is replayable to the exact chunk
  that grounded it.

## HoardCore v0.11.0

### Added
- **Source-diverse research recall.** The `research` action's hybrid recall now
  caps chunks per source URL (`research.max_per_source`, default `2`, `0` =
  unlimited) so a single rich page can't crowd out every other source. A new
  `VaultManager._diverse_order` rebalances the RRF-ranked list; the top-ranked
  hit is always kept, so relevance stays first while the set spans more distinct
  sources (directly helps hit the DeepResearch distinct-source quota). Wired into
  both the answer-first and post-DISCOVER recall paths; plain `search` is
  unchanged (unlimited by default).

### Changed
- **Markdown-tidy stored chunk text.** Parser-emitted markdown emphasis/code
  markers (`**bold**`, `*italic*`, `` `code` ``) are now stripped when chunks are
  built, so the vault's canonical text reads as clean prose. This mirrors the
  verifier's own marker-strip, so `[V]` fidelity is unchanged; newlines and
  ``` code fences (and their contents) are preserved.
- **Config banner tracks the module version.** The generated `hoardcore.toml`
  header is now `# HoardCore v{__version__}` instead of a hardcoded string, so
  it can no longer drift from the engine version.
- **Grounding label clarified.** The artifact heading is now "Distinct sources
  in recall" (was "ingested") — the count refers to the recall set, not the
  vault's total ingested sources.
- **`pyproject.toml` version aligned** to the engine version (was stale at
  0.9.9 while the module had moved to 0.10.x).

### Docs
- **`README.md`:** added a "Quick Start for Humans" onboarding section
  (AI-agent-assisted and manual paths) with an example query; corrected the
  fetch-chain description to concurrent (aiohttp ∥ curl_cffi) rather than
  serial; documented `filter_low` (keeps one `low` per distinct source) and
  `max_per_source`; added a precise `verify` "lenient vs strict" breakdown with
  an explicit "a denial is not a falsification" callout.
- **`skill.md`:** `filter_low` now described as "keeps one low per source";
  added the `max_per_source` recall note; clarified that `PARTIAL`/`UNVERIFIED`
  ≠ "false".

### Tests
- `tests/test_network.py`: research mock updated to accept the new
  `max_per_source` keyword. Full suite passes (143), ruff clean.

### Notes
- Verified live on a 254-chunk multi-source vault: research recall went from
  10 chunks / 3 distinct sources to 10 chunks / 6 distinct sources with the top
  hit unchanged.

## HoardCore v0.10.2

### Added
- **`--parallel` / `--no-parallel` CLI flag.** Threaded ingest could previously
  only be toggled via the `indexer.parallel` config value. It can now be forced
  per-run from the CLI (`--parallel` on, `--no-parallel` off). The override is
  in-memory only (never written to `hoardcore.toml`) and engages the parallel
  reader→embed→write pipeline for batches of 8+ chunks; smaller batches fall
  through to the sequential path (silent no-op). Uses
  `argparse.BooleanOptionalAction` so both forms are accepted.

### Fixed
- **Anti-bot fetch latency.** The `balanced`/`aggressive` strategy chains ran
  aiohttp → curl_cffi → FlareSolverr *serially*, so when aiohttp was
  anti-bot-blocked you paid a full curl_cffi round-trip before FlareSolverr.
  aiohttp and curl_cffi now run **concurrently** (`asyncio.gather`); the first
  leg that returns content wins, and the anti-bot 404-disguise case is rescued
  (a curl 200 beats an aiohttp 404 body). FlareSolverr remains a serialized
  terminal leg. Both legs still SSRF-validate independently, so no security
  semantics change.

### Changed
- **Docs made factual (`README.md`, `skill.md`, `fetch()` docstring).** The
  `--mode hybrid` behavior is now described accurately: with
  `embeddings.fts_fast_path=true` (default) `hybrid` still short-circuits to the
  FTS fast path when FTS5 alone fills the result set (hits tagged
  `retrieval='fts_fast'`). Added the `--parallel` flag to the README CLI table,
  corrected the `skill.md` "Expected Behaviors" note that previously claimed
  parallel ingest had no CLI flag, and documented the `--parallel`/`--no-parallel`
  forms in the CLI help.

### Tests
- `tests/test_cli.py`: `test_cli_parallel_flag_parses_on_off` (parser yields
  `True`/`False`/`None`) and `test_cli_parallel_flag_runs_check` (flag accepted
  end-to-end through `main()`).
- `tests/test_network.py`: `test_aggressive_picks_curl_when_aiohttp_soft404s`
  (anti-bot 404 rescue) and `test_aggressive_runs_aiohttp_and_curl_concurrently`
  (concurrent legs, neither skipped).

### Notes
- Full suite passes (143), ruff clean. `--parallel` verified live on a 119-chunk
  Wikipedia page (343/343 vectors aligned after ingest).

## HoardCore v0.10.1

### Fixed
- **Parallel ingest deadlocked on large documents.** `ingest_chunks_parallel`
  fed all work into a bounded `work_q` *before* draining the bounded `result_q`.
  When embeddings were non-trivial (real latency, not instant test stubs), the
  workers filled `result_q`, blocked on `result_q.put()`, and stopped consuming
  `work_q`, while the main thread blocked on `work_q.put()` — a deadlock that
  hung the CLI indefinitely. Triggered by any batch larger than the queue budget
  (20) + worker threads (4), e.g. a long Wikipedia article with
  `indexer.parallel=true`. Found by the lunar-programs 2026 live stress test;
  reproduced deterministically with a 120-chunk slow-embed batch.
  `ingest_chunks_parallel` now drains `result_q` concurrently with feeding
  `work_q` via a reader thread started before any feeding, preserving the
  bounded queues and the sentinel shutdown.
- **New regression test.** `test_parallel_ingest_large_batch_slow_embed_does_not_deadlock`
  uses 60 chunks + a deliberately slow (50 ms) `vectorize` so the race is
  deterministic; it hangs on the pre-fix code and passes after. Verified live:
  an 83-chunk Wikipedia page now ingests via the parallel path with vectors
  staying 1:1 with chunks.

### Notes
- Fix + test only; full suite passes (139), ruff clean. Found and fixed during
  the SMR/lunar 2026 engine stress tests.

## HoardCore v0.10.0

### Fixed
- **Parallel-ingest vector misalignment (B1).** The near-duplicate filter ran
  *after* the embed pipeline, so when a chunk was collapsed the vectors written
  back no longer matched the chunk rows; the filter now runs before embedding so
  results stay index-aligned.
- **`verify --recall` was ignored (B2).** The verify action called
  `verify_hint(claim, recall=5)` unconditionally; it now honors `--recall`.
- **`document_exists` treated `ttl_seconds <= 0` as always-expired (B3).**
  Config with a zero/negative TTL (explicit "never expire") was misread; now
  `<= 0` means never expire, matching the README contract.
- **Cached re-ingest dropped cached chunks (B4).** `_ingest_many` on
  `meta['cached']` re-parsed the URL instead of serving the vaulted chunks.
- **Discovery plugins appended *before* the engine's own search (B5).** Custom
  providers shadowed built-in discovery results; they are now a fallback tail.
- **`_near_duplicate_candidates` SQL-bind overflow (B6).** Large chunk batches
  exceeded SQLite's 999-variable limit (OperationalError) on URL/key sets;
  batches now respect `_MAX_SQL_VARIABLES = 900`, falling back to a full-table
  scan for the residual set.
- **robots.txt without a Sitemap line skipped discovery (B7).** A 200 response
  with no `Sitemap:` field was treated as authoritative and discovery stopped;
  it now probes `/sitemap.xml` before giving up.
- **Broken pooled connection leaked (S5).** `ConnectionPool.get()` returned a
  new connection but left the broken one unclosed; it now closes the dead
  connection first (no fd/WAL leak).
- **Repeated manual deletes wiped all versions' chunks (S6).** A leftover
  `documents_after_delete` trigger fired on *any* row delete; replaced with a
  one-shot `DROP TRIGGER IF EXISTS`.
- **PDF file handles leaked on parse errors (S7).** `parse_pdf` now closes the
  document in a `finally` block.
- **`VACUUM INTO` crashed on paths with quotes (S8).** The backup filename is
  now single-quote-escaped.
- **`--parallel 0` deadlocked (S9).** Both `_ingest_many` and `_crawl_domain`
  created `asyncio.Semaphore(parallel_workers)`; now `max(1, parallel_workers)`.
- **Module docstring pinned an old version (S12).** Removed the stale version
  string and the dead `random.seed(0)`/`import random`.

### Security
- **Preflight SSRF refusal is now a hard error (S3/S4).** `fetch()` raises
  `RuntimeError("SSRF_BLOCKED")` instead of returning a partial/odd result, and
  `main()` catches `SSRF_BLOCKED`, `CF_COOKIE_EXPIRED`, and `FETCH_FAILED` to
  exit 2 with a clean message (no traceback) — matching the aiohttp per-hop
  re-validation semantics documented in `README.md`/`skill.md`.

### Changed
- **`search()` plugin providers become a fallback tail (B5)** — see above; sync
  and async callables both supported (`inspect.isawaitable`).

### Tests
- New `tests/test_regressions.py` (8 regression tests covering B1, B2, B3, B4,
  B5, B6, S1, S5).
- `tests/test_cli.py`: isolated-tmp isolation for argument-validation tests via
  `_isolated_toml()`; new tests for `verify` on unverified claims (exit 2) and
  SSRF-blocked `scrape` (exit 2, no traceback).
- `tests/test_vault.py`: fixed a trivial-pass test that used `assert 1 == 1`-style
  querying; dense-model tests now skip cleanly when no dense backend is installed.
- `tests/test_network.py`: `test_default_strategy_is_aggressive` no longer
  depends on ambient config files.

### Docs
- `skill.md` + `README.md`: SSRF re-validation wording corrected (aiohttp
  re-validates every redirect hop; curl_cffi/FlareSolverr re-validate the final
  URL only).
- `peel_anchor/`: doc honesty pass — corrected stale runtime figures
  (`200→0.31ms … 6400→15.7ms`), the active-scales bound (`|active| = k or k+1`,
  not `n_peels`), retracted the unreproducible "widthcap fails ~54%" claim
  (0/2000 at shipped settings, vacuous saturation), fixed the stale `max()`
  glue description in `benchmark.py` exp F, and surfaced the falsified
  anchor-aligned hypothesis (exp D: ratios 0.09–0.23) as an explicit negative
  result.

### Notes
- Engine bugfix + test-hardening release; supersedes the stale
  `README.md` badge (0.9.9 → 0.10.0).

## HoardCore v0.9.10

### Fixed
- **Wrong third author cited for arXiv:2507.22486.** The deterministic
  near-linear LCS approximation is by **Boneh, Golan, and Kraus** (Bar-Ilan);
  the repo cited `Krauthgamer` in six places (`peel_anchor/peel_anchor_lcs.py`,
  `peel_anchor/README.md`, `deterministic_single_pass_edit_distance_sketch.md`,
  root `README.md`) and misspelled it as `Krauthgammer` once. A third-party
  review flagged this; web + vault (`cshard`) cross-check confirmed the correct
  author is Matan Kraus. All six citations corrected; the distinct real author
  `Andoni–Krauthgamer` (FOCS'07) references are untouched.

### Notes
- Documentation/citation fix; no `hoardcore.py` engine behavior changed.

## HoardCore v0.9.9

### Fixed
- **`--urls` ignored by `scrape`/`crawl`.** The CLI placeholder `_` leaked into
  the fetch and got refused as `SSRF_BLOCKED`; `--urls` was dropped because
  `main()` only forwarded it for `ingest`. `fetch()` now routes explicit
  `--urls` through the batch ingester for scrape/crawl/ingest, and `main()`
  forwards them unconditionally.
- **`--discover 0` silently became 5.** `main()` used `discover or 5`, so the
  documented recall-only mode never actually skipped the web. Now uses
  `discover if discover is not None else 5`.
- **`--limit` was a no-op for `search` and `discover`.** `search` always used
  the config `indexer.search_limit`, and the discovery ingest count came from
  `discovery.top_rank` regardless of the CLI. Both now honor `--limit`
  (falling back to config when unset), and the discovery pool is sized to the
  requested count.
- **Crawl cache hits returned nothing.** `_crawl_domain` served an empty list
  on a cache hit while `_scrape_single` served the vaulted chunks; the crawl
  path now returns the cached chunks too.
- **`filter_low` could strip a source entirely.** The EMIT filter dropped every
  `low`-banded chunk, so a low-scoring but authoritative primary source could
  vanish from the deliverable while secondary blogs survived. `_drop_low_confidence`
  now keeps at least one chunk per distinct source, and the grounding file's
  drop note counts only chunks actually removed.
- **Markdown `**bold**` artifacts broke `[V]` verification.** Parser-emitted
  emphasis markers stored in chunk text made verbatim quoting of an artifact
  sentence return `PARTIAL`/`UNVERIFIED`. `normalize_claim` now strips
  `**`/`*`/backtick render markers from both the claim and the stored text, and
  the verify `LIKE` pre-filter widens across them.

### Added
- **Hybrid Discovery guidance in `skill.md`.** New section + workflow bullet
  documenting the harness-agnostic pattern (prime the hunt, fill discovery gaps,
  pre-flight URLs, rescue blocked fetches), with OpenCode's `webfetch`/`websearch`
  as the named example — the vault stays the source of truth.

### Changed
- **Docs integrated with the flag fixes** (`README.md`, `skill.md`): action and
  flag tables now describe batch `--urls`, `--limit`, and `--discover 0`
  correctly; the README test count is no longer hardcoded.
- **`_drop_low_confidence` retained-source accounting** — the grounding file's
  `filter_low` note reports only chunks actually dropped, not all low hits.

### Notes
- 10 new regression tests across `tests/test_network.py` and
  `tests/test_vault.py`; full suite green (128 passing). Both fixes re-verified
  live in a second end-to-end stress run (fresh vault + topic).

## HoardCore v0.9.8

### Fixed
- **CI lint on the committed `peel_anchor/` scripts.** The in-repo research
  files failed `ruff check .` (42 errors: `zip()` without `strict=`, semicolon
  statements, ambiguous `l` names, nested `if`s, unused variables, missing
  trailing newlines). All fixed so the repo lints clean and CI passes.
  Re-verified the scripts still validate (`peel_anchor_hybrid.py`:
  0/2000 certificate violations) and all 118 tests pass.

### Notes
- Research-script lint fixes only; no `hoardcore.py` engine code changed.

## HoardCore v0.9.7

### Changed
- **Changelog header format.** All release headers now read
  `## HoardCore vX.Y.Z` (version name only, dropping the `[X.Y.Z] - date`
  form) for consistent naming across the file.

### Notes
- Documentation-only; no `hoardcore.py` engine code changed.

## HoardCore v0.9.6

### Added
- **In-repo reproducible research (`peel_anchor/`).** The deterministic-LCS
  research that backs the "Stress-tested on frontier problems" claim is now
  committed and tracked at the repo root — `peel_anchor_*.py`,
  `sample_round_lcs.py`, `benchmark.py` + results, the original
  `cs_hard_problem_novel_idea.md`, and the new
  `deterministic_single_pass_edit_distance_sketch.md` (the 6-pass HARDORE-loop
  deliverable: a deterministic single-pass ED sketch with `[V]/[E]/[H]`
  provenance, falsification experiments F1–F4, and a benchmark spec). Every
  script self-validates (`peel_anchor_hybrid.py`: sound-by-construction,
  0/2000 certificate violations), so the claims are provable, not asserted.
- **README** documents the `peel_anchor/` directory, the reproduction commands,
  and the FlareSolverr requirement (now a required dependency for full
  anti-bot coverage, since most sites gate content behind a challenge).

### Notes
- `hoardcore.py` engine code is unchanged in this release; the version bump
  tracks the README rewrite + committed research artifacts.

## HoardCore v0.9.5

### Fixed
- **Stats confidence probe sampled generic headers.** `confidence_distribution`
  (used by `--action stats`) took the first 40 header phrases, which on some
  vaults are generic single-word labels (`Production`, `Farmers`, `History`) —
  those match too broadly to be keyword-backed, so the probe reported a
  misleading all-`medium` distribution even when real topical recalls spread
  normally. The probe now prefers the deepest, longest (multi-word,
  keyword-dense) header segments and skips generic labels, so the histogram
  reflects genuine retrieval health.
- **`--help` crash on `%` in help text.** The `--claim` help contained a
  literal `%`, which argparse's `%-formatting` tried to interpret as a format
  character, raising `ValueError` on `--help`. Escaped as `%%`.

### Added
- **Fresh-agent "Read First" guidance.** A new prominent block at the top of
  `skill.md` documents the by-design behaviors that can look like bugs to a
  new agent session — `PARTIAL`/`UNVERIFIED` means reword, `%` vs "percent"
  are distinct tokens, `$` needs `--claim-file`/escaping in shells,
  set-relative confidence, `filter_low` drops, and grounding counts below
  `--recall N`. The `--claim` CLI help now carries the same warnings, so a
  fresh agent reading either `skill.md` or `--help` is not misled.

### Notes
- Regression coverage for the probe-selection fix. Full suite green.

## HoardCore v0.9.4

### Fixed
- **Currency-token alignment in FTS.** The vault's `porter unicode61` tokenizer
  treats `$` as a separator, so `$13` is indexed as the bare token `13` — but
  `_fts_query` built phrases that kept the `$`, so FTS keyword/OR-fallback
  matching could silently miss currency figures even when their text was
  stored. `_fts_token` now reduces a `$` directly followed by digits
  (e.g. `$13`, `$21.3`, `$1`) to its digit-only form for the FTS phrase, so the
  keyword signal and the OR-fallback guard agree with the index. `verify`'s
  raw-text `LIKE` still confirms the verbatim `$13` for `[V]` (exact phrasing
  preserved).

### Added
- **`--keep-low` for research.** By default `filter_low` still drops
  low-confidence hits at EMIT (the documented hygiene), but exhaustive/deep
  hunts that want the full evidence tail can pass `--keep-low` to retain
  low-confidence chunks in the grounding context.
- **`--claim-file` for verify.** Read the claim from a file instead of
  `--claim`, so characters like `$` survive the shell intact (bash would
  otherwise expand `$13` to empty). Also documented: escape `\$` in `--claim`.

### Notes
- Regression coverage: currency-token alignment, verbatim `$13` verification,
  and `keep_low` retention. Full suite green.

## HoardCore v0.9.3

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

## HoardCore v0.9.2

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

## HoardCore v0.9.1

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

## HoardCore v0.9.0

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

## HoardCore v0.8.4

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

## HoardCore v0.8.3

### Added
- **Regression test for fast-path confidence (v0.8.2 test gap).** New
  `test_fts_fast_path_confidence_is_medium_not_high` asserts every FTS-only
  fast-path hit is tagged `confidence='medium'` (vector scan skipped, so
  semantic closeness is unverified) — closing the gap that would have let the
  dishonest `'high'` silently return. Test-only release; no behavior change.

## HoardCore v0.8.2

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

## HoardCore v0.8.1

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

## HoardCore v0.8.0

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

## HoardCore v0.7.0

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

## HoardCore v0.6.0

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

## HoardCore v0.5.0

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

## HoardCore v0.4.0

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

## HoardCore v0.3.1

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

## HoardCore v0.3.0

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

## HoardCore v0.2.2

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

## HoardCore v0.2.1

### Changed
- DeepResearch is now the **default research mode**. The bounded Hardcore
  Research Loop (with `[V]`/`[E]`/`[H]` provenance and adversarial audit)
  triggers on any open-ended research request; the former `autoresearch`
  trigger word is redundant and may be omitted. Feature renamed `autoresearch`
  → **DeepResearch** (depth presets are now `research`, `research deep`,
  `research exhaustive`, and the `x N` cap).

## HoardCore v0.2.0

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

## HoardCore v0.1.0

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