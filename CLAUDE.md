# Knowledge Base System

A Claude-powered knowledge base using Obsidian-compatible markdown notes.

## Project Structure

```
notes/           — All atomic notes (flat, one concept per file)
references/      — Source material for ingestion (READ-ONLY — never modify)
publish/         — Generated HTML reports for sharing
.kb/config.yaml  — KB settings and all tunable thresholds
.kb/taxonomy.yaml — Loose tag registry (descriptive, not prescriptive)
.kb/index/       — Search index (TF-IDF vectors, metadata, link graph)
.kb/build-report.py  — HTML report builder (uses python-markdown)
.kb/kb-index.py  — Search index, linter, link graph, and query engine
.claude/skills/  — Claude Code skill definitions
```

## Read-Only Directories

**`references/`** — Contains structured summaries of source material (papers, articles, docs). Each file has the external URL at the top (`Source: https://...`) and a summary of key content. Claude may **write new files** here, but must **never edit or delete existing files**. Notes cite these local files rather than external URLs directly — the reference file is the bridge between the KB and the outside world.

## Note Format

All notes use this format:

```markdown
---
title: Note Title
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: question | concept | reference | insight | synthesis
valid_from: YYYY-MM-DD    # when this information became true (optional)
valid_until: null          # null = still valid; date = superseded/expired
deprecated_by: null        # slug of superseding note, if any
depends_on: []             # for synthesis notes only: list of atomic note slugs this depends on
sources: []
related: []
---

(body in markdown with [[wikilinks]] to related notes)
```

**Temporal validity**: Notes can become outdated. `valid_from`/`valid_until` track when information was true. `deprecated_by` points to the superseding note. Default retrieval returns only currently-valid entries. `/kb-review` flags notes past their expected freshness window.

## Note Types

- **question** — Q&A format: Question section + Answer section + Key Takeaways
- **concept** — Explanation of a single concept: Definition + Details + Key Takeaways
- **reference** — Extracted from external sources: Summary + Key Points + Source
- **insight** — Original observations or connections: Observation + Analysis
- **synthesis** — Generated narrative weaving multiple notes; rewritable (regenerate from atomic notes anytime). Must include `depends_on:` listing all atomic note slugs used, enabling staleness detection via `kb-index.py stale-syntheses`.

## Rules for Writing Notes

1. **Atomic**: One concept per note. If a topic has multiple distinct concepts, create multiple notes.
2. **Slugified filenames**: `tcp-congestion-control.md`, lowercase, hyphens for spaces.
3. **Wikilinks**: Use `[[note-name]]` (without `.md`) to link related notes. Add links in both directions.
4. **Tags**: Use existing tags from taxonomy.yaml when applicable. Create new tags freely when needed.
5. **Update vs Create**: When a closely related note exists, update it if the new info deepens the same concept. Create a new note + link if it's a distinct concept.
6. **Key Takeaways**: Always end with bullet-point takeaways for quick scanning.
7. **Sources**: Frontmatter `sources` is a list of local reference file paths (e.g. `../references/filename.md`). These are the authoritative source pointers for the note.
8. **Citations**: Always cite sources inline by linking to the local reference file: `[display text](../references/filename.md)`. The reference file itself contains the external URL. This keeps the KB self-contained and offline-verifiable. For sources not saved locally, use `[text](url)` as a fallback, but prefer saving substantive sources to `references/` first.

## Session Log

`.kb/log.md` is an append-only record of research sessions, notes created, and gaps identified. At the start of a session, read the last 20 entries for continuity. After any `/kb-research`, `/kb-ingest`, or significant infrastructure change, append a log entry with date, action type, what was done, notes created, and key findings.

Format: `## [YYYY-MM-DD] type | Title` followed by bullet points.

## Infrastructure Commands

All commands via `python3 .kb/kb-index.py <command>`:

| Command | Purpose |
|---|---|
| `build` | Full index rebuild (TF-IDF + clusters + link graph) |
| `build --incremental` | Only re-index changed notes (compares content hashes) |
| `build --embed` | Also build dense embeddings |
| `search "query"` | Hybrid search with optional `--tags`, `--type` filters |
| `similar <slug>` | Find notes similar to a given note |
| `coverage "topic"` | Check if KB covers a topic |
| `contradictions <slug>` | Find potentially conflicting notes |
| `stale [days]` | Find temporally stale notes |
| `stale-syntheses` | Find synthesis notes with updated dependencies |
| `stats` | Index statistics with type distribution |
| `clusters` | Show topic clusters |
| `lint [slug]` | Validate notes (frontmatter, links, citations, tags) |
| `graph` | Link graph summary |
| `graph orphans` | Notes with no links in or out |
| `graph components` | Disconnected subgraphs |
| `graph neighbors <slug> [n]` | Notes within n hops |
| `graph bridges` | Critical connector notes |
| `quick "query"` | Fast title/slug/tag match (no TF-IDF) |
| `backlink [slug]` | Add missing reverse wikilinks |
| `feedback summary` | Show accumulated search feedback |
| `feedback log "q" "type" "slugs"` | Log a search quality issue |
| `map` | Topic map with coverage and link density |
| `explore <slug> [steps]` | Suggested reading path from a note |
| `gaps` | Find thin/weak topic areas |
| `eval` | Run retrieval evaluation (Recall@5, MRR, nDCG@5) |
| `eval generation` | Run faithfulness + relevancy checks (requires OPENAI_API_KEY) |
| `eval all --verbose` | Run all evaluations with per-query detail |

## MCP Server

The KB exposes a read-only MCP server for external tools (Claude Code from other projects, Cursor, etc.).

**Start:** `uv run --directory .kb python mcp_server.py`

**Tools:** `kb_search`, `kb_quick`, `kb_read`, `kb_map`, `kb_explore`, `kb_gaps`, `kb_stats`, `kb_coverage`

**Configure in Claude Code** (add to `~/.claude/settings.json` under `mcpServers`):
```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/martinetlee/Project/Personal/KnowledgeBase/.kb", "python", "mcp_server.py"],
      "env": {}
    }
  }
}
```

The server auto-rebuilds the index if notes have changed since the last build (checks every 30s).

## Configuration

`.kb/config.yaml` contains all tunable thresholds for search, coverage, similarity, clustering, staleness, and indexing. Skills read from this config — do not hard-code thresholds in skill prompts or scripts.

## Backup

Run `.kb/backup.sh` to back up notes and references to `~/.kb-backups/`. Keeps last 10 timestamped backups.

## KB Role

The KB is **supplementary** — Claude uses its own knowledge combined with KB content. When the user asks to answer "from KB only", restrict to KB content.
