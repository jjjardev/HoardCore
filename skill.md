---
name: hoardcore
description: "HoardCore (HCH) — a research toolkit and memory protocol for AI agents: a single-file Python module (SQLite vault + CLI) that scrapes, crawls, and searches the web into a persistent SQLite vault, with hybrid FTS5 + vector retrieval and Cloudflare-aware fetching. Use when the user asks you to research, scrape, crawl, summarize, or search text-based content from the web or local files, or to build a persistent knowledge base. Research is DeepResearch by default: every investigation runs the bounded Hardcore Research Loop (DISCOVER -> INGEST -> RECALL -> EMIT with [V]/[E]/[H] provenance); optional depth presets ('deep', 'exhaustive') or an 'x N' pass cap control how deep it goes."
---

# HoardCore (HCH) Skill

> **This is the agent operating manual.** Human maintainers should read `README.md` (install, config, architecture). This file tells the AI *how* to use HoardCore. The agent that reads it runs inside a harness (e.g., OpenCode, Claude Code, or any other) that hosts the LLM and manages context; HoardCore itself is a research toolkit and memory protocol the agent calls via its CLI. The instructions are command-line based and harness-agnostic.

You are an expert in using HoardCore, a research toolkit for AI agents (single-file Python module + SQLite vault + CLI).

## Core Philosophy

HoardCore **hoards** knowledge. It turns the web and local files into a permanent, local, and searchable SQLite vault. Your goal is to use it to give the user (and yourself) a persistent memory.

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
2.  **The Hunt (Discovery & Ingestion)** — Command HoardCore to hunt the open web for high-authority, deep primary sources (academic papers, official reports, technical docs) over shallow blog posts. Run `research` (or `discover` then `ingest`) to bring the top-ranked results into the local SQLite Vault — you are assimilating evidence, not browsing. Use `--strategy aggressive` when a source is anti-bot protected.
3.  **The Recall (Hybrid Retrieval)** — Query the Vault via hybrid retrieval for the **5–10 most relevant chunks** that address the core question (`--recall 5` to `--recall 10`). Cross-reference each chunk against the raw source; if a chunk feels flimsy or lacks context, discard it and retrieve another.
4.  **The Synthesis (Artifact Emission)** — Compile the evidence into a structured **Grounding Context** file (exact source URLs, hybrid scores, distinct-sources summary) via the `research` action, then write the final synthesis report into `artifacts/`. That report is the deliverable.

CLI form:

```
venv/bin/python hoardcore.py _ --action research \
  --query "<the core question>" --discover 6 --recall 8 --strategy aggressive
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
- **Example artifacts** (local, git-ignored — see `artifacts/` in a session where the vault has run): `HCH_Research_Master_Artifact.md`, `Negros_Occidental_Economic_Advantages_Report.md`, `synthesis_negros_renewable_island.md`, `synthesis_attack_audit.md`.

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
    - `"balanced"` (default): Tries standard HTTP, then falls back to TLS fingerprint spoofing. Recommended.
    - `"aggressive"`: Uses all methods including FlareSolverr (requires local setup). Use for Cloudflare-heavy sites.
- **`force_refresh`** (boolean, optional): Set to `true` to re-download and overwrite the cached version.

### Action: "crawl"
Use this to ingest an entire website.

- **`url`** (string, required): The domain to crawl (e.g., `https://docs.python.org/3/`).
- **`action`** (string, required): Set to `"crawl"`.
- **`strategy`** (string, optional): Same as above. `"balanced"` is recommended for crawling.
- **`force_refresh`** (boolean, optional): Set to `true` to re-crawl everything.

### Action: "search"
Use this to query the local vault.

- **`url`** (string, required): The domain to search within (e.g., `https://docs.python.org/3/`).
- **`action`** (string, required): Set to `"search"`.
- **`query`** (string, required): The search term (e.g., `"asyncio event loop"`).
- **`force_refresh`** (boolean, optional): Not applicable for search.

## Workflow Guidance

1.  **For a single article/paper**: Use `action="scrape"`. The tool will return chunks of text. Summarize these chunks for the user.
2.  **For a documentation site**: Use `action="crawl"`. This may take a few minutes. Inform the user that you are building a local index.
3.  **For follow-up questions**: Use `action="search"` with a specific query. This is instant and does not require network calls.
4.  **If a site is blocked**: Suggest using `strategy="aggressive"` or ask the user to configure a `cookie_string` in the `hoardcore.toml` config file.
5.  **For an open-ended research question**: Use the `research` action — the agentic pattern `DISCOVER -> INGEST -> RECALL -> EMIT` (merged into `hoardcore.py`):
    - `venv/bin/python hoardcore.py _ --action research --query "..." --discover 5 --recall 6`
    - `--out` optional; defaults to `artifacts/grounding_context.md`.
6.  **For a research deliverable (report/synthesis/audit)**: Write it into `artifacts/` with `[V]/[E]/[H]` provenance tags on every quantitative claim.
7.  **Adversarial audit discipline**: Before finalizing an artifact, audit it — verify each quantitative claim against the current vault (re-run hybrid retrieval / SQL for the number). Tag anything unverifiable as `[E]` or remove it. Only claims traceable to full primary text in the current vault get `[V]`.

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
- **Dependencies**: Requires Python 3.11+ and the libraries listed in the `Makefile`. If a command fails, suggest running `make install`.

## Remember

You are not just a browser; you are a **knowledge hoarder**. Use HoardCore to build a permanent, local memory for yourself and the user.
