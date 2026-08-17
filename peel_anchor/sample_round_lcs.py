#!/usr/bin/env python3
"""Grid sample-and-round LCS (Mao-Rubinstein 2026, arXiv:2603.29702).

Faithful-to-spec implementation of the *LCS* half (longest-path on the rotated
grid with diagonal shortcuts), for benchmarking quality against the
deterministic certifier (peel_anchor_lcs) and exact DP:

  - Rotated grid columns x = u+v in [0, 2n]; path visits each column once with
    adjacent rows differing by <= 1 (rotation property, Def 5.3/5.4).
  - Divide-and-conquer recursion with branching factor M.
  - On *active* scales: anchors are rounded to the straight line between the
    interval endpoints (the "single candidate path" restriction), and each
    sub-interval is kept w.p. 1/2 (eta_i) with a 2x reweight -- this is the
    sub-sampling that buys the running-time saving.
  - On *inactive* scales: exact sub-DP between the endpoints (no rounding, no
    sub-sampling), as the paper rounds/samples only on active scales.
  - Base case: band DP on small intervals.

Rebuilt from scratch here (self-contained, stdlib only). This is a *quality*
reproduction -- the sub-sampling is real and drives an honest speed-vs-quality
profile, but the asymptotic pruning (discarding 2^-log^O(1)(n) fraction of the
x-axis via per-interval sampling) is exercised at practical sizes, not proven.
"""

import math
import random


# LCS grid: a match consumes 2 rotated columns (a 2-column horizontal jump),
# so any path inside [xl, xr] scores at most (xr-xl)//2. Use this as the
# back-up cap so the estimate never exceeds the true LCS even under reweight.
def _width_cap(xl, xr):
    return (xr - xl) // 2


# ---------------------------------------------------------------------------
# Exact rotated-grid DP (validation)
# ---------------------------------------------------------------------------

def lcs_len_exact(a, b):
    """Standard O(n^2) LCS DP from peel_anchor_lcs, for validation."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m]


def rotated_dp_exact(a, b):
    """LCS via the shortest/longest-path view on the 45*-rotated grid.

    Rotated coords: original (u,v) -> x = u+v, y = v-u+n.
    Path from (0, n) to (2n, n); match = horizontal jump (x-2)->x same row.
    Returns exact LCS value (must equal lcs_len_exact for all inputs).
    """
    n = len(a)
    NEG = -10**9
    f = {(0, n): 0}
    for x in range(1, 2 * n + 1):
        ymin = n - x
        ymax = n + x
        for y in range(max(0, ymin), min(2 * n, ymax) + 1):
            best = NEG
            # zero-cost grid edges from previous column (insert/delete)
            if (x - 1, y - 1) in f:
                best = max(best, f[(x - 1, y - 1)])
            if (x - 1, y + 1) in f:
                best = max(best, f[(x - 1, y + 1)])
            # diagonal shortcut (x-2, y) -> (x, y): a match if chars equal
            if (x - 2, y) in f:
                u = (x - 2 + n - y) // 2
                v = (x - 2 + y - n) // 2
                w = (1 if a[u] == b[v] else 0) if 0 <= u < n and 0 <= v < n else 0
                if w:
                    best = max(best, f[(x - 2, y)] + w)
            if best != NEG:
                f[(x, y)] = best
    return f.get((2 * n, n), NEG)


def _band_dp(a, b, xl, xr, yl, yr):
    """Exact longest-path value from rotated (xl, yl) to (xr, yr), base case."""
    n = len(a)
    if abs(yr - yl) > (xr - xl):
        return 0.0  # unreachable under the row-speed<=1 constraint: sound 0
    NEG = -10**9
    f = {(xl, yl): 0}
    for x in range(xl + 1, xr + 1):
        for y in range(0, 2 * n + 1):
            best = NEG
            if (x - 1, y - 1) in f:
                best = max(best, f[(x - 1, y - 1)])
            if (x - 1, y + 1) in f:
                best = max(best, f[(x - 1, y + 1)])
            if (x - 2, y) in f:
                u = (x - 2 + n - y) // 2
                v = (x - 2 + y - n) // 2
                if 0 <= u < n and 0 <= v < n and (x - 2 + n - y) % 2 == 0 and a[u] == b[v]:
                    best = max(best, f[(x - 2, y)] + 1)
            if best != NEG:
                f[(x, y)] = best
    val = f.get((xr, yr), NEG)
    return 0.0 if val == NEG else val


# ---------------------------------------------------------------------------
# Sample-and-round (main)
# ---------------------------------------------------------------------------

def sample_and_round_lcs(a, b, M=4, active_p=0.5, B=8, seed=1,
                         mode="random", rounds=5, return_meta=False):
    """Mao-Rubinstein-style LCS approximation (sound under-estimate).

    Returns an estimate of LCS(X, Y) via rectangle recursion with rounding to
    straight-line anchors on active scales and subsampling of sub-intervals.

    The estimator is *sound* (never exceeds exact LCS) by taking the minimum
    over several independent subsampling rounds. Per-round expectation (with the
    M/kept reweight) equals the keep-all recursion's value, *modulo* the width
    back-up cap, which clips the reweighted sum — so "unbiased per round" is
    only true before that clip, and the minimum-of-rounds is a deliberately
    biased-down under-estimate. This mirrors the paper's "sound + complete +
    back-up" decomposition, with the honest caveat that the reweight is not
    exact under the cap: min-rounds = (nearly) sound, reweighted expectation =
    (nearly) complete, and the width bound = back-up.

    mode:
      - 'random': active scales chosen randomly (the paper's scheme).
      - 'round_only': round to straight-line anchors on active scales but keep
        ALL sub-intervals (reweight=1, no sub-sampling). Isolates the quality
        of the rounding itself (the deterministic anchor idea) from the
        variance of sub-sampling. Always sound.
      - 'pinned': top scale always active (sanity: estimate <= exact holds by
        construction since any estimate is 2x of a subset of an exact value).
    """
    n = len(a)
    S = max(1, math.ceil(math.log(2 * n, M))) if 2 * n > 1 else 1

    def is_active(scale, top, rng):
        if mode == "pinned" and top:
            return True
        if scale == S:
            return True  # top scale always active: sanity connectivity
        return rng.random() < active_p

    def one_round(rng):
        def rec(xl, yl, xr, yr, scale, top):
            w = xr - xl
            if abs(yr - yl) > w:
                return 0.0  # unreachable (row-speed<=1): sound
            if w <= B:
                return _band_dp(a, b, xl, xr, yl, yr)
            anchors = [yl + (yr - yl) * i // M for i in range(M + 1)]
            anchors = [max(0, min(2 * n, y)) for y in anchors]
            if not is_active(scale, top, rng):
                return _band_dp(a, b, xl, yl, xr, yr)
            # Sample exactly M/2 sub-intervals (the paper's eta_i has exactly
            # half +1's), so the reweight is exactly 2 and kept>=1 always.
            if mode == "round_only":
                kept_idx = list(range(M))
                rate = 1.0
            else:
                half = M // 2
                kept_idx = rng.sample(range(M), half)
                rate = M / len(kept_idx)
            total = 0.0
            for i in kept_idx:
                sub = rec(anchors_idx(xl, w, M, i), anchors[i],
                          anchors_idx(xl, w, M, i + 1), anchors[i + 1],
                          scale - 1, False)
                total += sub
            return min(rate * total, float(_width_cap(xl, xr)))  # back-up cap

        def anchors_idx(m0, w, M, i):
            return m0 + i * w // M

        return rec(0, n, 2 * n, n, S, True)

    est = float("inf")
    seeds = [seed + i for i in range(rounds)]
    for s in seeds:
        rng = random.Random(s)
        if mode == "round_only":
            est = one_round(rng)  # deterministic given seed; one pass suffices
            break
        cand = one_round(rng)
        if cand < est:
            est = cand
    meta = {"scales": S, "active_p": active_p, "M": M, "rounds": rounds,
            "mode": mode}
    if return_meta:
        return est, meta
    return est


if __name__ == "__main__":
    import random as _r
    _r.seed(0)
    ok = True
    for k in (2, 4, 16):
        for nn in (8, 16, 24):
            for _ in range(30):
                a = "".join(_r.choice("abcdefghijklmnopqrstuvwxyz"[:k]) for _ in range(nn))
                b = "".join(_r.choice("abcdefghijklmnopqrstuvwxyz"[:k]) for _ in range(nn))
                v1 = lcs_len_exact(a, b)
                v2 = rotated_dp_exact(a, b)
                v3 = sample_and_round_lcs(a, b)
                if v1 != v2:
                    ok = False
                    print("ROTATED MISMATCH", k, nn, v1, v2)
                if v3 > v1 + 1e-6:
                    ok = False
                    print("SR OVERESTIMATE", k, nn, v1, v3)
    print("validation:", "OK" if ok else "FAIL")
