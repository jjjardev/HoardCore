#!/usr/bin/env python3
"""Vector-search benchmark harness for HoardCore vaults.

Measures brute-force cosine-scan latency against a growing number of
384-dim vectors, both for the float32 storage format and the int8-quantized
format, and across the 4 KB (legacy) and 16 KB (new default) page sizes.
This is the data behind future HNSW/sqlite-vec scaling decisions.

Usage:
    python tools/bench_vector.py              # defaults below
    python tools/bench_vector.py --vectors 50_000 --dim 384 --seed 7
    python tools/bench_vector.py --page-sizes 4096 16384 --csv bench.csv

The database is created in a temp dir and removed afterwards.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hoardcore as hc  # noqa: E402


def make_vecs(n: int, dim: int, seed: int = 0) -> list[bytes]:
    """Deterministic L2-normalized float32 vectors (and their int8 forms)."""
    import random

    rng = random.Random(seed)
    out = []
    for _ in range(n):
        vals = [rng.gauss(0.0, 1.0) for _ in range(dim)]
        norm = sum(v * v for v in vals) ** 0.5 or 1.0
        vals = [v / norm for v in vals]
        out.append(array('f', vals).tobytes())
    return out


def build_db(path: str, vecs: list[bytes], page_size: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA page_size = {page_size}")
    conn.execute("CREATE TABLE chunk_vectors (chunk_rowid INTEGER PRIMARY KEY, vector BLOB)")
    conn.executemany(
        "INSERT INTO chunk_vectors VALUES (?, ?)",
        enumerate(vecs),
    )
    conn.commit()
    conn.close()


def bench_db(path: str, dim: int, queries: list[bytes], reps: int = 3) -> float:
    """Return median query latency (ms) for a brute-force cosine scan."""
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT chunk_rowid, vector FROM chunk_vectors").fetchall()
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        for q in queries:
            scored = []
            for rid, blob in rows:
                s = hc.EmbeddingsEngine.cosine(q, blob, dim)
                scored.append((s, rid))
            scored.sort(key=lambda t: t[0], reverse=True)
        times.append((time.perf_counter() - t0) / len(queries) * 1000.0)
    conn.close()
    times.sort()
    return times[len(times) // 2]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vectors", type=int, default=20_000)
    ap.add_argument("--dim", type=int, default=384)
    ap.add_argument("--queries", type=int, default=5)
    ap.add_argument("--page-sizes", type=int, nargs="+", default=[4096, 16384])
    ap.add_argument("--csv", type=str, default="", help="append results to this CSV")
    ap.add_argument("--max-ms", type=float, default=0.0,
                    help="exit 1 if any float32/16KB ms-per-query exceeds this "
                         "(0 disables; a CI regression gate)")
    args = ap.parse_args()

    print(f"# HoardCore vector benchmark — {args.vectors} vectors, dim={args.dim}, "
          f"{args.queries} queries")
    print("# float32 and int8 storage, both page sizes, brute-force cosine.")
    print()

    float32 = make_vecs(args.vectors, args.dim, seed=0)
    int8 = [hc.EmbeddingsEngine._quantize_int8(v) for v in float32]
    # Queries must be in the SAME format as storage: float32 vectors against
    # float32 storage, int8-quantized queries against int8 storage. Mixing
    # them is a format mismatch (surfaced by EmbeddingsEngine.cosine) and
    # produces meaningless timings.
    q_f32 = make_vecs(args.queries, args.dim, seed=99)
    q_int8 = [hc.EmbeddingsEngine._quantize_int8(v) for v in q_f32]

    rows: list[tuple[str, int, float, float, int, float]] = []

    for fmt, vecs, queries in (
        ("float32", float32, q_f32),
        ("int8", int8, q_int8),
    ):
        for page_size in args.page_sizes:
            tmp = os.path.join(tempfile.mkdtemp(), f"bench_{fmt}_{page_size}.db")
            build_db(tmp, vecs, page_size)
            ms = bench_db(tmp, args.dim, queries)
            size_mb = os.path.getsize(tmp) / 1e6
            rows.append((fmt, page_size, ms, 1000.0 / ms, len(vecs), size_mb))
            os.remove(tmp)

    print(f"{'format':<9}{'page':<7}{'ms/query':<11}{'queries/s':<11}{'vectors':<9}{'db (MB)':<9}")
    print("-" * 56)
    for fmt, page, ms, qps, n, mb in rows:
        print(f"{fmt:<9}{page:<7}{ms:<11.2f}{qps:<11.0f}{n:<9}{mb:<9.1f}")

    if args.csv:
        with open(args.csv, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write("format,page_size,ms_per_query,queries_per_sec,vectors,db_mb\n")
            for fmt, page, ms, qps, n, mb in rows:
                f.write(f"{fmt},{page},{ms:.3f},{qps:.1f},{n},{mb:.3f}\n")
        print(f"\nAppended to {args.csv}")

    if args.max_ms > 0:
        worst = max(r[2] for r in rows)
        if worst > args.max_ms:
            print(f"\nBENCH FAIL: worst {worst:.2f}ms/query exceeds --max-ms {args.max_ms:.2f}",
                  file=sys.stderr)
            raise SystemExit(1)
        print(f"\nbench OK: worst {worst:.2f}ms/query within --max-ms {args.max_ms:.2f}")


if __name__ == "__main__":
    main()
