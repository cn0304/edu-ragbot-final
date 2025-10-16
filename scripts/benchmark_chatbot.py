import asyncio
import json
import os
import time
import sys
from typing import Any, Dict, List

# Ensure repo root on sys.path
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.app.rag_engine import SmartRAGEngine


QUERIES: List[Dict[str, Any]] = [
    # Accuracy-focused: known simple docs
    {"q": "How to apply INTI?", "expect": {"doc_type": "how_to_apply", "university": "INTI"}},
    {"q": "Where is ATC campus located?", "expect": {"doc_type": "campus", "university": "ATC"}},
    {"q": "List scholarships for UOW", "expect": {"doc_type": "scholarship", "university": "UOW", "list_query": True}},
    # Course sections (fees, structure)
    {"q": "What is the MSU bachelor-information-technology-mobile-wireless fees?", "expect": {"doc_type": "courses", "university": "MSU", "section": "Fees"}},
]


def measure_latency(func, *args, **kwargs) -> float:
    t0 = time.perf_counter()
    r = func(*args, **kwargs)
    # support async generator in generate_response
    if asyncio.iscoroutine(r) or hasattr(r, "__aiter__"):
        async def _consume():
            async for _ in r:
                pass
        asyncio.run(_consume())
    t1 = time.perf_counter()
    return t1 - t0


def check_accuracy(rag: SmartRAGEngine, q: str, expect: Dict[str, Any]) -> bool:
    info = rag.detect_query_type(q)
    # basic intent match
    ok = True
    for k, v in expect.items():
        if info.get(k) != v:
            ok = False
            break
    # light retrieval check: ensure at least one source returned
    srcs = rag.get_sources(q)
    return ok and len(srcs) > 0


async def run_scalability_test(rag: SmartRAGEngine, q: str, concurrency: int = 10) -> Dict[str, Any]:
    async def one():
        t0 = time.perf_counter()
        async for _ in rag.generate_response(q, stream=True):
            pass
        return time.perf_counter() - t0
    tasks = [asyncio.create_task(one()) for _ in range(concurrency)]
    durations = await asyncio.gather(*tasks)
    return {
        "concurrency": concurrency,
        "avg_latency_sec": sum(durations) / len(durations),
        "p95_latency_sec": sorted(durations)[int(0.95 * len(durations)) - 1],
        "min_latency_sec": min(durations),
        "max_latency_sec": max(durations),
    }


def write_benchmark(results: Dict[str, Any]) -> None:
    root = os.path.dirname(os.path.dirname(__file__))
    out = os.path.join(root, 'metrics', 'benchmark.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    print(f"Wrote benchmark metrics to {out}")


def main() -> None:
    rag = SmartRAGEngine()
    # Accuracy & latency per query
    per_query = []
    for item in QUERIES:
        q = item["q"]
        expect = item["expect"]
        acc = check_accuracy(rag, q, expect)
        lat = measure_latency(rag.generate_response, q)
        per_query.append({
            "query": q,
            "accuracy": 1.0 if acc else 0.0,
            "latency_sec": lat,
        })

    # Scalability under workload
    scal = asyncio.run(run_scalability_test(rag, "Compare scholarships between INTI and UOW", concurrency=8))

    results = {
        "timestamp": int(time.time()),
        "model": rag.model_name,
        "per_query": per_query,
        "scalability": scal,
    }
    write_benchmark(results)


if __name__ == '__main__':
    main()