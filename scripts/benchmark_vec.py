"""Benchmark sqlite-vec vector search performance at different data volumes.

Usage:
    python scripts/benchmark_vec.py

Measures:
  - Insert latency (embedding + index)
  - Vector search latency at 100 / 500 / 1000 / 5000 entries
  - FTS5 search latency for comparison
  - Memory usage (DB file size)

Requires: sqlite-vec installed. Uses random vectors (no API key needed).
"""

import json
import os
import random
import struct
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from my_agent_memory.db import Database

DIM = 4096  # Match Qwen3-Embedding-8B dimensions
DB_PATH = ":memory:"  # Use in-memory for benchmark isolation


def random_vector(dim: int = DIM) -> list[float]:
    """Generate a random unit vector."""
    v = [random.gauss(0, 1) for _ in range(dim)]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def random_content() -> str:
    """Generate random content for FTS5 indexing."""
    words = [
        "server", "database", "api", "deploy", "config", "memory", "agent",
        "search", "vector", "embedding", "cache", "log", "error", "timeout",
        "connection", "authentication", "token", "key", "secret", "password",
        "network", "firewall", "proxy", "load", "balance", "cluster", "node",
        "service", "endpoint", "request", "response", "header", "body", "query",
        "index", "table", "column", "row", "schema", "migration", "backup",
    ]
    n = random.randint(10, 30)
    return " ".join(random.choices(words, k=n))


def benchmark_insert(db: Database, count: int) -> dict:
    """Benchmark inserting entries with vector indexing."""
    vectors = [random_vector() for _ in range(count)]

    start = time.perf_counter()
    for i in range(count):
        content = random_content()
        cursor = db.execute(
            """INSERT INTO memory_entries (content, title, tags, source, checksum, owner_agent, state, last_access_ts)
               VALUES (?, ?, '[]', 'manual', ?, 'noor', 'raw', datetime('now'))""",
            (content, f"Entry {i}", f"bench{i}"),
        )
        entry_id = cursor.lastrowid
        blob = struct.pack(f"<{DIM}f", *vectors[i])
        db.execute("INSERT INTO memory_vec (rowid, embedding) VALUES (?, ?)", (entry_id, blob))
    db.commit()
    elapsed = time.perf_counter() - start

    return {
        "count": count,
        "total_ms": round(elapsed * 1000, 1),
        "per_entry_ms": round(elapsed * 1000 / count, 3),
    }


def benchmark_vec_search(db: Database, iterations: int = 50) -> dict:
    """Benchmark vector similarity search."""
    query_vec = random_vector()
    blob = struct.pack(f"<{DIM}f", *query_vec)

    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        rows = db.fetchall("""
            SELECT v.rowid, v.distance FROM memory_vec v
            WHERE v.embedding MATCH ?
            ORDER BY v.distance LIMIT 10
        """, [blob])
        elapsed = time.perf_counter() - start
        times.append(elapsed * 1000)

    times.sort()
    return {
        "iterations": iterations,
        "avg_ms": round(sum(times) / len(times), 3),
        "p50_ms": round(times[len(times) // 2], 3),
        "p95_ms": round(times[int(len(times) * 0.95)], 3),
        "max_ms": round(times[-1], 3),
    }


def benchmark_fts_search(db: Database, iterations: int = 50) -> dict:
    """Benchmark FTS5 full-text search for comparison."""
    queries = ["server api", "database config", "memory agent search", "token key auth", "network proxy load"]

    times = []
    for i in range(iterations):
        q = queries[i % len(queries)]
        start = time.perf_counter()
        rows = db.fetchall("""
            SELECT e.id, rank FROM memory_fts f
            JOIN memory_entries e ON f.rowid = e.id
            WHERE memory_fts MATCH ?
            ORDER BY rank LIMIT 10
        """, [q])
        elapsed = time.perf_counter() - start
        times.append(elapsed * 1000)

    times.sort()
    return {
        "iterations": iterations,
        "avg_ms": round(sum(times) / len(times), 3),
        "p50_ms": round(times[len(times) // 2], 3),
        "p95_ms": round(times[int(len(times) * 0.95)], 3),
        "max_ms": round(times[-1], 3),
    }


def run_benchmark():
    volumes = [100, 500, 1000, 5000]
    results = {}

    print("=" * 65)
    print(" sqlite-vec Vector Search Benchmark")
    print(f" Dimensions: {DIM}  |  DB: in-memory  |  Platform: {sys.platform}")
    print("=" * 65)

    for vol in volumes:
        print(f"\n--- Volume: {vol} entries ---")
        db = Database(DB_PATH)

        # Insert
        insert_result = benchmark_insert(db, vol)
        print(f"  Insert:  {insert_result['total_ms']}ms total, {insert_result['per_entry_ms']}ms/entry")

        # Vector search
        vec_result = benchmark_vec_search(db)
        print(f"  Vec:     avg={vec_result['avg_ms']}ms  p50={vec_result['p50_ms']}ms  p95={vec_result['p95_ms']}ms  max={vec_result['max_ms']}ms")

        # FTS search
        fts_result = benchmark_fts_search(db)
        print(f"  FTS5:    avg={fts_result['avg_ms']}ms  p50={fts_result['p50_ms']}ms  p95={fts_result['p95_ms']}ms  max={fts_result['max_ms']}ms")

        results[vol] = {
            "insert": insert_result,
            "vec_search": vec_result,
            "fts_search": fts_result,
        }

        db.close()

    # Summary
    print("\n" + "=" * 65)
    print(" Summary")
    print("=" * 65)
    print(f"{'Volume':>8}  {'Insert/entry':>14}  {'Vec avg':>10}  {'Vec p95':>10}  {'FTS5 avg':>10}  {'FTS5 p95':>10}")
    print("-" * 65)
    for vol, r in results.items():
        print(f"{vol:>8}  {r['insert']['per_entry_ms']:>11.3f}ms  {r['vec_search']['avg_ms']:>7.3f}ms  {r['vec_search']['p95_ms']:>7.3f}ms  {r['fts_search']['avg_ms']:>7.3f}ms  {r['fts_search']['p95_ms']:>7.3f}ms")

    # Save to JSON
    out_path = Path(__file__).parent.parent / "benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    run_benchmark()
