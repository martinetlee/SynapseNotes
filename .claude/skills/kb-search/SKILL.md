---
name: kb-search
description: Search the knowledge base and synthesize an answer from matching notes
user_invocable: true
arguments: The search query
---

# /kb-search

Search the knowledge base for notes relevant to the user's query and synthesize an answer grounded in KB content.

## Steps

1. **Search notes** — Use multiple strategies to find relevant notes in `notes/`:

   a. **Filename match**: Glob for filenames containing query keywords
   b. **Tag match**: Grep for query terms in YAML frontmatter `tags` fields
   c. **Body match**: Grep for query terms in note body content
   d. **Wikilink match**: Grep for `[[note-names]]` that relate to the query

   Cast a wide net — partial matches and related terms count.

2. **Read matching notes** — Read the full content of all matched notes.

3. **Synthesize answer** — Present:

   **Synthesized answer** at the top — a coherent response assembled from the matched notes. Cite each claim with `[[note-name]]` so the user knows where it came from.

   **Notes consulted** below — a list of all matched notes with:
   - Filename
   - Title
   - Tags
   - Brief relevance description (why this note matched)

4. **Handle edge cases**:
   - **No matches**: Tell the user nothing was found. Suggest `/kb-question <query>` to research the topic and save it to the KB.
   - **Vague query**: Show broader matches grouped by tag or topic area. Ask the user to narrow down if needed.

## Rules

- Only cite notes that actually exist in `notes/` — never fabricate note references
- Use `[[note-name]]` (without `.md`) for all note citations
- If the KB content is incomplete or outdated, say so — don't silently fill gaps with Claude's own knowledge unless the user asks
- Keep the synthesized answer concise; point to the notes for full detail

## Query

$ARGUMENTS
