# HoardCore

Agent Harness for Retrieval & Deep Research — give your agent a memory it can prove.

Terminal tool that turns the web and local files into a permanent, local SQLite vault your AI agent can hunt with, recall from, and cite. Key-free web discovery, Cloudflare-aware fetching, hybrid FTS5 + lexical-vector retrieval, and a bounded `DISCOVER → INGEST → RECALL → EMIT` research loop with mandatory `[V]/[E]/[H]` provenance.

![Version](https://img.shields.io/badge/version-0.3.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Table of Contents

- [About](#about)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Agent Harness Integration](#agent-harness-integration)
- [Feature Tour](#feature-tour)
  - [Ingest Mode](#ingest-mode-scrape--crawl)
  - [Hybrid Retrieval](#hybrid-retrieval)
  - [Discovery Mode](#discovery-mode-key-free-web-search)
  - [Research Workflow](#research-workflow)
  - [Artifacts](#artifacts)
- [CLI Reference](#cli-reference)
- [Architecture](#architecture)
- [Package Structure](#package-structure)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## About

HoardCore is a single-file agent harness for retrieval and deep research. It hoards knowledge: it turns the web and local files into a permanent, local, searchable SQLite vault, then backs your agent's research with explicit discovery budgets, hybrid retrieval, and citeable provenance. It runs offline-capable, needs **no API keys**, and requires **no ML modeling** (no torch) — retrieval is a hybrid of SQLite FTS5 keyword search and dependency-free lexical hashing vectors fused via Reciprocal Rank Fusion (RRF).

The relationship to the agent is the point. Chat-based "deep research" is a ride — you get in, it drives, you hope it took the right route. HoardCore is a **vehicle with the hood off**: you set the discovery budget (`--discover N`), the recall depth (`--recall N`), the anti-bot escalation (`--strategy`), the output schema, and the epistemic standard (`[V]/[E]/[H]` on every claim). Consumer AI is a portal you enter; HoardCore is a protocol your agent follows.

Key characteristics:

- **Single-file core.** The entire engine is one module, `hoardcore.py`, runnable as a CLI or imported as a library.
- **No API keys, no embeddings model.** Discovery uses free HTML search endpoints (DuckDuckGo, with Mojeek fallback) through the same fetch chain as the crawler. Retrieval mixes FTS5 and hashed lexical vectors — cheap, deterministic, offline.
- **Lightweight and self-hosted.** Everything stays on your machine. Optional FlareSolverr (Docker) enables Cloudflare-heavy sites via the `aggressive` strategy; lazy binary imports mean HTML-only usage never pulls in PDF/DOCX/EPUB libraries.
- **Resilient fetch chain.** `fast` → aiohttp, `balanced` → aiohttp then curl_cffi TLS-impersonation, `aggressive` → adds FlareSolverr. Discovery adds bounded retry with exponential backoff and automatic provider fallback.
- **Hybrid retrieval.** Merges keyword (BM25-style FTS5) and vector-similarity ranks via RRF, so both exact terms and near-literal matches surface. Empty or punctuation-only queries return safely instead of crashing.
- **Research workflow.** A single `research` action runs `DISCOVER → INGEST → RECALL → EMIT`, writing a grounding-context file into the `artifacts/` directory for direct injection into an LLM.
- **Artifacts discipline.** Finished deliverables live in `artifacts/` with `[V]/[E]/[H]` provenance tags and numbered source links; a safe `write_artifact()` helper includes path-traversal protection, and deliverables are day-sorted into `artifacts/YYYY-MM-DD/`.
- **MIT licensed.** Free to use, modify, and redistribute.

---

## Prerequisites

| Requirement | Purpose | Install |
|---|---|---|
| **Python 3.11+** | Runtime (uses `tomllib`) | `python3 --version` |
| **curl_cffi** *(optional)* | TLS-fingerprint impersonation for harder anti-bot pages | Installed via Makefile |
| **FlareSolverr** *(optional)* | Fetch Cloudflare-protected pages for the `aggressive` strategy | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| **PyMuPDF / python-docx / ebooklib** *(optional)* | PDF, DOCX, EPUB parsing | Installed via Makefile |
| **rapidocr_onnxruntime** *(optional)* | OCR fallback for scanned/image-only PDF pages (local ONNX, no system deps) | `pip install .[ocr]` |

FlareSolverr is only needed for Cloudflare-protected sites and is disabled by default in the auto-generated config (override with `[solver] enabled = true` in `hoardcore.toml`). The default endpoint is `http://localhost:8191/v1`.

---

## Installation

```bash
git clone https://github.com/jjjardev/HoardCore.git
cd HoardCore
```

### Option A: Makefile (zero-config)

```bash
make install    # create venv + install core deps
make run        # smoke-test a scrape
make discover   # web-search + ingest (feed the vault from a live query)
make test       # run the pytest suite
make clean      # wipe vault, caches, and config
```

### Option B: pip editable (development install)

```bash
pip install -e ".[test]"
hoardcore        # console script
```

### Option C: run in-place (no install)

```bash
venv/bin/python hoardcore.py https://example.com --action scrape
```

The console script `hoardcore` (or `python -m hoardcore` / `python hoardcore.py`) all launch the same CLI. The vault is written to `hoardcore_data/vault.db` and deliverables to `artifacts/` (both configurable).

**Optional — OCR for scanned PDFs** (not installed by default, per the zero-dependency ethos):

```bash
make install && venv/bin/python -m pip install rapidocr_onnxruntime   # or: pip install .[ocr]
```

Once installed, image-only/scanned PDF pages are OCR'd automatically (RapidOCR, local ONNX, no system deps); without it, those pages degrade gracefully. See [Ingest](#ingest-mode-scrape--crawl).

---

## Quick Start

```bash
# Scrape a single URL and index it
python hoardcore.py https://en.wikipedia.org/wiki/Solar_power --action scrape

# Search the vault (full-text or hybrid) for previously ingested content
python hoardcore.py _ --action search --query "renewable energy negros"

# Ingest an explicit list of URLs
python hoardcore.py _ --action ingest --urls "u1,https://a.test/1,https://b.test/2"

# Discover the web for a topic and ingest the top results (no API key)
python hoardcore.py _ --action discover --query "negros renewable energy" --limit 5

# Run the full agentic research workflow -> appends grounding context to artifacts/
python hoardcore.py _ --action research --query "how does bokashi compost" --discover 5 --recall 6

# Same, but write the grounding context to a specific file
python hoardcore.py _ --action research --query "negros economy" --out artifacts/report.md

# Crawl an entire site via its sitemap
python hoardcore.py https://docs.python.org --action crawl --strategy aggressive
```

The vault persists between runs. Later searches are instant and require no network.

---

## Agent Harness Integration

**This tool is designed to be driven by an AI agent, and the only harness it has been tested against is [OpenCode](https://opencode.ai).** HoardCore's role is to give the agent a persistent, verifiable memory: the agent calls it to hoard the web and local files, then queries the vault instead of trusting its own (decaying or invented) recall.

**HoardCore is a protocol, not a portal.** Chat products are portals you enter and whose epistemic standards you accept. HoardCore specifies *how* your agent hunts (`discover` → `ingest`), recalls (`FTS5 + RRF`), verifies (`[V]/[E]/[H]` + adversarial audit), and emits (`artifacts/` with grounding context). You can plug any LLM into this protocol; the harness doesn't care who drives it, only that the driver follows the protocol.

| HoardCore does (on your machine, key-free) | The agent does (any model) |
|---|---|
| Fetch web + local files (HTML/PDF/DOCX/EPUB/OCR) | Reads the user's request and `skill.md` |
| Store everything in a persistent SQLite vault | Drives the `hoardcore` CLI (discover / ingest / search / research) |
| Hybrid-retrieve the most relevant chunks (FTS5 + vectors, RRF) | Reads the grounding context and cross-checks claims |
| Keep it offline, single-file, with zero model dependency | Writes the finished, `[V]/[E]/[H]`-tagged report |

### The harness controls the chat products hide

Because HoardCore is a harness (driven by an agent via `skill.md`), these parameters are **exposed as first-class controls**, not buried in prompt engineering:

| Parameter | What It Controls | Why It Matters |
|---|---|---|
| `--discover N` | How many sources to hunt before ingesting | More sources = broader coverage; fewer = faster turnaround |
| `--recall N` | How many chunks to retrieve per synthesis pass | More chunks = deeper context; fewer = sharper focus |
| `--strategy {fast,balanced,aggressive}` | Anti-bot escalation chain | Government sites and job boards require FlareSolverr; lightweight blogs don't |
| **Depth presets** (`research` / `deep` / `exhaustive` / `x N`) | Pass count × source quota | You decide when "enough" is enough, not the platform |
| **Output schema** | Defined in the prompt / `skill.md` | A SWOT matrix, a legal brief, a lead list — the harness emits what you specify |
| **Provenance tags** | `[V]/[E]/[H]` enforcement | You decide the epistemic standard |
| **Termination conditions** | Saturation, source quota, diminishing returns, pass cap, user interrupt | The loop is bounded and auditable |

In a chat product you *plead* ("please be thorough, cite sources, check your work"). In HoardCore you *command* via `skill.md` — and the agent **must** read the manual before it acts.

### The three companion documents

| File | Audience | Purpose |
|---|---|---|
| `README.md` | Humans / maintainers | Install, config, architecture, CLI reference |
| `AGENTS.md` | The AI agent (loaded first) | The **trigger**: a short file OpenCode auto-loads into the agent's context at every session start, mandating that the agent read `skill.md` before any task |
| `skill.md` | The AI agent | The **manual**: what the agent reads to learn **how** to use the tool |

`skill.md` is written as an agent skill (YAML frontmatter + instructions). It teaches the agent when to trigger, which actions map to which user request, the `[V]/[E]/[H]` provenance discipline, and the adversarial-audit step before finalizing an artifact.

### Where the agent reads `skill.md` first

The order is enforced by the harness, not by habit:

1. **Session start** — OpenCode (and other harnesses that honor `AGENTS.md`) loads the repo-root `AGENTS.md` into the agent's context automatically. It contains one hard rule: *"Before any task, you MUST read `skill.md` in full."*
2. **First task** — the agent obeys that rule and reads `skill.md` before touching the web, the vault, or `artifacts/`. Everything the agent then does (scrape / crawl / search / discover / research) is driven by `skill.md`'s action mapping.
3. **Ongoing** — `skill.md` stays the reference: if a task needs an action the agent is unsure of, it re-consults `skill.md`, never its own memory.

So the chain is: **OpenCode auto-loads `AGENTS.md` → `AGENTS.md` forces `skill.md` → `skill.md` drives the CLI.** If your harness does not auto-read `AGENTS.md`, treat that file as the instruction to point the agent at first.

### DeepResearch: the Hardcore Research Loop

**DeepResearch is the default research mode.** Any open-ended research request — "research", "investigate", "deep dive", "find out about" — triggers a full end-to-end investigation instead of an ad-hoc scrape/search. The agent reads the behavior out of `skill.md` and runs a **bounded, adversarial** loop:

1. **Parse** the directive into the core question.
2. **Hunt** — DISCOVER the web for high-authority primary sources (key-free) and INGEST them into the local vault (uses `--strategy aggressive` past anti-bot protection).
3. **Recall** — hybrid-retrieve the **5–10 most relevant chunks**, discarding any that feel flimsy or lack context.
4. **Synthesize** — emit `grounding_context` (source URLs + hybrid scores + distinct sources), then write the deliverable into `artifacts/`.
5. **Provenance** — tag every claim `[V]/[E]/[H]` and **adversarially re-query the vault** to confirm each `[V]` before presenting it.

**Depth presets — control effort, not a raw number.** At kickoff the agent maps a direction to a budget, then stops as soon as **any one** trips:

| Direction | Passes | Sources |
|-----------|--------|---------|
| `research` (default) | 3 | ≥5 |
| `research deep` | 6 | ≥8 |
| `research exhaustive` | up to 10 | ≥15 |

A raw-count override exists for strictness: `research x 10 <topic>` hard-caps the loop at exactly 10 passes. The stop conditions are answer saturation (two re-queries with no new `[V]` claim), the distinct-source quota, diminishing returns (identical re-ranking), the pass cap, or a user interrupt. **Conversational deepening** — after a stop the user can simply say **"go deeper"** to re-enter one more pass (retaining all prior evidence, interruptible, capped by the session's pass budget) or **"that's enough"** to finalize — so nobody has to predict the right count up front. On stop the agent runs the audit, emits the artifact (labeled `[INCOMPLETE — N passes]` if a budget guard or interrupt cut it short), and returns a **3-bullet Executive Summary**.

### Workflow examples in an agent harness

The examples below assume you run an agent inside the `HoardCore` project directory and are chatting with it. In each, the agent reads `skill.md` first, then drives the `hoardcore` CLI.

**0. DeepResearch — the Hardcore Research Loop (default)**

```
You: research deep the economic impact of renewable energy in Negros

Agent: (loads skill.md -> recognizes 'research' + 'deep' preset
       -> opens the Hardcore Research Loop, budget: 6 passes x >=8 sources)
  venv/bin/python hoardcore.py _ --action research \
     --query "economic impact renewable energy Negros" \
     --discover 6 --recall 8 --strategy aggressive
  -> "Engaging the Hardcore Research Loop... budget: 6 passes x >=8 sources"
  -> DISCOVER (web) -> INGEST (vault) -> RECALL 5-10 chunks -> EMIT
  -> stops on saturation / source quota / pass budget / user interrupt
  -> writes artifacts/*.md with source URLs + hybrid scores + distinct-sources
  -> tags each claim [V]/[E]/[H] and adversarially audits them against the vault
  -> replies with a 3-bullet Executive Summary; full evidence in the artifact file

You: go deeper
Agent: (re-enters the loop for one more pass, retaining all prior evidence)
```

**1. Research a new topic from scratch**

```
You: Summarize the latest state of renewable energy in Negros Occidental,
     with sources.

Agent: (loads skill.md -> runs)
  venv/bin/python hoardcore.py _ --action research \
     --query "negros occidental renewable energy 2026" --discover 6 --recall 8
  -> writes artifacts/grounding_context.md  (sources + scores)
  -> drafts a synthesis into artifacts/, tagging each figure [V]/[E]/[H]
  -> re-queries the vault to verify the key numbers before showing you
```

**2. Persistent memory across sessions**

```
You: I had you ingest docs on solar earlier. Now: "which figure did we capture
     on bagasse vs wind capacity?"

Agent: (no network needed)
  venv/bin/python hoardcore.py _ --action search --query "bagasse wind capacity"
  -> instant, offline answer grounded in the local vault, with source URLs
```

**3. A single source, now**

```
You: Read and summarize this: https://arxiv.org/pdf/2401.xxxxx.pdf

Agent: venv/bin/python hoardcore.py https://arxiv.org/pdf/2401.xxxxx.pdf --action scrape
  -> fetches, parses, chunks, indexes, returns the text to summarize
  -> FYI: also saved to hoardcore_data/.../extracted/ for future lookup
```

**4. An explicit list of documents**

```
You: Soak up these three URLs so I can ask follow-ups later:
  https://a.example/report , https://b.example/analysis , https://c.example/paper

Agent: venv/bin/python hoardcore.py _ --action ingest \
       --urls "https://a.example/report,https://b.example/analysis,https://c.example/paper"
  -> indexes all three; your follow-ups now hit the vault instantly
```

**5. Reach a Cloudflare-protected page**

```
You: That page is behind Cloudflare. Get me the text of
  https://protected.example.com/article

Agent: venv/bin/python hoardcore.py https://protected.example.com/article \
       --action scrape --strategy aggressive
  -> aiohttp -> curl_cffi -> (FlareSolverr if enabled) until it succeeds
```

Each example ends with content that is **grounded and recallable later** — the vault is the source of truth, not the agent's memory.

### Agent-friendly fact

Because HoardCore is key-free, offline-capable, and has no ML modeling, it runs inside most sandboxes and CI environments the moment its dependencies are installed. The `hoardcore.toml` config is auto-generated, so the agent can bootstrap a vault on first run with no setup ceremony.

---

## Feature Tour

### Ingest Mode (Scrape / Crawl)

**Scrape** fetches a single URL (HTML, PDF, DOCX, EPUB), cleans it, chunks it semantically by headings, and indexes it. **Crawl** discovers a site's URLs via `robots.txt` / sitemap and ingests them with a bounded, semaphore-limited worker pool (`crawler.parallel_workers`).

The pipeline for each document:

1. **Fetch** — tries the strategy chain (aiohttp → curl_cffi → FlareSolverr) until one returns content.
2. **Parse** — HTML via `trafilatura` + `readability` with a self-selecting-best fallback; PDF/DOCX/EPUB via lazy-loaded binaries; else raw-text strip. **Scanned PDFs:** pages with no extractable text are auto-OCRed via RapidOCR (optional `pip install .[ocr]`, fully local ONNX, no system deps); OCR'd pages are flagged in metadata (`parser: pymupdf+ocr`, `ocr_pages`).
3. **Junk-filter** — boilerplate/redirect/404/captcha pages and near-empty extractions are detected and refused entry to the vault.
4. **Chunk** — semantic splitting respecting headers (or paragraphs for binaries).
5. **Store** — chunks persisted to FTS5, mirrored as markdown + chunks JSON under `hoardcore_data/`, and embeddings backfilled.

### Hybrid Retrieval

`search` fuses two candidate lists via Reciprocal Rank Fusion (RRF):
- **FTS5** keyword ranking across chunk text (BM25-style).
- **Lexical vector** similarity via dependency-free sparse hashing (FNV-1a feature hashing of word + 3-gram shingles into a 256-dim unit vector).

Because it's lexical, a query that doesn't literally match can still surface near-similar chunks (e.g., `sol*r` → `solar`). Empty, whitespace, and punctuation-only queries return `[]` instead of raising.

Queries are sanitized: operator characters (`" ( ) * ^ : -`) are stripped and tokens quoted, so free-text input cannot alter query semantics or raise FTS syntax errors.

### Discovery Mode (key-free web search)

`discover` turns a plain-language query into ingested sources *without an API key*. It hits DuckDuckGo's HTML endpoint via the same resilient fetch chain (so a rate-limited/shaped search page gets retried and can even be solved by FlareSolverr), with Mojeek as an automatic fallback provider. Only the top-N ranked results (`discovery.top_rank`) are ingested, with bounded retry + exponential backoff on transient failures.

### Research Workflow

`research` is the full agentic loop in one command:
```
[1/DISCOVER] web-search the question, ingest top sources
[2/RECALL]   hybrid-retrieve the best chunks
[3/EMIT]     write a grounding-context file
```
The emitted file lists each retrieved chunk with its source URL and hybrid score, plus a distinct-sources summary and a **Source Links / Citations** block — ready to be injected verbatim as grounding context for an LLM.

### Artifacts

Finished deliverables live in `artifacts/` (configurable via `storage.artifacts_dir`), day-sorted into `artifacts/YYYY-MM-DD/` subfolders. The tool ships `write_artifact(filename, content)` which refuses path-traversing names, plus `citation_list()` to render the source-links block. Research outputs carry provenance tags:

- `[V]` — verified against full primary text in the current vault
- `[E]` — extracted/captured earlier, not in the current vault
- `[H]` — hypothesis / authored framing

Example artifacts already produced: the renewable-island synthesis, its adversarial audit, and the master research portfolio.

---

## CLI Reference

```
hoardcore [URL] [options]
```

### Actions

| Action | Purpose |
|---|---|
| `scrape` *(default)* | Fetch + ingest a single URL or document. Requires a URL positional. |
| `crawl` | Crawl a whole site via sitemap/robots.txt. Requires a domain URL positional. |
| `search` | Query the vault with `--query`; restrict to a domain by passing its host as the positional. |
| `ingest` | Index an explicit URL list given as a comma/space separated `--urls` string. |
| `discover` | Web-search `--query`, ingest the top `--limit` results. |
| `research` | Run `discover -> ingest -> recall -> emit`; writes to `--out` or `artifacts/grounding_context.md`. |

Use a positional of `_` when an action (e.g. `search`, `discover`, `research`, `ingest`) does not need a URL.

### Flags

| Flag | Description |
|---|---|
| `--action ACTION` | One of `scrape`, `crawl`, `search`, `ingest`, `discover`, `research`. |
| `--strategy S` | `fast`, `balanced` (default from config), or `aggressive`. |
| `--query Q` | Required for `search`, `discover`, `research`. |
| `--limit N` | Web results to ingest for `discover`. |
| `--urls U1,U2,U3` | Explicit URL list for `ingest`. |
| `--discover N` | Sources to discover first in `research` (default 5). |
| `--recall N` | Chunks to retrieve in `research` (default 6). |
| `--out PATH` | Output file for `research` (default `artifacts/grounding_context.md`). |
| `--force` | Ignore the cache and re-fetch / re-index. |

### Configuration file (`hoardcore.toml`)

Created automatically on first run. Key sections:

| Section | Notable keys |
|---|---|
| `[general]` | `timeout_seconds`, `max_retries`, `user_agent` |
| `[network]` | `default_strategy` (`fast`/`balanced`/`aggressive`), `enable_preflight` |
| `[auth]` | `cookie_string` (e.g. `cf_clearance=...; session=...`) |
| `[solver]` | `enabled`, `url`, `solver_timeout` |
| `[storage]` | `root_dir`, `artifacts_dir`, `artifacts_by_day`, `save_binary`, `save_raw_html` |
| `[parsers]` | `enable_pdf`, `enable_docx`, `enable_epub`, `extract_pdf_tables` |
| `[crawler]` | `respect_robots`, `sitemap_limit`, `parallel_workers` |
| `[indexer]` | `enable_fts`, `search_limit` |
| `[embeddings]` | `enabled`, `dim`, `hybrid_search`, `top_k` |
| `[discovery]` | `provider`, `top_rank`, `max_retries`, `backoff_seconds` |
| `[chunking]` | `max_tokens`, `overlap_tokens`, `strategy` |
| `[cache]` | `ttl_seconds` |

---

## Architecture

### Processing Pipeline (single document)

```
  +-------------------------------------------------------------+
  | Fetch  aiohttp -> (curl_cffi) -> (FlareSolverr)              |
  +-------------------------------------------------------------+
              |  (text, binary, content_type)
              v
  +-------------------------------------------------------------+
  | Parse   trafilatura + readability  |  PDF / DOCX / EPUB      |
  |         (HTML)                     |  (lazy binaries)        |
  +-------------------------------------------------------------+
              |  markdown + parser_meta
              v
  +-------------------------------------------------------------+
  | Junk-filter  boilerplate / captcha / 404 / empty detection   |
  +-------------------------------------------------------------+
              |  clean markdown
              v
  +-------------------------------------------------------------+
  | Chunk   heading-aware semantic chunking (or paragraph)       |
  +-------------------------------------------------------------+
              |  List[Chunk]
              v
  +-------------------------------------------------------------+
  | Store   SQLite FTS5 + chunk_vectors | markdown/chunks on disk|
  +-------------------------------------------------------------+
```

### Hybrid Retrieval (RRF)

`_search_hybrid()` computes two candidate lists (`k=60` RRF constant):
- **FTS**: `SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank`.
- **Vector**: brute-force cosine over `chunk_vectors` (fine for a hoard vault), top-`top_k`.

Each candidate contributes `1 / (k + rank + 1)`; results are sorted by the sum and the top-N returned. The brute-force vector scan is O(N) per query — ideal for thousands of chunks, not millions.

### Resilience & DB Hygiene

A fetch chain runs `aiohttp` then `curl_cffi` then `FlareSolverr` (aggressive). Discovery wraps fetches in bounded retries with exponential backoff and falls back DuckDuckGo → Mojeek. All SQLite access flows through `VaultManager._db()`:

```
conn = sqlite3.connect(db_path); conn.execute("PRAGMA busy_timeout=5000")
try:      yield conn, cursor; conn.commit()
except:   conn.rollback()
finally:  conn.close()
```

WAL mode and `synchronous=NORMAL` balance durability against speed. FTS cleanup is handled by an `AFTER DELETE` trigger on documents.

---

## Package Structure

```
HoardCore/
    hoardcore.py           The entire engine (config, fetcher, parsers, chunker,
                           crawler, discovery, vault, CLI, research action)
                           (research.py was merged into this single file)
    hoardcore.toml         Generated config on first run (git-ignored)
    Makefile               install / run / discover / test / clean
    pyproject.toml         Packaging, deps + extras, console script
    AGENTS.md              Agent trigger doc — auto-loaded by OpenCode at
                           session start; mandates reading skill.md first
    skill.md               Uses-guide / agent skill (OpenCode harness doc)
    CHANGELOG.md           Release history (SemVer)
    artifacts/               Runtime deliverables (git-ignored, not in repo)
    tests/
        conftest.py            TempConfig + vault / chunk fixtures
        test_vault.py          indexing, RRF, backfill, empty-query safety, _fts_query
        test_network.py        fetch chain fallback, provider parsing/fallback,
                               research strategy forwarding
        test_junk.py           boilerplate/empty/real-content detection
        test_crawler.py        sitemap/robots/discovery (no network I/O)
    hoardcore_data/         The vault (vault.db, per-domain binaries/extracted)
```

---

## Development

### Quick commands

```bash
make install            # create venv + install deps
make test               # run pytest suite
make clean              # wipe vault, caches, and config
```

### Manual

```bash
venv/bin/python -m pip install -e ".[test]"
venv/bin/python -m pytest tests/ -v     # 37 tests
```

### Code standards

- **Python 3.11+**, `from __future__ import annotations` throughout
- **Zero global mutable state** where possible — one note: `ConfigManager` is a well-behaved singleton per-process (fine for the CLI)
- **DB access always through `_db()`** — the context manager guarantees commit/rollback/close
- **Optional heavy dependencies lazy-imported** — HTML-only usage never pulls PDF/DOCX/EPUB libraries
- **Annotated signatures** (`Optional`, `Tuple`, `List`) on all public methods

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Status 403 Blocked` on a protected site | Anti-bot challenging connection | Use `--strategy aggressive`; ensure FlareSolverr is running; set `cookie_string` in `hoardcore.toml` |
| `aiohttp: Status 202` on discovery / FlareSolverr timeouts | Rate-limit or proxy-shaped search page | Discovery auto-retries with backoff and falls back to Mojeek; rerun or lower `--limit` |
| Discovery returns nothing | Search provider empty | Mojeek fallback is automatic; increase `discovery.max_retries` / `backoff_seconds` |
| `PyMuPDF (fitz) not installed` printed | Optional PDF lib missing | `make install` (installs PyMuPDF) or `pip install pymupdf` |
| `python-docx` / `ebooklib` message | Optional binaries missing | `pip install python-docx ebooklib` |
| Search returns empty for an unusual query | FTS operator / empty tokens | Search is safe now (returns `[]`, never raises); try hybrid mode |
| Vault garbled / bad results | Indexed junk before detection | Junk detection now filters boilerplate/empty; re-ingest with `--force` after upgrade |
| Cache expiry surprises | `cache.ttl_seconds` | Default 24h (`86400`); set to `0` to never expire |

---

## Contributing

Bug reports, feature requests, and pull requests are welcome.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/description`)
3. Set up: `make install`
4. Make changes; add/update tests in `tests/`
5. Run `make test` (all tests must pass)
6. Commit with a conventional prefix (`fix:`, `feat:`, `refactor:`, `docs:`, `chore:`)
7. Push and open a pull request

Areas open to contribution:
- A `--log-level` flag and structured exit codes
- A config version single-source-of-truth
- Multi-process config reload / real library confidence
- Expanded test coverage (crawl, ingest, PDF/DOCX/EPUB, CLI end-to-end)
- CI/CD with GitHub Actions

---

## License

MIT License. See [LICENSE](LICENSE) for full text.

This tool is intended for personal, research, and educational use. When accessing third-party websites, respect their terms of service and robots.txt. Support the sites and creators you rely on.
