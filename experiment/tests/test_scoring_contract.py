"""Rubric 0.3.0 scoring rules, as tests rather than as intentions.

The rules most likely to be quietly undone by a later edit: the split into two
instruments, the missing-input rule (no weight redistribution), the coverage
definition (whole-rubric, not core-relative), and the surface table.
"""
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from src import gharchive, rubric as rubric_mod, score

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "runs" / "run-003.json"
CORPUS = yaml.safe_load((ROOT / "benchmarks" / "projects.yaml").read_text())


def base_record(gh=None, **over):
    rec = {
        "repo": "example/thing", "surface": "database",
        "entity_at_snapshot": "funded_company",
        "entity_at_fund_snapshot": "funded_company",
        "entity_at_fund_snapshot_source": "annotated_inline",
        "features": {
            "top1_commit_share": 0.4, "top3_commit_share": 0.7,
            "licence_family": "permissive",
            "readme": {"commercial_intent": {"score": 1.0},
                       "production_users": {"score": 1.0}},
            "gharchive": gh if gh is not None else {"collected": False,
                                                    "responsiveness": None},
        },
    }
    rec["features"].update(over.pop("features", {}))
    rec.update(over)
    return rec


FULL_GH = {
    "collected": True,
    "distinct_non_contributor_issue_reporters_12mo": 0.8,
    "issue_reporter_growth_yoy": 0.6,
    "fork_velocity_trailing_12mo": 0.7,
    "responsiveness": 0.9,
}


# --- the split --------------------------------------------------------------

def test_both_instruments_are_emitted_separately():
    result = score.score_project(base_record())
    assert "backtested_core" in result and "live_extension" in result
    assert result["backtested_core"]["instrument"] == "backtested_core"
    assert result["live_extension"]["calibration"] == "none — priors, never tested"


def test_no_blended_number_is_produced():
    result = score.score_project(base_record(gh=FULL_GH))
    assert result["blended_score"] is None
    # and nothing else in the payload is a combined total
    assert "total" not in result and "score" not in result


def test_core_and_live_partition_the_rubric_exactly():
    rubric = rubric_mod.load()
    spec = score.all_inputs(base_record(), rubric, "fund")
    weights = rubric_mod.dimension_weights(rubric)
    core = live = 0.0
    for dim_id, inputs in spec.items():
        assert sum(i.weight for i in inputs) == pytest.approx(1.0), dim_id
        core += sum(i.weight for i in inputs if i.instrument == score.CORE) * weights[dim_id]
        live += sum(i.weight for i in inputs if i.instrument == score.LIVE) * weights[dim_id]
    assert core + live == pytest.approx(100.0)
    assert core == pytest.approx(54.5)


def test_the_moved_gate4_signals_are_in_the_live_extension():
    rubric = rubric_mod.load()
    inputs = score.maintainer_inputs(base_record(), rubric, "fund")
    by_id = {i.id: i for i in inputs}
    for moved in ("entity_employs_primary_maintainers", "entity_controls_trademark"):
        assert by_id[moved].instrument == score.LIVE
    assert by_id["contributor_concentration"].instrument == score.CORE
    assert by_id["responsiveness"].instrument == score.CORE


def test_an_unlabelled_input_defaults_out_of_the_core():
    """Silence must not admit an input to a backtest."""
    rubric = json.loads(json.dumps(rubric_mod.load()))
    dim = rubric_mod.dimension(rubric, "maintainer_profile")
    for entry in dim["inputs_fund_mode"]:
        for block in entry.values():
            block.pop("instrument", None)
    assert all(i[3] == "live_extension"
               for i in rubric_mod.maintainer_inputs(rubric, "fund"))


# --- coverage ---------------------------------------------------------------

def test_coverage_is_whole_rubric_not_core_relative():
    """Redefining coverage as core-relative would move the pre-registered bar."""
    result = score.score_project(base_record(gh=FULL_GH))
    assert result["coverage_definition"].startswith("supplied input weight / 100")
    # every core input supplied, no live input: 54.5 of 100
    assert result["coverage"] == pytest.approx(0.545, abs=1e-3)
    # the core-relative diagnostic is 1.0 and is NOT what the criterion tests
    assert result["core_coverage"] == pytest.approx(1.0)
    assert result["coverage"] < result["core_coverage"]


def test_git_only_coverage_sits_below_the_project_floor():
    """The honest consequence of not collecting GHArchive."""
    crit = rubric_mod.coverage_criterion(rubric_mod.load())
    result = score.score_project(base_record())
    assert result["coverage"] == pytest.approx(0.29, abs=1e-3)
    assert result["coverage"] < crit["project_floor"]


def test_gharchive_lifts_coverage_over_the_preregistered_target():
    prereg = yaml.safe_load(
        (ROOT / "runs" / "run-002-preregistration.yaml").read_text())
    target = prereg["run_002_preregistration"]["target_coverage"]
    result = score.score_project(base_record(gh=FULL_GH))
    assert result["coverage"] >= target


def test_unresolved_licence_costs_coverage():
    a = score.score_project(base_record(gh=FULL_GH))
    b = score.score_project(base_record(gh=FULL_GH,
                                        features={"licence_family": "ambiguous"}))
    assert b["coverage"] < a["coverage"]


# --- missing-input rule -----------------------------------------------------

def test_dropped_dimension_lowers_the_ceiling_and_is_not_redistributed():
    core = score.score_project(base_record(gh=FULL_GH))["backtested_core"]
    assert "category_timing" in core["dimensions_not_in_instrument"]
    assert core["max_achievable"] == pytest.approx(54.5)


def test_an_instrument_with_no_data_reports_none_not_zero():
    live = score.score_project(base_record())["live_extension"]
    assert live["score"] is None
    assert live["max_achievable"] == 0


def test_within_dimension_renormalisation_still_spans_0_to_100():
    rec = base_record(gh={"collected": True,
                          "distinct_non_contributor_issue_reporters_12mo": 1.0,
                          "issue_reporter_growth_yoy": 1.0,
                          "fork_velocity_trailing_12mo": 1.0,
                          "responsiveness": 1.0})
    dims = {d["id"]: d
            for d in score.score_project(rec)["backtested_core"]["dimensions"]}
    assert dims["adoption_without_capture"]["subscore"] == 100.0
    assert dims["adoption_without_capture"]["evidence_coverage"] == 1.0


def test_adoption_friction_applies_to_the_core_subscore_before_weighting():
    rec = base_record(gh=FULL_GH, features={"licence_family": "copyleft"})
    dims = {d["id"]: d
            for d in score.score_project(rec)["backtested_core"]["dimensions"]}
    friction = dims["adoption_without_capture"]["adoption_friction"]
    assert friction["scale"] == "dimension_subscore_pre_weight"
    assert friction["applied"] == -15      # database is infrastructure


# --- surface table ----------------------------------------------------------

def test_every_corpus_surface_has_a_managed_service_score():
    rubric_mod.validate_surface_coverage(
        rubric_mod.load(), {p["surface"] for p in CORPUS["projects"]})


def test_unmapped_surface_is_a_hard_error_not_a_default():
    with pytest.raises(rubric_mod.RubricError):
        rubric_mod.validate_surface_coverage(rubric_mod.load(), {"quantum_toaster"})
    with pytest.raises(rubric_mod.RubricError):
        score.substitution_inputs(base_record(surface="quantum_toaster"),
                                  rubric_mod.load())


# --- concentration ----------------------------------------------------------

def test_fund_mode_treats_concentration_as_key_man_risk():
    low = score.score_project(base_record(gh=FULL_GH,
                                          features={"top1_commit_share": 0.1}))
    high = score.score_project(base_record(gh=FULL_GH,
                                           features={"top1_commit_share": 0.9}))
    assert low["backtested_core"]["score"] > high["backtested_core"]["score"]


def test_concentration_uses_top1_not_top3():
    a = base_record(gh=FULL_GH,
                    features={"top1_commit_share": 0.2, "top3_commit_share": 0.99})
    b = base_record(gh=FULL_GH,
                    features={"top1_commit_share": 0.2, "top3_commit_share": 0.25})
    assert (score.score_project(a)["backtested_core"]["score"]
            == score.score_project(b)["backtested_core"]["score"])


# --- run file ---------------------------------------------------------------

@pytest.mark.skipif(not RUN.exists(), reason="run-003 not built")
def test_run_file_never_emits_a_bare_score():
    run = json.loads(RUN.read_text())
    for project in run["projects"]:
        s = project.get("score")
        if s is None:
            continue
        assert "coverage" in s
        for part in ("backtested_core", "live_extension"):
            assert {"score", "max_achievable"} <= set(s[part])
        assert s["blended_score"] is None


@pytest.mark.skipif(not RUN.exists(), reason="run-003 not built")
def test_run_applies_the_coverage_criterion_mechanically():
    run = json.loads(RUN.read_text())
    crit = run["coverage_criterion"]
    excluded = {e["repo"] for e in run["exclusions"]}
    for project in run["projects"]:
        s = project.get("score")
        below = s is None or s["coverage"] < crit["project_floor"]
        assert (project["repo"] in excluded) == below, project["repo"]
    assert run["exclusion_budget_exceeded"] == (
        run["n_excluded"] > crit["max_exclusions"])
    if run["exclusion_budget_exceeded"] or not run["meets_target_coverage"]:
        assert run["qualifies_as_confirmatory"] is False
        assert run["status"] == "exploratory"


@pytest.mark.skipif(not RUN.exists(), reason="run-003 not built")
def test_every_exclusion_is_named_with_a_reason():
    run = json.loads(RUN.read_text())
    for exclusion in run["exclusions"]:
        assert exclusion["repo"] and exclusion["reason"]


# --- end-to-end with a populated cache --------------------------------------

def test_a_populated_cache_makes_the_run_confirmatory(tmp_path, monkeypatch):
    """Proves the plumbing without pretending the data was collected.

    Writes a fixture cache for every collectable corpus project, scores through
    the real feature path, and checks the coverage criterion clears. The values
    are arbitrary; only the availability of the inputs matters here.
    """
    from src import features as feat
    monkeypatch.setattr(gharchive, "CACHE_ROOT", tmp_path)

    collectable = [p for p in CORPUS["projects"]
                   if feat.clone_available(p["repo"])]
    for p in collectable:
        bound = feat.snapshot_bound(p["fund_snapshot"])
        query = gharchive.build_query(p["repo"], bound)
        gharchive.save_extract(gharchive.Extract(
            repo=p["repo"], snapshot=bound.isoformat(),
            rows={"issue_reporters_12mo": 120,
                  "issue_reporters_prior_12mo": 90,
                  "non_contributor_issue_reporters_12mo": 95,
                  "non_contributor_issue_reporters_prior_12mo": 70,
                  "forks_12mo": 250, "pushers_to_date": 30},
            query=query,
            query_sha256=hashlib.sha256(query.encode()).hexdigest(),
            extracted_utc="2026-08-15T00:00:00+00:00"), root=tmp_path)

    crit = rubric_mod.coverage_criterion(rubric_mod.load())
    prereg = yaml.safe_load(
        (ROOT / "runs" / "run-002-preregistration.yaml").read_text())
    target = prereg["run_002_preregistration"]["target_coverage"]

    coverages = []
    for p in collectable:
        bound = feat.snapshot_bound(p["fund_snapshot"])
        gh = gharchive.features_from(
            gharchive.load_extract(p["repo"], bound, root=tmp_path), bound)
        gh["responsiveness"] = 0.5      # same collection step, not in the query
        coverages.append(score.score_project(
            base_record(gh=gh, surface=p["surface"]))["coverage"])

    excluded = len(CORPUS["projects"]) - len(collectable)
    excluded += sum(1 for c in coverages if c < crit["project_floor"])
    included = sorted(c for c in coverages if c >= crit["project_floor"])
    assert excluded <= crit["max_exclusions"]
    assert included[len(included) // 2] >= target
