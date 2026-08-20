# HoardCore

Research toolkit for AI agents — give your agent a memory it can prove.

Terminal tool that turns the web into a permanent, local SQLite vault your AI agent can hunt with, recall from, and cite. DuckDuckGo/Mojeek web discovery, Cloudflare-aware fetching, hybrid FTS5 + dense-vector retrieval (ONNX, no PyTorch), and a bounded `DISCOVER → INGEST → RECALL → EMIT` research loop with mandatory `[V]/[E]/[H]` provenance. Lightweight and single-file — but with real semantic retrieval, not a toy hash.

![Version](https://img.shields.io/badge/version-0.14.3-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Table of Contents

- [About](#about)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start for Humans](#quick-start-for-humans)
- [Quick Start (raw CLI)](#quick-start-raw-cli)
- [Agent Integration](#agent-integration)
- [Feature Tour](#feature-tour)
  - [Ingest Mode](#ingest-mode-scrape--crawl)
  - [Hybrid Retrieval](#hybrid-retrieval)
  - [Discovery Mode](#discovery-mode)
  - [Research Workflow](#research-workflow)
  - [Provenance Discipline & Epistemic Hygiene](#provenance-discipline--epistemic-hygiene)
  - [Cross-Domain Synthesis](#cross-domain-synthesis)
  - [Learning & Education Workflows](#learning--education-workflows)
  - [Artifacts](#artifacts)
  - [Vault durability, dedup & integrity](#vault-durability-dedup--integrity)
- [CLI Reference](#cli-reference)
- [Architecture](#architecture)
- [Package Structure](#package-structure)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## About

HoardCore is a research toolkit for AI agents — a single-file Python module (SQLite vault + CLI) for retrieval and deep research. It hoards knowledge: it turns the web into a permanent, local, searchable SQLite vault, then backs your agent's research with explicit discovery budgets, hybrid retrieval, and citeable provenance. Retrieval is **dense by default** — an ONNX-quantized sentence-transformer (via `fastembed` on `onnxruntime`, no PyTorch) fused with SQLite FTS5 keyword search via Reciprocal Rank Fusion (RRF). A lightweight sparse-hash fallback kicks in automatically only where the dense model is unavailable. The vault is schema-versioned and embedding-fingerprinted, so model/config switches never serve stale vectors.

**HoardCore is not an agent harness.** It provides the DISCOVER → INGEST → RECALL → EMIT loop, hybrid retrieval, and provenance tagging — but it does not host an LLM or manage context. That is the job of your **agent harness** (e.g., OpenCode, Claude Code, or any other): the harness hosts the agent, the agent reads `skill.md`, and the agent executes HoardCore commands via its CLI.

The relationship to the agent is the point. Chat-based "deep research" is a ride — you get in, it drives, you hope it took the right route. HoardCore is a **vehicle with the hood off**: you set the discovery budget (`--discover N`), the recall depth (`--recall N`), the anti-bot escalation (`--strategy`), the output schema, and the epistemic standard (`[V]/[E]/[H]` on every claim). Consumer AI is a portal you enter; HoardCore is a protocol your agent follows.

**Stress-tested on frontier problems.** The research loop has been validated on live theoretical computer science questions — e.g., deterministic approximation schemes for Edit Distance and Longest Common Subsequence — where it successfully ingested contemporaneous papers from disjoint domains (geometric grid sparsification, combinatorial streaming, algebraic fingerprinting), synthesized a non-obvious algorithmic bridge, and emitted a falsifiable research proposal with zero hallucinated citations and explicit impossibility barriers. The protocol does not eliminate human judgment; it makes the evidence chain machine-auditable so that human oversight scales.

Key characteristics:

- **Single-file core.** The entire engine is one module, `hoardcore.py`, runnable as a CLI or imported as a library.
- **Real semantic retrieval, dense by default.** An ONNX-quantized sentence-transformer (`BAAI/bge-small-en-v1.5`, 384-dim) runs on `onnxruntime` — no PyTorch, no GPU. Hybrid retrieval fuses FTS5 keyword search with dense vector similarity so both exact terms and *meaning* surface. A lightweight sparse hash (`mode = "sparse"`) is available as a fallback for environments without `fastembed`, and dense mode degrades to it automatically if the dependency is missing.
- **Lightweight and self-hosted.** One file, local-first, everything on your machine. **FlareSolverr (Docker) is required for Cloudflare-protected pages** — set `[solver] enabled = true` in `hoardcore.toml` and the `aggressive` (default) strategy routes Cloudflare-shaped challenges through FlareSolverr rather than failing on them. The shipped default config has `solver.enabled = false`; until you flip it, the FlareSolverr leg is a silent no-op and anti-bot pages will fail (open pages fetch fine). Lazy binary imports mean HTML-only usage never pulls in PDF/DOCX/EPUB libraries.
- **Resilient fetch chain.** `fast` → aiohttp; `balanced`/`aggressive` → aiohttp and curl_cffi TLS-impersonation **concurrently** (the first leg that returns content wins); `aggressive` (default) → adds FlareSolverr as a serialized terminal leg for Cloudflare-shaped challenges. Discovery adds bounded retry with exponential backoff and automatic provider fallback.
- **Hybrid retrieval.** Merges keyword (BM25-style FTS5) and vector-similarity ranks via RRF, so both exact terms and near-literal matches surface. The dense scan is a cached numpy matrix–vector product over the whole vector table — no per-row Python loop. Empty or punctuation-only queries return safely instead of crashing. Hits carry **confidence bands** (`high`/`medium`/`low`) surfaced in chunk metadata and grounding output; an optional cross-encoder (`embeddings.reranker_model`) can re-rank the final set.
- **Research workflow.** A single `research` action runs `DISCOVER → INGEST → RECALL → EMIT`, writing a grounding-context file into the `artifacts/` directory for direct injection into an LLM.
- **Programmatic provenance audit.** A `verify` action re-checks a claim against the vault's stored text (verbatim, partial, or unverified) with CI-wireable exit codes, so the `[V]` tag is machine-checkable, not just prompt-enforced.
- **Artifacts discipline.** Finished deliverables live in `artifacts/` with `[V]/[E]/[H]` provenance tags and numbered source links; a safe `write_artifact()` helper includes path-traversal protection, and deliverables are day-sorted into `artifacts/YYYY-MM-DD/`.
- **SSRF protection on by default.** Fetch targets are validated before any request (aiohttp re-validates every redirect hop; the curl_cffi/FlareSolverr fallbacks re-validate the post-redirect final URL) — non-`http(s)` schemes, private/LAN/loopback/link-local addresses, and DNS-special names are refused (`network.ssrf_protection`, default `true`).
- **Plugin system & event bus.** Third-party parsers/fetchers/providers/chunkers drop in via `importlib.metadata` entry points, and a lifecycle `EventBus` publishes `document.ingested` / `chunk.embedded` / `discovery.completed` / `search.completed` hooks — a broken plugin never aborts a crawl.
- **Self-verifying vault.** Content-addressed chunks (BLAKE2b), schema versioning (`PRAGMA user_version`), and embedding fingerprints (`embed_fp`) mean re-ingested content dedupes, stale vectors are never served, and `--action check` proves integrity with CI-wireable exit codes.
- **MIT licensed.** Free to use, modify, and redistribute.

---

## Prerequisites

| Requirement | Purpose | Install |
|---|---|---|
| **Python 3.11+** | Runtime (uses `tomllib`) | `python3 --version` |
| **fastembed + onnxruntime** | Dense retrieval (ONNX-quantized sentence-transformer; no PyTorch/GPU) | Installed via Makefile |
| **curl_cffi** | TLS-fingerprint impersonation for harder anti-bot pages | Installed via Makefile |
| **FlareSolverr** *(required for Cloudflare-protected pages)* | Fetch Cloudflare-protected pages — the `aggressive` strategy routes challenges through it once `[solver] enabled = true` in `hoardcore.toml`. The shipped default config has the solver disabled, so open pages work with no FlareSolverr at all | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| **PyMuPDF / python-docx / ebooklib** *(optional)* | PDF, DOCX, EPUB parsing | Installed via Makefile |
| **rapidocr_onnxruntime** *(optional)* | OCR fallback for scanned/image-only PDF pages (local ONNX, no system deps) | `pip install .[ocr]` |

FlareSolverr is **required for Cloudflare-protected coverage**: run the container, set `[solver] enabled = true` in `hoardcore.toml`, and the default `aggressive` strategy routes anti-bot-protected pages through it. It runs as a small Docker container on `http://localhost:8191/v1` (override the endpoint via `[solver] url` in `hoardcore.toml`). The shipped default config has `solver.enabled = false`, so a stock install never calls FlareSolverr — open pages work, protected ones fail. For environments that cannot run Docker, keep the solver off and rely on the curl_cffi TLS-impersonation leg of `balanced`/`aggressive` — but expect more anti-bot blocks, since FlareSolverr is what actually clears the challenge.

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
make bench      # run the vector-search benchmark (float32/int8 x page sizes)
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

**Start FlareSolverr (required for Cloudflare-protected coverage):**

```bash
docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr
```

Then enable the solver so the `aggressive` strategy (the default) actually hands challenges to it:

```toml
[solver]
enabled = true
```

Without the container **and** `enabled = true`, the `aggressive` strategy's FlareSolverr leg is a silent no-op, and Cloudflare-protected pages will be blocked. HoardCore still works on open pages either way — FlareSolverr is only needed to reach the many sites that gate their content behind a challenge.

**Optional — OCR for scanned PDFs**:

```bash
make install && venv/bin/python -m pip install rapidocr_onnxruntime   # or: pip install .[ocr]
```

Once installed, image-only/scanned PDF pages are OCR'd automatically (RapidOCR, local ONNX, no system deps); without it, those pages degrade gracefully. See [Ingest](#ingest-mode-scrape--crawl).

**Retrieval modes.** Dense retrieval is **on by default** — `make install` includes `fastembed`, so hybrid search uses an ONNX-quantized sentence-transformer (default `BAAI/bge-small-en-v1.5`, 384-dim, runs on `onnxruntime` — no PyTorch, no GPU) for meaning-based matching. If `fastembed` is unavailable in a given environment, dense mode **degrades gracefully to the lightweight sparse hash** — it never crashes. To force the sparse hash explicitly, set `mode = "sparse"` in the `[embeddings]` section of `hoardcore.toml`. Switching modes rebuilds the vector table automatically (resumable across interrupts). See [Hybrid Retrieval](#hybrid-retrieval) and [Configuration](#configuration-file-hoardcoretoml).

---

## Quick Start for Humans

HoardCore is a tool *for your AI agent* to hoard, recall, and cite — you rarely type its commands yourself; your agent does. This README is for you, the human, so here is the human's onboarding. Two paths — pick one.

### Path A — Let an AI agent set it up (recommended)

If you already use an agent harness (OpenCode, Claude Code, any other), you don't need to touch the terminal:

1. Open your harness in any directory and paste the clone URL to the agent:
   ```
   git clone https://github.com/jjjardev/HoardCore.git
   ```
2. Tell the agent: *"install HoardCore's dependencies and start FlareSolverr."* The agent reads the repo and runs `make install` plus the FlareSolverr container (below) for you.
3. When setup is done, **close the harness and reopen it inside the `HoardCore/` folder.** Your harness auto-loads `AGENTS.md` at session start, which tells the agent to read `skill.md` in full before any task. HoardCore is now ready: ask it to research, scrape, or summarize anything and it drives the CLI for you.
   - If your harness does not auto-load `AGENTS.md`, just start with the words: *"Read skill.md first, then ..."*.

Try it now — an example query you can type to your agent:

```
You: research deep the state of lunar exploration in 2026.
Agent: (reads skill.md -> opens the Hardcore Research Loop, budget 6 passes x >=8 sources)
  venv/bin/python hoardcore.py _ --action research \
     --query "lunar exploration 2026" --discover 6 --recall 8
  -> writes a [V]/[E]/[H]-tagged report to artifacts/ and replies with a 3-bullet summary
```

### Path B — Manual setup (no agent)

```bash
git clone https://github.com/jjjardev/HoardCore.git
cd HoardCore
make install                      # create venv + install core deps

# Start FlareSolverr (required for full anti-bot coverage):
docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr
```

Then either drive the raw CLI yourself (see [Quick Start (raw CLI)](#quick-start-raw-cli)) or reopen your harness in this folder so the agent reads `skill.md` and takes over.

> Once set up, the human's job is done: **you ask, the agent reads `skill.md`, and the agent runs HoardCore's CLI for you.** The vault persists between sessions — later questions are answered instantly from local memory, with no network.

---

## Quick Start (raw CLI, no agent)

```bash
# Scrape a single URL and index it
python hoardcore.py https://en.wikipedia.org/wiki/Solar_power --action scrape

# Search the vault (full-text or hybrid) for previously ingested content
python hoardcore.py _ --action search --query "renewable energy negros"

# Ingest an explicit list of URLs
python hoardcore.py _ --action ingest --urls "u1,https://a.test/1,https://b.test/2"

# Discover the web for a topic and ingest the top results
python hoardcore.py _ --action discover --query "negros renewable energy" --limit 5

# Run the full agentic research workflow -> appends grounding context to artifacts/
python hoardcore.py _ --action research --query "how does bokashi compost" --discover 5 --recall 6

# Programmatically verify a claim against the vault (0=verified, 1=partial, 2=unverified)
python hoardcore.py _ --action verify --claim "the Epoch doubling time is 6 months" --recall 5

# Audit an artifact's [V#N] evidence chain (verbatim verify + source-link mapping + ingested)
python hoardcore.py _ --action audit --artifact artifacts/2026-08-18/synthesis_x.md

# Run a three-phase vault integrity check (0=pass, 1=fail)
python hoardcore.py _ --action check
echo "exit code: $?"

# Rebuild an existing vault at the new 16 KB page size (legacy 4 KB vaults keep their old size otherwise)
python hoardcore.py _ --action check --migrate

# Search ergonomics: force FTS-only (fast) or force hybrid vector+RRF (hybrid)
python hoardcore.py _ --action search --query "solar farm" --mode fast
python hoardcore.py _ --action search --query "solar farm" --mode hybrid

# Same, but write the grounding context to a specific file
python hoardcore.py _ --action research --query "negros economy" --out artifacts/report.md

# Crawl an entire site via its sitemap
python hoardcore.py https://docs.python.org --action crawl --strategy aggressive
```

The vault persists between runs. Later searches are instant and require no network.

---

## Agent Integration

**HoardCore is a research toolkit and memory protocol that any AI agent or harness can call into. It is not itself a harness.** The harness (e.g., [OpenCode](https://opencode.ai), Claude Code, or any other) hosts the LLM and manages context; the agent that runs inside it reads `skill.md` and drives HoardCore via its CLI. HoardCore's role is to give the agent a persistent, verifiable memory: the agent calls it to hoard the web, then queries the vault instead of trusting its own (decaying or invented) recall.

**HoardCore is a protocol, not a portal.** Chat products are portals you enter and whose epistemic standards you accept. HoardCore specifies *how* your agent hunts (`discover` → `ingest`), recalls (`FTS5 + RRF`), verifies (`[V]/[E]/[H]` + adversarial audit), and emits (`artifacts/` with grounding context). You can plug any LLM and any harness into this protocol; HoardCore doesn't care which harness hosts the agent — it only requires that the agent follows the protocol.

| HoardCore provides (on your machine) | The agent does (any model, any harness) |
|---|---|
| Fetch web content (HTML/PDF/DOCX/EPUB/OCR) | Reads the user's request and `skill.md` |
| Store everything in a persistent SQLite vault | Drives the `hoardcore` CLI (discover / ingest / search / research) |
| Hybrid-retrieve the most relevant chunks (FTS5 + vectors, RRF) | Reads the grounding context and cross-checks claims |
| Stay single-file, with dense ONNX retrieval built in (no PyTorch/GPU) | Writes the finished, `[V]/[E]/[H]`-tagged report |

### First-class controls (not buried in prompt engineering)

Because an agent drives HoardCore via `skill.md` and the CLI, these parameters are **exposed as first-class controls**, not buried in prompt engineering:

| Parameter | What It Controls | Why It Matters |
|---|---|---|
| `--discover N` | How many sources to hunt before ingesting (`0` = recall-only: vault query + grounding only, web hunt skipped) | More sources = broader coverage; fewer = faster turnaround |
| `--recall N` | How many chunks to retrieve per synthesis pass | More chunks = deeper context; fewer = sharper focus |
| `--strategy {fast,balanced,aggressive}` | Anti-bot escalation chain | Government sites and job boards require FlareSolverr; lightweight blogs don't |
| `--vault NAME` | Scope the whole session to a per-topic vault (`hoardcore_data/NAME/`); a list (`a,b,c`) adds read-only companions for cross-vault search/verify/audit | Keeps recall clean: research memory is isolated per topic/domain instead of polluting one shared pool |
| **Depth presets** (`research` / `deep` / `exhaustive` / `x N`) | Pass count × source quota | You decide when "enough" is enough, not the platform |
| **Output schema** | Defined in the prompt / `skill.md` | A SWOT matrix, a legal brief, a lead list — the agent emits what you specify |
| **Provenance tags** | `[V]/[E]/[H]` enforcement | You decide the epistemic standard |
| **Termination conditions** | Saturation, source quota, diminishing returns, pass cap, user interrupt | The loop is bounded and auditable |

In a chat product you *plead* ("please be thorough, cite sources, check your work"). In HoardCore you *command* via `skill.md` — and the agent **must** read the manual before it acts.

### The three companion documents

| File | Audience | Purpose |
|---|---|---|
| `README.md` | Humans / maintainers | Install, config, architecture, CLI reference |
| `AGENTS.md` | The AI agent (loaded first) | The **trigger**: a short file your harness auto-loads into the agent's context at every session start, mandating that the agent read `skill.md` before any task |
| `skill.md` | The AI agent | The **manual**: what the agent reads to learn **how** to use the tool |

`skill.md` is written as an agent skill (YAML frontmatter + instructions). It teaches the agent when to trigger, which actions map to which user request, the `[V]/[E]/[H]` provenance discipline, and the adversarial-audit step before finalizing an artifact.

### Where the agent reads `skill.md` first

The order is enforced by the harness's session-start loading, not by habit:

1. **Session start** — your harness (e.g., OpenCode, Claude Code) loads the repo-root `AGENTS.md` into the agent's context automatically. It contains one hard rule: *"Before any task, you MUST read `skill.md` in full."*
2. **First task** — the agent obeys that rule and reads `skill.md` before touching the web, the vault, or `artifacts/`. Everything the agent then does (scrape / crawl / search / discover / research) is driven by `skill.md`'s action mapping.
3. **Ongoing** — `skill.md` stays the reference: if a task needs an action the agent is unsure of, it re-consults `skill.md`, never its own memory.

So the chain is: **harness auto-loads `AGENTS.md` → `AGENTS.md` forces `skill.md` → `skill.md` drives the agent's use of the HoardCore CLI.** If your harness does not auto-read `AGENTS.md`, treat that file as the instruction to point the agent at first.

### DeepResearch: the Hardcore Research Loop

**DeepResearch is the default research mode.** Any open-ended research request — "research", "investigate", "deep dive", "find out about" — triggers a full end-to-end investigation instead of an ad-hoc scrape/search. The agent reads the behavior out of `skill.md` and runs a **bounded, adversarial** loop:

1. **Parse** the directive into the core question.
2. **Hunt** — DISCOVER the web for high-authority primary sources and INGEST them into the local vault (uses `--strategy aggressive` past anti-bot protection).
3. **Recall** — hybrid-retrieve the **5–10 most relevant chunks**, discarding any that feel flimsy or lack context.
4. **Synthesize** — emit `grounding_context` (source URLs + hybrid scores + distinct sources), then write the deliverable into `artifacts/`.
5. **Adversarial audit** — re-query the vault to confirm each `[V]` tag before presenting it; for every `[H]` claim, generate the falsification experiment that would invalidate it.

**Depth presets — control effort, not a raw number.** At kickoff the agent maps a direction to a budget, then stops as soon as **any one** trips:

| Direction | Passes | Sources |
|-----------|--------|---------|
| `research` (default) | 3 | ≥5 |
| `research deep` | 6 | ≥8 |
| `research exhaustive` | up to 10 | ≥15 |

A raw-count override exists for strictness: `research x 10 <topic>` hard-caps the loop at exactly 10 passes. The stop conditions are answer saturation (two re-queries with no new `[V]` claim), the distinct-source quota, diminishing returns (identical re-ranking), the pass cap, or a user interrupt. **Conversational deepening** — after a stop the user can simply say **"go deeper"** to re-enter one more pass (retaining all prior evidence, interruptible, capped by the session's pass budget) or **"that's enough"** to finalize — so nobody has to predict the right count up front. On stop the agent runs the audit, emits the artifact (labeled `[INCOMPLETE — N passes]` if a budget guard or interrupt cut it short), and returns a **3-bullet Executive Summary**.

**Frontier synthesis.** The loop has demonstrated the ability to produce research artifacts that include: (1) explicit barrier analysis citing impossibility theorems, (2) novel `[H]` protocol proposals combining two distinct technical objects from disconnected literatures, (3) falsification experiments that would invalidate the proposal if failed, and (4) honest scoping that admits when a problem is blocked by known lower bounds rather than hallucinating a solution. This is not "AI writes a paper"; it is "AI produces a falsifiable research agenda with the receipts attached."

### Workflow examples (driving HoardCore from an agent)

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
  -> instant answer grounded in the local vault, with source URLs
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
  -> aiohttp ⟂ curl_cffi (concurrent) -> (FlareSolverr, required & running) until it succeeds
```

**6. Academic frontier synthesis (cross-domain)**

```
You: Can a deterministic single-pass sketch achieve (1+ε) Edit Distance on
     structured inputs? I have the Mao-Rubinstein grid paper and the Boneh
     certifier, but I need you to bridge them.

Agent: (loads skill.md -> recognizes a frontier synthesis request)
  venv/bin/python hoardcore.py _ --action research --vault ed_frontier \
     --query "deterministic single pass edit distance sketch structured inputs" \
     --discover 6 --recall 8 --strategy aggressive
  -> ingests arXiv:2603.29702, arXiv:2507.22486, streaming lower-bound papers
  -> recalls chunks across all three domains
  -> emits a proposal combining geometric grid sparsification + deterministic
     algebraic fingerprinting, tagged [V]/[E]/[H]
  -> includes explicit falsification experiments (F1–F4) and a benchmark spec
```

**7. Legal due diligence with citation verification**

```
You: Verify every claim in this draft merger memo against our deal vault.

Agent: (iterates over claims)
  venv/bin/python hoardcore.py _ --action verify --vault mna_2026 \
     --claim "Change-of-control clause is unenforceable per Delaware Ch. 251" \
     --recall 5
  -> exit 0: VERIFIED -> keep [V] tag
  -> exit 1: PARTIAL -> flag for associate review
  -> exit 2: UNVERIFIED -> strip or demote to [H]
```

**8. Student study guide from ingested course materials**

```
You: I uploaded the syllabus and 12 papers last week. Build me a study guide
     on electron transport chain ATP yield.

Agent: venv/bin/python hoardcore.py _ --action research --vault biology101 \
     --query "electron transport chain ATP yield" --discover 0 --recall 10
  -> no live search; builds guide purely from ingested course vault
  -> tags every claim [V] to the specific paper chunk
```

Each example ends with content that is **grounded and recallable later** — the vault is the source of truth, not the agent's memory.

### Agent-friendly fact

Because dense retrieval runs on `onnxruntime` (no PyTorch, no GPU) and the `hoardcore.toml` config is auto-generated, HoardCore is lightweight to install and runs inside most sandboxes and CI environments — the agent can bootstrap a vault on first run with no setup ceremony. Dense mode never blocks startup: if its dependency is absent it degrades to the sparse hash and keeps working.

---

## Feature Tour

### Ingest Mode (Scrape / Crawl)

**Scrape** fetches a single URL (HTML, PDF, DOCX, EPUB), cleans it, chunks it semantically by headings, and indexes it. **Crawl** discovers a site's URLs via `robots.txt` / sitemap and ingests them with a bounded, semaphore-limited worker pool (`crawler.parallel_workers`).

The pipeline for each document:

1. **Fetch** — tries the strategy chain (aiohttp ⟂ curl_cffi concurrently → FlareSolverr) until one returns content. With the default `aggressive` strategy and `[solver] enabled = true`, FlareSolverr is the terminal leg that clears Cloudflare-shaped challenges.
2. **Parse** — HTML via `trafilatura` + `readability` with a self-selecting-best fallback; PDF/DOCX/EPUB via lazy-loaded binaries; else raw-text strip. **Scanned PDFs:** pages with no extractable text are auto-OCRed via RapidOCR (optional `pip install .[ocr]`, fully local ONNX, no system deps); OCR'd pages are flagged in metadata (`parser: pymupdf+ocr`, `ocr_pages`).
3. **Junk-filter** — boilerplate/redirect/404/captcha pages and near-empty extractions are detected and refused entry to the vault.
4. **Chunk** — semantic splitting respecting headers (or paragraphs for binaries).
5. **Store** — chunks persisted to FTS5, mirrored as markdown + chunks JSON under `hoardcore_data/`, and embeddings backfilled.

### SSRF Protection

Fetch targets are validated before any request — non-`http(s)` schemes, private/LAN/loopback/link-local (incl. `169.254.x.x` cloud-metadata) addresses, and DNS-special names are refused unless `network.ssrf_protection` is set to `false` (default `true`) for a trusted isolated network. The aiohttp leg re-validates every redirect hop; the curl_cffi/FlareSolverr fallbacks follow redirects internally, so they re-validate the post-redirect final URL instead.

### Plugin System & Event Bus

Third-party extensions drop in via `importlib.metadata` entry points — no monkey-patching:
- `hoardcore.parsers` → binary parsers keyed by content type
- `hoardcore.fetchers` → extra fetch strategies appended to the strategy chain
- `hoardcore.providers` → extra discovery backends (fallback chain)
- `hoardcore.chunkers` → custom chunkers, selected by `chunking.strategy = "plugin.<name>"`

Any plugin that fails to load, throws, or returns bad output is skipped with a warning — a broken plugin never aborts a crawl. A glossing `hoardcore.EventBus` publishes lifecycle hooks (`document.ingested`, `chunk.embedded`, `discovery.completed`, `search.completed`) for observability or automation.

### Hybrid Retrieval

`search` fuses two candidate lists via Reciprocal Rank Fusion (RRF):
- **FTS5** keyword ranking across chunk text (BM25-style).
- **Dense vector** similarity from an ONNX-quantized sentence-transformer (default).

**Dense mode (default):** chunks are embedded with a sentence-transformer (`BAAI/bge-small-en-v1.5`, 384-dim) via `fastembed` on `onnxruntime` — no PyTorch, no GPU. This captures *semantic* similarity: a conceptual query surfaces on-topic sources even with no shared vocabulary.

**Sparse mode (fallback):** set `[embeddings] mode = "sparse"`. Uses FNV-1a feature hashing of word + 3-gram shingles into a 256-dim unit vector for lexical overlap. It's the automatic fallback when the dense dependency is missing and the mode for environments that want zero model weight. A lexical query can still surface near-literal matches (e.g., `sol*r` → `solar`).

**Confidence bands.** Every hybrid hit is tagged `high`, `medium`, or `low`. The default (`conf_mode = "relative"`) ranks *within* the returned recall set — the top hit(s) clearly above the set's own tail are `high`, hits hugging the coincidence floor are `low`, the middle is `medium`. Only a **keyword-backed** set (a genuine FTS match near the top) can crown `high`; a pure-vector/off-topic set never does. This fixes the "all-`medium`" flatness that absolute-score thresholds produced on homogeneous vaults (where RRF scores cluster into one band). The legacy behavior (`conf_mode = "absolute"`) tags `high` only for hits that matched *both* the keyword and vector lists or clear an absolute `conf_high_abs`/`conf_low_abs` ceiling. The band is attached to chunk metadata and printed in grounding-context output (e.g. `score 0.0325 | high`). A low-confidence hit signals a weak match that should be **re-verified before being tagged `[V]`** — or demoted to `[E]`. FTS fast-path hits (which skip the vector scan) are tagged `medium`, since semantic closeness is unverified.

Empty and whitespace-only queries return `[]` instead of raising. A punctuation-only query (no FTS tokens) still runs the vector scan in hybrid mode — the embedding model can match semantic content even when keywords are absent — while the FTS-only (`fast`) path returns `[]`.

The dense vector scan is a single numpy matrix–vector product over the whole vector table (`argpartition` for top-k, cached when the table is unchanged) instead of a per-row Python loop. Optionally, `embeddings.reranker_model` runs a cross-encoder over the final recalled set, loaded lazily and degrading to input order on any failure.

Queries are sanitized: operator characters (`" ( ) * ^ : -`) are stripped and tokens quoted, so free-text input cannot alter query semantics or raise FTS syntax errors.

### Discovery Mode

`discover` turns a plain-language query into ingested sources. It hits DuckDuckGo's HTML endpoint via the same resilient fetch chain (so a rate-limited/shaped search page gets retried and can even be solved by FlareSolverr), with Mojeek as an automatic fallback provider. The top `--limit` results are ingested (`discovery.top_rank`, default 6, when no `--limit` is given), with bounded retry + exponential backoff on transient failures.

### Research Workflow

`research` is the full agentic loop in one command:
```
[0/ANSWER-FIRST] optionally: serve a high-confidence existing memory hit, skip live DISCOVER
[1/DISCOVER] web-search the question, ingest top sources
[2/RECALL]   hybrid-retrieve the best chunks
[3/EMIT]     write a grounding-context file
```
By default the vault is queried *before* any web traffic: a high-confidence
memory hit for a repeat question answers immediately (no network; the
grounding file is flagged "Answer-first recall"). Pass `--no-answer-first` to
always run live DISCOVER. The emitted file lists each retrieved chunk with its
source URL, hybrid score, and confidence band, plus a distinct-sources summary
and a **Source Links / Citations** block — ready to be injected verbatim as
grounding context for an LLM.

### Provenance Discipline & Epistemic Hygiene

Every claim in a research artifact is tagged by the agent:

- `[V]` — **Verified.** The claim appears verbatim in a chunk stored in the vault. Machine-checkable via `verify`.
- `[E]` — **External.** Known fact or extracted evidence not currently in the vault.
- `[H]` — **Hypothesis.** Novel synthesis, inference, or authored framing by the agent.

This is not prompt-engineered politeness; it is a **structural feature of the loop**. The `verify` action punishes false `[V]` tags with exit code `2` (unverified). Over iterative research passes, the system selects for verifiable claims because unverified claims are flagged and must be defended. The emergent behavior is **epistemic caution**: the agent becomes more conservative in its assertions than the base model would be in chat mode, because the protocol makes honesty the path of least resistance.

In practice, this means:
- A `[V]` claim is a promise that `verify --claim "..."` will return exit `0`.
- An `[H]` claim must be accompanied by a falsification experiment: *"If you run X and get Y, this claim is dead."*
- The agent cannot retreat to "just use randomness and take the min over rounds" when the user asked for a deterministic protocol; the `[H]` tag forces it to own the limitation.

### Cross-Domain Synthesis

Because the vault persists across DISCOVER/INGEST cycles, the agent can hold evidence from multiple disjoint fields simultaneously. Retrieval surfaces structural analogies that no single source stated explicitly. In stress tests, the agent has combined:

- **Geometric approximation** (grid curvature sparsification)
- **Combinatorial streaming** (document exchange protocols)
- **Algebraic hashing** (deterministic fingerprinting)

into a single protocol — despite these papers sharing no authors, citations, or arXiv categories. The RRF retrieval and persistent vault make the **juxtaposition** computationally accessible. This is not creativity in the human sense; it is the emergent capability of a shared, queryable memory substrate that does not decay between sessions.

### Learning & Education Workflows

Students and self-learners can use HoardCore as a **second brain with integrity checks**:

1. **Scope by course:** `--vault biology101`, ingest the syllabus, textbook PDFs, and assigned papers.
2. **Study from your own vault:** `research --discover 0 --recall 10` builds study guides purely from ingested material, tagging every claim `[V]` to a specific source chunk.
3. **Opinion audit:** Write an essay, then run `verify` on every claim. If your draft is full of `[H]` and `[E]` with few `[V]` tags, your understanding is built on sand.
4. **Cumulative expertise:** After a semester, your vault is your intellectual history — machine-searchable, verifiable, and independent of any platform.

### Artifacts

Finished deliverables live in `artifacts/` (configurable via `storage.artifacts_dir`), day-sorted into `artifacts/YYYY-MM-DD/` subfolders. The tool ships `write_artifact(filename, content)` which refuses path-traversing names, plus `citation_list()` to render the source-links block. Research EMITs its grounding context into the day folder's `grounding/` subfolder (`storage.grounding_subdir`) — a working instrument, not a deliverable, so it never pollutes the day folder of finished syntheses/audits. Research outputs carry provenance tags:

- `[V#N]` — **verified verbatim** against full primary text in the current vault, traced to source `#N`. The only tag the audit machine-checks.
- `[E]` — extracted/captured earlier, or data present only as table/list cells (not contiguous prose); not in the current vault.
- `[H]` — hypothesis / authored framing.

Tag grammar: `[V#N]` appears only immediately after a verbatim `"…"` quote in a body paragraph; `[E]`/`[H]` claims carry **no** `[V#N]` (they can never verify). Every `[V#N]` resolves against the artifact's **Source Links / Citations** block.

Example artifacts already produced: the renewable-island synthesis, its adversarial audit, and the master research portfolio.

### Vault durability, dedup & integrity

HoardCore's SQLite vault is built for durable, append-only research memory:

- **WORM (write-once-read-many) documents.** Re-ingesting the same URL appends a new `version` row (`UNIQUE(url, version)`) rather than overwriting the previous one, so the vault is append-only and historical versions remain queryable. Existing vaults are auto-migrated on first run.
- **Content-addressed deduplication.** Chunks are hashed with BLAKE2b-256 into a canonical `chunks_ca` table; identical chunk text across documents is stored once and embedded only once (`chunk_vectors_ca` cache). Re-ingesting similar pages no longer grows storage linearly.
- **Connection pooling.** A reusable `ConnectionPool` (8 connections, WAL + mmap + page-cache tuning) replaces open-a-new-connection-per-query, improving concurrent throughput.
- **Integrity checking.** `--action check` runs a three-phase verification (document chunk counts, canonical content hashes, vector dimensions) and exits `0` on pass / `1` on fail, so it can gate CI or scheduled jobs.

### Proving the claims — the `peel_anchor/` directory

HoardCore's frontier-synthesis stress test is not a claim you have to take on faith. The deterministic-LCS research that produced it, and the streaming-edit-distance sketch that extends it, are committed **in-repo** under `peel_anchor/` so anyone can re-run them and see the algorithm validate. This is the proof-of-record for the "Stress-tested on frontier problems" section above — every number there is reproducible from these files.

```
peel_anchor/
    peel_anchor_lcs.py        Deterministic near-linear LCS approximation (Boneh–Golan–Kraus 2025)
                              + exact O(n²) DP baseline. Self-tests soundness (never over-estimates).
    sample_round_lcs.py       2026 Mao–Rubinstein 45°-rotated-grid reproduction (sample-and-round). Validates rotated-DP == exact LCS.
    peel_anchor_hybrid.py     The deterministic bridge: peel-certified active scales + deviation-ranked
                              sub-interval selection. Runs soundness, adversarial (2000-instance) probe,
                              and the per-instance certificate test.
    benchmark.py + benchmark_results.json   Full numeric benchmark (ratios, runtime, smoothed, certificate).
    cs_hard_problem_novel_idea.md   The original research proposal.
    deterministic_single_pass_edit_distance_sketch.md
                            The 6-pass HARDORE-loop deliverable: deterministic single-pass ED sketch (DASS),
                              with [V]/[E]/[H] provenance, falsification experiments F1–F4, and a benchmark spec.
    README.md               Provenance-tagged report of every result.
```

**To reproduce the headline claim** (deterministic sound-by-construction LCS with a per-instance certificate, 0/2000 violations):

```bash
venv/bin/python peel_anchor/peel_anchor_hybrid.py     # validation + 2000-instance certificate probe
venv/bin/python peel_anchor/peel_anchor_lcs.py        # selftest: soundness against exact DP
venv/bin/python peel_anchor/sample_round_lcs.py       # rotated-grid == exact validation
venv/bin/python peel_anchor/benchmark.py              # full numeric benchmark (A–H)
```

Each script prints its pass/fail explicitly — a failed soundness check would exit with an assertion error rather than a plausible-looking number. The `deterministic_single_pass_edit_distance_sketch.md` deliverable records the frontier-mapping, the deterministic protocol, and the falsification experiments; its `[V]` citations were verified against the `cshard` vault during the research loop.

---

## CLI Reference

```
hoardcore [URL] [options]
```

### Actions

| Action | Purpose |
|---|---|
| `scrape` *(default)* | Fetch + ingest a single URL/document — or an explicit `--urls` list (batch). Requires a URL positional (or `--urls`). |
| `crawl` | Crawl a whole site via sitemap/robots.txt, or ingest an explicit `--urls` list (batch). Requires a domain URL positional (or `--urls`). |
| `search` | Query the vault with `--query`; restrict to a domain by passing its host as the positional. `--limit` caps returned chunks. |
| `ingest` | Index an explicit URL list given as a comma/space separated `--urls` string. |
| `discover` | Web-search `--query`, ingest the top `--limit` results (default `discovery.top_rank`). |
| `research` | Run `discover -> ingest -> recall -> emit` (memory-first: live DISCOVER is skipped when the vault already has a high-confidence answer, unless `--no-answer-first`); writes to `--out` or a day-sorted `artifacts/YYYY-MM-DD/grounding/grounding_context.md` (the grounding subdir is configurable via `storage.grounding_subdir`). |
| `verify` | Programmatic provenance audit: confirm `--claim` against vault text (exact phrasing, typography-blind; `--hint` prints the nearest vault phrase on denial). |
| `audit` | Audit an artifact's `[V#N]` evidence chain (verbatim + source-link mapping + ingested). |
| `check` | Run a three-phase vault integrity check (content hashes, counts, vector dims). |
| `stats` | Vault summary in one command: sources, chunks, vectors, embedding dim/mode, schema version, DB size, plus a sampled confidence-band distribution (`high`/`medium`/`low`) to spot retrieval flatness. With `--vault a,b,c` prints a block per named vault. |
| `local` | Index local files from `storage.local_dir` (default `local_inputs/`, git-ignored) — no network. Supported: `.pdf .docx .epub .html .htm .txt .md`, walked recursively. `--path` scopes to a file/folder inside it, `--list` is a read-only scan. Freshness is content-based (unchanged extracted content is skipped unless `--force`). |

Use a positional of `_` when an action (e.g. `search`, `discover`, `research`, `ingest`, `verify`, `audit`, `check`, `stats`) does not need a URL.

### Flags

| Flag | Description |
|---|---|
| `--action ACTION` | One of `scrape`, `crawl`, `search`, `ingest`, `discover`, `research`, `verify`, `audit`, `check`, `stats`, `local`. |
| `--strategy S` | `fast`, `balanced`, or `aggressive` (default from config). |
| `--query Q` | Required for `search`, `discover`, `research`. |
| `--limit N` | Top results to ingest for `discover` (default `discovery.top_rank`); max chunks returned for `search`. |
| `--urls U1,U2,U3` | Explicit URL list for `scrape`/`crawl`/`ingest` (overrides the URL positional). |
| `--discover N` | Sources to discover first in `research` (default 5; `0` = recall-only, no web). |
| `--recall N` | Chunks to retrieve in `research` (default 6). |
| `--no-answer-first` | With `research`: always run live DISCOVER, even if the vault already has a high-confidence answer (default: `research.answer_first = true` skips it). |
| `--keep-low` | With `research`: retain low-confidence hits in the grounding context (skip `filter_low`) — for exhaustive/deep hunts that want the full evidence tail. |
| `--out PATH` | Output file for `research` (default: `artifacts/YYYY-MM-DD/grounding/grounding_context.md`, suffixed `_N` when today's already exists). |
| `--artifact PATH` | With `audit`: path to a synthesis artifact to audit. |
| `--claim C` | Claim text to verify for the `verify` action. In shells, escape `$` as `\$` (bash expands `$13` to empty); or use `--claim-file` to read the claim from a file so `$` survives untouched. |
| `--claim-file PATH` | With `verify`: read the claim from this file instead of `--claim` (preserves `$`, e.g. `$13`). |
| `--claim-list PATH` | With `verify`: batch-audit a file of claims (one per line; `#`/blank lines skipped). Prints a per-claim verdict table plus an aggregate **citation-accuracy %** (VERIFIED ÷ total) and exits with the worst verdict (2 on any UNVERIFIED) — so it doubles as a CI-wireable citation-accuracy gate. Mutually exclusive with `--claim`/`--claim-file`. |
| `--vault NAME` | Scope the whole session to a per-topic vault (`hoardcore_data/NAME/`). A comma/space list (`a,b,c`) enables **cross-vault read**: vault `a` is the write-primary; `b,c` are read-only companions. Search/verify/audit/`--hint` fuse across all of them; new ingest/discover only touches `a`. Single name behaves exactly as before. |
| `--path PATH` | With `local`: relative path (file or directory) inside `storage.local_dir` to process; defaults to the whole `local_dir`. |
| `--list` | With `local`: read-only scan — list supported files under `--path` without ingesting. |
| `--mode MODE` | For `search`: `fast` (FTS-only) or `hybrid` (vector+RRF). Default follows config. Note: with `embeddings.fts_fast_path=true` (default), `hybrid` still short-circuits to the FTS fast path whenever FTS5 alone fills the result set — hits are then tagged `retrieval='fts_fast'`, not `'hybrid'`. Set `fts_fast_path=false` to always force the vector+RRF path. |
| `--parallel` / `--no-parallel` | Override threaded ingest for this run (in-memory only, not written to `hoardcore.toml`). Engages the parallel reader→embed→write pipeline for batches of 8+ chunks; default follows `indexer.parallel` (on). On smaller batches it is a silent no-op (sequential path). |
| `--log-level LEVEL` | Override log verbosity for this run: `debug`, `info` (default), `warning`, or `error`. |
| `--migrate` | With `check`: rebuild the vault at the configured `storage.page_size` (16 KB default) via `VACUUM INTO`. |
| `--force` | Ignore the cache and re-fetch / re-index. |

Use a positional of `_` when an action (e.g. `search`, `discover`, `research`, `ingest`, `verify`, `check`, `audit`) does not need a URL.

### `verify` — the programmatic audit

Makes the `[V]` honor-system tag machine-checkable. It checks a claim against the vault's stored text and reports one of three states:

```bash
python hoardcore.py _ --action verify --claim "the Epoch doubling time is 6 months" --recall 5
```

| Result | Meaning | Exit code |
|---|---|---|
| `VERIFIED` | The normalized claim appears verbatim in stored chunk text (a sliding 60-char window is tested across the whole claim, so a distinctive tail still verifies even if the opening is generic; comparison is *typography-blind* — en/em dashes, smart quotes, curly vs straight apostrophes and full-width Unicode are folded so a typesetter's punctuation never flips a verdict) | `0` |
| `PARTIAL` | The top all-terms FTS5 hit **measurably beats the vault's coincidence floor** (the best rank any single claim term achieves alone, by a corpus-scaled relative margin), but there is no verbatim match; co-occurrence of a few common words in unrelated boilerplate does *not* count as partial | `1` |
| `UNVERIFIED` | No vault support for the claim | `2` |

The verbatim stage checks the full normalized claim (not just a fixed-size prefix) against all candidate rows — it does not truncate candidates to the first 100. Agents and CI can branch on the exit code: refuse to emit a `[V]` tag unless `verify` returns `0`.

> **CI gotcha: don't pipe `verify` output.** The exit code is the contract — but if you pipe `verify` through `tail`/`head` (e.g. to trim logs), the shell returns the *pipe's* exit status, not `verify`'s. Capture `$?` from an unpiped invocation, or branch directly on `subprocess.run(...).returncode`. Same caveat applies to `--claim-list` batch runs (they fail loudly with `2` on any UNVERIFIED — don't let a `| tail` mask that).

> **A denial is not a falsification.** `PARTIAL`/`UNVERIFIED` mean "the vault does not hold this wording verbatim" — they do **not** mean the claim is false. The claim may well be true; it just isn't supported by the stored text as phrased. Treat a denial as a *rewording* instruction, not a verdict.

**What `verify` is lenient about** (folded, so these never flip a verdict): typographic en/em/hyphen dashes, smart quotes **and curly vs straight apostrophes** (`’` ≡ `'`), full-width Unicode (NFKC), whitespace/newlines, and parser-emitted markdown markers (`**bold**`, `*italic*`, `` `code` ``).

**What `verify` is strict about** (these cause `PARTIAL`/`UNVERIFIED`): token identity, word **order**, and whether a word is present or absent — adding, dropping, or reordering words (e.g. a prefix the source doesn't have) fails even if the meaning is identical, and `%` never folds to "percent". To confirm a claim, quote the source's *exact stored words*; `--hint` prints the nearest vault phrase to reword toward.

### `audit` — execution-provenance gate

Where `verify` checks a claim against the vault, `audit` checks an artifact's *evidence chain*: every `[V#N]` tag must trace to a listed, ingested source, not just verify in isolation.

```bash
python hoardcore.py _ --action audit --artifact artifacts/2026-08-18/synthesis_x.md
```

For each `[V#N]` tag in the artifact it checks three links:

1. **VERBATIM** — the claim sentence (or its longest inline double-quoted passage, ≥24 normalized chars) `verify`s against the vault. A quote wrapped across two physical lines (normal markdown wrapping) is joined into one claim, and a line carrying several tags attributes each tag to the double-quoted passage ending nearest before it — so a paraphrased claim can't hide behind another tag's verbatim quote. A bare `[V]` (no `#N`) is verified but not mapping-checked.
2. **MAPPED** — `N` appears in the artifact's Source Links / Citations block as `[#N] <url>`.
3. **INGESTED** — the cited URL has chunks in the vault.

Strictness mirrors `verify` — paraphrased prose is `UNVERIFIED`; only verbatim quoted passages pass. Repeated `[V#N]` tags of the same claim+source count once. A `[V#N]` that appears on an `[H]`/`[E]` analysis line is flagged with an informational warning (it never changes the exit code, but it signals the tag belongs on a verbatim quote in the body). Outputs a per-claim table plus citation-accuracy %, then exits `0` verified / `1` partial / `2` (any unverified **or** any unmapped/not-ingested link). Same CI gotcha as `verify`: never pipe through `tail`/`head` — the shell reports the pipe's exit, not the gate's.

### Configuration file (`hoardcore.toml`)

Created automatically on first run. Key sections:

| Section | Notable keys |
|---|---|
| `[general]` | `timeout_seconds`, `max_retries`, `user_agent` |
| `[network]` | `default_strategy` (`fast`/`balanced`/`aggressive`), `enable_preflight`, `ssrf_protection` (block private/LAN/non-http(s) targets + re-validate redirects, default true) |
| `[auth]` | `cookie_string` (e.g. `cf_clearance=...; session=...`) |
| `[solver]` | `enabled` (default `false` — the FlareSolverr leg is a no-op until you set it to `true`), `url`, `solver_timeout` |
| `[storage]` | `root_dir`, `artifacts_dir`, `artifacts_by_day`, `grounding_subdir` (research grounding contexts land in `artifacts/YYYY-MM-DD/<subdir>/` so they don't pollute the day folder of finished deliverables; default `grounding`), `local_dir` (read-only root for `--action local`; default `local_inputs/`, git-ignored), `save_binary`, `save_raw_html`, `page_size` (16 KB default) |
| `[parsers]` | `enable_pdf`, `enable_docx`, `enable_epub`, `extract_pdf_tables`, `enable_pdf_ocr` (auto-OCR scanned PDF pages when `rapidocr_onnxruntime` is present, default true) |
| `[crawler]` | `respect_robots`, `sitemap_limit`, `parallel_workers` |
| `[indexer]` | `enable_fts`, `search_limit`, `parallel` (threaded ingest, default on for batches of 8+ chunks), `near_dedup` (simhash dup filter, default off), `near_dedup_threshold` |
| `[embeddings]` | `enabled`, `mode` (`sparse`/`dense`), `dense_model`, `dim`, `mrl_dims` (Matryoshka truncation, 0 = full), `hybrid_search`, `top_k`, `quantize`, `fts_fast_path`, `recency_half_life_days`, `conf_mode` (`relative` default / `absolute` legacy), `conf_high_abs`, `conf_low_abs`, `reranker_model` (optional cross-encoder re-ranker) |
| `[discovery]` | `provider`, `top_rank`, `max_retries`, `backoff_seconds` |
| `[research]` | `answer_first` (memory-first routing, default true), `filter_low` (at EMIT drops duplicate `low` hits but keeps one `low` chunk per distinct source, default true), `max_per_source` (cap recall chunks per source URL so one rich page can't crowd out others; 0 = unlimited, default 2) |
| `[chunking]` | `max_tokens`, `overlap_tokens` (sliding window, CJK-aware), `strategy` (`heading` / `paragraph` / `plugin.<name>`) |
| `[plugins]` | `enabled` (discover `hoardcore.*` entry-point plugins) |
| `[cache]` | `ttl_seconds` |

---

## Architecture

### Processing Pipeline (single document)

```
  +-------------------------------------------------------------+
  | Fetch  aiohttp ∥ curl_cffi (concurrent) -> (FlareSolverr)     |
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
  | Store   SQLite FTS5 + content-addressed chunks (chunks_ca)  |
  |         + chunk_vectors | markdown/chunks on disk            |
  +-------------------------------------------------------------+
```

### Hybrid Retrieval (RRF)

`_search_hybrid()` computes two candidate lists (`k=60` RRF constant):
- **FTS**: `SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank`.
- **Vector**: a single numpy matrix–vector product over the whole vector table (`argpartition` for top-k, matrix cached when the table is unchanged), falling back to per-row cosine when numpy is absent. In dense mode this is an ONNX-quantized sentence-transformer embedding (384-dim); in sparse mode it is the FNV-1a lexical hash.

Each candidate contributes `1 / (k + rank + 1)`; results are sorted by the sum and the top-N returned. Hits carry a **confidence band** (`high`/`medium`/`low`) derived per-recall-set rather than from a ratio-to-top (which stays ~0.9 even for weak queries because RRF scores cluster). The default relative mode ranks within the set: the top hit(s) clearly above the set's tail are `high`, the tail hugging the coincidence floor is `low`, and only a keyword-backed set can crown `high`. `conf_mode = "absolute"` restores the legacy thresholds (`conf_high_abs`/`conf_low_abs` on the fused score, plus the matched-both-lists signal). The vector scan is O(N) per query (one matmul + `argpartition`), but the numpy matrix form keeps the constant tiny — ideal for thousands of chunks, still fine at hundreds of thousands.

**Dimension / embedding-config migration.** Each cached vector is keyed by an embedding fingerprint (`embed_fp` = model + dim + quantize). `backfill_vectors` recomputes rows whose fingerprint no longer matches the configured mode/`dense_model`/`dim` (e.g. switching sparse 256-dim ↔ dense 384-dim, or swapping models) *in place*, in batch transactions with stale-row cleanup — so stale vectors are never served and a config switch is resumable across interrupts, no destructive delete-all.

### Resilience & DB Hygiene

A fetch chain runs `aiohttp` and `curl_cffi` concurrently (first leg that returns content wins), with `FlareSolverr` as a serialized terminal leg under the default `aggressive` strategy. With FlareSolverr running (the required Docker container), the chain fully covers Cloudflare-protected pages; discovery's DuckDuckGo hits also get retried and, if shaped, can be solved through the same container. Discovery wraps fetches in bounded retries with exponential backoff and falls back DuckDuckGo → Mojeek. All SQLite access flows through `VaultManager._db()`, which acquires a connection from a reusable **`ConnectionPool`** (default 8 connections, env-overridable via `HC_POOL_SIZE`):

```
with self._db() as (conn, cursor):
    cursor.execute(...)     # conn.commit() on success, rollback() on error
```

Each pooled connection is opened once with WAL, `synchronous=NORMAL`, a 512 MB mmap, an in-memory temp store, and a page cache — so query traffic reuses warm connections instead of paying SQLite open/close per call. A context manager guarantees commit/rollback per block. WAL mode and `synchronous=NORMAL` balance durability against speed. The vault is WORM (write-once-read-many): documents are append-only per version and chunks are content-addressed, so the normal ingest flow never deletes FTS rows — old versions stay queryable. (An earlier `AFTER DELETE` trigger that wiped a document's chunks on a URL-scoped delete was removed, since it could nuke every version's chunks in one row surgery.)

---

## Package Structure

```
HoardCore/
    hoardcore.py           The entire engine (config, fetcher, parsers, chunker,
                           crawler, discovery, vault, CLI, research action)
                           (research.py was merged into this single file)
    hoardcore.toml         Generated config on first run (git-ignored)
    Makefile               install / run / discover / test / bench / clean
    pyproject.toml         Packaging, deps + extras, console script
    AGENTS.md              Agent trigger doc — auto-loaded by your harness at
                           session start; mandates reading skill.md first
    skill.md               Uses-guide / agent skill (the agent operating manual)
    CHANGELOG.md           Release history (SemVer)
    artifacts/               Runtime deliverables (git-ignored, not in repo)
    tests/
        conftest.py            TempConfig + vault / chunk fixtures
        test_vault.py          indexing (WORM versions), RRF, backfill + dimension migration,
                               empty-query safety, content-addressed dedup, verify_vault
                               integrity, confidence bands, verify_claim, dense-mode fallback,
                               bge default model
        test_cli.py            argparse CLI smoke suite (actions, flags, unknown-flag rejection)
        test_network.py        fetch chain fallback, provider parsing/fallback,
                               research strategy forwarding
        test_junk.py           boilerplate/empty/real-content detection
        test_crawler.py        sitemap/robots/discovery (no network I/O)
        test_ocr.py            OCR fallback path for scanned PDF pages
        test_local.py          --action local list/ingest/content-hash-skip/search
        test_multivault.py     cross-vault --vault a,b search + verify fold
        test_audit.py          artifact [V#N] evidence-chain audit
        test_parser_robust.py  parser crash-resistance (random bytes + corrupt binaries)
        test_regressions.py    regression coverage for past bugs
    tools/
        check_version.py       CI version-sync gate (__version__ == pyproject == git tag)
        bench_vector.py        numpy matmul vector-scan benchmark (float32/int8 x page sizes)
        bench_hoardcore_full.py  full numeric benchmark: ingest throughput, search latency,
                               retrieval quality (P@1/P@5/MRR/nDCG), storage footprint,
                               integrity + page-size migration
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
venv/bin/python -m pytest tests/ -v     # run the pytest suite
```

### Code standards

- **Python 3.11+**, `from __future__ import annotations` throughout
- **Minimal global mutable state** — `ConfigManager` is a singleton **only on the default config path**; constructing one with a non-default `config_path` builds a fresh, independent instance (fixing state bleed between separately-constructed managers), and tests isolate via a `TempConfig` stand-in
- **DB access always through `_db()`** — the context manager guarantees commit/rollback/close
- **Optional heavy dependencies lazy-imported** — HTML-only usage never pulls PDF/DOCX/EPUB libraries
- **Annotated signatures** (`Optional`, `Tuple`, `List`) on all public methods

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Status 403 Blocked` on a protected site | Anti-bot challenging connection | Use `--strategy aggressive` (default); ensure FlareSolverr is running (`docker ps`), since it is required to clear Cloudflare challenges |
| `FlareSolverr: Solving challenge...` then timeout | FlareSolverr container not started, or endpoint mismatch | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr`; verify `[solver] url = "http://localhost:8191/v1"` |
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
- Structured exit codes for `scrape`/`crawl`/`search`/`ingest`/`discover` (only `verify`, `check`, `research`, `audit`, and `local` emit meaningful exit codes today)
- Multi-process config reload
- Expanded end-to-end test coverage (crawl with network, live discovery, plugin registration)

---

## License

MIT License. See [LICENSE](LICENSE) for full text.

This tool is intended for personal, research, and educational use. When accessing third-party websites, respect their terms of service and robots.txt. Support the sites and creators you rely on.
