"""The projection-regression check.

The flat GB/month threshold it replaced flagged dbt-core and directus, the two
deepest scans, as suspicious. They were not: GHArchive monthly tables grow by
more than an order of magnitude between 2011 and 2021, so a scan reaching back
to the epoch averages a completely different GB/month from one starting in
2019. The flat rate was measuring snapshot depth, not projection width.

Scan cost is a pure function of the bound year, so these tests pin the two
checks that follow from that: cohort consistency, and an absolute per-era
baseline.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "collect_gharchive", ROOT / "tools" / "collect_gharchive.py")
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)

GB = 1_000_000_000
# The operator's measured figures.
BOUND_2014, BOUND_2021 = 6.7 * GB, 170.4 * GB


def measured(**by_repo):
    return {repo: {"bound_year": year, "bytes": b, "history": 0, "window": 24}
            for repo, (year, b) in by_repo.items()}


@pytest.fixture(autouse=True)
def isolate_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "BASELINE_PATH", tmp_path / "baseline.json")


def seed(path, **years):
    """A baseline for the CURRENT query shape."""
    path.write_text(json.dumps({
        "query_shape": collect.query_shape(),
        "bytes_by_bound_year": {str(k): v for k, v in years.items()}}))


# --- the false positive that prompted this ----------------------------------

def test_the_deepest_scans_are_not_flagged_for_being_deep():
    """dbt-core and directus: 119 tables each, 170.4 GB each, both fine."""
    result = collect.report_projection_check(measured(
        **{"dbt-labs/dbt-core": (2021, BOUND_2021),
           "directus/directus": (2021, BOUND_2021),
           "puppetlabs/puppet": (2014, BOUND_2014),
           "mongodb/mongo": (2014, BOUND_2014)}))
    assert result == 0


def test_a_25x_spread_between_eras_is_normal():
    """6.7 GB against 170.4 GB is table growth, not a projection problem."""
    assert BOUND_2021 / BOUND_2014 > 25
    assert collect.report_projection_check(measured(
        **{"old/one": (2014, BOUND_2014), "new/one": (2021, BOUND_2021)})) == 0


# --- check 1: cohort consistency --------------------------------------------

def test_one_project_deviating_from_its_cohort_is_caught(capsys):
    """What a real projection regression looks like."""
    result = collect.report_projection_check(measured(
        **{"good/a": (2018, 80 * GB), "good/b": (2018, 80 * GB),
           "bad/c": (2018, 900 * GB)}))
    assert result == 1
    out = capsys.readouterr().out
    assert "bad/c" in out and "COHORT SPLIT" in out
    assert "good/a" not in out.split("PROJECTION CHECK FAILED")[1]


def test_cohort_tolerance_is_tight_because_the_table_set_is_identical():
    assert collect.COHORT_TOLERANCE <= 0.01
    # 2% apart in the same cohort is already a problem
    assert collect.report_projection_check(measured(
        **{"a/a": (2018, 100 * GB), "b/b": (2018, 102 * GB)})) == 1
    assert collect.report_projection_check(measured(
        **{"a/a": (2018, 100 * GB), "b/b": (2018, 100.5 * GB)})) == 0


def test_a_singleton_cohort_cannot_split():
    """mattermost is the only bound-2019 project; it needs the baseline instead."""
    assert collect.report_projection_check(
        measured(**{"mattermost/mattermost": (2019, 95 * GB)})) == 0


# --- check 2: absolute baseline ---------------------------------------------

def test_a_global_regression_is_caught_by_the_baseline(capsys):
    """Adding `payload` scales every cohort together, so cohorts stay consistent."""
    collect.BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    seed(collect.BASELINE_PATH, **{"2014": BOUND_2014, "2021": BOUND_2021})
    result = collect.report_projection_check(measured(
        **{"a/a": (2014, BOUND_2014 * 30), "b/b": (2021, BOUND_2021 * 30)}))
    assert result == 1
    out = capsys.readouterr().out
    assert "DRIFT" in out
    assert "immutable" in out          # the reason a cohort should never move


def test_matching_the_recorded_baseline_passes():
    collect.BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    seed(collect.BASELINE_PATH, **{"2014": BOUND_2014, "2021": BOUND_2021})
    assert collect.report_projection_check(measured(
        **{"a/a": (2014, BOUND_2014), "b/b": (2021, BOUND_2021)})) == 0


def test_an_unseen_bound_year_is_recorded_not_flagged(capsys):
    assert collect.report_projection_check(
        measured(**{"a/a": (2017, 48 * GB)})) == 0
    assert "new" in capsys.readouterr().out
    assert collect.load_baseline()[0][2017] == 48 * GB


def test_baseline_is_not_written_for_a_single_project_run():
    """--only must not overwrite the corpus baseline with one cohort."""
    collect.report_projection_check(measured(**{"a/a": (2017, 48 * GB)}),
                                    only="a/a")
    assert not collect.BASELINE_PATH.exists()


# --- the shipped seed --------------------------------------------------------

def test_the_committed_baseline_matches_the_current_query_shape():
    """Pins the shape, not the bytes.

    Byte counts legitimately change when the query changes — swapping repo.name
    for repo.id moved every cohort. What must hold is that the recorded numbers
    describe the query this code actually builds.
    """
    shipped = json.loads(
        (ROOT / "cache" / "gharchive" / "scan_baseline.json").read_text())
    assert shipped["query_shape"] == collect.query_shape()
    years = shipped["bytes_by_bound_year"]
    assert set(years) == {str(y) for y in range(2014, 2022)}
    # monotone in bound year: later snapshots scan strictly more tables
    ordered = [years[str(y)] for y in range(2014, 2022)]
    assert ordered == sorted(ordered)


# --- projects with no clone are never queried -------------------------------

class RecordingClient:
    """A BigQuery stand-in that records every query it is asked to run."""

    def __init__(self):
        self.queries = []

    def query(self, sql, job_config=None):
        self.queries.append(sql)
        return RecordingJob()


class RecordingJob:
    total_bytes_processed = 1_000_000_000
    total_bytes_billed = 1_000_000_000

    def result(self):
        return [{"issue_reporters_12mo": 1, "issue_reporters_prior_12mo": 1,
                 "non_contributor_issue_reporters_12mo": 1,
                 "non_contributor_issue_reporters_prior_12mo": 1,
                 "forks_12mo": 1, "pushers_to_date": 1}]


UNCLONEABLE = "sourcegraph/sourcegraph"


def test_uncloneable_project_is_absent_from_targets():
    assert not collect.features.clone_available(UNCLONEABLE)
    assert UNCLONEABLE not in [r for r, _ in collect.targets()]
    assert UNCLONEABLE in [r for r, _ in collect.skipped_targets()]


def test_uncloneable_project_never_generates_a_query(monkeypatch):
    """The assertion that matters: no query text mentions it, so nothing bills.

    sourcegraph/sourcegraph priced at 121.7 GB in the first full dry run. It
    has no clone, so it has no git features, so it is excluded from every run
    whatever GHArchive returns — collecting it buys nothing.
    """
    client = RecordingClient()
    monkeypatch.setattr(collect, "save_baseline", lambda *a, **k: None)
    collect.do_dry_run(client)

    assert client.queries, "the dry run should still price the other projects"
    for sql in client.queries:
        assert UNCLONEABLE not in sql
    assert f"repo.name = '{UNCLONEABLE}'" not in "\n".join(client.queries)


def test_uncloneable_project_is_not_collected_either(monkeypatch, tmp_path):
    client = RecordingClient()
    monkeypatch.setattr(collect.gharchive, "CACHE_ROOT", tmp_path)
    collect.do_collect(client, budget_tb=99)
    assert client.queries
    assert all(UNCLONEABLE not in sql for sql in client.queries)
    assert not list(tmp_path.glob("*sourcegraph*"))


def test_every_queried_project_has_a_clone():
    for repo, _ in collect.targets():
        assert collect.features.clone_available(repo), repo


def test_the_skip_is_reported_not_silent(capsys):
    collect.report_skipped()
    out = capsys.readouterr().out
    assert UNCLONEABLE in out
    assert "no clone" in out


def test_collector_and_scorer_agree_on_what_is_scoreable():
    """One predicate, so the two cannot drift apart.

    If the collector queried a project the scorer drops, that is money spent on
    a row that never appears; if it skipped one the scorer keeps, that project
    silently loses its adoption inputs. Both call
    features.clone_available, so the check is that neither has grown a second
    opinion — verified end to end on the boundary cases and by predicate for
    the rest.
    """
    collected = {r for r, _ in collect.targets()}
    skipped = {r for r, _ in collect.skipped_targets()}
    by_repo = {p["repo"]: p for p in collect.corpus_projects()}

    assert collected | skipped == set(by_repo)
    assert not (collected & skipped)

    # end to end through the real scorer, on one from each side
    for repo in sorted(skipped) + sorted(collected)[:1]:
        rec = collect.features.extract(by_repo[repo])
        assert (rec["clone_status"] == "ok") == (repo in collected), repo

    # and by predicate for the whole corpus
    for repo in by_repo:
        assert (repo in collected) == collect.features.clone_available(repo)


# --- the consistency halt ----------------------------------------------------

def test_consistency_flags_zero_events_against_a_repo_with_commits():
    from src import features as feat
    c = feat.gharchive_consistency({
        "total_commits_to_date": 12173,
        "gharchive": {"collected": True, "raw": {"pushers_to_date": 0}}})
    assert c["checked"] and c["ok"] is False
    assert "matched nothing" in c["problem"]


def test_consistency_passes_when_both_sources_see_activity():
    from src import features as feat
    c = feat.gharchive_consistency({
        "total_commits_to_date": 12173,
        "gharchive": {"collected": True, "raw": {"pushers_to_date": 14}}})
    assert c["ok"] is True and c["problem"] is None


def test_consistency_is_not_asserted_when_nothing_was_collected():
    """Uncollected is not the same as measured-zero."""
    from src import features as feat
    c = feat.gharchive_consistency({
        "total_commits_to_date": 12173, "gharchive": {"collected": False}})
    assert c["checked"] is False


def test_a_genuine_zero_reporter_count_is_not_flagged():
    """Kafka uses JIRA: zero IssuesEvent is real. Zero PUSHERS is not."""
    from src import features as feat
    c = feat.gharchive_consistency({
        "total_commits_to_date": 1856,
        "gharchive": {"collected": True, "raw": {"pushers_to_date": 2}}})
    assert c["ok"] is True


def test_the_run_halts_before_scoring_on_an_impossibility(tmp_path):
    """Not a warning. Non-zero exit, no run file, nothing scored.

    Builds a cache that is VALID for the current repo.id query — right hash,
    right shape — but carries the all-zero rows the repo.name collection
    produced. That is the dangerous case: the data parses, the zeros are legal,
    and the run would look complete.
    """
    import hashlib as _h
    import subprocess
    import sys as _sys
    import yaml as _yaml
    from src import features as feat, gharchive as gh

    corpus = _yaml.safe_load(
        (ROOT / "benchmarks" / "projects.yaml").read_text())["projects"]
    cache = tmp_path / "gharchive"
    cache.mkdir(parents=True)
    for p in corpus:
        if not feat.clone_available(p["repo"]):
            continue
        bound = feat.snapshot_bound(p["fund_snapshot"])
        query = gh.build_query(p["repo"], bound)
        gh.save_extract(gh.Extract(
            repo=p["repo"], snapshot=bound.isoformat(),
            rows={"issue_reporters_12mo": 0, "issue_reporters_prior_12mo": 0,
                  "non_contributor_issue_reporters_12mo": 0,
                  "non_contributor_issue_reporters_prior_12mo": 0,
                  "forks_12mo": 0,
                  "pushers_to_date": 0},          # the impossibility
            query=query,
            query_sha256=_h.sha256(query.encode()).hexdigest(),
            extracted_utc="2026-08-16T00:00:00+00:00"), root=cache)

    out = tmp_path / "run-halt-test.json"
    driver = tmp_path / "drive.py"
    driver.write_text(
        "import sys; from pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        f"sys.argv = ['run_scoring.py', '--out', {str(out)!r}]\n"
        "from src import gharchive\n"
        f"gharchive.CACHE_ROOT = Path({str(cache)!r})\n"
        "import runpy; runpy.run_path('run_scoring.py', run_name='__main__')\n")
    proc = subprocess.run([_sys.executable, str(driver)], cwd=str(ROOT),
                          capture_output=True, text=True)

    assert proc.returncode == 1, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "HALTED before scoring" in proc.stderr
    assert "No run file written" in proc.stderr
    assert not out.exists(), "a scored artefact was left on disk"
    # and it named the offenders rather than just failing
    assert "commits in clone, 0 pushers" in proc.stderr


def test_halt_logic_rejects_an_impossible_record():
    """The predicate the runner halts on, tested directly."""
    records = [
        {"repo": "ok/one", "features": {"gharchive": {"consistency": {"ok": True}}}},
        {"repo": "bad/two", "features": {"gharchive": {"consistency": {
            "ok": False, "commits_in_clone": 12173, "pushers_in_gharchive": 0}}}},
    ]
    impossible = [r for r in records
                  if ((r.get("features") or {}).get("gharchive") or {})
                  .get("consistency", {}).get("ok") is False]
    assert [r["repo"] for r in impossible] == ["bad/two"]


# --- baselines are per query shape ------------------------------------------

def test_shape_mismatch_is_not_reported_as_drift(capsys):
    """Swapping repo.name for repo.id moved every cohort 17-32%.

    That is a different query, not drifting data. Reporting it as DRIFT would
    fail every future run until someone hand-edited the baseline.
    """
    collect.BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    collect.BASELINE_PATH.write_text(json.dumps({
        "query_shape": "0000deadbeef0000",          # some older shape
        "bytes_by_bound_year": {"2014": BOUND_2014}}))
    result = collect.report_projection_check(
        measured(**{"a/a": (2014, BOUND_2014 * 0.825)}))     # -17.5%
    out = capsys.readouterr().out
    assert "BASELINE SHAPE MISMATCH" in out
    assert "DRIFT" not in out
    assert result == 0          # cohort check still passes, absolute skipped


def test_reseed_records_the_new_shape_and_archives_the_old():
    collect.BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    collect.BASELINE_PATH.write_text(json.dumps({
        "query_shape": "0000deadbeef0000",
        "recorded_utc": "2026-08-16T00:16:21+00:00",
        "bytes_by_bound_year": {"2014": BOUND_2014}}))
    collect.report_projection_check(
        measured(**{"a/a": (2014, BOUND_2014 * 0.825)}), reseed=True)

    values, shape = collect.load_baseline()
    assert shape == collect.query_shape()
    assert values[2014] == pytest.approx(BOUND_2014 * 0.825)
    archived = list(collect.BASELINE_PATH.parent.glob("*.superseded.json"))
    assert archived, "the repo.name-era values must be kept, not overwritten"
    assert json.loads(archived[0].read_text())["query_shape"] == "0000deadbeef0000"


def test_query_shape_tracks_the_filter_column(monkeypatch):
    before = collect.query_shape()
    monkeypatch.setattr(collect, "WINDOW_BLOCK",
                        collect.WINDOW_BLOCK.replace("repo.id", "repo.name"))
    assert collect.query_shape() != before


def test_query_shape_ignores_comments(monkeypatch):
    """Comments change nothing about what is scanned."""
    before = collect.query_shape()
    monkeypatch.setattr(collect.gharchive, "QUERY_TEMPLATE",
                        collect.gharchive.QUERY_TEMPLATE + "\n-- note\n")
    assert collect.query_shape() == before


def test_an_existing_baseline_value_is_not_silently_overwritten():
    """Recording is for NEW bound years. Replacing one needs --reseed-baseline."""
    collect.BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    seed(collect.BASELINE_PATH, **{"2014": BOUND_2014})
    collect.report_projection_check(
        measured(**{"a/a": (2014, BOUND_2014), "b/b": (2017, 48 * GB)}))
    values, _ = collect.load_baseline()
    assert values[2014] == BOUND_2014          # untouched
    assert values[2017] == 48 * GB             # new year merged in
