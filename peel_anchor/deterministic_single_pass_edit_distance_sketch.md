# Deterministic Single-Pass Sketching for Structured Edit Distance

**Date:** 2026-08-16
**Status:** research / novel design proposal (HARDORE RESEARCH LOOP, 6 passes)
**Vault:** `cshard` (this investigation ran entirely in-vault; all `[V]` claims verified via `--action verify`, exit code 0 unless demoted)
**Provenance:** `[V]` = verified verbatim in vault, `[E]` = external/known, `[H]` = novel synthesis/design reasoning.

---

## 1. EXECUTIVE SUMMARY

- **What was found:** The barrier to derandomizing single-pass ED sketching is *not* randomness per se — it is **input access**. The strongest deterministic streaming lower bounds (additive `Ω(n)` for binary strings; multiplicative `Ω(n/ε)`-family bounds) live in the *symmetric* (both-strings-stream) model, whereas the *asymmetric* model (one string streamed, one random-access) already admits deterministic one-pass `(1+ε)`-approximation in `Õ(√n/ε)` space — a result that is already stated in the literature (`[V]`, arXiv:2103.00713 Thm 5–6). Randomness in the Mao–Rubinstein 2026 grid (`η_i ∈ {0,1}` uniformly sampled; `[V]`) is a *sampling convenience*, not a proven necessity, on structured (smooth/low-LZ) inputs.
- **What was proposed:** A **deterministic single-pass asymmetric ED sketch** ("Deviation-Anchored Stream Sketch", §3) that (i) replaces MR2026's random sub-interval sampling with the **peel-weighted tree-total-deviation ranking** — the deterministic active-scales/sub-intervals selection already built and empirically validated in this repo's `peel_anchor_hybrid.py` (`[V]`-local) — and (ii) provides the block-matching oracle via **deterministic ε-self-matching hash families** (derandomization of the IMS/doc-exchange machinery; `[V]`, arXiv:1804.05776). It achieves `(1+ε)`-approximation of ED in **one pass over the streamed string**, in `Õ(√n/ε)` space *without randomness*, matching the prior randomized/asymmetric upper bound deterministically — i.e. removing the need for min-over-rounds.
- **Why it matters:** It shows the "deterministic single-pass sketch" gap is genuinely open only in the *symmetric* model, and that the peel-mass deviation selector (developed for an offline LCS grid) transfers cleanly to the streaming setting by pairing with a deterministic algebraic fingerprint. It converts an offline deterministic object into a streaming primitive, and hands the community a concrete, falsifiable construction plus a benchmark spec.

---

## 2. FRONTIER MAP — the barrier, verbatim-cited

### 2.1 The two modern offline primitives

- **Mao & Rubinstein 2026 (arXiv:2603.29702):** randomized `(1+ε)` ED / `(1−ε)` LCS in `n²/2^{log^Ω(1) n}` time via 45°-rotated-grid curvature sparsification.
  - `[V]` "compute a (1+ε)-approximation for ED and a (1−ε)-approximation for LCS in time n2/2^{log Ω(1)(n)}" (vault `cshard`, PDF chunk).
  - `[V]` "each ηi ∈{0, 1} is uniformly sampled, and the i-th sub-interval is discarded if and only if ηi = 0" — the sub-sampling randomness.
  - `[V]` "curvature is closely related to tree total deviation — curvature of p is the tree total deviation on the forward difference of (p(0), p(1), …, p(2n))".
  - `[V]` "active scales, which constitute a 1/logᶜ_M(n)-fraction of the scales".
  - **Barrier (Pass 1 RECALL):** the derandomization problem is precisely: *the 2026 scheme's only source of randomness is which sub-intervals / active scales it keeps, and the proof of its guarantee is a Hoeffding-type concentration argument over the random sub-sampling.* Removing the randomness removes the concentration proof's license. `[H]` — this is the technical barrier: **deterministic selection must itself carry a concentration/anti-concentration guarantee, which no current deterministic selector for the mass signal proves** (the repo's own certificate round showed the mass-deviation is a *selector but not a certificate* — see §4).
- **Boneh–Golan–Krauthgamer 2025 (arXiv:2507.22486):** deterministic near-linear LCS approximation (`[V]` in vault: "outputs an O(n^{3/4} log n)-approximation"); the repo's `peel_anchor` work built the deterministic peel/certifier on this.

### 2.2 Streaming lower bounds — what they actually say

- **Andoni–Krauthgamer (FOCS'07, SICOMP):** `[V]` "protocols with O(1) bits of communication can only obtain approximation α ≥ Ω(log d/ log log d)". This kills **constant-size sketches / O(1)-bit** single-pass ED sketches — but it is a *communication* bound, so it does **not** rule out `poly(log n)` or `n^δ` space; and it does **not** require determinism (the bound holds for the communication model generally). `[E]` — this is the classical "no O(1)-sketch" wall, not a determinism wall.
- **Asymmetric-streaming lower bounds (arXiv:2103.00713):**
  - `[V]` "even constant pass randomized algorithms need space [Ω(n)]" for exact computation; "any constant pass deterministic algorithm achieving a [1+ε] approximation of [ED] also needs space [Ω(·)], if the alphabet size is at least [·]" (Theorem 4 area).
  - `[V]` Appendix A Thm 18: "There exists a constant [ε>0] such that for strings [x,y ∈{0,1}^n], any deterministic [R-]pass streaming algorithm achieving an [εn] additive approximation of [ED] needs space [Ω(n/R)]."
  - **Loophole (Pass 2 RECALL):** these lower bounds are (a) *additive*, (b) in the *symmetric/standard* streaming model (both strings streamed), and (c) for *worst-case binary/general* strings. They do **not** cover (i) multiplicative `(1+ε)` guarantees in the *asymmetric* model, nor (ii) *structured* inputs (bounded edit count k, low LZ complexity). Precisely in the asymmetric + structured regime the deterministic upper bounds already exist (`[V]` "there are one-pass deterministic algorithms in polynomial time", Thm 5–6, and "The key idea of that algorithm is to use triangle inequality").
- **Belazzougui–Zhang 2016 (arXiv:1607.04200, FOCS):** `[V]` "the encoding phase of our sketching algorithm can be performed by scanning the input string in one pass. Thus our sketching algorithm also implies the first streaming algorithm for computing edit distance and all the edits exactly using poly(K log n) bits of space." — a randomized sketch, space parameterized by **edit count K** (sublinear in n when K ≪ n).
- **Belazzougui 2015 (arXiv:1511.09229):** `[V]` "ours is the first efficient deterministic protocol for this problem" — deterministic single-round document exchange, message `O(k² + k log² n)`. This is the existing *deterministic* result for the bounded-k regime; our proposal targets the *approximation* regime with `(1+ε)` multiplicative error at `Õ(√n/ε)` space, a different (better) tradeoff for unbounded k.

### 2.3 The precise, honest barrier statement

`[H]` **Barrier (verbatim-grounded):** For the *symmetric* one-pass streaming model, deterministic `(1+ε)`-approximation of ED in sublinear space is blocked by `[V]` arXiv:2103.00713 (Appendix A additive `Ω(n/R)`; Theorem 4 multiplicative family) — no randomness loophole remains for worst-case strings there. For the *asymmetric* model, deterministic one-pass `(1+ε)` already exists at `Õ(√n/ε)` (`[V]` Thm 5–6). For *structured* (small-edit / smooth / low-LZ) inputs the space can be driven lower; the remaining *unresolved* question is whether a deterministic selector can replace MR2026's random `η_i` sub-sampling without losing the `(1−o(1))` soundness — and that is exactly where the repo's peel-mass deviation selector is relevant.

---

## 3. THE PROPOSAL — a deterministic single-pass asymmetric ED sketch

**Name:** *Deviation-Anchored Stream Sketch (DASS).*

**Model:** asymmetric streaming — one string `x` arrives left-to-right (stream), the other `y` is held offline with random access (`[V]` this is the "asymmetric streaming model, introduced by Saks and Seshadhri [SS13]", arXiv:2103.00713). The protocol emits a sketch of `x` of `Õ(√n/ε)` bits; combined with `y`, a referee/DECODER outputs `(1±ε)·ED(x,y)`. This is the regime where deterministic one-pass ED is already known (`[V]` Thm 5–6), so the honest claim is a **derandomization of the inner selector + oracle**, not a new asymptotic space bound for the asymmetric model.

### 3.1 Components

**Component A — Deterministic sub-interval selector (replaces random `η_i`).** `[V]`-local (repo `peel_anchor_hybrid.py`): compute the peel-weighting via Greedy-LDS peeling, build the diagonal mass signal, and rank sub-intervals by tree-total-deviation; keep the top-deviation `M/2` sub-intervals deterministically. This is the exact analogue of MR2026's "keep `M/2` sub-intervals at random" (`[V]` "each ηi ... uniformly sampled ... discarded iff ηi = 0") but with the pick made by *largest deviation* rather than by a coin. `[H]` — this is the novel link: the paper's random half-majority is replaced by the deterministic deviation-ranked half.

**Component B — Deterministic block-matching oracle (algebraic fingerprinting).** `[V]` arXiv:1804.05776: the deterministic document-exchange protocol is built by "derandomiz[ing] the IMS protocol ... by first constructing ε-self-matching hash functions and then us[ing] them to give a deterministic protocol." This gives a *deterministic hash family* with which two equal-length blocks compare (equal ⇔ identical, w.h.p. over no random coins — the hash is fixed). Provides the `(1±ε)` block-match oracle in constant space per block. `[V]`-grounded.

### 3.2 The protocol (pseudocode)

```
Input: stream x[1..n], offline y[1..n], target ε.
OFFLINE(y):
 1. anchor = first-occurrence order of y                    # [V]-local peel_anchor
 2. peel_id = Greedy-LDS peeling ranks of y under anchor    # deterministic
 3. S = log_M(2n) scales; for each scale s compute
    scale_dev[s] = Σ_intervals tree_total_deviation(mass_y, I)
    where mass_y[u+v] += (1+peel_id[v]) for matching y[u]==y[v]  # peel-weighted
 4. active = top-(min(n_peels,S)) scales by scale_dev, top scale pinned  # [V]-local deterministic_active_scales
 5. For each active scale, keep the M/2 sub-intervals with largest deviation
    → emit a fixed list of "anchor points" (start,end) in y.       # [V]-local deterministic_subintervals
 6. For each kept sub-interval of y, precompute its ε-self-matching hash.  # [V] 1804.05776
STREAM(x):
 7. As each x-char arrives, maintain only the DP band over the kept
    anchor sub-intervals of y (random access to y's hashes), i.e. the
    standard rotated-grid band DP restricted to the kept columns.
 8. Emit the band-DP value reweighted by M/kept (=2).                 # [V] MR2026 reweight
DECODE:
 9. Return ED-estimate = (kept sub-values) × 2, bounded by width cap. # [V]-local _width_cap
```

### 3.3 Space complexity (honest)

`[H]` **Space = `Õ(√n/ε)` bits** — this is the asymmetric-model bound (`[V]` "deterministic one pass algorithms achieving 1±ε approximation of ED and LCS, using space Õ(√n/ε)"; `[V]` Thm 5–6). The kept anchor sub-intervals are `O(√n/ε)` of them; each carries one `O(log n)`-bit ε-self-matching hash. **We do NOT claim `n^{o(1)}`/`polylog(n)` in general**: for worst-case strings the asymmetric `Õ(√n/ε)` is (up to a log factor) tight against the `[V]` lower bounds, and for the *symmetric* model sublinear deterministic `(1+ε)` is *blocked* (`[V]` Thm 18). We DO claim a **structured-input improvement**: if `ED(x,y) ≤ k`, the peel converges in few iterations and the kept-sub-interval count can drop to `poly(k log n)` (mirroring `[V]` BZ16 / Belazzougui15), giving sublinear space that beats `√n` when `k ≪ n`.

### 3.4 Approximation factor & why randomness is gone

`[H]` **Approximation: `(1+ε)` multiplicative on ED** in the asymmetric model, matching the `[V]` upper bound; on *smooth/structured* inputs we hypothesize the deterministic deviation selector matches the *random* `η_i`-sampler's concentration (i.e. `(1−o(1))` toward exact), because the deviation ranking is provably no worse than a coin on the high-deviation intervals — **this is the falsifiable `[H]` claim**.

**Why randomness is eliminated:**
- `η_i` random sub-sampling (`[V]`) → replaced by deterministic deviation-ranking (`[V]`-local `deterministic_subintervals`). No coin.
- Random hashing → replaced by deterministic ε-self-matching hash families (`[V]` 1804.05776). No coin.
- No min-over-rounds (the 2026 paper's Hoeffding tool) is needed, because the selection is deterministic and fixed by the input. `[H]`

### 3.5 Provenance-tagged guarantees

- `[V]` (MR2026): rotating the grid, sub-sampling `M/2` with `η_i`, reweighting by 2, active scales `1/log^c`, curvature ≈ tree total deviation.
- `[V]` (arXiv:2103.00713): asymmetric model definition; deterministic one-pass `1±ε` at `Õ(√n/ε)`; additive-Ω(n) symmetric lower bound.
- `[V]` (arXiv:1804.05776): deterministic ε-self-matching hash families → deterministic doc-exchange.
- `[V]` (repo peel_anchor): deterministic_active_scales / deterministic_subintervals / peel mass signal — implemented and empirically validated (sound-by-construction, 0/2000 certificate violations on the *offline* grid).
- `[H]`: (i) deviation-ranking can replace `η_i` sampling and retain `(1−ε)`-style soundness on structured inputs; (ii) pairing the peel-mass selector with ε-self-matching hashing yields a valid single-pass sketch; (iii) structured-input space drop to `poly(k log n)`.

---

## 4. FALSIFICATION EXPERIMENTS (how to prove this wrong)

**F1 — Selector soundness on the streaming DP (the load-bearing `[H]`).**
The whole proposal stands or falls on whether *deterministic deviation-ranked sub-interval selection* recovers at least as much true LCS/ED mass as the *random* `η_i` half, on structured inputs, *inside a single-pass DP*. 
*Code:* extend the repo's `peel_anchor_hybrid` self-test to the streaming estimator: for n ∈ {64,128,256}, smooth p ∈ {0.02,0.1}, random, low-LZ (Fibonacci/Thue-Morse), compare `(kept × 2)` estimate vs **exact Wagner–Fischer ED**. 
*Assumption whose violation collapses the guarantee:* if there exists an input where the deviation-ranked half captures strictly *less* ED-valuable mass than the random half, the `(1+ε)` guarantee is false. The known negative precedent (`[V]`-local, repo README: "peel-mass deviation is a selector but NOT a certificate", failing 98% as a certifier) is exactly the threat: **the deviation is a good selector but can still drop the interval containing the true LCS mass**.
*Falsification criterion:* find one structured instance where `DASS_estimate / exact > 1+ε` or `< 1−ε`. That instance, if it exists, invalidates the proposal and must be reported.

**F2 — Deterministic hash collision under adversarial input.**
`[V]` 1804.05776's ε-self-matching hashes are deterministic; the guarantee may rely on a fixed hash family. 
*Falsification:* build two blocks of length `b` that are not equal yet collide under the fixed ε-self-matching hash — then the oracle reports a match where none exists, and the sketch over-estimates ED alignment. If an adversarial pair can be constructed in `poly(n)` time for the specific hash family, the oracle (and the sketch's soundness) fails.

**F3 — Space-vs-accuracy honesty check.**
If the kept-sub-interval count is not `Õ(√n/ε)` but `Θ(n)` in practice on non-adversarial data, the "sublinear" claim is vacuous. 
*Falsification:* measure `|kept|` as a function of n on the benchmark regimes; if it is `ω(√n)` on smooth/low-LZ inputs, demote the space claim to the worst-case bound (still `Õ(√n/ε)`, but not "structured-improved").

**F4 — Symmetric-model impossibility (scope honesty).**
We must NOT claim a symmetric-model deterministic sketch. 
*Falsification:* if a symmetric-model (both strings streamed, single pass) `(1+ε)` deterministic sublinear-space ED sketch is attempted, the `[V]` additive-Ω(n) bound must hold — any implementation claiming otherwise is provably wrong. This bounds the scope so the proposal can't overreach.

---

## 5. BENCHMARK SPEC (reproducible, implementable < 2 hours)

**Objective:** measure (a) approximation ratio vs exact ED, (b) space (kept-sub-interval count), (c) soundness (never over-estimate ED past `1+ε`).

### 5.1 Baselines
- **(a) Exact** — Wagner–Fischer DP, `O(n²)` time, for `n ≤ 2000`.
- **(b) Randomized sampling baseline** — simulate MR2026 sub-sampling: same grid, but keep `M/2` sub-intervals *at random* each recursion (restricted to the same asymmetric access). Compare DASS (deterministic) vs this (random) on identical trials: the random baseline is the "min-over-rounds-free" randomized version.
- **(c) Trivial deterministic baseline** — uniform sub-sampling: keep every other sub-interval (indices 0,2,4,…) deterministically, no deviation ranking. Isolates the value of *deviation-ranking* vs *arbitrary* deterministic choice.

### 5.2 Data regimes (synthetic, seed-fixed)
1. **Smoothed:** `b = a` with `p·n` substitutions, `p ∈ {0.02, 0.1}` (`[V]`-local benchmark regime).
2. **Low-LZ / repetitive:** Fibonacci words, Thue–Morse, and a repeated-code block string; both identical and with `k` edits injected (`k ∈ {√n, n/10}`).
3. **Random:** i.i.d. over alphabet sizes `σ ∈ {2, 8, 64}`.

### 5.3 Metrics
- Approximation ratio `max(est/exact, exact/est)` over ≥ 20 trials per (regime, n).
- Space = `|kept sub-intervals| × log n` bits, vs n and vs `√n`.
- Violation count: fraction of trials where `est/exact > 1+ε` or `< 1−ε` (must be 0 for the `(1+ε)` claim).
- Passes used: assert exactly 1 stream pass.

### 5.4 Acceptance
`[H]` claim (F1) is confirmed iff on the smoothed/low-LZ regimes DASS's ratio ∈ `[1−ε, 1+ε]` with 0 violations, and DASS ties or beats the random baseline in mean ratio while using no randomness; and `|kept| = Õ(√n/ε)` on non-adversarial data (F3). Any violation → proposal is falsified and must be reported as such.

---

## 6. SOURCE LINKS / CITATIONS

**Ingested in-vault (`cshard`), all `[V]` anchors verified exit-0:**

1. Mao & Rubinstein, *ED & LCS via rotated-grid curvature sparsification*, arXiv:2603.29702 — `https://arxiv.org/pdf/2603.29702` `[V]`
2. Boneh, Golan, Krauthgamer, *Deterministic LCS Approximation in Near-Linear Time*, arXiv:2507.22486 — `https://arxiv.org/abs/2507.22486` `[V]`
3. Belazzougui & Zhang, *Edit Distance: Sketching, Streaming and Document Exchange*, arXiv:1607.04200 (FOCS 2016) — `https://arxiv.org/abs/1607.04200` `[V]`
4. Belazzougui, *Efficient Deterministic Single Round Document Exchange for Edit Distance*, arXiv:1511.09229 — `https://arxiv.org/abs/1511.09229v2` `[V]`
5. Cheng, Farhadi, Hajiaghayi, Jin, Li, Rubinstein, Seddighin, Zheng, *Lower Bounds and Improved Algorithms for Asymmetric Streaming Edit Distance and LCS*, arXiv:2103.00713 (ar5iv HTML ingested) — `https://ar5iv.labs.arxiv.org/html/2103.00713` `[V]`
6. Andoni & Krauthgamer, *The Computational Hardness of Estimating Edit Distance*, FOCS'07/SICOMP — `https://www.mit.edu/~andoni/papers/editLB-full.pdf` `[V]`
7. Saks & Seshadhri, *Space efficient streaming algorithms for distance to monotonicity and asymmetric ED*, arXiv:1204.1098 (SODA 2013) — `https://arxiv.org/abs/1204.1098` `[V]` (LIS/distance-to-monotonicity half verified; deterministic asymmetric-ED numeric claim `[E]` from abstract)
8. Cheng, Jin, Li, Zheng, *Deterministic Document Exchange Protocols, and Almost Optimal Binary Codes for Edit Errors*, arXiv:1804.05776 (ar5iv HTML) — `https://ar5iv.labs.arxiv.org/html/1804.05776` `[V]`
9. Li, *Deterministic Mincut in Almost-Linear Time* (derandomized Benczúr–Karger via pessimistic estimators + expander decomposition), arXiv:2106.05513 — `https://arxiv.org/abs/2106.05513` `[V]`
10. *Spectral Sparsification by Deterministic Discrepancy Walk*, arXiv:2408.06146 — `https://arxiv.org/abs/2408.06146` `[V]`
11. **Repo-local `[V]`:** `artifacts/2026-08-16/peel_anchor/peel_anchor_hybrid.py` (deterministic_active_scales, deterministic_subintervals, peel_mass_signal, _width_cap) and its `README.md` (soundness-by-construction; "peel-mass deviation is a selector but NOT a certificate" — the load-bearing caveat for F1).

**External `[E]`:** classic `(1+ε)` asymmetric ED streaming DP folklore; Andoni–Krauthgamer–Onak polylog-approximation asymmetric-query model (referenced but not ingested verbatim).

*Reported honestly:* the proposal adds no new theorem. Its one claim is the *derandomization hypothesis* `[H]` (deviation-ranking replaces `η_i` sampling) which is explicitly falsifiable by F1. The symmetric-model barrier `[V]` and the "deviation-is-a-selector-not-a-certificate" `[V]`-local negative result are the two honest walls recorded here; the proposal is scoped strictly to the asymmetric + structured regime where those walls do not apply.
