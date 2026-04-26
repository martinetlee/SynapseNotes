#!/usr/bin/env python3
"""Retrieval quality evaluation for the KB search index.

Computes Recall@5, MRR, and nDCG@5 against a golden query set.
Run: python3 tests/test_retrieval.py [--verbose]
"""
import json
import sys
from pathlib import Path

# Add project root to path so we can import kb-index functions
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / ".kb"))

# Import after path setup
import importlib
kb_index = importlib.import_module("kb-index")


def load_eval_data():
    """Load golden query set."""
    eval_file = ROOT / "tests" / "eval_data.json"
    data = json.loads(eval_file.read_text())
    return data["queries"]


def compute_metrics(queries, use_multi=False, verbose=False):
    """Run search for each query and compute retrieval metrics.

    If use_multi=True and reformulations exist in the query data,
    uses multi_search to fuse results from multiple query variants.
    """
    total_recall5 = 0
    total_mrr = 0
    total_ndcg5 = 0
    valid_queries = 0
    per_query = []

    for q in queries:
        qid = q["id"]
        query_text = q["query"]
        relevant = q["relevant"]  # slug → grade (0-2)
        reformulations = q.get("reformulations", [])

        # Run search (single or multi-query)
        if use_multi and reformulations:
            all_queries = [query_text] + reformulations
            results = kb_index.multi_search(all_queries, top_k=10)
        else:
            results = kb_index.search(query_text, top_k=10)
        retrieved_slugs = [r["slug"] for r in results]
        top5 = retrieved_slugs[:5]

        # Recall@5: fraction of relevant docs found in top 5
        relevant_slugs = set(relevant.keys())
        found = relevant_slugs.intersection(set(top5))
        recall5 = len(found) / len(relevant_slugs) if relevant_slugs else 0

        # MRR: reciprocal rank of first relevant result
        mrr = 0
        for rank, slug in enumerate(retrieved_slugs, 1):
            if slug in relevant_slugs:
                mrr = 1.0 / rank
                break

        # nDCG@5: normalized discounted cumulative gain
        import math
        dcg = 0
        for i, slug in enumerate(top5):
            grade = relevant.get(slug, 0)
            dcg += grade / math.log2(i + 2)  # i+2 because rank starts at 1

        # Ideal DCG: sort relevance grades descending
        ideal_grades = sorted(relevant.values(), reverse=True)[:5]
        idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal_grades))
        ndcg5 = dcg / idcg if idcg > 0 else 0

        total_recall5 += recall5
        total_mrr += mrr
        total_ndcg5 += ndcg5
        valid_queries += 1

        per_query.append({
            "id": qid,
            "query": query_text,
            "recall5": round(recall5, 3),
            "mrr": round(mrr, 3),
            "ndcg5": round(ndcg5, 3),
            "retrieved_top5": top5,
            "found_relevant": sorted(found),
            "missed": sorted(relevant_slugs - set(retrieved_slugs[:10])),
        })

        if verbose:
            status = "✓" if recall5 >= 0.6 else "✗"
            print(f"  {status} {qid}: R@5={recall5:.2f} MRR={mrr:.2f} nDCG@5={ndcg5:.2f} — {query_text[:60]}")
            if recall5 < 0.6:
                print(f"      missed: {sorted(relevant_slugs - set(retrieved_slugs[:10]))}")

    n = valid_queries or 1
    metrics = {
        "recall_at_5": round(total_recall5 / n, 4),
        "mrr": round(total_mrr / n, 4),
        "ndcg_at_5": round(total_ndcg5 / n, 4),
        "num_queries": valid_queries,
        "per_query": per_query,
    }
    return metrics


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    use_multi = "--multi" in sys.argv
    queries = load_eval_data()

    mode = "multi-query" if use_multi else "single-query"
    print(f"Running retrieval evaluation ({len(queries)} queries, {mode})...")
    print()

    metrics = compute_metrics(queries, use_multi=use_multi, verbose=verbose)

    print()
    print(f"=== Retrieval Metrics ===")
    print(f"  Recall@5:  {metrics['recall_at_5']:.4f}")
    print(f"  MRR:       {metrics['mrr']:.4f}")
    print(f"  nDCG@5:    {metrics['ndcg_at_5']:.4f}")
    print(f"  Queries:   {metrics['num_queries']}")

    # Thresholds (from research)
    recall_ok = metrics["recall_at_5"] >= 0.6
    mrr_ok = metrics["mrr"] >= 0.5
    print()
    print(f"  Recall@5 {'PASS' if recall_ok else 'FAIL'} (threshold: 0.60)")
    print(f"  MRR      {'PASS' if mrr_ok else 'FAIL'} (threshold: 0.50)")

    # Save results
    results_file = ROOT / "tests" / "retrieval_results.json"
    results_file.write_text(json.dumps(metrics, indent=2))
    print(f"\n  Results saved: {results_file}")

    return 0 if (recall_ok and mrr_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
