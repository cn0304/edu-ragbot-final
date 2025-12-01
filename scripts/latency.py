#!/usr/bin/env python3
"""
Compare latency between different LLM models (e.g., llama3.2:1b vs llama3.2:3b)
and save results to metrics/latency.json
"""

import asyncio
import json
import os
import time
import sys
from pathlib import Path

# --- ensure repo root on sys.path ---
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.rag_engine import SmartRAGEngine

# List of models to test
MODELS = [
    "llama3.2:1b",
    "llama3.2:3b",
]

# Test queries (you can add more)
QUERIES = [
    "How to apply INTI?",
    "Where is ATC campus located?",
    "List scholarships for UOW",
    "What is the MSU bachelor-information-technology-mobile-wireless fees?",
]

async def measure_latency(rag: SmartRAGEngine, query: str) -> float:
    t0 = time.perf_counter()
    async for _ in rag.generate_response(query, stream=True):
        pass
    t1 = time.perf_counter()
    return t1 - t0


async def test_model(model_name: str):
    print(f"\n🚀 Testing model: {model_name}")
    rag = SmartRAGEngine(model_name=model_name)
    results = []

    for q in QUERIES:
        print(f" → Query: {q}")
        latency = await measure_latency(rag, q)
        print(f"   ⏱️  Latency: {latency:.2f} seconds")
        results.append({"query": q, "latency_sec": latency})

    avg_latency = sum(r["latency_sec"] for r in results) / len(results)
    print(f"\n✅ Average latency for {model_name}: {avg_latency:.2f}s")
    return {"model": model_name, "results": results, "avg_latency_sec": avg_latency}


async def main():
    all_results = []
    for model in MODELS:
        res = await test_model(model)
        all_results.append(res)

    # Save results to metrics/latency.json
    os.makedirs("metrics", exist_ok=True)
    out_path = os.path.join("metrics", "latency.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n📁 Results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
