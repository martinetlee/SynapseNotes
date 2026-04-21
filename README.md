# Knowledge Base

A personal knowledge base powered by Claude Code, stored as Obsidian-compatible markdown notes with agentic research, synthesis, and publishing capabilities.

## How It Works

```
You ask a question or topic
    → Claude researches the web (concurrent agents)
    → Saves source material to references/
    → Produces atomic notes in notes/
    → Links everything with wikilinks and citations
    → Can publish as interactive HTML reports
```

## Structure

```
notes/           Atomic markdown notes (one concept per file)
references/      Structured summaries of source material (local citation targets)
publish/         Generated HTML reports for sharing
.kb/
  config.yaml    KB settings
  taxonomy.yaml  Tag registry
  templates/     HTML templates for publishing
  build-report.py  Report builder script
.claude/skills/  Claude Code skill definitions
```

## Skills

| Skill | Purpose |
|-------|---------|
| `/kb-research <topic>` | Deep research: plan → search → save references → create notes |
| `/kb-question <question>` | Quick Q&A saved as a note |
| `/kb-ingest <file or URL>` | Extract notes from source material |
| `/kb-search <query>` | Search and synthesize from existing notes |
| `/kb-explain <topic>` | Synthesize a narrative from multiple notes |
| `/kb-publish <note or topic>` | Publish as interactive HTML report |
| `/kb-review` | Audit for quality issues (broken links, orphans, tag sprawl) |
| `/kb-note` | Extract insights from the current conversation |

## Note Format

```markdown
---
title: Note Title
tags: [tag1, tag2]
created: 2026-04-20
updated: 2026-04-20
type: concept | question | reference | insight
sources:
  - ../references/source-file.md
related:
  - "[[other-note]]"
---

Body content with [[wikilinks]] and inline citations
([Author 2024](../references/author-paper-2024.md)).
```

## Citation Chain

```
Note cites → ../references/local-file.md → contains Source: https://original-url
```

Notes never cite external URLs directly. Reference files are the bridge between the KB and the outside world. This keeps the KB self-contained and offline-verifiable.

## Publishing

Generate interactive single-file HTML reports from notes:

```bash
# Single note
python3 .kb/build-report.py <note-slug>

# Research hub (includes all linked notes)
python3 .kb/build-report.py <research-hub-slug>

# Custom title
python3 .kb/build-report.py research-hub-security-compliance-frameworks --title "SOC 2 & ISO 27001 Guide"
```

Or via Claude Code:
```
/kb-publish research-hub-security-compliance-frameworks
/kb-publish --explain soc2
```

Published reports include:
- Collapsible sections
- Full-text search
- Dark/light mode
- Wikilink navigation (click to scroll)
- Footnotes with source URLs (hover to preview)
- PDF export (browser print)

Output: `publish/<name>.html` — one self-contained file, no dependencies.

## Design Principles

1. **Atomic notes** — one concept per file, linked via wikilinks
2. **Local-first citations** — reference files store source summaries; notes cite locally
3. **Progressive complexity** — start with `/kb-question`, scale to `/kb-research`
4. **Obsidian-compatible** — standard markdown with YAML frontmatter and `[[wikilinks]]`
5. **Publishable** — any note or group of notes can become a shareable report

## Git Strategy

- `notes/` and `references/` are gitignored (content is personal/private)
- Skills, templates, config, and infrastructure are tracked
- The KB structure is reproducible; the content is not
