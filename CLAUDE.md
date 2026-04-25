# Knowledge Base System

A Claude-powered knowledge base using Obsidian-compatible markdown notes.

## Project Structure

```
notes/           — All atomic notes (flat, one concept per file)
references/      — Source material for ingestion (READ-ONLY — never modify)
publish/         — Generated HTML reports for sharing
.kb/config.yaml  — KB settings
.kb/taxonomy.yaml — Loose tag registry (descriptive, not prescriptive)
.kb/index/       — Search index (TF-IDF vectors, metadata cache)
.kb/build-report.py  — HTML report builder
.kb/kb-index.py  — Search index builder and query engine
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
- **synthesis** — Generated narrative weaving multiple notes; rewritable (regenerate from atomic notes anytime)

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

## Backup

Run `.kb/backup.sh` to back up notes and references to `~/.kb-backups/`. Keeps last 10 timestamped backups.

## KB Role

The KB is **supplementary** — Claude uses its own knowledge combined with KB content. When the user asks to answer "from KB only", restrict to KB content.
