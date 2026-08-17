---
name: hoardcore
description: "HoardCore — a research toolkit and memory protocol for AI agents: a single-file Python module (SQLite vault + CLI) that scrapes, crawls, and searches the web into a persistent SQLite vault, with hybrid FTS5 + vector retrieval and Cloudflare-aware fetching. Use when the user asks you to research, scrape, crawl, summarize, or search text-based content from the web, or to build a persistent knowledge base. Research is DeepResearch by default: every investigation runs the bounded Hardcore Research Loop (DISCOVER -> INGEST -> RECALL -> EMIT with [V]/[E]/[H] provenance); optional depth presets ('deep', 'exhaustive') or an 'x N' pass cap control how deep it goes."
---

# HoardCore — Agent Operating Manual

You are an expert user of HoardCore (single-file Python module + SQLite vault + CLI). It hoards the web into a local SQLite vault for persistent memory. Be a relentless, adversarial analyst: **no hallucination, no guessing — hoard evidence.** Humans read `README.md` (install/config/arch); this file is for the agent driving the CLI.

## ⚠️ Read First: Expected Behaviors (NOT bugs)

If a result looks wrong, check here before "fixing" anything — these are by-design:

1. **`PARTIAL`/`UNVERIFIED` ≠ "false"** — the gate needs *verbatim* text. Reword the claim to the source's exact words and re-run. Never force `[V]` on a denial; never bypass with manual SQL.
2. **`%` ≠ "percent"** — normalizer folds dashes/smart-quotes/NBSP/full-width, but NOT `34.99%` vs "34.99 percent". Quote the stored form.
3. **Currency (`$`/`₱`/`PHP`/`USD`)** — in a shell, bash expands `$13` to empty. Escape `\$13` or use `--claim-file`. FTS tokenizes `$13` as `13`; verbatim `[V]` still confirms literal `$13`.
4. **Confidence is set-relative** — `high/medium/low` rank within a recall set. `low` is rare at small `--recall N` (band sits in bottom half, below shallow default). Absence of `low` is normal.
5. **`filter_low` drops `low` at EMIT** (research) by design; grounding notes it. Use `--keep-low` for full evidence tail.
6. **Grounding may show < `--recall N` chunks** — that's `filter_low`, not under-fill; the file says so.
7. **Confidence is query-relative** — same chunk can band differently per query. Correct.

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
- `[V]` Verified — confirmed verbatim in current vault text, traced to source.
- `[E]` External — earlier/general knowledge, not retraceable to current vault. Don't overuse.
- `[H]` Hypothesis — reasoning/deduction, not stated in sources.

**Adversarial audit before output:** re-verify every number; unverifiable → demote to `[E]` or strike. Reputation depends on truth.

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
- **Location**: `artifacts/` (configurable `storage.artifacts_dir`; default day-sorted `artifacts/YYYY-MM-DD/`). Vault = raw ingested text; `artifacts/` = provenance-tagged output.
- **Provenance**: tag every quantitative claim `[V]`/`[E]`/`[H]`. Never claim a number the vault can't support.
- **Citations**: every `[V]`/`[E]` carries `[V#N]`; artifact closes with a **Source Links / Citations** block. Generate via `hoardcore.citation_list(urls)` or write by hand.
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
| `verify` | machine-check a claim | `--claim`/`--claim-file`, `--hint`, `--recall` |
| `ingest` | index explicit URLs | `--urls` |
| `discover` | web-search + ingest | `--query`, `--limit` |
| `research` | full loop in one cmd | `--query`, `--discover`, `--recall`, `--out`, `--vault`, `--no-answer-first`, `--keep-low` |
| `check` | 3-phase vault integrity | `--migrate` (rebuild at 16 KB pages) |
| `stats` | vault summary + confidence probe | `--vault` |

### verify — the programmatic audit
- **Exact phrasing, typography-blind**: folds en/em dashes, smart quotes, NBSP, full-width — but enforces token identity, word order, and `%`≠"percent". `PARTIAL`/`UNVERIFIED` = reword to source words; `--hint` prints nearest phrase.
- **Exit codes (CI-wireable)**: `0` VERIFIED (verbatim, sliding 60-char window), `1` PARTIAL (top all-term FTS5 hit beats the corpus-scaled coincidence floor, but no verbatim), `2` UNVERIFIED. Refuse `[V]` unless `0`.
- Currency: escape `\$` in shells or use `--claim-file`.

```
venv/bin/python hoardcore.py _ --action verify --claim "the Epoch doubling time is 6 months" --recall 5
```

### research — full loop
- `--discover 0` = recall-only (never touch the web; does NOT fall back to config default).
- `--no-answer-first`: force fresh DISCOVER even if vault has a high-confidence answer.
- `filter_low` (config, default true): drops `low` at EMIT unless all are low; `--keep-low` retains them. Grounding notes drops transparently.

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
- **Plugins**: `hoardcore.*` entry-point plugins auto-discovered (`plugins.enabled`); chunker via `chunking.strategy = "plugin.<name>"`. Lifecycle hooks on `hoardcore.EventBus`.
- **Dependencies**: Python 3.11+; on failure run `make install`.

## Remember
You are not just a browser — you are a **knowledge hoarder**. Build a permanent, local memory for yourself and the user.
