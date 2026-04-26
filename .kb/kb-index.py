#!/usr/bin/env python3
"""KB search index: build, query, and maintain.

Usage:
  python3 .kb/kb-index.py build                    # Build/rebuild TF-IDF index
  python3 .kb/kb-index.py build --embed            # Also build dense embeddings (needs API key)
  python3 .kb/kb-index.py search "query text"      # Hybrid search (top 10)
  python3 .kb/kb-index.py search "query" --tags security,rag  # With tag filter
  python3 .kb/kb-index.py search "query" --type concept       # With type filter
  python3 .kb/kb-index.py similar note-slug         # Find similar notes
  python3 .kb/kb-index.py stale                     # Find temporally stale notes
  python3 .kb/kb-index.py contradictions note-slug  # Find potentially conflicting notes
  python3 .kb/kb-index.py coverage "topic"          # Check if KB covers a topic
  python3 .kb/kb-index.py stats                     # Index statistics
  python3 .kb/kb-index.py clusters                  # Show topic clusters

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
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

BASE = Path(__file__).parent.parent
NOTES = BASE / "notes"
INDEX_DIR = BASE / ".kb" / "index"
INDEX_FILE = INDEX_DIR / "tfidf_index.json"
VECTORS_FILE = INDEX_DIR / "tfidf_vectors.npz"
VECTORIZER_FILE = INDEX_DIR / "vectorizer.pkl"
EMBEDDINGS_FILE = INDEX_DIR / "dense_embeddings.npz"
META_FILE = INDEX_DIR / "metadata.json"
CLUSTERS_FILE = INDEX_DIR / "clusters.json"


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


def extract_contextual_text(fm, body):
    """Prepend metadata context to body for better embedding (Anthropic contextual retrieval approach)."""
    parts = []
    if fm.get("title"):
        parts.append(f"Title: {fm['title']}")
    if fm.get("type"):
        parts.append(f"Type: {fm['type']}")
    if fm.get("tags"):
        parts.append(f"Tags: {', '.join(fm['tags'])}")
    # Strip wikilinks and markdown formatting for cleaner text
    clean_body = re.sub(r'\[\[([^\]|]+)\|?([^\]]*)\]\]', lambda m: m.group(2) or m.group(1), body)
    clean_body = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean_body)
    clean_body = re.sub(r'[#*`>|]', ' ', clean_body)
    clean_body = re.sub(r'\s+', ' ', clean_body).strip()
    parts.append(clean_body)
    return " | ".join(parts)


def build_index():
    """Build TF-IDF index over all notes with contextual metadata prepending."""
    notes = sorted(NOTES.glob("*.md"))
    if not notes:
        print("No notes found.")
        return

    slugs = []
    texts = []
    metadata = {}

    for path in notes:
        slug = path.stem
        fm, body, content_hash = parse_note(path)

        # Contextual text (metadata-prepended, Anthropic approach)
        contextual_text = extract_contextual_text(fm, body)

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
            "related": [re.search(r'\[\[([^\]|]+)', str(r)).group(1)
                        for r in fm.get("related", [])
                        if re.search(r'\[\[([^\]|]+)', str(r))],
            "content_hash": content_hash,
            "word_count": len(body.split()),
        }

    # Build TF-IDF matrix
    vectorizer = TfidfVectorizer(
        max_features=10000,
        ngram_range=(1, 2),  # Unigrams + bigrams
        min_df=1,
        max_df=0.95,
        sublinear_tf=True,
        stop_words="english",
    )
    tfidf_matrix = vectorizer.fit_transform(texts)

    # Save index
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # Save sparse matrix as dense (manageable at <500 notes)
    np.savez_compressed(VECTORS_FILE,
                        vectors=tfidf_matrix.toarray(),
                        slugs=np.array(slugs))

    # Save vectorizer via pickle (reliable reconstruction)
    import pickle
    with open(VECTORIZER_FILE, "wb") as f:
        pickle.dump(vectorizer, f)

    # Save index metadata
    index_data = {
        "slugs": slugs,
        "built_at": datetime.now().isoformat(),
        "note_count": len(slugs),
        "feature_count": len(vectorizer.vocabulary_),
    }
    INDEX_FILE.write_text(json.dumps(index_data, indent=2))

    # Save metadata
    META_FILE.write_text(json.dumps(metadata, indent=2))

    # Build topic clusters from tags
    build_clusters(slugs, metadata)

    print(f"Index built: {len(slugs)} notes, {len(vectorizer.vocabulary_)} features")
    return tfidf_matrix, vectorizer, slugs, metadata


def detect_embedding_provider():
    """Auto-detect the best available embedding provider. Returns (provider, detail) or (None, reason)."""
    import os

    # 1. Check for local sentence-transformers (best: no API key, free, fast)
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        return "local", "sentence-transformers (all-MiniLM-L6-v2)"
    except ImportError:
        pass

    # 2. Check for Voyage API key (best quality for code/technical content)
    if os.environ.get("VOYAGE_API_KEY"):
        return "voyage", "Voyage AI (voyage-3-lite)"

    # 3. Check for OpenAI API key
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", "OpenAI (text-embedding-3-small)"

    return None, "No provider available. Options: pip install sentence-transformers, or set VOYAGE_API_KEY/OPENAI_API_KEY"


def build_dense_embeddings(slugs, texts):
    """Build dense embeddings using best available provider. Fully optional."""
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


def build_clusters(slugs, metadata):
    """Build topic clusters from tag co-occurrence for routing."""
    tag_to_slugs = {}
    slug_to_tags = {}
    for slug in slugs:
        meta = metadata.get(slug, {})
        note_tags = meta.get("tags", [])
        slug_to_tags[slug] = note_tags
        for tag in note_tags:
            if tag not in tag_to_slugs:
                tag_to_slugs[tag] = []
            tag_to_slugs[tag].append(slug)

    # Identify major clusters (tags with 5+ notes)
    major_tags = {t: slugs_list for t, slugs_list in tag_to_slugs.items() if len(slugs_list) >= 5}

    # Merge overlapping clusters (if >60% of notes share two tags, they're one cluster)
    clusters = {}
    for tag, tag_slugs in sorted(major_tags.items(), key=lambda x: -len(x[1])):
        tag_set = set(tag_slugs)
        merged = False
        for cname, cdata in clusters.items():
            overlap = len(tag_set & cdata["slugs"]) / min(len(tag_set), len(cdata["slugs"]))
            if overlap > 0.6:
                cdata["slugs"] |= tag_set
                cdata["tags"].append(tag)
                merged = True
                break
        if not merged:
            clusters[tag] = {"slugs": tag_set, "tags": [tag], "count": len(tag_slugs)}

    # Finalize
    result = {}
    for primary_tag, cdata in clusters.items():
        result[primary_tag] = {
            "tags": cdata["tags"][:5],
            "count": len(cdata["slugs"]),
            "sample_notes": sorted(cdata["slugs"])[:5],
        }

    CLUSTERS_FILE.write_text(json.dumps(result, indent=2))
    print(f"Clusters built: {len(result)} topic clusters")


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


def search(query, tags=None, note_type=None, valid_only=True, top_k=10):
    """Hybrid search: TF-IDF + dense embeddings (when available) with metadata filtering and topic weighting."""
    vectors, vectorizer, slugs, metadata = load_index()

    # TF-IDF similarity
    query_vec = vectorizer.transform([query]).toarray()
    tfidf_sims = cosine_similarity(query_vec, vectors)[0]

    # Dense embedding similarity (if available — auto-detects provider)
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
            pass  # Fall back to TF-IDF only

    # Hybrid fusion: RRF (Reciprocal Rank Fusion) when both available, else TF-IDF only
    if has_dense:
        k_rrf = 60  # RRF constant
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

    # Topic weighting: boost underrepresented topic clusters
    if CLUSTERS_FILE.exists():
        try:
            clusters = json.loads(CLUSTERS_FILE.read_text())
            cluster_sizes = {t: c["count"] for t, c in clusters.items()}
            max_cluster = max(cluster_sizes.values()) if cluster_sizes else 1
            for i, slug in enumerate(slugs):
                meta = metadata.get(slug, {})
                note_tags = meta.get("tags", [])
                # Find the largest cluster this note belongs to
                largest_cluster = 0
                for t in note_tags:
                    if t in cluster_sizes:
                        largest_cluster = max(largest_cluster, cluster_sizes[t])
                if largest_cluster > 0:
                    # Boost notes from smaller clusters (inverse log scale)
                    boost = 1.0 + 0.15 * (1 - largest_cluster / max_cluster)
                    sims[i] *= boost
        except Exception:
            pass

    # Apply metadata filters
    for i, slug in enumerate(slugs):
        meta = metadata.get(slug, {})

        # Tag filter
        if tags:
            note_tags = set(meta.get("tags", []))
            if not note_tags.intersection(set(tags)):
                sims[i] = 0

        # Type filter
        if note_type and meta.get("type") != note_type:
            sims[i] = 0

        # Temporal validity filter
        if valid_only and meta.get("valid_until"):
            try:
                until = date.fromisoformat(str(meta["valid_until"]))
                if until < date.today():
                    sims[i] *= 0.3  # Penalize but don't exclude
            except (ValueError, TypeError):
                pass

        # Penalize deprecated notes
        if meta.get("deprecated_by"):
            sims[i] *= 0.2

    # Rank
    ranked = np.argsort(sims)[::-1][:top_k]

    results = []
    for idx in ranked:
        if sims[idx] < 0.01:
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

    if slug not in slugs:
        print(f"Note '{slug}' not found in index.", file=sys.stderr)
        return []

    idx = slugs.index(slug)
    sims = cosine_similarity(vectors[idx:idx+1], vectors)[0]
    sims[idx] = 0  # Exclude self

    ranked = np.argsort(sims)[::-1][:top_k]

    results = []
    for i in ranked:
        if sims[i] < 0.05:
            break
        results.append({
            "slug": slugs[i],
            "title": metadata.get(slugs[i], {}).get("title", slugs[i]),
            "score": round(float(sims[i]), 4),
        })

    return results


def check_contradictions(slug):
    """Find notes that might contradict a given note (high similarity + different conclusions)."""
    similar = find_similar(slug, top_k=20)
    vectors, vectorizer, slugs, metadata = load_index()

    # Notes with high content overlap are candidates for contradiction
    candidates = []
    for s in similar:
        if s["score"] > 0.15:  # Meaningful overlap
            meta = metadata.get(s["slug"], {})
            candidates.append({
                **s,
                "tags": meta.get("tags", []),
                "type": meta.get("type", ""),
                "warning": "HIGH OVERLAP — review for consistency" if s["score"] > 0.4 else "moderate overlap",
            })

    return candidates


def find_stale(days_threshold=180):
    """Find notes that may be outdated based on update date or temporal validity."""
    metadata = json.loads(META_FILE.read_text())
    today = date.today()
    stale = []

    for slug, meta in metadata.items():
        reasons = []

        # Check updated date
        if meta.get("updated"):
            try:
                updated = date.fromisoformat(str(meta["updated"]))
                age_days = (today - updated).days
                if age_days > days_threshold:
                    reasons.append(f"not updated in {age_days} days")
            except (ValueError, TypeError):
                pass

        # Check valid_until
        if meta.get("valid_until"):
            try:
                until = date.fromisoformat(str(meta["valid_until"]))
                if until < today:
                    reasons.append(f"expired on {meta['valid_until']}")
            except (ValueError, TypeError):
                pass

        # Check deprecated
        if meta.get("deprecated_by"):
            reasons.append(f"deprecated by [[{meta['deprecated_by']}]]")

        if reasons:
            stale.append({"slug": slug, "title": meta.get("title", slug), "reasons": reasons})

    return sorted(stale, key=lambda x: len(x["reasons"]), reverse=True)


def check_coverage(topic):
    """Check if the KB has adequate coverage of a topic. Uses TF-IDF scores (not hybrid RRF) for stable assessment."""
    # Use TF-IDF directly for coverage — it has natural similarity interpretation
    vectors, vectorizer, slugs, metadata = load_index()
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

    top_score = results[0]["score"] if results else 0
    num_relevant = sum(1 for r in results if r["score"] > 0.05)

    if top_score > 0.15 and num_relevant >= 3:
        level = "well-covered"
        confidence = min(top_score * 4, 1.0)
    elif top_score > 0.08 and num_relevant >= 1:
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
    print(f"Types: {json.dumps(types, indent=2)}")
    print(f"Top tags: {json.dumps(dict(sorted(all_tags.items(), key=lambda x: -x[1])[:20]), indent=2)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "build":
        build_index()
        if "--embed" in sys.argv:
            notes_list = sorted(NOTES.glob("*.md"))
            slugs = [p.stem for p in notes_list]
            texts = []
            for p in notes_list:
                fm, body, _ = parse_note(p)
                texts.append(extract_contextual_text(fm, body))
            build_dense_embeddings(slugs, texts)
        else:
            # Show embedding status
            provider, detail = detect_embedding_provider()
            if EMBEDDINGS_FILE.exists():
                print(f"Dense embeddings: cached (run 'build --embed' to rebuild)")
            elif provider:
                print(f"Dense embeddings: available via {detail} (run 'build --embed' to enable)")
            else:
                print(f"Dense embeddings: not available ({detail})")

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: kb-index.py search 'query' [--tags t1,t2] [--type concept]")
            sys.exit(1)
        query = sys.argv[2]
        tags = None
        note_type = None
        for i, arg in enumerate(sys.argv[3:], 3):
            if arg == "--tags" and i + 1 < len(sys.argv):
                tags = sys.argv[i + 1].split(",")
            if arg == "--type" and i + 1 < len(sys.argv):
                note_type = sys.argv[i + 1]

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
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 180
        results = find_stale(days)
        print(f"Stale notes (>{days} days or expired):")
        for r in results:
            print(f"  {r['slug']} — {', '.join(r['reasons'])}")

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

    elif cmd == "stats":
        stats()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
