#!/usr/bin/env python3
"""Build an interactive HTML report from KB notes.

Usage:
  python3 .kb/build-report.py <note-or-hub-slug> [--title "Custom Title"]
  python3 .kb/build-report.py research-hub-security-compliance-frameworks
  python3 .kb/build-report.py soc2-compliance-overview --title "SOC 2 Guide"
"""
import os
import re
import sys
import yaml
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent.parent
NOTES = BASE / "notes"
REFS = BASE / "references"
TEMPLATE = BASE / ".kb" / "templates" / "report.html"
PUBLISH = BASE / "publish"


def parse_note(filepath):
    """Parse a note file into frontmatter dict and body string."""
    content = filepath.read_text()
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2].strip()


def get_source_url(ref_path):
    """Extract Source: URL from a reference file."""
    full_path = REFS / ref_path.replace("../references/", "").replace("references/", "")
    if not full_path.exists():
        return None
    text = full_path.read_text()
    # Try "Source: URL" format
    m = re.search(r"^Source:\s*(https?://\S+)", text, re.MULTILINE)
    if m:
        return m.group(1)
    # Try "source: URL" (YAML frontmatter)
    m = re.search(r"^source:\s*(https?://\S+)", text, re.MULTILINE)
    if m:
        return m.group(1)
    return None


def md_to_html(md_text, footnotes, included_slugs):
    """Convert markdown body to HTML with footnote/wikilink resolution."""
    lines = md_text.split("\n")
    html_lines = []
    in_table = False
    in_list = False
    in_ol = False
    table_header_done = False

    def resolve_inline(line):
        """Resolve wikilinks and citations in a line."""
        # Wikilinks: [[slug|text]] or [[slug]]
        def replace_wikilink(m):
            inner = m.group(1)
            if "|" in inner:
                slug, text = inner.split("|", 1)
            else:
                slug = text = inner
            slug = slug.strip()
            text = text.strip()
            if slug in included_slugs:
                return f'<a class="wikilink" href="#{slug}" data-target="{slug}">{text}</a>'
            else:
                return f"<strong>{text}</strong>"
        line = re.sub(r"\[\[([^\]]+)\]\]", replace_wikilink, line)

        # Citations: [text](../references/file.md) → footnote
        def replace_ref_citation(m):
            text = m.group(1)
            ref_path = m.group(2)
            url = get_source_url(ref_path)
            if url:
                fn_num = len(footnotes) + 1
                footnotes.append((fn_num, text, url))
                return f'{text}<span class="footnote-ref" data-fn="{fn_num}">[{fn_num}]</span>'
            return text
        line = re.sub(r"\[([^\]]+)\]\((\.\.\/references\/[^)]+|references\/[^)]+)\)", replace_ref_citation, line)

        # External URLs: [text](https://...) → footnote
        def replace_ext_citation(m):
            text = m.group(1)
            url = m.group(2)
            fn_num = len(footnotes) + 1
            footnotes.append((fn_num, text, url))
            return f'{text}<span class="footnote-ref" data-fn="{fn_num}">[{fn_num}]</span>'
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", replace_ext_citation, line)

        # Bold
        line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", line)
        # Italic
        line = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", line)
        # Inline code
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)

        return line

    i = 0
    while i < len(lines):
        line = lines[i]

        # Headings (## → h3, ### → h4)
        if line.startswith("### "):
            if in_list: html_lines.append("</ul>"); in_list = False
            if in_ol: html_lines.append("</ol>"); in_ol = False
            html_lines.append(f"<h4>{resolve_inline(line[4:])}</h4>")
        elif line.startswith("## "):
            if in_list: html_lines.append("</ul>"); in_list = False
            if in_ol: html_lines.append("</ol>"); in_ol = False
            html_lines.append(f"<h3>{resolve_inline(line[3:])}</h3>")

        # Table
        elif "|" in line and line.strip().startswith("|"):
            if not in_table:
                html_lines.append("<table>")
                in_table = True
                table_header_done = False
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Skip separator row
            if all(re.match(r"^[-:]+$", c) for c in cells):
                html_lines.append("</thead><tbody>")
                table_header_done = True
            elif not table_header_done:
                html_lines.append("<thead><tr>" + "".join(f"<th>{resolve_inline(c)}</th>" for c in cells) + "</tr>")
            else:
                html_lines.append("<tr>" + "".join(f"<td>{resolve_inline(c)}</td>" for c in cells) + "</tr>")

        # End table if next line isn't table
        elif in_table:
            html_lines.append("</tbody></table>")
            in_table = False
            table_header_done = False
            # Re-process this line
            i -= 1; i += 1; continue

        # Unordered list
        elif line.strip().startswith("- "):
            if in_ol: html_lines.append("</ol>"); in_ol = False
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{resolve_inline(line.strip()[2:])}</li>")

        # Ordered list
        elif re.match(r"^\d+\.\s", line.strip()):
            if in_list: html_lines.append("</ul>"); in_list = False
            if not in_ol:
                html_lines.append("<ol>")
                in_ol = True
            text = re.sub(r"^\d+\.\s", "", line.strip())
            html_lines.append(f"<li>{resolve_inline(text)}</li>")

        # Blockquote
        elif line.strip().startswith("> "):
            if in_list: html_lines.append("</ul>"); in_list = False
            if in_ol: html_lines.append("</ol>"); in_ol = False
            html_lines.append(f"<blockquote>{resolve_inline(line.strip()[2:])}</blockquote>")

        # Empty line
        elif line.strip() == "":
            if in_list: html_lines.append("</ul>"); in_list = False
            if in_ol: html_lines.append("</ol>"); in_ol = False

        # Paragraph
        else:
            if in_list: html_lines.append("</ul>"); in_list = False
            if in_ol: html_lines.append("</ol>"); in_ol = False
            html_lines.append(f"<p>{resolve_inline(line)}</p>")

        i += 1

    if in_table: html_lines.append("</tbody></table>")
    if in_list: html_lines.append("</ul>")
    if in_ol: html_lines.append("</ol>")

    return "\n".join(html_lines)


def build_report(slug, custom_title=None):
    """Build an HTML report from a note or hub."""
    note_path = NOTES / f"{slug}.md"
    if not note_path.exists():
        print(f"Error: {note_path} not found")
        sys.exit(1)

    fm, body = parse_note(note_path)
    title = custom_title or fm.get("title", slug.replace("-", " ").title())

    # Determine if this is a hub (has many related notes)
    related = fm.get("related", [])
    related_slugs = []
    for r in related:
        # Extract slug from "[[slug]]" format
        m = re.search(r"\[\[([^\]|]+)", str(r))
        if m:
            related_slugs.append(m.group(1))

    # Collect all notes to include
    notes_to_include = []
    included_slugs = set()

    if len(related_slugs) > 3:  # Hub mode
        # Hub summary is the intro (not collapsible)
        notes_to_include.append(("__intro__", fm, body))
        included_slugs.add(slug)
        # Related notes become sections
        for rs in related_slugs:
            rpath = NOTES / f"{rs}.md"
            if rpath.exists():
                rfm, rbody = parse_note(rpath)
                notes_to_include.append((rs, rfm, rbody))
                included_slugs.add(rs)
    else:
        # Single note mode
        notes_to_include.append((slug, fm, body))
        included_slugs.add(slug)

    # Build HTML
    footnotes = []
    toc_items = []
    content_sections = []

    for note_slug, note_fm, note_body in notes_to_include:
        note_title = note_fm.get("title", note_slug.replace("-", " ").title())
        html_body = md_to_html(note_body, footnotes, included_slugs)

        if note_slug == "__intro__":
            # Intro section (not collapsible)
            content_sections.append(f'<div class="section-body">{html_body}</div>')
        else:
            toc_items.append(f'<li><a href="#{note_slug}">{note_title}</a></li>')
            content_sections.append(f'''<div class="section" id="{note_slug}">
  <div class="section-header">
    <h2>{note_title}</h2>
    <span class="toggle">\u25BC</span>
  </div>
  <div class="section-body">{html_body}</div>
</div>''')

    # Build footnotes HTML
    fn_html = ""
    for num, text, url in footnotes:
        fn_html += f'<li id="fn-{num}"><a href="{url}" target="_blank">{text}</a></li>\n'

    # Assemble template
    template = TEMPLATE.read_text()
    meta = f"Published {date.today().isoformat()} | {len(notes_to_include)} notes | {len(footnotes)} sources"

    html = template.replace("{{TITLE}}", title)
    html = html.replace("{{META}}", meta)
    html = html.replace("{{TOC}}", "\n".join(toc_items))
    html = html.replace("{{CONTENT}}", "\n".join(content_sections))
    html = html.replace("{{FOOTNOTES}}", fn_html)

    # Write output
    PUBLISH.mkdir(exist_ok=True)
    out_slug = slug if not custom_title else custom_title.lower().replace(" ", "-")
    out_path = PUBLISH / f"{out_slug}.html"
    out_path.write_text(html)
    print(f"Published: {out_path} ({len(html)//1024}KB)")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 .kb/build-report.py <note-slug> [--title 'Title']")
        sys.exit(1)

    slug = sys.argv[1]
    custom_title = None
    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        if idx + 1 < len(sys.argv):
            custom_title = sys.argv[idx + 1]

    build_report(slug, custom_title)
