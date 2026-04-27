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
REFS = BASE / "references"
TEMPLATE = BASE / ".kb" / "templates" / "report.html"
PUBLISH = BASE / "publish"
CONFIG_FILE = BASE / ".kb" / "config.yaml"
KBS_REGISTRY = BASE / "kbs.yaml"


def _load_kb_dirs():
    """Load KB directories from kbs.yaml registry. Falls back to notes/ for legacy layout."""
    if KBS_REGISTRY.exists():
        try:
            registry = yaml.safe_load(KBS_REGISTRY.read_text()) or {}
            dirs = []
            for name, info in registry.get("kbs", {}).items():
                kb_path = BASE / info.get("path", f"kbs/{name}")
                if kb_path.is_dir():
                    dirs.append(kb_path)
            if dirs:
                return dirs
        except yaml.YAMLError:
            pass
    # Legacy fallback
    legacy = BASE / "notes"
    if legacy.is_dir():
        return [legacy]
    return []


def find_note(slug):
    """Find a note file across all KBs. Returns the Path or None.

    Handles cross-KB syntax 'kb:slug' by searching the named KB first.
    If no prefix, searches all KBs (first match wins — prints warning if ambiguous).
    """
    target_kb = None
    bare_slug = slug
    if ":" in slug and not slug.startswith("http"):
        target_kb, bare_slug = slug.split(":", 1)

    kb_dirs = _load_kb_dirs()

    if target_kb:
        # Explicit KB: search only that KB
        for kb_dir in kb_dirs:
            if kb_dir.name == target_kb:
                candidate = kb_dir / f"{bare_slug}.md"
                if candidate.exists():
                    return candidate
        # Fall through to search all if named KB not found

    # Search all KBs, track matches for ambiguity detection
    matches = []
    for kb_dir in kb_dirs:
        candidate = kb_dir / f"{bare_slug}.md"
        if candidate.exists():
            matches.append(candidate)

    if len(matches) > 1:
        print(f"Warning: slug '{bare_slug}' found in {len(matches)} KBs: {[m.parent.name for m in matches]}. Using first match.", file=sys.stderr)

    return matches[0] if matches else None


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
    # Strip any relative prefix depth (../../references/, ../references/, references/)
    cleaned = re.sub(r"^(\.\./)*references/", "", ref_path)
    full_path = REFS / cleaned
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


def resolve_wikilinks_and_citations(html, footnotes, slug_to_id):
    """Post-process HTML to resolve wikilinks and citations into anchors/footnotes.

    slug_to_id maps bare slugs and kb:slug forms to qualified HTML IDs.
    """

    # Wikilinks: [[kb:slug|text]], [[slug|text]], [[kb:slug]], or [[slug]]
    def replace_wikilink(m):
        inner = m.group(1)
        if "|" in inner:
            raw_slug, text = inner.split("|", 1)
        else:
            raw_slug = text = inner
        raw_slug = raw_slug.strip()
        text = text.strip()
        # Try exact match first (including kb:slug), then bare slug
        qid = slug_to_id.get(raw_slug)
        if not qid:
            bare = raw_slug.split(":", 1)[1] if ":" in raw_slug and not raw_slug.startswith("http") else raw_slug
            qid = slug_to_id.get(bare)
        if qid:
            return f'<a class="wikilink" href="#{qid}" data-target="{qid}">{text}</a>'
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
    html = re.sub(r'<a href="((?:\.\.\/)+references\/[^"]+|references\/[^"]+)">([^<]+)</a>',
                  lambda m: replace_ref_citation(type('M', (), {'group': lambda self, n: [None, m.group(2), m.group(1)][n]})()),
                  html)
    # Also catch any raw markdown-style citations that python-markdown didn't convert
    html = re.sub(r"\[([^\]]+)\]\(((?:\.\.\/)+references\/[^)]+|references\/[^)]+)\)",
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


def md_to_html(md_text, footnotes, slug_to_id):
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
    html = resolve_wikilinks_and_citations(html, footnotes, slug_to_id)
    return html


def build_report(slug, custom_title=None):
    """Build an HTML report from a note or hub. Searches all KBs."""
    note_path = find_note(slug)
    if not note_path:
        print(f"Error: note '{slug}' not found in any KB")
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
    included_slugs = set()   # qualified IDs used in HTML anchors
    slug_to_id = {}          # maps bare slug → qualified ID for wikilink resolution

    def _qualify_slug(bare_slug, note_path):
        """Create a unique ID from slug + KB name to avoid collisions."""
        kb_name = note_path.parent.name if note_path else "unknown"
        return f"{kb_name}--{bare_slug}"

    def _register_note(bare_slug, note_path):
        """Register a note's qualified ID and bare slug mapping."""
        qid = _qualify_slug(bare_slug, note_path)
        included_slugs.add(qid)
        # Also register bare slug for wikilink resolution (first match wins)
        if bare_slug not in slug_to_id:
            slug_to_id[bare_slug] = qid
        # Register kb:slug form too
        if note_path:
            kb_slug = f"{note_path.parent.name}:{bare_slug}"
            slug_to_id[kb_slug] = qid
        return qid

    if len(related_slugs) > hub_threshold:  # Hub mode
        intro_qid = _qualify_slug(slug, note_path)
        notes_to_include.append(("__intro__", intro_qid, fm, body))
        _register_note(slug, note_path)
        for rs in related_slugs:
            bare = rs.split(":", 1)[1] if ":" in rs and not rs.startswith("http") else rs
            rpath = find_note(rs)  # pass original (possibly kb-qualified) to find_note
            if rpath:
                rfm, rbody = parse_note(rpath)
                qid = _register_note(bare, rpath)
                notes_to_include.append((bare, qid, rfm, rbody))
    else:
        qid = _register_note(slug, note_path)
        notes_to_include.append((slug, qid, fm, body))

    # Build HTML — pass slug_to_id for wikilink resolution
    footnotes = []
    toc_items = []
    content_sections = []

    for entry in notes_to_include:
        note_slug, qid, note_fm, note_body = entry
        note_title = note_fm.get("title", note_slug.replace("-", " ").title())
        html_body = md_to_html(note_body, footnotes, slug_to_id)

        if note_slug == "__intro__":
            content_sections.append(f'<div class="section-body">{html_body}</div>')
        else:
            toc_items.append(f'<li><a href="#{qid}">{note_title}</a></li>')
            content_sections.append(f'''<div class="section" id="{qid}">
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
