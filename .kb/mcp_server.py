#!/usr/bin/env python3
"""MCP server exposing KB read operations as tools.

Supports multiple knowledge bases. Each tool accepts an optional `kb` parameter
to target a specific KB.  When omitted, tools search the unified index (all
non-private KBs) or the default KB, depending on the operation.

Run via: uv run --directory .kb mcp_server.py
Or configure in Claude Code settings as an MCP server.
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# Ensure kb-index.py is importable
KB_DIR = Path(__file__).parent
BASE = KB_DIR.parent
sys.path.insert(0, str(KB_DIR))

# Import kb-index as a module (handles the hyphen in filename)
import importlib.util
spec = importlib.util.spec_from_file_location("kb_index", KB_DIR / "kb-index.py")
kb_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kb_mod)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Knowledge Base",
    instructions="Personal knowledge base with multiple KBs of atomic markdown notes. "
    "Search, explore, and navigate topics across scam detection, AI agents, "
    "blockchain security, compliance, and more. "
    "Use `kb_list()` to see available knowledge bases. "
    "All tools accept an optional `kb` parameter to target a specific KB; "
    "omit it to search the unified index across all non-private KBs.",
)

# ---------------------------------------------------------------------------
# Auto-rebuild: check if index is stale before any read
# ---------------------------------------------------------------------------

_last_check = None


def _ensure_index_fresh():
    """Rebuild index if notes have changed since last build."""
    global _last_check
    now = datetime.now()

    # Only check once per 30 seconds to avoid hammering the filesystem
    if _last_check and (now - _last_check).seconds < 30:
        return
    _last_check = now

    registry = kb_mod.get_registry()
    needs_rebuild = False

    for kbc in registry.all_kbs():
        meta_file = kbc.meta_file
        if not meta_file.exists():
            needs_rebuild = True
            break

        # Compare newest note mtime against index build time
        meta_mtime = meta_file.stat().st_mtime
        notes = list(kbc.notes_dir.glob("*.md"))
        if not notes:
            continue

        newest_note = max(p.stat().st_mtime for p in notes)
        if newest_note > meta_mtime:
            needs_rebuild = True
            break

    if needs_rebuild:
        kb_mod.build_index()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def kb_list() -> str:
    """List all available knowledge bases with name, note count, and privacy flag."""
    registry = kb_mod.get_registry()
    lines = []
    for kbc in registry.all_kbs():
        notes = list(kbc.notes_dir.glob("*.md")) if kbc.notes_dir.exists() else []
        privacy = " [PRIVATE]" if kbc.private else ""
        default = " [DEFAULT]" if kbc.default else ""
        lines.append(f"  {kbc.name}{default}{privacy} — {len(notes)} notes ({kbc.notes_dir})")
    return f"Available KBs ({len(lines)}):\n" + "\n".join(lines)


@mcp.tool()
def kb_search(query: str, reformulations: list[str] | None = None, tags: str | None = None, type: str | None = None, kb: str | None = None) -> str:
    """Search the knowledge base using hybrid TF-IDF + graph expansion.

    Args:
        query: The search query
        reformulations: Optional alternative phrasings for multi-query fusion (improves recall)
        tags: Comma-separated tag filter (e.g. "scam-detection,fraud")
        type: Note type filter (concept, question, insight, reference, synthesis)
        kb: Target a specific KB by name; omit to search unified index
    """
    _ensure_index_fresh()
    tag_list = tags.split(",") if tags else None

    if reformulations:
        all_queries = [query] + reformulations
        results = kb_mod.multi_search(all_queries, tags=tag_list, note_type=type, kb_name=kb)
    else:
        results = kb_mod.search(query, tags=tag_list, note_type=type, kb_name=kb)

    if not results:
        return "No results found. Try different keywords or check `kb_map()` for available topics."

    lines = [f"Found {len(results)} results:\n"]
    for r in results:
        dep = " [DEPRECATED]" if r.get("deprecated") else ""
        lines.append(f"  {r['score']:.4f}  {r['slug']}{dep}")
        lines.append(f"           {r['title']} ({r['type']}) [{', '.join(r.get('tags', [])[:5])}]")
    return "\n".join(lines)


@mcp.tool()
def kb_quick(query: str, kb: str | None = None) -> str:
    """Fast title/slug/tag lookup — no TF-IDF, instant results.

    Use for specific terms, note names, or concept lookups.
    Falls back to full search if no matches found.

    Args:
        query: The term or concept to look up
        kb: Target a specific KB by name; omit to search all KB metadata
    """
    _ensure_index_fresh()
    results = kb_mod.quick_search(query, kb_name=kb)

    if not results:
        return f"No quick matches for '{query}'. Use kb_search() for full semantic search."

    lines = [f"Quick matches ({len(results)}):\n"]
    for r in results:
        lines.append(f"  {r['score']:.2f}  {r['slug']}")
        lines.append(f"        {r['title']} ({r['type']}) matched: {', '.join(r['match'])}")
    return "\n".join(lines)


@mcp.tool()
def kb_read(slug: str, kb: str | None = None) -> str:
    """Read the full content of a specific note.

    Args:
        slug: The note slug (filename without .md), e.g. "tcp-congestion-control"
        kb: Target a specific KB by name; omit to search all non-private KBs
    """
    registry = kb_mod.get_registry()

    if kb:
        kbc = registry.get(kb)
        if not kbc:
            return f"Unknown KB '{kb}'. Available: {', '.join(registry.all_kb_names())}"
        note_path = kbc.notes_dir / f"{slug}.md"
        if not note_path.exists():
            return f"Note '{slug}' not found in KB '{kb}'. Use kb_search() or kb_quick() to find notes."
        return note_path.read_text()

    # Search all non-private KBs for the slug
    for kbc in registry.searchable_kbs():
        note_path = kbc.notes_dir / f"{slug}.md"
        if note_path.exists():
            return note_path.read_text()

    return f"Note '{slug}' not found. Use kb_search() or kb_quick() to find notes."


@mcp.tool()
def kb_map(kb: str | None = None) -> str:
    """Show the topic map — all knowledge clusters with note counts, type distribution, and link density.

    Args:
        kb: Target a specific KB by name; omit for unified view
    """
    _ensure_index_fresh()
    tmap = kb_mod.topic_map(kb_name=kb)

    if not tmap:
        return "No topic map available. The KB may be empty."

    lines = [f"Topic Map ({len(tmap)} topics):\n"]
    for tag, data in sorted(tmap.items(), key=lambda x: -x[1]["count"]):
        types_str = ", ".join(f"{t}:{c}" for t, c in sorted(data["types"].items()))
        lines.append(f"  {tag} ({data['count']} notes, ~{data['avg_words']} words/note)")
        lines.append(f"    types: {types_str}")
        lines.append(f"    density: {data['link_density']:.1%}")
        lines.append(f"    tags: {', '.join(data['tags'][:5])}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def kb_explore(slug: str, max_steps: int = 5, kb: str | None = None) -> str:
    """Suggest a reading path starting from a note, following the most relevant links.

    Args:
        slug: Starting note slug
        max_steps: Maximum number of notes in the path (default 5)
        kb: Target a specific KB by name; omit for unified view
    """
    _ensure_index_fresh()
    path = kb_mod.explore_path(slug, max_steps, kb_name=kb)

    if not path:
        return f"No path from '{slug}'. The note may not exist or have no links."

    # Load metadata for display
    registry = kb_mod.get_registry()
    if kb:
        kbc = registry.get(kb)
        meta_file = kbc.meta_file if kbc else None
    else:
        meta_file = kb_mod.UNIFIED_DIR / "metadata.json"

    metadata = {}
    if meta_file and meta_file.exists():
        metadata = json.loads(meta_file.read_text())

    lines = [f"Reading path from '{slug}' ({len(path)} notes):\n"]
    for i, s in enumerate(path):
        meta = metadata.get(s, {})
        arrow = "  ->" if i > 0 else "    "
        lines.append(f"  {arrow} {i+1}. [{meta.get('type', '')}] {meta.get('title', s)}")
        lines.append(f"       {s} ({meta.get('word_count', '?')} words)")
    return "\n".join(lines)


@mcp.tool()
def kb_gaps(kb: str | None = None) -> str:
    """Find thin or weak topic areas in the KB that need more coverage.

    Args:
        kb: Target a specific KB by name; omit for unified view
    """
    _ensure_index_fresh()
    gaps = kb_mod.find_topic_gaps(kb_name=kb)

    if not gaps:
        return "No significant topic gaps found."

    lines = [f"Topic gaps ({len(gaps)}):\n"]
    for g in gaps:
        lines.append(f"  {g['topic']} ({g['count']} notes)")
        for issue in g["issues"]:
            lines.append(f"    - {issue}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def kb_stats(kb: str | None = None) -> str:
    """Show KB index statistics: note counts, types, features, embeddings, graph, top tags.

    Args:
        kb: Target a specific KB by name; omit for unified stats
    """
    _ensure_index_fresh()

    registry = kb_mod.get_registry()
    if kb:
        kbc = registry.get(kb)
        if not kbc:
            return f"Unknown KB '{kb}'. Available: {', '.join(registry.all_kb_names())}"
        meta_file = kbc.meta_file
        index_file = kbc.index_file
        graph_file = kbc.graph_file
    else:
        meta_file = kb_mod.UNIFIED_DIR / "metadata.json"
        index_file = kb_mod.UNIFIED_DIR / "tfidf_index.json"
        graph_file = kb_mod.UNIFIED_DIR / "graph.json"

    if not meta_file.exists():
        return "No index. The KB may be empty."

    metadata = json.loads(meta_file.read_text())
    index_data = json.loads(index_file.read_text())

    types = {}
    all_tags = {}
    total_words = 0
    deprecated = 0

    for slug, meta in metadata.items():
        t = meta.get("type", "unknown")
        types[t] = types.get(t, 0) + 1
        for tag in meta.get("tags", []):
            all_tags[tag] = all_tags.get(tag, 0) + 1
        total_words += meta.get("word_count", 0)
        if meta.get("deprecated_by"):
            deprecated += 1

    total = len(metadata)
    label = f"KB: {kb}" if kb else "Unified (all KBs)"
    lines = [
        label,
        f"Notes: {total}",
        f"Features: {index_data.get('feature_count', 'unknown')}",
        f"Total words: {total_words:,}",
        f"Deprecated: {deprecated}",
        f"Types:",
    ]
    for t in sorted(types.keys()):
        count = types[t]
        pct = count / total * 100 if total else 0
        lines.append(f"  {t:12s} {count:4d} ({pct:4.1f}%)")

    if graph_file.exists():
        g = json.loads(graph_file.read_text())
        lines.append(f"Link graph: {g['node_count']} nodes, {g['edge_count']} edges, {g['orphan_count']} orphans")

    top_tags = dict(sorted(all_tags.items(), key=lambda x: -x[1])[:15])
    lines.append(f"Top tags: {json.dumps(top_tags)}")

    return "\n".join(lines)


@mcp.tool()
def kb_coverage(topic: str, kb: str | None = None) -> str:
    """Check if the KB has adequate coverage of a topic.

    Returns coverage level (well-covered, partially-covered, not-covered)
    with confidence score and matching notes.

    Args:
        topic: The topic to check coverage for
        kb: Target a specific KB by name; omit for unified check
    """
    _ensure_index_fresh()
    result = kb_mod.check_coverage(topic, kb_name=kb)

    lines = [f"Coverage: {result.get('level', 'unknown')} (confidence: {result.get('confidence', 0)})"]
    if result.get("notes"):
        lines.append("Relevant notes:")
        for n in result["notes"]:
            lines.append(f"  {n['score']:.4f}  {n['slug']}")
    if not result.get("covered"):
        lines.append(f"\nThe KB does not have strong coverage on '{topic}'.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
