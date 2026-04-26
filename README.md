# SynapseNotes

A multi-KB personal knowledge base powered by Claude Code. Obsidian-compatible markdown notes with agentic research, hybrid retrieval, link graph analysis, evaluation harness, and MCP server for external tool access.

## What This Is

SynapseNotes is the machinery for building and querying a personal knowledge base through Claude Code. You ask questions, research topics, ingest sources — and the system produces atomic markdown notes with citations, wikilinks, and metadata. The notes are Obsidian-compatible, the search is TF-IDF + graph-augmented, and everything is exposed as an MCP server so other tools can query your knowledge.

The system is the infrastructure. The content is yours.

## How It Works

```
You ask a question or research a topic
    → Claude searches the web (concurrent agents, configurable depth)
    → Saves source material to references/ (structured summaries with URLs)
    → Produces atomic notes in kbs/<kb_name>/ (one concept per file)
    → Links everything with [[wikilinks]] and inline citations
    → Indexes for hybrid search (TF-IDF + title/slug/tag boost + graph expansion)
    → Can publish as interactive HTML reports
    → Accessible from any project via MCP server
```

## Multi-KB Architecture

Notes are organized into multiple knowledge bases with different purposes:

```
kbs.yaml                    ← Registry declaring all KBs
kbs/
  general/                  ← Default KB (cross-cutting concepts, tools)
  personal/                 ← Private notes (gitignored)
  blockchain-security/      ← Domain KB (can be shared independently)
  <your-domain>/            ← Add more by editing kbs.yaml
```

Each KB is a flat directory of markdown files. The system builds per-KB indices and a unified index for cross-KB search. Private KBs are excluded from the MCP server and default searches.

**Add a new KB**: edit `kbs.yaml`, create the directory, done. The system discovers KBs from the registry.

## Project Structure

```
kbs.yaml                    KB registry (declares all KBs)
kbs/*/                      Note directories (one per KB, gitignored)
references/                 Source material (shared across KBs, gitignored)
publish/                    Generated HTML reports (gitignored)

.kb/
  config.yaml               All tunable thresholds (search, coverage, similarity, etc.)
  taxonomy.seed.yaml         Starter tag registry (copy to taxonomy.yaml)
  kb-index.py               Search engine, linter, link graph, eval — the core
  build-report.py            HTML report generator (uses python-markdown)
  mcp_server.py             Read-only MCP server (9 tools, stdio transport)
  backup.sh                 Backup script (keeps last 10 timestamped tarballs)
  pyproject.toml             Python 3.12 deps for MCP server (managed by uv)
  templates/report.html      HTML template for published reports

.claude/skills/
  kb-research/               Deep agentic research loop
  kb-question/               Quick Q&A → saved as note
  kb-ingest/                 Extract notes from files/URLs
  kb-search/                 Search + synthesize from KB
  kb-explain/                Narrative synthesis across notes
  kb-publish/                Publish as interactive HTML
  kb-review/                 Quality audit (lint, graph, staleness, duplicates)
  kb-note/                   Capture insights from conversation

tests/
  eval_data.json             Golden query set (10 queries, graded relevance)
  test_retrieval.py          Recall@5, MRR, nDCG@5 evaluation
  test_generation.py         DeepEval faithfulness + answer relevancy
```

## Skills

### Research & Creation

| Skill | Purpose |
|---|---|
| `/kb-research <topic>` | Deep research: plan questions → web search → save references → create atomic notes. Configurable depth (shallow/medium/deep) and checkpoints. |
| `/kb-question <question>` | Quick Q&A saved as a note. Uses own knowledge + optional web search. |
| `/kb-ingest <file or URL>` | Extract atomic notes from source material (papers, articles, docs). Creates a hub note linking all extractions. |
| `/kb-note` | Extract insights from the current conversation into notes. |

### Retrieval & Synthesis

| Skill | Purpose |
|---|---|
| `/kb-search <query>` | Tiered retrieval: quick lookup for specific terms, full hybrid search + multi-query expansion for complex queries. Synthesizes a grounded answer with citations. |
| `/kb-explain <topic>` | Synthesize a coherent narrative from all relevant notes. Flags gaps. Can save as a rewritable synthesis note. |

### Publishing & Maintenance

| Skill | Purpose |
|---|---|
| `/kb-publish <note>` | Generate a self-contained interactive HTML report. Supports hub mode (collapsible sections for linked notes). |
| `/kb-review` | Quality audit: lint (frontmatter, links, citations), link graph (orphans, components, bridges), staleness, duplicates, tag sprawl, note size, retrieval quality. |

All skills support `--kb <name>` to target a specific knowledge base.

## Search & Retrieval

The retrieval pipeline combines multiple signals:

1. **TF-IDF** with contextual metadata prepending (title, type, tags prepended to body text)
2. **Title/slug/tag boosting** — direct lexical matches get multiplied scores
3. **Graph expansion** — top results' wikilink neighbors get additive score boosts
4. **Multi-query fusion** — Claude generates reformulations, each searched independently, results merged via Reciprocal Rank Fusion
5. **Dense embeddings** (optional) — sentence-transformers, Voyage AI, or OpenAI; fused with TF-IDF via RRF
6. **Topic weighting** — underrepresented clusters get a configurable boost

All thresholds are in `.kb/config.yaml`. The system includes an evaluation harness (`tests/`) with 10 golden queries to measure Recall@5, MRR, and nDCG@5 after any infrastructure change.

## Note Format

```markdown
---
title: Note Title
tags: [tag1, tag2]
created: 2026-04-20
updated: 2026-04-20
type: concept | question | reference | insight | synthesis
valid_from: 2026-04-20    # when this became true (optional)
valid_until: null          # null = still valid; date = expired
deprecated_by: null        # slug of superseding note
depends_on: []             # synthesis notes only: atomic note slugs
sources:
  - ../../references/source-file.md
related:
  - "[[other-note]]"
  - "[[other-kb:cross-kb-note]]"
---

Body with [[wikilinks]] and inline citations
([Author 2024](../../references/author-paper-2024.md)).

## Key Takeaways
- Bullet points for quick scanning
```

**Types**: `concept` (explanation), `question` (Q&A), `reference` (source extraction), `insight` (original observation), `synthesis` (rewritable narrative from multiple notes).

**Temporal validity**: `valid_from`/`valid_until` track when information was true. The search engine penalizes expired notes. `/kb-review` flags staleness.

## Citation Chain

```
Note cites → ../../references/local-file.md → contains Source: https://original-url
```

Notes cite local reference files, not external URLs directly. Reference files store structured summaries with the original URL. This keeps the KB self-contained and offline-verifiable.

## Infrastructure Commands

All via `python3 .kb/kb-index.py <command> [--kb <name>]`:

| Command | Purpose |
|---|---|
| `build` | Build per-KB + unified indices (TF-IDF, clusters, link graph) |
| `build --incremental` | Only re-index changed notes (content hash comparison) |
| `search "query" --multi "alt1" "alt2"` | Multi-query hybrid search with RRF fusion |
| `quick "query"` | Instant title/slug/tag match (no TF-IDF) |
| `lint` | Validate frontmatter, wikilinks, citations, tags, dates |
| `graph` | Link graph summary (also: `orphans`, `components`, `bridges`, `neighbors`) |
| `map` | Topic map with coverage stats and link density |
| `explore <slug>` | Suggested reading path from a starting note |
| `gaps` | Find thin/weak topic areas |
| `backlink` | Add missing reverse wikilinks |
| `stats` | Index statistics with type distribution |
| `eval` | Run retrieval evaluation (Recall@5, MRR, nDCG@5) |
| `feedback summary` | Show accumulated search quality feedback |

## MCP Server

The KB exposes a read-only MCP server for external tools (Claude Code from other projects, Cursor, Claude Desktop, etc.):

```bash
# Start manually
uv run --directory .kb python mcp_server.py
```

**Configure in Claude Code** (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/KnowledgeBase/.kb", "python", "mcp_server.py"]
    }
  }
}
```

**9 tools**: `kb_list`, `kb_search`, `kb_quick`, `kb_read`, `kb_map`, `kb_explore`, `kb_gaps`, `kb_stats`, `kb_coverage`. All accept an optional `kb` parameter. The server auto-rebuilds indices when notes change.

## Publishing

Generate interactive single-file HTML reports:

```bash
python3 .kb/build-report.py <note-slug> [--title "Custom Title"]
```

Or via Claude Code: `/kb-publish <note-or-topic>`

Reports include collapsible sections, full-text search, dark/light mode, wikilink navigation, footnotes with source URLs, and PDF export. Output: one self-contained HTML file with no external dependencies.

## Setup

**Requirements**: Python 3.9+ (for kb-index.py), Python 3.12+ with uv (for MCP server), Claude Code.

```bash
# Clone
git clone <repo-url> && cd KnowledgeBase

# Install Python deps (for search index)
pip3 install pyyaml scikit-learn numpy markdown

# Initialize taxonomy
cp .kb/taxonomy.seed.yaml .kb/taxonomy.yaml

# Create your first KB directories
mkdir -p kbs/general kbs/personal

# Build index (will be empty initially)
python3 .kb/kb-index.py build

# (Optional) Install MCP server deps
cd .kb && uv sync && cd ..

# Start using via Claude Code
# /kb-question "How does TCP congestion control work?"
# /kb-research "Zero-knowledge proofs" --depth deep
```

## Design Principles

1. **Atomic notes** — one concept per file, linked via wikilinks. Target 400-700 words per note.
2. **Local-first citations** — reference files store source summaries; notes cite locally.
3. **Multi-KB separation** — private, general, and domain KBs with different access levels.
4. **Measurable retrieval** — evaluation harness with golden queries; every infrastructure change can be regression-tested.
5. **Obsidian-compatible** — standard markdown with YAML frontmatter and `[[wikilinks]]`.
6. **Tool-accessible** — MCP server exposes all read operations to external LLM tools.
7. **Progressive complexity** — start with `/kb-question`, scale to `/kb-research` with concurrent agents.

## Git Strategy

- `kbs/*/`, `references/`, `publish/` are gitignored (content is personal)
- `.kb/taxonomy.yaml` and `.kb/log.md` are gitignored (usage data)
- Skills, scripts, config, templates, and tests are tracked (machinery)
- The system is reproducible; the content is yours
