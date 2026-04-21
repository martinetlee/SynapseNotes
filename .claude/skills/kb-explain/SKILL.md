---
name: kb-explain
description: Synthesize a coherent narrative explanation of a topic from multiple KB notes
user_invocable: true
arguments: The topic to explain
---

# /kb-explain

Synthesize everything the KB knows about a topic into a coherent, structured narrative.

## Steps

1. **Find relevant notes** — Use the search index + manual search:
   - Run `python3 .kb/kb-index.py search "TOPIC"` for semantic ranking
   - Run `python3 .kb/kb-index.py coverage "TOPIC"` to assess coverage level
   - Also check: filename and tag matches
   - Body content matches
   - Notes linked from direct matches (follow the wikilink graph)
   - Cast a wide net — include tangentially related notes that add context

2. **Read all relevant notes** — Read full content of every matched note.

3. **Synthesize a narrative** — Produce a structured explanation that:
   - Weaves multiple notes into a logical flow (not just concatenation)
   - Starts with the big picture, then drills into specifics
   - Uses transitions that show how concepts connect
   - Cites `[[note-name]]` inline for every claim sourced from a note
   - Preserves inline citations from the original notes (URLs, file paths, etc.)

4. **Context sufficiency & claim verification** — Before presenting:
   - Check: does the KB actually have enough coverage to explain this topic? If coverage is "not-covered" or "partially-covered", say so explicitly at the top.
   - For each factual claim in the narrative, verify it traces to a specific `[[note]]`. If a claim can't be sourced to a note, either remove it or mark it as "Claude's general knowledge, not from KB".
   - If notes contradict each other, present both perspectives with citations.
   - **Never silently fill gaps** — this is the #1 hallucination vector.

5. **Highlight gaps** — After the narrative, add a section:

   ```
   ## Gaps in KB Coverage
   - {topic area not covered} — Claude knows about X, want to `/kb-question` it?
   - {shallow coverage} — [[note-name]] touches on this but lacks depth
   ```

   Actively suggest `/kb-question <specific question>` for each gap so the user can fill them with one click.

5. **Show sources** — End with a list of all notes used:
   ```
   ## Notes Used
   - [[note-a]] — contributed X
   - [[note-b]] — contributed Y
   ```

## Key Differences from /kb-search

| /kb-search | /kb-explain |
|---|---|
| Answers a specific question | Comprehensive topic overview |
| Concise, targeted | Thorough, narrative |
| Lists matched notes | Weaves notes into a story |
| Stays within KB content | Flags gaps and suggests filling them |

## Rules

- The narrative itself should only contain KB content — clearly separate what the KB says from what's missing
- Don't silently fill gaps with Claude's own knowledge; flag them explicitly
- If the KB has nothing on the topic, say so and suggest `/kb-question` to start building coverage
- Don't create a new note — this is read-only unless the user asks to save the explanation

## Topic

$ARGUMENTS
