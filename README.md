# SynapseNotes

A multi-KB personal knowledge base powered by Claude Code. Obsidian-compatible markdown notes with agentic research, hybrid retrieval, link graph analysis, knowledge quality tracking, and MCP server for external tool access.

## What This Is

SynapseNotes is the machinery for building, querying, and maintaining a personal knowledge base through Claude Code. You ask questions, research topics, ingest sources — and the system produces atomic markdown notes with citations, wikilinks, and metadata. The notes are Obsidian-compatible, the search is TF-IDF + graph-augmented, and everything is exposed as an MCP server so other tools can query your knowledge.

**The system is the infrastructure. The content is yours.**

```
┌─────────────────────────────────────────────────────────────┐
│                        YOU (Claude Code)                     │
│  /kb-research  /kb-search  /kb-question  /kb-ingest         │
└──────────┬──────────┬──────────┬──────────┬─────────────────┘
           │          │          │          │
     ┌─────▼──────────▼──────────▼──────────▼─────┐
     │              SynapseNotes Engine            │
     │  ┌─────────┐ ┌──────────┐ ┌──────────────┐ │
     │  │ Search  │ │  Linter  │ │ Link Graph   │ │
     │  │ TF-IDF  │ │ Quality  │ │ Patterns     │ │
     │  │ + Graph │ │ Checks   │ │ Contradicts  │ │
     │  └─────────┘ └──────────┘ └──────────────┘ │
     └──────────┬──────────┬──────────┬───────────┘
                │          │          │
     ┌──────────▼──┐  ┌───▼────┐  ┌──▼──────────┐
     │ kbs/general │  │ kbs/   │  │ kbs/        │
     │ 238 notes   │  │personal│  │ blockchain- │
     │             │  │ private│  │ security    │
     │             │  │        │  │ 114 notes   │
     └─────────────┘  └────────┘  └─────────────┘
                │          │          │
     ┌──────────▼──────────▼──────────▼───────────┐
     │           references/ (shared)              │
     │     Structured source summaries with URLs   │
     └─────────────────────────────────────────────┘
                         │
     ┌───────────────────▼─────────────────────────┐
     │              MCP Server (stdio)              │
     │   kb_search  kb_read  kb_map  kb_explore    │
     │   Accessible from any Claude Code project    │
     └─────────────────────────────────────────────┘
```

## How It Works

```
You ask a question or research a topic
    │
    ├─→ Claude searches the web (concurrent agents, configurable depth)
    ├─→ Saves source material to references/ (with source_type classification)
    ├─→ Infers which KB to save to (from topic + existing coverage)
    ├─→ Produces atomic notes with epistemic_status tracking
    ├─→ Links everything with [[wikilinks]] and inline citations
    ├─→ Indexes for hybrid search (TF-IDF + title boost + graph expansion)
    ├─→ Detects patterns, contradictions, and gaps automatically
    └─→ Accessible from any project via MCP server
```

## Multi-KB Architecture

Notes are organized into multiple knowledge bases with different purposes and access levels:

```
kbs.yaml                    ← Registry declaring all KBs
kbs/
  general/                  ← Default KB (cross-cutting concepts, tools)
  personal/                 ← Private notes (gitignored, excluded from MCP)
  blockchain-security/      ← Domain KB (can be shared independently)
  <your-domain>/            ← Add more with /kb-init
```

### Smart KB Routing

The system automatically routes queries and notes to the right KB:

```
                    "reentrancy exploit"
                          │
                   ┌──────▼──────┐
                   │  KB Router  │
                   │ Check each  │
                   │ KB for best │
                   │   match     │
                   └──┬───────┬──┘
                      │       │
          Strong match│       │No clear match
          in one KB   │       │
                      ▼       ▼
            ┌─────────────┐ ┌──────────┐
            │ blockchain- │ │ Unified  │
            │ security    │ │ Index    │
            │ (scoped)    │ │ (all KBs)│
            └─────────────┘ └──────────┘
```

- **Writes** (`/kb-research`, `/kb-question`): infer KB from topic → propose → user confirms
- **Reads** (`/kb-search`, `/kb-explain`): infer KB from query → auto-route or fall back to unified
- **Explicit** (`--kb blockchain-security`): always overrides to specified KB

## Knowledge Quality Tracking

### Epistemic Status

Every note tracks how confident we are in its claims:

```
                    Confidence Spectrum

  verified ──── likely ──── speculative ──── disputed ──── opinion
     │            │              │               │            │
  On-chain    Security       Informed       Conflicting   Author's
  data,       firm           inference,     claims        judgment,
  audit       analyses,      may change     exist         not
  reports     Rekt News                                   falsifiable
```

When synthesizing across notes, the system surfaces confidence:
- "Based on verified on-chain data: X"
- "According to Prestwich's analysis [opinion]: Y"

### Source Classification

Reference files are classified by reliability:

| Source Type | Examples | Weight |
|---|---|---|
| **primary** | On-chain data, audit reports, official post-mortems, academic papers | Highest |
| **secondary** | Security firm blogs, Rekt News, Chainalysis reports | High |
| **opinion** | Tweets, blog opinions, community commentary | Medium |
| **unverified** | News articles, aggregator summaries | Lowest |

## Search & Retrieval

```
         User Query: "How do oracle attacks work?"
                        │
              ┌─────────▼─────────┐
              │   KB Router       │
              │   (infer best KB) │
              └─────────┬─────────┘
                        │
              ┌─────────▼─────────┐
              │  Multi-Query      │
              │  Expansion        │
              │  + reformulations │
              └─────────┬─────────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
    ┌─────────┐   ┌──────────┐   ┌─────────┐
    │ TF-IDF  │   │  Title/  │   │  Dense  │
    │ + meta  │   │  Slug/   │   │ Embed   │
    │ prepend │   │  Tag     │   │ (opt)   │
    │         │   │  Boost   │   │         │
    └────┬────┘   └────┬─────┘   └────┬────┘
         │             │              │
         └──────┬──────┘──────────────┘
                │
       ┌────────▼────────┐
       │  RRF Fusion     │
       │  (merge ranks)  │
       └────────┬────────┘
                │
       ┌────────▼────────┐
       │  Graph Expansion │
       │  (1-hop wikilink │
       │   neighbors)     │
       └────────┬────────┘
                │
       ┌────────▼────────┐
       │  Topic Weighting │
       │  (boost small    │
       │   clusters)      │
       └────────┬────────┘
                │
                ▼
         Ranked Results
         (with KB label +
          epistemic status)
```

All thresholds are in `.kb/config.yaml`. The system includes an evaluation harness (`tests/`) with golden queries to measure Recall@5, MRR, and nDCG@5 after any infrastructure change.

## Active Knowledge Management

The KB doesn't just store notes — it actively analyzes its own quality:

### Pattern Detection
```bash
$ python3 .kb/kb-index.py patterns --kb blockchain-security

Detected patterns (10):
  social-engineering (10 notes, NO synthesis note) → $2.4B total
  key-compromise (5 notes, NO synthesis note) → $2.8B total
  oracle-manipulation (8 notes, HAS synthesis) ✓
  bridge-security (15 notes, HAS synthesis) ✓
```

### Contradiction Scanning
```bash
$ python3 .kb/kb-index.py contradictions-scan --kb blockchain-security

Potential contradictions:
  Euler: $240M in euler-exploit vs $197M in largest-defi-exploits
  → pre-recovery vs post-recovery amount
```

### Gap Suggestions
```bash
$ python3 .kb/kb-index.py gaps suggestions --kb blockchain-security

1. [HIGH] Synthesize "key-compromise" pattern (5 incidents, $2.8B)
2. [HIGH] Synthesize "social-engineering" pattern (10 incidents, $2.4B)
3. [MEDIUM] Add insights to cluster with 0 insight notes
```

### Research Gaps
```bash
$ python3 .kb/kb-index.py gaps research

Unresolved research gaps (66 across 10 hubs):
  Research Hub: DeFi Exploits — 6 gaps
  Research Hub: RAG Evaluation — 4 gaps
```

## Skills

### Research & Creation

| Skill | Purpose |
|---|---|
| `/kb-research <topic>` | Deep research: plan → web search → save references → create notes. Configurable depth (shallow/medium/deep). Auto-infers target KB. |
| `/kb-question <question>` | Quick Q&A saved as a note with epistemic status. |
| `/kb-ingest <file or URL>` | Extract atomic notes from source material. Classifies source type. |
| `/kb-note` | Capture insights from the current conversation. |
| `/kb-init` | Create a new knowledge base interactively. |

### Retrieval & Synthesis

| Skill | Purpose |
|---|---|
| `/kb-search <query>` | Smart KB routing → tiered retrieval → multi-query expansion → grounded synthesis with epistemic transparency. |
| `/kb-explain <topic>` | Comprehensive narrative synthesis. Flags gaps. Saves as rewritable synthesis note with dependency tracking. |

### Publishing & Maintenance

| Skill | Purpose |
|---|---|
| `/kb-publish <note>` | Self-contained interactive HTML report with collapsible sections, footnotes, wikilink navigation. |
| `/kb-review` | Quality audit: lint, link graph, staleness, duplicates, tag sprawl, note size, research gaps, retrieval quality. |
| `/kb-move` | Migrate notes between KBs with cross-reference updates. |

## Note Format

```markdown
---
title: Note Title
tags: [tag1, tag2]
created: 2026-04-20
updated: 2026-04-20
type: concept | question | reference | insight | synthesis
epistemic_status: verified | likely | speculative | disputed | opinion
valid_from: 2026-04-20
valid_until: null
deprecated_by: null
depends_on: []           # synthesis notes: atomic note slugs
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

### Exploit Note Template

For blockchain security exploit notes, a richer template is used:

```
## Incident Summary
## Artifacts        ← attacker address, tx hash, contract, block
## Attack Flow      ← indented call trace
## Vulnerable Code  ← pseudocode of the bug
## Root Cause
## The Fix
## Audit History    ← table of auditors, scope, findings
## Fund Flow        ← extraction → laundering → current status
## Classification   ← attack category, SWC ID, on/off-chain
## Similar Exploits ← cross-links to related incidents
## Reproduction     ← DeFiHackLabs PoC, Foundry fork command
## Key Takeaways
```

## Citation Chain

```
Note                    Reference File              Original Source
┌──────────────┐       ┌──────────────────┐        ┌──────────────┐
│ Claims X     │──────▶│ # Title          │───────▶│ https://...  │
│ [cite](ref)  │       │ Source: URL      │        │ The actual   │
│              │       │ Source-Type:     │        │ web page     │
│              │       │   primary        │        │              │
└──────────────┘       │ ## Key Content   │        └──────────────┘
                       └──────────────────┘
```

Notes cite local reference files, not external URLs directly. Reference files store structured summaries with the original URL and a source type classification. This keeps the KB self-contained, offline-verifiable, and quality-traceable.

## Dashboard

```bash
python3 .kb/build-dashboard.py
open publish/dashboard.html
```

Interactive HTML dashboard with 4 tabs:

- **Overview**: KB cards, topic treemap, type distribution, notes over time
- **Knowledge Graph**: D3 force-directed link graph, coverage radar, bridge concepts, tag co-occurrence network
- **Research**: open research gaps (with severity coloring), timeline, depth heatmap
- **Quality**: gap burden chart, retrieval heatmap

## Infrastructure Commands

All via `python3 .kb/kb-index.py <command> [--kb <name>]`:

### Index & Search
| Command | Purpose |
|---|---|
| `build [--incremental]` | Build per-KB + unified indices |
| `search "query" [--multi "alt1" "alt2"]` | Multi-query hybrid search with RRF fusion |
| `quick "query"` | Instant title/slug/tag match |

### Quality & Analysis
| Command | Purpose |
|---|---|
| `lint [slug]` | Validate frontmatter, wikilinks, citations, tags |
| `patterns` | Detect recurring attack/topic patterns, flag unsynthesized ones |
| `contradictions-scan` | Find conflicting amounts, dates, classifications |
| `gaps [topics\|research\|suggestions\|all]` | Topic gaps, research gaps, ranked suggestions |

### Graph & Discovery
| Command | Purpose |
|---|---|
| `graph [orphans\|components\|bridges\|neighbors]` | Link graph analysis |
| `map` | Topic map with coverage stats and link density |
| `explore <slug> [steps]` | Suggested reading path |
| `backlink [slug]` | Add missing reverse wikilinks (including cross-KB) |

### Evaluation & Feedback
| Command | Purpose |
|---|---|
| `eval [retrieval\|generation\|all]` | Retrieval metrics (Recall@5, MRR, nDCG@5) |
| `feedback [summary\|log]` | Search quality feedback loop |
| `stats` | Index statistics with type distribution |

## MCP Server

The KB exposes a read-only MCP server for external tools:

```bash
uv run --directory .kb python mcp_server.py
```

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Claude Code  │     │   Cursor     │     │ Claude       │
│ (any project)│     │              │     │ Desktop      │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────┬───────┘────────────────────┘
                    │ MCP (stdio)
              ┌─────┴─────────────────────────────┐
              │  SynapseNotes MCP Server           │
              │                                    │
              │  kb_list    kb_search  kb_quick    │
              │  kb_read    kb_map     kb_explore  │
              │  kb_gaps    kb_stats   kb_coverage │
              │                                    │
              │  All tools accept optional `kb`    │
              │  parameter. Private KBs excluded.  │
              └────────────────────────────────────┘
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

## Setup

**Requirements**: Python 3.9+ (for kb-index.py), Python 3.12+ with uv (for MCP server), Claude Code.

```bash
# Clone
git clone <repo-url> && cd SynapseNotes

# Install Python deps
pip3 install pyyaml scikit-learn numpy markdown

# Initialize
cp .kb/taxonomy.seed.yaml .kb/taxonomy.yaml
mkdir -p kbs/general kbs/personal
python3 .kb/kb-index.py build

# (Optional) MCP server
cd .kb && uv sync && cd ..

# Start using
# /kb-init blockchain-security
# /kb-research "ZK rollup security" --depth deep
# /kb-search "oracle manipulation attacks"
```

## Design Principles

1. **Atomic notes** — one concept per file, linked via wikilinks. Target 400-700 words.
2. **Epistemic honesty** — every note tracks confidence (verified → opinion). Sources classified by reliability.
3. **Multi-KB separation** — private, general, and domain KBs with smart routing.
4. **Active quality management** — pattern detection, contradiction scanning, gap suggestions. The KB analyzes itself.
5. **Measurable retrieval** — evaluation harness with golden queries. Infrastructure changes are regression-tested.
6. **Local-first citations** — reference files bridge notes to the web. Self-contained and offline-verifiable.
7. **Tool-accessible** — MCP server exposes all read operations to any LLM tool.
8. **Obsidian-compatible** — standard markdown with YAML frontmatter and `[[wikilinks]]`.

## Git Strategy

```
Tracked (machinery):          Gitignored (content):
  .kb/kb-index.py               kbs/*/
  .kb/build-report.py           references/
  .kb/build-dashboard.py        publish/
  .kb/mcp_server.py             .kb/taxonomy.yaml
  .kb/config.yaml               .kb/log.md
  .kb/pyproject.toml             .kb/index/
  .claude/skills/               tests/*_results.json
  tests/*.py
  kbs.yaml
  CLAUDE.md
```

The system is reproducible; the content is yours.
