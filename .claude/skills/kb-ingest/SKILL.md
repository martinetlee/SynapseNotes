---
name: kb-ingest
description: Extract atomic notes from files in references/ or from URLs (fetched content saved to references/)
user_invocable: true
arguments: "Filename in references/, glob pattern, or URL to fetch. Optional: --kb <name> to target a specific KB (default: general)"
---

# /kb-ingest

Ingest source material and extract atomic knowledge base notes.

**KB selection**: Parse $ARGUMENTS for `--kb <name>` flag. If not specified, infer the best KB:
1. After reading the source, assess its topic. Search each non-private KB for related existing notes.
2. If a domain KB has related content or its `kbs.yaml` description fits the source's topic, propose that KB.
3. If no clear match, default to `general`.
4. State the chosen KB when presenting candidates — the user can override.

## Steps

1. **Locate the source** — Based on $ARGUMENTS:

   **URL** (starts with `http://` or `https://`):
   - Use WebFetch to retrieve the content
   - Save the fetched content as a markdown file in `references/` with a slugified name derived from the page title or URL (e.g. `references/how-tcp-works.md`)
   - Tell the user the file was saved
   - Continue with ingestion from that file

   **Filename** (e.g. `tcp-guide.pdf`):
   - Read `references/<filename>`

   **Glob pattern** (e.g. `*.md`):
   - List matches in `references/` and let the user pick

   **No argument**:
   - List all files in `references/` and let the user pick

   If the file doesn't exist or the URL can't be fetched, tell the user and stop.

2. **Read the source** — Use Read to read the file (supports markdown, text, PDF, etc.)

3. **Analyze content** — Read through the full content and identify:
   - Distinct concepts worth their own atomic note
   - Key claims, frameworks, or mental models
   - Definitions or explanations
   - Notable insights or non-obvious points

4. **Present candidates** — Show the user a numbered list:

   ```
   Source: "How TCP Works" (references/tcp-guide.pdf)

   Reference note (hub):
   0. [reference] How TCP Works — summary of the source with links to all extracted notes

   Concept notes:
   1. [concept] TCP Three-Way Handshake — SYN/SYN-ACK/ACK connection setup
   2. [concept] TCP Congestion Control — slow start, congestion avoidance, fast recovery
   3. [insight] Why TCP backoff is exponential — design tradeoff explained in the article
   ```

   The reference hub note (item 0) is always included. User picks which concept notes to create.

   **Type balance**: When classifying candidates, actively consider whether each is better as a `question` (if the source answers a specific "how/why" question), `insight` (if it reveals a non-obvious connection or tradeoff), or `concept` (if it's a pure explanation). Don't default everything to `concept`.

5. **Wait for user selection** — Ask which to save (e.g. "all", "1,3", "none"). Use AskUserQuestion.

6. **Create the reference hub note** — Always created. This is a `reference`-type note that:
   - Summarizes the source as a whole
   - Lists the source file path in `sources` frontmatter (e.g. `references/tcp-guide.pdf`)
   - Links to all extracted concept notes via `[[wikilinks]]`
   - Serves as the "entry point" for everything learned from this source

7. **Create selected concept notes** — For each selected candidate:

   a. Search existing notes for overlap (Glob + Grep in `kbs/<kb_name>/` and other KBs)
   b. If a closely related note exists, show the user and ask: update existing or create new?
   c. Create/update the note in `kbs/<kb_name>/` following CLAUDE.md format
   d. **Cite the source inline** — every claim must have a citation: `[text](../../references/filename.md)` (two levels up from `kbs/<kb_name>/`) or `(Source: Title, p.123)`
   e. Include the source file path in `sources` frontmatter
   f. Add `[[wikilinks]]` to the hub note and any other related notes, in both directions

8. **Update taxonomy** — Read `.kb/taxonomy.yaml`, add any new tags, write back.

9. **Summary** — Show:
   - Hub note created
   - Concept notes created/updated
   - Tags applied
   - Links added

## Rules

- **Never edit or delete existing files in `references/`** — only read, or write new files (from URL fetch)
- **One concept per note** — don't create a single mega-note from a long document. Split into atoms.
- **Always cite the source** — every note extracted must trace back to the original with inline citations
- **Hub note links everything** — the reference note is the connective tissue between the source and all extracted concepts
- **Respect user selection** — only create the notes the user picked (except the hub, which is always created)
- **Check for existing notes** — don't duplicate concepts already in the KB; update and link instead
- If the source is very short or only covers one concept, skip the hub and just create a single note

## Source

$ARGUMENTS
