#!/usr/bin/env python3
"""Peel-Anchor: deterministic Longest Common Subsequence approximation.

MIT-style reference implementation of the deterministic near-linear-time LCS
approximation of Boneh, Golan, Krauthgamer (arXiv:2507.22486, Jul 2025), which
is the "certifier" half of the Peel-Anchor scheme (see novel_idea artifact):

  - Exact O(n^2) DP baseline for validation.
  - Deterministic approx: dyadic frequency-band certificate (Lemma 8
    pigeonhole) + Greedy LDS peeling of a first-occurrence order -> anchors.

The 2026 Mao-Rubinstein curvature-sparsified-grid rounding *is not* reimplemented
here; it is out of scope for a tiny self-contained benchmark. This file gives
the deterministic anchor-selection machinery that scheme would consume.
"""

from bisect import bisect_left
from collections import Counter
import random

# ---------------------------------------------------------------------------
# Exact LCS (O(n^2) DP), for correctness baseline
# ---------------------------------------------------------------------------

def lcs_len_exact(a, b):
    """Length of LCS via classic DP. Returns (length, lcs_string)."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    # backtrack
    i, j = n, m
    out = []
    while i > 0 and j > 0:
        if a[i - 1] == b[j - 1]:
            out.append(a[i - 1]); i -= 1; j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return dp[n][m], "".join(reversed(out))


def is_subsequence(sub, full):
    it = iter(full)
    return all(c in it for c in sub)


# ---------------------------------------------------------------------------
# Longest (increasing/decreasing) subsequence under a rank order, O(k log k)
# ---------------------------------------------------------------------------

def _lis_by_rank(seq, rank, decreasing=False):
    """Return a longest subsequence of seq whose ranks are monotone
    (increasing by default, decreasing if :decreasing:).

    rank: dict symbol -> integer rank.
    """
    tails_val = []      # tails_val[i] = rank-value of last element of best chain len i+1
    tails_idx = []      # tails_idx[i] = index (in seq) of that element
    prev = {}           # seq index -> its predecessor's seq index in the chain
    sign = -1 if decreasing else 1

    for idx, sym in enumerate(seq):
        r = rank.get(sym)
        if r is None:
            continue
        val = sign * r
        p = bisect_left(tails_val, val)
        prev[idx] = tails_idx[p - 1] if p > 0 else None
        if p == len(tails_val):
            tails_val.append(val)
            tails_idx.append(idx)
        else:
            tails_val[p] = val
            tails_idx[p] = idx

    # reconstruct from the tail of the longest chain
    chain = []
    k = tails_idx[-1] if tails_idx else None
    while k is not None:
        chain.append(seq[k])
        k = prev.get(k)
    chain.reverse()
    return chain


def lds_greedy(seq, rank):
    """As in the paper: an (approximate) longest *decreasing* subsequence of
    seq w.r.t. rank (exact via the symmetric LIS construction here).

    The peel itself can be long but is not (necessarily) output-sound; the
    paper takes, per iteration, the LIS of ``seq`` restricted to the peel's
    symbols (see lispiprime in the pseudocode) as the candidate. That restricted
    string is a subsequence of ``seq`` and, being increasing w.r.t. the anchor
    order, also a subsequence of the clean string -- hence genuinely common.
    """
    return _lis_by_rank(seq, rank, decreasing=True)


# ---------------------------------------------------------------------------
# The deterministic approximation (the "certifier")
# ---------------------------------------------------------------------------

def first_occurrence_order(s):
    """Repetition-free subsequence of s: first occurrence of each symbol.
    Interpreted as a total order over the symbols (occurs-before = smaller)."""
    order = []
    seen = set()
    for c in s:
        if c not in seen:
            seen.add(c)
            order.append(c)
    return order


def peel_anchor_lcs(a, b, band_logs=True):
    """Deterministic LCS approximation over alphabet symbols of a.

    Returns a dict with the output common subsequence, its length, the anchor
    order used, the band certificate, and work metrics.

    Algorithm (Deterministic near-linear, Boneh-Golan-Krauthgammer 2025):
      1. Anchor order: pi = first-occurrence order of a.
      2. If band_logs: certify a dyadic frequency band B (Lemma 8 pigeonhole)
         and restrict both strings to symbols in B. The heavy peeling then runs
         only on the certified band's subproblem.
      3. Greedy LDS peeling: repeatedly take LDS of (restricted) b w.r.t. pi,
         use each peel as a candidate for the answer, delete its symbols from b
         until empty; keep the longest peel.
      4. Return the longest among: best single-symbol run, the initial LIS of b
         w.r.t. pi, and all peels.
    """
    rank = {sym: i for i, sym in enumerate(first_occurrence_order(a))}
    if not rank:
        return {"lcs": "", "length": 0, "anchor_order": [], "band": None,
                "peels": 0, "candidates": 0}

    freq = Counter(a)
    n = len(a)

    band = None
    if band_logs and n:
        # dyadic bands [2^t, 2^{t+1}); pigeonhole-certified "good" band.
        best_score = -1
        for t in range(n.bit_length()):
            lo, hi = 1 << t, 1 << (t + 1)
            mass = sum(freq[c] for c, f in freq.items() if lo <= f < hi)
            if mass > best_score:
                best_score = mass
                band = (lo, hi)
        lo, hi = band
        keep = {c for c, f in freq.items() if lo <= f < hi}
        a = "".join(c for c in a if c in keep)
        b = "".join(c for c in b if c in keep)
        # re-anchor on the restricted string, as the scheme consumes it
        rank = {sym: i for i, sym in enumerate(first_occurrence_order(a))}
        if not rank:
            return {"lcs": "", "length": 0, "anchor_order": [],
                    "band": band, "peels": 0, "candidates": 0}

    # Pry the anchor order out: it is exactly "first occurrence order", the
    # deterministic replacement for Mao-Rubinstein's random active-scale sampler.
    anchor_order = first_occurrence_order(a)

    candidates = []

    # Candidate 0: best single-symbol common run.
    fa, fb = Counter(a), Counter(b)
    for sym, ca in fa.items():
        cb = fb.get(sym, 0)
        if cb:
            candidates.append(sym * min(ca, cb))

    if not b:
        length = max(map(len, candidates), default=0)
        return {"lcs": max(candidates, key=len, default=""),
                "length": length, "anchor_order": anchor_order,
                "band": band, "peels": 0, "candidates": len(candidates)}

    # Candidate 1: full LIS of b under the anchor order (a complete pass).
    candidates.append("".join(_lis_by_rank(b, rank)))

    # Candidate 2+: greedy LDS peeling. Every peel must be output-sound.
    peeled_bs = b
    n_peels = 0
    while peeled_bs and n_peels < 256:
        peel = lds_greedy(peeled_bs, rank)
        if not peel:
            break
        # output-sound candidate: LIS of the restricted string (per the paper)
        delset = set(peel)
        restricted = "".join(c for c in peeled_bs if c in delset)
        candidates.append("".join(_lis_by_rank(restricted, rank)))
        n_peels += 1
        # delete the peel's symbols out of the working copy of b
        peeled_bs = "".join(c for c in peeled_bs if c not in delset)

    best = max(candidates, key=len, default="")
    return {"lcs": best, "length": len(best),
            "anchor_order": anchor_order, "band": band,
            "peels": n_peels, "candidates": len(candidates)}


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

def _selftest():
    random.seed(0)
    for k in (2, 10, 26):
        for n in (20, 50, 100):
            for _ in range(50):
                a = "".join(random.choice("abcdefghijklmnopqrstuvwxyz"[:k])
                            for _ in range(n))
                b = "".join(random.choice("abcdefghijklmnopqrstuvwxyz"[:k])
                            for _ in range(n))
                exact, _ = lcs_len_exact(a, b)
                res = peel_anchor_lcs(a, b)
                got = res["length"]
                assert got <= exact, (got, exact, a, b)
                assert is_subsequence(res["lcs"], a), (a, res["lcs"])
                assert is_subsequence(res["lcs"], b), (b, res["lcs"])
                assert len(res["lcs"]) == got
    print("selftest OK")


if __name__ == "__main__":
    _selftest()
    print("deterministic LCS approx + exact baseline ready")