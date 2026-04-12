# Knowledge Base System

A Claude-powered knowledge base using Obsidian-compatible markdown notes.

## Project Structure

```
notes/          — All atomic notes (flat, one concept per file)
references/     — Source material for ingestion (READ-ONLY — never modify)
.kb/config.yaml — KB settings
.kb/taxonomy.yaml — Loose tag registry (descriptive, not prescriptive)
.claude/skills/ — Claude Code skill definitions
```

## Read-Only Directories

**`references/`** — Contains source material (PDFs, articles, documents) for ingestion via `/kb-ingest`. Claude may **write new files** here (e.g. saving fetched URL content), but must **never edit or delete existing files**. Treat existing files as read-only source of truth.

## Note Format

All notes use this format:

```markdown
---
title: Note Title
tags: [tag1, tag2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: question | concept | reference | insight
sources: []
related: []
---

(body in markdown with [[wikilinks]] to related notes)
```

## Note Types

- **question** — Q&A format: Question section + Answer section + Key Takeaways
- **concept** — Explanation of a single concept: Definition + Details + Key Takeaways
- **reference** — Extracted from external sources: Summary + Key Points + Source
- **insight** — Original observations or connections: Observation + Analysis

## Rules for Writing Notes

1. **Atomic**: One concept per note. If a topic has multiple distinct concepts, create multiple notes.
2. **Slugified filenames**: `tcp-congestion-control.md`, lowercase, hyphens for spaces.
3. **Wikilinks**: Use `[[note-name]]` (without `.md`) to link related notes. Add links in both directions.
4. **Tags**: Use existing tags from taxonomy.yaml when applicable. Create new tags freely when needed.
5. **Update vs Create**: When a closely related note exists, update it if the new info deepens the same concept. Create a new note + link if it's a distinct concept.
6. **Key Takeaways**: Always end with bullet-point takeaways for quick scanning.
7. **Sources**: Include URLs, file paths, or other references when information comes from web search, ingested files, or external content. Frontmatter `sources` is a list of all sources (URLs, file paths, book titles, etc.).
8. **Citations**: Always cite sources inline when referencing external information. Use `[text](url)` for web sources, or `[text](file-path)` / `(Source: Book Title, p.123)` for other source types. Inline citations show *which* claim comes from *which* source.

## KB Role

The KB is **supplementary** — Claude uses its own knowledge combined with KB content. When the user asks to answer "from KB only", restrict to KB content.
