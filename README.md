# HoardCore

Adaptive research engine for AI agents - a memory it can prove, tools you can swap, and a loop that adapts.

![Version](https://img.shields.io/badge/version-0.17.4-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Contents

- [What it is](#what-it-is)
- [How it adapts](#how-it-adapts)
- [Install](#install)
- [Quick start](#quick-start)
- [Working with an agent](#working-with-an-agent)
- [Core capabilities](#core-capabilities)
- [CLI reference](#cli-reference)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## What it is

HoardCore is a single-file Python engine plus CLI. It fetches web pages and local documents into a persistent SQLite vault, retrieves from that vault with hybrid search (SQLite FTS5 fused with ONNX vector search via Reciprocal Rank Fusion), and gives every claim a provenance tag that can be machine-audited.

**Research mode on crack, with the hood off.** The research loop has depth presets and termination guards. A hard question is split into fronts that are re-hunted when evidence runs thin. Fetching escalates from plain HTTP to TLS impersonation to a browser solver. Four plugin entry points and a lifecycle event bus let you swap in parsers, fetchers, discovery providers, and chunkers. The engine is **extensible, not self-modifying**: it does not write new tools at runtime.

HoardCore is **not an agent harness**. It hosts no LLM and manages no context. Your harness (OpenCode, Claude Code, or any other) hosts the agent; the agent reads `skill.md` and calls HoardCore through its CLI. The relationship is a protocol, not a portal: you set the discovery budget (`--discover N`), recall depth (`--recall N`), fetch strategy (`--strategy`), vault scope (`--vault NAME`), and epistemic standard (`[V]/[E]/[H]`) instead of hoping a chat product applied them.

Retrieval is dense by default: an ONNX-quantized sentence-transformer runs on `onnxruntime` (no PyTorch, no GPU), with an automatic sparse-hash fallback if the model is unavailable. The vault is schema-versioned and embedding-fingerprinted, so a model or config change never serves stale vectors.

## How it adapts

| What adapts | Mechanism |
|---|---|
| Research depth | `research` depth presets (3/6/10 passes, 5/8/15 sources) with saturation and diminishing-return termination guards |
| Attack on a hard question | `solve` skips fronts the vault already answers, then re-hunts unsaturated fronts with refined queries on later passes |
| Fetch strategy | `fast` to `balanced` to `aggressive`: aiohttp, then concurrent aiohttp + curl_cffi, then FlareSolverr for challenge pages, with backoff and retry |
| Retrieval backend | Dense ONNX embeddings by default, sparse hashing as fallback, RRF fusion either way |
| Tool surface | Four plugin groups (`parsers`, `fetchers`, `providers`, `chunkers`) and four event hooks, all failure-tolerant |

The judgment stays with the agent. HoardCore supplies the adaptive evidence machinery and the audit trail.

## Install

Requirements: Python 3.11+, and Docker only if you need Cloudflare-protected pages.

```bash
git clone https://github.com/jjjardev/HoardCore.git
cd HoardCore
make install                 # venv + core deps: fastembed, onnxruntime, curl_cffi, parsers, test/lint tools
make test                    # 352 tests
```

Editable install for development: `pip install -e ".[test]"` gives a `hoardcore` console script. Running in place works too: `venv/bin/python hoardcore.py ...`. All three launch the same CLI. The vault lives at `hoardcore_data/vault.db` and deliverables at `artifacts/` by default; both are git-ignored.

**FlareSolverr (only for protected pages).** Open pages work without it. For challenge pages, start the container and enable the solver:

```bash
docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr
```

```toml
[solver]
enabled = true
```

The default `aggressive` strategy then routes challenges through `http://localhost:8191/v1`. One browser session is reused per run and destroyed on exit, so a bulk crawl does not re-pay the challenge for every URL. Extra endpoints go in `[solver] urls`; a dead endpoint degrades instead of failing the run. Without Docker, leave the solver off and rely on curl_cffi, accepting more blocks.

Optional: `pip install rapidocr_onnxruntime` (or `pip install .[ocr]`) enables local OCR of scanned/image-only PDF pages.

## Quick start

**Easiest path with an agent:** clone the repo, open your harness inside the `HoardCore/` folder, and either say *"set up HoardCore"* or just ask your first research question. `AGENTS.md` makes the agent read `skill.md` before any task, and `skill.md` tells it to run `make install` when Python prerequisites are missing, so the first real request usually bootstraps the venv on its own. To be certain, ask explicitly:

> Read `skill.md` first, then set up HoardCore (`make install`) and start FlareSolverr if I need protected pages.

The commands below are the manual path. From the repo root, after install:

```bash
python hoardcore.py                                   # prints a short guide; scrapes nothing
python hoardcore.py scrape https://example.com         # fetch one page into the vault
python hoardcore.py search "example domain"            # recall what you stored
python hoardcore.py research "what is reciprocal rank fusion" --discover 3 --recall 5
python hoardcore.py verify "RRF ignores scores and uses rank positions"   # exit 0/1/2
python hoardcore.py stats                              # vault summary
```

Commands use the short form: `hoardcore ACTION [ARG] [flags]`. The legacy form (`python hoardcore.py _ --action search --query "..."`, or `URL --action scrape`) remains fully supported and is what `skill.md` teaches agents to use. `python hoardcore.py` with no arguments prints the quick start instead of trying to scrape a placeholder.

More one-liners:

```bash
python hoardcore.py ingest "https://a.test/1,https://b.test/2"
python hoardcore.py discover "renewable energy negros" --limit 5
python hoardcore.py crawl https://docs.python.org --strategy aggressive
python hoardcore.py audit artifacts/2026-11-22/report.md --vault mytopic
python hoardcore.py lint artifacts/2026-11-22/report.md --strict
python hoardcore.py check --migrate
python hoardcore.py export backup.jsonl --vault mytopic
python hoardcore.py stats --json
```

## Working with an agent

HoardCore is usually driven by an agent, not typed by a human. Three documents define the contract:

| File | Audience | Role |
|---|---|---|
| `README.md` | Humans / maintainers | Install, config, architecture, CLI reference |
| `AGENTS.md` | The agent | Trigger file auto-loaded at session start; requires reading `skill.md` first |
| `skill.md` | The agent | Operating manual: action mapping, provenance rules, audit step |

The chain is: harness loads `AGENTS.md` -> `AGENTS.md` forces `skill.md` -> `skill.md` drives the CLI. If your harness does not auto-load `AGENTS.md`, start by telling the agent to read `skill.md` first.

For any open-ended request, the agent runs the bounded **Hardcore Research Loop**: parse the question, discover and ingest high-authority sources, hybrid-recall 5-10 chunks, emit grounding context, then adversarially audit every `[V]` tag. Depth is a direction, not a guessed number:

| Direction | Passes | Sources |
|---|---|---|
| `research` (default) | 3 | at least 5 |
| `research deep` | 6 | at least 8 |
| `research exhaustive` | up to 10 | at least 15 |

`research x N <topic>` is a prompt directive to the agent that caps passes at exactly N; it is not a CLI flag. The loop stops on answer saturation, source quota, diminishing returns, pass cap, or user interrupt, emits an artifact, and returns a three-bullet executive summary. "Go deeper" adds one more pass; "enough" finalizes.

Typical agent calls (legacy form, as in `skill.md`):

```bash
venv/bin/python hoardcore.py _ --action research --query "lunar exploration 2026" --discover 6 --recall 8
venv/bin/python hoardcore.py _ --action search --query "bagasse wind capacity"
venv/bin/python hoardcore.py _ --action verify --claim "Change-of-control clause is unenforceable per Delaware Ch. 251"
```

First-class controls the agent can expose directly: `--discover N` (`0` = recall-only, no web hunt), `--recall N`, `--strategy`, `--vault NAME` (or `a,b,c` for read-only companion vaults), depth presets, output schema, and `[V]/[E]/[H]` tags.

## Core capabilities

### Ingest: scrape, crawl, local files

`scrape` fetches one URL; `crawl` follows sitemap/robots URLs with a bounded worker pool; `ingest` takes an explicit `--urls` list; `local` indexes files from `local_inputs/` with no network (`.pdf .docx .epub .html .htm .txt .md`, `--path` scopes inside that folder, `--list` previews).

Each document goes through: fetch (strategy chain) -> parse (trafilatura + readability for HTML; PyMuPDF / python-docx / ebooklib for binaries, optional OCR) -> junk filter (boilerplate, 404s, captchas, transport-error stubs) -> heading-aware chunking -> store in FTS5 plus a content-addressed chunk table, with embeddings backfilled. Batch ingests stream `[ingest n/N]` progress with an ETA and close with an `INGEST LEDGER` naming skipped URLs and reasons. Sitemap indexes are recursed; a crawl that finds URLs but indexes nothing is an error, not a pass. SSRF protection is on by default: non-HTTP schemes, private/LAN/loopback/link-local addresses, and DNS-special names are refused, and redirect hops are re-validated.

### Hybrid retrieval

`search` fuses two candidate lists with RRF (`k=60`): FTS5 keyword ranking and dense vector similarity (or sparse hashing if the dense model is absent). Hits carry:

- **Confidence bands** (`high`/`medium`/`low`). Default relative mode ranks within the returned set; only a keyword-backed set can crown `high`. `conf_mode = "absolute"` restores the legacy fixed thresholds.
- **`corroborated`** count: distinct vault URLs holding the exact same text. Distinct URL is not independence, so treat it as a lead.
- **`chrome` demotion**: text repeated across at least `retrieval.chrome_min_urls` (default 3) URLs is moved to the tail and flagged, never deleted, so it stays verifiable.

Empty queries return `[]`; punctuation-only queries still run the vector leg in hybrid mode. Query operators are sanitized. The dense scan is a single cached numpy matrix-vector product, not a per-row loop.

### Discovery and research

`discover` turns a query into ingested sources through DuckDuckGo HTML (or `hoardcore.providers` plugins) with bounded retry and backoff. If it is blocked, harness-side search plus `ingest --urls` is the documented rescue path.

`research` runs the full loop in one command: optional answer-first vault recall, DISCOVER, INGEST, RECALL, EMIT. It writes a grounding-context file (source URLs, scores, confidence bands, distinct-source summary, Source Links block) into `artifacts/YYYY-MM-DD/grounding/`. `--no-answer-first` forces a live hunt; `--discover 0` makes it recall-only.

`solve` runs the SIEGE Protocol for grand-challenge questions: decompose into fronts, hunt each front per pass (skipping satisfied fronts, refining unsaturated ones), then emit an Attack Dossier with claimed barriers, cross-front tensions, bridge candidates, and an honest siege budget. The dossier passes `audit` by construction. It is grounding, not an answer; the agent writes the final argument.

### Provenance and artifacts

Every artifact claim is tagged:

- `[V]` / `[V#N]` - verbatim in a stored source; `[V#N]` also maps to source `N`. Only `[V#N]` is machine-audited.
- `[E]` - external or earlier knowledge, or table/list data rather than contiguous prose.
- `[H]` - hypothesis or authored framing, ideally with a falsification experiment.

A `[V#N]` tag must sit immediately after a verbatim double-quoted passage in a body paragraph, never in a table row or on an `[E]`/`[H]` line. The artifact ends with a **Source Links / Citations** block (`[#N] URL`, one per line, last). `write_artifact()` refuses path-traversing filenames; day-foldering is automatic. `verify` checks one claim against stored text; `audit` checks that every `[V#N]` is verbatim, mapped to a listed source, and that the source has chunks in the vault.

### Extending the toolbox

No engine edits required. Installed packages advertise entry points:

```toml
[project.entry-points."hoardcore.parsers"]
myparser = "mylib:parser_entry"
```

Groups: `hoardcore.parsers`, `hoardcore.fetchers`, `hoardcore.providers`, `hoardcore.chunkers` (select with `chunking.strategy = "plugin.<name>"`). Broken plugins are skipped with a warning; they never abort a run. For in-process automation, use the event bus:

```python
import asyncio
import hoardcore as hc

async def main():
    engine = hc.HoardCore()
    engine.bus.on("document.ingested",
                  lambda **e: print("stored:", e.get("url")))
    await engine.fetch("https://example.com", action="scrape",
                       force_refresh=True)  # cached pages do not re-emit

asyncio.run(main())
```

Hooks: `document.ingested`, `chunk.embedded`, `discovery.completed`, `search.completed`.

### Vault durability and proof

Documents are append-only by version; chunks are BLAKE2b content-addressed and embedded once. `check` runs a three-phase integrity verification (counts, canonical hashes, vector dimensions) with exit 0/1. Switching embedding config recomputes vectors in place using an embedding fingerprint, so stale vectors are never served and migration is resumable. SQLite runs in WAL mode through a pooled connection manager.

The frontier-synthesis stress test and its reproducible scripts live in `peel_anchor/`; see `peel_anchor/README.md` for the results and how to re-run them.

## CLI reference

Syntax: `hoardcore ACTION [ARG] [flags]`. Legacy form: `hoardcore URL --action ACTION` or `hoardcore _ --action ACTION`, still supported everywhere.

### Actions

| Action | Purpose |
|---|---|
| `scrape` (default) | Fetch and index one URL/document, or an explicit `--urls` list |
| `crawl` | Walk a site via robots.txt/sitemap, or ingest `--urls` |
| `search` | Query the vault with `--query`; a URL positional restricts to that host; `--limit` caps chunks |
| `ingest` | Index a comma/space separated `--urls` list |
| `discover` | Web-search `--query`, ingest the top `--limit` results |
| `research` | DISCOVER -> INGEST -> RECALL -> EMIT; memory-first unless `--no-answer-first` |
| `solve` | SIEGE Protocol: fronts, passes, barriers/tensions/bridges, Attack Dossier |
| `verify` | Check `--claim` against stored text (verbatim, partial, unverified) |
| `audit` | Check an artifact's `[V#N]` chain: verbatim + mapped + ingested + in cited source |
| `lint` | Pre-audit authoring checks on an artifact; `--strict` escalates warnings |
| `prune` | Find and optionally delete transport-error ghost sources (dry-run unless `--apply`) |
| `check` | Three-phase vault integrity check; `--migrate` rebuilds at the configured page size |
| `stats` | Sources, versions, chunks, vectors, embedding mode/dim, schema, DB size, confidence sample |
| `export` | Stream the vault as JSONL to `--out PATH` or stdout |
| `local` | Index local files from `local_inputs/` (no network); `--list` previews, `--path` scopes |

### Flags

| Flag | Meaning |
|---|---|
| `--action ACTION` | Legacy form action selector; all 15 actions above are valid |
| `--query Q` | Required for `search`, `discover`, `research`, `solve` |
| `--claim C` / `--claim-file PATH` / `--claim-list PATH` | `verify`: one claim, one claim from a file (keeps `$` intact), or a file of claims (one per line; prints accuracy, exits with worst verdict) |
| `--hint` | `verify`: on denial, print the nearest vault phrase to reword toward |
| `--artifact PATH` | `audit` / `lint`: artifact to check |
| `--strict` | `lint`: warnings become failures |
| `--discover N` / `--recall N` | `research`/`solve`: source count and chunk count (`0` discover = recall-only) |
| `--fronts "F1; F2"` / `--passes N` | `solve`: attack fronts and pass budget |
| `--no-answer-first` / `--keep-low` | Force live discovery; retain low-confidence hits at EMIT |
| `--limit N` | `discover` top results; `search` max chunks |
| `--urls U1,U2` | Explicit URL list for scrape/crawl/ingest |
| `--vault NAME` | Per-topic vault; `a,b,c` adds read-only companions for cross-vault recall |
| `--out PATH` | Research output path, or `export` JSONL destination |
| `--mode fast|hybrid` / `--preview-chars N` | Force FTS-only or vector+RRF; CLI preview length (`0` = full) |
| `--strategy fast|balanced|aggressive` | Fetch chain selection (default from config) |
| `--parallel` / `--no-parallel` | Per-run override of threaded ingest (8+ chunks) |
| `--log-level LEVEL` | `debug`, `info` (default), `warning`, `error` |
| `--json` | Machine-readable stdout for `search`, `stats`, `verify`, `audit`, `solve`; exit codes unchanged |
| `--path PATH` / `--list` | `local`: scoped path inside `local_inputs/`; read-only preview |
| `--apply` / `--migrate` / `--force` | Execute prune deletions; rebuild page size; bypass cache |

`--query` is required for the query actions: omitting it exits 2 with a `Try:` line. Unknown commands such as `hoardcore serach` suggest the closest action.

### Exit codes

| Actions | Codes |
|---|---|
| `verify`, `audit`, `check`, `lint`, `research`, `local` | `0` pass/verified, `1` partial/incomplete, `2` unverified/error |
| `solve` | `0` all fronts saturated, `1` dossier written with open fronts, `2` hard error |
| `scrape`, `crawl`, `ingest`, `discover` | `0` ingested or served from cache, `1` nothing retrievable, `2` hard error |
| `search` | `0` even for an empty result set (`chunks: []` under `--json`) |

Never pipe a gate through `tail`/`head`: the shell reports the pipe's exit status, not the gate's. Capture `$?` from an unpiped run.

### `verify` and `audit`

`verify` returns `VERIFIED` (exit 0), `PARTIAL` (exit 1), or `UNVERIFIED` (exit 2). It folds typographic dashes, smart quotes and apostrophes, full-width Unicode, Unicode case in every script, whitespace, and markdown markers. It does not fold word identity, order, presence, or `%` versus "percent". A denial means "not stored verbatim in this wording", not "false"; `--hint` helps reword. The candidate scan is widened so long claims and non-ASCII text still resolve.

`audit` checks each `[V#N]`: the quoted claim must verify, `N` must appear in the Source Links block as `[#N] URL`, that URL must have chunks in the vault, and the quote must appear in **that cited source** (not merely somewhere in the vault). It prints a per-claim table and citation accuracy, then exits `0`/`1`/`2` as above.

### Configuration (`hoardcore.toml`)

Generated on first run. Key sections:

| Section | Notable keys |
|---|---|
| `[general]` | `timeout_seconds`, `user_agent` |
| `[network]` | `default_strategy`, `enable_preflight`, `ssrf_protection` |
| `[auth]` | `cookie_string` (e.g. `cf_clearance=...`) |
| `[storage]` | `root_dir`, `artifacts_dir`, `artifacts_by_day`, `grounding_subdir`, `local_dir`, `page_size`, `save_binary`, `save_raw_html` |
| `[parsers]` | `enable_pdf`, `enable_docx`, `enable_epub`, `enable_pdf_ocr` |
| `[solver]` | `enabled` (default false), `url`, `urls`, `solver_timeout`, `session_reuse` |
| `[crawler]` | `respect_robots`, `sitemap_limit`, `sitemap_index_depth`, `parallel_workers` |
| `[indexer]` | `enable_fts`, `search_limit`, `parallel`, `near_dedup`, `near_dedup_threshold` |
| `[embeddings]` | `enabled`, `mode`, `dense_model`, `dim`, `cache_dir`, `offline`, `hybrid_search`, `top_k`, `quantize`, `fts_fast_path`, `conf_mode`, `conf_high_abs`, `conf_low_abs`, `reranker_model` |
| `[discovery]` | `max_results`, `top_rank`, `max_retries`, `backoff_seconds` |
| `[solve]` | `passes`, `sources_per_front`, `facets`, `refinements`, `barrier_patterns`, `bridge_threshold`, `bridge_top_k`, `tension_min_shared`, `saturation_min_sources`, `answer_first` |
| `[research]` | `answer_first`, `filter_low`, `max_per_source` |
| `[chunking]` | `max_tokens`, `overlap_tokens`, `strategy` |
| `[retrieval]` | `chrome_min_urls` |
| `[plugins]` | `enabled` |
| `[cache]` | `ttl_seconds` |

The ONNX model is cached in `~/.cache/hoardcore/models` (`embeddings.cache_dir`) and downloaded once; `offline = true` loads only from that directory and fails loudly if absent.

## Architecture

```text
 Fetch   aiohttp + curl_cffi (concurrent) -> FlareSolverr (challenge pages)
   |
 Parse   trafilatura/readability (HTML) | lazy PDF/DOCX/EPUB | local text
   |
 Filter  boilerplate / captcha / transport-error stubs
   |
 Chunk   heading-aware splitting (or paragraph / plugin)
   |
 Store   SQLite FTS5 + content-addressed chunks + vectors + markdown mirror
```

Retrieval fuses FTS5 rank and vector rank with `1 / (k + rank + 1)`, `k = 60`. The vector scan is one cached numpy matrix-vector product with `argpartition`; embeddings carry a fingerprint (model + dim + quantization) so config switches trigger in-place, resumable backfill. All database access goes through a pooled connection manager with WAL, commit/rollback context managers, and connection reuse. FlareSolverr sessions are destroyed on every exit path, including errors and Ctrl-C.

## Repository layout

```text
hoardcore.py       Entire engine: config, fetcher, parsers, chunker, crawler,
                   discovery, vault, CLI, research and SIEGE actions
skill.md           Agent operating manual (read by AGENTS.md)
AGENTS.md          Agent trigger file loaded at session start
peel_anchor/       Reproducible frontier-synthesis stress test
tools/             Version-sync gate, solver health check, benchmarks
tests/             352 pytest tests (CLI, vault, network, audit, solve, OCR, ...)
artifacts/         Runtime deliverables (git-ignored)
hoardcore_data/    Vault files (git-ignored)
local_inputs/      Read-only source folder for --action local (git-ignored)
```

## Development

```bash
make install     # venv + deps
make test        # pytest suite
make lint        # ruff
make audit       # bandit
make typecheck   # pyright
make coverage    # 70% gate
make bench       # vector-search benchmark
make check       # all of the above
make clean       # wipe vault, caches, config
```

Python 3.11+, `from __future__ import annotations` throughout, annotated public signatures, heavy dependencies lazy-imported, minimal global state. Run `venv/bin/python tools/check_version.py` before a release: `__version__`, `pyproject.toml`, the README badge, and the git tag must agree.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Status 403 Blocked` on a protected site | Default `aggressive` strategy plus a running FlareSolverr with `[solver] enabled = true` |
| Solver times out or every solve fails | Check `docker ps`; verify `[solver] url`; run `venv/bin/python tools/check_flaresolverr.py`. For wedged DNS, recreate from `deploy/docker-compose.flaresolverr.yml` |
| Discovery returns nothing | DuckDuckGo may be IP-blocked; use harness search plus `ingest --urls`, or add a `hoardcore.providers` plugin |
| Search returns empty | Empty queries return `[]` safely; try hybrid mode or a broader query |
| Vault has junk or ghost sources | Purge with `prune --apply`, then re-ingest with `--force` |
| Cache seems stale | `cache.ttl_seconds` defaults to 86400; set to `0` never to expire, or use `--force` |
| Missing PyMuPDF / python-docx / ebooklib | `make install` or install those packages directly |

## Contributing

Fork, branch, add tests under `tests/`, run `make check`, commit with a conventional prefix (`fix:`, `feat:`, `docs:`, `chore:`), and open a pull request. Useful areas: live plugin-provider coverage, multi-process config reload, end-to-end crawl and discovery tests.

## License

MIT. See [LICENSE](LICENSE). For personal, research, and educational use; respect the terms of service and robots.txt of sites you access.
