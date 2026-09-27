#!/usr/bin/env python3
"""Backtest evaluation. THE ONLY FILE PERMITTED TO READ benchmarks/outcomes.yaml.

Nothing under src/ may reference that file — tests/test_no_outcome_leak.py
enforces it structurally. This module sits at the top level, outside the scoring
package, and is imported by nothing.

It reads runs/run-003.json READ-ONLY. run-003 is frozen as scored: it was
produced before this file existed, and nothing here may alter it.

Evaluation follows runs/run-002-preregistration.yaml, committed 2026-08-15,
before any comparison to results:

    PRIMARY    AUC separating commercialized_success (n=6) from
               commercialized_modest (n=7) on backtested_core scores.
               Threshold 0.75. Ties count 0.5.
    SECONDARY  success vs failed. Reported, no threshold, explicitly weaker —
               the failed class still skews earlier in vintage.
    POWER      n=13 on the primary. A directional check that CANNOT support a
               significance claim. No p-value is computed or quoted.
    STOP RULE  If the threshold is not met the finding is recorded and
               published. No retuning. Any later variant is a new run with its
               own pre-registration.

    python3 evaluate.py
"""
from __future__ import annotations

import json
import random
import statistics as st
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
RUN_PATH = ROOT / "runs" / "run-003.json"
PREREG_PATH = ROOT / "runs" / "run-002-preregistration.yaml"
OUTCOMES_PATH = ROOT / "benchmarks" / "outcomes.yaml"
OUT_PATH = ROOT / "runs" / "run-003-evaluation.json"

BOOTSTRAP_RESAMPLES = 20_000
BOOTSTRAP_SEED = 20260816          # fixed so the interval reproduces exactly


# ---------------------------------------------------------------------------
# metric
# ---------------------------------------------------------------------------

def auc(positive: list[float], negative: list[float]) -> float | None:
    """Mann-Whitney concordance: P(positive ranks above negative), ties at 0.5.

    The pre-registered metric. Computed on raw core scores — never divided by
    max_achievable, which would be the weight redistribution the rubric
    forbids, and which cannot change a ranking anyway since every project in a
    run shares the same ceiling.
    """
    if not positive or not negative:
        return None
    wins = sum((1.0 if p > n else 0.5 if p == n else 0.0)
               for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def bootstrap_ci(positive, negative, resamples=BOOTSTRAP_RESAMPLES, alpha=0.05):
    """Stratified percentile bootstrap on the AUC.

    Resamples within each class with replacement, preserving class sizes. At
    n=6 against n=7 this is coarse — a resample can draw the same project six
    times — and the interval should be read as an honest statement of how
    little the sample constrains the estimate, not as a precise bound.
    """
    rng = random.Random(BOOTSTRAP_SEED)
    values = []
    for _ in range(resamples):
        p = [rng.choice(positive) for _ in positive]
        n = [rng.choice(negative) for _ in negative]
        values.append(auc(p, n))
    values.sort()
    lo = values[int((alpha / 2) * resamples)]
    hi = values[min(int((1 - alpha / 2) * resamples), resamples - 1)]
    return {"lo": round(lo, 4), "hi": round(hi, 4),
            "level": f"{int((1 - alpha) * 100)}%",
            "resamples": resamples, "seed": BOOTSTRAP_SEED,
            "median": round(values[resamples // 2], 4)}


def discordant_pairs(pos, neg):
    """Every (success, modest) pair the ranking got wrong or tied."""
    out = []
    for pr, ps in pos:
        for nr, ns in neg:
            if ps < ns:
                out.append({"success": pr, "success_score": ps,
                            "other": nr, "other_score": ns, "kind": "inverted"})
            elif ps == ns:
                out.append({"success": pr, "success_score": ps,
                            "other": nr, "other_score": ns, "kind": "tied"})
    return out


def spearman(xs, ys):
    """Rank correlation, average ranks for ties. No scipy dependency."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    return pearson(rx, ry)


def pearson(xs, ys):
    n = len(xs)
    mx, my = st.fmean(xs), st.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return None if dx == 0 or dy == 0 else round(num / (dx * dy), 4)


# ---------------------------------------------------------------------------

def main() -> int:
    run = json.loads(RUN_PATH.read_text())          # read-only, never written
    prereg = yaml.safe_load(PREREG_PATH.read_text())["run_002_preregistration"]
    outcomes = yaml.safe_load(OUTCOMES_PATH.read_text())

    threshold = prereg["threshold"]

    # The pre-registration governs runs that clear the coverage criterion. A
    # run that did not qualify must not be evaluated at all.
    if not run.get("qualifies_as_confirmatory"):
        print(f"{run['run']} did not qualify as confirmatory "
              f"(coverage {run['coverage']}, target {run['target_coverage']}). "
              "The pre-registration does not permit evaluating it.",
              file=sys.stderr)
        return 2

    labels = {p["repo"]: p["label"] for p in outcomes["projects"]}
    snapshots = {p["repo"]: p["fund_snapshot"] for p in run["projects"]}

    scored = {p["repo"]: p["score"]["backtested_core"]["score"]
              for p in run["projects"] if p["score"]}
    excluded = [p["repo"] for p in run["projects"] if not p["score"]]

    by_label = {}
    for repo, value in scored.items():
        by_label.setdefault(labels[repo], []).append((repo, value))

    success = sorted(by_label.get("commercialized_success", []), key=lambda t: -t[1])
    modest = sorted(by_label.get("commercialized_modest", []), key=lambda t: -t[1])
    failed = sorted(by_label.get("commercialized_failed", []), key=lambda t: -t[1])

    primary = auc([s for _, s in success], [s for _, s in modest])
    secondary = auc([s for _, s in success], [s for _, s in failed])
    ci = bootstrap_ci([s for _, s in success], [s for _, s in modest])
    wrong = discordant_pairs(success, modest)

    years = [snapshots[r] for r in scored]
    values = [scored[r] for r in scored]
    vintage_all = {"spearman": spearman(years, values),
                   "pearson": pearson(years, values), "n": len(years)}
    prim_repos = [r for r, _ in success + modest]
    vintage_primary = {
        "spearman": spearman([snapshots[r] for r in prim_repos],
                             [scored[r] for r in prim_repos]),
        "pearson": pearson([snapshots[r] for r in prim_repos],
                           [scored[r] for r in prim_repos]),
        "n": len(prim_repos)}

    median_vintage = {k: st.median([snapshots[r] for r, _ in v])
                      for k, v in by_label.items() if v}

    # ---------------- report ------------------------------------------------
    print("=" * 74)
    print(f"EVALUATION OF {run['run']}  (rubric {run['rubric_version']}, "
          f"frozen {run['generated_utc']})")
    print(f"pre-registered {prereg['committed_at']}; threshold {threshold} AUC, "
          "ties 0.5")
    print("=" * 74)

    print("\n1. PRIMARY RESULT")
    print(f"   AUC (commercialized_success n={len(success)} vs "
          f"commercialized_modest n={len(modest)}) = {primary}")
    print(f"   pairs = {len(success) * len(modest)}, "
          f"concordant-equivalent = {primary * len(success) * len(modest):.1f}")
    verdict = "MEETS" if primary is not None and primary >= threshold else "MISSES"
    print(f"   threshold {threshold}: {verdict}")

    print("\n2. CONFIDENCE INTERVAL")
    print(f"   {ci['level']} stratified percentile bootstrap "
          f"({ci['resamples']} resamples, seed {ci['seed']})")
    print(f"   AUC {primary}   CI [{ci['lo']}, {ci['hi']}]   "
          f"bootstrap median {ci['median']}")
    print(f"   includes 0.5: {ci['lo'] <= 0.5 <= ci['hi']}")

    print("\n3. FULL RANKING (backtested_core, max_achievable "
          f"{run['projects'][0]['score']['backtested_core']['max_achievable'] if scored else 'n/a'})")
    print(f"   {'#':>3} {'project':26} {'score':>7} {'snapshot':>9}  label")
    for i, (repo, value) in enumerate(
            sorted(scored.items(), key=lambda kv: -kv[1]), 1):
        print(f"   {i:>3} {repo:26} {value:7.2f} {snapshots[repo]:>9}  "
              f"{labels[repo]}")
    for repo in excluded:
        print(f"   {'--':>3} {repo:26} {'--':>7} {snapshots[repo]:>9}  "
              f"{labels[repo]}  (excluded: not scored)")

    print("\n4. PAIRS THE MODEL GOT WRONG (success ranked at or below modest)")
    if not wrong:
        print("   none")
    for w in wrong:
        print(f"   {w['kind']:8} {w['success']} ({w['success_score']:.2f}) "
              f"<= {w['other']} ({w['other_score']:.2f})")
    print(f"   {len(wrong)} of {len(success) * len(modest)} pairs")

    print("\n5. VINTAGE CHECK — does score track era rather than quality?")
    print(f"   all scored (n={vintage_all['n']}):     "
          f"spearman {vintage_all['spearman']}, pearson {vintage_all['pearson']}")
    print(f"   primary subset (n={vintage_primary['n']}): "
          f"spearman {vintage_primary['spearman']}, "
          f"pearson {vintage_primary['pearson']}")
    print("   median fund_snapshot by class:")
    for label in sorted(median_vintage):
        print(f"     {label:26} {median_vintage[label]}")

    print("\n6. SECONDARY — success vs failed")
    print(f"   AUC (success n={len(success)} vs failed n={len(failed)}) = {secondary}")
    print("   WEAKER COMPARISON. The failed class still skews earlier in "
          "vintage than\n   success, so any separation here is partly "
          "confounded with era. Reported\n   for completeness; no threshold "
          "attaches to it.")

    print("\n" + "=" * 74)
    print("n=13 on the primary comparison. This is a directional check and "
          "CANNOT\nsupport a significance claim. No p-value has been computed "
          "or quoted.")
    print("=" * 74)

    OUT_PATH.write_text(json.dumps({
        "evaluates": run["run"],
        "run_generated_utc": run["generated_utc"],
        "rubric_version": run["rubric_version"],
        "preregistration_committed_at": prereg["committed_at"],
        "threshold": threshold,
        "metric": "AUC by rank, ties 0.5, on backtested_core scores",
        "primary": {"comparison": "commercialized_success vs commercialized_modest",
                    "n_positive": len(success), "n_negative": len(modest),
                    "auc": primary, "meets_threshold": primary >= threshold,
                    "confidence_interval": ci,
                    "discordant_or_tied_pairs": wrong},
        "secondary": {"comparison": "commercialized_success vs commercialized_failed",
                      "n_positive": len(success), "n_negative": len(failed),
                      "auc": secondary,
                      "caveat": "weaker; failed class skews earlier in vintage"},
        "vintage_check": {"all_scored": vintage_all,
                          "primary_subset": vintage_primary,
                          "median_fund_snapshot_by_label": median_vintage},
        "ranking": [{"repo": r, "core_score": v, "label": labels[r],
                     "fund_snapshot": snapshots[r]}
                    for r, v in sorted(scored.items(), key=lambda kv: -kv[1])],
        "excluded_from_evaluation": [
            {"repo": r, "label": labels[r], "reason": "not scored in the run"}
            for r in excluded],
        "power": prereg["power"],
        "stop_rule": prereg["stop_rule"],
    }, indent=2, default=str) + "\n")
    print(f"\nwritten {OUT_PATH.relative_to(ROOT)}   "
          f"({RUN_PATH.relative_to(ROOT)} unmodified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
