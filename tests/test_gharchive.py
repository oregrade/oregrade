"""GHArchive adoption collection: query construction, cache, honest absence.

BigQuery is never called from a test. The contract is that scoring reads the
cache and only the cache, so the cache is what these exercise.

Every non-obvious rule here was a bug first. The comments say which.
"""
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from oregrade import gharchive

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ID = 12345


def extract_for(repo="a/b", bound=date(2018, 1, 1), repo_id=FIXTURE_ID, **rows):
    base = {"issue_reporters_12mo": 100, "issue_reporters_prior_12mo": 60,
            "non_contributor_issue_reporters_12mo": 80,
            "non_contributor_issue_reporters_prior_12mo": 50,
            "forks_12mo": 300, "pushers_to_date": 40}
    base.update(rows)
    query = gharchive.build_query(repo, bound, repo_id)
    return gharchive.Extract(
        repo=repo, snapshot=bound.isoformat(), rows=base, query=query,
        query_sha256=hashlib.sha256(query.encode()).hexdigest(),
        extracted_utc="2026-08-16T00:00:00+00:00", bytes_billed=42_000_000_000)


def executable_sql(query: str) -> str:
    """The query minus comments — the header names repo.name deliberately."""
    return "\n".join(line for line in query.splitlines()
                     if not line.lstrip().startswith("--"))


# --- identity: the bug that cost a whole collection --------------------------

def test_the_filter_is_repo_id_never_repo_name():
    """GHArchive stores the name as of the event; only the id is invariant.

    Filtering on the current name made eight of twenty-four corpus projects
    match zero events, because each had been renamed since its snapshot.
    """
    sql = executable_sql(gharchive.build_query("a/b", date(2018, 1, 1), 999))
    assert "repo.id = 999" in sql
    assert "repo.name" not in sql


def test_repo_id_is_required_not_defaulted():
    with pytest.raises(TypeError):
        gharchive.build_query("a/b", date(2018, 1, 1))


def test_changing_the_repo_id_changes_the_cache_key():
    a = gharchive.cache_key("a/b", date(2018, 1, 1), 1)
    b = gharchive.cache_key("a/b", date(2018, 1, 1), 2)
    assert a != b


# --- query shape --------------------------------------------------------------

def test_query_is_deterministic():
    args = ("grafana/grafana", date(2018, 1, 1), 15111821)
    assert gharchive.build_query(*args) == gharchive.build_query(*args)


def test_no_wildcard_anywhere():
    """`githubarchive.day` holds the views `today` and `yesterday`, and
    BigQuery refuses to resolve a prefix that matches a view at all. A bounded
    _TABLE_SUFFIX does not help: prefix resolution happens first."""
    sql = executable_sql(gharchive.build_query("a/b", date(2018, 1, 1), FIXTURE_ID))
    assert "githubarchive.day" not in sql
    assert ".*`" not in sql and "_TABLE_SUFFIX" not in sql
    assert "yesterday" not in sql and "today" not in sql


def test_tables_are_enumerated_at_month_granularity():
    bound = date(2018, 1, 1)
    q = gharchive.build_query("a/b", bound, FIXTURE_ID)
    months = gharchive.months_for(bound)
    for month in months:
        assert f"`githubarchive.month.{month}`" in q, month


def test_windows_land_on_calendar_years():
    """Exact year arithmetic, so no leap-year drift between vintages."""
    w = gharchive.windows(date(2018, 1, 1))
    assert w["t_start"] == date(2017, 1, 1) and w["p_start"] == date(2016, 1, 1)
    w = gharchive.windows(date(2017, 1, 1))          # spans a leap year
    assert w["t_start"] == date(2016, 1, 1) and w["p_start"] == date(2015, 1, 1)


def test_payload_is_never_read():
    """Reading `payload` takes a month from ~0.4 GB scanned to tens of GB."""
    sql = executable_sql(gharchive.build_query("a/b", date(2018, 1, 1), FIXTURE_ID))
    assert "payload" not in sql.lower()


def test_history_scan_drops_created_at():
    """History months exist only to build the committer set, and every event
    in them precedes the bound, so the timestamp is never read."""
    sql = executable_sql(gharchive.build_query("a/b", date(2018, 1, 1), FIXTURE_ID))
    history = sql.split("history_pushers AS (")[1].split(
        "pushers_before_snapshot AS (")[0]
    assert "created_at" not in history
    assert "repo.id" in history and "type = 'PushEvent'" in history


def test_committer_set_spans_all_history_not_just_the_window():
    """Bounding it to the window would classify someone who pushed in 2012 and
    stopped as a non-contributing USER in 2017."""
    months = gharchive.months_for(date(2018, 1, 1))
    assert months[0] == "201102" and months[-1] == "201712"
    sql = gharchive.build_query("a/b", date(2018, 1, 1), FIXTURE_ID)
    assert "type = 'PushEvent' AND created_at < TIMESTAMP('2018-01-01')" in sql


def test_identity_space_is_login_never_email():
    sql = executable_sql(gharchive.build_query("a/b", date(2018, 1, 1), FIXTURE_ID))
    assert "actor.login" in sql and "email" not in sql.lower()


# --- cache ---------------------------------------------------------------------

def test_round_trip(tmp_path):
    gharchive.save_extract(extract_for(), FIXTURE_ID, root=tmp_path)
    loaded = gharchive.load_extract("a/b", date(2018, 1, 1), FIXTURE_ID,
                                    root=tmp_path)
    assert loaded.rows["forks_12mo"] == 300
    assert loaded.bytes_billed == 42_000_000_000


def test_missing_cache_entry_is_not_an_error(tmp_path):
    assert gharchive.load_extract("nope/nope", date(2018, 1, 1), FIXTURE_ID,
                                  root=tmp_path) is None


def test_stale_cache_is_rejected_rather_than_reused(tmp_path):
    extract = extract_for()
    path = gharchive.save_extract(extract, FIXTURE_ID, root=tmp_path)
    path.write_text(path.read_text().replace(extract.query_sha256, "0" * 64))
    with pytest.raises(gharchive.GHArchiveUnavailable):
        gharchive.load_extract("a/b", date(2018, 1, 1), FIXTURE_ID, root=tmp_path)


def test_cache_write_is_atomic(tmp_path):
    path = gharchive.save_extract(extract_for(), FIXTURE_ID, root=tmp_path)
    assert path.exists() and not list(tmp_path.glob("*.partial"))


def test_cache_stores_raw_counts_only(tmp_path):
    """Nothing derived is cached, so a change of interpretation is a re-read
    rather than a re-query. BigQuery is metered."""
    path = gharchive.save_extract(extract_for(forks_12mo=300), FIXTURE_ID,
                                  root=tmp_path)
    rows = json.loads(path.read_text())["rows"]
    assert rows["forks_12mo"] == 300
    assert all(isinstance(v, int) for v in rows.values())


# --- facts ----------------------------------------------------------------------

def test_absent_cache_yields_unavailable_never_zeros():
    """The distinction that cost a full collection: a zero that means 'not
    collected' reads as absence of adoption and is simply wrong."""
    facts = gharchive.facts_from(None, date(2018, 1, 1))
    assert facts["status"] == "unavailable"
    for key in ("distinct_issue_reporters_12mo", "forks_12mo", "pushers_to_date"):
        assert facts[key] is None


def test_raw_counts_pass_through_unmodified():
    facts = gharchive.facts_from(extract_for(), date(2018, 1, 1))
    assert facts["status"] == "collected"
    assert facts["distinct_issue_reporters_12mo"] == 100
    assert facts["forks_12mo"] == 300


def test_no_normalisation_or_score_is_emitted():
    """The 0-1 scaling and its two arbitrary caps were scoring machinery."""
    facts = gharchive.facts_from(extract_for(), date(2018, 1, 1))
    assert not hasattr(gharchive, "REPORTER_CAP")
    assert not any("score" in k or "normalis" in k for k in facts)


def test_issues_tracked_off_github_is_flagged_not_reported_as_zero_adoption():
    """MongoDB at 2013 used JIRA. Pushes exist, IssuesEvent does not."""
    facts = gharchive.facts_from(
        extract_for(issue_reporters_12mo=0, issue_reporters_prior_12mo=0,
                    non_contributor_issue_reporters_12mo=0, pushers_to_date=46),
        date(2014, 1, 1))
    assert facts["caveat"] == "off_github_detected"
    assert "measure GitHub, not adoption" in facts["caveat_detail"]


def test_a_repo_with_issues_on_github_gets_the_plain_caveat():
    facts = gharchive.facts_from(extract_for(), date(2018, 1, 1))
    assert facts["caveat"] == "issue_tracker_on_github"


def test_zero_events_of_any_kind_is_flagged_as_a_lookup_problem():
    facts = gharchive.facts_from(extract_for(pushers_to_date=0), date(2018, 1, 1))
    assert facts["caveat"] == "no_events_found"
    assert "check the id" in facts["caveat_detail"]


def test_consistency_guard_catches_the_impossible_case():
    facts = gharchive.facts_from(extract_for(pushers_to_date=0),
                                 date(2018, 1, 1), commits_in_clone=12173)
    assert facts["consistency"]["ok"] is False
    assert "matched nothing" in facts["consistency"]["problem"]


def test_consistency_guard_passes_when_both_sources_agree():
    facts = gharchive.facts_from(extract_for(), date(2018, 1, 1),
                                 commits_in_clone=12173)
    assert facts["consistency"]["ok"] is True


def test_provenance_travels_with_the_facts():
    facts = gharchive.facts_from(extract_for(), date(2018, 1, 1))
    prov = facts["provenance"]
    assert prov["query_sha256"] and prov["extracted_utc"]
    assert prov["bytes_billed"] == 42_000_000_000
