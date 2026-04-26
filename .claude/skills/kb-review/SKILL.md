---
name: kb-review
description: Audit the knowledge base for quality issues and suggest improvements
user_invocable: true
arguments: "Optional focus area, e.g. tags, links, duplicates"
---

# /kb-review

Audit the knowledge base for quality issues and suggest improvements.

## Steps

1. **Read everything** — Read all notes in `notes/` and `.kb/taxonomy.yaml`. Build a mental map of the full KB: all titles, tags, wikilinks, sources, and update dates.

2. **Run checks** — Perform all of the following (or only the focused area if $ARGUMENTS specifies one):

   ### Lint
   - Run `python3 .kb/kb-index.py lint` to validate all notes for frontmatter errors, broken wikilinks, broken citations, unknown tags, and malformed dates.
   - Present errors (must-fix) and warnings (should-fix) separately.

   ### Link Graph
   - Run `python3 .kb/kb-index.py graph orphans` to find disconnected notes.
   - Run `python3 .kb/kb-index.py graph components` to identify disconnected subgraphs that should be linked.
   - Run `python3 .kb/kb-index.py graph bridges` to identify critical connector notes.

   ### Synthesis Staleness
   - Run `python3 .kb/kb-index.py stale-syntheses` to find synthesis notes whose dependencies have changed since the synthesis was generated. Suggest regenerating via `/kb-explain`.

   ### Tag Sprawl
   - Find similar/redundant tags (e.g. `js` and `javascript`, `ml` and `machine-learning`)
   - Find tags used only once (may be too specific)
   - Suggest merges or renames
   - Flag tags in notes that aren't in taxonomy.yaml (and vice versa — tags in taxonomy.yaml not used by any note)

   ### Orphan Notes
   - Notes with no incoming or outgoing `[[wikilinks]]`
   - These are disconnected from the knowledge graph — suggest links

   ### Missing Links
   - Notes that reference similar concepts but aren't linked to each other
   - Look for shared tags, overlapping content, or mentions of the same terms
   - Suggest specific `[[wikilinks]]` to add in both directions

   ### Duplicates / Overlaps
   - Notes covering the same or heavily overlapping concepts
   - Suggest which to merge and which to keep as separate-but-linked

   ### Stale Notes
   - Notes with `updated` date older than 6 months
   - Flag for review — content may be outdated

   ### Broken Links
   - `[[wikilinks]]` pointing to notes that don't exist in `notes/`
   - Suggest creating the missing note or removing the link

   ### Citation Gaps
   - Notes with `sources` in frontmatter but no inline citations in the body
   - Notes with inline citations but empty `sources` frontmatter
   - Notes of type `reference` with no sources at all

   ### Temporal Staleness
   - Notes with `valid_until` date in the past (expired knowledge)
   - Notes with `deprecated_by` set (superseded)
   - Run `python3 .kb/kb-index.py stale` to find candidates

   ### Note Size
   - Target range: **400-700 words** per note (empirically validated sweet spot for RAG retrieval)
   - Flag notes **under 300 words** — may be too thin for useful retrieval; suggest merging with a related note or expanding
   - Flag notes **over 900 words** — may cover multiple concepts; suggest splitting into separate atomic notes
   - Check via word count of body text (after frontmatter)

   ### Retrieval Quality
   - Run `python3 .kb/kb-index.py stats` for index health
   - Test 5-10 representative queries via `python3 .kb/kb-index.py search "query"` and check if top results are relevant
   - Run `python3 .kb/kb-index.py coverage "TOPIC"` for each major KB topic area to identify coverage gaps
   - Flag notes that never appear in any search result (potential tagging/content issues)
   - Check for high-similarity note pairs via `python3 .kb/kb-index.py similar SLUG` that might be duplicates or contradictions

3. **Present report** — Show findings grouped by check type. For each issue:
   - What the problem is
   - Which notes are affected
   - Suggested fix (specific and actionable)

   Format as a numbered list so the user can refer to specific items.

4. **Ask what to fix** — Ask the user which issues to resolve. Options:
   - "all" — fix everything suggested
   - Specific numbers — "fix 2, 5, 7"
   - "none" — just wanted the report

5. **Apply fixes** — For approved fixes:
   - Edit notes (add links, update tags, merge content)
   - Update taxonomy.yaml
   - Show a summary of all changes made

## Rules

- Never delete notes without explicit user approval
- When merging notes, preserve all unique content from both
- When suggesting links, be specific: "Add `[[tcp-congestion-control]]` to the Related section of `[[flow-control]]`"
- If the KB is small (< 5 notes), keep the review brief — most checks aren't meaningful yet

$ARGUMENTS
