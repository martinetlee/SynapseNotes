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
    If no prefix and slug is unambiguous (exists in exactly one KB), returns it.
    If ambiguous (multiple KBs), prints error and returns None — caller must qualify.
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
        # Explicit KB specified but not found — fail, don't fall through
        return None

    # Search all KBs, track matches for ambiguity detection
    matches = []
    for kb_dir in kb_dirs:
        candidate = kb_dir / f"{bare_slug}.md"
        if candidate.exists():
            matches.append(candidate)

    if len(matches) > 1:
        kb_names = [m.parent.name for m in matches]
        print(f"Error: slug '{bare_slug}' found in {len(matches)} KBs: {kb_names}. "
              f"Qualify with kb:slug, e.g. '{kb_names[0]}:{bare_slug}'", file=sys.stderr)
        return None

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


def resolve_wikilinks_and_citations(html, footnotes, resolve_fn, context_kb=None):
    """Post-process HTML to resolve wikilinks and citations into anchors/footnotes.

    resolve_fn(raw_slug, context_kb) returns a qualified HTML ID or None.
    context_kb is the KB name of the note being rendered — used to prefer same-KB matches.
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
        qid = resolve_fn(raw_slug, context_kb)
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


def md_to_html(md_text, footnotes, resolve_fn, context_kb=None):
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
    html = resolve_wikilinks_and_citations(html, footnotes, resolve_fn, context_kb)
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
    # Maps qualified ID → True (for existence checks)
    included_qids = set()
    # Maps (kb_name, bare_slug) → qid — allows context-aware resolution
    slug_by_kb = {}
    # Maps kb:slug → qid (explicit cross-KB links)
    qualified_slug_to_id = {}
    # All bare slugs that appear in the report (for fallback)
    all_bare_slugs = {}  # bare_slug → [qid, ...] (list if ambiguous)

    def _strip_kb_prefix(s):
        """Strip kb: prefix if present, return (bare_slug)."""
        if ":" in s and not s.startswith("http"):
            return s.split(":", 1)[1]
        return s

    def _qualify_slug(bare_slug, note_path):
        """Create a unique ID from slug + KB name to avoid collisions."""
        kb_name = note_path.parent.name if note_path else "unknown"
        return f"{kb_name}--{bare_slug}"

    def _register_note(raw_slug, note_path):
        """Register a note. raw_slug may contain kb: prefix — stripped here."""
        bare_slug = _strip_kb_prefix(raw_slug)
        kb_name = note_path.parent.name if note_path else "unknown"
        qid = _qualify_slug(bare_slug, note_path)
        included_qids.add(qid)
        # Register by (kb, bare) for context-aware resolution
        slug_by_kb[(kb_name, bare_slug)] = qid
        # Register kb:slug for explicit cross-KB links
        qualified_slug_to_id[f"{kb_name}:{bare_slug}"] = qid
        # Track bare slug → qid(s) for ambiguity detection
        if bare_slug not in all_bare_slugs:
            all_bare_slugs[bare_slug] = qid
        elif all_bare_slugs[bare_slug] != qid:
            # Ambiguous: multiple KBs have this slug
            if isinstance(all_bare_slugs[bare_slug], list):
                all_bare_slugs[bare_slug].append(qid)
            else:
                all_bare_slugs[bare_slug] = [all_bare_slugs[bare_slug], qid]
        return bare_slug, qid

    def _resolve_wikilink_slug(raw_slug, context_kb=None):
        """Resolve a wikilink slug to a qualified ID, preferring the context KB.

        Fail-closed: explicit [[kb:slug]] that doesn't match returns None
        immediately — never falls through to a different KB's note.
        """
        is_explicit = ":" in raw_slug and not raw_slug.startswith("http")
        bare = _strip_kb_prefix(raw_slug)

        # 1. Explicit kb:slug — fail closed if not found
        if is_explicit:
            return qualified_slug_to_id.get(raw_slug)

        # 2. Plain [[slug]] — context-aware: prefer the note's own KB
        if context_kb:
            qid = slug_by_kb.get((context_kb, bare))
            if qid:
                return qid

        # 3. Bare slug fallback (only if unambiguous)
        entry = all_bare_slugs.get(bare)
        if entry and not isinstance(entry, list):
            return entry
        return None

    # Strip any kb: prefix from the root slug before registration
    root_bare = _strip_kb_prefix(slug)
    root_kb = note_path.parent.name if note_path else "unknown"

    if len(related_slugs) > hub_threshold:  # Hub mode
        root_bare, intro_qid = _register_note(slug, note_path)
        notes_to_include.append(("__intro__", intro_qid, root_kb, fm, body))
        for rs in related_slugs:
            rpath = find_note(rs)  # pass original (possibly kb-qualified) to find_note
            if rpath:
                rfm, rbody = parse_note(rpath)
                bare, qid = _register_note(rs, rpath)
                rkb = rpath.parent.name
                notes_to_include.append((bare, qid, rkb, rfm, rbody))
    else:
        root_bare, qid = _register_note(slug, note_path)
        notes_to_include.append((root_bare, qid, root_kb, fm, body))

    # Build HTML — pass resolution function for wikilinks
    footnotes = []
    toc_items = []
    content_sections = []

    for entry in notes_to_include:
        note_slug, qid, note_kb, note_fm, note_body = entry
        note_title = note_fm.get("title", note_slug.replace("-", " ").title())
        html_body = md_to_html(note_body, footnotes, _resolve_wikilink_slug, note_kb)

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
