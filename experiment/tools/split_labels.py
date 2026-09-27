#!/usr/bin/env python3
"""One-off: merge annotations.yaml into labels.yaml and split the result into
benchmarks/projects.yaml (inputs, readable by src/) and benchmarks/outcomes.yaml
(labels, readable only by evaluate.py).

Run from the repo root. Idempotent — reads the superseded originals if the flat
files have already been moved.

Two things this script deliberately makes explicit rather than smoothing over:

1. annotations.yaml keys `surface` / `entity_at_snapshot` to its own `snapshot`
   field, which equals `form_snapshot` for all 34 entries it covers. The 8
   entries added later carry the same two fields inline alongside BOTH a
   form_snapshot and a fund_snapshot, and their values describe the fund
   snapshot (e.g. gitlabhq is `funded_company` although GitLab Inc did not
   exist at its 2013 form snapshot). The merged record therefore records
   `entity_at_snapshot_asof` so downstream code knows which date the value
   describes. Nothing is rewritten.

2. Scope. Only entries with `outcome_resolved: true` reach projects.yaml, so
   src/ never has to consult a resolution field to know what is in the corpus.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmarks"
SUPERSEDED = BENCH / "_superseded"

PROJECT_FIELDS = ["repo", "form_snapshot", "fund_snapshot", "surface",
                  "entity_at_snapshot", "entity_at_fund_snapshot",
                  "entity_at_fund_snapshot_source"]

# Resolved in the post-run-001 decision memo (2026-08-15). Both were annotated
# `none` at their FORM snapshot and scored at a fund snapshot years later, which
# made them the only two projects the date mismatch actually bit.
DECISION_MEMO_FUND_ENTITY = {
    "grafana/grafana": "funded_company",    # Grafana Labs, Series A 2017
    "getsentry/sentry": "funded_company",   # Sentry, funded from 2016
}
OUTCOME_FIELDS = ["repo", "label", "resolution", "outcome_resolved", "outcome",
                  "company", "note", "label_provisional", "fund_mode",
                  "expected_rubric_output", "actual_outcome"]


def _find(name):
    for cand in (ROOT / name, BENCH / name, SUPERSEDED / name):
        if cand.exists():
            return cand
    sys.exit(f"cannot find {name}")


def _load(name):
    """The flat files carry a « path » banner on line 1 that is not YAML."""
    text = _find(name).read_text()
    if text.lstrip().startswith("«"):
        text = text.split("\n", 1)[1]
    return yaml.safe_load(text)


def main():
    labels = _load("labels.yaml")
    annotations = _load("annotations.yaml")

    ann_by_repo = {a["repo"]: a for a in annotations}
    projects = labels["projects"]

    # --- merge + verify -----------------------------------------------------
    missing, conflicts = [], []
    for p in projects:
        repo = p["repo"]
        ann = ann_by_repo.get(repo)
        if ann:
            for field in ("surface", "entity_at_snapshot"):
                if field in p and p[field] != ann[field]:
                    conflicts.append((repo, field, p[field], ann[field]))
                p[field] = p.get(field, ann[field])
            if ann["snapshot"] != p.get("form_snapshot"):
                conflicts.append((repo, "snapshot", p.get("form_snapshot"),
                                  ann["snapshot"]))
            # labels.yaml demoted the 2019-2021 cohort to `unresolved` and kept
            # the prior label in label_provisional; annotations.yaml still holds
            # the stale one. That disagreement is expected and documented in
            # labels.yaml:labelling_rule. Any other disagreement is not.
            stale_ok = (p["label"] == "unresolved"
                        and p.get("label_provisional") == ann["label"])
            if ann["label"] != p["label"] and not stale_ok:
                conflicts.append((repo, "label", p["label"], ann["label"]))
            p["_asof"] = "form_snapshot"
        else:
            p["_asof"] = "fund_snapshot"
        for field in ("surface", "entity_at_snapshot"):
            if p.get(field) in (None, ""):
                missing.append((repo, field))

    unannotated = set(ann_by_repo) - {p["repo"] for p in projects}

    print(f"labels.yaml projects      : {len(projects)}")
    print(f"annotations.yaml entries  : {len(annotations)}")
    print(f"annotation-only repos     : {sorted(unannotated) or 'none'}")
    print(f"missing surface/entity    : {missing or 'none'}")
    print(f"value conflicts           : {conflicts or 'none'}")
    if missing or conflicts or unannotated:
        sys.exit("MERGE FAILED — resolve the above before splitting")

    # --- scope --------------------------------------------------------------
    in_scope = [p for p in projects if p.get("outcome_resolved") is True]
    print(f"in scope (resolved)       : {len(in_scope)}")

    for p in in_scope:
        if p.get("fund_snapshot") is None:
            sys.exit(f"{p['repo']}: in scope but no fund_snapshot")

    # --- entity, split by the date each annotation actually describes --------
    # entity_at_snapshot means FORM time, always. entity_at_fund_snapshot is a
    # separate field so the two can never be confused again.
    for p in in_scope:
        repo = p["repo"]
        if repo in DECISION_MEMO_FUND_ENTITY:
            p["entity_at_fund_snapshot"] = DECISION_MEMO_FUND_ENTITY[repo]
            p["entity_at_fund_snapshot_source"] = "decision_memo_2026_08_15"
        elif p["_asof"] == "fund_snapshot":
            # the 8 later entries: their inline value describes the fund
            # snapshot, so it moves across and form time becomes unknown rather
            # than being back-dated onto a year nobody annotated.
            p["entity_at_fund_snapshot"] = p["entity_at_snapshot"]
            p["entity_at_fund_snapshot_source"] = "annotated_inline"
            p["entity_at_snapshot"] = "unknown"
        else:
            # form-time annotation, no fund-time annotation. An entity present
            # at form time is still present years later, so presence carries
            # forward safely; the TYPE may be understated (a consultancy that
            # since raised), which the gate flags.
            p["entity_at_fund_snapshot"] = p["entity_at_snapshot"]
            p["entity_at_fund_snapshot_source"] = "carried_forward_from_form_snapshot"

    unknown_fund = [p["repo"] for p in in_scope
                    if p["entity_at_fund_snapshot"] in (None, "", "none", "unknown")]
    print(f"entity_at_fund_snapshot   : "
          f"{len(in_scope) - len(unknown_fund)}/{len(in_scope)} resolved"
          f"{'  UNRESOLVED: ' + str(unknown_fund) if unknown_fund else ''}")

    # --- write --------------------------------------------------------------
    proj_out = [{k: p[k] for k in PROJECT_FIELDS if k in p} for p in in_scope]
    outcome_out = [{k: p[k] for k in OUTCOME_FIELDS if k in p} for p in projects]

    # round-trip check: every key of every source record lands somewhere
    keep = set(PROJECT_FIELDS) | set(OUTCOME_FIELDS) | {"_asof"}
    dropped = {k for p in projects for k in p} - keep
    if dropped:
        sys.exit(f"fields would be dropped: {sorted(dropped)}")

    (BENCH / "projects.yaml").write_text(
        "# oregrade — scoring inputs. Contains NO outcome information.\n"
        "# The 25 projects whose outcome resolved; src/ reads this file only.\n"
        "#\n"
        "# entity_at_snapshot        entity at form_snapshot ('unknown' where\n"
        "#                           form time was never annotated)\n"
        "# entity_at_fund_snapshot   entity at fund_snapshot — the field fund\n"
        "#                           mode actually scores against\n"
        "# ...._source               decision_memo | annotated_inline |\n"
        "#                           carried_forward_from_form_snapshot\n"
        "# See tools/split_labels.py.\n"
        + yaml.safe_dump({"projects": proj_out}, sort_keys=False, width=100))

    meta = {k: v for k, v in labels.items() if k != "projects"}
    (BENCH / "outcomes.yaml").write_text(
        "# oregrade — resolved labels. NOTHING UNDER src/ MAY READ THIS FILE.\n"
        "# All 42 benchmark entries, including unresolved and never-fund-mode ones.\n"
        + yaml.safe_dump({**meta, "projects": outcome_out}, sort_keys=False,
                         width=100))

    print(f"wrote benchmarks/projects.yaml ({len(proj_out)} projects)")
    print(f"wrote benchmarks/outcomes.yaml ({len(outcome_out)} entries)")


if __name__ == "__main__":
    main()
