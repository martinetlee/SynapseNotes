---
name: kb-publish
description: Publish KB notes or synthesized reports as interactive HTML
user_invocable: true
arguments: "Note name, research hub name, or --explain topic"
---

# /kb-publish

Publish KB content as a self-contained interactive HTML report.

## Modes

- `/kb-publish <note-name>` — publish a single note as a clean report
- `/kb-publish <research-hub-name>` — publish the hub + all linked notes as a multi-section report
- `/kb-publish --explain <topic>` — run `/kb-explain` internally, then publish the synthesized narrative

## Steps

### 1. Determine mode and gather content

**Single note mode:**
- Read the specified note from `notes/`
- Read any notes in its `related:` frontmatter for wikilink resolution

**Hub mode:**
- Read the research hub note
- Follow ALL `[[wikilinks]]` in the hub's `related:` frontmatter
- Read every linked note — these become collapsible sections in the report
- Order sections logically (concepts first, then insights, then references)

**Explain mode:**
- Run the kb-explain logic: search notes, read all relevant ones, synthesize a narrative
- The narrative becomes the main content; individual notes become expandable sections below it

### 2. Resolve citations and links

For each note's body content:

a. **Wikilinks** `[[note-name]]` or `[[note-name|display text]]`:
   - If the linked note is included in the report → convert to a clickable anchor: `<a class="wikilink" href="#note-name" data-target="note-name">display text</a>`
   - If not included → render as plain bold text

b. **Reference citations** `[display text](../references/filename.md)`:
   - Read the reference file to extract the `Source:` URL
   - Convert to a numbered footnote: `<span class="footnote-ref" data-fn="N">[N]</span>`
   - Add to footnotes list: `<li id="fn-N"><a href="URL">Display text</a> — Source title</li>`

c. **External URL citations** `[text](https://...)`:
   - Convert to footnote same as above

d. **Strip YAML frontmatter** — don't render it. Extract `title`, `tags`, `type`, `updated` for the section header.

### 3. Convert markdown to HTML

For each note's body (after frontmatter is stripped):
- Convert markdown headings (## → h3, ### → h4 within sections)
- Convert tables, lists, bold, italic, code, blockquotes
- Convert line breaks and paragraphs

Use a simple markdown-to-HTML approach. The template handles styling.

### 4. Assemble the report

Read the HTML template from `.kb/templates/report.html`.

Replace template placeholders:

- `{{TITLE}}` — the report title (note title, hub title, or "Topic: X" for explain mode)
- `{{META}}` — "Published DATE | N notes | N sources" + tags
- `{{TOC}}` — generated table of contents: `<li><a href="#section-id">Section Title</a></li>` for each section
- `{{CONTENT}}` — all sections assembled as:
  ```html
  <div class="section" id="note-slug">
    <div class="section-header">
      <h2>Note Title</h2>
      <span class="toggle">▼</span>
    </div>
    <div class="section-body">
      (converted HTML content)
    </div>
  </div>
  ```
- `{{FOOTNOTES}}` — all collected footnotes as `<li id="fn-N">...</li>`

For single-note mode: don't use collapsible sections — just render the content directly.
For hub/explain mode: each note is a collapsible section; the hub summary or narrative is rendered first (not collapsible).

### 5. Write output

Write the assembled HTML to `publish/<slug>.html` where slug is derived from the title.

Report the file path and size to the user.

## Rules

- Output must be ONE self-contained HTML file — all CSS and JS inline, no external dependencies
- Never modify the template file — read it and inject content
- Preserve all source attribution — every claim should trace to a footnote
- Wikilinks within the report should be navigable (scroll to section)
- The report should look good when printed to PDF via the browser Print button
- Keep section ordering logical: overview/summary first, then concepts, then insights, then references
- If a note has no body content (only frontmatter), skip it

## Template Location

`.kb/templates/report.html`

## Output Location

`publish/`

$ARGUMENTS
