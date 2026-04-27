# Knowledge Base System

An AI-agent-powered multi-KB knowledge base using Obsidian-compatible markdown notes. Designed for use with Claude Code, Codex, Cursor, and other agentic CLIs (this file is also exposed as `AGENTS.md` via symlink).

## Project Structure

```
kbs.yaml             — KB registry (declares all KBs and their properties; per-user, gitignored — copy from kbs.example.yaml)
kbs/
  general/           — Default KB for cross-cutting concepts
  personal/          — Private notes (gitignored)
  <your-domain>/     — Add domain KBs as needed
references/          — Source material for ingestion (READ-ONLY — shared across all KBs)
publish/             — Generated HTML reports for sharing
.kb/config.yaml      — KB settings and all tunable thresholds
.kb/taxonomy.yaml    — Loose tag registry (shared across all KBs)
.kb/index/
  general/           — Per-KB search index
  personal/
  <your-domain>/
  _unified/          — Merged cross-KB index
.kb/kb-index.py      — Search index, linter, link graph, and query engine
.kb/build-report.py  — HTML report builder (uses python-markdown)
.kb/mcp_server.py    — Read-only MCP server for external tools
.claude/skills/      — Agent skill definitions (also exposed at .agents/skills/ via symlink)
tests/               — Retrieval and generation eval harness
```

## Multi-KB Architecture

The system supports multiple knowledge bases with different purposes and access levels. Each KB is a directory under `kbs/` declared in `kbs.yaml`:

```yaml
kbs:
  general:
    path: kbs/general
    default: true           # where notes go if no KB specified
  personal:
    path: kbs/personal
    private: true           # gitignored, excluded from MCP and default search
  my-domain:
    path: kbs/my-domain
```

**Key rules:**
- All CLI commands support `--kb <name>` to scope to a specific KB
- Reads/searches without `--kb` use the unified index (all non-private KBs)
- Writes without `--kb` default to `general`
- Private KBs are excluded from MCP and unified search unless explicitly requested
- New domain KBs: add an entry to `kbs.yaml` and `mkdir kbs/<name>/`

## Read-Only Directories

**`references/`** — Shared across all KBs. Contains structured summaries of source material (papers, articles, docs). Each file has the external URL at the top (`Source: https://...`) and a summary of key content. The agent may **write new files** here, but must **never edit or delete existing files**. Notes cite these local files rather than external URLs directly.

Reference files should include a `source_type` classification:
```markdown
# Title
Source: https://url
Fetched: YYYY-MM-DD
Source-Type: primary | secondary | opinion | unverified

## Key Content
...
```

Source types:
- **primary** — original data, official post-mortems, academic papers, protocol/product documentation, raw measurements
- **secondary** — analyses or write-ups based on primary sources (firm reports, technical breakdowns, reputable journalism)
- **opinion** — tweets, blog opinion pieces, community commentary
- **unverified** — news articles, aggregator summaries, AI-generated content

Notes inherit confidence from their sources. A note citing only primary sources has higher epistemic weight than one citing opinions.

## Note Format

All notes use this format:

```markdown
---
title: Note Title
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: question | concept | reference | insight | synthesis
epistemic_status: verified | likely | speculative | disputed | opinion
valid_from: YYYY-MM-DD    # when this information became true (optional)
valid_until: null          # null = still valid; date = superseded/expired
deprecated_by: null        # slug of superseding note, if any
depends_on: []             # for synthesis notes only: list of atomic note slugs this depends on
sources: []
related: []
---

(body in markdown with [[wikilinks]] to related notes)
```

**Epistemic status**: How confident are we in this note's claims?
- **verified** — backed by primary sources. Independently confirmable.
- **likely** — backed by credible secondary sources. Consistent with evidence but not independently verified.
- **speculative** — informed inference or extrapolation. May be wrong as new information emerges.
- **disputed** — conflicting claims exist between credible sources. Note presents both sides.
- **opinion** — the author's judgment or interpretation. Not falsifiable.

When synthesizing across notes, `/kb-search` and `/kb-explain` should surface epistemic status: "Based on verified primary sources: X. Based on [author]'s analysis [opinion]: Y."

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
3. **Wikilinks**: Use `[[note-name]]` (without `.md`) to link related notes within the same KB. For cross-KB links: `[[kb-name:note-name]]`. Add links in both directions.
4. **Tags**: Use existing tags from taxonomy.yaml when applicable. Create new tags freely when needed.
5. **Update vs Create**: When a closely related note exists, update it if the new info deepens the same concept. Create a new note + link if it's a distinct concept.
6. **Key Takeaways**: Always end with bullet-point takeaways for quick scanning.
7. **Sources**: Frontmatter `sources` is a list of local reference file paths (e.g. `../../references/filename.md`). These are the authoritative source pointers for the note. Note: two levels up from `kbs/<name>/`.
8. **Citations**: Always cite sources inline by linking to the local reference file: `[display text](../../references/filename.md)`. The reference file itself contains the external URL. This keeps the KB self-contained and offline-verifiable. For sources not saved locally, use `[text](url)` as a fallback, but prefer saving substantive sources to `references/` first.

## Session Log

`.kb/log.md` is an append-only record of research sessions, notes created, and gaps identified. At the start of a session, read the last 20 entries for continuity. After any `/kb-research`, `/kb-ingest`, or significant infrastructure change, append a log entry with date, action type, what was done, notes created, and key findings.

Format: `## [YYYY-MM-DD] type | Title` followed by bullet points.

## Infrastructure Commands

All commands via `python3 .kb/kb-index.py <command> [--kb <name>]`:

| Command | Purpose |
|---|---|
| `build [--kb name]` | Full index rebuild (per-KB + unified) |
| `build --incremental [--kb name]` | Only re-index changed notes |
| `build --embed [--kb name]` | Also build dense embeddings |
| `search "query" [--kb name]` | Hybrid search (unified or scoped) |
| `search "query" --multi "alt1" "alt2"` | Multi-query fusion search |
| `similar <slug> [--kb name]` | Find notes similar to a given note |
| `coverage "topic" [--kb name]` | Check if KB covers a topic |
| `contradictions <slug> [--kb name]` | Find potentially conflicting notes |
| `stale [days] [--kb name]` | Find temporally stale notes |
| `stale-syntheses [--kb name]` | Find synthesis notes with updated dependencies |
| `stats [--kb name]` | Index statistics with type distribution |
| `clusters [--kb name]` | Show topic clusters |
| `lint [slug] [--kb name]` | Validate notes (frontmatter, links, citations, tags) |
| `graph [--kb name]` | Link graph summary |
| `graph orphans [--kb name]` | Notes with no links in or out |
| `graph components [--kb name]` | Disconnected subgraphs |
| `graph neighbors <slug> [n]` | Notes within n hops |
| `graph bridges [--kb name]` | Critical connector notes |
| `quick "query" [--kb name]` | Fast title/slug/tag match (no TF-IDF) |
| `backlink [slug] [--kb name]` | Add missing reverse wikilinks |
| `feedback summary` | Show accumulated search feedback |
| `feedback log "q" "type" "slugs"` | Log a search quality issue |
| `map [--kb name]` | Topic map with coverage and link density |
| `explore <slug> [steps]` | Suggested reading path from a note |
| `gaps [--kb name]` | Find thin/weak topic areas |
| `eval [--kb name]` | Run retrieval evaluation (Recall@5, MRR, nDCG@5) |
| `eval generation` | Run faithfulness + relevancy checks (requires OPENAI_API_KEY) |
| `eval all --verbose` | Run all evaluations with per-query detail |

## MCP Server

The KB exposes a read-only MCP server for external tools (Claude Code, Codex, Cursor, etc.).

**Start:** `uv run --directory .kb python mcp_server.py`

**Tools:** `kb_search`, `kb_quick`, `kb_read`, `kb_list`, `kb_map`, `kb_explore`, `kb_gaps`, `kb_stats`, `kb_coverage`

All MCP tools accept an optional `kb` parameter to scope to a specific KB. Private KBs are excluded from default search.

**Configure in Claude Code** (add to `~/.claude/settings.json` under `mcpServers`):
```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/KnowledgeBase/.kb", "python", "mcp_server.py"],
      "env": {}
    }
  }
}
```

The server auto-rebuilds the index if notes have changed since the last build (checks every 30s).

## Configuration

`.kb/config.yaml` contains all tunable thresholds for search, coverage, similarity, clustering, staleness, and indexing. Skills read from this config — do not hard-code thresholds in skill prompts or scripts.

`kbs.yaml` at the project root declares all KBs. It is per-user and gitignored — copy `kbs.example.yaml` to `kbs.yaml` to bootstrap. To add a new KB: add an entry to `kbs.yaml` and create the directory.

## Backup

Run `.kb/backup.sh` to back up all KBs and references to `~/.kb-backups/`. Keeps last 10 timestamped backups.

## KB Role

The KB is **supplementary** — the agent uses its own knowledge combined with KB content. When the user asks to answer "from KB only", restrict to KB content.
