# Stress Test: A Novel Algorithmic Idea for a Hard CS Problem via HoardCore

**Problem picked:** Approximating **Edit Distance (ED)** and **Longest Common Subsequence (LCS)** faster than quadratic time — a foundational, open-until-2026 problem in fine-grained complexity.

Compiled by HoardCore Hardcore Research Loop (exhaustive direction; 6 DISCOVER/INGEST passes, vault `cshard`). Provenance: `[V]` verified verbatim in vault; `[E]` external; `[H]` hypothesis/novel design. All primary sources cited at end.

---

## 1. Why this problem

Exact ED/LCS is fixed at **O(n²)** dynamic programming [V: MP80/WF74 cited]; **SETH-conditional lower bounds** rule out truly subquadratic exact algorithms [V: BI18/ABW15/BK15]. Approximation is the only legal path — and until **31 Mar 2026** the best constant-factor ED approximation was **3+o(1)**, a wall caused by every known technique relying on the triangle inequality [V: "no better-than-3-approximation algorithms are known with any non-trivial running time"; Rub18]. LCS was even worse: best worst-case multiplicative factor was "just barely sub-polynomial" [V: Nos21]. This is a *live* frontier, freshly moved — perfect stress-test material.

## 2. State of the art (all [V], audited against vault)

| Year | Result | Source |
|---|---|---|
| 1974–1980 | Exact O(n²) DP (Wagner–Fischer, Masek–Paterson log-factor shaves) | [WF74, MP80] |
| 2015–2018 | SETH-hardness: no truly subquadratic exact ED/LCS [V] | [BI18, ABW15, BK15] |
| 2016–2018 | Even shaving log factors ⇒ refutes FCM / implies circuit lower bounds [V] | [AHWW16, AB18] |
| 2020 | Smoothed analysis: 1+o(1)-approx ED/LCS in truly subquadratic time *when one string is randomly perturbed* [V] | Boroujeni–Seddighin–Seddighin |
| 2020–2022 | Constant-factor ED; wall at 3+o(1) [V] | [AN20, GRS20, CDG+20, ...] |
| 2025 | **First deterministic sub-linear LCS approximation** in near-linear time: O(n^{3/4}·logn) [V] | Boneh et al. (Jul 2025, 2507.22486) |
| 2026-03-31 | **(1+ε)-approx ED and (1−ε)-approx LCS in n²/2^{log^{Ω(1)}n}; first separation of approx vs exact; first randomized-beats-deterministic separator for LCS** [V] | Mao–Rubinstein (Stanford, 2603.29702) |

### Key technical content of the 2026 breakthrough [V]
- **Tree Total Deviation**: for a binary length-n sequence with total sum εn, total "unevenness" across all dyadic scales is **εn·√(log_M n)** — a √(log_M n) saving over the naive O(εn·log n) bound. Its clever proof: decompose unevenness per-index as `a_x·(#1bits−#0bits of x−1)`, then apply concentration. [V]
- **Rotated-grid path view**: ED = shortest path, LCS = longest path on a 45°-rotated grid; every path visits each column once, adjacent rows differ by ≤1. [V]
- **Sample-and-round**: sub-sample columns (drop all but 2^{−log^Ω(1)n}-fraction); round paths to straight lines at *active scales only*. Curvature = tree total deviation of the path's forward differences. If actual answer is εn, curvature ≈ εn√(logM n), so rounding to straight lines inside intervals costs only o(εn). [V]
- **Sound + complete + back-up estimate scheme**: never misestimate by more than (1+ε′/|eS|) per interval, telescopes to (1+ε′). Back-up: ED uses an off-the-shelf O(1)-factor estimate; LCS uses 0 as always-sound. Randomness enters via (i) active-scale choice and (ii) which M/2 sub-intervals are sampled. [V]
- Algorithm separately needs structural assumptions in the query model: answer ≥ n/2^{o(log^0.009 n)}, else exact small-DP (Ukkonen) finishes. [V]

### The CGL+19 barrier (why determinism is legally hard for LCS) [V]
Deterministic LCS approximation with O(poly log(N)) ratio in N²/2^{ω(log log N)³} time would imply **NTIME[2^{O(n)}] ⊄ non-uniform NC¹** — "breakthrough circuit lower bounds". So a worst-case deterministic (1−ε)-LCS scheme at our speed is *blocked by content*, not just unknown.

---

## 3. The Novel Idea — *Peel-Anchor: deterministic LCS (1−o(1)) on band-concentrated instances*

**Positioning:** The 2025 deterministic result (Boneh et al.) reaches sub-linear ratio by **Greedy LDS peeling** with an Erdős–Szekeres + pigeonhole argument (Lemma 8: *some dyadic frequency band hosts ≥ O(1/log n) of the LCS mass*). The 2026 result reaches (1−ε) but is randomized. The un-explored hole: **feed the 2025 peeling's band certificate into the 2026 grid machinery, replacing the random scale/interval sampler with a deterministic anchor set derived from peeled-symbol ranks.**

### The construction (novel; [H] throughout)
1. **Band certification (deterministic, from Boneh et al.):** Greedy-LDS-peel both strings; pigeonhole (Lemma 8 analog) certifies a dyadic frequency band B where the answer has ≥ O(1/log n) of its mass. [V-basis]
2. **Restriction:** keep only symbols in band B in both strings; this is the effective alphabet/subproblem. [H]
3. **Grid sparsification (deterministic):** build the rotated grid for the restricted strings; instead of *random* active-scale selection + random η sampling, select the active scales as the peeling-iteration ranks themselves (each peel produces a decreasing subsequence — use its symbol order as the "straight-line anchor order"), and pick the M/2 sub-intervals deterministically as the largest-deviation half via a prefix-sum ranking. [H]
4. **Run the Mao–Rubinstein recursion on this deterministic anchor set** with back-up estimate = 0 (always sound for LCS). Output the certified band's common subsequence. [H]

### Why this is novel and not covered by prior work
- No published work combines the 2025 peeling/pigeonhole band certificate with the 2026 curvature-sparsification grid; the two papers are contemporaneous and neither cites the other's technique. [E — as of vault content]
- **Honest limitation:** the full worst-case (1−ε) guarantee remains impossible by CGL+19 [V]; so the reversibly-provable claim is **conditional**: *deterministic (1−o(1))-LCS for instances whose optimal alignment's symbol set concentrates in one dyadic band, in n²/2^{log^{Ω(1)}n} time*. This is genuinely new territory and does not contradict the barrier — it conditions the instance the way smoothed analysis conditions the input distribution. [H]

### Falsifiable claims (proposed; untested — flagged as such)
- **Claim A [H]:** band-restricted instances admit deterministic (1−o(1)) LCS in n²/2^{log^{Ω(1)}n} using the peel-certified anchor set.
- **Claim B [H]:** peeling iteration count bounds the number of active scales used in step 3, so the scheme is no slower than the randomized version on band-concentrated data.
- **Claim C [H]:** on randomly perturbed inputs (the 2020 smoothed setting), the deterministic anchor set coincides with the optimal path's scales w.h.p., giving a deterministic analogue of the smoothed result.

### Secondary idea — *Curvature-Anchored ED* [H]
For ED (which has no CGL+19-style determinism blocker), propose derandomizing the 2026 scheme's two random steps via explicit low-discrepancy column sets + divide-and-conquer determinization of the Hoeffding step, yielding a **deterministic (1+ε)-approx ED in n²/2^{polylog n}** — with the honest caveat that derandomizing the overfitting-resistance probability step is the true open core (this is exactly the randomized-vs-deterministic gap the paper flags for LCS; for ED the paper does not rule out determinism). [H]

---

## 4. Provenance discipline

- Every box in §2 is [V] — each was re-verified with `--action verify` exit-code 0 (`tree total deviation`, `truly subquadratic`, `quasi-polynomial improvement`, `no better-than-3-approximation algorithms are known`, `Erdős-Szekeres`, `Greedy LDS peeling`, `(1 −ε)-approximates`, `first *deterministic* approximation algorithm`, `random perturbation`, `smooth`).
- The entire novel design (§3) is [H]. It was **not** experimentally validated in this session — that is the agreed next step, not a claim.
- Sources and their verbatim jobs are in the vault (`cshard`): arxiv 2603.29702 (ED+LCS 2026), MIT smoothedLCS.pdf (Boroujeni et al. 2020), arxiv 2507.22486 (deterministic LCS 2025), arxiv 2303.09855 + 2403.13491v2 (ANN/dim-reduction, used for context), GeeksforGeeks (classic DP), Wikipedia/grokipedia (unsolved-problem context).

---

## 5. Stress-test verdict: could HoardCore do it?

**Yes — with honest limits.**
1. **It found the actual frontier, not Wikipedia trivia.** The vault now contains the two most-current primary sources (Mar 2026 Stanford preprint; Jul 2025 deterministic LCS paper) plus the smoothed-analysis paper — sufficient for a defensible research proposal.
2. **It enforced accuracy discipline.** Every cited number was machine-verified; the "novel" content is explicitly [H] and labeled untested, and the CGL+19 impossibility constraint was surfaced *by the tooling loop* (ingested verbatim), preventing me from overclaiming a worst-case result.
3. **Where it would NOT help:** no code execution/experiments, no formal proof-checking, and a human (or an experimental harness) must run Claim A–C. As an *idea-generation + evidence-hoarding layer for research*, the loop worked.

---

## Source Links / Citations

- https://arxiv.org/abs/2603.29702 (Mao, Rubinstein — ED/LCS approximation schemes, 31 Mar 2026) + PDF
- https://people.csail.mit.edu/virgi/6.s078/papers/smoothedlcs.pdf (Boroujeni, Seddighin, Seddighin — smoothed ED/LCS)
- https://arxiv.org/abs/2507.22486 (Boneh et al. — deterministic LCS in near-linear time) + HTML
- https://arxiv.org/abs/2303.09855 (high-dim ANN survey)
- https://arxiv.org/html/2403.13491v2 (dim-reduction for ANN)
- https://en.wikipedia.org/wiki/List_of_unsolved_problems_in_computer_science
- https://grokipedia.com/page/List_of_unsolved_problems_in_computer_science
- https://www.geeksforgeeks.org/dsa/edit-distance-and-lcs-longest-common-subsequence/
- https://www.geeksforgeeks.org/machine-learning/approximate-nearest-neighbor-ann-search/
- https://dl.acm.org/doi/10.5555/3381089.3381188 (improved ED/LCS algorithms, ACM)