#!/usr/bin/env python3
"""Build a topic-centered Knowledge Atlas report.

The Atlas is an enriched publish artifact: a self-contained HTML report that
bundles a topic brief, evidence profile, link map, gap radar, research trail,
and every relevant note body into one navigable file.

Usage:
  python3 .kb/build-atlas-report.py "topic of interest"
  python3 .kb/build-atlas-report.py "another topic" --kb my-domain
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BASE = Path(__file__).parent.parent
PUBLISH = BASE / "publish"
REFS = BASE / "references"
LOG_FILE = BASE / ".kb" / "log.md"

KB_INDEX_PATH = BASE / ".kb" / "kb-index.py"
REPORT_PATH = BASE / ".kb" / "build-report.py"

TIER_RANK = {"core": 0, "supporting": 1, "adjacent": 2}
TIER_LABELS = {
    "core": "Core match",
    "supporting": "Linked support",
    "adjacent": "Adjacent context",
}

BROAD_TAGS = {
    "concept",
    "reference",
    "insight",
    "synthesis",
    "question",
    "research-hub",
    "code-analysis",
    "product",
    "architecture",
    "personal",
}

SOURCE_TYPES = ("primary", "secondary", "opinion", "unverified", "unknown", "missing")


@dataclass
class SelectedNote:
    key: str
    kb: str
    slug: str
    title: str
    tier: str
    relevance: float = 0.0
    distance: int = 0
    reasons: List[str] = field(default_factory=list)
    matched_tags: List[str] = field(default_factory=list)
    fm: Dict[str, Any] = field(default_factory=dict)
    body: str = ""
    path: Optional[Path] = None
    html_id: str = ""


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


KB_INDEX = load_local_module("kb_index_for_atlas", KB_INDEX_PATH)
REPORT = load_local_module("kb_report_for_atlas", REPORT_PATH)


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "atlas"


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def parse_iso_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def display_date(value: Any) -> str:
    parsed = parse_iso_date(value)
    return parsed.isoformat() if parsed else ""


def split_index_key(key: str, meta: Dict[str, Any], kb_name: Optional[str] = None) -> Tuple[str, str]:
    if ":" in key:
        kb, slug = key.split(":", 1)
        return kb, slug
    return str(meta.get("kb") or kb_name or ""), key


def qualified_lookup(kb: str, slug: str) -> str:
    return f"{kb}:{slug}" if kb else slug


def html_id_for(kb: str, slug: str) -> str:
    prefix = slugify(kb) if kb else "kb"
    return f"{prefix}--{slugify(slug)}"


def load_metadata_and_graph(kb_name: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if kb_name:
        kb = KB_INDEX.resolve_kb(kb_name)
        meta_file = kb.meta_file
        graph_file = kb.graph_file
        label = kb.name
    else:
        meta_file = KB_INDEX.UNIFIED_DIR / "metadata.json"
        graph_file = KB_INDEX.UNIFIED_DIR / "graph.json"
        label = "unified"

    if not meta_file.exists():
        print(f"[{label}] No index. Run: python3 .kb/kb-index.py build", file=sys.stderr)
        sys.exit(1)

    metadata = json.loads(meta_file.read_text())
    graph = json.loads(graph_file.read_text()) if graph_file.exists() else {"adjacency": {}}
    return metadata, graph


def result_key(result: Dict[str, Any], kb_name: Optional[str]) -> str:
    if result.get("qualified_slug"):
        return result["qualified_slug"]
    if kb_name:
        return result["slug"]
    if result.get("kb"):
        return f"{result['kb']}:{result['slug']}"
    return result["slug"]


def update_selection(
    selected: Dict[str, Dict[str, Any]],
    key: str,
    tier: str,
    relevance: float,
    distance: int,
    reason: str,
    matched_tags: Optional[Iterable[str]] = None,
) -> None:
    rank = TIER_RANK[tier]
    tags = sorted(set(matched_tags or []))
    if key not in selected:
        selected[key] = {
            "tier": tier,
            "tier_rank": rank,
            "relevance": relevance,
            "distance": distance,
            "reasons": [reason] if reason else [],
            "matched_tags": tags,
        }
        return

    current = selected[key]
    if rank < current["tier_rank"]:
        current["tier"] = tier
        current["tier_rank"] = rank
    current["relevance"] = max(float(current.get("relevance", 0.0)), relevance)
    current["distance"] = min(int(current.get("distance", distance)), distance)
    if reason and reason not in current["reasons"]:
        current["reasons"].append(reason)
    current["matched_tags"] = sorted(set(current.get("matched_tags", [])) | set(tags))


def collect_relevant_notes(
    topic: str,
    kb_name: Optional[str],
    limit: int,
    core_limit: int,
    depth: int,
    include_adjacent: bool,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    metadata, graph = load_metadata_and_graph(kb_name)
    adjacency = graph.get("adjacency", {})

    core_results = KB_INDEX.search(topic, top_k=core_limit, kb_name=kb_name)
    if not core_results:
        core_results = KB_INDEX.quick_search(topic, top_k=core_limit, kb_name=kb_name)

    selected: Dict[str, Dict[str, Any]] = {}
    core_keys: List[str] = []

    for result in core_results:
        key = result_key(result, kb_name)
        if key not in metadata:
            continue
        core_keys.append(key)
        update_selection(
            selected,
            key,
            "core",
            float(result.get("score", 0.0)),
            0,
            "direct search match",
        )

    if depth > 0 and adjacency:
        for seed in core_keys:
            seed_score = float(selected.get(seed, {}).get("relevance", 0.0))
            seen = {seed}
            frontier = [(seed, 0)]
            while frontier:
                current, dist = frontier.pop(0)
                if dist >= depth:
                    continue
                neighbors = (
                    adjacency.get(current, {}).get("outgoing", [])
                    + adjacency.get(current, {}).get("incoming", [])
                )
                for neighbor in neighbors:
                    if neighbor in seen or neighbor not in metadata:
                        continue
                    seen.add(neighbor)
                    next_dist = dist + 1
                    relevance = seed_score * (0.55 ** next_dist)
                    update_selection(
                        selected,
                        neighbor,
                        "supporting",
                        relevance,
                        next_dist,
                        f"{next_dist}-hop graph neighbor of {seed}",
                    )
                    frontier.append((neighbor, next_dist))

    if include_adjacent and selected:
        core_tag_counts: Counter[str] = Counter()
        for key in core_keys:
            for tag in metadata.get(key, {}).get("tags", []):
                if tag not in BROAD_TAGS:
                    core_tag_counts[tag] += 1

        top_tags = [tag for tag, _ in core_tag_counts.most_common(10)]
        if top_tags:
            candidates = []
            top_tag_set = set(top_tags)
            for key, meta in metadata.items():
                if key in selected:
                    continue
                note_tags = set(meta.get("tags", []))
                matched = sorted(note_tags & top_tag_set)
                if not matched:
                    continue
                score = len(matched) * 0.1 + min(meta.get("word_count", 0), 1200) / 12000.0
                candidates.append((key, score, matched))

            candidates.sort(key=lambda item: (-item[1], item[0]))
            room = max(0, limit - len(selected))
            for key, score, matched in candidates[:room]:
                update_selection(
                    selected,
                    key,
                    "adjacent",
                    score,
                    depth + 1,
                    "shares high-signal topic tags",
                    matched_tags=matched,
                )

    ranked = sorted(
        selected.items(),
        key=lambda item: (
            item[1]["tier_rank"],
            -float(item[1].get("relevance", 0.0)),
            int(item[1].get("distance", 0)),
            item[0],
        ),
    )[:limit]

    selected_rows = []
    for key, info in ranked:
        meta = metadata.get(key, {})
        kb, slug = split_index_key(key, meta, kb_name)
        selected_rows.append({
            "key": key,
            "kb": kb,
            "slug": slug,
            "title": meta.get("title", slug),
            **info,
        })

    return selected_rows, metadata, graph, core_results


def load_selected_notes(
    selected_rows: Sequence[Dict[str, Any]],
    metadata: Dict[str, Any],
    kb_name: Optional[str],
) -> List[SelectedNote]:
    notes: List[SelectedNote] = []
    for row in selected_rows:
        key = row["key"]
        meta = metadata.get(key, {})
        kb, slug = row["kb"], row["slug"]
        lookup = qualified_lookup(kb, slug)
        path = REPORT.find_note(lookup)
        if not path:
            continue
        fm, body = REPORT.parse_note(path)
        title = fm.get("title") or meta.get("title") or slug.replace("-", " ").title()
        note = SelectedNote(
            key=key,
            kb=kb or kb_name or "",
            slug=slug,
            title=title,
            tier=row["tier"],
            relevance=float(row.get("relevance", 0.0)),
            distance=int(row.get("distance", 0)),
            reasons=list(row.get("reasons", [])),
            matched_tags=list(row.get("matched_tags", [])),
            fm=fm,
            body=body,
            path=path,
            html_id=html_id_for(kb or kb_name or "", slug),
        )
        notes.append(note)
    return notes


def build_wikilink_resolver(notes: Sequence[SelectedNote]):
    slug_by_kb: Dict[Tuple[str, str], str] = {}
    qualified_to_id: Dict[str, str] = {}
    bare_to_ids: Dict[str, List[str]] = defaultdict(list)

    for note in notes:
        slug_by_kb[(note.kb, note.slug)] = note.html_id
        qualified_to_id[f"{note.kb}:{note.slug}"] = note.html_id
        if note.html_id not in bare_to_ids[note.slug]:
            bare_to_ids[note.slug].append(note.html_id)

    def resolve(raw_slug: str, context_kb: Optional[str] = None) -> Optional[str]:
        explicit = ":" in raw_slug and not raw_slug.startswith("http")
        if explicit:
            return qualified_to_id.get(raw_slug)
        if context_kb:
            found = slug_by_kb.get((context_kb, raw_slug))
            if found:
                return found
        ids = bare_to_ids.get(raw_slug, [])
        return ids[0] if len(ids) == 1 else None

    return resolve


def strip_markdown(text: str) -> str:
    text = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`>#]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_key_takeaways(body: str, limit: int = 4) -> List[str]:
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^#{2,4}\s+key takeaways\s*$", line.strip(), re.I):
            start = i + 1
            break
    if start is None:
        return []

    takeaways = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith(("- ", "* ")):
            takeaways.append(strip_markdown(stripped[2:]))
            if len(takeaways) >= limit:
                break
    return takeaways


def extract_excerpt(body: str, max_chars: int = 260) -> str:
    paragraphs = re.split(r"\n\s*\n", body)
    for para in paragraphs:
        stripped = para.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```"):
            continue
        text = strip_markdown(stripped)
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "..."
        return text
    return ""


def summarize_topic(topic: str, notes: Sequence[SelectedNote]) -> Dict[str, Any]:
    kb_counts = Counter(note.kb for note in notes)
    type_counts = Counter(str(note.fm.get("type", "unknown")) for note in notes)
    status_counts = Counter(str(note.fm.get("epistemic_status", "unknown")) for note in notes)
    tier_counts = Counter(note.tier for note in notes)

    takeaways: List[Tuple[str, str]] = []
    seen = set()
    for note in notes:
        for takeaway in extract_key_takeaways(note.body, limit=3):
            key = takeaway.lower()
            if key in seen:
                continue
            seen.add(key)
            takeaways.append((note.html_id, takeaway))
            if len(takeaways) >= 10:
                break
        if len(takeaways) >= 10:
            break

    if not takeaways:
        for note in notes[:8]:
            excerpt = extract_excerpt(note.body)
            if excerpt:
                takeaways.append((note.html_id, excerpt))

    return {
        "topic": topic,
        "note_count": len(notes),
        "kb_counts": kb_counts,
        "type_counts": type_counts,
        "status_counts": status_counts,
        "tier_counts": tier_counts,
        "takeaways": takeaways[:10],
    }


def normalize_ref_path(ref_path: str) -> str:
    cleaned = str(ref_path).strip().split("#", 1)[0]
    cleaned = re.sub(r"^(\.\./)*references/", "", cleaned)
    cleaned = re.sub(r"^references/", "", cleaned)
    return cleaned


def extract_reference_paths(fm: Dict[str, Any], body: str) -> List[str]:
    paths = []
    for src in fm.get("sources", []) or []:
        if isinstance(src, str) and "references/" in src:
            paths.append(normalize_ref_path(src))
    for match in re.findall(r"\[[^\]]+\]\(((?:\.\./)*references/[^)#]+)(?:#[^)]+)?\)", body):
        paths.append(normalize_ref_path(match))
    return sorted(set(path for path in paths if path))


def parse_reference_file(cleaned_path: str) -> Dict[str, Any]:
    full_path = REFS / cleaned_path
    if not full_path.exists():
        return {
            "path": cleaned_path,
            "title": cleaned_path,
            "url": "",
            "source_type": "missing",
            "exists": False,
        }
    text = full_path.read_text(errors="replace")
    title_match = re.search(r"^#\s+(.+)$", text, re.M)
    source_match = re.search(r"^Source:\s*(https?://\S+)", text, re.M | re.I)
    type_match = re.search(r"^Source-Type:\s*([A-Za-z_-]+)", text, re.M | re.I)
    source_type = (type_match.group(1).lower() if type_match else "unknown")
    if source_type not in SOURCE_TYPES:
        source_type = "unknown"
    return {
        "path": cleaned_path,
        "title": title_match.group(1).strip() if title_match else full_path.stem.replace("-", " ").title(),
        "url": source_match.group(1).strip() if source_match else "",
        "source_type": source_type,
        "exists": True,
    }


def build_source_index(notes: Sequence[SelectedNote]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]]]:
    sources: Dict[str, Dict[str, Any]] = {}
    by_note: Dict[str, List[str]] = {}
    for note in notes:
        ref_paths = extract_reference_paths(note.fm, note.body)
        by_note[note.html_id] = ref_paths
        for ref_path in ref_paths:
            if ref_path not in sources:
                sources[ref_path] = parse_reference_file(ref_path)
            sources[ref_path].setdefault("notes", []).append(note.html_id)
    return sources, by_note


def evidence_summary(notes: Sequence[SelectedNote], source_index: Dict[str, Dict[str, Any]], by_note: Dict[str, List[str]]) -> Dict[str, Any]:
    status_counts = Counter(str(note.fm.get("epistemic_status", "unknown")) for note in notes)
    type_counts = Counter(str(note.fm.get("type", "unknown")) for note in notes)
    kb_counts = Counter(note.kb for note in notes)
    source_type_counts = Counter(src.get("source_type", "unknown") for src in source_index.values())
    notes_without_sources = [note for note in notes if not by_note.get(note.html_id)]

    stale_threshold = KB_INDEX.cfg("staleness", "default_days")
    today = date.today()
    stale_notes = []
    expired_notes = []
    deprecated_notes = []
    for note in notes:
        updated = parse_iso_date(note.fm.get("updated"))
        if updated and (today - updated).days > stale_threshold:
            stale_notes.append((note, (today - updated).days))
        valid_until = parse_iso_date(note.fm.get("valid_until"))
        if valid_until and valid_until < today:
            expired_notes.append(note)
        if note.fm.get("deprecated_by"):
            deprecated_notes.append(note)

    return {
        "status_counts": status_counts,
        "type_counts": type_counts,
        "kb_counts": kb_counts,
        "source_type_counts": source_type_counts,
        "notes_without_sources": notes_without_sources,
        "stale_notes": stale_notes,
        "expired_notes": expired_notes,
        "deprecated_notes": deprecated_notes,
        "stale_threshold": stale_threshold,
    }


def matching_topic_gaps(topic: str, notes: Sequence[SelectedNote], kb_name: Optional[str]) -> List[Dict[str, Any]]:
    selected_tags = Counter()
    for note in notes:
        for tag in note.fm.get("tags", []) or []:
            selected_tags[tag] += 1
    tag_set = set(selected_tags)
    topic_words = set(re.sub(r"[^a-z0-9\s-]", " ", topic.lower()).replace("-", " ").split())

    gaps = []
    for gap in KB_INDEX.find_topic_gaps(kb_name):
        gap_topic = gap.get("topic", "")
        gap_words = set(gap_topic.replace("-", " ").split())
        if gap_topic in tag_set or topic_words & gap_words:
            gaps.append(gap)

    research_gaps = []
    for hub in KB_INDEX.find_research_gaps(kb_name):
        haystack = " ".join([hub.get("title", ""), hub.get("hub", ""), " ".join(hub.get("gaps", []))]).lower()
        if any(word and word in haystack for word in topic_words) or tag_set.intersection(set(haystack.split())):
            research_gaps.append(hub)

    return [{"kind": "topic", **g} for g in gaps[:8]] + [{"kind": "research", **g} for g in research_gaps[:8]]


def included_graph_edges(notes: Sequence[SelectedNote], graph: Dict[str, Any]) -> List[Tuple[str, str]]:
    included_keys = {note.key for note in notes}
    key_to_id = {note.key: note.html_id for note in notes}
    edges = []
    for source_key, links in graph.get("adjacency", {}).items():
        if source_key not in included_keys:
            continue
        for target_key in links.get("outgoing", []):
            if target_key in included_keys:
                edges.append((key_to_id[source_key], key_to_id[target_key]))
    return edges


def render_pills(items: Iterable[str], class_name: str = "pill") -> str:
    return "".join(f'<span class="{class_name}">{escape(item)}</span>' for item in items if item)


def render_overview(summary: Dict[str, Any]) -> str:
    kb_bits = ", ".join(f"{escape(k)} ({v})" for k, v in summary["kb_counts"].most_common())
    tier_bits = ", ".join(f"{TIER_LABELS.get(k, k)}: {v}" for k, v in summary["tier_counts"].most_common())
    status_bits = ", ".join(f"{escape(k)}: {v}" for k, v in summary["status_counts"].most_common())
    takeaways_html = "\n".join(
        f'<li><a href="#{escape(note_id)}">{escape(text)}</a></li>'
        for note_id, text in summary["takeaways"]
    )
    if not takeaways_html:
        takeaways_html = "<li>No key takeaways found yet. The included notes below are the source of truth.</li>"

    return f"""
<section class="atlas-panel" id="overview">
  <div class="panel-kicker">Topic Brief</div>
  <h2>What This Atlas Contains</h2>
  <p>This report packages <strong>{summary['note_count']}</strong> relevant notes about <strong>{escape(summary['topic'])}</strong>. It combines direct search matches with linked supporting notes and shared-tag adjacent context.</p>
  <div class="stat-grid">
    <div><span class="stat-value">{summary['note_count']}</span><span class="stat-label">notes</span></div>
    <div><span class="stat-value">{len(summary['kb_counts'])}</span><span class="stat-label">KBs</span></div>
    <div><span class="stat-value">{sum(summary['status_counts'].values())}</span><span class="stat-label">status-labeled notes</span></div>
  </div>
  <dl class="compact-facts">
    <dt>KBs</dt><dd>{kb_bits or "unknown"}</dd>
    <dt>Relevance tiers</dt><dd>{escape(tier_bits) or "unknown"}</dd>
    <dt>Epistemic status</dt><dd>{status_bits or "unknown"}</dd>
  </dl>
  <h3>Extracted Takeaways</h3>
  <ul class="takeaway-list">{takeaways_html}</ul>
</section>
"""


def render_evidence(evidence: Dict[str, Any], sources: Dict[str, Dict[str, Any]]) -> str:
    def count_list(counter: Counter) -> str:
        if not counter:
            return "<li>None recorded</li>"
        return "".join(f"<li><strong>{escape(k)}</strong>: {v}</li>" for k, v in counter.most_common())

    no_source_html = "".join(
        f'<li><a href="#{note.html_id}">{escape(note.title)}</a></li>'
        for note in evidence["notes_without_sources"][:12]
    ) or "<li>Every included note has at least one local reference citation or source pointer.</li>"

    stale_html = "".join(
        f'<li><a href="#{note.html_id}">{escape(note.title)}</a> ({days} days since update)</li>'
        for note, days in evidence["stale_notes"][:12]
    ) or "<li>No included notes exceed the configured freshness threshold.</li>"

    return f"""
<section class="atlas-panel" id="evidence">
  <div class="panel-kicker">Evidence</div>
  <h2>Evidence Profile</h2>
  <div class="evidence-grid">
    <div>
      <h3>Epistemic Status</h3>
      <ul>{count_list(evidence["status_counts"])}</ul>
    </div>
    <div>
      <h3>Note Types</h3>
      <ul>{count_list(evidence["type_counts"])}</ul>
    </div>
    <div>
      <h3>Source Types</h3>
      <ul>{count_list(evidence["source_type_counts"])}</ul>
    </div>
  </div>
  <div class="callout-grid">
    <div class="callout">
      <h3>Missing Source Pointers</h3>
      <ul>{no_source_html}</ul>
    </div>
    <div class="callout">
      <h3>Freshness Watch</h3>
      <p>Threshold: {evidence['stale_threshold']} days.</p>
      <ul>{stale_html}</ul>
    </div>
  </div>
  <p class="small-note">Local references found: {len(sources)}. Source type comes from each reference file's <code>Source-Type</code> field when available.</p>
</section>
"""


def graph_layout(notes: Sequence[SelectedNote], width: int = 960, height: int = 560) -> Dict[str, Tuple[float, float]]:
    cx, cy = width / 2, height / 2
    tiers = {
        "core": [n for n in notes if n.tier == "core"],
        "supporting": [n for n in notes if n.tier == "supporting"],
        "adjacent": [n for n in notes if n.tier == "adjacent"],
    }
    radii = {"core": 85, "supporting": 205, "adjacent": 300}
    positions: Dict[str, Tuple[float, float]] = {}
    for tier, tier_notes in tiers.items():
        count = max(1, len(tier_notes))
        radius = radii[tier]
        for i, note in enumerate(tier_notes):
            angle = -math.pi / 2 + (2 * math.pi * i / count)
            if tier == "core" and count == 1:
                x, y = cx, cy
            else:
                x = cx + radius * math.cos(angle)
                y = cy + radius * math.sin(angle)
            positions[note.html_id] = (x, y)
    return positions


def render_graph(notes: Sequence[SelectedNote], edges: Sequence[Tuple[str, str]]) -> str:
    if not notes:
        return ""
    width, height = 960, 560
    positions = graph_layout(notes, width, height)
    edge_html = []
    for source, target in edges:
        if source not in positions or target not in positions:
            continue
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        edge_html.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" />')

    node_html = []
    for i, note in enumerate(notes):
        x, y = positions[note.html_id]
        label = escape(note.title if len(note.title) <= 38 else note.title[:35] + "...")
        classes = f"node {note.tier}"
        radius = 10 if note.tier == "core" else 7 if note.tier == "supporting" else 5
        show_label = i < 32 or note.tier == "core"
        text_html = (
            f'<text x="{x + 12:.1f}" y="{y + 4:.1f}">{label}</text>'
            if show_label else ""
        )
        node_html.append(f"""
<a href="#{escape(note.html_id)}">
  <circle class="{classes}" cx="{x:.1f}" cy="{y:.1f}" r="{radius}">
    <title>{escape(note.title)} [{escape(note.kb)}]</title>
  </circle>
  {text_html}
</a>""")

    legend = "".join(
        f'<span><i class="legend-dot {tier}"></i>{label}</span>'
        for tier, label in TIER_LABELS.items()
    )
    return f"""
<section class="atlas-panel" id="map">
  <div class="panel-kicker">Map</div>
  <h2>Knowledge Map</h2>
  <div class="map-legend">{legend}</div>
  <div class="map-wrap">
    <svg viewBox="0 0 {width} {height}" role="img" aria-label="Knowledge graph of included notes">
      <g class="edges">{''.join(edge_html)}</g>
      <g class="nodes">{''.join(node_html)}</g>
    </svg>
  </div>
</section>
"""


def render_gaps(
    notes: Sequence[SelectedNote],
    graph: Dict[str, Any],
    by_note: Dict[str, List[str]],
    gaps: Sequence[Dict[str, Any]],
    evidence: Dict[str, Any],
) -> str:
    included_keys = {note.key for note in notes}
    orphans = []
    for note in notes:
        links = graph.get("adjacency", {}).get(note.key, {"incoming": [], "outgoing": []})
        local_degree = len(set(links.get("incoming", []) + links.get("outgoing", [])) & included_keys)
        if local_degree == 0:
            orphans.append(note)

    items = []
    if evidence["notes_without_sources"]:
        items.append(
            f"<li><strong>{len(evidence['notes_without_sources'])} included notes have no local source pointers.</strong> Add references or cite local files when these claims matter.</li>"
        )
    if orphans:
        items.append(
            f"<li><strong>{len(orphans)} included notes are isolated inside this atlas.</strong> Consider backlinks or a synthesis note to connect them.</li>"
        )
    if evidence["stale_notes"]:
        items.append(
            f"<li><strong>{len(evidence['stale_notes'])} notes are older than the freshness threshold.</strong> Review them before treating the report as current.</li>"
        )
    for gap in gaps[:10]:
        if gap.get("kind") == "topic":
            issues = "; ".join(gap.get("issues", []))
            items.append(f"<li><strong>Thin topic: {escape(gap.get('topic', 'unknown'))}.</strong> {escape(issues)}</li>")
        else:
            hub = escape(gap.get("title", gap.get("hub", "research hub")))
            gap_text = "; ".join(str(g) for g in gap.get("gaps", [])[:3])
            items.append(f"<li><strong>Research gaps from {hub}.</strong> {escape(gap_text)}</li>")

    if not items:
        items.append("<li>No obvious gaps surfaced from the included notes and current index checks.</li>")

    return f"""
<section class="atlas-panel" id="gaps">
  <div class="panel-kicker">Gaps</div>
  <h2>Gap Radar</h2>
  <ul class="gap-list">{''.join(items)}</ul>
</section>
"""


def render_research_trail(topic: str, notes: Sequence[SelectedNote], resolve_fn) -> str:
    if not LOG_FILE.exists():
        return ""

    text = LOG_FILE.read_text(errors="replace")
    entries = re.split(r"(?=^## \[)", text, flags=re.M)
    topic_words = {
        word for word in re.sub(r"[^a-z0-9\s-]", " ", topic.lower()).replace("-", " ").split()
        if len(word) > 3
    }
    note_terms = {note.slug for note in notes[:20]}
    tag_terms = set()
    for note in notes[:20]:
        tag_terms.update(str(tag).lower() for tag in note.fm.get("tags", []) or [])

    matches = []
    for entry in entries:
        lower = entry.lower()
        if not entry.strip().startswith("## ["):
            continue
        if any(term in lower for term in topic_words | note_terms | tag_terms):
            matches.append(entry.strip())
    matches = matches[-6:]

    if not matches:
        return ""

    trail_html = []
    footnotes: List[Tuple[int, str, str]] = []
    for entry in matches:
        trail_html.append(f'<div class="trail-entry">{REPORT.md_to_html(entry, footnotes, resolve_fn)}</div>')

    return f"""
<section class="atlas-panel" id="trail">
  <div class="panel-kicker">Trail</div>
  <h2>Research Trail</h2>
  {''.join(trail_html)}
</section>
"""


def render_note_sections(notes: Sequence[SelectedNote], resolve_fn, footnotes: List[Tuple[int, str, str]]) -> Tuple[str, str]:
    toc_items = []
    sections = []
    current_tier = None
    for note in notes:
        if note.tier != current_tier:
            current_tier = note.tier
            sections.append(f'<h2 class="tier-heading">{TIER_LABELS.get(note.tier, note.tier)}</h2>')
        note_type = str(note.fm.get("type", "unknown"))
        status = str(note.fm.get("epistemic_status", "unknown"))
        tags = note.fm.get("tags", []) or []
        updated = display_date(note.fm.get("updated"))
        score = f"{note.relevance:.4f}" if note.relevance else ""
        toc_items.append(f'<li><a href="#{note.html_id}">{escape(note.title)}</a></li>')
        meta_bits = [
            f'<span class="pill">{escape(note.kb)}</span>',
            f'<span class="pill">{escape(note_type)}</span>',
            f'<span class="pill status">{escape(status)}</span>',
        ]
        if updated:
            meta_bits.append(f'<span class="pill">updated {escape(updated)}</span>')
        if score:
            meta_bits.append(f'<span class="pill">score {escape(score)}</span>')
        if note.matched_tags:
            meta_bits.append(f'<span class="pill">tags: {escape(", ".join(note.matched_tags[:4]))}</span>')
        tags_html = render_pills(tags[:12], "tag")
        body_html = REPORT.md_to_html(note.body, footnotes, resolve_fn, note.kb)
        sections.append(f"""
<article class="section note-section" id="{escape(note.html_id)}" data-search="{escape(note.title + ' ' + ' '.join(tags) + ' ' + note.slug)}">
  <div class="section-header">
    <div>
      <h2>{escape(note.title)}</h2>
      <div class="note-meta">{''.join(meta_bits)}</div>
    </div>
    <span class="toggle">v</span>
  </div>
  <div class="section-body">
    <div class="note-tags">{tags_html}</div>
    {body_html}
  </div>
</article>
""")
    return "\n".join(toc_items), "\n".join(sections)


def render_sources(
    sources: Dict[str, Dict[str, Any]],
    footnotes: Sequence[Tuple[int, str, str]],
) -> str:
    source_rows = []
    for path, src in sorted(sources.items(), key=lambda item: (item[1].get("source_type", ""), item[0])):
        url = src.get("url", "")
        title = src.get("title", path)
        link = f'<a href="{escape(url)}" target="_blank" rel="noopener">{escape(title)}</a>' if url else escape(title)
        source_rows.append(f"""
<tr>
  <td><span class="source-type {escape(src.get('source_type', 'unknown'))}">{escape(src.get('source_type', 'unknown'))}</span></td>
  <td>{link}<br><code>{escape(path)}</code></td>
  <td>{len(src.get('notes', []))}</td>
</tr>""")

    if not source_rows:
        source_rows.append('<tr><td colspan="3">No local references found in the included notes.</td></tr>')

    footnote_rows = []
    for num, text, url in footnotes:
        footnote_rows.append(
            f'<li id="fn-{num}"><a href="{escape(url)}" target="_blank" rel="noopener">{escape(text)}</a></li>'
        )
    footnotes_html = "".join(footnote_rows) or "<li>No inline footnotes generated.</li>"

    return f"""
<section class="atlas-panel" id="sources">
  <div class="panel-kicker">Sources</div>
  <h2>Source Index</h2>
  <table class="source-table">
    <thead><tr><th>Type</th><th>Reference</th><th>Notes</th></tr></thead>
    <tbody>{''.join(source_rows)}</tbody>
  </table>
  <h3>Inline Footnotes</h3>
  <ol class="footnotes">{footnotes_html}</ol>
</section>
"""


def render_manifest(notes: Sequence[SelectedNote], sources: Dict[str, Dict[str, Any]], edges: Sequence[Tuple[str, str]], topic: str) -> Dict[str, Any]:
    return {
        "topic": topic,
        "generated": date.today().isoformat(),
        "notes": [
            {
                "id": note.html_id,
                "key": note.key,
                "kb": note.kb,
                "slug": note.slug,
                "title": note.title,
                "tier": note.tier,
                "type": note.fm.get("type", "unknown"),
                "epistemic_status": note.fm.get("epistemic_status", "unknown"),
                "tags": note.fm.get("tags", []) or [],
                "sources": extract_reference_paths(note.fm, note.body),
            }
            for note in notes
        ],
        "sources": list(sources.values()),
        "edges": [{"source": source, "target": target} for source, target in edges],
    }


def build_html(
    title: str,
    topic: str,
    notes: Sequence[SelectedNote],
    metadata: Dict[str, Any],
    graph: Dict[str, Any],
    kb_name: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    resolve_fn = build_wikilink_resolver(notes)
    source_index, by_note = build_source_index(notes)
    evidence = evidence_summary(notes, source_index, by_note)
    summary = summarize_topic(topic, notes)
    gaps = matching_topic_gaps(topic, notes, kb_name)
    edges = included_graph_edges(notes, graph)

    note_footnotes: List[Tuple[int, str, str]] = []
    note_toc, note_sections = render_note_sections(notes, resolve_fn, note_footnotes)
    trail_html = render_research_trail(topic, notes, resolve_fn)

    content_parts = [
        render_overview(summary),
        render_evidence(evidence, source_index),
        render_graph(notes, edges),
        render_gaps(notes, graph, by_note, gaps, evidence),
    ]
    if trail_html:
        content_parts.append(trail_html)
    content_parts.extend([
    f"""
<section class="atlas-panel" id="notes">
  <div class="panel-kicker">Notes</div>
  <h2>Included Notes</h2>
  <div class="search-bar"><input type="text" id="section-search" placeholder="Filter included notes..." oninput="filterSections(this.value)"></div>
  {note_sections}
</section>
""",
        render_sources(source_index, note_footnotes),
    ])
    content = "\n".join(content_parts)

    meta = f"Published {date.today().isoformat()} | {len(notes)} notes | {len(source_index)} local references"
    toc_items = [
        '<li><a href="#overview">Topic Brief</a></li>',
        '<li><a href="#evidence">Evidence Profile</a></li>',
        '<li><a href="#map">Knowledge Map</a></li>',
        '<li><a href="#gaps">Gap Radar</a></li>',
    ]
    if trail_html:
        toc_items.append('<li><a href="#trail">Research Trail</a></li>')
    toc_items.extend([
        '<li><a href="#notes">Included Notes</a></li>',
        note_toc,
        '<li><a href="#sources">Sources</a></li>',
    ])
    toc = "\n".join(toc_items)

    html_doc = ATLAS_TEMPLATE.replace("{{TITLE}}", escape(title))
    html_doc = html_doc.replace("{{META}}", escape(meta))
    html_doc = html_doc.replace("{{TOC}}", toc)
    html_doc = html_doc.replace("{{CONTENT}}", content)

    manifest = render_manifest(notes, source_index, edges, topic)
    return html_doc, manifest


def build_atlas_report(
    topic: str,
    kb_name: Optional[str] = None,
    limit: int = 80,
    core_limit: int = 24,
    depth: int = 1,
    include_adjacent: bool = True,
    custom_title: Optional[str] = None,
) -> Tuple[Path, Path]:
    selected_rows, metadata, graph, _ = collect_relevant_notes(
        topic=topic,
        kb_name=kb_name,
        limit=limit,
        core_limit=core_limit,
        depth=depth,
        include_adjacent=include_adjacent,
    )
    notes = load_selected_notes(selected_rows, metadata, kb_name)
    if not notes:
        print(f"No relevant notes found for '{topic}'.", file=sys.stderr)
        sys.exit(1)

    title = custom_title or f"Atlas: {topic}"
    html_doc, manifest = build_html(title, topic, notes, metadata, graph, kb_name)

    PUBLISH.mkdir(exist_ok=True)
    out_slug = slugify(custom_title or topic) + "-atlas"
    html_path = PUBLISH / f"{out_slug}.html"
    json_path = PUBLISH / f"{out_slug}.json"
    html_path.write_text(html_doc)
    json_path.write_text(json.dumps(manifest, indent=2))

    print(f"Published atlas: {html_path} ({len(html_doc) // 1024}KB)")
    print(f"Manifest: {json_path}")
    print(f"Included notes: {len(notes)} | Local references: {len(manifest['sources'])}")
    return html_path, json_path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a topic-centered Knowledge Atlas HTML report.")
    parser.add_argument("topic", nargs="+", help="Topic query to package into an atlas report")
    parser.add_argument("--kb", help="Scope to a specific KB")
    parser.add_argument("--limit", type=int, default=80, help="Maximum included notes (default: 80)")
    parser.add_argument("--core-limit", type=int, default=24, help="Direct search matches before expansion (default: 24)")
    parser.add_argument("--depth", type=int, default=1, help="Graph expansion hops from core notes (default: 1)")
    parser.add_argument("--no-adjacent", action="store_true", help="Skip shared-tag adjacent context")
    parser.add_argument("--title", help="Custom report title")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    topic = " ".join(args.topic).strip()
    if not topic:
        print("Topic is required.", file=sys.stderr)
        sys.exit(1)
    build_atlas_report(
        topic=topic,
        kb_name=args.kb,
        limit=max(1, args.limit),
        core_limit=max(1, args.core_limit),
        depth=max(0, args.depth),
        include_adjacent=not args.no_adjacent,
        custom_title=args.title,
    )


ATLAS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TITLE}}</title>
<style>
:root {
  --bg: #f7f8f5;
  --paper: #ffffff;
  --ink: #202124;
  --muted: #667085;
  --border: #d9dfd2;
  --soft: #eef2ea;
  --accent: #176b87;
  --accent-2: #c76f2e;
  --accent-3: #3f7d58;
  --danger: #a33a3a;
  --shadow: 0 12px 30px rgba(32, 33, 36, 0.08);
}
[data-theme="dark"] {
  --bg: #151815;
  --paper: #20241f;
  --ink: #edf1e8;
  --muted: #aeb8aa;
  --border: #3a4238;
  --soft: #2a3028;
  --accent: #7fc7d9;
  --accent-2: #e6a15f;
  --accent-3: #9bd28f;
  --danger: #ee8b8b;
  --shadow: 0 12px 30px rgba(0, 0, 0, 0.28);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.65;
  font-size: 15px;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  min-height: 100vh;
}
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  border-right: 1px solid var(--border);
  background: var(--soft);
  padding: 24px 18px;
}
.sidebar h2 {
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.toc-list { list-style: none; padding: 0; margin: 0; }
.toc-list li { margin: 2px 0; }
.toc-list a {
  display: block;
  color: var(--ink);
  font-size: 13px;
  line-height: 1.35;
  padding: 6px 8px;
  border-radius: 6px;
}
.toc-list a:hover, .toc-list a.active {
  color: var(--accent);
  background: var(--paper);
  box-shadow: inset 3px 0 0 var(--accent);
  text-decoration: none;
}
.main {
  max-width: 1100px;
  padding: 42px 54px 80px;
}
.header {
  margin-bottom: 28px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
.header h1 {
  font-size: 34px;
  line-height: 1.12;
  margin: 0 0 10px;
}
.meta { color: var(--muted); font-size: 13px; }
.controls {
  position: fixed;
  top: 14px;
  right: 16px;
  display: flex;
  gap: 8px;
  z-index: 10;
}
.controls button {
  border: 1px solid var(--border);
  background: var(--paper);
  color: var(--ink);
  border-radius: 7px;
  padding: 7px 10px;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.atlas-panel {
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 24px;
  margin: 0 0 24px;
  box-shadow: var(--shadow);
}
.panel-kicker {
  color: var(--accent-2);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
  margin-bottom: 4px;
}
.atlas-panel h2 { margin: 0 0 12px; font-size: 22px; }
.atlas-panel h3 { margin: 18px 0 8px; font-size: 15px; }
.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0;
}
.stat-grid div {
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 14px;
  background: var(--soft);
}
.stat-value { display: block; font-size: 26px; font-weight: 750; color: var(--accent); }
.stat-label { color: var(--muted); font-size: 12px; }
.compact-facts {
  display: grid;
  grid-template-columns: 150px 1fr;
  gap: 8px 16px;
  margin: 16px 0;
}
.compact-facts dt { color: var(--muted); font-weight: 700; }
.compact-facts dd { margin: 0; }
.takeaway-list, .gap-list { padding-left: 20px; }
.evidence-grid, .callout-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.callout-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 14px; }
.evidence-grid > div, .callout {
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 14px;
  background: var(--soft);
}
.small-note { color: var(--muted); font-size: 13px; }
.map-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: linear-gradient(180deg, var(--paper), var(--soft));
}
svg { width: 100%; min-width: 760px; display: block; }
.edges line { stroke: var(--border); stroke-width: 1.3; }
.node { stroke: var(--paper); stroke-width: 2.5; cursor: pointer; }
.node.core { fill: var(--accent-2); }
.node.supporting { fill: var(--accent); }
.node.adjacent { fill: var(--accent-3); }
.nodes text { font-size: 11px; fill: var(--ink); pointer-events: none; }
.map-legend {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  color: var(--muted);
  font-size: 13px;
}
.legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 6px;
}
.legend-dot.core { background: var(--accent-2); }
.legend-dot.supporting { background: var(--accent); }
.legend-dot.adjacent { background: var(--accent-3); }
.search-bar {
  position: sticky;
  top: 0;
  z-index: 5;
  padding: 8px 0 14px;
  background: var(--paper);
}
.search-bar input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--soft);
  color: var(--ink);
  font-size: 14px;
}
.tier-heading {
  margin: 30px 0 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 15px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.section {
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  margin: 0 0 14px;
  background: var(--paper);
}
.section-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  padding: 15px 18px;
  cursor: pointer;
  background: var(--soft);
}
.section-header h2 {
  margin: 0 0 5px;
  font-size: 17px;
}
.section.collapsed .section-body { display: none; }
.section.collapsed .toggle { transform: rotate(-90deg); }
.section-body {
  padding: 20px 22px;
  border-top: 1px solid var(--border);
}
.section-body h2 { font-size: 19px; margin-top: 24px; }
.section-body h3 { font-size: 16px; margin-top: 20px; }
.section-body h4 { font-size: 14px; margin-top: 16px; }
.section-body p { margin: 0 0 12px; }
.section-body ul, .section-body ol { padding-left: 22px; }
.section-body table, .source-table {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 13px;
}
.section-body th, .section-body td, .source-table th, .source-table td {
  border: 1px solid var(--border);
  padding: 8px 10px;
  vertical-align: top;
}
.section-body th, .source-table th {
  background: var(--soft);
  text-align: left;
}
code {
  background: var(--soft);
  border: 1px solid var(--border);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}
pre {
  overflow-x: auto;
  background: var(--soft);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}
blockquote {
  border-left: 3px solid var(--accent);
  margin: 12px 0;
  padding: 8px 14px;
  background: var(--soft);
  color: var(--muted);
}
.note-meta, .note-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.note-tags { margin-bottom: 14px; }
.pill, .tag, .source-type {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--paper);
  color: var(--muted);
  font-size: 12px;
  line-height: 1;
  padding: 5px 7px;
}
.tag { background: var(--soft); }
.pill.status { color: var(--accent); }
.source-type.primary { color: var(--accent-3); }
.source-type.secondary { color: var(--accent); }
.source-type.opinion { color: var(--accent-2); }
.source-type.unverified, .source-type.missing { color: var(--danger); }
.wikilink {
  color: var(--accent);
  border-bottom: 1px dashed var(--accent);
}
.footnote-ref {
  color: var(--accent-2);
  font-size: 11px;
  vertical-align: super;
  font-weight: 700;
}
.trail-entry {
  border-top: 1px solid var(--border);
  padding-top: 12px;
  margin-top: 12px;
}
.trail-entry:first-of-type { border-top: none; padding-top: 0; }
.footnotes { font-size: 13px; }
.footnotes li { margin-bottom: 6px; }
@media (max-width: 860px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { display: none; }
  .main { padding: 26px 18px 64px; }
  .controls { position: static; padding: 10px; justify-content: flex-end; background: var(--bg); }
  .stat-grid, .evidence-grid, .callout-grid { grid-template-columns: 1fr; }
  .compact-facts { grid-template-columns: 1fr; }
}
@media print {
  .sidebar, .controls, .search-bar { display: none !important; }
  .layout { display: block; }
  .main { max-width: none; padding: 0; }
  .atlas-panel, .section { box-shadow: none; break-inside: avoid; }
  .section.collapsed .section-body { display: block !important; }
}
</style>
</head>
<body>
<div class="controls">
  <button onclick="toggleAll()" id="toggle-all-btn">Collapse All</button>
  <button onclick="toggleTheme()">Dark/Light</button>
  <button onclick="window.print()">Export PDF</button>
</div>
<div class="layout">
  <aside class="sidebar">
    <h2>Atlas</h2>
    <ul class="toc-list">{{TOC}}</ul>
  </aside>
  <main class="main">
    <header class="header">
      <h1>{{TITLE}}</h1>
      <div class="meta">{{META}}</div>
    </header>
    {{CONTENT}}
  </main>
</div>
<script>
document.querySelectorAll('.section-header').forEach(function(header) {
  header.addEventListener('click', function() {
    header.parentElement.classList.toggle('collapsed');
  });
});
let allCollapsed = false;
function toggleAll() {
  allCollapsed = !allCollapsed;
  document.querySelectorAll('.section').forEach(function(section) {
    section.classList.toggle('collapsed', allCollapsed);
  });
  document.getElementById('toggle-all-btn').textContent = allCollapsed ? 'Expand All' : 'Collapse All';
}
function toggleTheme() {
  const root = document.documentElement;
  root.setAttribute('data-theme', root.getAttribute('data-theme') === 'dark' ? '' : 'dark');
}
function filterSections(query) {
  const q = query.toLowerCase();
  document.querySelectorAll('.note-section').forEach(function(section) {
    const text = (section.textContent + ' ' + section.getAttribute('data-search')).toLowerCase();
    section.style.display = text.includes(q) ? '' : 'none';
  });
}
document.querySelectorAll('.wikilink').forEach(function(link) {
  link.addEventListener('click', function(event) {
    const target = link.getAttribute('data-target');
    const section = target ? document.getElementById(target) : null;
    if (section) {
      event.preventDefault();
      section.classList.remove('collapsed');
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
});
const observer = new IntersectionObserver(function(entries) {
  entries.forEach(function(entry) {
    const link = document.querySelector('.toc-list a[href="#' + entry.target.id + '"]');
    if (link) link.classList.toggle('active', entry.isIntersecting);
  });
}, { threshold: 0.1 });
document.querySelectorAll('section[id], article[id]').forEach(function(section) {
  observer.observe(section);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
