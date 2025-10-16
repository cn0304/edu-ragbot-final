#!/usr/bin/env python3
"""
Benchmark reranker performance and relevance quality.
"""

import sys
import time
import asyncio
from pathlib import Path

# Add backend path for import
sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.app.rag_engine import SmartRAGEngine


async def test_with_without_reranker():
    """Compare retrieval latency and relevance with and without reranker"""

    test_queries = [
        "How do I apply to INTI?",
        "What is the programme structure for computer science at UOW?",
        # "Tell me about scholarships at ATC",
        # "Where is TARC campus located?",
        # "Entry requirements for business diploma",
    ]

    print("=" * 70)
    print(" RERANKER PERFORMANCE TEST ".center(70))
    print("=" * 70)

    # Helper to run test
    def run_test(use_reranker: bool):
        rag = SmartRAGEngine(
            use_reranker=use_reranker,
            reranker_strategy="cross-encoder" if use_reranker else None,
        )

        results = []
        for query in test_queries:
            start = time.time()
            sources = rag.get_sources(query)
            elapsed = time.time() - start

            # compute average relevance across retrieved chunks
            avg_score = 0
            if sources:
                vals = []
                for s in sources:
                    if 'rerank_score' in s:
                        vals.append(s['rerank_score'])
                    elif 'relevance' in s:
                        vals.append(s['relevance'])
                    else:
                        vals.append(0.0)
                avg_score = sum(vals) / len(vals)
            else:
                avg_score = 0.0

            results.append(
                {
                    "query": query,
                    "time": elapsed,
                    "avg_score": avg_score,
                }
            )

            tag = "🟢" if use_reranker else "🔵"
            print(f"{tag} {query[:55]:55s} {elapsed:.3f}s (avg score {avg_score:.3f})")

        return results

    # Run both tests
    print("\n🔵 Testing WITHOUT Reranker...")
    no_rerank_results = run_test(False)

    print("\n🟢 Testing WITH Reranker...")
    with_rerank_results = run_test(True)

    # --- Summary ---
    def avg(lst): return sum(lst) / len(lst) if lst else 0

    no_rerank_avg_time = avg([r["time"] for r in no_rerank_results])
    with_rerank_avg_time = avg([r["time"] for r in with_rerank_results])

    no_rerank_total_time = sum(r["time"] for r in no_rerank_results)
    with_rerank_total_time = sum(r["time"] for r in with_rerank_results)

    no_rerank_avg_score = avg([r["avg_score"] for r in no_rerank_results])
    with_rerank_avg_score = avg([r["avg_score"] for r in with_rerank_results])

    print("\n" + "=" * 70)
    print(" SUMMARY ".center(70))
    print("=" * 70)
    print(f"{'Metric':<30} {'Without Reranker':<20} {'With Reranker':<20}")
    print("-" * 70)
    print(f"{'Average Latency':<30} {no_rerank_avg_time:.3f}s{'':<15} {with_rerank_avg_time:.3f}s")
    print(f"{'Total Time':<30} {no_rerank_total_time:.3f}s{'':<15} {with_rerank_total_time:.3f}s")
    print(f"{'Average Relevance Score':<30} {no_rerank_avg_score:.3f}{'':<16} {with_rerank_avg_score:.3f}")

    # --- Speed comparison ---
    print("\n" + "-" * 70)
    if with_rerank_avg_time < no_rerank_avg_time:
        speedup = no_rerank_avg_time / with_rerank_avg_time
        print(f"⚡ Reranker is {speedup:.2f}x FASTER")
    else:
        slowdown = with_rerank_avg_time / no_rerank_avg_time
        print(f"⏳ Reranker is {slowdown:.2f}x slower")

    # --- Quality comparison ---
    print("\n" + "-" * 70)
    if with_rerank_avg_score > no_rerank_avg_score:
        improvement = ((with_rerank_avg_score - no_rerank_avg_score) / no_rerank_avg_score) * 100
        print(f"✨ Reranker improves relevance by {improvement:.1f}%")
    else:
        degradation = ((no_rerank_avg_score - with_rerank_avg_score) / no_rerank_avg_score) * 100
        print(f"⚠️  Reranker reduces relevance by {degradation:.1f}%")

    # --- Per-query breakdown ---
    print("\n" + "=" * 80)
    print(" PER-QUERY BREAKDOWN ".center(80))
    print("=" * 80)
    print(f"\n{'Query':<45} {'Time Δ (ms)':<15} {'Score Δ':<15}")
    print("-" * 80)
    for i, query in enumerate(test_queries):
        t_diff = (with_rerank_results[i]["time"] - no_rerank_results[i]["time"]) * 1000
        s_diff = with_rerank_results[i]["avg_score"] - no_rerank_results[i]["avg_score"]

        print(f"{query[:42]:<45} {t_diff:+8.0f}ms{'':<5} {s_diff:+.3f}")


if __name__ == "__main__":
    asyncio.run(test_with_without_reranker())
