---
name: kb-explain
description: Synthesize a coherent narrative explanation of a topic from multiple KB notes
user_invocable: true
arguments: The topic to explain
---

# /kb-explain

Synthesize everything the KB knows about a topic into a coherent, structured narrative.

## Steps

1. **Find relevant notes** — Search `notes/` broadly for the topic:
   - Filename and tag matches
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

4. **Highlight gaps** — After the narrative, add a section:

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
