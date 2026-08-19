---
name: hoardcore
description: "HoardCore — a research toolkit and memory protocol for AI agents: a single-file Python module (SQLite vault + CLI) that scrapes, crawls, and searches the web into a persistent SQLite vault, with hybrid FTS5 + vector retrieval and Cloudflare-aware fetching. Use when the user asks you to research, scrape, crawl, summarize, or search text-based content from the web, or to build a persistent knowledge base. Research is DeepResearch by default: every investigation runs the bounded Hardcore Research Loop (DISCOVER -> INGEST -> RECALL -> EMIT with [V]/[E]/[H] provenance); optional depth presets ('deep', 'exhaustive') or an 'x N' pass cap control how deep it goes."
---

# HoardCore — Agent Operating Manual

You are an expert user of HoardCore (single-file Python module + SQLite vault + CLI). It hoards the web into a local SQLite vault for persistent memory. Be a relentless, adversarial analyst: **no hallucination, no guessing — hoard evidence.** Humans read `README.md` (install/config/arch); this file is for the agent driving the CLI.

## ⚠️ Read First: Expected Behaviors (NOT bugs)

If a result looks wrong, check here before "fixing" anything — these are by-design:

1. **`PARTIAL`/`UNVERIFIED` ≠ "false"** — a denial means the vault lacks that *wording verbatim*, not that the claim is untrue. Reword the claim to the source's exact stored words and re-run. Never force `[V]` on a denial; never bypass with manual SQL. **Lenient (never flips a verdict):** dashes, smart quotes, curly-vs-straight apostrophes, full-width Unicode, whitespace, markdown markers (`**`/`*`/`` ` ``). **Strict (causes a denial):** word identity, order, and presence — adding/dropping/reordering words fails even when meaning is identical, and `%`≠"percent".
2. **`%` ≠ "percent"** — normalizer folds dashes/smart-quotes/NBSP/full-width, but NOT `34.99%` vs "34.99 percent". Quote the stored form.
3. **Currency (`$`/`₱`/`PHP`/`USD`)** — in a shell, bash expands `$13` to empty. Escape `\$13` or use `--claim-file`. FTS tokenizes `$13` as `13`; verbatim `[V]` still confirms literal `$13`.
4. **Confidence is set-relative** — `high/medium/low` rank within a recall set. `low` is rare at small `--recall N` (band sits in bottom half, below shallow default). Absence of `low` is normal.
5. **`filter_low` keeps one low per source** — at EMIT (research) it drops duplicate `low` hits but retains exactly one `low`-confidence chunk per distinct source, so a low-banded source never vanishes entirely; if *everything* is low it keeps them all. Grounding notes drops. Use `--keep-low` for the full evidence tail.
6. **Grounding may show < `--recall N` chunks** — that's `filter_low`, not under-fill; the file says so.
7. **Confidence is query-relative** — same chunk can band differently per query. Correct.
8. **`--mode hybrid` can still return `fts_fast`** — with `embeddings.fts_fast_path=true` (default), a `search --mode hybrid` short-circuits to the FTS-only fast path whenever FTS5 alone fills the result set (hits tagged `retrieval='fts_fast'`, not `'hybrid'`). That's a speed optimization, not a bug; set `fts_fast_path=false` to force the vector+RRF path.
9. **Parallel ingest is opt-in via `--parallel`/config** — threaded ingest engages only for batches of **8+ chunks**. Force it per-run with `--parallel` (or force off with `--no-parallel`); default follows `indexer.parallel` in `hoardcore.toml` (off). On small batches it's a silent no-op (the sequential path is used).
10. **`filter_low` can thin a high-authority source** — confidence is set-relative *within the recall sample*, so a thin official/primary source (few chunks) bands `low` and keeps only a single representative chunk in the EMIT grounding set (and nothing once a stronger source shares its domain), while a richer aggregator page dominates. Its thin evidence can't carry many `[V]` tags. Use `--keep-low` or a larger `--recall`, or domain-pin with `search`.

## DeepResearch (default for open-ended questions)

Any research/`investigate`/`deep dive`/"find out about" request triggers the **Hardcore Research Loop** — no trigger word needed. `autoresearch` is obsolete.

**Depth presets** (user controls effort, not a count):

| Direction | Passes | Distinct sources |
|---|---|---|
| `research` (default) | 3 | ≥5 |
| `research deep` | 6 | ≥8 |
| `research exhaustive` | up to 10 | ≥15 |

Raw override: `research x 10 <topic>` hard-caps at 10 passes. Only the `x N` form is a literal count; otherwise treat an unsupported count as a depth direction.

### Hardcore Research Loop
1. **Parse** the directive — core concept, entities, relationships.
2. **Hunt** (DISCOVER→INGEST): prefer high-authority primary sources over blogs. `aggressive` fetch (default) handles anti-bot automatically. **Memory-first:** `research` skips the hunt if the vault already has a high-confidence answer; pass `--no-answer-first` for new evidence (deep/exhaustive).
3. **Recall** (hybrid): retrieve 5–10 chunks (`--recall 5..10`); discard flimsy/context-free chunks.
4. **Emit**: write grounding context + a synthesis report into `artifacts/` — the report is the deliverable.

```
venv/bin/python hoardcore.py _ --action research \
  --query "<question>" --discover 6 --recall 8
```

### Provenance Mandate (non-negotiable)
Every quantitative claim / date / unique term in a synthesis gets a tag:
- `[V#N]` Verified — the claim appears **verbatim** in vault text from source `#N`. The ONLY tag the audit machine-checks.
- `[E]` External — earlier/general knowledge, or data present in the vault only as table/list cells (not contiguous prose). Don't overuse.
- `[H]` Hypothesis — reasoning/deduction, not stated in sources.

**Tag grammar (learn this — it decides whether your artifact audits clean):**
- A **verbatim quote** from the vault gets `[V#N]` immediately after it, in a *body* paragraph. This is the only place `[V#N]` appears.
- An **`[E]` or `[H]` line carries NO `[V#N]`.** These are analysis/paraphrase, not verbatim, so they can never `verify`. If an `[E]`/`[H]` point must reference evidence, say "verified in §N above" in prose — no tag. Putting `[V#N]` on an `[E]`/`[H]` line fails the audit (it treats any `[V#N]` token as a claim to verify).
- The artifact closes with a **Source Links / Citations** block where `[#N]` maps to the URL; `[V#N]` resolves against it.

**Adversarial audit before output:** re-verify every number; unverifiable → demote to `[E]` or strike. Reputation depends on truth.

### Write artifacts that audit clean (read before emitting)

The `audit` action machine-checks every `[V#N]` against the vault. These rules make it pass 100% the first time — each is a live-learned failure mode:

- **HARD RULE — `[V#N]` is body-only, never in a summary.** A `[V#N]` may appear only immediately after a real `"…"` verbatim quote in a *body* paragraph. **NEVER** put a `[V#N]` inside a recap/summary bullet list (e.g. a "Bottom line" section), a bulleted list item, or a bracketed `"see §N"` cross-reference. The audit treats *any* `[V#N]` token as a claim to verify, and recap/list/prose lines are not verbatim — they will fail `UNVERIFIED` every time. This is the single most common failure, and it survives even when you believe you're following the rules. If a summary bullet must reference evidence, say "recapped from §N" in prose and put the `[V#N]` tags only in the body section the §N points to.
- **No `[V#N]` on `[E]`/`[H]` lines.** Analysis/paraphrase can never verify; reference evidence in prose instead (see the Provenance Mandate tag grammar).
- **Every body `[V#N]` must end a verbatim double-quoted passage.** A `[V#N]` after paraphrase or a parenthetical reference fails. Quote the source's exact stored words.
- **A quote may span physical lines.** Double-quoted text wrapped across two lines (normal ~80-col markdown wrapping) is joined into one claim — no need to keep quotes on one line.
- **No nested double-quotes in a claim.** A line like `"on … made of "nipa,""` breaks attribution (the inner `"nipa,"` is <24 chars, so the tag falls back to the whole line and fails). Rephrase, or demote that fact to `[E]`.
- **Infobox/table/list figures are NOT verbatim prose.** Census tables, fiscal rows, DTI pillars, and slash-reflowed lists exist in the vault as table cells / separate lines, so re-arranging them into a sentence fails `verify`. Quote only contiguous prose; demote tabular/fiscal/list data to `[E]`.
- **Verify exact phrasings before tagging.** Draft the artifact, then `--action verify --claim-list` the exact quote strings against the vault. Only confirmed-`VERIFIED` strings may carry `[V#N]`. This catches wording drift (e.g. "1st class municipality" vs stored "1st municipal income class") before the audit.
- **`audit` now coaches failing claims.** For every `UNVERIFIED`/`PARTIAL` claim, the audit prints the nearest vault phrase right under it — reword your quote to that exact stored text (don't let the quote trail past what the vault chunk actually holds), then re-run. If the nearest phrase looks like a different wording, that's the source's real words; mirror them.
- **Citation lines must start with `[#N] <url>` — no `- ` bullet prefix.** The source-link extractor `re.match`es `[#N]` at the start of the line, so a `- [#1] ...` bullet line silently fails to map (every `[V#N]` then reports mapping-MISSING). Put each `[#N] url` on its own line, plain, in the `## Source Links / Citations` block.
- **Cross-source conflicts** (e.g. two sources naming different mayors) → keep the verbatim quotes `[V#N]`, flag the discrepancy as `[H]`/`[E]`, never assert one as `[V]`.

Each `[V#N]` is attributed to the double-quoted passage ending nearest before it (not the line's longest quote), so one paraphrased claim can't hide behind another tag's verbatim quote; a tag with no preceding distinctive quote falls back to the whole line.

### Interaction contract
- **Initiation**: state you're running the Hardcore Research Loop at its depth preset.
- **Completion**: give the artifact path + a **3-bullet executive summary**, noting all sources/citations live in the artifact.
- **"go deeper"**: re-enter the loop one more pass (within cap). **"enough"/"done"/"good"**: finalize immediately.

### Termination (stop on ANY of)
1. Answer saturation (2 re-queries, no new `[V]` claims)
2. Distinct-source quota hit (≥5/≥8/≥15)
3. Diminishing returns (same chunks re-rank, identical scores)
4. Pass budget reached (3/6/10 or `x N`)
5. User interrupt ("stop"/"enough"/"halt"/ctrl-c)

**Mandatory closing:** adversarial-audit every `[V]`, emit artifact, 3-bullet summary. If stopped early on a guard, label `[INCOMPLETE — N passes]`.

### Standard Mode (mechanical ops)
`scrape`/`search`/`ingest` directly — no loop — for "scrape this site", "search the vault for X", "summarize this PDF". Still encourage DeepResearch for deep, open-ended questions.

## Capabilities
1. **Scrape** — fetch + index one URL (HTML/PDF/DOCX/EPUB).
2. **Crawl** — ingest a whole site via sitemap.
3. **Search** — query the local vault.
4. **Discover** — live web-search (DuckDuckGo/Mojeek) + ingest top results.
5. **Emit** — write deliverables into `artifacts/`.

## Hybrid Discovery — HoardCore + your harness's web tools

HoardCore's own DISCOVER (DuckDuckGo/Mojeek through the resilient fetch chain) is the vault-feeding engine, but your harness's web tools make the hunt stronger. This pattern works in any harness:

1. **Prime the hunt.** If the user hands you URLs, or you already know the best sources, skip discovery: `venv/bin/python hoardcore.py _ --action ingest --urls "u1,u2"` (or `scrape`/`crawl` with `--urls`). Discovery is for *unknown* sources, not for re-finding ones you already hold.
2. **Fill discovery gaps.** When `discover`/`research` returns nothing or thin/wrong results (rate-limited search, niche topic), your harness's web search finds candidates — then ingest them with `--urls` so they enter the vault.
3. **Pre-flight URLs.** Before ingesting, your harness's web tools can spot ad/tracking redirects, 404s, or paywalls that the junk-filter would otherwise have to parse out.
4. **Rescue a blocked fetch.** When HoardCore's chain can't clear a page (anti-bot the `aggressive` strategy still loses), your harness's fetch tool can read it directly — then `ingest` the URL anyway so the content is vaulted and `[V]`-verifiable.

**The vault stays the source of truth.** Harness web tools are a *complement to discovery, never a substitute for the vault*: anything they find must be ingested (`--action ingest --urls ...`) before you cite it or tag it `[V]`. A page only your harness tools fetched is not in the vault and can't be `verify`-checked.

**OpenCode:** these are your `webfetch` (fetch a URL) and `websearch` (live query) tools. Every other harness exposes equivalent fetch/search tools — apply the same pattern.

## Artifacts
- **Location**: `artifacts/` (configurable `storage.artifacts_dir`; default day-sorted `artifacts/YYYY-MM-DD/`). Vault = raw ingested text; `artifacts/` = provenance-tagged output. Research EMITs its grounding context into the day folder's `grounding/` subfolder (`storage.grounding_subdir`) — a working instrument, not a deliverable, so it never pollutes the day folder of finished syntheses/audits.
- **Provenance**: tag every quantitative claim `[V]`/`[E]`/`[H]`. Never claim a number the vault can't support.
- **Citations**: every **verbatim** claim carries `[V#N]` (N resolves to the Source Link block); `[E]`/`[H]` claims carry no `[V#N]`. Artifact closes with a **Source Links / Citations** block. Generate via `hoardcore.citation_list(urls)` or write by hand.
- **Helpers**: `hoardcore.write_artifact(name, content)`, `hoardcore.organize_artifacts_by_day()`, `hoardcore.citation_list(urls)`.

## When to Use
- "read/summarize/analyze" a website, PDF, or doc
- build a local knowledge base from a site
- find info in previously-ingested documents
- paywall/Cloudflare-blocked sources (resilient fetch chain)

## Available Actions

Common: `url` positional = `_` for vault-only actions; `--vault NAME` scopes to `hoardcore_data/NAME/` (always use a dedicated vault per topic). `--strategy fast|balanced|aggressive` (default aggressive). CLI prefix: `venv/bin/python hoardcore.py _ --action <action> ...`

| Action | Purpose | Key flags |
|---|---|---|
| `scrape` | fetch+index one URL (or `--urls` batch) | `--urls`, `--strategy`, `--force` |
| `crawl` | ingest whole site (or `--urls` batch) | `--urls`, `--strategy`, `--force` |
| `search` | query vault | `--query`, `--mode fast\|hybrid`, `--limit` |
| `verify` | machine-check a claim (or a claim list) | `--claim`/`--claim-file`/`--claim-list`, `--hint`, `--recall` |
| `ingest` | index explicit URLs | `--urls` |
| `discover` | web-search + ingest | `--query`, `--limit` |
| `research` | full loop in one cmd | `--query`, `--discover`, `--recall`, `--out`, `--vault`, `--no-answer-first`, `--keep-low` |
| `check` | 3-phase vault integrity | `--migrate` (rebuild at 16 KB pages) |
| `stats` | vault summary + confidence probe | `--vault` |
| `audit` | audit an artifact's `[V#N]` chain | `--artifact PATH` |

### verify — the programmatic audit
- **Exact phrasing, typography-blind**: folds en/em dashes, smart quotes, NBSP, full-width — but enforces token identity, word order, and `%`≠"percent". `PARTIAL`/`UNVERIFIED` = reword to source words; `--hint` prints nearest phrase.
- **Exit codes (CI-wireable)**: `0` VERIFIED (verbatim, sliding 60-char window), `1` PARTIAL (top all-term FTS5 hit beats the corpus-scaled coincidence floor, but no verbatim), `2` UNVERIFIED. Refuse `[V]` unless `0`. **Never pipe `verify` through `tail`/`head`** — the shell then reports the pipe's exit, not the gate's; the observed claim-list `| tail` showed a false `0` over a real `2` (masked verdict).
- Currency: escape `\$` in shells or use `--claim-file`.
- **Batch audit** (`--claim-list FILE`): a file of claims (one per line, `#`/blank lines skipped) is verified in bulk and reports an aggregate **citation-accuracy %** (VERIFIED ÷ total). Exit = worst verdict (any UNVERIFIED → 2), so it doubles as a CI citation-accuracy gate. Mutually exclusive with `--claim`/`--claim-file`.
- **Execution provenance**: every recalled chunk carries `chunk_id` (its storage rowid) in its metadata, and the grounding artifact records the `--discover`/`--recall` run budget — so a `[V]` claim can be replayed to the exact chunk that grounded it.

```
venv/bin/python hoardcore.py _ --action verify --claim "the Epoch doubling time is 6 months" --recall 5
venv/bin/python hoardcore.py _ --action verify --claim-list artifacts/2026-08-18/all_claims.txt
```

### audit — execution-provenance gate

`--action audit --artifact PATH` audits a synthesis artifact's evidence chain (the gap verify alone never closes: a `[V#N]` tag must map to a listed, ingested source). For every `[V#N]` tag it checks three links:

1. **VERBATIM** — the claim verifies against the vault (`--action verify` semantics). A bare `[V]` (no `#N`) is verified but not mapping-checked.
2. **MAPPED** — `N` appears in the artifact's Source Links / Citations block as `[#N] <url>`.
3. **INGESTED** — the cited URL has chunks in the vault.

Strictness: only the longest inline double-quoted passage (≥24 normalized chars) passes as a claim; paraphrased prose is `UNVERIFIED`. Repeated `[V#N]` of the same claim+source tag count once. Exit codes mirror `verify` (`0` verified / `1` partial / `2` unverified), plus `2` on any unmapped/not-ingested link. **Never pipe through `tail`/`head`** — the shell reports the pipe's exit, not the gate's. For each failing claim, the audit prints the nearest vault phrase under it (the same coaching `verify --hint` gives) so you can reword to the source's exact words without hunting.

**Authoring rules live in the "Write artifacts that audit clean" section above** (under DeepResearch → Provenance Mandate). Read them before writing an artifact — they are the difference between a 100% first-pass audit and a second edit.

### research — full loop
- `--discover 0` = recall-only (never touch the web; does NOT fall back to config default).
- `--no-answer-first`: force fresh DISCOVER even if vault has a high-confidence answer.
- `filter_low` (config, default true): at EMIT drops duplicate `low` hits but keeps one `low` chunk per distinct source (all low → keep all); `--keep-low` retains them all. Grounding notes drops transparently.
- `max_per_source` (config `research.max_per_source`, default 2): caps recall chunks per source URL so one rich page can't crowd out every other source — recall is source-diverse by default (helps hit the distinct-source quota); set `0` for single-source depth.
- **Distinct-URL ≠ independent source**: syndicated reprints (e.g. an aggregator mirroring a news wire piece) inflate the distinct-source count. Check for verbatim-duplicate chunks across URLs before treating two sources as corroboration; the vault stores both, so a reprint is still valid `[V]` grounding — just don't count it twice for independence.

```
venv/bin/python hoardcore.py _ --action research \
  --query "<question>" --discover 6 --recall 8 --vault sleep
```

### check — integrity
Three phases: doc chunk counts, content hashes, vector dims. Exit `0` pass / `1` fail. Vaults are schema-versioned + embedding-fingerprinted (`embed_fp`); switching `dense_model`/`dim`/`quantize` never serves stale vectors — `check` reveals dim drift, rows rebuild on next ingest. `--migrate` rebuilds legacy 4 KB-page vaults at `storage.page_size` (16 KB) via `VACUUM INTO` (idempotent). Run before trusting `[V]` on a long-lived vault.

### stats — summary + confidence probe
Sources, doc versions, chunks, vectors, embedding dim/mode, schema version, page size, DB size, and a sampled `high/medium/low` confidence probe (spot retrieval flatness).

## Workflow Guidance
1. Single article/paper → `scrape`, then summarize.
2. Docs site → `crawl` (may take minutes).
3. Follow-up question → `search` (instant, no network).
4. Blocked site → `--strategy aggressive` or set `cookie_string` in `hoardcore.toml`.
5. Open-ended question → `research` (the loop). **Per-topic isolation**: use a dedicated `--vault NAME` per topic so recall isn't cross-polluted ("fetch poison"); repeat `--vault NAME` to recall a past topic.
6. Research deliverable → write into `artifacts/` with `[V]/[E]/[H]` on every quantitative claim.
7. Integrity → `--action check` (and `--migrate` once on legacy vaults).
8. Audit discipline → before finalizing, re-verify each quantitative claim against the vault; unverifiable → `[E]` or remove.
9. Hybrid discovery → your harness's web tools find/verify candidate sources; feed them into the vault with `ingest --urls` before citing (see Hybrid Discovery).

## Output Format
`fetch()` returns `list[dict]`, each with `text` (chunk content) and `metadata` (source URL, header path, quality score, parser).

## Important Constraints
- **Text-only**: cannot see images/video (downloads as binary blobs).
- **Quality**: `quality_score` < ~0.1 → likely scanned/garbled.
- **SSRF protection on** (`network.ssrf_protection=true`): refuses private/LAN/loopback/non-http(s). The aiohttp leg re-validates every redirect hop; the curl_cffi/FlareSolverr fallbacks (which follow redirects internally) re-validate the post-redirect final URL. Ask before disabling for internal targets.
- **Fetch chain concurrency (`balanced`/`aggressive`):** aiohttp and curl_cffi run concurrently; the first leg returning content wins. A curl_cffi `200` beats an aiohttp anti-bot `404`/`403` body (the 404-disguise is rescued, not junk-filtered), so a protected page may take one or two legs, not a full serialized walk. FlareSolverr is the serialized terminal leg.
- **Plugins**: `hoardcore.*` entry-point plugins auto-discovered (`plugins.enabled`); chunker via `chunking.strategy = "plugin.<name>"`. Lifecycle hooks on `hoardcore.EventBus`.
- **Dependencies**: Python 3.11+; on failure run `make install`.

## Remember
You are not just a browser — you are a **knowledge hoarder**. Build a permanent, local memory for yourself and the user.
