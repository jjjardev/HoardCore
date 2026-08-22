---
name: hoardcore
description: "HoardCore — a research toolkit and memory protocol for AI agents: a single-file Python module (SQLite vault + CLI) that scrapes, crawls, and searches the web into a persistent SQLite vault, with hybrid FTS5 + vector retrieval and Cloudflare-aware fetching. Use when the user asks you to research, scrape, crawl, summarize, or search text-based content from the web, or to build a persistent knowledge base. Research is DeepResearch by default: every investigation runs the bounded Hardcore Research Loop (DISCOVER -> INGEST -> RECALL -> EMIT with [V]/[E]/[H] provenance); optional depth presets ('deep', 'exhaustive') or an 'x N' pass cap control how deep it goes."
---

# HoardCore — Agent Operating Manual

You are an expert user of HoardCore (single-file Python module + SQLite vault + CLI) that hoards the web into a local SQLite vault. Be a relentless, adversarial analyst: **no hallucination, no guessing — hoard evidence.** Humans read `README.md`; agents read this file.

## ⚠️ Expected Behaviors (NOT bugs)

Check these before "fixing" a surprising result:

1. **`PARTIAL`/`UNVERIFIED` ≠ false** — a denial means the vault lacks that wording *verbatim*, not that the claim is untrue. Reword to the source's exact stored words and re-run; never force `[V]`, never bypass with manual SQL. Lenient (never flips): dashes, smart quotes, apostrophes, full-width, whitespace, markdown markers. Strict (denies): word identity/order/presence; `%`≠"percent".
2. **Currency `$`/`₱`/`PHP`/`USD`** — bash eats `$13`; escape `\$13` or use `--claim-file`. FTS tokenizes `$13` as `13`; verbatim `[V]` still confirms literal `$13`.
3. **Confidence is set-relative** — `high/medium/low` rank within a recall set; `low` is rare at small `--recall N`. Absence of `low` is normal.
4. **`filter_low`** keeps one `low` chunk per distinct source at EMIT (all-low → keep all). Grounding may show < `--recall N` chunks — that's the filter, not under-fill. Side effect: a thin authoritative source can band `low` and be thinned to one chunk — use `--keep-low`, larger `--recall`, or domain-pin with `search`.
5. **`--mode hybrid` can still return `fts_fast`** — with `embeddings.fts_fast_path=true` (default), an all-term FTS match short-circuits the vector scan (hits tagged `retrieval='fts_fast'`). Not a bug; set `fts_fast_path=false` to force the vector+RRF path.
6. **Parallel ingest is opt-in, batch-gated** — engages only for batches of **8+ chunks**; `--parallel`/`--no-parallel` override `indexer.parallel` (default on). Small batches: silent sequential no-op.

## DeepResearch (default for open-ended questions)

Any "research / investigate / deep dive / find out about" request triggers the **Hardcore Research Loop** — no trigger word needed; `autoresearch` is obsolete.

**Depth presets:** `research` = 3 passes ≥5 sources · `research deep` = 6 ≥8 · `research exhaustive` = ≤10 ≥15. Raw override `research x N` hard-caps at N passes; only the `x N` form is a literal count — otherwise treat a count as a depth direction.

**Loop:**
1. **Parse** the directive (core concept, entities, relationships).
2. **Hunt** (DISCOVER→INGEST) — prefer high-authority primary sources over blogs; `aggressive` fetch (default) handles anti-bot. **Memory-first:** skip the hunt if the vault already has a high-confidence answer; pass `--no-answer-first` for fresh evidence.
3. **Recall** (hybrid) — 5–10 chunks (`--recall 5..10`); drop flimsy/context-free chunks.
4. **Emit** — grounding context + a synthesis report into `artifacts/` (the report is the deliverable).

```
venv/bin/python hoardcore.py _ --action research --query "<question>" --discover 6 --recall 8
```

### Provenance Mandate (non-negotiable)
Every quantitative claim / date / unique term in a synthesis gets a tag:
- `[V#N]` — **verified verbatim** in vault text from source `#N`; the ONLY tag audit machine-checks.
- `[E]` — external/earlier knowledge, or vault data present only as table/list cells (not contiguous prose). Don't overuse.
- `[H]` — hypothesis/deduction, not stated in sources.

**Tag grammar (decides a clean audit):**
- `[V#N]` appears ONLY immediately after a verbatim `"…"` quote in a **body** paragraph — never in a summary/bullet list, and never on an `[E]`/`[H]` line (analysis/paraphrase can never verify; reference evidence in prose, e.g. "verified in §N above").
- Quotes may span wrapped lines; each `[V#N]` attributes to the double-quoted passage ending nearest before it (tags with no preceding distinctive quote fall back to the whole line).
- No nested double-quotes in a claim — inner quotes (<24 chars) break attribution; rephrase or demote to `[E]`.
- Tabular/list/fiscal data is not contiguous prose → quote only contiguous prose; demote figures to `[E]`.
- Artifact closes with a **Source Links / Citations** block: each `[#N] URL` on its own line with **no `- ` bullet prefix** (the extractor `re.match`es `[#N]` at line start — bullets silently fail MAPPED).
- Cross-source conflicts → keep both verbatim quotes `[V#N]`, flag the discrepancy as `[H]`/`[E]`, never assert either as `[V]`.
- Before tagging: draft, then `verify --claim-list` the exact quote strings; only VERIFIED strings carry `[V#N]`. The audit prints the nearest vault phrase under any failing claim — reword your quote to that exact stored text and re-run.

**Adversarial audit before output:** re-verify every number; unverifiable → demote to `[E]` or strike. Reputation depends on truth.

### Interaction contract
- **Initiation:** state you're running the Hardcore Research Loop at its depth preset.
- **Completion:** artifact path + a **3-bullet executive summary** (sources/citations live in the artifact).
- "go deeper" → one more pass (within cap); "enough"/"done"/"good" → finalize immediately.

### Termination (stop on ANY of)
Answer saturation (2 re-queries, no new `[V]`) · distinct-source quota (≥5/8/15) · diminishing returns (identical re-ranking) · pass budget (3/6/10 or `x N`) · user interrupt (stop/enough/halt/ctrl-c). On early stop from a guard: label `[INCOMPLETE — N passes]`.

### Standard Mode (mechanical ops)
`scrape`/`search`/`ingest` directly (no loop) for "scrape this site", "search the vault", "summarize this PDF". Still prefer DeepResearch for deep, open-ended questions.

## Capabilities
Scrape (one URL: HTML/PDF/DOCX/EPUB) · Crawl (whole site via sitemap) · Search (local vault) · Discover (live DuckDuckGo + ingest top results) · Local (ingest `storage.local_dir`, no network) · Emit (`artifacts/`).

## Hybrid Discovery — HoardCore + your harness's web tools
- **Prime the hunt:** if you already hold the URLs, skip discovery and `ingest --urls "u1,u2"` (or `scrape`/`crawl` with `--urls`) — discovery is for *unknown* sources, not re-finding known ones.
- **Fill gaps:** when discover/research returns nothing or thin/wrong results (rate-limited, niche topic), your harness's web search finds candidates → `ingest --urls` them so they enter the vault.
- **Pre-flight** URLs for ad/tracking redirects, 404s, or paywalls before ingesting.
- **Rescue blocked fetches:** if the chain can't clear a page, your harness's fetch tool reads it directly, then `ingest` the URL so the content is vaulted and verifiable.
- **The vault stays the source of truth:** anything your harness tools find must be ingested before you cite or tag `[V]` — a page only the harness fetched isn't in the vault and can't `verify`.
- **OpenCode:** your `webfetch` (fetch a URL) and `websearch` (live query) tools; other harnesses expose equivalents — same pattern.

## Artifacts
- **Where:** `artifacts/` (`storage.artifacts_dir`), day-sorted `artifacts/YYYY-MM-DD/`. Research EMITs its grounding context into the day folder's `grounding/` subdir (`storage.grounding_subdir`) — a working instrument, not a deliverable.
- **Provenance:** every quantitative claim `[V]`/`[E]`/`[H]`; never claim a number the vault can't support.
- **Citations:** verbatim claims carry `[V#N]`; `[E]`/`[H]` claims carry none; close with a Source Links / Citations block via `hoardcore.citation_list(urls)` or by hand.
- **Helpers:** `hoardcore.write_artifact(name, content)`, `hoardcore.organize_artifacts_by_day()`, `hoardcore.citation_list(urls)`.

## When to Use
Read/summarize/analyze a site, PDF, or doc · build a local knowledge base from a site · find info in previously-ingested content · ingest files dropped into `local_inputs/` (`--action local`) · reach paywall/Cloudflare-blocked sources (resilient fetch chain).

## Available Actions

**Common:** positional `_` for vault-only actions; `--vault NAME` scopes to `hoardcore_data/NAME/` (always a dedicated vault per topic). **Quiet the noise:** add `--log-level error|warning` to commands whose output you read — never pipe `verify`/`audit`/`check` through `tail`/`head` (the shell reports the pipe's exit and masks the gate's; read `$?` or the returncode instead). **Cross-vault read** `--vault a,b,c`: `a` is write-primary; `b,c` are read-only companions — search/verify/audit/hint/hybrid-recall fuse across all; new ingest/discover only touches `a`; a single name or none behaves exactly as before. **Pollution guard:** cross-vault pools every vault you name — only combine one coherent topic; every recalled chunk is stamped `| vault <name>`; drop off-topic hits at RECALL. `--strategy fast|balanced|aggressive` (default aggressive). CLI: `venv/bin/python hoardcore.py _ --action <action> ...`

| Action | Purpose | Key flags |
|---|---|---|
| `scrape` | fetch+index one URL (or `--urls` batch) | `--urls`, `--strategy`, `--force` |
| `crawl` | ingest whole site (or `--urls` batch) | `--urls`, `--strategy`, `--force` |
| `search` | query vault (all named vaults) | `--query`, `--mode fast\|hybrid`, `--limit`, `--vault` |
| `verify` | machine-check a claim / claim list | `--claim`/`--claim-file`/`--claim-list`, `--hint`, `--recall` |
| `ingest` | index explicit URLs (primary vault only) | `--urls` |
| `discover` | web-search + ingest (primary vault only) | `--query`, `--limit` |
| `research` | full loop in one command | `--query`, `--discover`, `--recall`, `--out`, `--vault`, `--no-answer-first`, `--keep-low` |
| `check` | 3-phase vault integrity | `--migrate` (rebuild at 16 KB pages) |
| `stats` | vault summary + confidence probe | `--vault` |
| `audit` | audit an artifact's `[V#N]` chain | `--artifact PATH` |
| `local` | index local files from `storage.local_dir` | `--path`, `--list`, `--force` |

### verify — programmatic audit
- **Cross-vault fold** (`--vault a,b,c`): VERIFIED if ANY named vault holds it verbatim; PARTIAL if any vault is partial; else UNVERIFIED. `--hint` shows the nearest phrase from the best-matching vault.
- **Exact phrasing, typography-blind** — folds en/em dashes, smart quotes, NBSP, full-width; enforces token identity, word order, `%`≠"percent". `PARTIAL`/`UNVERIFIED` = reword to source words; `--hint` prints the nearest phrase.
- **Exit codes (CI-wireable):** `0` VERIFIED (verbatim, sliding 60-char window) · `1` PARTIAL (top all-term FTS5 hit beats the corpus-scaled coincidence floor, no verbatim) · `2` UNVERIFIED. Refuse `[V]` unless `0`. **Never pipe through `tail`/`head`.**
- Escape `\$` in shells or use `--claim-file` for currency.
- **Batch audit** `--claim-list FILE` (one claim/line, `#`/blank skipped): bulk-verifies and reports citation-accuracy % (VERIFIED÷total); exit = worst verdict (any UNVERIFIED → 2) — a CI citation-accuracy gate. Mutually exclusive with `--claim`/`--claim-file`.
- **Execution provenance:** recalled chunks carry `chunk_id` and the grounding file records the `--discover`/`--recall` budget, so a `[V]` replays to its exact chunk.

```
venv/bin/python hoardcore.py _ --action verify --claim "the Epoch doubling time is 6 months" --recall 5
venv/bin/python hoardcore.py _ --action verify --claim-list artifacts/2026-08-18/all_claims.txt
```

### audit — execution-provenance gate
`--action audit --artifact PATH` checks every `[V#N]`: **(1) VERBATIM** — verify vs the vault (`verify` semantics; a bare `[V]` is verified but not mapping-checked); **(2) MAPPED** — `N` appears in the Source Links block as `[#N] <url>`; **(3) INGESTED** — the URL has chunks in **any** named vault (with `--vault a,b,c` a companion still passes). Strictness: only the longest inline double-quoted passage (≥24 normalized chars) passes as a claim; paraphrase is `UNVERIFIED`. Repeated same claim+source tag counts once. Exit mirrors `verify` (`0`/`1`/`2`), plus `2` on any unmapped/not-ingested link. **Never pipe through `tail`/`head`.** Failing claims print the nearest vault phrase for rewording. Authoring rules: see the Provenance Mandate tag grammar above.

### research — full loop
`--discover 0` = recall-only (never touches the web; does NOT fall back to config). `--no-answer-first` forces fresh DISCOVER. `filter_low` (default true) drops duplicate `low` hits at EMIT but keeps one per distinct source; `--keep-low` retains all. `max_per_source` (default 2) caps chunks per source URL for source-diverse recall (0 = unlimited, single-source depth). Cross-vault recall reads *all* named vaults; DISCOVER/ingest writes only the primary. **Distinct-URL ≠ independent source:** syndicated reprints inflate source counts — check for verbatim-duplicate chunks before counting independence (a reprint still grounds `[V]`, just not twice).

```
venv/bin/python hoardcore.py _ --action research --query "<question>" --discover 6 --recall 8 --vault sleep
```

### local — ingest local files (no network)
Reads ONLY `storage.local_dir` (`local_inputs/`, git-ignored); any path outside it is refused. No SSRF/cache-TTL — freshness is **content-based** (unchanged content skipped unless `--force`). Supported: `.pdf .docx .epub .html .htm .txt .md` (recursive). Chunks stamped `local://local/<relpath>` (domain `local`); HTML goes through the boilerplate/junk filter, markdown/text as-authored. After ingesting, `research --discover 0` recalls only over the newly vaulted docs (verify/audit work on them like any source). Cite a local file with its synthetic `local://local/<relpath>` URL in the Source Links block — that is the URL MAPPED/INGESTED checks resolve, not the filesystem path.

```
venv/bin/python hoardcore.py _ --action local --list              # read-only scan
venv/bin/python hoardcore.py _ --action local --path docs/        # ingest local_inputs/docs/
venv/bin/python hoardcore.py _ --action local --force             # re-index even if unchanged
```

### check — integrity
Three phases: doc chunk counts, content hashes, vector dims. Exit `0` pass / `1` fail. Vaults are schema-versioned + embedding-fingerprinted (`embed_fp`) — switching `dense_model`/`dim`/`quantize` never serves stale vectors; dim drift shows here and rows rebuild on next ingest. `--migrate` rebuilds legacy 4 KB-page vaults at `storage.page_size` (16 KB) via `VACUUM INTO` (idempotent). Run before trusting `[V]` on a long-lived vault.

### stats — summary + confidence probe
Sources, doc versions, chunks, vectors, embedding dim/mode, schema version, page size, DB size, and a sampled `high/medium/low` confidence probe (spot retrieval flatness). With `--vault a,b,c` prints a block per named vault.

## Workflow Guidance
1. Single article/paper → `scrape`, then summarize.
2. Docs site → `crawl` (may take minutes).
3. Follow-up question → `search` (instant, no network).
4. Blocked site → `--strategy aggressive` or set `cookie_string` in `hoardcore.toml`.
5. Open-ended question → `research` (the loop), with a dedicated `--vault NAME` per topic (repeat to recall; `--vault a,b,c` to recall several at once — primary gets new ingest, companions read-only).
6. Deliverable → `artifacts/` with `[V]/[E]/[H]` on every quantitative claim.
7. Integrity → `--action check` (`--migrate` once on legacy vaults).
8. Audit → re-verify every quantitative claim before finalizing; unverifiable → `[E]` or remove.
9. Hybrid discovery → harness web tools find candidates; `ingest --urls` before citing.

## Output Format
`fetch()` returns `list[dict]`: `text` (chunk content) + `metadata` (source URL, header path, quality score, parser).

## Important Constraints
- **Text-only** — cannot see images/video (downloads as binary blobs).
- `quality_score` < ~0.1 → likely scanned/garbled.
- **SSRF protection on** (`network.ssrf_protection=true`): refuses private/LAN/loopback/non-http(s); redirects re-validated on every hop/leg (curl_cffi/FlareSolverr re-validate the post-redirect final URL). Ask before disabling for internal targets.
- **Fetch chain** (`balanced`/`aggressive`): aiohttp ∥ curl_cffi concurrently, first leg wins; a curl_cffi `200` beats an aiohttp anti-bot `404`/`403` body; FlareSolverr is the serialized terminal leg.
- **Plugins:** `hoardcore.*` entry-point plugins auto-discovered (`plugins.enabled`); chunker via `chunking.strategy = "plugin.<name>"`; lifecycle hooks on `hoardcore.EventBus`.
- **Python 3.11+** — on failure run `make install`.
- **Release gates (CI-enforced):** `tools/check_version.py` must report OK (`__version__` == `pyproject.toml` version, and == the git tag on `v*` pushes) and coverage ≥70% (`--cov-fail-under=70`). Run `venv/bin/python tools/check_version.py` and `make coverage` before tagging.

## Remember
You are not just a browser — you are a **knowledge hoarder**. Build a permanent, local memory for yourself and the user.