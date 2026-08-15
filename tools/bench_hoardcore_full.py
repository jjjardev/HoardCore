#!/usr/bin/env python3
"""Full numeric benchmark for HoardCore v0.8.1 (lean/deterministic).

Measures the real VaultManager/EmbeddingsEngine code path with no network:

  [A] Ingestion throughput  — chunks/s, docs/s, vectors/s (index_document)
  [B] Search latency        — fast (FTS-only) vs hybrid (RRF), ms/query,
                              at vault scales 1k and 10k chunks
  [C] Retrieval quality     — P@1, P@5, R@5, MRR, nDCG@5 on a synthetic
                              corpus with ground-truth topics (fast vs hybrid)
  [D] Vector store          — float32 vs int8 x page sizes 4096/16384
  [E] Storage footprint     — bytes/chunk, DB size, FTS/vector overhead
  [F] Integrity check       — integrity_check() wall time at ~8k chunks
  [G] Page-size migration   — migrate_page_size() wall time + correctness

Usage:
    python tools/bench_hoardcore_full.py [--docs 3000]
    python tools/bench_hoardcore_full.py --csv bench.csv

Runs in a temp dir; cleaned up afterwards.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hoardcore as hc  # noqa: E402

TOPICS = [
    ("sugarcane", ["sugarcane", "molasses", "harvest", "cane", "ethanol", "mill", "bagasse", "rind", "juice"]),
    ("geothermal", ["geothermal", "steam", "reservoir", "well", "turbine", "drilling", "enthalpy", "borehole"]),
    ("coffee", ["coffee", "arabica", "roast", "brew", "beans", "barista", "espresso", "caffeine"]),
    ("solar", ["solar", "photovoltaic", "inverter", "irradiance", "panel", "cell", "module", "battery"]),
    ("wind", ["wind", "turbine", "blade", "anemometer", "offshore", "wake", "nacelle", "rotor"]),
    ("fishing", ["fishing", "trawl", "seine", "mackerel", "harbor", "longline", "bycatch", "gillnet"]),
    ("rice", ["rice", "paddy", "irrigation", "terrace", "harvest", "milling", "grain", "paddy"]),
    ("tourism", ["tourism", "resort", "island", "dive", "beach", "guests", "snorkeling", "lagoon"]),
]

SENTINEL = [
    "statistical analysis revealed a statistically significant relationship",
    "the regression model was fitted using ordinary least squares with robust standard errors",
    "qualitative interviews were thematically coded and triangulated across respondents",
    "the dataset was cleaned, normalized, and split into stratified train and validation sets",
    "results were visualized using matplotlib and reported with 95 percent confidence intervals",
    "further research is needed to establish causality under controlled experimental conditions",
]

# Semantic paraphrase queries — held-out words NOT present in any doc, so only
# dense vectors (not lexical FTS) can match. The frontier where hybrid earns
# its keep: (topic_target_index, paraphrase_query).
PARAPHRASE_QUERIES = [
    (0, "energy extraction heat underground earth source power"),
    (5, "ocean fish market boat crew catch nets"),
    (7, "holiday destination visitors coastline hotel swimming"),
]


def make_doc(topic_idx: int, d: int, c: int) -> str:
    label, words = TOPICS[topic_idx]
    rng = random.Random(f"{topic_idx}:{d}:{c}")
    body = " ".join(rng.choice(words) for _ in range(24))
    pad = " ".join(rng.choice(SENTINEL) for _ in range(6))
    return f"## {label} report {d} part {c}\n\n{body}. {pad}"


def build_corpus(n_docs: int, chunks_per_doc: int = 4) -> list[list[str]]:
    return [[make_doc(d % len(TOPICS), d, c) for c in range(chunks_per_doc)]
            for d in range(n_docs)]


def fresh_config(root: str, quantize: str = "float32", page_size: int = 16384) -> hc.ConfigManager:
    """Build a from-scratch ConfigManager scoped to a temp storage root."""
    hc.ConfigManager._instance = None
    hc.ConfigManager._config = {}
    cfg = hc.ConfigManager()
    cfg._config["storage"] = {
        "root_dir": root, "page_size": page_size,
        "save_binary": False, "save_raw_html": False,
        "artifacts_dir": "artifacts", "artifacts_by_day": False,
    }
    cfg._config["embeddings"] = {
        "enabled": True, "mode": "dense", "hybrid_search": True,
        "dense_model": "BAAI/bge-small-en-v1.5", "dim": 256,
        "top_k": 40, "quantize": quantize, "fts_fast_path": True,
        "recency_half_life_days": 0,
    }
    cfg._config["indexer"] = {"enable_fts": True, "search_limit": 20, "parallel": False}
    cfg._config["chunking"] = {"max_tokens": 512, "overlap_tokens": 50, "strategy": "heading"}
    return cfg


def load_corpus(vault: hc.VaultManager, corpus: list[list[str]]) -> float:
    t0 = time.perf_counter()
    for d, doc in enumerate(corpus):
        url = f"https://bench.example.local/doc/{d}"
        meta = {"file_name": f"doc{d}.md", "content_type": "text/markdown",
                "parser_used": "bench", "quality_score": 1.0}
        vault.index_document(
            url,
            [hc.Chunk(t, {"header_path": f"part{c}"}) for c, t in enumerate(doc)],
            meta,
        )
    return time.perf_counter() - t0


def bench_search(vault: hc.VaultManager, queries: list[str], hybrid: bool,
                 reps: int = 5) -> float:
    for _ in range(reps):            # warm
        for q in queries:
            vault.search_vault(q, limit=5, hybrid=hybrid)
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        for q in queries:
            vault.search_vault(q, limit=5, hybrid=hybrid)
        times.append((time.perf_counter() - t0) / len(queries) * 1000.0)
    times.sort()
    return times[len(times) // 2]


def quality(vault: hc.VaultManager, k: int = 5) -> dict[str, dict[str, float]]:
    out = {"fast": {"lex": [], "sem": []}, "hybrid": {"lex": [], "sem": []}}

    def _run(mode: str, query: str, words: list[str]) -> dict:
        res = vault.search_vault(query, limit=k, hybrid=(mode == "hybrid"))
        rel = [1 if any(w in r.text for w in words) else 0 for r in res]
        p1 = rel[0] if rel else 0.0
        p5 = sum(rel[:k]) / k
        r5 = sum(rel) / max(k, 1)
        mrr = next((1.0 / (i + 1) for i, r in enumerate(rel[:k]) if r), 0.0)
        dcg = sum(r / (i + 2) for i, r in enumerate(rel[:k]))
        idcg = sum(1.0 / (i + 2) for i in range(min(5, k)))
        ndcg = dcg / idcg if idcg else 0.0
        return {"p1": p1, "p5": p5, "r5": r5, "mrr": mrr, "ndcg": ndcg}

    # Lexical-topic queries (exact words present in docs)
    for _label, words in TOPICS:
        query = f"{words[0]} {words[1]}"
        for mode in ("fast", "hybrid"):
            out[mode]["lex"].append(_run(mode, query, words))

    # Semantic paraphrase queries (no shared vocabulary)
    for ti, pq in PARAPHRASE_QUERIES:
        _l, words = TOPICS[ti]
        for mode in ("fast", "hybrid"):
            out[mode]["sem"].append(_run(mode, pq, words))

    agg = {}
    for mode in out:
        agg[mode] = {}
        for bucket, runs in out[mode].items():
            if not runs:
                continue
            agg[f"{mode}.{bucket}"] = {
                m: sum(r[m] for r in runs) / len(runs) for m in ("p1", "p5", "r5", "mrr", "ndcg")
            }
    return agg


def vector_bench(dim: int, n: int, page_sizes: list[int]) -> list[tuple]:
    rng = random.Random(0)
    vecs = []
    for _ in range(n):
        vals = [rng.gauss(0, 1) for _ in range(dim)]
        nrm = sum(v * v for v in vals) ** 0.5 or 1.0
        vecs.append(array('f', (v / nrm for v in vals)).tobytes())
    int8 = [hc.EmbeddingsEngine._quantize_int8(v) for v in vecs]
    rows = []
    for fmt, vs in (("float32", vecs), ("int8", int8)):
        queries, dimq = (vecs[:3], dim) if fmt == "float32" else (int8[:3], dim)
        for ps in page_sizes:
            tmp = os.path.join(tempfile.mkdtemp(), f"vb_{fmt}_{ps}.db")
            conn = sqlite3.connect(tmp)
            conn.execute(f"PRAGMA page_size = {ps}")
            conn.execute("CREATE TABLE chunk_vectors (j INTEGER PRIMARY KEY, v BLOB)")
            conn.executemany("INSERT INTO chunk_vectors VALUES (?, ?)", enumerate(vs))
            conn.commit()
            conn.close()
            conn = sqlite3.connect(tmp)
            allv = [r[0] for r in conn.execute("SELECT v FROM chunk_vectors")]
            t0 = time.perf_counter()
            for _ in range(3):
                for q in queries:
                    sc = [hc.EmbeddingsEngine.cosine(q, b, dimq) for b in allv]
                    sc.sort(reverse=True)
            dt = (time.perf_counter() - t0) / 3 / 3 * 1000
            mb = os.path.getsize(tmp) / 1e6
            conn.close()
            os.remove(tmp)
            rows.append((fmt, ps, dt, 1000.0 / dt, n, mb))
    return rows


def footprint(db_path: str, n_chunks: int) -> tuple[float, float, float]:
    size = os.path.getsize(db_path) / 1e6
    conn = sqlite3.connect(db_path)

    def q(pat: str) -> int:
        return conn.execute(
            "SELECT COALESCE(sum(pgsize),0) FROM dbstat WHERE name LIKE ?",
            (pat,)).fetchone()[0]

    fts = q("chunks_fts%")
    vec = q("chunk_vectors%")
    conn.close()
    return size, fts / n_chunks, vec / n_chunks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs", type=int, default=1000, help="docs in master vault (x4 chunks)")
    ap.add_argument("--csv", type=str, default="")
    args = ap.parse_args()

    n_docs = args.docs
    master_chunks = n_docs * 4
    root = tempfile.mkdtemp(prefix="hc_bench_")
    print(f"# HoardCore v{hc.__version__} — full numeric benchmark (master={master_chunks} chunks)")
    print("# deterministic synthetic corpus, no network\n")
    res: dict = {}

    corpus = build_corpus(n_docs)

    print("[A] INGESTION")
    cfg = fresh_config(root)
    vault = hc.VaultManager(cfg, vault_name="m")
    wall = load_corpus(vault, corpus)
    with vault._db() as (_c, cur):
        cur.execute("SELECT count(*) FROM chunk_vectors")
        nv = cur.fetchone()[0]
    res["ingest"] = {"seconds": wall, "chunks_per_s": master_chunks / wall,
                     "vectors_per_s": nv / wall, "chunks": master_chunks}
    print(f"    {master_chunks} chunks in {wall:.1f}s "
          f"({master_chunks / wall:.0f} chunks/s, {nv / wall:.0f} vectors/s)")

    # [A2] pure DB/index cost (embeddings off) — isolates embed overhead
    cfg_noemb = fresh_config(root)
    cfg_noemb._config["embeddings"]["enabled"] = False
    vault_ne = hc.VaultManager(cfg_noemb, vault_name="m_ne")
    wall_ne = load_corpus(vault_ne, corpus)
    res["ingest_noembed"] = {"seconds": wall_ne, "chunks_per_s": master_chunks / wall_ne}
    print(f"    {master_chunks} chunks (no embeddings): {wall_ne:.1f}s "
          f"({master_chunks / wall_ne:.0f} chunks/s) "
          f"[embedding cost = {(wall - wall_ne):.1f}s]")

    queries = [f"{w0} {w1}" for _, (w0, w1) in
               ((None, (words[0], words[1])) for _, words in TOPICS)]

    # use the no-embed vault's chunks for quality recall denominators later is
    # redundant; quality runs on the main (embedded) vault which is correct.

    print("\n[B] SEARCH LATENCY (median ms/query)")
    res["search"] = {}
    for label, vault_here in (("1,000", None), (f"{master_chunks:,}", vault)):
        if vault_here is None:                       # build the small vault
            cfg_s = fresh_config(root)
            vault_here = hc.VaultManager(cfg_s, vault_name=f"s{len(corpus[:250])}")
            load_corpus(vault_here, corpus[:250])
        fast = bench_search(vault_here, queries, hybrid=False)
        hyb = bench_search(vault_here, queries, hybrid=True)
        res["search"][label] = {"fast_ms": round(fast, 2), "hybrid_ms": round(hyb, 2)}
        print(f"    {label:>9} chunks: fast {fast:6.2f} ms | hybrid {hyb:6.2f} ms"
              f" ({1000/fast:5.0f}q/s) ({1000/hyb:5.0f}q/s)")

    print("\n[C] RETRIEVAL QUALITY (k=5)")
    q = quality(vault)
    res["quality"] = q
    print(f"    {'mode':<16}{'p1':>7}{'p5':>7}{'r5':>7}{'mrr':>7}{'ndcg':>7}")
    for key in ("fast.lex", "hybrid.lex", "fast.sem", "hybrid.sem"):
        d = q.get(key)
        if not d:
            continue
        print(f"    {key:<16}{d['p1']:>7.3f}{d['p5']:>7.3f}{d['r5']:>7.3f}"
              f"{d['mrr']:>7.3f}{d['ndcg']:>7.3f}")

    print("\n[D] VECTOR STORE (10k vecs, brute-force cosine)")
    vb = vector_bench(384, 10_000, [4096, 16384])
    res["vector"] = [{"format": f, "page": p, "ms": m, "qps": q_,
                      "db_mb": mb} for f, p, m, q_, _n, mb in vb]
    print(f"    {'format':<9}{'page':<7}{'ms/query':<10}{'q/s':<7}{'db_mb':<8}")
    for f, p, m, q_, _n, mb in vb:
        print(f"    {f:<9}{p:<7}{m:<10.3f}{q_:<7.0f}{mb:<8.2f}")

    print(f"\n[E] STORAGE FOOTPRINT @ {master_chunks} chunks")
    res["footprint"] = {}
    for qname, vq in (("float32", vault),):
        db_gb, fts_b, vec_b = footprint(vq.db_path, master_chunks)
        res["footprint"][qname] = {"db_mb": round(db_gb, 2), "fts_b_per_chunk": round(fts_b),
                                   "vec_b_per_chunk": round(vec_b)}
        print(f"    {qname:<8}: {db_gb:>7.2f} MB db | {fts_b:>6.0f} B/chunk FTS"
              f" | {vec_b:>6.0f} B/chunk vectors")

    print(f"\n[F] INTEGRITY CHECK @ {master_chunks} chunks")
    t0 = time.perf_counter()
    ok = vault.verify_vault()
    dt = time.perf_counter() - t0
    res["integrity"] = {"seconds": round(dt, 2), "pass": ok}
    print(f"    {dt:.2f}s wall ({'PASS' if ok else 'FAIL'})")

    print("\n[G] PAGE-SIZE MIGRATION")
    # only report migration if not already at target
    with vault._db() as (_c, cur):
        cur_sz = cur.execute("PRAGMA page_size").fetchone()[0]
    if cur_sz != 16384:
        t0 = time.perf_counter()
        vault.migrate_page_size(16384)
        dt = time.perf_counter() - t0
    else:
        dt = 0.0
    with vault._db() as (_c, cur):
        sz = cur.execute("PRAGMA page_size").fetchone()[0]
    res["migrate"] = {"seconds": round(dt, 2), "page_size": sz}
    print(f"    {dt:.2f}s wall -> page_size={sz}")

    shutil.rmtree(root, ignore_errors=True)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8") as f:
            f.write("category,key,value\n")
            def flat(d, pre=""):
                for k, v in d.items():
                    if isinstance(v, dict):
                        flat(v, f"{pre}{k}.")
                    else:
                        f.write(f"{pre}{k},{v}\n")
            flat(res)
        print(f"\nCSV: {args.csv}")


if __name__ == "__main__":
    main()
