#!/usr/bin/env python3
"""Blind scoring run. Writes runs/run-001.json.

Reads benchmarks/projects.yaml and the clones under /tmp/oregrade-clones.
Does not read benchmarks/outcomes.yaml, and neither does anything it imports —
tests/test_no_outcome_leak.py enforces that for src/.

    python3 run_scoring.py [--out runs/run-001.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src import features, gates, rubric as rubric_mod, score

ROOT = Path(__file__).resolve().parent


def blockers(records):
    """What is actually standing between this run and confirmatory status."""
    out = []
    uncollected = [r["repo"] for r in records
                   if r.get("features")
                   and not r["features"].get("gharchive", {}).get("collected")]
    if uncollected:
        out.append(f"GHArchive not collected for {len(uncollected)}/{len(records)} "
                   "projects — run tools/collect_gharchive.py (needs BigQuery "
                   "credentials and billing)")
    unresolved = [r["repo"] for r in records
                  if r.get("features")
                  and r["features"].get("licence_family") == "ambiguous"]
    if unresolved:
        out.append(f"licence unresolved for {unresolved} — costs "
                   "license_hosting_protection and drops coverage")
    failed_clone = [r["repo"] for r in records if r.get("clone_status") != "ok"]
    if failed_clone:
        out.append(f"not cloneable: {failed_clone}")
    inconsistent = [r["repo"] for r in records
                    if ((r.get("features") or {}).get("gharchive") or {})
                    .get("consistency", {}).get("ok") is False]
    if inconsistent:
        out.append(f"GHArchive returned zero events for repos that have commits "
                   f"— the query matched nothing: {inconsistent}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/run-003.json")
    ap.add_argument("--run-id", default="run-003")
    ap.add_argument("--mode", default="fund")
    args = ap.parse_args()

    corpus = yaml.safe_load((ROOT / "benchmarks" / "projects.yaml").read_text())
    rubric = rubric_mod.load()

    # Fail before scoring anything if a corpus surface has no managed-service
    # score. An unmapped surface must never silently take a default.
    rubric_mod.validate_surface_coverage(
        rubric, {p["surface"] for p in corpus["projects"]})

    records = [features.extract(project) for project in corpus["projects"]]

    # HALT before scoring on a cross-source impossibility.
    #
    # A repo with commits in its clone and zero PushEvent actors in GHArchive
    # has not been measured — its query matched nothing. Those zeros are legal
    # values that score as absent adoption, so a run containing them looks
    # complete and is not. This gate exists because that shipped once: the
    # repo.name collection returned all-zero rows for eight of twenty-four
    # projects and nothing objected.
    #
    # Deliberately a halt, not a warning. Writing the run file and flagging it
    # afterwards leaves a scored artefact on disk that someone can pick up.
    impossible = [r for r in records
                  if ((r.get("features") or {}).get("gharchive") or {})
                  .get("consistency", {}).get("ok") is False]
    if impossible:
        print("\nHALTED before scoring — GHArchive returned zero events for "
              "repos that have commits:", file=sys.stderr)
        for rec in impossible:
            c = rec["features"]["gharchive"]["consistency"]
            print(f"  {rec['repo']:26} {c['commits_in_clone']:6} commits in "
                  f"clone, {c['pushers_in_gharchive']} pushers in GHArchive",
                  file=sys.stderr)
        print("\nThe query matched nothing for these. Check repo.id in "
              "benchmarks/repo_ids.yaml and recollect.\nNo run file written.",
              file=sys.stderr)
        return 1

    for rec in records:
        rec.update(gates.run(rec))
        rec["score"] = (score.score_project(rec, args.mode, rubric)
                        if rec["cleared_all_gates"] else None)
        stop = rec["stopped_at"] or "SURVIVED"
        s = rec["score"]
        def show(part):
            return ("n/a" if part["score"] is None
                    else f"{part['score']}/{part['max_achievable']}")
        shown = (f"core {show(s['backtested_core']):>12} "
                 f"live {show(s['live_extension']):>6} "
                 f"cov {s['coverage']}") if s else "-"
        print(f"{rec['repo']:26} {rec['fund_snapshot']}  {stop:18} {shown}")

    # --- coverage criterion (rubric scoring.coverage_criterion) -------------
    # Two parts. A project below the floor is excluded, not the run; but more
    # than max_exclusions and the RUN fails rather than the projects. The run
    # basis is the median across INCLUDED projects.
    crit = rubric_mod.coverage_criterion(rubric)
    prereg = yaml.safe_load(
        (ROOT / "runs" / "run-002-preregistration.yaml").read_text())
    target = prereg["run_002_preregistration"]["target_coverage"]

    exclusions, included = [], []
    for rec in records:
        if rec["score"] is None:
            exclusions.append({"repo": rec["repo"], "coverage": None,
                               "reason": rec["stopped_at"] or "not scored"})
        elif rec["score"]["coverage"] < crit["project_floor"]:
            exclusions.append({
                "repo": rec["repo"], "coverage": rec["score"]["coverage"],
                "reason": f"coverage {rec['score']['coverage']} below "
                          f"project_floor {crit['project_floor']}"})
        else:
            included.append(rec)

    covs = sorted(r["score"]["coverage"] for r in included)
    median_coverage = covs[len(covs) // 2] if covs else None
    coverage_summary = {"min": covs[0], "median": median_coverage,
                        "max": covs[-1]} if covs else None

    over_exclusion_budget = len(exclusions) > crit["max_exclusions"]
    meets_target = bool(median_coverage is not None and median_coverage >= target)
    confirmatory = meets_target and not over_exclusion_budget

    run = {
        "run": args.run_id,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": args.mode,
        "rubric_version": rubric["version"],
        "corpus": "benchmarks/projects.yaml",
        "n_projects": len(records),
        "status": "confirmatory" if confirmatory else "exploratory",
        "coverage": coverage_summary,
        "coverage_criterion": dict(crit),
        "coverage_basis": "median across included projects",
        "target_coverage": target,
        "n_included": len(included),
        "n_excluded": len(exclusions),
        "exclusions": exclusions,
        "exclusion_budget_exceeded": over_exclusion_budget,
        "meets_target_coverage": meets_target,
        "qualifies_as_confirmatory": confirmatory,
        "confirmatory_blocked_on": ([] if confirmatory else blockers(records)),
        "note": (
            "Blind run. Scoring code has never read the resolved labels. "
            "Git-only features; the rubric's live_tool_only and "
            "not_reconstructable inputs are absent and each dimension "
            "renormalises over what was available. Dropped dimensions lower "
            "max_achievable and are NOT redistributed. PR velocity is "
            "substituted with commit velocity at gate 2. Gate 5 "
            "(counterparty) is not evaluated for any project. The gates are "
            "not tested by this corpus — see runs/README.md."),
        "projects": records,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(run, indent=2, default=str) + "\n")
    cleared = sum(1 for r in records if r["cleared_all_gates"])
    print(f"\n{cleared}/{len(records)} cleared all evaluated gates")
    print(f"included {len(included)}  excluded {len(exclusions)} "
          f"(budget {crit['max_exclusions']})")
    if coverage_summary:
        print(f"coverage median {coverage_summary['median']} vs target {target}")
    else:
        print(f"coverage: no project cleared the {crit['project_floor']} floor")
    print(f"-> {'CONFIRMATORY' if confirmatory else 'EXPLORATORY'}")
    for b in ([] if confirmatory else blockers(records)):
        print(f"   blocked on: {b}")
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.exit(main() or 0)
