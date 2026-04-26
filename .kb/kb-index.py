#!/usr/bin/env python3
"""KB search index: build, query, lint, and maintain.

Usage:
  python3 .kb/kb-index.py build                    # Build/rebuild TF-IDF index
  python3 .kb/kb-index.py build --embed            # Also build dense embeddings
  python3 .kb/kb-index.py build --incremental      # Only re-index changed notes
  python3 .kb/kb-index.py search "query text"      # Hybrid search (top 10)
  python3 .kb/kb-index.py search "query" --tags security,rag  # With tag filter
  python3 .kb/kb-index.py search "query" --type concept       # With type filter
  python3 .kb/kb-index.py search "query" --multi "alt query 1" "alt query 2"  # Multi-query fusion
  python3 .kb/kb-index.py similar note-slug         # Find similar notes
  python3 .kb/kb-index.py stale                     # Find temporally stale notes
  python3 .kb/kb-index.py stale-syntheses           # Find synthesis notes with updated dependencies
  python3 .kb/kb-index.py contradictions note-slug  # Find potentially conflicting notes
  python3 .kb/kb-index.py coverage "topic"          # Check if KB covers a topic
  python3 .kb/kb-index.py stats                     # Index statistics
  python3 .kb/kb-index.py clusters                  # Show topic clusters
  python3 .kb/kb-index.py lint                      # Validate all notes
  python3 .kb/kb-index.py lint note-slug            # Validate a single note
  python3 .kb/kb-index.py graph                     # Show graph summary
  python3 .kb/kb-index.py graph orphans             # Notes with no links in or out
  python3 .kb/kb-index.py graph components          # Disconnected subgraphs
  python3 .kb/kb-index.py graph neighbors slug [n]  # Notes within n hops (default 2)
  python3 .kb/kb-index.py graph bridges             # Notes whose removal disconnects components
  python3 .kb/kb-index.py backlink [slug]             # Add missing reverse wikilinks (all or one note)
  python3 .kb/kb-index.py quick "query"              # Fast title/slug/tag match (no TF-IDF)
  python3 .kb/kb-index.py feedback summary           # Show accumulated search feedback
  python3 .kb/kb-index.py feedback log "q" "type" "expected_slugs"  # Log a feedback entry
  python3 .kb/kb-index.py map                        # Topic map with coverage stats
  python3 .kb/kb-index.py explore <slug> [steps]     # Suggested reading path from a note
  python3 .kb/kb-index.py gaps                       # Find thin/weak topic areas
  python3 .kb/kb-index.py eval                       # Run retrieval evaluation
  python3 .kb/kb-index.py eval generation            # Run generation (faithfulness) evaluation
  python3 .kb/kb-index.py eval all --verbose         # Run all evaluations with details

Dense embeddings require OPENAI_API_KEY or VOYAGE_API_KEY in environment.
Falls back to TF-IDF-only when no API key is available.
"""
import json
import hashlib
import sys
import re
import warnings
import yaml
import numpy as np

warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from datetime import date, datetime
from collections import defaultdict
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

BASE = Path(__file__).parent.parent
NOTES = BASE / "notes"
REFS = BASE / "references"
INDEX_DIR = BASE / ".kb" / "index"
INDEX_FILE = INDEX_DIR / "tfidf_index.json"
VECTORS_FILE = INDEX_DIR / "tfidf_vectors.npz"
VECTORIZER_FILE = INDEX_DIR / "vectorizer.pkl"
EMBEDDINGS_FILE = INDEX_DIR / "dense_embeddings.npz"
META_FILE = INDEX_DIR / "metadata.json"
CLUSTERS_FILE = INDEX_DIR / "clusters.json"
GRAPH_FILE = INDEX_DIR / "graph.json"
CONFIG_FILE = BASE / ".kb" / "config.yaml"
TAXONOMY_FILE = BASE / ".kb" / "taxonomy.yaml"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_config_cache = None

def load_config():
    """Load config.yaml with defaults for every key."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    defaults = {
        "search": {"rrf_constant": 25, "min_score": 0.01, "cluster_boost_scale": 0.15,
                    "temporal_expired_penalty": 0.3, "deprecated_penalty": 0.2,
                    "title_boost": 2.0, "tag_boost": 1.5},
        "coverage": {"well_covered_min_score": 0.15, "well_covered_min_notes": 3,
                      "partial_min_score": 0.08, "partial_min_notes": 1, "relevance_floor": 0.05},
        "similarity": {"min_score": 0.05, "contradiction_overlap": 0.15,
                        "contradiction_high": 0.4, "dedup_threshold": 0.5},
        "clusters": {"min_tag_notes": 5, "merge_overlap": 0.6},
        "staleness": {"default_days": 180},
        "indexing": {"max_features": 10000, "ngram_range": [1, 2], "min_df": 1,
                      "max_df": 0.95, "vocab_drift_threshold": 0.1},
        "graph": {"hub_threshold": 3},
    }

    if CONFIG_FILE.exists():
        try:
            raw = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        except yaml.YAMLError:
            raw = {}
    else:
        raw = {}

    # Deep merge: config values override defaults
    cfg = {}
    for section, section_defaults in defaults.items():
        cfg[section] = dict(section_defaults)
        if section in raw and isinstance(raw[section], dict):
            cfg[section].update(raw[section])

    # Top-level keys
    for k in ("notes_dir", "naming", "note_types", "web_search"):
        if k in raw:
            cfg[k] = raw[k]

    _config_cache = cfg
    return cfg


def cfg(section, key):
    """Shorthand: cfg('search', 'rrf_constant') → 60"""
    return load_config()[section][key]


# ---------------------------------------------------------------------------
# Note parsing
# ---------------------------------------------------------------------------

def parse_note(filepath):
    """Parse note into frontmatter dict and body string."""
    content = filepath.read_text()
    if not content.startswith("---"):
        return {}, content, hashlib.md5(content.encode()).hexdigest()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content, hashlib.md5(content.encode()).hexdigest()
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    body = parts[2].strip()
    content_hash = hashlib.md5(content.encode()).hexdigest()
    return fm, body, content_hash


def extract_wikilinks(text):
    """Extract all [[target]] wikilinks from text (body or frontmatter related list)."""
    return re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]*?)?\]\]', text)


def extract_contextual_text(fm, body):
    """Prepend metadata context to body for better embedding (Anthropic contextual retrieval approach)."""
    parts = []
    if fm.get("title"):
        parts.append(f"Title: {fm['title']}")
    if fm.get("type"):
        parts.append(f"Type: {fm['type']}")
    if fm.get("tags"):
        parts.append(f"Tags: {', '.join(fm['tags'])}")
    clean_body = re.sub(r'\[\[([^\]|]+)\|?([^\]]*)\]\]', lambda m: m.group(2) or m.group(1), body)
    clean_body = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean_body)
    clean_body = re.sub(r'[#*`>|]', ' ', clean_body)
    clean_body = re.sub(r'\s+', ' ', clean_body).strip()
    parts.append(clean_body)
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Build index
# ---------------------------------------------------------------------------

def build_index(incremental=False):
    """Build TF-IDF index over all notes with contextual metadata prepending.

    If incremental=True, compare content hashes against existing metadata and
    only re-process changed/added notes.  Falls back to full rebuild when the
    vocabulary drifts beyond the configured threshold.
    """
    notes = sorted(NOTES.glob("*.md"))
    if not notes:
        print("No notes found.")
        return

    # --- Incremental: detect changes ---
    old_metadata = {}
    changed_slugs = set()
    deleted_slugs = set()
    if incremental and META_FILE.exists() and VECTORS_FILE.exists() and VECTORIZER_FILE.exists():
        old_metadata = json.loads(META_FILE.read_text())
        current_slugs = {p.stem for p in notes}
        deleted_slugs = set(old_metadata.keys()) - current_slugs

        for path in notes:
            slug = path.stem
            _, _, content_hash = parse_note(path)
            old_hash = old_metadata.get(slug, {}).get("content_hash")
            if old_hash != content_hash:
                changed_slugs.add(slug)

        if not changed_slugs and not deleted_slugs:
            print("Index is up to date (no changes detected).")
            return

        # Check if vocabulary would drift too much — if so, full rebuild
        new_count = len(changed_slugs - set(old_metadata.keys()))
        drift = new_count / max(len(old_metadata), 1)
        if drift > cfg("indexing", "vocab_drift_threshold"):
            print(f"Vocabulary drift {drift:.0%} exceeds threshold — full rebuild.")
            incremental = False
        else:
            print(f"Incremental: {len(changed_slugs)} changed, {len(deleted_slugs)} deleted, "
                  f"{len(current_slugs) - len(changed_slugs)} unchanged.")

    slugs = []
    texts = []
    metadata = {}

    for path in notes:
        slug = path.stem
        fm, body, content_hash = parse_note(path)
        contextual_text = extract_contextual_text(fm, body)

        # Extract all wikilinks (body + related frontmatter)
        body_links = extract_wikilinks(body)
        fm_links = []
        for r in fm.get("related", []):
            fm_links.extend(extract_wikilinks(str(r)))

        slugs.append(slug)
        texts.append(contextual_text)
        metadata[slug] = {
            "title": fm.get("title", slug),
            "type": fm.get("type", "unknown"),
            "tags": fm.get("tags", []),
            "created": str(fm.get("created", "")),
            "updated": str(fm.get("updated", "")),
            "valid_from": str(fm.get("valid_from", "")),
            "valid_until": fm.get("valid_until"),
            "deprecated_by": fm.get("deprecated_by"),
            "related": list(dict.fromkeys(fm_links)),  # deduplicated, ordered
            "outgoing_links": list(dict.fromkeys(body_links + fm_links)),
            "content_hash": content_hash,
            "word_count": len(body.split()),
            "depends_on": fm.get("depends_on", []),  # for synthesis notes
        }

    # Build TF-IDF matrix
    icfg = load_config()["indexing"]
    vectorizer = TfidfVectorizer(
        max_features=icfg["max_features"],
        ngram_range=tuple(icfg["ngram_range"]),
        min_df=icfg["min_df"],
        max_df=icfg["max_df"],
        sublinear_tf=True,
        stop_words="english",
    )
    tfidf_matrix = vectorizer.fit_transform(texts)

    # Save index
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(VECTORS_FILE,
                        vectors=tfidf_matrix.toarray(),
                        slugs=np.array(slugs))

    import pickle
    with open(VECTORIZER_FILE, "wb") as f:
        pickle.dump(vectorizer, f)

    index_data = {
        "slugs": slugs,
        "built_at": datetime.now().isoformat(),
        "note_count": len(slugs),
        "feature_count": len(vectorizer.vocabulary_),
    }
    INDEX_FILE.write_text(json.dumps(index_data, indent=2))
    META_FILE.write_text(json.dumps(metadata, indent=2))

    # Build topic clusters + link graph
    build_clusters(slugs, metadata)
    build_graph(slugs, metadata)

    print(f"Index built: {len(slugs)} notes, {len(vectorizer.vocabulary_)} features")
    return tfidf_matrix, vectorizer, slugs, metadata


# ---------------------------------------------------------------------------
# Dense embeddings
# ---------------------------------------------------------------------------

def detect_embedding_provider():
    """Auto-detect the best available embedding provider."""
    import os
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        return "local", "sentence-transformers (all-MiniLM-L6-v2)"
    except ImportError:
        pass
    if os.environ.get("VOYAGE_API_KEY"):
        return "voyage", "Voyage AI (voyage-3-lite)"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", "OpenAI (text-embedding-3-small)"
    return None, "No provider available. Options: pip install sentence-transformers, or set VOYAGE_API_KEY/OPENAI_API_KEY"


def build_dense_embeddings(slugs, texts):
    """Build dense embeddings using best available provider."""
    provider, detail = detect_embedding_provider()
    if provider is None:
        print(f"Dense embeddings: skipped ({detail})")
        return

    print(f"Dense embeddings: using {detail}")
    try:
        if provider == "local":
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)
            np.savez_compressed(EMBEDDINGS_FILE,
                                embeddings=np.array(embeddings),
                                slugs=np.array(slugs))
            print(f"Dense embeddings built: {len(embeddings)} vectors, {embeddings.shape[1]} dims (local)")

        elif provider in ("openai", "voyage"):
            import os, urllib.request
            if provider == "voyage":
                api_key = os.environ["VOYAGE_API_KEY"]
                url = "https://api.voyageai.com/v1/embeddings"
                model = "voyage-3-lite"
            else:
                api_key = os.environ["OPENAI_API_KEY"]
                url = "https://api.openai.com/v1/embeddings"
                model = "text-embedding-3-small"

            embeddings = []
            for i in range(0, len(texts), 20):
                batch = texts[i:i+20]
                req = urllib.request.Request(url,
                    data=json.dumps({"input": batch, "model": model}).encode(),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req) as resp:
                    result = json.loads(resp.read())
                    for item in result["data"]:
                        embeddings.append(item["embedding"])
                print(f"  Embedded {min(i+20, len(texts))}/{len(texts)}")

            np.savez_compressed(EMBEDDINGS_FILE,
                                embeddings=np.array(embeddings),
                                slugs=np.array(slugs))
            print(f"Dense embeddings built: {len(embeddings)} vectors, {len(embeddings[0])} dims ({provider})")

    except Exception as e:
        print(f"Dense embedding failed: {e}")


# ---------------------------------------------------------------------------
# Clusters
# ---------------------------------------------------------------------------

def build_clusters(slugs, metadata):
    """Build topic clusters from tag co-occurrence."""
    min_notes = cfg("clusters", "min_tag_notes")
    merge_thresh = cfg("clusters", "merge_overlap")

    tag_to_slugs = {}
    for slug in slugs:
        for tag in metadata.get(slug, {}).get("tags", []):
            tag_to_slugs.setdefault(tag, []).append(slug)

    major_tags = {t: s for t, s in tag_to_slugs.items() if len(s) >= min_notes}

    clusters = {}
    for tag, tag_slugs in sorted(major_tags.items(), key=lambda x: -len(x[1])):
        tag_set = set(tag_slugs)
        merged = False
        for cname, cdata in clusters.items():
            overlap = len(tag_set & cdata["slugs"]) / min(len(tag_set), len(cdata["slugs"]))
            if overlap > merge_thresh:
                cdata["slugs"] |= tag_set
                cdata["tags"].append(tag)
                merged = True
                break
        if not merged:
            clusters[tag] = {"slugs": tag_set, "tags": [tag], "count": len(tag_slugs)}

    result = {}
    for primary_tag, cdata in clusters.items():
        result[primary_tag] = {
            "tags": cdata["tags"][:5],
            "count": len(cdata["slugs"]),
            "sample_notes": sorted(cdata["slugs"])[:5],
        }

    CLUSTERS_FILE.write_text(json.dumps(result, indent=2))
    print(f"Clusters built: {len(result)} topic clusters")


# ---------------------------------------------------------------------------
# Link graph
# ---------------------------------------------------------------------------

def build_graph(slugs, metadata):
    """Build a directed adjacency list from wikilinks and save as graph.json."""
    slug_set = set(slugs)
    adjacency = {}  # slug → {outgoing: [...], incoming: [...]}

    for slug in slugs:
        adjacency[slug] = {"outgoing": [], "incoming": []}

    for slug in slugs:
        targets = metadata.get(slug, {}).get("outgoing_links", [])
        seen = set()
        for target in targets:
            if target in slug_set and target != slug and target not in seen:
                adjacency[slug]["outgoing"].append(target)
                adjacency[target]["incoming"].append(slug)
                seen.add(target)

    # Compute summary stats
    orphans = [s for s in slugs if not adjacency[s]["outgoing"] and not adjacency[s]["incoming"]]
    total_edges = sum(len(adjacency[s]["outgoing"]) for s in slugs)

    graph_data = {
        "built_at": datetime.now().isoformat(),
        "node_count": len(slugs),
        "edge_count": total_edges,
        "orphan_count": len(orphans),
        "adjacency": adjacency,
    }

    GRAPH_FILE.write_text(json.dumps(graph_data, indent=2))
    print(f"Graph built: {len(slugs)} nodes, {total_edges} edges, {len(orphans)} orphans")


def load_graph():
    """Load graph.json."""
    if not GRAPH_FILE.exists():
        print("Graph not found. Run: python3 .kb/kb-index.py build", file=sys.stderr)
        sys.exit(1)
    return json.loads(GRAPH_FILE.read_text())


def graph_orphans():
    """Notes with no incoming or outgoing links."""
    g = load_graph()
    adj = g["adjacency"]
    orphans = [s for s in adj if not adj[s]["outgoing"] and not adj[s]["incoming"]]
    return sorted(orphans)


def graph_components():
    """Find connected components (treating graph as undirected)."""
    g = load_graph()
    adj = g["adjacency"]
    all_nodes = set(adj.keys())
    visited = set()
    components = []

    def bfs(start):
        queue = [start]
        component = set()
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            for neighbor in adj.get(node, {}).get("outgoing", []) + adj.get(node, {}).get("incoming", []):
                if neighbor not in visited and neighbor in all_nodes:
                    queue.append(neighbor)
        return component

    for node in sorted(all_nodes):
        if node not in visited:
            comp = bfs(node)
            components.append(sorted(comp))

    return sorted(components, key=lambda c: -len(c))


def graph_neighbors(slug, hops=2):
    """Find all notes within n hops of a given note."""
    g = load_graph()
    adj = g["adjacency"]
    if slug not in adj:
        return {}

    visited = {slug: 0}
    frontier = [slug]
    for depth in range(1, hops + 1):
        next_frontier = []
        for node in frontier:
            for neighbor in adj.get(node, {}).get("outgoing", []) + adj.get(node, {}).get("incoming", []):
                if neighbor not in visited and neighbor in adj:
                    visited[neighbor] = depth
                    next_frontier.append(neighbor)
        frontier = next_frontier

    del visited[slug]  # exclude the starting node
    return visited


def graph_bridges():
    """Find bridge notes whose removal would increase the number of connected components."""
    g = load_graph()
    adj = g["adjacency"]
    all_nodes = set(adj.keys())

    base_components = len(graph_components())
    bridges = []

    for node in sorted(all_nodes):
        # Skip orphans and low-connectivity nodes
        degree = len(adj[node]["outgoing"]) + len(adj[node]["incoming"])
        if degree < 2:
            continue

        # Simulate removal
        visited = set()
        remaining = all_nodes - {node}
        comp_count = 0

        def bfs(start):
            queue = [start]
            while queue:
                n = queue.pop(0)
                if n in visited:
                    continue
                visited.add(n)
                for nb in adj.get(n, {}).get("outgoing", []) + adj.get(n, {}).get("incoming", []):
                    if nb not in visited and nb in remaining:
                        queue.append(nb)

        for n in sorted(remaining):
            if n not in visited:
                bfs(n)
                comp_count += 1

        if comp_count > base_components:
            bridges.append({"slug": node, "components_after_removal": comp_count})

    return bridges


# ---------------------------------------------------------------------------
# Linter
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"title", "tags", "created", "type"}
VALID_TYPES = {"question", "concept", "reference", "insight", "synthesis"}

def lint_note(filepath, taxonomy_tags=None, all_slugs=None):
    """Validate a single note. Returns list of (severity, message) tuples."""
    issues = []
    slug = filepath.stem

    # Read raw content
    try:
        content = filepath.read_text()
    except Exception as e:
        return [("error", f"Cannot read file: {e}")]

    # Check frontmatter exists
    if not content.startswith("---"):
        issues.append(("error", "Missing YAML frontmatter (file must start with ---)"))
        return issues

    parts = content.split("---", 2)
    if len(parts) < 3:
        issues.append(("error", "Malformed frontmatter (needs opening and closing ---)"))
        return issues

    # Parse YAML
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        issues.append(("error", f"Invalid YAML in frontmatter: {e}"))
        return issues

    body = parts[2].strip()

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in fm:
            issues.append(("error", f"Missing required field: {field}"))

    # Type validation
    note_type = fm.get("type")
    if note_type and note_type not in VALID_TYPES:
        issues.append(("warning", f"Unknown note type '{note_type}' (expected: {', '.join(sorted(VALID_TYPES))})"))

    # Tags should be a list
    tags = fm.get("tags")
    if tags is not None and not isinstance(tags, list):
        issues.append(("error", f"'tags' must be a list, got {type(tags).__name__}"))
    elif isinstance(tags, list) and taxonomy_tags is not None:
        unknown = set(tags) - taxonomy_tags
        if unknown:
            issues.append(("warning", f"Tags not in taxonomy.yaml: {', '.join(sorted(unknown))}"))

    # Date validation
    for date_field in ("created", "updated", "valid_from"):
        val = fm.get(date_field)
        if val is not None:
            try:
                if isinstance(val, str):
                    date.fromisoformat(val)
                elif not isinstance(val, date):
                    issues.append(("warning", f"'{date_field}' is not a valid date: {val}"))
            except ValueError:
                issues.append(("warning", f"'{date_field}' has invalid date format: {val}"))

    # Wikilink targets
    if all_slugs is not None:
        links = extract_wikilinks(body)
        fm_related = fm.get("related", [])
        for r in fm_related:
            links.extend(extract_wikilinks(str(r)))
        for target in set(links):
            if target not in all_slugs:
                issues.append(("warning", f"Broken wikilink: [[{target}]] (note does not exist)"))

    # Citation paths
    ref_citations = re.findall(r'\[([^\]]+)\]\((\.\.\/references\/[^)]+|references\/[^)]+)\)', body)
    for text, ref_path in ref_citations:
        full_path = REFS / ref_path.replace("../references/", "").replace("references/", "")
        if not full_path.exists():
            issues.append(("warning", f"Broken citation: [{text}]({ref_path}) (file not found)"))

    # Sources frontmatter paths
    for src in fm.get("sources", []):
        if isinstance(src, str) and ("references/" in src):
            full_path = REFS / src.replace("../references/", "").replace("references/", "")
            if not full_path.exists():
                issues.append(("warning", f"Source file not found: {src}"))

    # Synthesis dependency tracking
    if note_type == "synthesis":
        depends = fm.get("depends_on", [])
        if not depends:
            issues.append(("info", "Synthesis note has no 'depends_on' field — cannot track staleness"))
        elif all_slugs is not None:
            for dep in depends:
                if dep not in all_slugs:
                    issues.append(("warning", f"Dependency not found: {dep}"))

    # Filename convention
    if slug != slug.lower():
        issues.append(("warning", f"Filename should be lowercase: {slug}"))
    if " " in slug:
        issues.append(("error", f"Filename contains spaces: {slug}"))

    # Body checks
    if not body:
        issues.append(("warning", "Note body is empty"))
    elif len(body.split()) < 10:
        issues.append(("info", "Note body is very short (< 10 words)"))

    return issues


def lint_all(target_slug=None):
    """Lint all notes (or a single one). Returns dict of slug → issues."""
    # Load taxonomy tags
    taxonomy_tags = set()
    if TAXONOMY_FILE.exists():
        try:
            tax = yaml.safe_load(TAXONOMY_FILE.read_text()) or {}
            taxonomy_tags = set(tax.get("tags", {}).keys())
        except yaml.YAMLError:
            pass

    all_slugs = {p.stem for p in NOTES.glob("*.md")}

    if target_slug:
        filepath = NOTES / f"{target_slug}.md"
        if not filepath.exists():
            return {target_slug: [("error", f"Note file not found: {filepath}")]}
        return {target_slug: lint_note(filepath, taxonomy_tags, all_slugs)}

    results = {}
    for path in sorted(NOTES.glob("*.md")):
        issues = lint_note(path, taxonomy_tags, all_slugs)
        if issues:
            results[path.stem] = issues

    return results


# ---------------------------------------------------------------------------
# Auto-backlinks
# ---------------------------------------------------------------------------

FEEDBACK_FILE = INDEX_DIR / "feedback.jsonl"

def auto_backlink(target_slugs=None):
    """Scan notes for outgoing [[wikilinks]] and add missing reverse links.

    If target_slugs is provided, only process those notes.
    Otherwise, scan all notes.
    Returns list of (source, target, action) tuples.
    """
    all_notes = {p.stem: p for p in sorted(NOTES.glob("*.md"))}
    changes = []

    # Build outgoing link map
    if target_slugs:
        notes_to_scan = {s: all_notes[s] for s in target_slugs if s in all_notes}
    else:
        notes_to_scan = all_notes

    for slug, path in notes_to_scan.items():
        fm, body, _ = parse_note(path)
        outgoing = set(extract_wikilinks(body))
        # Also extract from related frontmatter
        for r in fm.get("related", []):
            outgoing.update(extract_wikilinks(str(r)))

        for target in outgoing:
            if target == slug or target not in all_notes:
                continue

            # Check if target note has a backlink to this slug
            target_path = all_notes[target]
            target_fm, target_body, _ = parse_note(target_path)
            target_related = target_fm.get("related", [])
            target_links = set()
            for r in target_related:
                target_links.update(extract_wikilinks(str(r)))

            if slug not in target_links:
                # Add backlink to target's related field
                target_related.append(f"[[{slug}]]")
                target_fm["related"] = target_related

                # Rewrite the note with updated frontmatter
                _rewrite_frontmatter(target_path, target_fm)
                changes.append((slug, target, "added backlink"))

    return changes


def _rewrite_frontmatter(filepath, new_fm):
    """Rewrite a note's YAML frontmatter while preserving body."""
    content = filepath.read_text()
    if not content.startswith("---"):
        return
    parts = content.split("---", 2)
    if len(parts) < 3:
        return
    body = parts[2]
    # Use yaml.dump with default_flow_style for lists to keep compact format
    fm_text = yaml.dump(new_fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    filepath.write_text(f"---\n{fm_text}\n---\n{body}")


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------

def log_feedback(query, retrieved, expected, failure_type, notes_text=""):
    """Append a feedback entry to .kb/index/feedback.jsonl."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "query": query,
        "retrieved": retrieved,
        "expected": expected,
        "failure_type": failure_type,  # missed | wrong | stale | irrelevant
        "notes": notes_text,
    }
    with open(FEEDBACK_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def get_feedback_summary():
    """Summarize accumulated feedback."""
    if not FEEDBACK_FILE.exists():
        return {"total": 0, "by_type": {}, "top_missed": []}

    entries = []
    for line in FEEDBACK_FILE.read_text().strip().split("\n"):
        if line:
            entries.append(json.loads(line))

    by_type = {}
    missed_slugs = defaultdict(int)
    for e in entries:
        ft = e.get("failure_type", "unknown")
        by_type[ft] = by_type.get(ft, 0) + 1
        for slug in e.get("expected", []):
            if slug not in e.get("retrieved", []):
                missed_slugs[slug] += 1

    top_missed = sorted(missed_slugs.items(), key=lambda x: -x[1])[:10]

    return {
        "total": len(entries),
        "by_type": by_type,
        "top_missed": top_missed,
        "recent": entries[-5:] if entries else [],
    }


# ---------------------------------------------------------------------------
# Quick search (title/slug/tag match only)
# ---------------------------------------------------------------------------

def quick_search(query, top_k=10):
    """Fast title/slug/tag match — no TF-IDF, no embeddings.

    Returns notes where query words appear in title, slug, or tags.
    Ranked by match density (fraction of query words found).
    """
    if not META_FILE.exists():
        print("No index. Run: python3 .kb/kb-index.py build", file=sys.stderr)
        sys.exit(1)

    metadata = json.loads(META_FILE.read_text())
    query_lower = query.lower()
    stop_words = {"the", "a", "an", "is", "are", "how", "do", "does", "what", "which",
                  "and", "or", "for", "in", "of", "to", "with", "can", "be", "it",
                  "this", "that", "between", "vs", "my", "your"}
    query_words = set(re.sub(r'[^\w\s]', '', query_lower).split()) - stop_words

    if not query_words:
        return []

    results = []
    for slug, meta in metadata.items():
        title_words = set(re.sub(r'[^\w\s]', '', meta.get("title", "").lower()).split())
        slug_words = set(slug.split("-"))
        tag_words = set()
        for t in meta.get("tags", []):
            tag_words.update(t.lower().split("-"))

        all_note_words = title_words | slug_words | tag_words

        overlap = query_words & all_note_words
        if not overlap:
            continue

        # Score: fraction of query words matched, with title matches weighted higher
        title_match = len(query_words & title_words) / len(query_words)
        slug_match = len(query_words & slug_words) / len(query_words)
        tag_match = len(query_words & tag_words) / len(query_words)
        score = title_match * 2.0 + slug_match * 1.5 + tag_match * 1.0

        results.append({
            "slug": slug,
            "title": meta.get("title", slug),
            "score": round(score, 4),
            "type": meta.get("type", ""),
            "tags": meta.get("tags", []),
            "match": sorted(overlap),
        })

    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


# ---------------------------------------------------------------------------
# Discovery: topic map, explore, gaps
# ---------------------------------------------------------------------------

def topic_map():
    """Generate a topic map from clusters + link graph."""
    if not META_FILE.exists() or not CLUSTERS_FILE.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return {}

    metadata = json.loads(META_FILE.read_text())
    clusters = json.loads(CLUSTERS_FILE.read_text())

    # Enrich clusters with type distribution and link density
    result = {}
    for tag, cdata in sorted(clusters.items(), key=lambda x: -x[1]["count"]):
        sample_slugs = cdata.get("sample_notes", [])
        # Get all slugs in this cluster
        all_cluster_slugs = set()
        for slug, meta in metadata.items():
            if tag in meta.get("tags", []):
                all_cluster_slugs.add(slug)

        # Type distribution within cluster
        types = defaultdict(int)
        total_words = 0
        for slug in all_cluster_slugs:
            meta = metadata.get(slug, {})
            types[meta.get("type", "unknown")] += 1
            total_words += meta.get("word_count", 0)

        # Link density
        total_links = 0
        if GRAPH_FILE.exists():
            graph = json.loads(GRAPH_FILE.read_text())
            adj = graph.get("adjacency", {})
            for slug in all_cluster_slugs:
                if slug in adj:
                    # Count links within the cluster
                    for target in adj[slug].get("outgoing", []):
                        if target in all_cluster_slugs:
                            total_links += 1

        max_links = len(all_cluster_slugs) * (len(all_cluster_slugs) - 1)
        density = total_links / max_links if max_links > 0 else 0

        result[tag] = {
            "tags": cdata.get("tags", [tag]),
            "count": len(all_cluster_slugs),
            "types": dict(types),
            "total_words": total_words,
            "avg_words": round(total_words / len(all_cluster_slugs)) if all_cluster_slugs else 0,
            "link_density": round(density, 3),
            "sample": sorted(all_cluster_slugs)[:8],
        }

    return result


def explore_path(start_slug, max_steps=5):
    """Suggest a reading path from a starting note, following the most relevant links."""
    if not GRAPH_FILE.exists() or not META_FILE.exists():
        return []

    graph = json.loads(GRAPH_FILE.read_text())
    metadata = json.loads(META_FILE.read_text())
    adj = graph.get("adjacency", {})

    if start_slug not in adj:
        return []

    path = [start_slug]
    visited = {start_slug}

    for _ in range(max_steps):
        current = path[-1]
        candidates = []
        for neighbor in adj.get(current, {}).get("outgoing", []) + adj.get(current, {}).get("incoming", []):
            if neighbor not in visited and neighbor in metadata:
                meta = metadata[neighbor]
                # Score by: word count (prefer substantial notes), type (prefer insight/question)
                type_bonus = {"insight": 0.3, "question": 0.2, "concept": 0.1, "reference": 0.0, "synthesis": -0.1}
                score = meta.get("word_count", 0) / 1000.0 + type_bonus.get(meta.get("type", ""), 0)
                candidates.append((neighbor, score))

        if not candidates:
            break

        # Pick the highest-scoring unvisited neighbor
        candidates.sort(key=lambda x: -x[1])
        next_slug = candidates[0][0]
        path.append(next_slug)
        visited.add(next_slug)

    return path


def find_topic_gaps():
    """Identify thin areas in the KB based on cluster analysis."""
    tmap = topic_map()
    if not tmap:
        return []

    gaps = []
    for tag, data in tmap.items():
        issues = []
        if data["count"] < 5:
            issues.append(f"only {data['count']} notes")
        if data["avg_words"] < 350:
            issues.append(f"avg {data['avg_words']} words (thin)")
        if data["link_density"] < 0.05:
            issues.append(f"link density {data['link_density']:.1%} (poorly connected)")
        # Type imbalance: all concepts, no insights or questions
        types = data.get("types", {})
        if types.get("concept", 0) > 5 and types.get("insight", 0) == 0 and types.get("question", 0) == 0:
            issues.append("no insights or questions (all concepts)")

        if issues:
            gaps.append({
                "topic": tag,
                "count": data["count"],
                "issues": issues,
                "tags": data["tags"],
            })

    return sorted(gaps, key=lambda x: len(x["issues"]), reverse=True)


# ---------------------------------------------------------------------------
# Synthesis staleness
# ---------------------------------------------------------------------------

def find_stale_syntheses():
    """Find synthesis notes whose dependencies have been updated more recently."""
    if not META_FILE.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return []

    metadata = json.loads(META_FILE.read_text())
    stale = []

    for slug, meta in metadata.items():
        if meta.get("type") != "synthesis":
            continue

        depends = meta.get("depends_on", [])
        if not depends:
            continue

        synth_updated = meta.get("updated", "")
        stale_deps = []
        for dep in depends:
            dep_meta = metadata.get(dep)
            if not dep_meta:
                stale_deps.append(f"{dep} (not found)")
                continue
            dep_updated = dep_meta.get("updated", "")
            if dep_updated > synth_updated:
                stale_deps.append(f"{dep} (updated {dep_updated})")

        if stale_deps:
            stale.append({
                "slug": slug,
                "title": meta.get("title", slug),
                "last_updated": synth_updated,
                "stale_dependencies": stale_deps,
            })

    return stale


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def load_index():
    """Load existing index."""
    if not INDEX_FILE.exists() or not VECTORS_FILE.exists():
        print("Index not found. Run: python3 .kb/kb-index.py build", file=sys.stderr)
        sys.exit(1)

    import pickle
    with open(VECTORIZER_FILE, "rb") as f:
        vectorizer = pickle.load(f)

    npz = np.load(VECTORS_FILE)
    data = json.loads(INDEX_FILE.read_text())
    metadata = json.loads(META_FILE.read_text())

    return npz["vectors"], vectorizer, list(npz["slugs"]), metadata


def multi_search(queries, tags=None, note_type=None, valid_only=True, top_k=10):
    """Run search for multiple query variants and merge results via RRF.

    Each query is searched independently, then results are fused using
    Reciprocal Rank Fusion. This handles vocabulary mismatch by trying
    different phrasings of the same intent.
    """
    scfg = load_config()["search"]
    k_rrf = scfg["rrf_constant"]

    # Collect per-query ranked lists
    all_results = []
    for q in queries:
        results = search(q, tags=tags, note_type=note_type, valid_only=valid_only, top_k=top_k * 2)
        all_results.append(results)

    # RRF fusion across all query variants
    slug_scores = defaultdict(float)
    slug_meta = {}
    for results in all_results:
        for rank, r in enumerate(results):
            slug_scores[r["slug"]] += 1.0 / (k_rrf + rank)
            slug_meta[r["slug"]] = r  # keep latest metadata

    # Sort by fused score
    ranked = sorted(slug_scores.items(), key=lambda x: -x[1])[:top_k]

    fused = []
    for slug, score in ranked:
        r = slug_meta[slug].copy()
        r["score"] = round(score, 4)
        fused.append(r)

    return fused


def search(query, tags=None, note_type=None, valid_only=True, top_k=10):
    """Hybrid search: TF-IDF + dense embeddings with metadata filtering and topic weighting."""
    vectors, vectorizer, slugs, metadata = load_index()
    scfg = load_config()["search"]

    # TF-IDF similarity
    query_vec = vectorizer.transform([query]).toarray()
    tfidf_sims = cosine_similarity(query_vec, vectors)[0]

    # Title, slug, and tag boosting: direct lexical match signals
    title_boost = scfg.get("title_boost", 2.0)
    tag_boost = scfg.get("tag_boost", 1.5)
    query_lower = query.lower()
    stop_words = {"the", "a", "an", "is", "are", "how", "do", "does", "what", "which",
                  "and", "or", "for", "in", "of", "to", "with", "can", "be", "it", "its",
                  "this", "that", "these", "those", "my", "your", "their", "between"}
    query_words = set(re.sub(r'[^\w\s]', '', query_lower).split()) - stop_words

    # Also build bigrams from query for compound term matching (e.g. "pig butchering")
    query_word_list = [w for w in re.sub(r'[^\w\s]', '', query_lower).split() if w not in stop_words]
    query_bigrams = set()
    for j in range(len(query_word_list) - 1):
        query_bigrams.add(f"{query_word_list[j]}-{query_word_list[j+1]}")
        query_bigrams.add(f"{query_word_list[j]}{query_word_list[j+1]}")

    if query_words:
        for i, slug in enumerate(slugs):
            meta = metadata.get(slug, {})
            title_lower = meta.get("title", "").lower()
            title_words = set(re.sub(r'[^\w\s]', '', title_lower).split())

            # Slug words (split on hyphens)
            slug_words = set(slug.split("-"))

            # Tag words (split hyphenated tags into individual words)
            tag_words = set()
            tag_compounds = set()  # full hyphenated tags for bigram matching
            for t in meta.get("tags", []):
                tag_words.update(t.lower().split("-"))
                tag_compounds.add(t.lower())

            # Title match: fraction of query words found in title
            title_overlap = len(query_words & title_words) / len(query_words)
            if title_overlap > 0:
                tfidf_sims[i] *= (1.0 + (title_boost - 1.0) * title_overlap)

            # Slug match: fraction of query words found in slug
            slug_overlap = len(query_words & slug_words) / len(query_words)
            if slug_overlap > 0:
                tfidf_sims[i] *= (1.0 + (title_boost - 1.0) * slug_overlap)

            # Tag match: query words or bigrams match tags
            tag_overlap = len(query_words & tag_words) / len(query_words)
            # Bigram match: "pig butchering" → matches tag "pig-butchering"
            bigram_match = bool(query_bigrams & tag_compounds)
            if bigram_match:
                tfidf_sims[i] *= tag_boost  # full boost for compound match
            elif tag_overlap > 0:
                tfidf_sims[i] *= (1.0 + (tag_boost - 1.0) * tag_overlap)

    # Dense embedding similarity
    dense_sims = np.zeros_like(tfidf_sims)
    has_dense = False
    if EMBEDDINGS_FILE.exists():
        try:
            dense_npz = np.load(EMBEDDINGS_FILE)
            dense_vecs = dense_npz["embeddings"]
            provider, _ = detect_embedding_provider()
            q_emb = None

            if provider == "local":
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer("all-MiniLM-L6-v2")
                q_emb = model.encode([query]).reshape(1, -1)
            elif provider in ("openai", "voyage"):
                import os, urllib.request
                if provider == "voyage":
                    api_key = os.environ["VOYAGE_API_KEY"]
                    url = "https://api.voyageai.com/v1/embeddings"
                    model_name = "voyage-3-lite"
                else:
                    api_key = os.environ["OPENAI_API_KEY"]
                    url = "https://api.openai.com/v1/embeddings"
                    model_name = "text-embedding-3-small"
                req = urllib.request.Request(url,
                    data=json.dumps({"input": [query], "model": model_name}).encode(),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req) as resp:
                    result = json.loads(resp.read())
                    q_emb = np.array(result["data"][0]["embedding"]).reshape(1, -1)

            if q_emb is not None:
                dense_sims = cosine_similarity(q_emb, dense_vecs)[0]
                has_dense = True
        except Exception:
            pass

    # Hybrid fusion: RRF when both available
    if has_dense:
        k_rrf = scfg["rrf_constant"]
        tfidf_ranks = np.argsort(-tfidf_sims)
        dense_ranks = np.argsort(-dense_sims)
        rrf_scores = np.zeros(len(slugs))
        for rank, idx in enumerate(tfidf_ranks):
            rrf_scores[idx] += 1.0 / (k_rrf + rank)
        for rank, idx in enumerate(dense_ranks):
            rrf_scores[idx] += 1.0 / (k_rrf + rank)
        sims = rrf_scores
    else:
        sims = tfidf_sims

    # Topic weighting
    if CLUSTERS_FILE.exists():
        try:
            clusters = json.loads(CLUSTERS_FILE.read_text())
            cluster_sizes = {t: c["count"] for t, c in clusters.items()}
            max_cluster = max(cluster_sizes.values()) if cluster_sizes else 1
            boost_scale = scfg["cluster_boost_scale"]
            for i, slug in enumerate(slugs):
                note_tags = metadata.get(slug, {}).get("tags", [])
                largest_cluster = 0
                for t in note_tags:
                    if t in cluster_sizes:
                        largest_cluster = max(largest_cluster, cluster_sizes[t])
                if largest_cluster > 0:
                    boost = 1.0 + boost_scale * (1 - largest_cluster / max_cluster)
                    sims[i] *= boost
        except Exception:
            pass

    # Graph expansion: boost neighbors of top-scoring notes via wikilinks
    gcfg = load_config().get("graph", {})
    expansion_hops = gcfg.get("expansion_hops", 1)
    expansion_seeds = gcfg.get("expansion_seeds", 5)
    expansion_decay = gcfg.get("expansion_decay", 0.5)

    if expansion_hops > 0 and GRAPH_FILE.exists():
        try:
            graph_data = json.loads(GRAPH_FILE.read_text())
            adj = graph_data.get("adjacency", {})
            slug_to_idx = {s: i for i, s in enumerate(slugs)}

            # Get top seeds by current score
            seed_indices = np.argsort(sims)[::-1][:expansion_seeds]
            seeds = [(slugs[i], float(sims[i])) for i in seed_indices if sims[i] > scfg["min_score"]]

            # Expand: for each seed, boost its 1-hop neighbors by adding decayed seed score
            for seed_slug, seed_score in seeds:
                if seed_slug not in adj:
                    continue
                neighbors = set(adj[seed_slug].get("outgoing", []) + adj[seed_slug].get("incoming", []))
                for neighbor in neighbors:
                    if neighbor in slug_to_idx:
                        n_idx = slug_to_idx[neighbor]
                        boost = seed_score * expansion_decay
                        sims[n_idx] += boost
        except Exception:
            pass

    # Apply metadata filters
    for i, slug in enumerate(slugs):
        meta = metadata.get(slug, {})

        if tags:
            if not set(meta.get("tags", [])).intersection(set(tags)):
                sims[i] = 0

        if note_type and meta.get("type") != note_type:
            sims[i] = 0

        if valid_only and meta.get("valid_until"):
            try:
                until = date.fromisoformat(str(meta["valid_until"]))
                if until < date.today():
                    sims[i] *= scfg["temporal_expired_penalty"]
            except (ValueError, TypeError):
                pass

        if meta.get("deprecated_by"):
            sims[i] *= scfg["deprecated_penalty"]

    # Rank
    ranked = np.argsort(sims)[::-1][:top_k]
    min_score = scfg["min_score"]

    results = []
    for idx in ranked:
        if sims[idx] < min_score:
            break
        slug = slugs[idx]
        meta = metadata.get(slug, {})
        results.append({
            "slug": slug,
            "title": meta.get("title", slug),
            "score": round(float(sims[idx]), 4),
            "type": meta.get("type", ""),
            "tags": meta.get("tags", []),
            "deprecated": bool(meta.get("deprecated_by")),
        })

    return results


def find_similar(slug, top_k=10):
    """Find notes most similar to a given note."""
    vectors, vectorizer, slugs, metadata = load_index()
    min_score = cfg("similarity", "min_score")

    if slug not in slugs:
        print(f"Note '{slug}' not found in index.", file=sys.stderr)
        return []

    idx = slugs.index(slug)
    sims = cosine_similarity(vectors[idx:idx+1], vectors)[0]
    sims[idx] = 0

    ranked = np.argsort(sims)[::-1][:top_k]
    results = []
    for i in ranked:
        if sims[i] < min_score:
            break
        results.append({
            "slug": slugs[i],
            "title": metadata.get(slugs[i], {}).get("title", slugs[i]),
            "score": round(float(sims[i]), 4),
        })
    return results


def check_contradictions(slug):
    """Find notes that might contradict a given note."""
    similar = find_similar(slug, top_k=20)
    _, _, slugs, metadata = load_index()
    overlap_thresh = cfg("similarity", "contradiction_overlap")
    high_thresh = cfg("similarity", "contradiction_high")

    candidates = []
    for s in similar:
        if s["score"] > overlap_thresh:
            meta = metadata.get(s["slug"], {})
            candidates.append({
                **s,
                "tags": meta.get("tags", []),
                "type": meta.get("type", ""),
                "warning": "HIGH OVERLAP — review for consistency" if s["score"] > high_thresh else "moderate overlap",
            })
    return candidates


def find_stale(days_threshold=None):
    """Find notes that may be outdated."""
    if days_threshold is None:
        days_threshold = cfg("staleness", "default_days")
    metadata = json.loads(META_FILE.read_text())
    today = date.today()
    stale = []

    for slug, meta in metadata.items():
        reasons = []
        if meta.get("updated"):
            try:
                updated = date.fromisoformat(str(meta["updated"]))
                age_days = (today - updated).days
                if age_days > days_threshold:
                    reasons.append(f"not updated in {age_days} days")
            except (ValueError, TypeError):
                pass
        if meta.get("valid_until"):
            try:
                until = date.fromisoformat(str(meta["valid_until"]))
                if until < today:
                    reasons.append(f"expired on {meta['valid_until']}")
            except (ValueError, TypeError):
                pass
        if meta.get("deprecated_by"):
            reasons.append(f"deprecated by [[{meta['deprecated_by']}]]")
        if reasons:
            stale.append({"slug": slug, "title": meta.get("title", slug), "reasons": reasons})

    return sorted(stale, key=lambda x: len(x["reasons"]), reverse=True)


def check_coverage(topic):
    """Check if the KB has adequate coverage of a topic."""
    vectors, vectorizer, slugs, metadata = load_index()
    ccfg = load_config()["coverage"]

    query_vec = vectorizer.transform([topic]).toarray()
    tfidf_sims = cosine_similarity(query_vec, vectors)[0]
    ranked = np.argsort(tfidf_sims)[::-1][:10]
    results = []
    for idx in ranked:
        if tfidf_sims[idx] < 0.01:
            break
        slug = slugs[idx]
        meta = metadata.get(slug, {})
        results.append({"slug": slug, "title": meta.get("title", slug), "score": round(float(tfidf_sims[idx]), 4)})

    if not results:
        return {"covered": False, "confidence": 0, "message": f"No notes found matching '{topic}'", "notes": []}

    top_score = results[0]["score"]
    num_relevant = sum(1 for r in results if r["score"] > ccfg["relevance_floor"])

    if top_score > ccfg["well_covered_min_score"] and num_relevant >= ccfg["well_covered_min_notes"]:
        level = "well-covered"
        confidence = min(top_score * 4, 1.0)
    elif top_score > ccfg["partial_min_score"] and num_relevant >= ccfg["partial_min_notes"]:
        level = "partially-covered"
        confidence = top_score * 2
    else:
        level = "not-covered"
        confidence = top_score

    return {
        "covered": level != "not-covered",
        "level": level,
        "confidence": round(confidence, 3),
        "top_match": results[0] if results else None,
        "relevant_notes": num_relevant,
        "notes": results[:5],
    }


def stats():
    """Print index statistics."""
    if not META_FILE.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return

    metadata = json.loads(META_FILE.read_text())
    index_data = json.loads(INDEX_FILE.read_text())

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

    print(f"Notes: {len(metadata)}")
    print(f"Features: {index_data.get('feature_count', 'unknown')}")
    print(f"Total words: {total_words:,}")
    print(f"Deprecated: {deprecated}")

    # Type distribution with balance indicator
    total = len(metadata)
    print(f"Types:")
    for t in sorted(types.keys()):
        count = types[t]
        pct = count / total * 100 if total else 0
        bar = "█" * int(pct / 2)
        print(f"  {t:12s} {count:4d} ({pct:4.1f}%) {bar}")

    provider, detail = detect_embedding_provider()
    if EMBEDDINGS_FILE.exists():
        dense_npz = np.load(EMBEDDINGS_FILE)
        dims = dense_npz["embeddings"].shape[1]
        print(f"Dense embeddings: yes ({dims} dims, {dense_npz['embeddings'].shape[0]} vectors)")
    elif provider:
        print(f"Dense embeddings: available but not built (run 'build --embed'). Provider: {detail}")
    else:
        print(f"Dense embeddings: not available. {detail}")

    print(f"Topic clusters: {len(json.loads(CLUSTERS_FILE.read_text())) if CLUSTERS_FILE.exists() else 0}")

    if GRAPH_FILE.exists():
        g = json.loads(GRAPH_FILE.read_text())
        print(f"Link graph: {g['node_count']} nodes, {g['edge_count']} edges, {g['orphan_count']} orphans")

    print(f"Top tags: {json.dumps(dict(sorted(all_tags.items(), key=lambda x: -x[1])[:20]), indent=2)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "build":
        incremental = "--incremental" in sys.argv
        build_index(incremental=incremental)
        if "--embed" in sys.argv:
            notes_list = sorted(NOTES.glob("*.md"))
            slugs = [p.stem for p in notes_list]
            texts = []
            for p in notes_list:
                fm, body, _ = parse_note(p)
                texts.append(extract_contextual_text(fm, body))
            build_dense_embeddings(slugs, texts)
        else:
            provider, detail = detect_embedding_provider()
            if EMBEDDINGS_FILE.exists():
                print(f"Dense embeddings: cached (run 'build --embed' to rebuild)")
            elif provider:
                print(f"Dense embeddings: available via {detail} (run 'build --embed' to enable)")
            else:
                print(f"Dense embeddings: not available ({detail})")

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py search 'query' [--tags t1,t2] [--type concept] [--multi 'q2' 'q3']")
            sys.exit(1)
        query = sys.argv[2]
        tags = None
        note_type = None
        extra_queries = []
        i = 3
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == "--tags" and i + 1 < len(sys.argv):
                tags = sys.argv[i + 1].split(",")
                i += 2
            elif arg == "--type" and i + 1 < len(sys.argv):
                note_type = sys.argv[i + 1]
                i += 2
            elif arg == "--multi":
                # Collect all remaining non-flag arguments as extra queries
                i += 1
                while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                    extra_queries.append(sys.argv[i])
                    i += 1
            else:
                i += 1

        if extra_queries:
            all_queries = [query] + extra_queries
            results = multi_search(all_queries, tags=tags, note_type=note_type)
        else:
            results = search(query, tags=tags, note_type=note_type)
        for r in results:
            dep = " [DEPRECATED]" if r["deprecated"] else ""
            print(f"  {r['score']:.4f}  {r['slug']}{dep}")
            print(f"           {r['title']} ({r['type']}) [{', '.join(r['tags'][:5])}]")

    elif cmd == "similar":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py similar <note-slug>")
            sys.exit(1)
        results = find_similar(sys.argv[2])
        for r in results:
            print(f"  {r['score']:.4f}  {r['slug']} — {r['title']}")

    elif cmd == "contradictions":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py contradictions <note-slug>")
            sys.exit(1)
        results = check_contradictions(sys.argv[2])
        for r in results:
            print(f"  {r['score']:.4f}  {r['slug']} — {r['warning']}")

    elif cmd == "stale":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else None
        results = find_stale(days)
        threshold = days or cfg("staleness", "default_days")
        print(f"Stale notes (>{threshold} days or expired):")
        for r in results:
            print(f"  {r['slug']} — {', '.join(r['reasons'])}")

    elif cmd == "stale-syntheses":
        results = find_stale_syntheses()
        if not results:
            print("No stale synthesis notes found.")
        else:
            print(f"Stale synthesis notes ({len(results)}):")
            for r in results:
                print(f"  {r['slug']} (updated {r['last_updated']})")
                for dep in r["stale_dependencies"]:
                    print(f"    → {dep}")

    elif cmd == "coverage":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py coverage 'topic'")
            sys.exit(1)
        result = check_coverage(sys.argv[2])
        print(f"Coverage: {result['level']} (confidence: {result['confidence']})")
        if result.get("notes"):
            print("Relevant notes:")
            for n in result["notes"]:
                print(f"  {n['score']:.4f}  {n['slug']}")

    elif cmd == "clusters":
        if not CLUSTERS_FILE.exists():
            print("No clusters. Run: python3 .kb/kb-index.py build")
            sys.exit(1)
        clusters = json.loads(CLUSTERS_FILE.read_text())
        print(f"Topic clusters ({len(clusters)}):")
        for tag, data in sorted(clusters.items(), key=lambda x: -x[1]["count"]):
            print(f"  {tag} ({data['count']} notes)")
            print(f"    tags: {', '.join(data['tags'][:5])}")
            print(f"    sample: {', '.join(data['sample_notes'][:3])}")

    elif cmd == "lint":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        results = lint_all(target)
        if not results:
            print("All notes pass lint checks. ✓")
        else:
            total_issues = sum(len(v) for v in results.values())
            errors = sum(1 for v in results.values() for sev, _ in v if sev == "error")
            warnings = sum(1 for v in results.values() for sev, _ in v if sev == "warning")
            infos = sum(1 for v in results.values() for sev, _ in v if sev == "info")
            print(f"Lint: {len(results)} notes with issues ({errors} errors, {warnings} warnings, {infos} info)")
            print()
            for slug, issues in sorted(results.items()):
                print(f"  {slug}:")
                for severity, msg in issues:
                    icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}[severity]
                    print(f"    {icon} [{severity}] {msg}")

    elif cmd == "graph":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else "summary"

        if subcmd == "summary":
            if not GRAPH_FILE.exists():
                print("No graph. Run: python3 .kb/kb-index.py build")
                sys.exit(1)
            g = load_graph()
            comps = graph_components()
            print(f"Link graph:")
            print(f"  Nodes: {g['node_count']}")
            print(f"  Edges: {g['edge_count']}")
            print(f"  Orphans: {g['orphan_count']}")
            print(f"  Connected components: {len(comps)}")
            if len(comps) > 1:
                print(f"  Largest component: {len(comps[0])} nodes")
                print(f"  Smallest component: {len(comps[-1])} nodes")

        elif subcmd == "orphans":
            orphans = graph_orphans()
            print(f"Orphan notes ({len(orphans)}) — no incoming or outgoing links:")
            for s in orphans:
                print(f"  {s}")

        elif subcmd == "components":
            comps = graph_components()
            print(f"Connected components ({len(comps)}):")
            for i, comp in enumerate(comps):
                if len(comp) <= 5:
                    print(f"  Component {i+1} ({len(comp)}): {', '.join(comp)}")
                else:
                    print(f"  Component {i+1} ({len(comp)}): {', '.join(comp[:5])}...")

        elif subcmd == "neighbors":
            if len(sys.argv) < 4:
                print("Usage: kb-index.py graph neighbors <slug> [hops]")
                sys.exit(1)
            slug = sys.argv[3]
            hops = int(sys.argv[4]) if len(sys.argv) > 4 else 2
            neighbors = graph_neighbors(slug, hops)
            if not neighbors:
                print(f"No neighbors found for '{slug}' within {hops} hops.")
            else:
                print(f"Neighbors of '{slug}' within {hops} hops ({len(neighbors)}):")
                for s, d in sorted(neighbors.items(), key=lambda x: (x[1], x[0])):
                    print(f"  {d} hop{'s' if d > 1 else ' '}  {s}")

        elif subcmd == "bridges":
            bridges = graph_bridges()
            if not bridges:
                print("No bridge notes found (graph is well-connected or fully disconnected).")
            else:
                print(f"Bridge notes ({len(bridges)}) — removal increases component count:")
                for b in bridges:
                    print(f"  {b['slug']} → {b['components_after_removal']} components")

        else:
            print(f"Unknown graph subcommand: {subcmd}")
            print("Available: summary, orphans, components, neighbors, bridges")

    elif cmd == "backlink":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        targets = [target] if target else None
        changes = auto_backlink(targets)
        if not changes:
            print("All backlinks are up to date.")
        else:
            print(f"Added {len(changes)} backlinks:")
            for src, tgt, action in changes:
                print(f"  {src} → {tgt}: {action}")

    elif cmd == "quick":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py quick 'query'")
            sys.exit(1)
        results = quick_search(sys.argv[2])
        if not results:
            print("No matches. Try full search: kb-index.py search 'query'")
        else:
            for r in results:
                print(f"  {r['score']:.2f}  {r['slug']}")
                print(f"        {r['title']} ({r['type']}) matched: {', '.join(r['match'])}")

    elif cmd == "feedback":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else "summary"
        if subcmd == "summary":
            s = get_feedback_summary()
            if s["total"] == 0:
                print("No feedback recorded yet.")
            else:
                print(f"Feedback entries: {s['total']}")
                print(f"By type: {json.dumps(s['by_type'])}")
                if s["top_missed"]:
                    print("Most frequently missed notes:")
                    for slug, count in s["top_missed"]:
                        print(f"  {count}x  {slug}")
        elif subcmd == "log":
            if len(sys.argv) < 6:
                print("Usage: kb-index.py feedback log 'query' 'failure_type' 'expected_slug1,slug2' ['notes']")
                sys.exit(1)
            query = sys.argv[3]
            failure_type = sys.argv[4]
            expected = sys.argv[5].split(",") if sys.argv[5] else []
            notes_text = sys.argv[6] if len(sys.argv) > 6 else ""
            entry = log_feedback(query, [], expected, failure_type, notes_text)
            print(f"Feedback logged: {failure_type} for '{query}'")
        else:
            print("Usage: kb-index.py feedback [summary|log]")

    elif cmd == "map":
        tmap = topic_map()
        if not tmap:
            print("No data. Run: python3 .kb/kb-index.py build")
        else:
            print(f"Topic Map ({len(tmap)} topics):\n")
            for tag, data in sorted(tmap.items(), key=lambda x: -x[1]["count"]):
                types_str = ", ".join(f"{t}:{c}" for t, c in sorted(data["types"].items()))
                density_bar = "█" * int(data["link_density"] * 20)
                print(f"  {tag} ({data['count']} notes, ~{data['avg_words']} words/note)")
                print(f"    types: {types_str}")
                print(f"    density: {data['link_density']:.1%} {density_bar}")
                print(f"    tags: {', '.join(data['tags'][:5])}")
                print()

    elif cmd == "explore":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py explore <start-slug> [max-steps]")
            sys.exit(1)
        slug = sys.argv[2]
        steps = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        path = explore_path(slug, steps)
        if not path:
            print(f"No path from '{slug}'.")
        else:
            metadata = json.loads(META_FILE.read_text())
            print(f"Reading path from '{slug}' ({len(path)} notes):\n")
            for i, s in enumerate(path):
                meta = metadata.get(s, {})
                arrow = "  →" if i > 0 else "   "
                print(f"  {arrow} {i+1}. [{meta.get('type','')}] {meta.get('title', s)}")
                print(f"       {s} ({meta.get('word_count', '?')} words)")

    elif cmd == "gaps":
        gaps = find_topic_gaps()
        if not gaps:
            print("No significant topic gaps found.")
        else:
            print(f"Topic gaps ({len(gaps)}):\n")
            for g in gaps:
                print(f"  {g['topic']} ({g['count']} notes)")
                for issue in g["issues"]:
                    print(f"    - {issue}")
                print()

    elif cmd == "eval":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else "retrieval"
        verbose = "--verbose" in sys.argv or "-v" in sys.argv
        import subprocess
        if subcmd == "retrieval":
            result = subprocess.run(
                [sys.executable, str(BASE / "tests" / "test_retrieval.py")] + (["--verbose"] if verbose else []),
                cwd=str(BASE))
            sys.exit(result.returncode)
        elif subcmd == "generation":
            result = subprocess.run(
                [sys.executable, str(BASE / "tests" / "test_generation.py")] + (["--verbose"] if verbose else []),
                cwd=str(BASE))
            sys.exit(result.returncode)
        elif subcmd == "all":
            print("=== Retrieval Evaluation ===")
            r1 = subprocess.run(
                [sys.executable, str(BASE / "tests" / "test_retrieval.py")] + (["--verbose"] if verbose else []),
                cwd=str(BASE))
            print("\n=== Generation Evaluation ===")
            r2 = subprocess.run(
                [sys.executable, str(BASE / "tests" / "test_generation.py")] + (["--verbose"] if verbose else []),
                cwd=str(BASE))
            sys.exit(max(r1.returncode, r2.returncode))
        else:
            print("Usage: kb-index.py eval [retrieval|generation|all] [--verbose]")

    elif cmd == "stats":
        stats()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
