#!/usr/bin/env python3
"""KB search index: build, query, lint, and maintain.

Usage:
  python3 .kb/kb-index.py build                    # Build all per-KB indices + unified
  python3 .kb/kb-index.py build --kb general        # Build index for one KB only
  python3 .kb/kb-index.py build --embed            # Also build dense embeddings
  python3 .kb/kb-index.py build --incremental      # Only re-index changed notes
  python3 .kb/kb-index.py search "query text"      # Hybrid search unified index (top 10)
  python3 .kb/kb-index.py search "query" --kb general  # Search specific KB
  python3 .kb/kb-index.py search "query" --tags security,rag  # With tag filter
  python3 .kb/kb-index.py search "query" --type concept       # With type filter
  python3 .kb/kb-index.py search "query" --multi "alt query 1" "alt query 2"  # Multi-query fusion
  python3 .kb/kb-index.py similar note-slug         # Find similar notes (unified)
  python3 .kb/kb-index.py similar note-slug --kb general  # Find similar in specific KB
  python3 .kb/kb-index.py stale                     # Find temporally stale notes
  python3 .kb/kb-index.py stale-syntheses           # Find synthesis notes with updated dependencies
  python3 .kb/kb-index.py contradictions note-slug  # Find potentially conflicting notes
  python3 .kb/kb-index.py coverage "topic"          # Check if KB covers a topic
  python3 .kb/kb-index.py stats                     # Index statistics (all KBs)
  python3 .kb/kb-index.py stats --kb general        # Stats for one KB
  python3 .kb/kb-index.py clusters                  # Show topic clusters
  python3 .kb/kb-index.py lint                      # Validate all notes in all KBs
  python3 .kb/kb-index.py lint note-slug            # Validate a single note
  python3 .kb/kb-index.py lint --kb general         # Lint one KB
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
  python3 .kb/kb-index.py gaps suggestions           # Ranked research & synthesis suggestions
  python3 .kb/kb-index.py patterns                   # Detect recurring patterns (e.g. exploit types)
  python3 .kb/kb-index.py contradictions-scan        # Scan for contradictory facts across notes
  python3 .kb/kb-index.py eval                       # Run retrieval evaluation
  python3 .kb/kb-index.py eval generation            # Run generation (faithfulness) evaluation
  python3 .kb/kb-index.py eval all --verbose         # Run all evaluations with details

Global flag:
  --kb <name>    Target a specific KB (default: unified/all depending on command)

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

# ---------------------------------------------------------------------------
# KB Registry — multi-KB support
# ---------------------------------------------------------------------------

REGISTRY_FILE = BASE / "kbs.yaml"


class KBConfig:
    """Configuration for a single KB."""
    def __init__(self, name, path, private=False, default=False):
        self.name = name
        self.notes_dir = BASE / path
        self.private = private
        self.default = default
        self.index_dir = BASE / ".kb" / "index" / name
        # Per-KB index files
        self.index_file = self.index_dir / "tfidf_index.json"
        self.vectors_file = self.index_dir / "tfidf_vectors.npz"
        self.vectorizer_file = self.index_dir / "vectorizer.pkl"
        self.embeddings_file = self.index_dir / "dense_embeddings.npz"
        self.meta_file = self.index_dir / "metadata.json"
        self.clusters_file = self.index_dir / "clusters.json"
        self.graph_file = self.index_dir / "graph.json"


class KBRegistry:
    def __init__(self):
        self.kbs = {}
        self._load()

    def _load(self):
        if REGISTRY_FILE.exists():
            raw = yaml.safe_load(REGISTRY_FILE.read_text()) or {}
            for name, cfg in raw.get("kbs", {}).items():
                self.kbs[name] = KBConfig(
                    name=name, path=cfg["path"],
                    private=cfg.get("private", False),
                    default=cfg.get("default", False),
                )
        if not self.kbs:
            # Fallback: single-KB mode for backward compat
            self.kbs["general"] = KBConfig("general", "kbs/general", default=True)

    def get(self, name): return self.kbs.get(name)
    def all_kbs(self): return list(self.kbs.values())
    def searchable_kbs(self): return [kb for kb in self.kbs.values() if not kb.private]
    def all_kb_names(self): return list(self.kbs.keys())
    def default_kb(self):
        for kb in self.kbs.values():
            if kb.default:
                return kb
        return list(self.kbs.values())[0] if self.kbs else None


_registry = None


def get_registry():
    global _registry
    if _registry is None:
        _registry = KBRegistry()
    return _registry


def resolve_kb(kb_name=None):
    """Get KBConfig for a given kb_name, or default KB if None."""
    reg = get_registry()
    if kb_name:
        kb = reg.get(kb_name)
        if not kb:
            print(f"Unknown KB: {kb_name}. Available: {', '.join(reg.all_kb_names())}", file=sys.stderr)
            sys.exit(1)
        return kb
    return reg.default_kb()


# Unified index directory
UNIFIED_DIR = BASE / ".kb" / "index" / "_unified"

# Backward compat — shared paths (KB-independent)
REFS = BASE / "references"
CONFIG_FILE = BASE / ".kb" / "config.yaml"
TAXONOMY_FILE = BASE / ".kb" / "taxonomy.yaml"
FEEDBACK_FILE = BASE / ".kb" / "index" / "feedback.jsonl"

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
    """Shorthand: cfg('search', 'rrf_constant') -> 60"""
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
    """Extract [[target]] or [[kb:target]] wikilinks.

    Returns list of (kb_part, slug) tuples.
    kb_part is None for plain [[slug]] links.
    """
    raw = re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]*?)?\]\]', text)
    results = []
    for link in raw:
        if ':' in link and not link.startswith('http'):
            kb_part, slug = link.split(':', 1)
            results.append((kb_part, slug))
        else:
            results.append((None, link))
    return results


def extract_wikilink_slugs(text):
    """Extract just the slug part of wikilinks (ignoring kb prefix)."""
    return [slug for _, slug in extract_wikilinks(text)]


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

def _build_single_kb(kb_config, incremental=False):
    """Build TF-IDF index for a single KB.

    Returns (slugs, texts, metadata) for use by unified index builder,
    or None if no notes found.
    """
    notes = sorted(kb_config.notes_dir.glob("*.md"))
    if not notes:
        print(f"[{kb_config.name}] No notes found in {kb_config.notes_dir}.")
        return None

    # --- Incremental: detect changes ---
    old_metadata = {}
    changed_slugs = set()
    deleted_slugs = set()
    if (incremental and kb_config.meta_file.exists()
            and kb_config.vectors_file.exists() and kb_config.vectorizer_file.exists()):
        old_metadata = json.loads(kb_config.meta_file.read_text())
        current_slugs = {p.stem for p in notes}
        deleted_slugs = set(old_metadata.keys()) - current_slugs

        for path in notes:
            slug = path.stem
            _, _, content_hash = parse_note(path)
            old_hash = old_metadata.get(slug, {}).get("content_hash")
            if old_hash != content_hash:
                changed_slugs.add(slug)

        if not changed_slugs and not deleted_slugs:
            print(f"[{kb_config.name}] Index is up to date (no changes detected).")
            # Still return data for unified build
            metadata = old_metadata
            slugs = sorted(metadata.keys())
            texts = []
            for slug in slugs:
                path = kb_config.notes_dir / f"{slug}.md"
                if path.exists():
                    fm, body, _ = parse_note(path)
                    texts.append(extract_contextual_text(fm, body))
                else:
                    texts.append("")
            return slugs, texts, metadata

        # Check if vocabulary would drift too much
        new_count = len(changed_slugs - set(old_metadata.keys()))
        drift = new_count / max(len(old_metadata), 1)
        if drift > cfg("indexing", "vocab_drift_threshold"):
            print(f"[{kb_config.name}] Vocabulary drift {drift:.0%} exceeds threshold -- full rebuild.")
            incremental = False
        else:
            print(f"[{kb_config.name}] Incremental: {len(changed_slugs)} changed, "
                  f"{len(deleted_slugs)} deleted, "
                  f"{len(current_slugs) - len(changed_slugs)} unchanged.")

    slugs = []
    texts = []
    metadata = {}

    for path in notes:
        slug = path.stem
        fm, body, content_hash = parse_note(path)
        contextual_text = extract_contextual_text(fm, body)

        # Extract all wikilinks (body + related frontmatter)
        body_links = extract_wikilink_slugs(body)
        fm_links = []
        for r in fm.get("related", []):
            fm_links.extend(extract_wikilink_slugs(str(r)))

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
            "related": list(dict.fromkeys(fm_links)),
            "outgoing_links": list(dict.fromkeys(body_links + fm_links)),
            "content_hash": content_hash,
            "word_count": len(body.split()),
            "depends_on": fm.get("depends_on", []),
            "kb": kb_config.name,
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
    kb_config.index_dir.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(kb_config.vectors_file,
                        vectors=tfidf_matrix.toarray(),
                        slugs=np.array(slugs))

    import pickle
    with open(kb_config.vectorizer_file, "wb") as f:
        pickle.dump(vectorizer, f)

    index_data = {
        "slugs": slugs,
        "built_at": datetime.now().isoformat(),
        "note_count": len(slugs),
        "feature_count": len(vectorizer.vocabulary_),
        "kb": kb_config.name,
    }
    kb_config.index_file.write_text(json.dumps(index_data, indent=2))
    kb_config.meta_file.write_text(json.dumps(metadata, indent=2))

    # Build topic clusters + link graph for this KB
    build_clusters(slugs, metadata, kb_config)
    build_graph(slugs, metadata, kb_config)

    print(f"[{kb_config.name}] Index built: {len(slugs)} notes, {len(vectorizer.vocabulary_)} features")
    return slugs, texts, metadata


def build_unified_index(all_kb_data):
    """Build a unified index across all searchable KBs.

    all_kb_data: list of (kb_config, slugs, texts, metadata) tuples
    """
    unified_slugs = []
    unified_texts = []
    unified_metadata = {}

    for kb_config, slugs, texts, metadata in all_kb_data:
        for i, slug in enumerate(slugs):
            qualified = f"{kb_config.name}:{slug}"
            unified_slugs.append(qualified)
            unified_texts.append(texts[i])
            meta_copy = dict(metadata[slug])
            meta_copy["kb"] = kb_config.name
            unified_metadata[qualified] = meta_copy

    if not unified_slugs:
        print("[unified] No notes to index.")
        return

    # Build unified TF-IDF matrix
    icfg = load_config()["indexing"]
    vectorizer = TfidfVectorizer(
        max_features=icfg["max_features"],
        ngram_range=tuple(icfg["ngram_range"]),
        min_df=icfg["min_df"],
        max_df=icfg["max_df"],
        sublinear_tf=True,
        stop_words="english",
    )
    tfidf_matrix = vectorizer.fit_transform(unified_texts)

    # Save unified index
    UNIFIED_DIR.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(UNIFIED_DIR / "tfidf_vectors.npz",
                        vectors=tfidf_matrix.toarray(),
                        slugs=np.array(unified_slugs))

    import pickle
    with open(UNIFIED_DIR / "vectorizer.pkl", "wb") as f:
        pickle.dump(vectorizer, f)

    index_data = {
        "slugs": unified_slugs,
        "built_at": datetime.now().isoformat(),
        "note_count": len(unified_slugs),
        "feature_count": len(vectorizer.vocabulary_),
        "kbs": [kbc.name for kbc, _, _, _ in all_kb_data],
    }
    (UNIFIED_DIR / "tfidf_index.json").write_text(json.dumps(index_data, indent=2))
    (UNIFIED_DIR / "metadata.json").write_text(json.dumps(unified_metadata, indent=2))

    # Merged graph
    _build_unified_graph(all_kb_data, unified_slugs, unified_metadata)

    # Merged clusters
    _build_unified_clusters(unified_slugs, unified_metadata)

    print(f"[unified] Index built: {len(unified_slugs)} notes from "
          f"{len(all_kb_data)} KBs, {len(vectorizer.vocabulary_)} features")


def _build_unified_graph(all_kb_data, unified_slugs, unified_metadata):
    """Build merged graph across all KBs with qualified slugs."""
    slug_set = set(unified_slugs)
    adjacency = {}
    for qs in unified_slugs:
        adjacency[qs] = {"outgoing": [], "incoming": []}

    # Build a mapping from bare slug to qualified slug(s) for cross-KB link resolution
    bare_to_qualified = defaultdict(list)
    for qs in unified_slugs:
        _, bare = qs.split(":", 1)
        bare_to_qualified[bare].append(qs)

    for qs in unified_slugs:
        kb_name = unified_metadata[qs]["kb"]
        targets = unified_metadata[qs].get("outgoing_links", [])
        seen = set()
        for target in targets:
            # Resolve: prefer same-KB, then any KB
            same_kb_qualified = f"{kb_name}:{target}"
            if same_kb_qualified in slug_set and same_kb_qualified != qs:
                resolved = same_kb_qualified
            else:
                # Try any KB
                candidates = [q for q in bare_to_qualified.get(target, []) if q != qs]
                resolved = candidates[0] if candidates else None

            if resolved and resolved not in seen:
                adjacency[qs]["outgoing"].append(resolved)
                adjacency[resolved]["incoming"].append(qs)
                seen.add(resolved)

    orphans = [s for s in unified_slugs
               if not adjacency[s]["outgoing"] and not adjacency[s]["incoming"]]
    total_edges = sum(len(adjacency[s]["outgoing"]) for s in unified_slugs)

    graph_data = {
        "built_at": datetime.now().isoformat(),
        "node_count": len(unified_slugs),
        "edge_count": total_edges,
        "orphan_count": len(orphans),
        "adjacency": adjacency,
    }
    (UNIFIED_DIR / "graph.json").write_text(json.dumps(graph_data, indent=2))
    print(f"[unified] Graph built: {len(unified_slugs)} nodes, {total_edges} edges, {len(orphans)} orphans")


def _build_unified_clusters(unified_slugs, unified_metadata):
    """Build merged clusters across all KBs."""
    build_clusters(
        unified_slugs, unified_metadata,
        kb_config=None,  # signals unified mode
        output_file=UNIFIED_DIR / "clusters.json",
    )


def build_index(incremental=False, kb_name=None):
    """Build TF-IDF index.

    If kb_name is specified, build only that KB's index.
    If kb_name is None, build all per-KB indices + unified.
    """
    reg = get_registry()

    if kb_name:
        kb = resolve_kb(kb_name)
        _build_single_kb(kb, incremental=incremental)
        return

    # Build all KBs
    all_kb_data = []
    for kb in reg.all_kbs():
        result = _build_single_kb(kb, incremental=incremental)
        if result is not None:
            slugs, texts, metadata = result
            if not kb.private:
                all_kb_data.append((kb, slugs, texts, metadata))

    # Build unified index from searchable KBs
    if all_kb_data:
        build_unified_index(all_kb_data)


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


def build_dense_embeddings(slugs, texts, kb_config=None):
    """Build dense embeddings using best available provider.

    If kb_config is None, saves to unified dir.
    """
    provider, detail = detect_embedding_provider()
    if provider is None:
        print(f"Dense embeddings: skipped ({detail})")
        return

    embeddings_file = kb_config.embeddings_file if kb_config else (UNIFIED_DIR / "dense_embeddings.npz")
    label = kb_config.name if kb_config else "unified"

    print(f"[{label}] Dense embeddings: using {detail}")
    try:
        if provider == "local":
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)
            np.savez_compressed(embeddings_file,
                                embeddings=np.array(embeddings),
                                slugs=np.array(slugs))
            print(f"[{label}] Dense embeddings built: {len(embeddings)} vectors, {embeddings.shape[1]} dims (local)")

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

            np.savez_compressed(embeddings_file,
                                embeddings=np.array(embeddings),
                                slugs=np.array(slugs))
            print(f"[{label}] Dense embeddings built: {len(embeddings)} vectors, {len(embeddings[0])} dims ({provider})")

    except Exception as e:
        print(f"[{label}] Dense embedding failed: {e}")


# ---------------------------------------------------------------------------
# Clusters
# ---------------------------------------------------------------------------

def build_clusters(slugs, metadata, kb_config=None, output_file=None):
    """Build topic clusters from tag co-occurrence.

    If kb_config is provided, saves to that KB's clusters file.
    If output_file is provided, saves there (used for unified).
    """
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

    if output_file:
        out = output_file
    elif kb_config:
        out = kb_config.clusters_file
    else:
        out = UNIFIED_DIR / "clusters.json"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))

    label = kb_config.name if kb_config else "unified"
    print(f"[{label}] Clusters built: {len(result)} topic clusters")


# ---------------------------------------------------------------------------
# Link graph
# ---------------------------------------------------------------------------

def build_graph(slugs, metadata, kb_config=None, output_file=None):
    """Build a directed adjacency list from wikilinks and save as graph.json."""
    slug_set = set(slugs)
    adjacency = {}

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

    orphans = [s for s in slugs if not adjacency[s]["outgoing"] and not adjacency[s]["incoming"]]
    total_edges = sum(len(adjacency[s]["outgoing"]) for s in slugs)

    graph_data = {
        "built_at": datetime.now().isoformat(),
        "node_count": len(slugs),
        "edge_count": total_edges,
        "orphan_count": len(orphans),
        "adjacency": adjacency,
    }

    if output_file:
        out = output_file
    elif kb_config:
        out = kb_config.graph_file
    else:
        out = UNIFIED_DIR / "graph.json"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph_data, indent=2))

    label = kb_config.name if kb_config else "unified"
    print(f"[{label}] Graph built: {len(slugs)} nodes, {total_edges} edges, {len(orphans)} orphans")


def _resolve_graph_file(kb_name=None):
    """Get the graph file path for a KB or unified."""
    if kb_name:
        kb = resolve_kb(kb_name)
        return kb.graph_file
    return UNIFIED_DIR / "graph.json"


def load_graph(kb_name=None):
    """Load graph.json for a specific KB or unified."""
    graph_file = _resolve_graph_file(kb_name)
    if not graph_file.exists():
        label = kb_name or "unified"
        print(f"[{label}] Graph not found. Run: python3 .kb/kb-index.py build", file=sys.stderr)
        sys.exit(1)
    return json.loads(graph_file.read_text())


def graph_orphans(kb_name=None):
    """Notes with no incoming or outgoing links."""
    g = load_graph(kb_name)
    adj = g["adjacency"]
    orphans = [s for s in adj if not adj[s]["outgoing"] and not adj[s]["incoming"]]
    return sorted(orphans)


def graph_components(kb_name=None):
    """Find connected components (treating graph as undirected)."""
    g = load_graph(kb_name)
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


def graph_neighbors(slug, hops=2, kb_name=None):
    """Find all notes within n hops of a given note."""
    g = load_graph(kb_name)
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

    del visited[slug]
    return visited


def graph_bridges(kb_name=None):
    """Find bridge notes whose removal would increase the number of connected components."""
    g = load_graph(kb_name)
    adj = g["adjacency"]
    all_nodes = set(adj.keys())

    base_components = len(graph_components(kb_name))
    bridges = []

    for node in sorted(all_nodes):
        degree = len(adj[node]["outgoing"]) + len(adj[node]["incoming"])
        if degree < 2:
            continue

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


def lint_note(filepath, taxonomy_tags=None, all_slugs=None, kb_name=None):
    """Validate a single note. Returns list of (severity, message) tuples.

    all_slugs can be a set of bare slugs (single KB) or a dict mapping
    kb_name -> set of slugs (multi-KB mode for cross-KB link validation).
    """
    issues = []
    slug = filepath.stem

    try:
        content = filepath.read_text()
    except Exception as e:
        return [("error", f"Cannot read file: {e}")]

    if not content.startswith("---"):
        issues.append(("error", "Missing YAML frontmatter (file must start with ---)"))
        return issues

    parts = content.split("---", 2)
    if len(parts) < 3:
        issues.append(("error", "Malformed frontmatter (needs opening and closing ---)"))
        return issues

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

    # Wikilink targets — support cross-KB validation
    if all_slugs is not None:
        links = extract_wikilinks(body)
        fm_related = fm.get("related", [])
        for r in fm_related:
            links.extend(extract_wikilinks(str(r)))

        # Build the valid slug set depending on what was passed
        if isinstance(all_slugs, dict):
            # Multi-KB mode: dict of kb_name -> set of slugs
            for link_kb, target in set(links):
                if link_kb:
                    # Explicit cross-KB link [[kb:slug]]
                    if link_kb in all_slugs:
                        if target not in all_slugs[link_kb]:
                            issues.append(("warning", f"Broken wikilink: [[{link_kb}:{target}]] (note does not exist in {link_kb})"))
                    else:
                        issues.append(("warning", f"Broken wikilink: [[{link_kb}:{target}]] (unknown KB '{link_kb}')"))
                else:
                    # Plain link — check current KB first, then all KBs
                    found = False
                    if kb_name and kb_name in all_slugs:
                        if target in all_slugs[kb_name]:
                            found = True
                    if not found:
                        for kb_slugs in all_slugs.values():
                            if target in kb_slugs:
                                found = True
                                break
                    if not found:
                        issues.append(("warning", f"Broken wikilink: [[{target}]] (note does not exist)"))
        else:
            # Simple set of slugs (single KB or backward compat)
            for _, target in set(links):
                if target not in all_slugs:
                    issues.append(("warning", f"Broken wikilink: [[{target}]] (note does not exist)"))

    # Citation paths — handle any depth of ../ prefix (notes can be at kbs/<name>/ = ../../references/)
    ref_citations = re.findall(r'\[([^\]]+)\]\(((?:\.\./)*references/[^)]+)\)', body)
    for text, ref_path in ref_citations:
        full_path = REFS / re.sub(r'^(\.\./)*references/', '', ref_path)
        if not full_path.exists():
            issues.append(("warning", f"Broken citation: [{text}]({ref_path}) (file not found)"))

    # Sources frontmatter paths
    for src in fm.get("sources", []):
        if isinstance(src, str) and ("references/" in src):
            full_path = REFS / re.sub(r'^(\.\./)*references/', '', src)
            if not full_path.exists():
                issues.append(("warning", f"Source file not found: {src}"))

    # Synthesis dependency tracking
    if note_type == "synthesis":
        depends = fm.get("depends_on", [])
        if not depends:
            issues.append(("info", "Synthesis note has no 'depends_on' field -- cannot track staleness"))
        elif all_slugs is not None:
            valid_set = set()
            if isinstance(all_slugs, dict):
                for s in all_slugs.values():
                    valid_set |= s
            else:
                valid_set = all_slugs
            for dep in depends:
                if dep not in valid_set:
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


def lint_all(target_slug=None, kb_name=None):
    """Lint notes. Returns dict of slug -> issues.

    If kb_name is specified, lint that KB only.
    If kb_name is None, lint all KBs.
    If target_slug is given, lint only that note (within the given or all KBs).
    """
    # Load taxonomy tags
    taxonomy_tags = set()
    if TAXONOMY_FILE.exists():
        try:
            tax = yaml.safe_load(TAXONOMY_FILE.read_text()) or {}
            taxonomy_tags = set(tax.get("tags", {}).keys())
        except yaml.YAMLError:
            pass

    reg = get_registry()

    if kb_name:
        kbs_to_lint = [resolve_kb(kb_name)]
    else:
        kbs_to_lint = reg.all_kbs()

    # Build cross-KB slug map for wikilink validation
    all_slugs_map = {}
    for kb in reg.all_kbs():
        if kb.notes_dir.exists():
            all_slugs_map[kb.name] = {p.stem for p in kb.notes_dir.glob("*.md")}
        else:
            all_slugs_map[kb.name] = set()

    if target_slug:
        # Find the note in the target KB(s)
        for kb in kbs_to_lint:
            filepath = kb.notes_dir / f"{target_slug}.md"
            if filepath.exists():
                return {target_slug: lint_note(filepath, taxonomy_tags, all_slugs_map, kb_name=kb.name)}
        return {target_slug: [("error", f"Note file not found: {target_slug}")]}

    results = {}
    for kb in kbs_to_lint:
        if not kb.notes_dir.exists():
            continue
        for path in sorted(kb.notes_dir.glob("*.md")):
            issues = lint_note(path, taxonomy_tags, all_slugs_map, kb_name=kb.name)
            if issues:
                key = f"{kb.name}:{path.stem}" if not kb_name else path.stem
                results[key] = issues

    return results


# ---------------------------------------------------------------------------
# Auto-backlinks
# ---------------------------------------------------------------------------

def auto_backlink(target_slugs=None, kb_name=None):
    """Scan notes for outgoing [[wikilinks]] and add missing reverse links.

    Handles both same-KB and cross-KB links:
    - [[slug]] links are resolved within the same KB
    - [[other-kb:slug]] links are resolved in the target KB, and the
      reverse link uses [[source-kb:slug]] syntax

    If kb_name is specified, operate on that KB only.
    If kb_name is None, operate on all KBs.
    If target_slugs is provided, only process those notes.
    Returns list of (source_kb:slug, target_kb:slug, action) tuples.
    """
    reg = get_registry()
    if kb_name:
        kbs_to_scan = [resolve_kb(kb_name)]
    else:
        kbs_to_scan = reg.all_kbs()

    # Build a global map: kb_name → {slug → path}
    all_kb_notes = {}
    for kbc in reg.all_kbs():
        if kbc.notes_dir.exists():
            all_kb_notes[kbc.name] = {p.stem: p for p in sorted(kbc.notes_dir.glob("*.md"))}
        else:
            all_kb_notes[kbc.name] = {}

    changes = []

    for kb in kbs_to_scan:
        if not kb.notes_dir.exists():
            continue

        local_notes = all_kb_notes.get(kb.name, {})

        if target_slugs:
            notes_to_scan = {s: local_notes[s] for s in target_slugs if s in local_notes}
        else:
            notes_to_scan = local_notes

        for slug, path in notes_to_scan.items():
            fm, body, _ = parse_note(path)

            # Extract wikilinks with KB context: (kb_part_or_none, target_slug)
            outgoing = extract_wikilinks(body)
            for r in fm.get("related", []):
                outgoing.extend(extract_wikilinks(str(r)))

            for link_kb, target_slug in outgoing:
                if target_slug == slug and not link_kb:
                    continue

                # Determine which KB the target lives in
                if link_kb:
                    # Explicit cross-KB link: [[other-kb:slug]]
                    target_kb_name = link_kb
                else:
                    # Same-KB link: [[slug]]
                    target_kb_name = kb.name

                target_notes = all_kb_notes.get(target_kb_name, {})
                if target_slug not in target_notes:
                    continue

                # Check if target already has a backlink to this note
                target_path = target_notes[target_slug]
                target_fm, _, _ = parse_note(target_path)
                target_related = target_fm.get("related", [])
                existing_backlinks = set()
                for r in target_related:
                    for bk_kb, bk_slug in extract_wikilinks(str(r)):
                        if bk_kb:
                            existing_backlinks.add(f"{bk_kb}:{bk_slug}")
                        else:
                            existing_backlinks.add(bk_slug)

                # Determine the backlink format
                if target_kb_name == kb.name:
                    # Same KB: plain [[slug]]
                    if slug not in existing_backlinks:
                        target_related.append(f"[[{slug}]]")
                        target_fm["related"] = target_related
                        _rewrite_frontmatter(target_path, target_fm)
                        changes.append((f"{kb.name}:{slug}", f"{target_kb_name}:{target_slug}", "added backlink"))
                else:
                    # Cross-KB: [[source-kb:slug]]
                    backlink_key = f"{kb.name}:{slug}"
                    if backlink_key not in existing_backlinks:
                        target_related.append(f"[[{kb.name}:{slug}]]")
                        target_fm["related"] = target_related
                        _rewrite_frontmatter(target_path, target_fm)
                        changes.append((f"{kb.name}:{slug}", f"{target_kb_name}:{target_slug}", "added cross-KB backlink"))

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
    fm_text = yaml.dump(new_fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    filepath.write_text(f"---\n{fm_text}\n---\n{body}")


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------

def log_feedback(query, retrieved, expected, failure_type, notes_text=""):
    """Append a feedback entry to .kb/index/feedback.jsonl."""
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "query": query,
        "retrieved": retrieved,
        "expected": expected,
        "failure_type": failure_type,
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

def quick_search(query, top_k=10, kb_name=None):
    """Fast title/slug/tag match -- no TF-IDF, no embeddings.

    If kb_name is None, searches unified metadata.
    If kb_name is specified, searches that KB's metadata.
    """
    if kb_name:
        kb = resolve_kb(kb_name)
        meta_file = kb.meta_file
        label = kb.name
    else:
        meta_file = UNIFIED_DIR / "metadata.json"
        label = "unified"

    if not meta_file.exists():
        print(f"[{label}] No index. Run: python3 .kb/kb-index.py build", file=sys.stderr)
        sys.exit(1)

    metadata = json.loads(meta_file.read_text())
    query_lower = query.lower()
    stop_words = {"the", "a", "an", "is", "are", "how", "do", "does", "what", "which",
                  "and", "or", "for", "in", "of", "to", "with", "can", "be", "it",
                  "this", "that", "between", "vs", "my", "your"}
    query_words = set(re.sub(r'[^\w\s]', '', query_lower).split()) - stop_words

    if not query_words:
        return []

    results = []
    for slug, meta in metadata.items():
        # For unified index, slug is "kb:bare-slug"; extract bare slug for matching
        if ":" in slug:
            kb_part, bare_slug = slug.split(":", 1)
        else:
            kb_part, bare_slug = meta.get("kb", ""), slug

        title_words = set(re.sub(r'[^\w\s]', '', meta.get("title", "").lower()).split())
        slug_words = set(bare_slug.split("-"))
        tag_words = set()
        for t in meta.get("tags", []):
            tag_words.update(t.lower().split("-"))

        all_note_words = title_words | slug_words | tag_words

        overlap = query_words & all_note_words
        if not overlap:
            continue

        title_match = len(query_words & title_words) / len(query_words)
        slug_match = len(query_words & slug_words) / len(query_words)
        tag_match = len(query_words & tag_words) / len(query_words)
        score = title_match * 2.0 + slug_match * 1.5 + tag_match * 1.0

        results.append({
            "slug": bare_slug,
            "title": meta.get("title", bare_slug),
            "score": round(score, 4),
            "type": meta.get("type", ""),
            "tags": meta.get("tags", []),
            "match": sorted(overlap),
            "kb": meta.get("kb", kb_part),
        })

    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


# ---------------------------------------------------------------------------
# Discovery: topic map, explore, gaps
# ---------------------------------------------------------------------------

def topic_map(kb_name=None):
    """Generate a topic map from clusters + link graph.

    If kb_name is None, uses unified index.
    """
    if kb_name:
        kb = resolve_kb(kb_name)
        meta_file = kb.meta_file
        clusters_file = kb.clusters_file
        graph_file = kb.graph_file
    else:
        meta_file = UNIFIED_DIR / "metadata.json"
        clusters_file = UNIFIED_DIR / "clusters.json"
        graph_file = UNIFIED_DIR / "graph.json"

    if not meta_file.exists() or not clusters_file.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return {}

    metadata = json.loads(meta_file.read_text())
    clusters = json.loads(clusters_file.read_text())

    result = {}
    for tag, cdata in sorted(clusters.items(), key=lambda x: -x[1]["count"]):
        all_cluster_slugs = set()
        for slug, meta in metadata.items():
            if tag in meta.get("tags", []):
                all_cluster_slugs.add(slug)

        types = defaultdict(int)
        total_words = 0
        for slug in all_cluster_slugs:
            meta = metadata.get(slug, {})
            types[meta.get("type", "unknown")] += 1
            total_words += meta.get("word_count", 0)

        total_links = 0
        if graph_file.exists():
            graph = json.loads(graph_file.read_text())
            adj = graph.get("adjacency", {})
            for slug in all_cluster_slugs:
                if slug in adj:
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


def explore_path(start_slug, max_steps=5, kb_name=None):
    """Suggest a reading path from a starting note, following the most relevant links."""
    if kb_name:
        kb = resolve_kb(kb_name)
        graph_file = kb.graph_file
        meta_file = kb.meta_file
    else:
        graph_file = UNIFIED_DIR / "graph.json"
        meta_file = UNIFIED_DIR / "metadata.json"

    if not graph_file.exists() or not meta_file.exists():
        return []

    graph = json.loads(graph_file.read_text())
    metadata = json.loads(meta_file.read_text())
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
                type_bonus = {"insight": 0.3, "question": 0.2, "concept": 0.1, "reference": 0.0, "synthesis": -0.1}
                score = meta.get("word_count", 0) / 1000.0 + type_bonus.get(meta.get("type", ""), 0)
                candidates.append((neighbor, score))

        if not candidates:
            break

        candidates.sort(key=lambda x: -x[1])
        next_slug = candidates[0][0]
        path.append(next_slug)
        visited.add(next_slug)

    return path


def find_topic_gaps(kb_name=None):
    """Identify thin areas in the KB based on cluster analysis."""
    tmap = topic_map(kb_name)
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


def find_research_gaps(kb_name=None):
    """Aggregate unresolved research gaps from all research hub notes.

    Reads the `gaps` frontmatter field from notes tagged `research-hub`.
    Returns a list of {hub, title, gaps: [str]} dicts.
    """
    reg = get_registry()
    if kb_name:
        kbs_to_check = [resolve_kb(kb_name)]
    else:
        kbs_to_check = reg.all_kbs()

    results = []
    for kbc in kbs_to_check:
        if not kbc.notes_dir.exists():
            continue
        for path in sorted(kbc.notes_dir.glob("*.md")):
            fm, body, _ = parse_note(path)
            tags = fm.get("tags", [])
            if "research-hub" not in tags:
                continue

            # Check frontmatter gaps field first
            fm_gaps = fm.get("gaps", [])
            if fm_gaps:
                results.append({
                    "kb": kbc.name,
                    "hub": path.stem,
                    "title": fm.get("title", path.stem),
                    "gaps": fm_gaps,
                })
                continue

            # Fallback: parse "## Research Gaps" or "## Gaps" section from body
            gap_lines = []
            in_gaps = False
            for line in body.split("\n"):
                if line.strip().startswith("## ") and ("gap" in line.lower() or "unresolved" in line.lower()):
                    in_gaps = True
                    continue
                elif line.strip().startswith("## ") and in_gaps:
                    break
                elif in_gaps and line.strip().startswith("- "):
                    # Extract gap text, strip markdown bold/links
                    gap_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', line.strip()[2:])
                    gap_text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', gap_text)
                    gap_lines.append(gap_text.strip())

            if gap_lines:
                results.append({
                    "kb": kbc.name,
                    "hub": path.stem,
                    "title": fm.get("title", path.stem),
                    "gaps": gap_lines,
                })

    return results


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

# Tags that represent exploit classification categories
CLASSIFICATION_TAGS = {
    "reentrancy", "oracle-manipulation", "key-compromise", "bridge-security",
    "flash-loan", "access-control", "price-manipulation", "governance-attack",
    "front-running", "sandwich-attack", "rug-pull", "logic-error",
    "signature-replay", "cross-chain", "mev", "social-engineering",
    "private-key-leak", "smart-contract-vulnerability", "upgrade-vulnerability",
}


def find_patterns(kb_name=None):
    """Group notes by shared tags to find recurring attack/topic patterns.

    For exploit/incident notes, groups by classification tags and checks
    whether a synthesis/concept note already exists for each pattern.
    """
    if kb_name:
        kb = resolve_kb(kb_name)
        meta_file = kb.meta_file
    else:
        meta_file = UNIFIED_DIR / "metadata.json"

    if not meta_file.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return []

    metadata = json.loads(meta_file.read_text())

    # Identify exploit/incident notes
    exploit_tags = {"defi-exploits", "incidents", "exploit", "exploits", "hack", "hacks"}
    incident_prefix = "incidents-"

    exploit_slugs = set()
    for slug, meta in metadata.items():
        tags = set(meta.get("tags", []))
        if tags & exploit_tags or any(t.startswith(incident_prefix) for t in tags):
            exploit_slugs.add(slug)

    # Group exploit notes by classification tags
    tag_groups = defaultdict(list)
    for slug in exploit_slugs:
        meta = metadata[slug]
        tags = set(meta.get("tags", []))
        # Match against known classification tags OR any tag on 3+ exploit notes
        for tag in tags:
            if tag in CLASSIFICATION_TAGS or tag.startswith(incident_prefix):
                tag_groups[tag].append(slug)

    # Also group ALL notes by tag (not just exploits) for broader patterns
    all_tag_groups = defaultdict(list)
    for slug, meta in metadata.items():
        for tag in meta.get("tags", []):
            all_tag_groups[tag].append(slug)

    # Merge: prefer exploit-specific groups, supplement with all-tag groups
    # that have classification tags
    for tag in list(all_tag_groups.keys()):
        if tag in CLASSIFICATION_TAGS and tag not in tag_groups:
            tag_groups[tag] = all_tag_groups[tag]

    # Filter to patterns with 3+ notes
    patterns = []
    for tag, slugs_list in sorted(tag_groups.items(), key=lambda x: -len(x[1])):
        if len(slugs_list) < 3:
            continue

        # Check if a synthesis or concept note exists for this pattern
        has_synthesis = None
        for slug, meta in metadata.items():
            if slug in slugs_list:
                continue
            meta_type = meta.get("type", "")
            if meta_type in ("synthesis", "concept"):
                meta_tags = set(meta.get("tags", []))
                title_lower = meta.get("title", "").lower()
                tag_lower = tag.lower().replace("-", " ").replace("_", " ")
                if tag in meta_tags or tag_lower in title_lower:
                    has_synthesis = slug
                    break

        patterns.append({
            "pattern": tag,
            "count": len(slugs_list),
            "example_notes": sorted(slugs_list)[:6],
            "has_synthesis_note": has_synthesis,
        })

    return sorted(patterns, key=lambda x: -x["count"])


# ---------------------------------------------------------------------------
# Contradictions scan
# ---------------------------------------------------------------------------

# Regex for dollar amounts: $624M, $1.4B, $100 million, $1,234,567, etc.
_DOLLAR_RE = re.compile(
    r'\$\s*([\d,.]+)\s*([BMKbmk](?:illion|ill)?)?'
)


def _parse_dollar(match_str, suffix_str):
    """Parse a dollar amount string into a float (in millions)."""
    num = float(match_str.replace(",", ""))
    if suffix_str:
        s = suffix_str.lower()
        if s.startswith("b"):
            num *= 1000  # billions to millions
        elif s.startswith("m"):
            pass  # already in millions
        elif s.startswith("k"):
            num /= 1000  # thousands to millions
    else:
        # Raw number -- if it's large enough, assume dollars
        if num >= 1_000_000:
            num /= 1_000_000  # convert to millions
        elif num >= 1_000:
            num /= 1_000_000  # still dollars, just smaller
        else:
            # Small number, could be millions already or just dollars
            # Heuristic: if < 1000 and no suffix, treat as millions
            pass
    return num


def _extract_amounts(text):
    """Extract all dollar amounts from text, returned as list of (original_str, value_in_millions)."""
    results = []
    for m in _DOLLAR_RE.finditer(text):
        try:
            val = _parse_dollar(m.group(1), m.group(2))
            results.append((m.group(0), val))
        except (ValueError, IndexError):
            continue
    return results


def _extract_entity_from_title(title):
    """Extract a likely entity/protocol name from a note title.

    E.g., 'Ronin Exploit ($624M)' -> 'ronin'
          'Bybit Exploit 1.4B' -> 'bybit'
    """
    # Remove common suffixes
    cleaned = re.sub(r'\s*\(.*?\)', '', title)
    cleaned = re.sub(r'\s*[-\u2013]\s*\$.*', '', cleaned)
    cleaned = re.sub(
        r'\b(exploit|hack|attack|incident|bridge|rekt|overview|analysis|deep.?dive|technical|post.?mortem)\b',
        '', cleaned, flags=re.IGNORECASE
    )
    # Generic words that don't identify a specific incident/protocol
    _GENERIC_TITLE_WORDS = {
        "the", "and", "of", "for", "in", "on", "with", "from", "to", "by",
        "security", "smart", "contract", "vulnerability", "protocol", "finance",
        "capital", "network", "chain", "cross", "defi", "web3", "blockchain",
        "oracle", "token", "how", "why", "what", "are", "their", "all",
        "pattern", "trends", "architecture", "comparison", "classification",
        "landscape", "largest", "top", "major", "key", "risk", "fund",
    }
    # Take the first meaningful word(s)
    words = [w.strip().lower() for w in cleaned.split()
             if w.strip() and len(w.strip()) > 2 and w.strip().lower() not in _GENERIC_TITLE_WORDS]
    return words[0] if words else None


def scan_contradictions(kb_name=None):
    """Scan for contradictory facts across notes about the same incidents.

    Detects:
    - Dollar amount mismatches for the same incident
    - Date conflicts
    """
    reg = get_registry()
    if kb_name:
        kbs_to_check = [resolve_kb(kb_name)]
    else:
        kbs_to_check = reg.all_kbs()

    # Load all note content (need bodies for amount extraction)
    notes_data = {}  # slug -> {title, body, amounts, tags, path}
    for kbc in kbs_to_check:
        if not kbc.notes_dir.exists():
            continue
        for path in sorted(kbc.notes_dir.glob("*.md")):
            fm, body, _ = parse_note(path)
            slug = path.stem
            title = fm.get("title", slug)
            amounts = _extract_amounts(body)
            # Also extract amounts from title
            amounts.extend(_extract_amounts(title))
            notes_data[slug] = {
                "title": title,
                "body": body,
                "amounts": amounts,
                "tags": fm.get("tags", []),
                "path": str(path),
            }

    # Generic entity names that don't refer to specific protocols/incidents
    _GENERIC_ENTITIES = {
        "bridge", "cross", "defi", "web3", "blockchain", "smart", "oracle",
        "token", "price", "flash", "key", "access", "social", "legacy",
        "responsible", "largest", "research", "synthesis", "overview",
        "solana", "ethereum", "bitcoin",  # L1 chains are too broad
        "custodial", "intent", "liquidity", "light", "durable",
        "exploits", "hacks", "databases", "accounts", "categories",
        "manipulation", "vulnerability", "security", "landscape",
        "comparison", "classification", "composability", "step",
        "fund", "flow", "april", "crypto", "trezor", "yieldblox",
        "cross-chain", "smart-contract",
    }

    # Group notes by entity (shared entity names in titles)
    entity_groups = defaultdict(list)
    for slug, data in notes_data.items():
        entity = _extract_entity_from_title(data["title"])
        if entity and len(entity) > 3 and entity not in _GENERIC_ENTITIES:
            entity_groups[entity].append(slug)

    # Also match on slug prefix for protocol-specific notes
    for slug in notes_data:
        parts = slug.split("-")
        if parts and len(parts[0]) > 3 and parts[0] not in _GENERIC_ENTITIES:
            entity_groups[parts[0]].append(slug)

    # Deduplicate entity group members
    for entity in entity_groups:
        entity_groups[entity] = list(dict.fromkeys(entity_groups[entity]))

    contradictions = []

    # Check dollar amount mismatches within each entity group
    for entity, slugs_list in entity_groups.items():
        if len(slugs_list) < 2:
            continue

        # Collect the largest dollar amount per note as the "headline" figure
        slug_amounts = {}
        for slug in slugs_list:
            data = notes_data.get(slug)
            if not data or not data["amounts"]:
                continue
            # Use the largest amount as the headline figure
            largest = max(data["amounts"], key=lambda x: x[1])
            slug_amounts[slug] = largest

        # Compare pairs
        checked = set()
        for s1, (orig1, val1) in slug_amounts.items():
            for s2, (orig2, val2) in slug_amounts.items():
                if s1 >= s2:
                    continue
                pair = (s1, s2)
                if pair in checked:
                    continue
                checked.add(pair)

                if val1 == 0 or val2 == 0:
                    continue

                ratio = max(val1, val2) / min(val1, val2)
                if ratio > 1.10:  # >10% difference
                    contradictions.append({
                        "entity": entity,
                        "type": "amount_mismatch",
                        "slug1": s1,
                        "slug2": s2,
                        "detail1": orig1,
                        "detail2": orig2,
                        "suggestion": "Check which is correct (pre-recovery vs post-recovery?)",
                    })

    # Check for overview/list notes vs individual incident notes
    # Only compare when the overview note has a table/list with entity + amount on same line
    overview_slugs = [s for s in notes_data if "largest" in s or ("overview" in s and "exploit" in s)]
    for overview_slug in overview_slugs:
        overview_body = notes_data[overview_slug]["body"]
        if not overview_body:
            continue

        # Parse line-by-line: look for lines containing both an entity name and a dollar amount
        for line in overview_body.split("\n"):
            line_amounts = _extract_amounts(line)
            if not line_amounts:
                continue
            line_lower = line.lower()

            for slug, data in notes_data.items():
                if slug == overview_slug or not data["amounts"]:
                    continue
                entity = _extract_entity_from_title(data["title"])
                if not entity or len(entity) < 4 or entity in _GENERIC_ENTITIES:
                    continue

                # Check if this specific entity appears on this line
                if entity.lower() not in line_lower:
                    continue

                largest_individual = max(data["amounts"], key=lambda x: x[1])
                # Use the amount on this line (closest to entity)
                for ov_orig, ov_val in line_amounts:
                    if ov_val == 0 or largest_individual[1] == 0:
                        continue
                    ratio = max(ov_val, largest_individual[1]) / min(ov_val, largest_individual[1])
                    if ratio > 1.10:
                        contradictions.append({
                            "entity": entity,
                            "type": "overview_mismatch",
                            "slug1": slug,
                            "slug2": overview_slug,
                            "detail1": largest_individual[0],
                            "detail2": ov_orig,
                            "suggestion": "Overview note vs individual note disagree",
                        })

    # Deduplicate contradictions by entity+slugs
    seen = set()
    unique = []
    for c in contradictions:
        key = (c["entity"], tuple(sorted([c["slug1"], c["slug2"]])))
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


# ---------------------------------------------------------------------------
# Gap suggestions (enhanced gaps command)
# ---------------------------------------------------------------------------

def find_gap_suggestions(kb_name=None):
    """Generate ranked research and synthesis suggestions.

    Combines:
    - Unsynthesized patterns from find_patterns()
    - High-degree notes with no incoming synthesis links
    - Topic clusters with no insights (all concepts)
    """
    if kb_name:
        kb = resolve_kb(kb_name)
        meta_file = kb.meta_file
        graph_file = kb.graph_file
    else:
        meta_file = UNIFIED_DIR / "metadata.json"
        graph_file = UNIFIED_DIR / "graph.json"

    if not meta_file.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return []

    metadata = json.loads(meta_file.read_text())
    suggestions = []

    # 1. Unsynthesized patterns
    patterns = find_patterns(kb_name)
    for p in patterns:
        if p["has_synthesis_note"]:
            continue

        # Estimate total dollar amount for exploit-related patterns
        total_amount = 0
        for slug in p["example_notes"]:
            meta = metadata.get(slug, {})
            title = meta.get("title", "")
            amts = _extract_amounts(title)
            if amts:
                total_amount += max(a[1] for a in amts)

        amount_str = ""
        if total_amount > 0:
            if total_amount >= 1000:
                amount_str = f", ${total_amount/1000:.1f}B total"
            else:
                amount_str = f", ${total_amount:.0f}M total"

        suggestions.append({
            "priority": "HIGH",
            "type": "missing_synthesis",
            "description": f'Synthesize "{p["pattern"]}" pattern ({p["count"]} incidents{amount_str})',
            "detail": f'Missing: a dedicated synthesis note connecting all {p["pattern"]} incidents',
            "score": p["count"] * 10 + total_amount,
        })

    # 2. Topic clusters with no insights (all concepts, no insights)
    tmap = topic_map(kb_name)
    for tag, data in tmap.items():
        types = data.get("types", {})
        concept_count = types.get("concept", 0) + types.get("reference", 0)
        insight_count = types.get("insight", 0) + types.get("synthesis", 0)
        if concept_count >= 3 and insight_count == 0:
            suggestions.append({
                "priority": "MEDIUM",
                "type": "no_insights",
                "description": f'Add insights to "{tag}" cluster ({concept_count} concepts, 0 insights)',
                "detail": "All notes are factual; no cross-cutting observations yet",
                "score": concept_count * 5,
            })

    # 3. Orphan notes (no incoming links) that are not synthesis/reference type
    if graph_file.exists():
        graph = json.loads(graph_file.read_text())
        adj = graph.get("adjacency", {})

        # Count incoming links per note
        incoming_count = defaultdict(int)
        for slug, edges in adj.items():
            for target in edges.get("outgoing", []):
                incoming_count[target] += 1

        for slug, meta in metadata.items():
            bare = slug.split(":")[-1] if ":" in slug else slug
            note_type = meta.get("type", "")
            if note_type in ("synthesis", "reference"):
                continue
            if incoming_count.get(slug, 0) == 0 and incoming_count.get(bare, 0) == 0:
                suggestions.append({
                    "priority": "LOW",
                    "type": "orphan",
                    "description": f'Link orphan note: {bare} has no incoming links',
                    "detail": f'Title: {meta.get("title", bare)}',
                    "score": meta.get("word_count", 0) / 100,
                })

    # Sort by score descending
    suggestions.sort(key=lambda x: -x["score"])
    return suggestions


# ---------------------------------------------------------------------------
# Synthesis staleness
# ---------------------------------------------------------------------------

def find_stale_syntheses(kb_name=None):
    """Find synthesis notes whose dependencies have been updated more recently."""
    if kb_name:
        kb = resolve_kb(kb_name)
        meta_file = kb.meta_file
    else:
        meta_file = UNIFIED_DIR / "metadata.json"

    if not meta_file.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return []

    metadata = json.loads(meta_file.read_text())
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
            # Try both bare and qualified lookups
            dep_meta = metadata.get(dep)
            if not dep_meta:
                # Try qualifying with same KB
                slug_kb = meta.get("kb", "")
                dep_meta = metadata.get(f"{slug_kb}:{dep}")
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
                "kb": meta.get("kb", ""),
            })

    return stale


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def load_index(kb_name=None):
    """Load existing index.

    If kb_name is None, loads unified index.
    If kb_name is specified, loads that KB's index.
    """
    if kb_name:
        kb = resolve_kb(kb_name)
        index_file = kb.index_file
        vectors_file = kb.vectors_file
        vectorizer_file = kb.vectorizer_file
        meta_file = kb.meta_file
        label = kb.name
    else:
        index_file = UNIFIED_DIR / "tfidf_index.json"
        vectors_file = UNIFIED_DIR / "tfidf_vectors.npz"
        vectorizer_file = UNIFIED_DIR / "vectorizer.pkl"
        meta_file = UNIFIED_DIR / "metadata.json"
        label = "unified"

    if not index_file.exists() or not vectors_file.exists():
        print(f"[{label}] Index not found. Run: python3 .kb/kb-index.py build", file=sys.stderr)
        sys.exit(1)

    import pickle
    with open(vectorizer_file, "rb") as f:
        vectorizer = pickle.load(f)

    npz = np.load(vectors_file)
    data = json.loads(index_file.read_text())
    metadata = json.loads(meta_file.read_text())

    return npz["vectors"], vectorizer, list(npz["slugs"]), metadata


def multi_search(queries, tags=None, note_type=None, valid_only=True, top_k=10, kb_name=None):
    """Run search for multiple query variants and merge results via RRF."""
    scfg = load_config()["search"]
    k_rrf = scfg["rrf_constant"]

    all_results = []
    for q in queries:
        results = search(q, tags=tags, note_type=note_type, valid_only=valid_only,
                         top_k=top_k * 2, kb_name=kb_name)
        all_results.append(results)

    slug_scores = defaultdict(float)
    slug_meta = {}
    for results in all_results:
        for rank, r in enumerate(results):
            slug_scores[r["slug"]] += 1.0 / (k_rrf + rank)
            slug_meta[r["slug"]] = r

    ranked = sorted(slug_scores.items(), key=lambda x: -x[1])[:top_k]

    fused = []
    for slug, score in ranked:
        r = slug_meta[slug].copy()
        r["score"] = round(score, 4)
        fused.append(r)

    return fused


def search(query, tags=None, note_type=None, valid_only=True, top_k=10, kb_name=None):
    """Hybrid search: TF-IDF + dense embeddings with metadata filtering and topic weighting.

    If kb_name is None, searches unified index.
    If kb_name is specified, searches that KB only.
    """
    vectors, vectorizer, slugs, metadata = load_index(kb_name)
    scfg = load_config()["search"]

    # Determine file paths for auxiliary data
    if kb_name:
        kb = resolve_kb(kb_name)
        embeddings_file = kb.embeddings_file
        clusters_file = kb.clusters_file
        graph_file = kb.graph_file
    else:
        embeddings_file = UNIFIED_DIR / "dense_embeddings.npz"
        clusters_file = UNIFIED_DIR / "clusters.json"
        graph_file = UNIFIED_DIR / "graph.json"

    # TF-IDF similarity
    query_vec = vectorizer.transform([query]).toarray()
    tfidf_sims = cosine_similarity(query_vec, vectors)[0]

    # Title, slug, and tag boosting
    title_boost = scfg.get("title_boost", 2.0)
    tag_boost = scfg.get("tag_boost", 1.5)
    query_lower = query.lower()
    stop_words = {"the", "a", "an", "is", "are", "how", "do", "does", "what", "which",
                  "and", "or", "for", "in", "of", "to", "with", "can", "be", "it", "its",
                  "this", "that", "these", "those", "my", "your", "their", "between"}
    query_words = set(re.sub(r'[^\w\s]', '', query_lower).split()) - stop_words

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

            # For unified slugs like "general:tcp-congestion", extract bare slug
            bare_slug = slug.split(":", 1)[-1] if ":" in slug else slug
            slug_words = set(bare_slug.split("-"))

            tag_words = set()
            tag_compounds = set()
            for t in meta.get("tags", []):
                tag_words.update(t.lower().split("-"))
                tag_compounds.add(t.lower())

            title_overlap = len(query_words & title_words) / len(query_words)
            if title_overlap > 0:
                tfidf_sims[i] *= (1.0 + (title_boost - 1.0) * title_overlap)

            slug_overlap = len(query_words & slug_words) / len(query_words)
            if slug_overlap > 0:
                tfidf_sims[i] *= (1.0 + (title_boost - 1.0) * slug_overlap)

            bigram_match = bool(query_bigrams & tag_compounds)
            if bigram_match:
                tfidf_sims[i] *= tag_boost
            else:
                tag_overlap = len(query_words & tag_words) / len(query_words)
                if tag_overlap > 0:
                    tfidf_sims[i] *= (1.0 + (tag_boost - 1.0) * tag_overlap)

    # Dense embedding similarity
    dense_sims = np.zeros_like(tfidf_sims)
    has_dense = False
    if embeddings_file.exists():
        try:
            dense_npz = np.load(embeddings_file)
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
    if clusters_file.exists():
        try:
            clusters = json.loads(clusters_file.read_text())
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

    # Graph expansion
    gcfg = load_config().get("graph", {})
    expansion_hops = gcfg.get("expansion_hops", 1)
    expansion_seeds = gcfg.get("expansion_seeds", 5)
    expansion_decay = gcfg.get("expansion_decay", 0.5)

    if expansion_hops > 0 and graph_file.exists():
        try:
            graph_data = json.loads(graph_file.read_text())
            adj = graph_data.get("adjacency", {})
            slug_to_idx = {s: i for i, s in enumerate(slugs)}

            seed_indices = np.argsort(sims)[::-1][:expansion_seeds]
            seeds = [(slugs[i], float(sims[i])) for i in seed_indices if sims[i] > scfg["min_score"]]

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
        # Extract bare slug for display; keep qualified slug in "qualified_slug"
        if ":" in slug:
            kb_part, bare_slug = slug.split(":", 1)
        else:
            kb_part = meta.get("kb", "")
            bare_slug = slug
        results.append({
            "slug": bare_slug,
            "qualified_slug": slug,
            "title": meta.get("title", bare_slug),
            "score": round(float(sims[idx]), 4),
            "type": meta.get("type", ""),
            "tags": meta.get("tags", []),
            "deprecated": bool(meta.get("deprecated_by")),
            "kb": meta.get("kb", kb_part),
        })

    return results


def find_similar(slug, top_k=10, kb_name=None):
    """Find notes most similar to a given note.

    If kb_name is None, uses unified index. The slug can be bare or qualified.
    """
    vectors, vectorizer, slugs, metadata = load_index(kb_name)
    min_score = cfg("similarity", "min_score")

    # Try to find the slug in the index (bare or qualified)
    target_idx = None
    if slug in slugs:
        target_idx = slugs.index(slug)
    else:
        # Try qualifying with each KB
        for s in slugs:
            if s.endswith(f":{slug}"):
                target_idx = slugs.index(s)
                break

    if target_idx is None:
        print(f"Note '{slug}' not found in index.", file=sys.stderr)
        return []

    sims = cosine_similarity(vectors[target_idx:target_idx+1], vectors)[0]
    sims[target_idx] = 0

    ranked = np.argsort(sims)[::-1][:top_k]
    results = []
    for i in ranked:
        if sims[i] < min_score:
            break
        s = slugs[i]
        meta = metadata.get(s, {})
        if ":" in s:
            kb_part, bare = s.split(":", 1)
        else:
            kb_part, bare = meta.get("kb", ""), s
        results.append({
            "slug": bare,
            "title": meta.get("title", bare),
            "score": round(float(sims[i]), 4),
            "kb": meta.get("kb", kb_part),
        })
    return results


def check_contradictions(slug, kb_name=None):
    """Find notes that might contradict a given note."""
    similar = find_similar(slug, top_k=20, kb_name=kb_name)
    _, _, slugs, metadata = load_index(kb_name)
    overlap_thresh = cfg("similarity", "contradiction_overlap")
    high_thresh = cfg("similarity", "contradiction_high")

    candidates = []
    for s in similar:
        if s["score"] > overlap_thresh:
            # Find full metadata
            qualified = f"{s['kb']}:{s['slug']}" if s.get("kb") else s["slug"]
            meta = metadata.get(qualified, metadata.get(s["slug"], {}))
            candidates.append({
                **s,
                "tags": meta.get("tags", []),
                "type": meta.get("type", ""),
                "warning": "HIGH OVERLAP -- review for consistency" if s["score"] > high_thresh else "moderate overlap",
            })
    return candidates


def find_stale(days_threshold=None, kb_name=None):
    """Find notes that may be outdated."""
    if days_threshold is None:
        days_threshold = cfg("staleness", "default_days")

    if kb_name:
        kb = resolve_kb(kb_name)
        meta_file = kb.meta_file
    else:
        meta_file = UNIFIED_DIR / "metadata.json"

    if not meta_file.exists():
        print("No index. Run: python3 .kb/kb-index.py build")
        return []

    metadata = json.loads(meta_file.read_text())
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
            if ":" in slug:
                kb_part, bare = slug.split(":", 1)
            else:
                kb_part, bare = meta.get("kb", ""), slug
            stale.append({
                "slug": bare,
                "title": meta.get("title", bare),
                "reasons": reasons,
                "kb": meta.get("kb", kb_part),
            })

    return sorted(stale, key=lambda x: len(x["reasons"]), reverse=True)


def check_coverage(topic, kb_name=None):
    """Check if the KB has adequate coverage of a topic."""
    vectors, vectorizer, slugs, metadata = load_index(kb_name)
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
        if ":" in slug:
            kb_part, bare = slug.split(":", 1)
        else:
            kb_part, bare = meta.get("kb", ""), slug
        results.append({
            "slug": bare,
            "title": meta.get("title", bare),
            "score": round(float(tfidf_sims[idx]), 4),
            "kb": meta.get("kb", kb_part),
        })

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


def stats(kb_name=None):
    """Print index statistics.

    If kb_name is None, show per-KB stats + total.
    If kb_name is specified, show only that KB.
    """
    reg = get_registry()

    if kb_name:
        kbs_to_show = [resolve_kb(kb_name)]
    else:
        kbs_to_show = reg.all_kbs()

    grand_total_notes = 0
    grand_total_words = 0

    for kb in kbs_to_show:
        if not kb.meta_file.exists():
            print(f"[{kb.name}] No index. Run: python3 .kb/kb-index.py build")
            continue

        metadata = json.loads(kb.meta_file.read_text())
        index_data = json.loads(kb.index_file.read_text()) if kb.index_file.exists() else {}

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

        grand_total_notes += len(metadata)
        grand_total_words += total_words

        print(f"=== {kb.name} {'(private)' if kb.private else ''} ===")
        print(f"Notes: {len(metadata)}")
        print(f"Features: {index_data.get('feature_count', 'unknown')}")
        print(f"Total words: {total_words:,}")
        print(f"Deprecated: {deprecated}")

        total = len(metadata)
        print(f"Types:")
        for t in sorted(types.keys()):
            count = types[t]
            pct = count / total * 100 if total else 0
            bar = "=" * int(pct / 2)
            print(f"  {t:12s} {count:4d} ({pct:4.1f}%) {bar}")

        provider, detail = detect_embedding_provider()
        if kb.embeddings_file.exists():
            dense_npz = np.load(kb.embeddings_file)
            dims = dense_npz["embeddings"].shape[1]
            print(f"Dense embeddings: yes ({dims} dims, {dense_npz['embeddings'].shape[0]} vectors)")
        elif provider:
            print(f"Dense embeddings: available but not built (run 'build --embed'). Provider: {detail}")
        else:
            print(f"Dense embeddings: not available. {detail}")

        print(f"Topic clusters: {len(json.loads(kb.clusters_file.read_text())) if kb.clusters_file.exists() else 0}")

        if kb.graph_file.exists():
            g = json.loads(kb.graph_file.read_text())
            print(f"Link graph: {g['node_count']} nodes, {g['edge_count']} edges, {g['orphan_count']} orphans")

        print(f"Top tags: {json.dumps(dict(sorted(all_tags.items(), key=lambda x: -x[1])[:20]), indent=2)}")
        print()

    # Show unified stats if multiple KBs and no specific KB requested
    if not kb_name and len(kbs_to_show) > 1:
        unified_meta_file = UNIFIED_DIR / "metadata.json"
        if unified_meta_file.exists():
            unified_metadata = json.loads(unified_meta_file.read_text())
            print(f"=== UNIFIED (searchable) ===")
            print(f"Total notes: {len(unified_metadata)}")
            print(f"Grand total words: {grand_total_words:,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Extract global --kb flag before command dispatch
    kb_name = None
    if "--kb" in sys.argv:
        idx = sys.argv.index("--kb")
        if idx + 1 < len(sys.argv):
            kb_name = sys.argv[idx + 1]
            sys.argv = sys.argv[:idx] + sys.argv[idx+2:]

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "build":
        incremental = "--incremental" in sys.argv
        do_embed = "--embed" in sys.argv
        build_index(incremental=incremental, kb_name=kb_name)

        if do_embed:
            # Build dense embeddings for specified or all KBs
            reg = get_registry()
            if kb_name:
                kbs_to_embed = [resolve_kb(kb_name)]
            else:
                kbs_to_embed = reg.all_kbs()

            all_unified_slugs = []
            all_unified_texts = []
            for kb in kbs_to_embed:
                if not kb.notes_dir.exists():
                    continue
                notes_list = sorted(kb.notes_dir.glob("*.md"))
                kb_slugs = [p.stem for p in notes_list]
                kb_texts = []
                for p in notes_list:
                    fm, body, _ = parse_note(p)
                    kb_texts.append(extract_contextual_text(fm, body))
                build_dense_embeddings(kb_slugs, kb_texts, kb_config=kb)

                if not kb.private:
                    for i, s in enumerate(kb_slugs):
                        all_unified_slugs.append(f"{kb.name}:{s}")
                        all_unified_texts.append(kb_texts[i])

            # Build unified dense embeddings
            if not kb_name and all_unified_slugs:
                build_dense_embeddings(all_unified_slugs, all_unified_texts, kb_config=None)
        else:
            provider, detail = detect_embedding_provider()
            # Check any KB for existing embeddings
            any_embeddings = False
            reg = get_registry()
            for kb in reg.all_kbs():
                if kb.embeddings_file.exists():
                    any_embeddings = True
                    break
            if any_embeddings:
                print(f"Dense embeddings: cached (run 'build --embed' to rebuild)")
            elif provider:
                print(f"Dense embeddings: available via {detail} (run 'build --embed' to enable)")
            else:
                print(f"Dense embeddings: not available ({detail})")

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py search 'query' [--tags t1,t2] [--type concept] [--multi 'q2' 'q3'] [--kb name]")
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
                i += 1
                while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                    extra_queries.append(sys.argv[i])
                    i += 1
            else:
                i += 1

        if extra_queries:
            all_queries = [query] + extra_queries
            results = multi_search(all_queries, tags=tags, note_type=note_type, kb_name=kb_name)
        else:
            results = search(query, tags=tags, note_type=note_type, kb_name=kb_name)
        for r in results:
            dep = " [DEPRECATED]" if r.get("deprecated") else ""
            kb_label = f" [{r['kb']}]" if r.get("kb") else ""
            print(f"  {r['score']:.4f}  {r['slug']}{dep}{kb_label}")
            print(f"           {r['title']} ({r['type']}) [{', '.join(r['tags'][:5])}]")

    elif cmd == "similar":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py similar <note-slug> [--kb name]")
            sys.exit(1)
        results = find_similar(sys.argv[2], kb_name=kb_name)
        for r in results:
            kb_label = f" [{r['kb']}]" if r.get("kb") else ""
            print(f"  {r['score']:.4f}  {r['slug']}{kb_label} -- {r['title']}")

    elif cmd == "contradictions":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py contradictions <note-slug> [--kb name]")
            sys.exit(1)
        results = check_contradictions(sys.argv[2], kb_name=kb_name)
        for r in results:
            kb_label = f" [{r['kb']}]" if r.get("kb") else ""
            print(f"  {r['score']:.4f}  {r['slug']}{kb_label} -- {r['warning']}")

    elif cmd == "stale":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else None
        results = find_stale(days, kb_name=kb_name)
        threshold = days or cfg("staleness", "default_days")
        print(f"Stale notes (>{threshold} days or expired):")
        for r in results:
            kb_label = f" [{r['kb']}]" if r.get("kb") else ""
            print(f"  {r['slug']}{kb_label} -- {', '.join(r['reasons'])}")

    elif cmd == "stale-syntheses":
        results = find_stale_syntheses(kb_name=kb_name)
        if not results:
            print("No stale synthesis notes found.")
        else:
            print(f"Stale synthesis notes ({len(results)}):")
            for r in results:
                kb_label = f" [{r['kb']}]" if r.get("kb") else ""
                print(f"  {r['slug']}{kb_label} (updated {r['last_updated']})")
                for dep in r["stale_dependencies"]:
                    print(f"    -> {dep}")

    elif cmd == "coverage":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py coverage 'topic' [--kb name]")
            sys.exit(1)
        result = check_coverage(sys.argv[2], kb_name=kb_name)
        print(f"Coverage: {result['level']} (confidence: {result['confidence']})")
        if result.get("notes"):
            print("Relevant notes:")
            for n in result["notes"]:
                kb_label = f" [{n['kb']}]" if n.get("kb") else ""
                print(f"  {n['score']:.4f}  {n['slug']}{kb_label}")

    elif cmd == "clusters":
        if kb_name:
            kb = resolve_kb(kb_name)
            clusters_file = kb.clusters_file
        else:
            clusters_file = UNIFIED_DIR / "clusters.json"

        if not clusters_file.exists():
            print("No clusters. Run: python3 .kb/kb-index.py build")
            sys.exit(1)
        clusters = json.loads(clusters_file.read_text())
        print(f"Topic clusters ({len(clusters)}):")
        for tag, data in sorted(clusters.items(), key=lambda x: -x[1]["count"]):
            print(f"  {tag} ({data['count']} notes)")
            print(f"    tags: {', '.join(data['tags'][:5])}")
            print(f"    sample: {', '.join(data['sample_notes'][:3])}")

    elif cmd == "lint":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        results = lint_all(target, kb_name=kb_name)
        if not results:
            print("All notes pass lint checks.")
        else:
            total_issues = sum(len(v) for v in results.values())
            errors = sum(1 for v in results.values() for sev, _ in v if sev == "error")
            warnings_count = sum(1 for v in results.values() for sev, _ in v if sev == "warning")
            infos = sum(1 for v in results.values() for sev, _ in v if sev == "info")
            print(f"Lint: {len(results)} notes with issues ({errors} errors, {warnings_count} warnings, {infos} info)")
            print()
            for slug, issues in sorted(results.items()):
                print(f"  {slug}:")
                for severity, msg in issues:
                    icon = {"error": "X", "warning": "!", "info": "i"}[severity]
                    print(f"    {icon} [{severity}] {msg}")

    elif cmd == "graph":
        subcmd = sys.argv[2] if len(sys.argv) > 2 else "summary"

        if subcmd == "summary":
            g = load_graph(kb_name)
            comps = graph_components(kb_name)
            label = kb_name or "unified"
            print(f"Link graph ({label}):")
            print(f"  Nodes: {g['node_count']}")
            print(f"  Edges: {g['edge_count']}")
            print(f"  Orphans: {g['orphan_count']}")
            print(f"  Connected components: {len(comps)}")
            if len(comps) > 1:
                print(f"  Largest component: {len(comps[0])} nodes")
                print(f"  Smallest component: {len(comps[-1])} nodes")

        elif subcmd == "orphans":
            orphans = graph_orphans(kb_name)
            print(f"Orphan notes ({len(orphans)}) -- no incoming or outgoing links:")
            for s in orphans:
                print(f"  {s}")

        elif subcmd == "components":
            comps = graph_components(kb_name)
            print(f"Connected components ({len(comps)}):")
            for i, comp in enumerate(comps):
                if len(comp) <= 5:
                    print(f"  Component {i+1} ({len(comp)}): {', '.join(comp)}")
                else:
                    print(f"  Component {i+1} ({len(comp)}): {', '.join(comp[:5])}...")

        elif subcmd == "neighbors":
            if len(sys.argv) < 4:
                print("Usage: kb-index.py graph neighbors <slug> [hops] [--kb name]")
                sys.exit(1)
            slug = sys.argv[3]
            hops = int(sys.argv[4]) if len(sys.argv) > 4 else 2
            neighbors = graph_neighbors(slug, hops, kb_name=kb_name)
            if not neighbors:
                print(f"No neighbors found for '{slug}' within {hops} hops.")
            else:
                print(f"Neighbors of '{slug}' within {hops} hops ({len(neighbors)}):")
                for s, d in sorted(neighbors.items(), key=lambda x: (x[1], x[0])):
                    print(f"  {d} hop{'s' if d > 1 else ' '}  {s}")

        elif subcmd == "bridges":
            bridges = graph_bridges(kb_name)
            if not bridges:
                print("No bridge notes found (graph is well-connected or fully disconnected).")
            else:
                print(f"Bridge notes ({len(bridges)}) -- removal increases component count:")
                for b in bridges:
                    print(f"  {b['slug']} -> {b['components_after_removal']} components")

        else:
            print(f"Unknown graph subcommand: {subcmd}")
            print("Available: summary, orphans, components, neighbors, bridges")

    elif cmd == "backlink":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        targets = [target] if target else None
        changes = auto_backlink(targets, kb_name=kb_name)
        if not changes:
            print("All backlinks are up to date.")
        else:
            print(f"Added {len(changes)} backlinks:")
            for src, tgt, action in changes:
                print(f"  {src} -> {tgt}: {action}")

    elif cmd == "quick":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py quick 'query' [--kb name]")
            sys.exit(1)
        results = quick_search(sys.argv[2], kb_name=kb_name)
        if not results:
            print("No matches. Try full search: kb-index.py search 'query'")
        else:
            for r in results:
                kb_label = f" [{r['kb']}]" if r.get("kb") else ""
                print(f"  {r['score']:.2f}  {r['slug']}{kb_label}")
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
        tmap = topic_map(kb_name)
        if not tmap:
            print("No data. Run: python3 .kb/kb-index.py build")
        else:
            print(f"Topic Map ({len(tmap)} topics):\n")
            for tag, data in sorted(tmap.items(), key=lambda x: -x[1]["count"]):
                types_str = ", ".join(f"{t}:{c}" for t, c in sorted(data["types"].items()))
                density_bar = "=" * int(data["link_density"] * 20)
                print(f"  {tag} ({data['count']} notes, ~{data['avg_words']} words/note)")
                print(f"    types: {types_str}")
                print(f"    density: {data['link_density']:.1%} {density_bar}")
                print(f"    tags: {', '.join(data['tags'][:5])}")
                print()

    elif cmd == "explore":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py explore <start-slug> [max-steps] [--kb name]")
            sys.exit(1)
        slug = sys.argv[2]
        steps = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        path = explore_path(slug, steps, kb_name=kb_name)
        if not path:
            print(f"No path from '{slug}'.")
        else:
            if kb_name:
                kb = resolve_kb(kb_name)
                meta_file = kb.meta_file
            else:
                meta_file = UNIFIED_DIR / "metadata.json"
            metadata = json.loads(meta_file.read_text())
            print(f"Reading path from '{slug}' ({len(path)} notes):\n")
            for i, s in enumerate(path):
                meta = metadata.get(s, {})
                arrow = "  ->" if i > 0 else "   "
                print(f"  {arrow} {i+1}. [{meta.get('type','')}] {meta.get('title', s)}")
                print(f"       {s} ({meta.get('word_count', '?')} words)")

    elif cmd == "patterns":
        patterns = find_patterns(kb_name)
        if not patterns:
            print("No recurring patterns detected (need 3+ notes per tag).")
        else:
            print(f"Detected patterns ({len(patterns)}):\n")
            for p in patterns:
                synth = p["has_synthesis_note"]
                if synth:
                    synth_label = f"HAS synthesis: {synth}"
                else:
                    synth_label = "NO synthesis note"
                print(f"  {p['pattern']} ({p['count']} notes, {synth_label})")
                examples = ", ".join(p["example_notes"])
                print(f"    -> {examples}")
                if not synth:
                    print(f"    Suggestion: create a synthesis note for this pattern")
                print()

    elif cmd == "contradictions-scan":
        results = scan_contradictions(kb_name)
        if not results:
            print("No potential contradictions found.")
        else:
            print(f"Potential contradictions found ({len(results)}):\n")
            for c in results:
                entity = c["entity"].capitalize()
                print(f"  {entity}: {c['detail1']} in {c['slug1']} vs {c['detail2']} in {c['slug2']}")
                print(f"    -> {c['suggestion']}")
                print()

    elif cmd == "gaps":
        subcmd = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "topics"

        if subcmd == "topics":
            gaps = find_topic_gaps(kb_name)
            if not gaps:
                print("No significant topic gaps found.")
            else:
                print(f"Topic gaps ({len(gaps)}):\n")
                for g in gaps:
                    print(f"  {g['topic']} ({g['count']} notes)")
                    for issue in g["issues"]:
                        print(f"    - {issue}")
                    print()

        elif subcmd == "research":
            rgaps = find_research_gaps(kb_name)
            if not rgaps:
                print("No unresolved research gaps found.")
            else:
                total = sum(len(r["gaps"]) for r in rgaps)
                print(f"Unresolved research gaps ({total} across {len(rgaps)} hubs):\n")
                for r in rgaps:
                    print(f"  [{r['kb']}] {r['title']}:")
                    for gap in r["gaps"]:
                        print(f"    - {gap}")
                    print()

        elif subcmd == "suggestions":
            suggestions = find_gap_suggestions(kb_name)
            if not suggestions:
                print("No research or synthesis suggestions.")
            else:
                print(f"Research & synthesis suggestions ({len(suggestions)}):\n")
                for i, s in enumerate(suggestions, 1):
                    print(f"  {i}. [{s['priority']}] {s['description']}")
                    print(f"     {s['detail']}")
                    print()

        elif subcmd == "all":
            tgaps = find_topic_gaps(kb_name)
            rgaps = find_research_gaps(kb_name)
            if tgaps:
                print(f"=== Topic gaps ({len(tgaps)}) ===\n")
                for g in tgaps:
                    print(f"  {g['topic']} ({g['count']} notes)")
                    for issue in g["issues"]:
                        print(f"    - {issue}")
                    print()
            if rgaps:
                total = sum(len(r["gaps"]) for r in rgaps)
                print(f"=== Research gaps ({total} across {len(rgaps)} hubs) ===\n")
                for r in rgaps:
                    print(f"  [{r['kb']}] {r['title']}:")
                    for gap in r["gaps"]:
                        print(f"    - {gap}")
                    print()
            if not tgaps and not rgaps:
                print("No gaps found.")

        else:
            print("Usage: kb-index.py gaps [topics|research|suggestions|all] [--kb name]")

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
        stats(kb_name)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
