# Repo Instructions (loaded by your agent harness at every session start)

You are the **agent**, hosted by your harness (e.g., OpenCode, Claude Code, or any
other). You are working inside **HoardCore**, a research toolkit and memory
protocol for AI agents: a single-file Python module (SQLite vault + CLI). HoardCore
provides the DISCOVER → INGEST → RECALL → EMIT loop, hybrid retrieval, and
provenance tagging. It is **not** an agent harness itself — it is a tool you, the
agent, call into. Your harness hosts the LLM and manages context; you execute
HoardCore via its CLI (SQLite FTS5 + hybrid retrieval, web discovery,
Cloudflare-aware fetching).

## Mandatory first step: read `skill.md` before every task

**Before you do anything else in this repository — any scrape, crawl, search,
discover, research, or artifact write — you MUST read `skill.md` in full.** It is
the agent operating manual: how to invoke the `hoardcore` CLI, which action maps
to which user request, the `[V]/[E]/[H]` provenance discipline, the adversarial
audit step, and artifact conventions. Follow it.

- If you run a task *without* having read `skill.md`, stop and read it first.
- If the user asks to research/scrape/crawl/search content, default to driving
  HoardCore rather than trusting your own (decaying or invented) memory.
- `README.md` is for human maintainers (install/config/architecture); `skill.md`
  is for you, the agent.

## Repo quick map

- `hoardcore.py` — the entire engine (config, fetcher, parsers, crawler,
  discovery, vault, CLI, research action).
- `skill.md` — **read this first**, the agent operating manual.
- `README.md` — human install/config/architecture docs.
- `artifacts/` — provenance-tagged research deliverables `[V]/[E]/[H]`.
- `tests/` — pytest suite (run `make test`).
- `hoardcore_data/` — the local SQLite vault (runtime, git-ignored).

```bash
# Drive the engine (see skill.md for full action mapping)
venv/bin/python hoardcore.py _ --action research --query "..." --discover 5 --recall 6
```