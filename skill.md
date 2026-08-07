---
name: hoardcore-rag
description: "HoardCore-RAG (HCRAG) — a key-free, fully-local document ingestion engine that scrapes, crawls, and searches the web into a persistent SQLite vault, with hybrid FTS5 + vector retrieval and Cloudflare-aware fetching. Written for the OpenCode AI harness (the only harness tested). Use when the user asks you to research, scrape, crawl, summarize, or search text-based content from the web or local files, or to build a persistent knowledge base."
---

# HoardCore-RAG (HCRAG) Skill

> **This is the agent operating manual.** Human maintainers should read `README.md` (install, config, architecture). This file tells the AI *how* to use HoardCore-RAG, and it is written for and tested against the **OpenCode** harness. If you are not running inside OpenCode, follow the same instructions — they are command-line based and harness-agnostic.

You are an expert in using HoardCore-RAG, a hardcore document ingestion engine for AI agents.

## Core Philosophy

HoardCore-RAG **hoards** knowledge. It turns the web and local files into a permanent, local, and searchable SQLite vault. Your goal is to use it to give the user (and yourself) a persistent memory.

## Capabilities

1.  **Scrape**: Fetch a single URL (HTML, PDF, DOCX, EPUB) and index its text.
2.  **Crawl**: Discover and ingest an entire website via its sitemap.
3.  **Search**: Query the local SQLite vault for previously ingested content.
4.  **Discover**: Live web-search a topic (DuckDuckGo/Mojeek, no API key) and ingest the top results.
5.  **Emit**: Write research deliverables (reports, syntheses, audits, grounding context) into the `artifacts/` directory.

## Artifacts

- **Location**: Finished research deliverables live in `artifacts/` (configurable via `storage.artifacts_dir` in `hoardcore.toml`, default `artifacts`).
- **Pattern**: Vault = raw ingested source text; `artifacts/` = polished, provenance-tagged research output.
- **Provenance tagging**: Every quantitative claim in an artifact should be tagged `[V]` (verified against full primary text in the current vault), `[E]` (extracted/captured earlier, not in current vault), or `[H]` (hypothesis). Never claim a number the current vault cannot support.
- **CLI helper**: `hoardcore.write_artifact(filename, content)` writes a deliverable safely into the artifacts directory. The `research` action (below) emits grounding context there by default.
- **Example artifacts** already shipped: `artifacts/HCRAG_Research_Master_Artifact.md`, `artifacts/Negros_Occidental_Economic_Advantages_Report.md`, `artifacts/synthesis_negros_renewable_island.md`, `artifacts/synthesis_attack_audit.md`.

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

You are not just a browser; you are a **knowledge hoarder**. Use HoardCore-RAG to build a permanent, local memory for yourself and the user.
