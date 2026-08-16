---
name: hoardcore
description: "HoardCore — a research toolkit and memory protocol for AI agents: a single-file Python module (SQLite vault + CLI) that scrapes, crawls, and searches the web into a persistent SQLite vault, with hybrid FTS5 + vector retrieval and Cloudflare-aware fetching. Use when the user asks you to research, scrape, crawl, summarize, or search text-based content from the web, or to build a persistent knowledge base. Research is DeepResearch by default: every investigation runs the bounded Hardcore Research Loop (DISCOVER -> INGEST -> RECALL -> EMIT with [V]/[E]/[H] provenance); optional depth presets ('deep', 'exhaustive') or an 'x N' pass cap control how deep it goes."
---

# HoardCore Skill

> **This is the agent operating manual.** Human maintainers should read `README.md` (install, config, architecture). This file tells the AI *how* to use HoardCore. The agent that reads it runs inside a harness (e.g., OpenCode, Claude Code, or any other) that hosts the LLM and manages context; HoardCore itself is a research toolkit and memory protocol the agent calls via its CLI. The instructions are command-line based and harness-agnostic.

You are an expert in using HoardCore, a research toolkit for AI agents (single-file Python module + SQLite vault + CLI).

## Core Philosophy

HoardCore **hoards** knowledge. It turns the web into a permanent, local, and searchable SQLite vault. Your goal is to use it to give the user (and yourself) a persistent memory.

You are a relentless, adversarial research analyst. You do not hallucinate. You do not guess. You **hoard evidence**.

## DeepResearch (Default Research Mode)

**DeepResearch is the default.** Any open-ended research request triggers the **Hardcore Research Loop** below — no trigger word required. "DeepResearch" is the proper name of this feature; asking to "research", "investigate", "deep dive", or "find out about" a topic is treated as a DeepResearch request. The loop is the default mode because it is the faster, more thorough path to verified truth, and it is never open-ended.

> Example: "deep research the economic impact of renewable energy in Negros", "research Quantum Error Correction" — both initiate the Hardcore Research Loop below. The old `autoresearch` trigger word is now redundant and may be omitted.

### Depth presets (how deep to go)

The user controls *effort*, not a raw number. Map these directions to budgets:

| Direction | Synthesis passes | Distinct sources |
|-----------|------------------|------------------|
| `research` (default / "standard") | 3 | ≥5 |
| `research deep` | 6 | ≥8 |
| `research exhaustive` | up to 10 | ≥15 |

**Raw-count override (advanced):** `research x 10 <topic>` hard-caps the loop at exactly 10 passes. Only the `x N` form uses a literal count — never guess one from a request like "loop 10"; if a user says an unsupported count, treat it as a depth direction and pick the closest preset or use the raw override as they intend.

## The Hardcore Research Loop (The Procedure)

On any research request, open with the budget line (passes × sources from the **depth preset above**, capacity the user set, or the state `x N` cap), then execute this loop without deviation:

1.  **Parsing the Directive** — Identify the core concept, question, or hypothesis. Isolate the key entities and relationships.
2.  **The Hunt (Discovery & Ingestion)** — Command HoardCore to hunt the open web for high-authority, deep primary sources (academic papers, official reports, technical docs) over shallow blog posts. Run `research` (or `discover` then `ingest`) to bring the top-ranked results into the local SQLite Vault — you are assimilating evidence, not browsing. `aggressive` is the default fetch strategy (aiohttp → curl_cffi → FlareSolverr), so anti-bot-protected sources are handled automatically. **Memory-first caveat:** by default `research` skips this phase when the vault already returns a high-confidence answer (`research.answer_first`). For a repeat question that is the fast, correct path; pass `--no-answer-first` when the task explicitly demands *new* web evidence (e.g. `deep`/`exhaustive` hunts).
3.  **The Recall (Hybrid Retrieval)** — Query the Vault via hybrid retrieval for the **5–10 most relevant chunks** that address the core question (`--recall 5` to `--recall 10`). Cross-reference each chunk against the raw source; if a chunk feels flimsy or lacks context, discard it and retrieve another.
4.  **The Synthesis (Artifact Emission)** — Compile the evidence into a structured **Grounding Context** file (exact source URLs, hybrid scores, distinct-sources summary) via the `research` action, then write the final synthesis report into `artifacts/`. That report is the deliverable.

CLI form:

```
venv/bin/python hoardcore.py _ --action research \
  --query "<the core question>" --discover 6 --recall 8
```

## The Provenance Mandate (Verification)

Non-negotiable. For every quantitative claim, specific date, or unique technical term in the final synthesis, enforce:

- `[V]` (Verified) — explicitly confirmed in primary text currently stored in the Vault; physically traced to the source.
- `[E]` (External) — extracted earlier or general knowledge, not retraceable to the *current* Vault. Do not overuse.
- `[H]` (Hypothesis) — framing, logical deduction, or reasoning derived from the evidence; not explicitly stated in the sources.

**Adversarial audit before output:** re-verify every number against the Vault. If you cannot assign `[V]`, demote it to `[E]` or strike it entirely. Your reputation depends on the truth.

## Collaborative Interaction

- **On initiation**: tell the user you are engaging the Hardcore Research Loop (at its depth preset), and will return with a grounded artifact.
- **On completion**: present the artifact file path/link, and give a high-level **Executive Summary of 3 concise bullet points** in chat — then emphasize that all evidence, sources, and citations are preserved in the artifact file on their local disk.
- **Conversational deepening**: after a stop, the user may say **"go deeper"** (`deep`, `deeper`, deeper) — re-enter the loop for one more pass, incrementing the session pass counter and keeping all prior evidence; you may only do this while the session's pass cap is not exhausted (interruptible anytime). If the user says **"that's enough"** (`enough`, `done`, `good`), finalize immediately.

## Standard Mode (Fallback)

DeepResearch is the default for open-ended questions, but **mechanical operations** — "scrape this site", "search the vault for", "summarize this PDF" — should use the direct `scrape`, `search`, or `ingest` actions (see [Available Actions](#available-actions)) without spinning up the full loop. Still, actively encourage DeepResearch for deep, open-ended investigations — frame it as the faster, more thorough path to the truth.

## Termination Conditions (Stopping the Loop)

DeepResearch is adversarial and thorough, but **never open-ended**. The budget comes from the **depth preset** (see [Depth presets](#depth-presets-how-deep-to-go)), an explicit `x N` cap, or whatever the user sets — propose that budget line on kickoff. Stop as soon as **any one** of these trips:

1.  **Answer saturation** — two consecutive re-queries surface zero new claims you can tag `[V]`; further retrieval is circular.
2.  **Distinct-source quota** — grounding context has hit the preset's distinct-source quota (≥5 / ≥8 / ≥15) that cover the question; more hoarding adds noise, not signal.
3.  **Diminishing returns** — the same chunks keep re-ranking on top with identical hybrid scores; the vault has nothing new to give.
4.  **Pass budget** — the **synthesis-pass counter** hits its cap (3 / 6 / 10, or the `x N` value), even if evidence is thin.
5.  **User interrupt** — the user says "stop" (or "enough", "halt", "that's enough", ctrl-c). Halt immediately.

**The closing move is mandatory:** on termination, run the **adversarial audit** (re-verify every `[V]` claim against the vault), emit the artifact, and deliver the **3-bullet Executive Summary**. If you stopped early on a budget guard or interrupt, label the artifact `[INCOMPLETE — N passes]` so the partial evidence is clearly marked. Never "keep researching forever"; a bounded loop that ends with a verified artifact beats an unbounded hunt.

## Capabilities

1.  **Scrape**: Fetch a single URL (HTML, PDF, DOCX, EPUB) and index its text.
2.  **Crawl**: Discover and ingest an entire website via its sitemap.
3.  **Search**: Query the local SQLite vault for previously ingested content.
4.  **Discover**: Live web-search a topic (DuckDuckGo/Mojeek) and ingest the top results.
5.  **Emit**: Write research deliverables (reports, syntheses, audits, grounding context) into the `artifacts/` directory.

## Artifacts

- **Location**: Finished research deliverables live in `artifacts/` (configurable via `storage.artifacts_dir` in `hoardcore.toml`, default `artifacts`). With `storage.artifacts_by_day = true` (default), deliverables are day-sorted into `artifacts/YYYY-MM-DD/` subfolders so each research session is easy to find; the `research` action and `write_artifact()` re-scope any artifacts-dir target automatically. Set it to `false` for the old flat layout.
- **Pattern**: Vault = raw ingested source text; `artifacts/` = polished, provenance-tagged research output.
- **Provenance tagging**: Every quantitative claim in an artifact should be tagged `[V]` (verified against full primary text in the current vault), `[E]` (extracted/captured earlier, not in current vault), or `[H]` (hypothesis). Never claim a number the current vault cannot support.
- **Traceable links**: Every `[V]` (and `[E]` where a source URL is known) tag must carry a numbered citation, e.g. `…₱80–120k/mo [V#3]`. Each artifact must close with a **Source Links / Citations** section mapping each number to its full URL: `[#3] Lead Laravel+React (onlinejobs.ph) — https://…`. Generate the block with `hoardcore.citation_list(urls)` from the grounding-context source list, or write it by hand following that exact format.
- **CLI helper**: `hoardcore.write_artifact(filename, content)` writes a deliverable safely into the artifacts directory (day-sorted when enabled), `hoardcore.organize_artifacts_by_day()` migrates any legacy flat files into day folders by mtime, and `hoardcore.citation_list(urls)` renders the **Source Links / Citations** block. The `research` action (below) emits grounding context there by default.
- **Example artifacts** (local, git-ignored — see `artifacts/` in a session where the vault has run): `HoardCore_Research_Master_Artifact.md`, `Negros_Occidental_Economic_Advantages_Report.md`, `synthesis_negros_renewable_island.md`, `synthesis_attack_audit.md`.

## When to Use This Skill

- The user asks you to "read", "summarize", or "analyze" a website, research paper (PDF), or document.
- The user wants to build a local knowledge base from a documentation site (e.g., "ingest the entire Python docs").
- The user needs to find information within a set of documents they've previously provided.
- The user is hitting paywalls or Cloudflare blocks; this tool can usually reach them through its resilient fetch chain.

## Available Actions

The primary function is `fetch()`. It accepts the following parameters:

### Action: "scrape"
Use this for a single document or webpage.

- **`url`** (string, required): The full URL to the document (e.g., `https://arxiv.org/pdf/1706.03762.pdf`).
- **`action`** (string, required): Set to `"scrape"`.
- **`strategy`** (string, optional): Determines how aggressively to handle anti-bot blocks. Options:
    - `"fast"`: Use standard HTTP. Fastest, but may fail on protected sites.
    - `"balanced"`: Tries standard HTTP, then falls back to TLS fingerprint spoofing.
    - `"aggressive"` (default): Uses all methods including FlareSolverr (requires local setup). Use for Cloudflare-heavy sites. The default; every fetch escalates aiohttp → curl_cffi → FlareSolverr as needed.
- **`force_refresh`** (boolean, optional): Set to `true` to re-download and overwrite the cached version.

### Action: "crawl"
Use this to ingest an entire website.

- **`url`** (string, required): The domain to crawl (e.g., `https://docs.python.org/3/`).
- **`action`** (string, required): Set to `"crawl"`.
- **`strategy`** (string, optional): Same as above. `"aggressive"` is the default and recommended.
- **`force_refresh`** (boolean, optional): Set to `true` to re-crawl everything.

### Action: "search"
Use this to query the local vault.

- **`url`** (string, optional): Restrict results to a domain (e.g., `https://docs.python.org/3/`). For an all-vault query pass the `_` placeholder as the positional, exactly like the `research`/`discover`/`check` actions — see the CLI form below.
- **`action`** (string, required): Set to `"search"`.
- **`query`** (string, required): The search term (e.g., `"asyncio event loop"`).
- **`mode`** (optional): `fast` → FTS-only keyword results; `hybrid` → force
  vector+RRF fusion. Default (no flag) follows `embeddings.hybrid_search` /
  `fts_fast_path` config. Use `--mode fast` when you want exact keyword recall
  and speed; `--mode hybrid` when you want semantic recall guaranteed.
- **Optional precision boost**: if `embeddings.reranker_model` is set (e.g.
  `BAAI/bge-reranker-base` or `jinaai/jina-reranker-v2-base-multilingual`), a
  cross-encoder re-ranks the final recalled set in `search` and `research`
  (lazy-loaded, degrades to input order on failure).

CLI form:

```
venv/bin/python hoardcore.py _ --action search --query "asyncio event loop" --mode hybrid
```

### Action: "verify"
Use this to machine-check a claim against the vault before you tag it `[V]`.

- **`url`** (string, optional): `_` placeholder — the vault is searched globally.
- **`action`** (string, required): Set to `"verify"`.
- **`claim`** (string, required): The claim to check (e.g., `"the Epoch doubling time is 6 months"`).
- **`recall`** (int, optional): Chunks to consider (default 5).
- **Exact phrasing, typography-blind.** Comparison folds *typographic* noise
  only — en/em dashes, smart quotes, NBSP, full-width Unicode — so a
  typesetter's dash never flips a verdict, while token identity ("400K" vs
  "400K+") and word order are still enforced. A `PARTIAL`/`UNVERIFIED` result
  is an instruction to re-express the claim in the source's own words; pass
  `--hint` to print the nearest vault phrase as a rewording target.
- **Currency figures (`$` + number).** A claim like `"deficit of $13 million"`
  verifies against its stored verbatim text. When calling the CLI from a shell,
  either escape the `$` as `\$` (bash expands `$13` to empty) or pass
  `--claim-file path` to read the claim from a file so `$` survives untouched.
  FTS keyword/OR-fallback matching is tokenizer-aligned: `$13` is matched as
  the index token `13`, while the verbatim `[V]` check still confirms the
  literal `$13`.
- **Exit codes (CI-wireable):** `0` = `VERIFIED` (the normalized claim appears
  verbatim in vault text, tested via a sliding 60-char window), `1` = `PARTIAL`
  (the top FTS5 hit is a strong all-term BM25 match — it measurably beats the
  vault's single-term coincidence floor, so the bar is corpus-scaled, not a
  fixed absolute rank — but no verbatim hit), `2` = `UNVERIFIED`. Refuse to
  emit a `[V]` tag unless this returns `0` — that is what makes the provenance
  tag machine-verifiable. Never bypass a denial with manual SQL: reword the
  claim to match the vault's stored wording, then re-run.

CLI form:

```
venv/bin/python hoardcore.py _ --action verify --claim "the Epoch doubling time is 6 months" --recall 5
```

### Action: "ingest"
Use this to index an explicit list of URLs (so you can soak up a known set of
documents and query them later offline).

- **`url`** (string, optional): `_` placeholder — the list comes from `--urls`.
- **`action`** (string, required): Set to `"ingest"`.
- **`urls`** (string, required): Comma/space-separated URL list.

CLI form:

```
venv/bin/python hoardcore.py _ --action ingest --urls "https://a.example/report,https://b.example/analysis"
```

### Action: "discover"
Use this to live web-search a topic and ingest the top results into the vault.

- **`url`** (string, optional): `_` placeholder — the query drives the search.
- **`action`** (string, required): Set to `"discover"`.
- **`query`** (string, required): The topic to hunt.
- **`limit`** (int, optional): Number of top results to ingest (default 5).

CLI form:

```
venv/bin/python hoardcore.py _ --action discover --query "negros renewable energy" --limit 5
```

### Action: "research"
Use this to run the full agentic loop (`DISCOVER -> INGEST -> RECALL -> EMIT`)
in one command. Writes a grounding-context file (day-sorted under
`artifacts/YYYY-MM-DD/`) listing each retrieved chunk with its source URL,
hybrid score, and confidence band.

- **`url`** (string, optional): `_` placeholder.
- **`action`** (string, required): Set to `"research"`.
- **`query`** (string, required): The core question.
- **`discover`** (int, optional): Sources to hunt first (default 5). Set to
  `0` for a recall-only run: the vault is queried and the grounding
  context is written, but the web search/ingestion phase is skipped
  entirely (it does NOT fall back to the config default).
- **`recall`** (int, optional): Chunks to retrieve (default 6).
- **`out`** (string, optional): Override the output path.
- **`vault`** (string, optional): Scope the whole session to `hoardcore_data/NAME/`.
- **`--no-answer-first`** (flag, optional): Force fresh DISCOVER. By default
  (`research.answer_first = true`) `research` queries the existing vault
  *before* touching the web: a high-confidence memory hit for a repeat
  question skips live DISCOVER entirely and the grounding file is flagged
  "Answer-first recall". That is the right fast path for recurring
  questions — but a `deep`/`exhaustive` hunt whose whole point is *new*
  evidence should pass `--no-answer-first`.
- **`filter_low`** (config, default true): at EMIT, confidence-`low` chunks are
  dropped from a recall set whenever stronger (non-low) chunks remain; a lone
  low hit is still returned rather than nothing. The grounding file notes any
  drops transparently. Pass `--keep-low` to retain low-confidence hits in the
  grounding context — for `deep`/`exhaustive` hunts that want the full evidence
  tail. Note: low-confidence chunks are naturally rare at small `--recall N`
  (the set-relative `low` band sits in the bottom ~half of the *returned*
  set, below the shallow default recall), so their absence is expected, not a
  fault.

CLI form:

```
venv/bin/python hoardcore.py _ --action research \
  --query "<the core question>" --discover 6 --recall 8 --vault sleep
```

### Action: "check"
Use this to run the three-phase vault integrity check (document chunk counts,
content hashes, vector dims). Exit `0` = pass, `1` = fail. Run it before
trusting `[V]` claims built on a long-lived vault, and with `--migrate` to
rebuild legacy 4 KB-page vaults at the configured `storage.page_size`.

Vaults carry a schema version (`PRAGMA user_version`) and each cached vector is
keyed by an embedding fingerprint (`embed_fp` = model/dim/quantize). If you ever
switch `dense_model`/`dim`/`quantize`, stale vectors are never served — `check`
reveals any dim drift and the affected rows get rebuilt on the next ingest.

CLI form:

```
venv/bin/python hoardcore.py _ --action check --migrate
```

### Action: "stats"
Use this to summarize a vault in one command — the numbers a promotion or
maintenance pass needs (sources, chunks, vectors, embedding dim/mode, schema
version, page size, DB size). Exits `0`.

- **`url`** (string, optional): `_` placeholder.
- **`action`** (string, required): Set to `"stats"`.

CLI form:

```
venv/bin/python hoardcore.py _ --action stats --vault growth
```

## Workflow Guidance

1.  **For a single article/paper**: Use `action="scrape"`. The tool will return chunks of text. Summarize these chunks for the user.
2.  **For a documentation site**: Use `action="crawl"`. This may take a few minutes. Inform the user that you are building a local index.
3.  **For follow-up questions**: Use `action="search"` with a specific query. This is instant and does not require network calls.
4.  **If a site is blocked**: Suggest using `strategy="aggressive"` or ask the user to configure a `cookie_string` in the `hoardcore.toml` config file.
5.  **For an open-ended research question**: Use the `research` action — the agentic pattern `DISCOVER -> INGEST -> RECALL -> EMIT` (merged into `hoardcore.py`):
    - `venv/bin/python hoardcore.py _ --action research --query "..." --discover 5 --recall 6`
    - `--out` optional; day-sorted to `artifacts/YYYY-MM-DD/grounding_context.md` by default.
    - **Per-topic isolation**: pass `--vault NAME` to scope the whole session to `hoardcore_data/NAME/`. Always use a dedicated vault per topic/domain (e.g. `--vault sleep`, `--vault dating`) so recall is never polluted by other topics — this is the fix for cross-topic "fetch poison". Repeat `--vault NAME` to recall a past topic's memory.
6.  **For a research deliverable (report/synthesis/audit)**: Write it into `artifacts/` with `[V]/[E]/[H]` provenance tags on every quantitative claim.
7.  **For a vault integrity check**: `venv/bin/python hoardcore.py _ --action check` — three-phase
    verification (document chunk counts, content hashes, vector dims); exit `0` pass / `1` fail.
    Run it before trusting `[V]` claims built on a long-lived vault.
    - **Page-size migration**: new vaults use 16 KB SQLite pages (`storage.page_size`);
      existing ones keep their old size until `--action check --migrate` rebuilds them
      via `VACUUM INTO` (data preserved, idempotent). Run the migrate once on legacy vaults.
8.  **Adversarial audit discipline**: Before finalizing an artifact, audit it — verify each quantitative claim against the current vault (re-run hybrid retrieval / SQL for the number). Tag anything unverifiable as `[E]` or remove it. Only claims traceable to full primary text in the current vault get `[V]`.

## Output Format

The `fetch()` function returns a list of dictionaries. Each dictionary has:

- **`text`**: The textual content of the chunk.
- **`metadata`**: A dictionary containing source URL, header path, quality score, and parser used.

## Example Interaction

**User**: "Read this paper for me: https://arxiv.org/pdf/1706.03762.pdf"

**Agent**:
1.  Calls `fetch(url="https://arxiv.org/pdf/1706.03762.pdf", action="scrape")`.
2.  Receives chunks of text.
3.  Summarizes the abstract, introduction, and conclusion for the user.
4.  Informs the user: "I have saved the full text to `hoardcore_data/arxiv.org/extracted/` for future reference."

## Important Constraints

- **Text-only**: This tool extracts **text**. It cannot "see" images or play videos, though it will download them as binary blobs.
- **Quality**: The `quality_score` in the metadata indicates extraction success. A low score (e.g., < 0.1) means the document was likely a scanned image or heavily garbled.
- **Configuration**: For advanced usage (e.g., adding cookies for authentication), the user must edit the `hoardcore.toml` file. You can guide them to do this.
- **SSRF protection is on by default** (`network.ssrf_protection = true`): fetches to private/LAN/loopback/link-local addresses and non-http(s) URLs are refused, and every redirect hop is re-validated. If research involves an internal or isolated-network target, ask the user before setting `ssrf_protection = false` in `hoardcore.toml`.
- **Plugin system**: third-party `hoardcore.*` entry-point plugins (parsers/fetchers/providers/chunkers) are discovered automatically when installed (`plugins.enabled`, default true); a plugin chunker is selected by `chunking.strategy = "plugin.<name>"`. Lifecycle hooks (`document.ingested`, `chunk.embedded`, `discovery.completed`, `search.completed`) fire on `hoardcore.EventBus`.
- **Dependencies**: Requires Python 3.11+ and the libraries listed in the `Makefile`. If a command fails, suggest running `make install`.

## Remember

You are not just a browser; you are a **knowledge hoarder**. Use HoardCore to build a permanent, local memory for yourself and the user.
