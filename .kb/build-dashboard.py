#!/usr/bin/env python3
"""Build an interactive HTML dashboard for the knowledge base.

Usage:
  python3 .kb/build-dashboard.py
"""
import json
import math
import re
import sys
import yaml
from pathlib import Path
from datetime import date, datetime
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


# ---------------------------------------------------------------------------
# Cluster color palette (shared between treemap and link graph)
# ---------------------------------------------------------------------------

CLUSTER_COLORS = [
    "#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6",
    "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16",
    "#06b6d4", "#e11d48", "#a855f7", "#22c55e", "#eab308",
    "#0ea5e9", "#d946ef", "#64748b",
]


def get_cluster_color_map(topic_data):
    """Assign a color to each cluster tag, deterministic order."""
    tags = sorted(topic_data.keys(), key=lambda t: -topic_data[t]["count"])
    return {tag: CLUSTER_COLORS[i % len(CLUSTER_COLORS)] for i, tag in enumerate(tags)}


# ---------------------------------------------------------------------------
# New data loaders
# ---------------------------------------------------------------------------

def load_graph_data():
    """Load unified graph.json adjacency list."""
    graph_file = KB_DIR / "index" / "_unified" / "graph.json"
    if not graph_file.exists():
        graph_file = KB_DIR / "index" / "graph.json"
    if not graph_file.exists():
        return {}
    try:
        return json.loads(graph_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def load_all_metadata():
    """Load unified metadata.json."""
    meta_file = KB_DIR / "index" / "_unified" / "metadata.json"
    if not meta_file.exists():
        meta_file = KB_DIR / "index" / "metadata.json"
    if not meta_file.exists():
        return {}
    try:
        return json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def load_feedback():
    """Load feedback.jsonl if it exists."""
    fb_file = KB_DIR / "index" / "feedback.jsonl"
    if not fb_file.exists():
        return []
    entries = []
    try:
        for line in fb_file.read_text().strip().split("\n"):
            if line.strip():
                entries.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return entries


# ---------------------------------------------------------------------------
# New visualization builders
# ---------------------------------------------------------------------------

def build_link_graph_html(graph_data, metadata, topic_data, cluster_colors):
    """Build D3 force-directed link graph as inline script + SVG container."""
    adj = graph_data.get("adjacency", {})
    if not adj:
        return '<p class="empty">No graph data found. Run: python3 .kb/kb-index.py build</p>'

    # Compute degree for each node
    degree = {}
    for slug, links in adj.items():
        out_count = len(links.get("outgoing", []))
        in_count = len(links.get("incoming", []))
        degree[slug] = out_count + in_count

    # Limit to top 100 most-connected if > 150 nodes
    slugs = list(adj.keys())
    if len(slugs) > 150:
        slugs = sorted(slugs, key=lambda s: -degree.get(s, 0))[:100]
    slug_set = set(slugs)

    # Map each node to its primary cluster
    slug_to_cluster = {}
    for tag, cdata in sorted(topic_data.items(), key=lambda x: -x[1]["count"]):
        for slug in slug_set:
            if slug not in slug_to_cluster:
                meta = metadata.get(slug, {})
                if tag in meta.get("tags", []):
                    slug_to_cluster[slug] = tag

    # Build nodes and edges
    nodes = []
    for slug in slugs:
        meta = metadata.get(slug, {})
        deg = degree.get(slug, 1)
        cluster = slug_to_cluster.get(slug, "_none")
        color = cluster_colors.get(cluster, "#6b7280")
        nodes.append({
            "id": slug,
            "title": meta.get("title", slug),
            "type": meta.get("type", "unknown"),
            "tags": meta.get("tags", []),
            "degree": deg,
            "color": color,
        })

    edges = []
    seen_edges = set()
    for slug in slugs:
        for target in adj.get(slug, {}).get("outgoing", []):
            if target in slug_set:
                edge_key = (slug, target)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({"source": slug, "target": target})

    graph_json = json.dumps({"nodes": nodes, "edges": edges})

    return f"""<div id="link-graph-container" style="width:100%;max-width:800px;height:600px;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--surface);"></div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function() {{
  var data = {graph_json};
  var container = document.getElementById('link-graph-container');
  var width = container.clientWidth || 800;
  var height = 600;

  var svg = d3.select(container).append('svg')
    .attr('width', width).attr('height', height)
    .call(d3.zoom().scaleExtent([0.3, 5]).on('zoom', function(e) {{
      g.attr('transform', e.transform);
    }}));

  var g = svg.append('g');

  var tooltip = d3.select(container).append('div')
    .style('position', 'absolute').style('pointer-events', 'none')
    .style('background', 'rgba(0,0,0,0.85)').style('color', '#fff')
    .style('padding', '6px 10px').style('border-radius', '4px')
    .style('font-size', '12px').style('opacity', 0).style('z-index', 10);

  var sim = d3.forceSimulation(data.nodes)
    .force('link', d3.forceLink(data.edges).id(function(d){{ return d.id; }}).distance(60))
    .force('charge', d3.forceManyBody().strength(-80))
    .force('center', d3.forceCenter(width/2, height/2))
    .force('collide', d3.forceCollide().radius(function(d){{ return Math.sqrt(d.degree)*2+6; }}));

  var link = g.append('g').selectAll('line')
    .data(data.edges).join('line')
    .attr('stroke', 'var(--border)').attr('stroke-width', 0.5).attr('stroke-opacity', 0.4);

  var node = g.append('g').selectAll('circle')
    .data(data.nodes).join('circle')
    .attr('r', function(d){{ return Math.max(3, Math.sqrt(d.degree)*2); }})
    .attr('fill', function(d){{ return d.color; }})
    .attr('stroke', 'var(--bg)').attr('stroke-width', 1)
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', function(e,d){{ if(!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
      .on('drag', function(e,d){{ d.fx=e.x; d.fy=e.y; }})
      .on('end', function(e,d){{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}));

  node.on('mouseover', function(e,d) {{
    tooltip.style('opacity',1)
      .html('<strong>'+d.title+'</strong><br>Type: '+d.type+'<br>Tags: '+d.tags.length+'<br>Links: '+d.degree)
      .style('left', (e.offsetX+12)+'px').style('top', (e.offsetY-10)+'px');
  }}).on('mouseout', function() {{ tooltip.style('opacity',0); }});

  node.on('click', function(e, d) {{
    var connected = new Set();
    data.edges.forEach(function(e) {{
      var sid = typeof e.source === 'object' ? e.source.id : e.source;
      var tid = typeof e.target === 'object' ? e.target.id : e.target;
      if (sid === d.id) connected.add(tid);
      if (tid === d.id) connected.add(sid);
    }});
    connected.add(d.id);
    node.attr('opacity', function(n) {{ return connected.has(n.id) ? 1 : 0.1; }});
    link.attr('stroke-opacity', function(l) {{
      var sid = typeof l.source === 'object' ? l.source.id : l.source;
      var tid = typeof l.target === 'object' ? l.target.id : l.target;
      return (sid === d.id || tid === d.id) ? 0.8 : 0.03;
    }});
  }});

  svg.on('click', function(e) {{
    if (e.target.tagName !== 'circle') {{
      node.attr('opacity', 1);
      link.attr('stroke-opacity', 0.4);
    }}
  }});

  sim.on('tick', function() {{
    link.attr('x1', function(d){{ return d.source.x; }})
        .attr('y1', function(d){{ return d.source.y; }})
        .attr('x2', function(d){{ return d.target.x; }})
        .attr('y2', function(d){{ return d.target.y; }});
    node.attr('cx', function(d){{ return d.x; }})
        .attr('cy', function(d){{ return d.y; }});
  }});
}})();
</script>"""


def build_notes_over_time_html(metadata):
    """Build stacked area chart of notes created over time, by type."""
    if not metadata:
        return '<p class="empty">No metadata available.</p>'

    type_colors = {
        "concept": "#3b82f6",
        "insight": "#8b5cf6",
        "question": "#10b981",
        "reference": "#f97316",
        "synthesis": "#6b7280",
    }
    type_order = ["concept", "insight", "question", "reference", "synthesis"]

    # Group by month and type
    monthly = defaultdict(lambda: defaultdict(int))
    for slug, meta in metadata.items():
        created = meta.get("created", "")
        if not created or len(created) < 7:
            continue
        month = created[:7]  # YYYY-MM
        ntype = meta.get("type", "unknown")
        if ntype not in type_colors:
            ntype = "synthesis"  # fallback
        monthly[month][ntype] += 1

    if not monthly:
        return '<p class="empty">No date data in metadata.</p>'

    months = sorted(monthly.keys())

    # Compute cumulative stacks
    cumulative = defaultdict(int)
    stacks = {t: [] for t in type_order}
    for month in months:
        for t in type_order:
            cumulative[t] += monthly[month].get(t, 0)
            stacks[t].append(cumulative[t])

    total_max = sum(cumulative[t] for t in type_order)
    if total_max == 0:
        return '<p class="empty">No notes with dates.</p>'

    # SVG dimensions
    w, h = 900, 300
    pad_l, pad_r, pad_b, pad_t = 50, 60, 40, 20
    chart_w = w - pad_l - pad_r
    chart_h = h - pad_t - pad_b
    n = len(months)

    def x_pos(i):
        return pad_l + (i / max(n - 1, 1)) * chart_w

    def y_pos(val):
        return pad_t + chart_h - (val / total_max) * chart_h

    # Build area paths (stacked from bottom)
    svg_parts = [f'<svg viewBox="0 0 {w} {h}" style="width:100%;max-width:{w}px;height:auto;" xmlns="http://www.w3.org/2000/svg">']

    # Grid lines
    for frac in [0.25, 0.5, 0.75, 1.0]:
        yy = y_pos(total_max * frac)
        val = int(total_max * frac)
        svg_parts.append(f'<line x1="{pad_l}" y1="{yy}" x2="{w - pad_r}" y2="{yy}" stroke="var(--border)" stroke-width="0.5" />')
        svg_parts.append(f'<text x="{pad_l - 6}" y="{yy + 4}" text-anchor="end" fill="var(--muted)" font-size="10">{val}</text>')

    # Build stacked areas top-down (reverse so concept is on top visually at bottom of stack)
    for tidx in range(len(type_order) - 1, -1, -1):
        t = type_order[tidx]
        # Bottom line: sum of types below this one
        bottom = []
        top = []
        for i in range(n):
            base = sum(stacks[type_order[j]][i] for j in range(tidx))
            bottom.append(base)
            top.append(base + stacks[t][i])

        # Path: forward along top, backward along bottom
        points_top = " ".join(f"{x_pos(i)},{y_pos(top[i])}" for i in range(n))
        points_bottom = " ".join(f"{x_pos(i)},{y_pos(bottom[i])}" for i in range(n - 1, -1, -1))
        svg_parts.append(f'<polygon points="{points_top} {points_bottom}" fill="{type_colors[t]}" opacity="0.75" />')

    # X-axis labels (show every Nth month)
    step = max(1, n // 8)
    for i in range(0, n, step):
        svg_parts.append(f'<text x="{x_pos(i)}" y="{h - 8}" text-anchor="middle" fill="var(--muted)" font-size="10">{months[i]}</text>')

    # Total label at right edge
    svg_parts.append(f'<text x="{w - pad_r + 8}" y="{y_pos(total_max) + 4}" fill="var(--text)" font-size="12" font-weight="700">{total_max}</text>')

    svg_parts.append('</svg>')

    # Legend
    legend = " ".join(
        f'<span class="legend-item"><span class="legend-dot" style="background:{type_colors[t]}"></span>{t} ({cumulative[t]})</span>'
        for t in type_order if cumulative[t] > 0
    )

    return "\n".join(svg_parts) + f'\n<div class="type-legend" style="margin-top:8px;">{legend}</div>'


def build_coverage_radar_html(topic_data, metadata):
    """Build SVG radar chart for top topic clusters."""
    if not topic_data:
        return '<p class="empty">No topic data available.</p>'

    today = date.today()
    # Pick top 6-8 clusters by note count
    sorted_tags = sorted(topic_data.keys(), key=lambda t: -topic_data[t]["count"])[:8]
    if len(sorted_tags) < 3:
        return '<p class="empty">Need at least 3 clusters for radar chart.</p>'

    n = len(sorted_tags)
    axes = ["Note Count", "Link Density", "Type Diversity", "Freshness"]
    n_axes = len(axes)

    # Compute raw values per cluster
    raw = {}
    for tag in sorted_tags:
        td = topic_data[tag]
        note_count = td["count"]
        link_density = td["link_density"]
        type_diversity = len(td.get("types", {})) / 5.0

        # Avg freshness: days since avg updated date (inverted)
        updated_days = []
        for slug, meta in metadata.items():
            if tag in meta.get("tags", []):
                upd = meta.get("updated", meta.get("created", ""))
                if upd:
                    try:
                        d = datetime.strptime(upd, "%Y-%m-%d").date()
                        updated_days.append((today - d).days)
                    except ValueError:
                        pass
        avg_days = sum(updated_days) / len(updated_days) if updated_days else 365
        freshness = max(0, 1 - avg_days / 365)  # 0=stale, 1=fresh

        raw[tag] = [note_count, link_density, type_diversity, freshness]

    # Normalize each axis 0..1 across clusters
    for axis_idx in range(n_axes):
        vals = [raw[t][axis_idx] for t in sorted_tags]
        vmin, vmax = min(vals), max(vals)
        rng = vmax - vmin if vmax > vmin else 1
        for t in sorted_tags:
            raw[t][axis_idx] = (raw[t][axis_idx] - vmin) / rng

    # SVG radar
    cx, cy, r = 200, 200, 150
    w, h = 450, 420

    svg = [f'<svg viewBox="0 0 {w} {h}" style="width:100%;max-width:{w}px;height:auto;" xmlns="http://www.w3.org/2000/svg">']

    # Draw axis lines and labels
    angle_step = 2 * math.pi / n_axes
    for i in range(n_axes):
        angle = -math.pi / 2 + i * angle_step
        ex = cx + r * math.cos(angle)
        ey = cy + r * math.sin(angle)
        svg.append(f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="var(--border)" stroke-width="1" />')
        lx = cx + (r + 20) * math.cos(angle)
        ly = cy + (r + 20) * math.sin(angle)
        anchor = "middle"
        if math.cos(angle) > 0.3:
            anchor = "start"
        elif math.cos(angle) < -0.3:
            anchor = "end"
        svg.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" fill="var(--muted)" font-size="11">{axes[i]}</text>')

    # Concentric rings
    for ring in [0.25, 0.5, 0.75, 1.0]:
        points = []
        for i in range(n_axes):
            angle = -math.pi / 2 + i * angle_step
            px = cx + r * ring * math.cos(angle)
            py = cy + r * ring * math.sin(angle)
            points.append(f"{px:.1f},{py:.1f}")
        svg.append(f'<polygon points="{" ".join(points)}" fill="none" stroke="var(--border)" stroke-width="0.5" stroke-dasharray="3,3" />')

    # Draw polygon for each cluster
    cluster_colors_radar = CLUSTER_COLORS[:len(sorted_tags)]
    for cidx, tag in enumerate(sorted_tags):
        vals = raw[tag]
        points = []
        for i in range(n_axes):
            angle = -math.pi / 2 + i * angle_step
            v = max(vals[i], 0.05)  # minimum visible
            px = cx + r * v * math.cos(angle)
            py = cy + r * v * math.sin(angle)
            points.append(f"{px:.1f},{py:.1f}")
        color = cluster_colors_radar[cidx]
        svg.append(f'<polygon points="{" ".join(points)}" fill="{color}" fill-opacity="0.12" stroke="{color}" stroke-width="1.5" />')

    svg.append('</svg>')

    # Legend for clusters
    legend_parts = []
    for cidx, tag in enumerate(sorted_tags):
        color = cluster_colors_radar[cidx]
        legend_parts.append(f'<span class="legend-item"><span class="legend-dot" style="background:{color}"></span>{escape(tag)}</span>')
    legend = " ".join(legend_parts)

    return "\n".join(svg) + f'\n<div class="type-legend" style="margin-top:8px;">{legend}</div>'


def build_research_depth_html(metadata):
    """Build research depth heatmap table."""
    # Find research hubs
    hubs = {}
    today = date.today()
    for slug, meta in metadata.items():
        if "research-hub" in meta.get("tags", []):
            related = meta.get("related", []) + meta.get("outgoing_links", [])
            # Count related notes that exist in metadata
            spawned = len([r for r in related if r in metadata])
            updated = meta.get("updated", meta.get("created", ""))
            days_since = 999
            if updated:
                try:
                    d = datetime.strptime(updated, "%Y-%m-%d").date()
                    days_since = (today - d).days
                except ValueError:
                    pass
            hubs[slug] = {
                "title": meta.get("title", slug),
                "spawned": spawned,
                "gaps": 0,  # will fill from find_research_gaps
                "days_since": days_since,
            }

    if not hubs:
        return '<p class="empty">No research hubs found (notes tagged research-hub).</p>'

    # Merge gap counts
    try:
        gaps_data = kb.find_research_gaps()
        for gd in gaps_data:
            hub_slug = gd["hub"]
            if hub_slug in hubs:
                hubs[hub_slug]["gaps"] = len(gd["gaps"])
    except Exception:
        pass

    # Sort by most gaps first
    hub_list = sorted(hubs.values(), key=lambda h: -h["gaps"])

    # Compute max values for gradient normalization
    max_spawned = max((h["spawned"] for h in hub_list), default=1) or 1
    max_gaps = max((h["gaps"] for h in hub_list), default=1) or 1
    max_days = max((h["days_since"] for h in hub_list), default=1) or 1

    rows = []
    for h in hub_list:
        sp_intensity = min(h["spawned"] / max_spawned, 1)
        gap_intensity = min(h["gaps"] / max_gaps, 1) if max_gaps > 0 else 0
        fresh_ratio = min(h["days_since"] / max_days, 1)  # 0=fresh, 1=stale

        sp_bg = f"rgba(59,130,246,{sp_intensity * 0.5 + 0.05})"
        gap_bg = f"rgba(239,68,68,{gap_intensity * 0.5 + 0.05})"
        # Fresh=green -> stale=red
        fr_hue = int((1 - fresh_ratio) * 120)  # 120=green, 0=red
        fr_bg = f"hsla({fr_hue},65%,45%,0.35)"

        days_label = f"{h['days_since']}d" if h["days_since"] < 999 else "N/A"
        rows.append(
            f'<tr>'
            f'<td style="text-align:left;padding:6px 10px;font-size:13px;">{escape(h["title"])}</td>'
            f'<td style="text-align:center;padding:6px 10px;background:{sp_bg};font-weight:600;">{h["spawned"]}</td>'
            f'<td style="text-align:center;padding:6px 10px;background:{gap_bg};font-weight:600;">{h["gaps"]}</td>'
            f'<td style="text-align:center;padding:6px 10px;background:{fr_bg};font-weight:600;">{days_label}</td>'
            f'</tr>'
        )

    return (
        '<div style="overflow-x:auto;">'
        '<table style="width:100%;border-collapse:collapse;font-size:14px;">'
        '<thead><tr style="border-bottom:2px solid var(--border);">'
        '<th style="text-align:left;padding:8px 10px;color:var(--muted);font-size:12px;text-transform:uppercase;">Hub</th>'
        '<th style="text-align:center;padding:8px 10px;color:var(--muted);font-size:12px;text-transform:uppercase;">Notes Spawned</th>'
        '<th style="text-align:center;padding:8px 10px;color:var(--muted);font-size:12px;text-transform:uppercase;">Open Gaps</th>'
        '<th style="text-align:center;padding:8px 10px;color:var(--muted);font-size:12px;text-transform:uppercase;">Last Updated</th>'
        '</tr></thead><tbody>'
        + "\n".join(rows)
        + '</tbody></table></div>'
    )


def build_gap_burden_html():
    """Build horizontal bar chart of research gaps per hub."""
    try:
        gaps_data = kb.find_research_gaps()
    except Exception:
        return '<p class="empty">Could not load research gaps.</p>'

    if not gaps_data:
        return '<p class="empty">No research gaps found.</p>'

    # Sort by most gaps
    gaps_data = sorted(gaps_data, key=lambda g: -len(g["gaps"]))

    max_gaps = max(len(g["gaps"]) for g in gaps_data) if gaps_data else 1
    bar_max_w = 600
    bar_h = 28
    gap_y = 6
    total_h = len(gaps_data) * (bar_h + gap_y) + 20

    svg = [f'<svg viewBox="0 0 900 {total_h}" style="width:100%;max-width:900px;height:auto;" xmlns="http://www.w3.org/2000/svg">']

    for i, gd in enumerate(gaps_data):
        n_gaps = len(gd["gaps"])
        y = i * (bar_h + gap_y) + 10
        bar_w = max((n_gaps / max_gaps) * bar_max_w, 4)

        # Color: more gaps = more red
        intensity = n_gaps / max_gaps if max_gaps > 0 else 0
        r = int(59 + (239 - 59) * intensity)
        g_val = int(130 - 62 * intensity)
        b = int(246 - 178 * intensity)
        color = f"rgb({r},{g_val},{b})"

        title = gd.get("title", gd["hub"])
        svg.append(f'<text x="0" y="{y + bar_h / 2 + 4}" fill="var(--text)" font-size="12" text-anchor="start">{escape(title[:40])}</text>')
        svg.append(f'<rect x="260" y="{y}" width="{bar_w:.1f}" height="{bar_h}" rx="4" fill="{color}" opacity="0.8" />')
        svg.append(f'<text x="{260 + bar_w + 8}" y="{y + bar_h / 2 + 4}" fill="var(--muted)" font-size="12">{n_gaps} gaps</text>')

    svg.append('</svg>')
    return "\n".join(svg)


def build_retrieval_heatmap_html():
    """Build retrieval feedback visualization."""
    entries = load_feedback()

    if not entries:
        return '<p class="empty" style="padding:20px;background:var(--surface);border-radius:8px;text-align:center;">No feedback data yet. Use <code>/kb-search</code> and report issues to build this view.</p>'

    # Count missed notes
    missed_counts = defaultdict(int)
    failed_queries = []
    for entry in entries:
        for note in entry.get("expected", []):
            if note not in entry.get("retrieved", []):
                missed_counts[note] += 1
        if entry.get("failure_type") in ("missed", "empty"):
            failed_queries.append(entry.get("query", "unknown"))

    if not missed_counts and not failed_queries:
        return '<p class="empty">Feedback recorded but no misses detected.</p>'

    parts = []

    # Missed notes bar chart
    if missed_counts:
        top_missed = sorted(missed_counts.items(), key=lambda x: -x[1])[:15]
        max_miss = top_missed[0][1] if top_missed else 1
        bar_h = 24
        gap_y = 4
        total_h = len(top_missed) * (bar_h + gap_y) + 20
        bar_max_w = 400

        svg = [f'<svg viewBox="0 0 900 {total_h}" style="width:100%;max-width:900px;height:auto;" xmlns="http://www.w3.org/2000/svg">']
        for i, (slug, count) in enumerate(top_missed):
            y = i * (bar_h + gap_y) + 10
            bar_w = max((count / max_miss) * bar_max_w, 4)
            svg.append(f'<text x="0" y="{y + bar_h / 2 + 4}" fill="var(--text)" font-size="11" text-anchor="start">{escape(slug[:50])}</text>')
            svg.append(f'<rect x="350" y="{y}" width="{bar_w:.1f}" height="{bar_h}" rx="4" fill="#ef4444" opacity="0.7" />')
            svg.append(f'<text x="{350 + bar_w + 8}" y="{y + bar_h / 2 + 4}" fill="var(--muted)" font-size="11">missed {count}x</text>')
        svg.append('</svg>')
        parts.append('<h3 style="font-size:14px;color:var(--muted);margin-bottom:8px;">Most-Missed Notes</h3>')
        parts.append("\n".join(svg))

    # Failed queries list
    if failed_queries:
        parts.append('<h3 style="font-size:14px;color:var(--muted);margin-top:16px;margin-bottom:8px;">Failed Queries</h3>')
        items = [f'<li style="font-size:13px;color:var(--text);padding:2px 0;">{escape(q)}</li>' for q in failed_queries[:10]]
        parts.append(f'<ul style="list-style:disc;padding-left:20px;">{"".join(items)}</ul>')

    return "\n".join(parts)


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

    # New visualizations
    graph_data = load_graph_data()
    all_metadata = load_all_metadata()
    cluster_colors = get_cluster_color_map(topic_data)
    link_graph_html = build_link_graph_html(graph_data, all_metadata, topic_data, cluster_colors)
    notes_over_time_html = build_notes_over_time_html(all_metadata)
    coverage_radar_html = build_coverage_radar_html(topic_data, all_metadata)
    research_depth_html = build_research_depth_html(all_metadata)
    gap_burden_html = build_gap_burden_html()
    retrieval_heatmap_html = build_retrieval_heatmap_html()

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

<div class="section">
  <h2>Link Graph</h2>
  <p style="color:var(--muted);font-size:13px;margin-bottom:12px;">Force-directed graph of note connections. Nodes colored by cluster, sized by link count. Click a node to highlight its neighbors. Drag to rearrange, scroll to zoom.</p>
  {link_graph_html}
</div>

<div class="section">
  <h2>Notes Created Over Time</h2>
  <p style="color:var(--muted);font-size:13px;margin-bottom:12px;">Cumulative note count by type, grouped by month.</p>
  {notes_over_time_html}
</div>

<div class="section">
  <h2>Coverage Radar</h2>
  <p style="color:var(--muted);font-size:13px;margin-bottom:12px;">Top topic clusters compared across four axes: note count, link density, type diversity, and freshness (how recently updated).</p>
  {coverage_radar_html}
</div>

<div class="section">
  <h2>Research Depth Heatmap</h2>
  <p style="color:var(--muted);font-size:13px;margin-bottom:12px;">Research hubs with spawned notes, open gaps, and staleness. Cells colored by intensity.</p>
  {research_depth_html}
</div>

<div class="section">
  <h2>Gap Burden</h2>
  <p style="color:var(--muted);font-size:13px;margin-bottom:12px;">Open research gaps per hub. More gaps shifts the color toward red.</p>
  {gap_burden_html}
</div>

<div class="section">
  <h2>Retrieval Heatmap</h2>
  <p style="color:var(--muted);font-size:13px;margin-bottom:12px;">Search feedback analysis: which notes are most frequently missed by retrieval, and which queries fail.</p>
  {retrieval_heatmap_html}
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
