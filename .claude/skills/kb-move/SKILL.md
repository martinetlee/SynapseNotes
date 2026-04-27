---
name: kb-move
description: Move notes between knowledge bases, updating all links and references
user_invocable: true
arguments: "Note slug(s) to move, and target KB. E.g.: 'some-note-slug --to my-domain' or 'topic-* --to my-domain'"
---

# /kb-move

Move one or more notes from one KB to another, updating all cross-references.

## Steps

1. **Parse arguments** — Extract from $ARGUMENTS:
   - **Slugs**: one or more note slugs, or a glob pattern (e.g., `topic-*`)
   - **--to**: target KB name (required)
   - **--from**: source KB name (optional — if omitted, search all KBs for each slug)

2. **Resolve notes** — For each slug/pattern:
   - Find which KB currently owns the note
   - If glob pattern, expand it against the source KB's directory
   - Verify all notes exist
   - Verify target KB exists in `kbs.yaml`
   - If target KB directory doesn't exist, create it with `mkdir -p`

3. **Show migration plan** — Present what will happen:
   ```
   Moving 5 notes from general → my-domain:

   1. note-slug-one
   2. note-slug-two
   3. note-slug-three
   4. note-slug-four
   5. note-slug-five

   This will:
   - Move files from kbs/general/ to kbs/my-domain/
   - Update wikilinks in OTHER notes that reference these (add cross-KB prefix if needed)
   - No reference path changes needed (both KBs are at same depth under kbs/)

   Proceed? (yes / no / adjust)
   ```

4. **Move files** — For each note:
   ```bash
   mv kbs/<source>/<slug>.md kbs/<target>/<slug>.md
   ```

5. **Update cross-references** — For each moved note:

   a. **Notes in the SOURCE KB** that link to the moved note: their `[[slug]]` wikilinks still resolve (same-KB resolution fails, falls through to cross-KB), but for clarity, update them to `[[target:slug]]`.

   b. **Notes in OTHER KBs** that link to the moved note: update `[[slug]]` → `[[target:slug]]` if they were relying on cross-KB resolution to the old KB.

   c. **Notes in the TARGET KB** that link to the moved note: their `[[slug]]` now resolves directly (same-KB). Remove any `[[source:slug]]` prefix if present.

   d. **The moved note itself**: update any `[[source:other-note]]` prefixed links that pointed to the source KB — these can now be plain `[[other-note]]` if the target is in the same KB.

6. **Reference paths** — Since all KBs are at `kbs/<name>/`, the relative path to `references/` is `../../references/` regardless. No changes needed.

7. **Rebuild index** — Run:
   ```bash
   python3 .kb/kb-index.py build --kb <source>
   python3 .kb/kb-index.py build --kb <target>
   ```
   This also rebuilds the unified index.

8. **Summary** — Show:
   - Files moved (count and paths)
   - Wikilinks updated (count and which notes were edited)
   - Index rebuilt

## Rules

- Never move a note without showing the plan first
- Never overwrite an existing note in the target KB (if same slug exists, stop and ask)
- When updating wikilinks in other notes, only edit the `related:` frontmatter and body `[[wikilinks]]` — don't touch other content
- If moving ALL notes from a KB, warn that the source KB will be empty

$ARGUMENTS
