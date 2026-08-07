# HoardCore-RAG (HCRAG)

Lightweight, fully-local LLM document ingestion engine that scrapes, crawls, and searches the web into a persistent SQLite vault with hybrid FTS5 + lexical-vector retrieval, Cloudflare-aware fetching, and key-free web discovery. Vendored into a ready-made research workflow (`discover -> ingest -> recall -> emit`).

![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Table of Contents

- [About](#about)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [OpenCode (AI Harness) Integration](#opencode-ai-harness-integration)
  - [Workflow examples in OpenCode](#workflow-examples-in-opencode)
- [Feature Tour](#feature-tour)
  - [Ingest (Scrape / Crawl)](#ingest-scrape--crawl)
  - [Hybrid Retrieval](#hybrid-retrieval)
  - [Discovery (key-free web search)](#discovery-key-free-web-search)
  - [Research Workflow](#research-workflow)
  - [Artifacts](#artifacts)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Package Structure](#package-structure)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## About

HoardCore-RAG is a hardcore document ingestion engine for AI agents. It hoards knowledge: it turns the web and local files into a permanent, local, searchable SQLite vault that gives you and your agents persistent memory. It runs fully offline-capable, needs **no API keys**, and requires **no ML modeling** (no torch) — retrieval is a hybrid of SQLite FTS5 keyword search and dependency-free lexical hashing vectors fused via Reciprocal Rank Fusion (RRF).

Key characteristics:

- **Single-file core.** The entire engine is one module, `hoardcore.py`, runnable as a CLI or imported as a library.
- **No API keys, no embeddings model.** Discovery uses free HTML search endpoints (DuckDuckGo, with Mojeek fallback) through the same fetch chain as the crawler. Retrieval mixes FTS5 and hashed lexical vectors — cheap, deterministic, offline.
- **Lightweight and self-hosted.** Everything stays on your machine. Optional FlareSolverr (Docker) enables Cloudflare-heavy sites via the `aggressive` strategy; lazy binary imports mean HTML-only usage never pulls in PDF/DOCX/EPUB libraries.
- **Resilient fetch chain.** `fast` → aiohttp, `balanced` → aiohttp then curl_cffi TLS-impersonation, `aggressive` → adds FlareSolverr. Discovery adds bounded retry with exponential backoff and automatic provider fallback.
- **Hybrid retrieval.** Merges keyword (BM25-style FTS5) and vector-similarity ranks via RRF, so both exact terms and near-literal matches surface. Empty or punctuation-only queries return safely instead of crashing.
- **DB hygiene.** Every write goes through a context manager that commits on success, rolls back on exception, and always closes the connection (WAL + `busy_timeout`). No leaked connections, no dangling transactions.
- **Research workflow.** A single `research` action runs `DISCOVER -> INGEST -> RECALL -> EMIT`, writing a grounding-context file into the `artifacts/` directory for direct injection into an LLM.
- **Artifacts discipline.** Finished deliverables live in `artifacts/` with `[V]/[E]/[H]` provenance tags; a safe `write_artifact()` helper includes path-traversal protection.
- **MIT licensed.** Free to use, modify, and redistribute.

---

## Prerequisites

| Requirement | Purpose | Install |
|---|---|---|
| **Python 3.11+** | Runtime (uses `tomllib`) | `python3 --version` |
| **curl_cffi** *(optional)* | TLS-fingerprint impersonation for harder anti-bot pages | Installed via Makefile |
| **FlareSolverr** *(optional)* | Cloudflare bypass for the `aggressive` strategy | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| **PyMuPDF / python-docx / ebooklib** *(optional)* | PDF, DOCX, EPUB parsing | Installed via Makefile |

FlareSolverr is only needed for Cloudflare-protected sites and is disabled by default in the shipped config sample (override with `[solver] enabled = true` in `hoardcore.toml`). The default endpoint is `http://localhost:8191/v1`.

---

## Installation

```bash
git clone <your-repo-url>/HoardCore-RAG.git
cd HoardCore-RAG
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

## OpenCode (AI Harness) Integration

**This tool is designed to be driven by an AI agent, and the only harness it has been tested against is [OpenCode](https://opencode.ai).** HoardCore-RAG's role is to give the agent a persistent, verifiable memory: the agent calls it to hoard the web and local files, then queries the vault instead of trusting its own (decaying or invented) recall.

### The two companion documents

| File | Audience | Purpose |
|---|---|---|
| `README.md` | Humans / maintainers | Install, config, architecture, CLI reference |
| `skill.md` | The AI agent | What the agent reads to learn **how** to use the tool |

`skill.md` is written as an agent skill (YAML frontmatter + instructions). It teaches the agent when to trigger, which actions map to which user request, the `[V]/[E]/[H]` provenance discipline, and the adversarial-audit step before finalizing an artifact. **Point the agent at `skill.md` first;** it is the operating manual the model is expected to read and follow.

### How the agent uses it (example reasoning)

1. **User asks to research a topic** → agent runs `research`, which discovers → ingests → recalls → emits a grounding-context file into `artifacts/`.
2. **Agent writes the deliverable** into `artifacts/`, tagging every quantitative claim `[V]` (verified in the current vault), `[E]` (captured earlier), or `[H]` (hypothesis).
3. **Agent audits itself** — re-hybrid-queries the vault for each claimed figure. Anything it cannot retrace to full primary text is demoted to `[E]` or dropped.
4. **Repeat queries** hit the vault (`--action search`), which is instant and offline.

### Workflow examples in OpenCode

The examples below assume you run OpenCode inside the `HoardCore-RAG` project directory and are chatting with the agent. In each, the agent reads `skill.md` first, then drives the `hoardcore` CLI.

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

**5. Bypass a Cloudflare wall**

```
You: That page is behind Cloudflare. Get me the text of
  https://blocked.example.com/article

Agent: venv/bin/python hoardcore.py https://blocked.example.com/article \
       --action scrape --strategy aggressive
  -> aiohttp -> curl_cffi -> (FlareSolverr if enabled) until it succeeds
```

Each example ends with content that is **grounded and recallable later** — the vault is the source of truth, not the agent's memory.

### Agent-friendly fact

Because HCRAG is key-free, offline-capable, and has no ML modeling, it runs inside most sandboxes and CI environments the moment its dependencies are installed. The `hoardcore.toml` config is auto-generated, so the agent can bootstrap a vault on first run with no setup ceremony.

---

## Feature Tour

### Ingest (Scrape / Crawl)

**Scrape** fetches a single URL (HTML, PDF, DOCX, EPUB), cleans it, chunks it semantically by headings, and indexes it. **Crawl** discovers a site's URLs via `robots.txt` / sitemap and ingests them with a bounded, semaphore-limited worker pool (`crawler.parallel_workers`).

The pipeline for each document:

1. **Fetch** — tries the strategy chain (aiohttp → curl_cffi → FlareSolverr) until one returns content.
2. **Parse** — HTML via `trafilatura` + `readability` with a self-selecting-best fallback; PDF/DOCX/EPUB via lazy-loaded binaries; else raw-text strip.
3. **Junk-filter** — boilerplate/redirect/404/captcha pages and near-empty extractions are detected and refused entry to the vault.
4. **Chunk** — semantic splitting respecting headers (or paragraphs for binaries).
5. **Store** — chunks persisted to FTS5, mirrored as markdown + chunks JSON under `hoardcore_data/`, and embeddings backfilled.

### Hybrid Retrieval

`search` fuses two candidate lists via Reciprocal Rank Fusion (RRF):
- **FTS5** keyword ranking across chunk text (BM25-style).
- **Lexical vector** similarity via dependency-free sparse hashing (FNV-1a feature hashing of word + 3-gram shingles into a 256-dim unit vector).

Because it's lexical, a query that doesn't literally match can still surface near-similar chunks (e.g., `sol*r` → `solar`). Empty, whitespace, and punctuation-only queries return `[]` instead of raising.

Queries are sanitized: operator characters (`" ( ) * ^ : -`) are stripped and tokens quoted, so free-text input cannot alter query semantics or raise FTS syntax errors.

### Discovery (key-free web search)

`discover` turns a plain-language query into ingested sources *without an API key*. It hits DuckDuckGo's HTML endpoint via the same resilient fetch chain (so a rate-limited/shaped search page gets retried and can even be solved by FlareSolverr), with Mojeek as an automatic fallback provider. Only the top-N ranked results (`discovery.top_rank`) are ingested, with bounded retry + exponential backoff on transient failures.

### Research Workflow

`research` is the full agentic loop in one command:
```
[1/DISCOVER] web-search the question, ingest top sources
[2/RECALL]   hybrid-retrieve the best chunks
[3/EMIT]     write a grounding-context file
```
The emitted file lists each retrieved chunk with its source URL and hybrid score, plus a distinct-sources summary — ready to be injected verbatim as grounding context for an LLM.

### Artifacts

Finished deliverables live in `artifacts/` (configurable via `storage.artifacts_dir`). The tool ships `write_artifact(filename, content)` which refuses path-traversing names. Research outputs carry provenance tags:

- `[V]` — verified against full primary text in the current vault
- `[E]` — extracted/captured earlier, not in the current vault
- `[H]` — hypothesis / authored framing

Example artifacts already produced: the renewable-island synthesis, its adversarial audit, and the master research portfolio.

---

## CLI Reference

```
hoardcore [URL] [options]
```

### Actions (the sub-command concept)

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
| `[storage]` | `root_dir`, `artifacts_dir`, `save_binary`, `save_raw_html` |
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
HoardCore-RAG/
    hoardcore.py           The entire engine (config, fetcher, parsers, chunker,
                           crawler, discovery, vault, CLI, research action)
                           (research.py was merged into this single file)
    hoardcore.toml         Generated config (sample shipped in repo)
    Makefile               install / run / discover / test / clean
    pyproject.toml         Packaging, deps + extras, console script
    skill.md               Uses-guide / agent skill (OpenCode harness doc)
    artifacts/               Runtime deliverables (git-ignored, not in repo) —
    tests/
        conftest.py            TempConfig + vault / chunk fixtures
        test_vault.py          9 tests: indexing, RRF, backfill, empty-query safety, _fts_query
        test_network.py        8 tests: fetch chain fallback, provider parsing/fallback
        test_junk.py           4 tests: boilerplate/empty/real-content detection
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
venv/bin/python -m pytest tests/ -v     # 21 tests
```

### Test structure

```
tests/
    conftest.py        TempConfig (isolated vault in tmp), chunk fixtures
    test_vault.py      indexing, update/delete, db-leak, TTL, backfill,
                       hybrid ranking, lexical-similarity, empty/punctuation query safety,
                       _fts_query operator stripping
    test_network.py    strategy chain (fast/balanced/aggressive), all-fail raise,
                       DuckDuckGo & Mojeek link parsing, provider fallback, empty query
    test_junk.py       empty extraction, boilerplate/captcha/404, real content,
                       short-low-quality vs short-valid
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