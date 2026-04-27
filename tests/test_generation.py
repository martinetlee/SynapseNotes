#!/usr/bin/env python3
"""Generation quality evaluation using DeepEval.

Tests faithfulness (are claims grounded in context?) and answer relevancy
(does the answer address the query?).

Requires: OPENAI_API_KEY env var (DeepEval uses OpenAI as the judge model).
Run: python3 tests/test_generation.py [--verbose]
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / ".kb"))

import importlib
kb_index = importlib.import_module("kb-index")


def load_eval_data():
    """Load golden query set."""
    eval_file = ROOT / "tests" / "eval_data.json"
    data = json.loads(eval_file.read_text())
    return data["queries"]


def generate_test_cases(queries, max_cases=10):
    """For each query, run search, read notes, and build test cases with context."""
    cases = []
    for q in queries[:max_cases]:
        results = kb_index.search(q["query"], top_k=5)
        if not results:
            continue

        # Build retrieval context by reading actual note content
        # Search across all KB directories for each slug
        context_parts = []
        registry = kb_index.get_registry()
        for r in results[:3]:  # Top 3 for context
            slug = r["slug"]
            note_path = None
            for kbc in registry.all_kbs():
                candidate = kbc.notes_dir / f"{slug}.md"
                if candidate.exists():
                    note_path = candidate
                    break
            if note_path and note_path.exists():
                fm, body, _ = kb_index.parse_note(note_path)
                context_parts.append(f"[{slug}]: {body[:1500]}")

        if not context_parts:
            continue

        cases.append({
            "id": q["id"],
            "query": q["query"],
            "context": context_parts,
        })

    return cases


def run_deepeval(cases, verbose=False):
    """Run DeepEval faithfulness and answer relevancy checks."""
    try:
        from deepeval import evaluate
        from deepeval.test_case import LLMTestCase
        from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric
    except ImportError:
        print("ERROR: deepeval not installed. Run: pip3 install deepeval")
        return None

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set. DeepEval requires OpenAI as judge model.")
        print("Set it with: export OPENAI_API_KEY=sk-...")
        return None

    faithfulness = FaithfulnessMetric(threshold=0.7, model="gpt-4o-mini")
    relevancy = AnswerRelevancyMetric(threshold=0.7, model="gpt-4o-mini")

    test_cases = []
    for case in cases:
        # Generate a simple synthesis from context (simulates what /kb-search does)
        context_text = "\n\n".join(case["context"])
        # Use the first ~2000 chars of context as a simulated answer
        # In real usage, this would be Claude's synthesized response
        simulated_answer = f"Based on the KB notes:\n\n{context_text[:2000]}"

        tc = LLMTestCase(
            input=case["query"],
            actual_output=simulated_answer,
            retrieval_context=case["context"],
        )
        test_cases.append((case["id"], tc))

    results = []
    for case_id, tc in test_cases:
        if verbose:
            print(f"  Evaluating {case_id}: {tc.input[:60]}...")

        try:
            faithfulness.measure(tc)
            relevancy.measure(tc)

            result = {
                "id": case_id,
                "faithfulness": round(faithfulness.score, 3),
                "relevancy": round(relevancy.score, 3),
                "faithfulness_pass": faithfulness.score >= 0.7,
                "relevancy_pass": relevancy.score >= 0.7,
            }

            if verbose:
                f_status = "✓" if result["faithfulness_pass"] else "✗"
                r_status = "✓" if result["relevancy_pass"] else "✗"
                print(f"    {f_status} faith={result['faithfulness']:.2f}  {r_status} relev={result['relevancy']:.2f}")

            results.append(result)
        except Exception as e:
            print(f"    ERROR on {case_id}: {e}")
            results.append({"id": case_id, "error": str(e)})

    return results


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    queries = load_eval_data()

    print(f"Building test cases ({len(queries)} queries)...")
    cases = generate_test_cases(queries)
    print(f"Generated {len(cases)} test cases with retrieval context.")
    print()

    print("Running DeepEval faithfulness + answer relevancy...")
    print()
    results = run_deepeval(cases, verbose=verbose)

    if results is None:
        return 1

    # Aggregate
    valid = [r for r in results if "error" not in r]
    if not valid:
        print("No valid results.")
        return 1

    avg_faith = sum(r["faithfulness"] for r in valid) / len(valid)
    avg_relev = sum(r["relevancy"] for r in valid) / len(valid)
    faith_pass_rate = sum(1 for r in valid if r["faithfulness_pass"]) / len(valid)
    relev_pass_rate = sum(1 for r in valid if r["relevancy_pass"]) / len(valid)

    print()
    print(f"=== Generation Metrics ===")
    print(f"  Avg Faithfulness:     {avg_faith:.4f}")
    print(f"  Avg Answer Relevancy: {avg_relev:.4f}")
    print(f"  Faithfulness pass:    {faith_pass_rate:.0%} (threshold: 0.70)")
    print(f"  Relevancy pass:       {relev_pass_rate:.0%} (threshold: 0.70)")
    print(f"  Test cases:           {len(valid)}")

    overall_pass = avg_faith >= 0.7 and avg_relev >= 0.7
    print(f"\n  Overall: {'PASS' if overall_pass else 'FAIL'}")

    # Save results
    output = {
        "avg_faithfulness": round(avg_faith, 4),
        "avg_relevancy": round(avg_relev, 4),
        "faithfulness_pass_rate": round(faith_pass_rate, 4),
        "relevancy_pass_rate": round(relev_pass_rate, 4),
        "per_case": results,
    }
    results_file = ROOT / "tests" / "generation_results.json"
    results_file.write_text(json.dumps(output, indent=2))
    print(f"  Results saved: {results_file}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
