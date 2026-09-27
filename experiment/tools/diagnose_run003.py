#!/usr/bin/env python3
"""Read-only decomposition of run-003's backtested_core scores.

DIAGNOSTIC. Changes nothing, re-scores nothing, proposes nothing. run-003 is
frozen and the stop rule stands; this explains the failure, it does not address
it.

Labels come from runs/run-003-evaluation.json rather than from the label file,
so evaluate.py remains the only module that has ever opened it.

Decomposition, from src/score.py:

    score = SUM over dimensions of subscore/100 * instrument_share * weight

    adoption      core share 0.80 x weight 30 = 24.0 points available
    maintainer    core share 0.60 x weight 30 = 18.0
    substitution  core share 0.50 x weight 25 = 12.5   (lower_is_better)
                                        total = 54.5

An input contributes value * effective_weight * pool, except in
substitution_risk, which inverts: inputs SUBTRACT from a 12.5-point ceiling.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = json.loads((ROOT / "runs" / "run-003.json").read_text())
EVAL = json.loads((ROOT / "runs" / "run-003-evaluation.json").read_text())

LABELS = {r["repo"]: r["label"] for r in EVAL["ranking"]}
YEARS = {r["repo"]: r["fund_snapshot"] for r in EVAL["ranking"]}
SHARE = {"adoption_without_capture": 0.80, "maintainer_profile": 0.60,
         "substitution_risk": 0.50}
INVERTED = {"substitution_risk"}

SHORT = {"distinct_non_contributor_issue_reporters_12mo": "reporters",
         "issue_reporter_growth_yoy": "reporter_growth",
         "fork_velocity_trailing_12mo": "forks",
         "production_mentions": "production_mentions",
         "contributor_concentration": "concentration",
         "stated_commercial_intent": "commercial_intent",
         "surface_is_natural_managed_service": "managed_service_surface",
         "license_hosting_protection": "licence_hosting",
         "(adoption_friction)": "adoption_friction",
         "(inverted baseline)": "subst_baseline"}


def raw_value(rec, input_id):
    f = rec["features"]
    raw = (f.get("gharchive") or {}).get("raw") or {}
    if input_id == "distinct_non_contributor_issue_reporters_12mo":
        return raw.get("non_contributor_issue_reporters_12mo")
    if input_id == "issue_reporter_growth_yoy":
        cur = raw.get("non_contributor_issue_reporters_12mo")
        pri = raw.get("non_contributor_issue_reporters_prior_12mo")
        return None if not pri else round(cur / pri, 2)
    if input_id == "fork_velocity_trailing_12mo":
        return raw.get("forks_12mo")
    if input_id == "production_mentions":
        return f["readme"]["production_users"]["tier"]
    if input_id == "contributor_concentration":
        return f["top1_commit_share"]
    if input_id == "stated_commercial_intent":
        return f["readme"]["commercial_intent"]["tier"]
    if input_id == "surface_is_natural_managed_service":
        return rec["surface"]
    if input_id == "license_hosting_protection":
        return f["licence_family"]
    return None


def decompose(rec):
    rows = []
    for dim in rec["score"]["backtested_core"]["dimensions"]:
        if not dim.get("in_instrument") or dim["subscore"] is None:
            continue
        dim_id = dim["id"]
        pool = SHARE[dim_id] * dim["rubric_weight"]
        for inp in dim["inputs"]:
            if not inp["available"]:
                rows.append({"dim": dim_id, "input": inp["input"],
                             "raw": raw_value(rec, inp["input"]),
                             "norm": None, "points": None})
                continue
            part = inp["value"] * inp["effective_weight"]
            rows.append({"dim": dim_id, "input": inp["input"],
                         "raw": raw_value(rec, inp["input"]),
                         "norm": round(inp["value"], 4),
                         "points": round(-part * pool if dim_id in INVERTED
                                         else part * pool, 3)})
        if dim_id in INVERTED:
            rows.append({"dim": dim_id, "input": "(inverted baseline)",
                         "raw": "constant", "norm": None, "points": round(pool, 3)})
        if dim_id == "adoption_without_capture":
            applied = dim.get("adoption_friction", {}).get("applied", 0)
            if applied:
                rows.append({"dim": dim_id, "input": "(adoption_friction)",
                             "raw": dim["adoption_friction"]["bucket"],
                             "norm": None, "points": round(applied / 100 * pool, 3)})
    return rows


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.fmean(rx), st.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return None if dx == 0 or dy == 0 else round(num / (dx * dy), 3)


scored = {p["repo"]: p for p in RUN["projects"] if p["score"]}
DECOMP = {r: decompose(rec) for r, rec in scored.items()}
SCORES = {r: scored[r]["score"]["backtested_core"]["score"] for r in scored}
ORDER = sorted(SCORES, key=lambda r: -SCORES[r])
# Union across all projects, order-preserving. Deriving this from a single
# project drops any term that project happens not to have — adoption_friction
# applies only to copyleft, so taking it from Terraform silently hid a -3.6
# point term on MongoDB.
INPUTS = []
for _repo in ORDER:
    for _row in DECOMP[_repo]:
        if _row["input"] not in INPUTS:
            INPUTS.append(_row["input"])


def points(repo, input_id):
    for row in DECOMP[repo]:
        if row["input"] == input_id:
            return row["points"]
    return None


def cell(repo, input_id):
    for row in DECOMP[repo]:
        if row["input"] == input_id:
            return row
    return {"raw": None, "norm": None, "points": None}


def main():
    print("=" * 100)
    print("run-003 backtested_core DECOMPOSITION — diagnostic only, nothing "
          "re-scored")
    print("pools: adoption 24.0  maintainer 18.0  substitution 12.5 "
          "(inverted)   total 54.5")
    print("=" * 100)

    # --- 0. full table --------------------------------------------------
    print("\n0. PER-INPUT CONTRIBUTION, ALL 24 SCORED PROJECTS")
    print("   raw | normalised | weighted points\n")
    for repo in ORDER:
        print(f"  {repo}  ({LABELS[repo]}, {YEARS[repo]})  "
              f"core {SCORES[repo]:.2f}/54.5")
        for row in DECOMP[repo]:
            name = SHORT.get(row["input"], row["input"])
            raw = "n/a" if row["raw"] is None else str(row["raw"])
            norm = "  --  " if row["norm"] is None else f"{row['norm']:.4f}"
            pts = " unavailable" if row["points"] is None else f"{row['points']:+8.3f}"
            print(f"      {name:24} {raw:>16} | {norm:>8} | {pts}")
        total = sum(r["points"] for r in DECOMP[repo] if r["points"] is not None)
        print(f"      {'TOTAL':24} {'':>16} | {'':>8} | {total:+8.3f}")
        print()

    # --- 1. head to tail -------------------------------------------------
    print("=" * 100)
    print("1. TOP TWO vs THE TWO LOWEST SUCCESSES, INPUT BY INPUT")
    pair = ["hashicorp/terraform", "docker/docker",
            "getsentry/sentry", "mongodb/mongo"]
    print(f"\n   {'input':24} " + " ".join(f"{r.split('/')[-1][:12]:>13}" for r in pair)
          + f" {'gap 1st-24th':>13}")
    for input_id in INPUTS:
        name = SHORT.get(input_id, input_id)
        vals = [points(r, input_id) for r in pair]
        cells = " ".join("          n/a" if v is None else f"{v:+13.3f}" for v in vals)
        gap = (None if vals[0] is None or vals[3] is None else vals[0] - vals[3])
        print(f"   {name:24} {cells} "
              + ("          n/a" if gap is None else f"{gap:+13.3f}"))
    tot = [SCORES[r] for r in pair]
    print(f"   {'TOTAL':24} " + " ".join(f"{v:+13.2f}" for v in tot)
          + f" {tot[0]-tot[3]:+13.2f}")
    print("\n   raw values behind those:")
    for input_id in INPUTS:
        if input_id.startswith("("):
            continue
        name = SHORT.get(input_id, input_id)
        print(f"   {name:24} " + " ".join(
            f"{str(cell(r, input_id)['raw']):>13}" for r in pair))

    # --- 2. per-input correlation with the primary label ------------------
    print("\n" + "=" * 100)
    print("2. PER-INPUT SPEARMAN WITH THE PRIMARY LABEL (success=1, modest=0, n=13)")
    print("   positive = input pushes successes UP; negative = wrong direction\n")
    primary = [r for r in ORDER
               if LABELS[r] in ("commercialized_success", "commercialized_modest")]
    y = [1 if LABELS[r] == "commercialized_success" else 0 for r in primary]
    print(f"   {'input':24} {'rho(points,label)':>18} {'n_used':>7}  direction")
    rows = []
    for input_id in INPUTS:
        pts = [points(r, input_id) for r in primary]
        pairs = [(p, lab) for p, lab in zip(pts, y) if p is not None]
        if len({p for p, _ in pairs}) <= 1:
            rows.append((input_id, None, len(pairs), "constant — no signal"))
            continue
        rho = spearman([p for p, _ in pairs], [lab for _, lab in pairs])
        direction = ("WRONG DIRECTION" if rho is not None and rho < 0
                     else "right direction" if rho and rho > 0 else "flat")
        rows.append((input_id, rho, len(pairs), direction))
    for input_id, rho, n, d in sorted(
            rows, key=lambda t: (t[1] is None, t[1] if t[1] is not None else 0)):
        name = SHORT.get(input_id, input_id)
        print(f"   {name:24} {('n/a' if rho is None else f'{rho:+.3f}'):>18} "
              f"{n:>7}  {d}")

    # --- 3. does any one input drive the ranking --------------------------
    print("\n" + "=" * 100)
    print("3. DOES ANY SINGLE INPUT DOMINATE THE RANKING?")
    print("   rho of each input's weighted points with the final core score, "
          "all 24\n")
    print(f"   {'input':24} {'rho(points,score)':>18} {'spread':>9} {'sd':>8} "
          f"{'share of spread':>16}")
    spreads = {}
    for input_id in INPUTS:
        pts = [points(r, input_id) for r in ORDER]
        have = [p for p in pts if p is not None]
        spreads[input_id] = (max(have) - min(have)) if len(have) > 1 else 0.0
    total_spread = sum(spreads.values())
    for input_id in sorted(INPUTS, key=lambda i: -spreads[i]):
        pts = [points(r, input_id) for r in ORDER]
        pairs = [(p, SCORES[r]) for p, r in zip(pts, ORDER) if p is not None]
        rho = (spearman([p for p, _ in pairs], [s for _, s in pairs])
               if len({p for p, _ in pairs}) > 1 else None)
        name = SHORT.get(input_id, input_id)
        print(f"   {name:24} {('n/a' if rho is None else f'{rho:+.3f}'):>18} "
              f"{spreads[input_id]:9.3f} "
              f"{(st.pstdev([p for p, _ in pairs]) if len(pairs) > 1 else 0):8.3f} "
              f"{spreads[input_id] / total_spread if total_spread else 0:15.1%}")

    # --- 4. mongodb -------------------------------------------------------
    print("\n" + "=" * 100)
    print("4. MONGODB — last of 24, and the least ambiguous success in the corpus")
    repo = "mongodb/mongo"
    print(f"\n   core {SCORES[repo]:.2f}/54.5, rank {ORDER.index(repo)+1}/24, "
          f"fund_snapshot {YEARS[repo]}\n")
    print(f"   {'input':24} {'mongo raw':>14} {'mongo pts':>11} "
          f"{'corpus median pts':>18} {'delta':>9}")
    for input_id in INPUTS:
        name = SHORT.get(input_id, input_id)
        mine = points(repo, input_id)
        others = [points(r, input_id) for r in ORDER if r != repo]
        others = [o for o in others if o is not None]
        med = st.median(others) if others else None
        raw = str(cell(repo, input_id)["raw"])
        if mine is None:
            print(f"   {name:24} {raw:>14} {'unavailable':>11} "
                  f"{(f'{med:.3f}' if med is not None else 'n/a'):>18} {'--':>9}")
        else:
            print(f"   {name:24} {raw:>14} {mine:+11.3f} "
                  f"{(f'{med:+.3f}' if med is not None else 'n/a'):>18} "
                  f"{(mine - med if med is not None else 0):+9.3f}")
    print("\n   corpus reporter counts for context (raw non-contributor "
          "issue reporters):")
    counts = sorted(((cell(r, "distinct_non_contributor_issue_reporters_12mo")["raw"] or 0, r)
                     for r in ORDER))
    for c, r in counts[:6]:
        print(f"      {r:26} {c:>6}   {LABELS[r]}")


if __name__ == "__main__":
    main()
