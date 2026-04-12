---
name: kb-note
description: Extract topics and Q&A from the current conversation and save selected ones as notes
user_invocable: true
arguments: Optional filter or hint about what to extract (e.g. "the discussion about TCP")
---

# /kb-note

You are helping the user capture knowledge from the current conversation into their knowledge base.

## Steps

1. **Scan the conversation** — Review the full conversation history. Identify distinct topics, Q&A pairs, concepts explained, insights shared, or decisions made that would be valuable as knowledge base notes.

2. **Present candidates** — Show the user a numbered list of potential notes to save:

   ```
   Found these topics worth noting:
   
   1. [question] How TCP congestion control works — from your question about networking
   2. [concept] Difference between flow control and congestion control — explained during discussion
   3. [insight] Our project's retry logic may need backoff — observation from debugging session
   ```

   For each candidate, show: type, proposed title, and where in the conversation it came from.

3. **Wait for user selection** — Ask which ones to save (e.g. "all", "1,3", "none"). Use AskUserQuestion to prompt them.

4. **For each selected candidate**:

   a. Search existing notes for overlap (Glob + Grep in `notes/`)
   
   b. If a closely related note exists, show the user and ask: update existing or create new?
   
   c. Create/update the note following CLAUDE.md format
   
   d. Add `[[wikilinks]]` to related notes in both directions
   
   e. Update `.kb/taxonomy.yaml` with any new tags

5. **Summary** — After saving, show:
   - Files created/updated
   - Tags applied
   - Links added

## Note Type Selection

Pick the type based on content:
- **question**: Clear Q&A exchange in the conversation
- **concept**: Explanation of a topic or idea
- **reference**: Information from an external source discussed
- **insight**: Original observation, connection, or conclusion

## Rules

- Only extract genuinely useful knowledge — skip small talk, debugging noise, and trivial exchanges
- Prefer atomic notes: if a conversation covered 3 distinct concepts, suggest 3 separate notes
- Respect the user's selection — don't save anything they didn't pick
- If $ARGUMENTS contains a filter, focus extraction on that topic

$ARGUMENTS
