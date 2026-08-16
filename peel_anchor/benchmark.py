#!/usr/bin/env python3
"""Benchmark: deterministic LCS approx (Peel-Anchor certifier) vs exact O(n^2).

Three experiments:
  A. Approximation ratio   -- how close to exact LCS on random strings, across
                               alphabet sizes and lengths (validated vs DP).
  B. Runtime scaling       -- near-linear claimed; compare times as n grows.
  C. Smoothed/banded case  -- the Peel-Anchor thesis: when the anchor band is
                              chosen by the Lemma-8 pigeonhole certificate,
                              ratio vs exact stays high while cost is near-linear.
"""

import json
import os
import random
import time

from peel_anchor_lcs import is_subsequence as is_subseq, lcs_len_exact, peel_anchor_lcs
from sample_round_lcs import rotated_dp_exact, sample_and_round_lcs
from peel_anchor_hybrid import peel_anchor_hybrid, peel_anchor_hybrid_multipass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "benchmark_results.json")


def rand_string(n, k):
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHJKLMNOPQRSTUVWXYZ"
    return "".join(random.choice(alphabet[:k]) for _ in range(n))


def exp_A_ratio():
    """Approximation ratio vs exact, avg over samples."""
    rows = []
    for k in (2, 4, 8, 16, 26):
        for n in (20, 40, 80, 160):
            ratios, exacts, appxs = [], [], []
            for _ in range(40):
                a = rand_string(n, k)
                b = rand_string(n, k)
                ex, _ = lcs_len_exact(a, b)
                ap = peel_anchor_lcs(a, b)["length"]
                exacts.append(ex)
                appxs.append(ap)
                ratios.append((ap / ex) if ex else 1.0)
            rows.append({
                "k": k, "n": n,
                "avg_exact": round(sum(exacts) / len(exacts), 2),
                "avg_approx": round(sum(appxs) / len(appxs), 2),
                "avg_ratio": round(sum(ratios) / len(ratios), 4),
                "min_ratio": round(min(ratios), 4),
            })
    return rows


def exp_B_runtime():
    """Runtime vs n (peel-anchor approx)."""
    rows = []
    for n in (200, 400, 800, 1600, 3200, 6400):
        a = rand_string(n, 16)
        b = rand_string(n, 16)
        t0 = time.perf_counter()
        peel_anchor_lcs(a, b)
        dt = time.perf_counter() - t0
        rows.append({"n": n, "approx_sec": round(dt, 5)})
    return rows


def exp_C_banded_smoothed():
    """The smoothed/structured regime: b = a with random substitutions.
    LCS is ~(1-p)n, but a *sublinear-ratio* deterministic algorithm may still
    miss it badly -- we report both exact and approx so the gap is visible.
    """
    rows = []
    for n in (100, 200, 400):
        for p in (0.02, 0.05, 0.10):
            exacts, appxs = [], []
            for _ in range(20):
                a = rand_string(n, 16)
                b = list(a)
                for i in range(n):
                    if random.random() < p:
                        b[i] = random.choice("abcdefghijklmnop")
                b = "".join(b)
                ex, _ = lcs_len_exact(a, b)
                res = peel_anchor_lcs(a, b)
                ap = res["length"]
                exacts.append(ex)
                appxs.append(ap)
            rows.append({
                "n": n, "mutation_p": p,
                "avg_exact": round(sum(exacts) / len(exacts), 2),
                "avg_approx": round(sum(appxs) / len(appxs), 2),
                "avg_ratio": round(sum(ap / ex for ap, ex in zip(appxs, exacts)
                                     ) / len(exacts), 4),
                "min_ratio": round(min(ap / ex for ap, ex in zip(appxs, exacts)), 4),
            })
    return rows


def exp_D_anchor_aligned():
    """Strings structured w.r.t. the anchor order: block-repetition and
    increasing-cycle strings. The LIS-of-b-under-anchor candidate should
    recover ~all of a large LCS (ratio -> 1)."""
    rows = []
    # D1: block-repeated cycles  abcdeabcde... (anchor order=lcs order)
    for n in (100, 200, 400, 800):
        cyc = "abcde"
        a = "".join(cyc[i % 5] for i in range(n))
        b = "".join(cyc[(i + 2) % 5] for i in range(n))  # same cycle, shifted
        ex, _ = lcs_len_exact(a, b) if n <= 400 else (n, None)
        res = peel_anchor_lcs(a, b)
        ap = res["length"]
        rows.append({
            "case": "cycle_shift", "n": n,
            "exact": ex, "approx": ap,
            "ratio": round(ap / ex, 4),
        })
    # D2: b = a with a shuffled tail (top half identical, bottom random):
    # anchor consistency holds on the head.
    for n in (100, 200, 400, 800):
        a = "abcdefghijklmnop" * (n // 16)
        a = a[:n]
        head = a[: n // 2]
        tail = "".join(random.choice("abcdefghijklmnop") for _ in range(n // 2))
        b = head + tail
        ex, _ = lcs_len_exact(a, b) if n <= 400 else (n // 2, None)
        res = peel_anchor_lcs(a, b)
        ap = res["length"]
        rows.append({
            "case": "head_exact", "n": n,
            "exact": ex, "approx": ap,
            "ratio": round(ap / ex, 4),
        })
    return rows


def exp_E_sample_round():
    """The 2026 sample-and-round grid vs the deterministic certifier vs exact,
    on the smoothed regime (b = a with p·n substitutions) that killed the
    certifier, and on plain random strings.
    Columns: certifier, random sub-sample+round, round-only (no sub-sampling)."""
    rows = []
    for n in (32, 64, 128):
        for p in (0.02, 0.10, None):
            if p is None:
                kind = "random"
            else:
                kind = f"p{p:g}"
            exacts, certs, srs, ros = [], [], [], []
            for _ in range(20):
                a = "".join(random.choice("abcdefghijklmnop") for _ in range(n))
                if kind == "random":
                    b = "".join(random.choice("abcdefghijklmnop") for _ in range(n))
                else:
                    b = list(a)
                    for i in range(n):
                        if random.random() < p:
                            b[i] = random.choice("abcdefghijklmnop")
                    b = "".join(b)
                ex = lcs_len_exact(a, b)[0]
                cert = peel_anchor_lcs(a, b)["length"]
                sr = sample_and_round_lcs(a, b, B=8)
                ro = sample_and_round_lcs(a, b, B=8, mode="round_only")
                for tag, v in (("sr", sr), ("ro", ro)):
                    if v > ex + 1e-9:
                        raise RuntimeError(f"{tag} unsound: {v} > {ex}")
                exacts.append(ex)
                certs.append(cert)
                srs.append(sr)
                ros.append(ro)
            rows.append({
                "case": kind, "n": n,
                "avg_exact": round(sum(exacts) / len(exacts), 2),
                "avg_certifier": round(sum(certs) / len(certs), 2),
                "avg_cert_ratio": round(
                    sum(c / e for c, e in zip(certs, exacts)) / len(exacts), 4),
                "avg_sample_round": round(sum(srs) / len(srs), 2),
                "avg_sr_ratio": round(
                    sum(s / e for s, e in zip(srs, exacts)) / len(exacts), 4),
                "avg_round_only": round(sum(ros) / len(ros), 2),
                "avg_ro_ratio": round(
                    sum(s / e for s, e in zip(ros, exacts)) / len(exacts), 4),
            })
    return rows


def exp_F_peel_anchor():
    """The new deterministic Peel-Anchor hybrid vs the two parents (certifier,
    random 2026 sample-and-round) and round_only, on the smoothed regime and
    plain random strings -- the SAME trial grid as exp E so every column is
    apples-to-apples.

    Columns: certifier (sound), random sample+round (sound via min-over-rounds),
    round-only (sound, no sub-sampling), and the deterministic hybrid. The hybrid
    reports *two* values:
      - length:   max(certifier, concatenated kept-sub-path) -- a REAL common
                  subsequence, provably <= exact (sound by construction);
      - reweighted: the deterministic reweighted estimator = the paper's
                  completeness ceiling (may exceed exact on a single pass; not
                  claimed sound, reported to show the machinery's headroom).
    """
    rows = []
    for n in (32, 64, 128):
        for p in (0.02, 0.10, None):
            kind = "random" if p is None else f"p{p:g}"
            exacts, certs, srs, ros, hybs, hyb_r, hyb_act = [], [], [], [], [], [], []
            for _ in range(20):
                a = "".join(random.choice("abcdefghijklmnop") for _ in range(n))
                if kind == "random":
                    b = "".join(random.choice("abcdefghijklmnop") for _ in range(n))
                else:
                    b = list(a)
                    for i in range(n):
                        if random.random() < p:
                            b[i] = random.choice("abcdefghijklmnop")
                    b = "".join(b)
                ex = lcs_len_exact(a, b)[0]
                cert = peel_anchor_lcs(a, b)["length"]
                sr = sample_and_round_lcs(a, b, B=8)
                ro = sample_and_round_lcs(a, b, B=8, mode="round_only")
                hyb = peel_anchor_hybrid(a, b, B=8)
                if hyb["length"] > ex:
                    raise RuntimeError(f"hybrid unsound: {hyb['length']} > {ex}")
                if hyb["lcs"] and not (is_subseq(hyb["lcs"], a)
                                       and is_subseq(hyb["lcs"], b)):
                    raise RuntimeError("hybrid lcs not a common subsequence")
                exacts.append(ex); certs.append(cert)
                srs.append(sr); ros.append(ro)
                hybs.append(hyb["length"]); hyb_r.append(hyb["reweighted"])
                hyb_act.append(len(hyb["active"]))
            rows.append({
                "case": kind, "n": n,
                "avg_exact": round(sum(exacts) / len(exacts), 2),
                "avg_certifier": round(sum(certs) / len(certs), 2),
                "avg_cert_ratio": round(
                    sum(c / e for c, e in zip(certs, exacts)) / len(exacts), 4),
                "avg_sample_round": round(sum(srs) / len(srs), 2),
                "avg_sr_ratio": round(
                    sum(s / e for s, e in zip(srs, exacts)) / len(exacts), 4),
                "avg_round_only": round(sum(ros) / len(ros), 2),
                "avg_ro_ratio": round(
                    sum(s / e for s, e in zip(ros, exacts)) / len(exacts), 4),
                "avg_hybrid": round(sum(hybs) / len(hybs), 2),
                "avg_hyb_ratio": round(
                    sum(h / e for h, e in zip(hybs, exacts)) / len(exacts), 4),
                "avg_hyb_reweighted": round(sum(hyb_r) / len(hyb_r), 2),
                "avg_hyb_rw_ratio": round(
                    sum(r / e for r, e in zip(hyb_r, exacts)) / len(exacts), 4),
                "avg_active_scales": round(sum(hyb_act) / len(hyb_act), 2),
            })
    return rows


def exp_G_peel_scaling():
    """Claim B support: Greedy-LDS peel iteration count (n_peels) vs n, on
    band-concentrated (smoothed) vs plain random instances. Claim B requires
    active scales = k = n_peels to stay small (O(log log n)) on the instances
    the scheme targets; this measures how n_peels actually grows, and how many
    of the S dyadic scales end up active under the Bug-1-fixed selector."""
    rows = []
    for n in (32, 64, 128, 256):
        for kind in ("random", "smooth"):
            peels, actives, scales_s = [], [], []
            for _ in range(20):
                a = rand_string(n, 16)
                if kind == "random":
                    b = rand_string(n, 16)
                else:
                    b = list(a)
                    for i in range(n):
                        if random.random() < 0.05:
                            b[i] = random.choice("abcdefghijklmnop")
                    b = "".join(b)
                r = peel_anchor_hybrid(a, b, B=8)
                peels.append(r["peels"])
                actives.append(len(r["active"]))
                scales_s.append(r["scales"])
            rows.append({
                "case": kind, "n": n,
                "avg_peels": round(sum(peels) / len(peels), 2),
                "avg_active": round(sum(actives) / len(actives), 2),
                "scales": round(sum(scales_s) / len(scales_s), 1),
                "active_fraction": round(
                    sum(a for a in actives) /
                    (sum(s for s in scales_s) or 1), 3),
            })
    return rows


def exp_H_keepfrac_closure():
    """The deterministic sound-gap closure: sweep keep_frac (fraction of the M
    sub-intervals kept at each active scale) on the smoothed regime that killed
    the certifier. At keep_frac=0.5 we match the 2026 paper's M/2 budget; at
    keep_frac=0.75 we spend 3/4 of round_only's sub-sampling budget; at 0.9-1.0
    we degenerate toward round_only (all sub-intervals kept -> exact recursion).
    Every row reports the SOUND reconstructed path ratio -- a verified common
    subsequence -- plus the reweighted ceiling and the multi-pass count."""
    rows = []
    for n in (64, 128):
        for frac in (0.5, 0.6, 0.75, 0.9):
            sound, rw, npass = [], [], []
            for _ in range(15):
                a = "".join(random.choice("abcdefghijklmnop") for _ in range(n))
                b = list(a)
                for i in range(n):
                    if random.random() < 0.20:
                        b[i] = random.choice("abcdefghijklmnop")
                b = "".join(b)
                ex = lcs_len_exact(a, b)[0]
                r = peel_anchor_hybrid_multipass(a, b, B=8, keep_frac=frac)
                if r["length"] > ex:
                    raise RuntimeError(f"keep_frac hybrid unsound: {r['length']} > {ex}")
                sound.append(r["length"] / ex)
                rw.append(r["reweighted"] / ex)
                npass.append(r["passes"])
            rows.append({
                "n": n, "keep_frac": frac,
                "avg_sound_ratio": round(sum(sound) / len(sound), 4),
                "avg_rw_ratio": round(sum(rw) / len(rw), 4),
                "avg_passes": round(sum(npass) / len(npass), 1),
            })
    return rows


def main():
    random.seed(20260816)
    out = {}
    out["exp_A_ratio"] = exp_A_ratio()
    out["exp_B_runtime"] = exp_B_runtime()
    out["exp_C_banded_smoothed"] = exp_C_banded_smoothed()
    out["exp_D_anchor_aligned"] = exp_D_anchor_aligned()
    out["exp_E_sample_round"] = exp_E_sample_round()
    out["exp_F_peel_anchor"] = exp_F_peel_anchor()
    out["exp_G_peel_scaling"] = exp_G_peel_scaling()
    out["exp_H_keepfrac_closure"] = exp_H_keepfrac_closure()
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()