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
import markdown
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent.parent
NOTES = BASE / "notes"
REFS = BASE / "references"
TEMPLATE = BASE / ".kb" / "templates" / "report.html"
PUBLISH = BASE / "publish"
CONFIG_FILE = BASE / ".kb" / "config.yaml"


def load_hub_threshold():
    """Read graph.hub_threshold from config, default 3."""
    if CONFIG_FILE.exists():
        try:
            cfg = yaml.safe_load(CONFIG_FILE.read_text()) or {}
            return cfg.get("graph", {}).get("hub_threshold", 3)
        except yaml.YAMLError:
            pass
    return 3


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
    m = re.search(r"^Source:\s*(https?://\S+)", text, re.MULTILINE)
    if m:
        return m.group(1)
    m = re.search(r"^source:\s*(https?://\S+)", text, re.MULTILINE)
    if m:
        return m.group(1)
    return None


def resolve_wikilinks_and_citations(html, footnotes, included_slugs):
    """Post-process HTML to resolve wikilinks and citations into anchors/footnotes."""

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
    html = re.sub(r"\[\[([^\]]+)\]\]", replace_wikilink, html)

    # Reference citations: [text](../references/file.md) → footnote
    def replace_ref_citation(m):
        text = m.group(1)
        ref_path = m.group(2)
        url = get_source_url(ref_path)
        if url:
            fn_num = len(footnotes) + 1
            footnotes.append((fn_num, text, url))
            return f'{text}<span class="footnote-ref" data-fn="{fn_num}">[{fn_num}]</span>'
        return text
    html = re.sub(r'<a href="(\.\.\/references\/[^"]+|references\/[^"]+)">([^<]+)</a>',
                  lambda m: replace_ref_citation(type('M', (), {'group': lambda self, n: [None, m.group(2), m.group(1)][n]})()),
                  html)
    # Also catch any raw markdown-style citations that python-markdown didn't convert
    html = re.sub(r"\[([^\]]+)\]\((\.\.\/references\/[^)]+|references\/[^)]+)\)",
                  replace_ref_citation, html)

    # External URL links → footnotes
    def replace_ext_link(m):
        url = m.group(1)
        text = m.group(2)
        fn_num = len(footnotes) + 1
        footnotes.append((fn_num, text, url))
        return f'{text}<span class="footnote-ref" data-fn="{fn_num}">[{fn_num}]</span>'
    html = re.sub(r'<a href="(https?://[^"]+)">([^<]+)</a>', replace_ext_link, html)

    return html


def md_to_html(md_text, footnotes, included_slugs):
    """Convert markdown body to HTML using python-markdown, then resolve wikilinks/citations."""
    # python-markdown handles headings, tables, lists, code, blockquotes, bold, italic, etc.
    md = markdown.Markdown(extensions=[
        'tables',
        'fenced_code',
        'codehilite',
        'sane_lists',
    ], extension_configs={
        'codehilite': {'css_class': 'highlight', 'guess_lang': False},
    })

    # Shift headings: ## → h3, ### → h4 (so they nest under the section h2)
    shifted = []
    for line in md_text.split("\n"):
        if line.startswith("#### "):
            shifted.append("#####" + line[4:])
        elif line.startswith("### "):
            shifted.append("####" + line[3:])
        elif line.startswith("## "):
            shifted.append("###" + line[2:])
        elif line.startswith("# "):
            shifted.append("##" + line[1:])
        else:
            shifted.append(line)

    html = md.convert("\n".join(shifted))
    html = resolve_wikilinks_and_citations(html, footnotes, included_slugs)
    return html


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
        m = re.search(r"\[\[([^\]|]+)", str(r))
        if m:
            related_slugs.append(m.group(1))

    hub_threshold = load_hub_threshold()
    notes_to_include = []
    included_slugs = set()

    if len(related_slugs) > hub_threshold:  # Hub mode
        notes_to_include.append(("__intro__", fm, body))
        included_slugs.add(slug)
        for rs in related_slugs:
            rpath = NOTES / f"{rs}.md"
            if rpath.exists():
                rfm, rbody = parse_note(rpath)
                notes_to_include.append((rs, rfm, rbody))
                included_slugs.add(rs)
    else:
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
    seen_urls = {}
    for num, text, url in footnotes:
        if url in seen_urls:
            # Deduplicate: point to the first footnote with this URL
            continue
        seen_urls[url] = num
        fn_html += f'<li id="fn-{num}"><a href="{url}" target="_blank">{text}</a></li>\n'

    # Assemble template
    template = TEMPLATE.read_text()
    meta = f"Published {date.today().isoformat()} | {len(notes_to_include)} notes | {len(seen_urls)} sources"

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
