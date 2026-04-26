#!/usr/bin/env python3
"""Build an interactive HTML dashboard for the knowledge base.

Usage:
  python3 .kb/build-dashboard.py
"""
import json
import re
import sys
import yaml
from pathlib import Path
from datetime import date
from collections import defaultdict

# Import kb-index.py via importlib (hyphen in filename)
import importlib.util

KB_DIR = Path(__file__).parent
BASE = KB_DIR.parent
PUBLISH = BASE / "publish"
LOG_FILE = KB_DIR / "log.md"
KBS_REGISTRY = BASE / "kbs.yaml"

spec = importlib.util.spec_from_file_location("kb_index", KB_DIR / "kb-index.py")
kb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kb)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_topic_map():
    """Load topic clusters via kb-index topic_map()."""
    try:
        return kb.topic_map() or {}
    except Exception as e:
        print(f"Warning: Could not load topic map: {e}", file=sys.stderr)
        return {}


def load_kb_stats():
    """Load per-KB stats by reading metadata files directly."""
    reg = kb.get_registry()
    results = []
    for kbc in reg.all_kbs():
        info = {
            "name": kbc.name,
            "private": kbc.private,
            "note_count": 0,
            "word_count": 0,
            "top_tags": [],
            "types": {},
        }
        if kbc.meta_file.exists():
            try:
                metadata = json.loads(kbc.meta_file.read_text())
                info["note_count"] = len(metadata)
                total_words = 0
                tags = defaultdict(int)
                types = defaultdict(int)
                for slug, meta in metadata.items():
                    total_words += meta.get("word_count", 0)
                    for tag in meta.get("tags", []):
                        tags[tag] += 1
                    types[meta.get("type", "unknown")] += 1
                info["word_count"] = total_words
                info["top_tags"] = sorted(tags.items(), key=lambda x: -x[1])[:5]
                info["types"] = dict(types)
            except (json.JSONDecodeError, KeyError):
                pass
        results.append(info)
    return results


def load_global_types():
    """Aggregate type distribution across all KBs."""
    reg = kb.get_registry()
    types = defaultdict(int)
    for kbc in reg.all_kbs():
        if kbc.meta_file.exists():
            try:
                metadata = json.loads(kbc.meta_file.read_text())
                for meta in metadata.values():
                    types[meta.get("type", "unknown")] += 1
            except (json.JSONDecodeError, KeyError):
                pass
    return dict(types)


def parse_log():
    """Parse .kb/log.md for timeline entries."""
    if not LOG_FILE.exists():
        return []

    text = LOG_FILE.read_text()
    entries = []
    header_re = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]\s+(\w+)\s*\|\s*(.+)$")
    bullet_re = re.compile(r"^- (.+)$")

    current = None
    for line in text.split("\n"):
        m = header_re.match(line.strip())
        if m:
            if current:
                entries.append(current)
            current = {
                "date": m.group(1),
                "type": m.group(2).lower(),
                "title": m.group(3).strip(),
                "first_bullet": "",
            }
            continue
        if current and not current["first_bullet"]:
            bm = bullet_re.match(line.strip())
            if bm:
                current["first_bullet"] = bm.group(1)

    if current:
        entries.append(current)

    return entries


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

TYPE_COLORS = {
    "concept": "#3b82f6",
    "question": "#8b5cf6",
    "insight": "#f59e0b",
    "reference": "#10b981",
    "synthesis": "#ef4444",
    "unknown": "#6b7280",
}

TIMELINE_COLORS = {
    "research": "#3b82f6",
    "infrastructure": "#f97316",
    "ingest": "#10b981",
}


def density_color(density):
    """Map link density 0..1 to red..green via hsl."""
    hue = int(density * 120)  # 0=red, 120=green
    return f"hsl({hue}, 65%, 42%)"


def density_color_dark(density):
    hue = int(density * 120)
    return f"hsl({hue}, 55%, 55%)"


def escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_treemap_html(topic_map):
    """Build treemap HTML from topic_map data."""
    if not topic_map:
        return '<p class="empty">No topic clusters found. Run: python3 .kb/kb-index.py build</p>'

    total = sum(c["count"] for c in topic_map.values())
    if total == 0:
        return '<p class="empty">No notes indexed.</p>'

    items = sorted(topic_map.items(), key=lambda x: -x[1]["count"])
    boxes = []
    for tag, data in items:
        pct = max(data["count"] / total * 100, 3)  # min 3% for visibility
        density = data["link_density"]
        boxes.append(
            f'<div class="tree-box" style="flex-basis:{pct:.1f}%;'
            f'--density-light:{density_color(density)};'
            f'--density-dark:{density_color_dark(density)};"'
            f' title="Density: {density:.2f} | Avg words: {data["avg_words"]}">'
            f'<span class="tree-label">{escape(tag)}</span>'
            f'<span class="tree-count">{data["count"]}</span>'
            f'</div>'
        )

    return f'<div class="treemap">{"".join(boxes)}</div>'


def build_type_bar(types):
    """Build a horizontal stacked bar of note types."""
    total = sum(types.values())
    if total == 0:
        return ""
    segments = []
    for t in ["concept", "question", "insight", "reference", "synthesis"]:
        count = types.get(t, 0)
        if count == 0:
            continue
        pct = count / total * 100
        color = TYPE_COLORS.get(t, "#6b7280")
        segments.append(
            f'<div class="bar-seg" style="width:{pct:.1f}%;background:{color};"'
            f' title="{t}: {count} ({pct:.0f}%)">'
            f'{"" if pct < 6 else t[:3]}'
            f'</div>'
        )
    legend = " ".join(
        f'<span class="legend-item"><span class="legend-dot" style="background:{TYPE_COLORS[t]}"></span>{t} ({types.get(t, 0)})</span>'
        for t in ["concept", "question", "insight", "reference", "synthesis"]
        if types.get(t, 0) > 0
    )
    return f'<div class="type-bar">{"".join(segments)}</div><div class="type-legend">{legend}</div>'


def build_timeline_html(entries):
    """Build a vertical timeline."""
    if not entries:
        return '<p class="empty">No log entries found.</p>'

    items = []
    for e in reversed(entries):  # newest first
        color = TIMELINE_COLORS.get(e["type"], "#6b7280")
        bullet = f'<span class="tl-bullet">{escape(e["first_bullet"])}</span>' if e["first_bullet"] else ""
        items.append(
            f'<div class="tl-item">'
            f'<div class="tl-date">{e["date"]}</div>'
            f'<div class="tl-dot" style="background:{color};"></div>'
            f'<div class="tl-content">'
            f'<span class="tl-type" style="color:{color};">{e["type"]}</span>'
            f'<span class="tl-title">{escape(e["title"])}</span>'
            f'{bullet}'
            f'</div>'
            f'</div>'
        )

    legend = " ".join(
        f'<span class="legend-item"><span class="legend-dot" style="background:{c}"></span>{t}</span>'
        for t, c in TIMELINE_COLORS.items()
    )

    return f'<div class="tl-legend">{legend}</div><div class="timeline">{"".join(items)}</div>'


def build_kb_cards(kb_stats):
    """Build overview cards for each KB."""
    if not kb_stats:
        return '<p class="empty">No KBs found.</p>'

    cards = []
    for info in kb_stats:
        private_badge = '<span class="badge-private">private</span>' if info["private"] else ""
        tags_html = ", ".join(f'{t[0]} ({t[1]})' for t in info["top_tags"]) if info["top_tags"] else "no tags"
        cards.append(
            f'<div class="kb-card">'
            f'<div class="kb-card-header">'
            f'<h3>{escape(info["name"])}</h3>{private_badge}'
            f'</div>'
            f'<div class="kb-card-stats">'
            f'<div class="stat"><span class="stat-num">{info["note_count"]}</span><span class="stat-label">notes</span></div>'
            f'<div class="stat"><span class="stat-num">{info["word_count"]:,}</span><span class="stat-label">words</span></div>'
            f'</div>'
            f'<div class="kb-card-tags">{escape(tags_html)}</div>'
            f'</div>'
        )

    return f'<div class="kb-cards">{"".join(cards)}</div>'


def build_dashboard():
    """Build the full dashboard HTML."""
    topic_data = load_topic_map()
    kb_stats = load_kb_stats()
    global_types = load_global_types()
    log_entries = parse_log()

    total_notes = sum(s["note_count"] for s in kb_stats)
    total_words = sum(s["word_count"] for s in kb_stats)
    total_clusters = len(topic_data)

    treemap_html = build_treemap_html(topic_data)
    type_bar_html = build_type_bar(global_types)
    timeline_html = build_timeline_html(log_entries)
    kb_cards_html = build_kb_cards(kb_stats)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KB Dashboard</title>
<style>
:root {{
  --bg: #ffffff;
  --text: #1a1a2e;
  --muted: #6b7280;
  --border: #e5e7eb;
  --accent: #2563eb;
  --accent-light: #dbeafe;
  --surface: #f9fafb;
  --shadow: 0 1px 3px rgba(0,0,0,0.08);
}}
[data-theme="dark"] {{
  --bg: #0f172a;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --border: #334155;
  --accent: #60a5fa;
  --accent-light: #1e3a5f;
  --surface: #1e293b;
  --shadow: 0 1px 3px rgba(0,0,0,0.3);
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  font-size: 15px;
  padding: 24px;
  max-width: 1100px;
  margin: 0 auto;
}}

/* Header */
.header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 32px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}}
.header h1 {{ font-size: 24px; font-weight: 700; }}
.header-meta {{ color: var(--muted); font-size: 13px; }}
.theme-toggle {{
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}}
.theme-toggle:hover {{ background: var(--border); }}

/* Sections */
.section {{
  margin-bottom: 40px;
}}
.section h2 {{
  font-size: 16px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
  margin-bottom: 16px;
}}

/* Summary bar */
.summary {{
  display: flex;
  gap: 32px;
  margin-bottom: 32px;
}}
.summary .stat {{
  text-align: center;
}}
.summary .stat-num {{
  display: block;
  font-size: 28px;
  font-weight: 700;
  color: var(--accent);
}}
.summary .stat-label {{
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}

/* Treemap */
.treemap {{
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 120px;
}}
.tree-box {{
  background: var(--density-light);
  color: #fff;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-width: 80px;
  min-height: 70px;
  flex-grow: 1;
  cursor: default;
  transition: opacity 0.15s;
}}
[data-theme="dark"] .tree-box {{
  background: var(--density-dark);
}}
.tree-box:hover {{ opacity: 0.85; }}
.tree-label {{
  font-size: 12px;
  font-weight: 600;
  text-align: center;
  word-break: break-word;
}}
.tree-count {{
  font-size: 20px;
  font-weight: 700;
  margin-top: 2px;
}}

/* Type bar */
.type-bar {{
  display: flex;
  height: 28px;
  border-radius: 6px;
  overflow: hidden;
  margin-top: 16px;
}}
.bar-seg {{
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  min-width: 2px;
}}
.type-legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
}}
.legend-item {{
  display: inline-flex;
  align-items: center;
  gap: 4px;
}}
.legend-dot {{
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}}

/* Timeline */
.timeline {{
  position: relative;
  padding-left: 140px;
  border-left: 2px solid var(--border);
  margin-left: 120px;
}}
.tl-item {{
  position: relative;
  padding: 0 0 20px 24px;
}}
.tl-date {{
  position: absolute;
  left: -164px;
  width: 120px;
  text-align: right;
  font-size: 13px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  top: 2px;
}}
.tl-dot {{
  position: absolute;
  left: -30px;
  top: 5px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid var(--bg);
}}
.tl-content {{
  display: flex;
  flex-direction: column;
  gap: 2px;
}}
.tl-type {{
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}}
.tl-title {{
  font-weight: 600;
  font-size: 14px;
}}
.tl-bullet {{
  font-size: 13px;
  color: var(--muted);
}}
.tl-legend {{
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
  font-size: 12px;
  color: var(--muted);
}}

/* KB Cards */
.kb-cards {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}}
.kb-card {{
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  background: var(--surface);
}}
.kb-card-header {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}}
.kb-card-header h3 {{
  font-size: 16px;
  font-weight: 700;
}}
.badge-private {{
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #fbbf24;
  color: #78350f;
  font-weight: 600;
  text-transform: uppercase;
}}
.kb-card-stats {{
  display: flex;
  gap: 24px;
  margin-bottom: 10px;
}}
.kb-card-stats .stat {{
  display: flex;
  flex-direction: column;
}}
.kb-card-stats .stat-num {{
  font-size: 22px;
  font-weight: 700;
  color: var(--accent);
}}
.kb-card-stats .stat-label {{
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
}}
.kb-card-tags {{
  font-size: 12px;
  color: var(--muted);
}}

.empty {{
  color: var(--muted);
  font-style: italic;
  padding: 20px 0;
}}

/* Responsive */
@media (max-width: 700px) {{
  body {{ padding: 12px; }}
  .summary {{ flex-wrap: wrap; gap: 16px; }}
  .timeline {{ padding-left: 20px; margin-left: 0; }}
  .tl-date {{ position: static; width: auto; text-align: left; font-weight: 600; }}
  .tl-dot {{ left: -28px; }}
  .kb-cards {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Knowledge Base Dashboard</h1>
    <span class="header-meta">Generated {date.today().isoformat()} | {total_notes} notes | {total_words:,} words | {total_clusters} clusters</span>
  </div>
  <button class="theme-toggle" onclick="toggleTheme()">Toggle theme</button>
</div>

<div class="summary">
  <div class="stat"><span class="stat-num">{total_notes}</span><span class="stat-label">Notes</span></div>
  <div class="stat"><span class="stat-num">{total_words:,}</span><span class="stat-label">Words</span></div>
  <div class="stat"><span class="stat-num">{total_clusters}</span><span class="stat-label">Clusters</span></div>
  <div class="stat"><span class="stat-num">{len(log_entries)}</span><span class="stat-label">Sessions</span></div>
</div>

<div class="section">
  <h2>Topic Clusters</h2>
  {treemap_html}
  {type_bar_html}
</div>

<div class="section">
  <h2>Knowledge Bases</h2>
  {kb_cards_html}
</div>

<div class="section">
  <h2>Research Timeline</h2>
  {timeline_html}
</div>

<script>
function toggleTheme() {{
  var el = document.documentElement;
  el.setAttribute('data-theme', el.getAttribute('data-theme') === 'dark' ? '' : 'dark');
  try {{ localStorage.setItem('kb-dash-theme', el.getAttribute('data-theme') || 'light'); }} catch(e) {{}}
}}
(function() {{
  try {{
    var saved = localStorage.getItem('kb-dash-theme');
    if (saved === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else if (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches)
      document.documentElement.setAttribute('data-theme', 'dark');
  }} catch(e) {{}}
}})();
</script>
</body>
</html>"""

    PUBLISH.mkdir(exist_ok=True)
    out_path = PUBLISH / "dashboard.html"
    out_path.write_text(html)
    print(f"Published: {out_path} ({len(html) // 1024}KB)")
    return out_path


if __name__ == "__main__":
    build_dashboard()
