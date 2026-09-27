"""Gate behaviour under rubric 0.2.0, and the entity date fix."""
from pathlib import Path

import yaml

from src import gates

ROOT = Path(__file__).resolve().parent.parent
CORPUS = {p["repo"]: p
          for p in yaml.safe_load(
              (ROOT / "benchmarks" / "projects.yaml").read_text())["projects"]}


def record(**over):
    rec = {
        "repo": "example/thing", "surface": "server",
        "form_snapshot": 2014, "fund_snapshot": 2017,
        "entity_at_snapshot": "none",
        "entity_at_fund_snapshot": "funded_company",
        "entity_at_fund_snapshot_source": "annotated_inline",
        "clone_status": "ok",
        "features": {
            "licence": {"spdx": "Apache-2.0", "side_licences": []},
            "licence_family": "permissive",
            "licence_changed_within_24mo": False, "licence_24mo_ago": "Apache-2.0",
            "commits_trailing_90d": 200, "commits_prior_90d": 180,
            "authors_trailing_90d": 20, "days_since_last_commit": 1,
        },
    }
    rec["features"].update(over.pop("features", {}))
    rec.update(over)
    return rec


def gate4(rec):
    return gates.gate4_commercialisation(rec, rec["features"])


# --- the data fix -----------------------------------------------------------

def test_projects_yaml_separates_the_two_entity_dates():
    for repo, p in CORPUS.items():
        assert "entity_at_fund_snapshot" in p, repo
        assert "entity_at_fund_snapshot_source" in p, repo
        assert "entity_at_snapshot_asof" not in p, f"{repo}: superseded field"


def test_every_in_scope_project_has_a_resolved_fund_entity():
    unresolved = [r for r, p in CORPUS.items()
                  if p["entity_at_fund_snapshot"] in (None, "", "none", "unknown")]
    assert not unresolved, unresolved


def test_grafana_and_sentry_are_resolved_from_the_decision_memo():
    """Both were `none` at form time and are the two the date mismatch bit."""
    for repo in ("grafana/grafana", "getsentry/sentry"):
        p = CORPUS[repo]
        assert p["entity_at_snapshot"] == "none"          # form time, unchanged
        assert p["entity_at_fund_snapshot"] == "funded_company"
        assert p["entity_at_fund_snapshot_source"] == "decision_memo_2026_08_15"
        result = gate4({**record(), **p, "features": record()["features"]})
        assert result["verdict"] == "pass"
        assert "entity_type_at_snapshot_unknown" not in result["flags"]


def test_inline_annotated_entries_no_longer_claim_to_describe_form_time():
    p = CORPUS["gitlabhq/gitlabhq"]
    assert p["entity_at_fund_snapshot_source"] == "annotated_inline"
    assert p["entity_at_snapshot"] == "unknown"
    assert p["entity_at_fund_snapshot"] == "funded_company"


# --- gate 4 -----------------------------------------------------------------

def test_gate4_fails_only_on_absence_of_an_entity():
    assert gate4(record(entity_at_fund_snapshot="none"))["verdict"] == "fail"
    assert gate4(record(entity_at_fund_snapshot=None))["verdict"] == "fail"
    assert gate4(record())["verdict"] == "pass"


def test_gate4_no_longer_carries_unreachable_control_conditions():
    result = gate4(record())
    assert result["moved_to_scoring"] == ["entity_controls_trademark",
                                          "entity_employs_primary_maintainers"]
    joined = " ".join(result["unevaluated"])
    assert "trademark" not in joined
    assert "employs" not in joined
    assert "adoption_captured_by_third_party" in joined


def test_unknown_entity_type_still_flags():
    """Mechanism retained for benchmark entries added without a fund annotation."""
    result = gate4(record(entity_at_fund_snapshot="present_type_unrecorded"))
    assert result["verdict"] == "pass"
    assert "entity_type_at_snapshot_unknown" in result["flags"]


def test_carried_forward_type_below_funded_company_is_flagged():
    result = gate4(record(entity_at_fund_snapshot="small_consultancy",
                          entity_at_fund_snapshot_source=
                          "carried_forward_from_form_snapshot"))
    assert "entity_type_carried_forward_may_understate" in result["flags"]


def test_carried_forward_funded_company_is_not_flagged():
    result = gate4(record(entity_at_fund_snapshot_source=
                          "carried_forward_from_form_snapshot"))
    assert "entity_type_carried_forward_may_understate" not in result["flags"]


# --- gate 3 vocabulary ------------------------------------------------------

def test_gate3_speaks_the_annotation_vocabulary():
    for surface in ("cli_stateful", "object_store", "framework_hosted"):
        assert gates.gate3_surface(record(surface=surface),
                                   {})["verdict"] == "pass"
    assert gates.gate3_surface(record(surface="library"), {})["verdict"] == "fail"
    flagged = gates.gate3_surface(record(surface="desktop_app"), {})
    assert flagged["verdict"] == "pass" and flagged["flags"]


def test_every_corpus_surface_is_recognised_by_gate3():
    for repo, p in CORPUS.items():
        result = gates.gate3_surface({"surface": p["surface"]}, {})
        assert result["verdict"] == "pass", f"{repo}: {p['surface']}"


# --- gate 1 -----------------------------------------------------------------

def test_gate1_fails_only_on_no_licence_in_fund_mode():
    result = gates.gate1_licence(
        record(features={"licence": {"spdx": "none", "side_licences": []},
                         "licence_family": "none"}),
        record(features={"licence": {"spdx": "none", "side_licences": []},
                         "licence_family": "none"})["features"])
    assert result["verdict"] == "fail"
    assert gates.gate1_licence(record(), record()["features"])["verdict"] == "pass"


def test_agpl_is_not_a_gate1_fail():
    rec = record(features={"licence": {"spdx": "AGPL-3.0", "side_licences": []},
                           "licence_family": "copyleft"})
    result = gates.gate1_licence(rec, rec["features"])
    assert result["verdict"] == "pass"
    assert "copyleft_or_source_available_position" in result["flags"]
