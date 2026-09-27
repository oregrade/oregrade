"""Point-in-time adoption signals from GHArchive.

GHArchive is the GitHub public event firehose, one row per event from
2011-02-12, mirrored as the BigQuery public dataset `githubarchive`. Unlike the
REST API it is an immutable log: an issue opened in 2014 by an account that has
since been deleted is still there, and nothing that happened after a snapshot
can alter what it says about the snapshot. That property is why it is the only
external source this project will accept in a backtest.

WHAT THIS ANSWERS

A person who files a bug and never commits is a USER. That is the adoption
signal the README-based `production_mentions` was reaching for and failing to
find: 21 of 24 projects had no "Who's using this" section at their snapshot,
because the convention postdates those years. The signal was absent from the
README, not absent from history.

  distinct_non_contributor_issue_reporters_12mo
  issue_reporter_growth_yoy
  fork_velocity_trailing_12mo

NON-CONTRIBUTOR MEANS "HAD NOT PUSHED AS OF THE SNAPSHOT"

Not "never pushed at any point". The second form looks past the snapshot: a
person who first commits two years later would be reclassified as a contributor
using information that did not exist at the scoring date, which would
systematically shrink the user count for precisely the projects that went on to
grow a contributor base. The cutoff is the snapshot and nothing after it is
consulted.

Both sides of that set difference use the GHArchive `actor.login`. The committer
set comes from PushEvent actors before the snapshot rather than from git commit
emails, so there is no login-to-email join to get wrong.

THE SOURCE ASYMMETRY

Everything else in this project comes from a local clone and can be recomputed
from cold with git alone. This comes from a metered external service. So:
queries are cached to disk, the cache is the only thing scoring reads, and each
cache entry carries the exact SQL, its hash, the extraction timestamp and the
bytes billed. A backtest that cannot be re-run from cold is not a backtest —
`tools/collect_gharchive.py --verify` re-derives every cache key and fails on
drift.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

DEFAULT_CACHE_ROOT = Path(
    os.environ.get("OREGRADE_CACHE",
                   Path.home() / ".cache" / "oregrade")) / "gharchive"
CACHE_ROOT = DEFAULT_CACHE_ROOT

# GHArchive begins here. A trailing-12mo window is complete for any snapshot
# from 2012-02-12; a prior-12mo window (for the year-on-year ratio) is complete
# from 2013-02-12. Earlier windows are truncated and the record says so rather
# than silently reporting a smaller count.
GHARCHIVE_EPOCH = date(2011, 2, 12)

class GHArchiveUnavailable(RuntimeError):
    """No cache entry, and no credentials to build one."""


# ---------------------------------------------------------------------------
# query construction — the cache key is derived from this text, so any edit
# invalidates the cache rather than silently reusing rows from a different query
# ---------------------------------------------------------------------------

EVENT_TYPES = ("PushEvent", "IssuesEvent", "ForkEvent")

# Two projections, because the two scans need different things.
#
# WINDOW months — the two calendar years the issue and fork windows cover —
# need the timestamp to place each event in the trailing or prior year.
#
# HISTORY months — everything from the GHArchive epoch up to those two years —
# exist only to build the committer set. Every event in them is before the
# snapshot by construction, so `created_at` is never read and is dropped. That
# leaves repo.name and type in the WHERE and actor.login in the SELECT: three
# columns instead of four, across the majority of the tables scanned.
#
# `payload` appears in neither. It is the enormous column in every GHArchive
# table and reading it takes a month from ~0.4 GB scanned to tens of GB. A dry
# run pricing much above ~0.4 GB per window-month means it has leaked back in.
WINDOW_PROJECTION = "type, actor.login AS login, created_at"
HISTORY_PROJECTION = "actor.login AS login"

# Filtered by repo.id, NEVER by repo.name.
#
# GHArchive stores the repo name as it was AT THE TIME OF THE EVENT. The first
# collection filtered on the current name and eight of twenty-four projects
# matched zero events — elasticsearch, redis, chef, etcd, mattermost, dbt-core,
# owncloud and rethinkdb, each with five figures of commits in its clone. They
# had all been renamed between their snapshot and today, so their history sits
# under names the query never asked for.
#
# The failure was silent and, worse, systematically biased: renames cluster
# around commercialisation (opscode -> chef, fishtown-analytics -> dbt-labs,
# coreos -> etcd-io), so the missing rows were not missing at random.
#
# repo.id is permanent across renames. Ids are resolved once by
# tools/resolve_repo_ids.py and committed to benchmarks/repo_ids.yaml.
WINDOW_BLOCK = """\
  SELECT {projection}
  FROM `githubarchive.month.{month}`
  WHERE repo.id = {repo_id}
    AND type IN ({types})"""

HISTORY_BLOCK = """\
  SELECT DISTINCT {projection}
  FROM `githubarchive.month.{month}`
  WHERE repo.id = {repo_id}
    AND type = 'PushEvent'"""

QUERY_TEMPLATE = """\
-- oregrade adoption signals for {repo} (repo.id {repo_id})
-- snapshot bound  = {bound} EXCLUSIVE (so the snapshot includes all of {year})
-- trailing window = [{t_start}, {bound})   = calendar {year}
-- prior window    = [{p_start}, {t_start}) = calendar {prior_year}
--
-- Tables are ENUMERATED, never wildcarded. `githubarchive.day` contains the
-- views `today` and `yesterday` alongside its dated tables, and BigQuery
-- refuses to resolve a prefix that matches a view at all:
--   400 Views cannot be queried through prefix. First view githubarchive:day.yesterday
-- A bounded _TABLE_SUFFIX does not save it, because prefix resolution happens
-- before the suffix filter is applied. Enumeration sidesteps the question and
-- is exact about what is scanned, which is also the cost control.
--
-- Filtered by repo.id, never repo.name: GHArchive stores the name as of the
-- event, so a project renamed after its snapshot has its whole history under a
-- name the query would never ask for.
--
-- Two scans, deliberately:
--   window_events  {n_window:3} tables, 4 columns — needs created_at to place
--                      each event in the trailing or prior year
--   history_pushers {n_history:3} tables, 3 columns — every event in them
--                      precedes the snapshot, so created_at is never read
--
-- History runs from the GHArchive epoch because `pushers_before_snapshot`
-- means EVERY push before the snapshot. Bounding it to the two-year window
-- would classify someone who pushed in 2012 and stopped as a non-contributing
-- user in {year}, inflating adoption for exactly the oldest projects in the
-- corpus. It also keeps committers and reporters in one identity namespace —
-- the GitHub login — instead of forcing an email-to-login join against the
-- clone, which degrades worst in the pre-2015 era.
WITH window_events AS (
{window_blocks}
),
history_pushers AS (
{history_blocks}
),
pushers_before_snapshot AS (
  SELECT login FROM history_pushers
  UNION DISTINCT
  SELECT login FROM window_events
  WHERE type = 'PushEvent' AND created_at < TIMESTAMP('{bound}')
),
issues_trailing AS (
  SELECT DISTINCT login FROM window_events
  WHERE type = 'IssuesEvent'
    AND created_at >= TIMESTAMP('{t_start}')
    AND created_at <  TIMESTAMP('{bound}')
),
issues_prior AS (
  SELECT DISTINCT login FROM window_events
  WHERE type = 'IssuesEvent'
    AND created_at >= TIMESTAMP('{p_start}')
    AND created_at <  TIMESTAMP('{t_start}')
)
SELECT
  (SELECT COUNT(*) FROM issues_trailing) AS issue_reporters_12mo,
  (SELECT COUNT(*) FROM issues_prior)    AS issue_reporters_prior_12mo,
  (SELECT COUNT(*) FROM issues_trailing
     WHERE login NOT IN (SELECT login FROM pushers_before_snapshot))
    AS non_contributor_issue_reporters_12mo,
  (SELECT COUNT(*) FROM issues_prior
     WHERE login NOT IN (SELECT login FROM pushers_before_snapshot))
    AS non_contributor_issue_reporters_prior_12mo,
  (SELECT COUNT(*) FROM window_events
     WHERE type = 'ForkEvent'
       AND created_at >= TIMESTAMP('{t_start}')
       AND created_at <  TIMESTAMP('{bound}')) AS forks_12mo,
  (SELECT COUNT(*) FROM pushers_before_snapshot) AS pushers_to_date
"""


def _minus_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:
        return d.replace(year=d.year - n, day=28)


def windows(bound: date) -> dict:
    """`bound` is the EXCLUSIVE snapshot instant — 1 January of year+1.

    Exact year arithmetic rather than 365 days, so the windows land on calendar
    year boundaries instead of drifting a day per leap year.
    """
    trailing_start = _minus_years(bound, 1)
    prior_start = _minus_years(bound, 2)
    return {"bound": bound, "t_start": trailing_start, "p_start": prior_start}


def month_range(start: date, end: date) -> list[str]:
    """Inclusive YYYYMM strings from `start`'s month to `end`'s month."""
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def month_split(bound: date) -> tuple[list[str], list[str]]:
    """(history_months, window_months) for one repo-snapshot.

    `bound` is exclusive, so the last month with data is the month before it.
    """
    last_day = bound - timedelta(days=1)
    w = windows(bound)
    window_months = month_range(w["p_start"], last_day)
    history_end = w["p_start"] - timedelta(days=1)
    history_months = ([] if history_end < GHARCHIVE_EPOCH
                      else month_range(GHARCHIVE_EPOCH, history_end))
    return history_months, window_months


def months_for(bound: date) -> list[str]:
    """Every month scanned for one repo-snapshot, history then window."""
    history, window = month_split(bound)
    return history + window


def build_query(repo: str, bound: date, repo_id: int) -> str:
    """Build the adoption query. `repo_id` is REQUIRED and never a name.

    See pitgit.identity for why: GHArchive stores the repo name as of the
    event, so a name filter misses everything before a rename.
    """
    w = windows(bound)
    history_months, window_months = month_split(bound)
    types = ", ".join(f"'{t}'" for t in EVENT_TYPES)
    window_blocks = "\n  UNION ALL\n".join(
        WINDOW_BLOCK.format(projection=WINDOW_PROJECTION, month=m,
                            repo_id=repo_id, types=types)
        for m in window_months)
    history_blocks = "\n  UNION ALL\n".join(
        HISTORY_BLOCK.format(projection=HISTORY_PROJECTION, month=m,
                             repo_id=repo_id)
        for m in history_months) or (
        f"  SELECT CAST(NULL AS STRING) AS login WHERE FALSE  "
        f"-- no months precede {w['p_start']}")
    return QUERY_TEMPLATE.format(
        repo=repo,
        repo_id=repo_id,
        bound=w["bound"].isoformat(),
        year=w["bound"].year - 1,
        prior_year=w["bound"].year - 2,
        t_start=w["t_start"].isoformat(),
        p_start=w["p_start"].isoformat(),
        window_blocks=window_blocks,
        history_blocks=history_blocks,
        n_window=len(window_months),
        n_history=len(history_months),
    )


def cache_key(repo: str, snapshot: date, repo_id: int) -> str:
    query_hash = hashlib.sha256(
        build_query(repo, snapshot, repo_id).encode()).hexdigest()[:12]
    return f"{repo.replace('/', '__')}__{snapshot.isoformat()}__{query_hash}"


def cache_path(repo: str, snapshot: date, repo_id: int,
               root: Path | None = None) -> Path:
    return (root or CACHE_ROOT) / f"{cache_key(repo, snapshot, repo_id)}.json"


def window_completeness(snapshot: date) -> dict:
    w = windows(snapshot)
    return {
        "trailing_window_complete": w["t_start"] >= GHARCHIVE_EPOCH,
        "prior_window_complete": w["p_start"] >= GHARCHIVE_EPOCH,
        "gharchive_epoch": GHARCHIVE_EPOCH.isoformat(),
    }


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

@dataclass
class Extract:
    """One cached BigQuery result plus everything needed to reproduce it."""
    repo: str
    snapshot: str
    rows: dict
    query: str
    query_sha256: str
    extracted_utc: str
    bytes_billed: int | None = None
    collector_version: str = "1"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"repo": self.repo, "snapshot": self.snapshot, "rows": self.rows,
                "query": self.query, "query_sha256": self.query_sha256,
                "extracted_utc": self.extracted_utc,
                "bytes_billed": self.bytes_billed,
                "collector_version": self.collector_version,
                "notes": self.notes}

    def provenance(self) -> dict:
        """What the run file records — everything but the row values."""
        return {"query_sha256": self.query_sha256,
                "extracted_utc": self.extracted_utc,
                "bytes_billed": self.bytes_billed,
                "collector_version": self.collector_version,
                "cache_file": None, "notes": self.notes}


def load_extract(repo: str, snapshot: date, repo_id: int,
                 root: Path | None = None) -> Extract | None:
    path = cache_path(repo, snapshot, repo_id, root)
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    expected = hashlib.sha256(
        build_query(repo, snapshot, repo_id).encode()).hexdigest()
    if raw["query_sha256"] != expected:
        raise GHArchiveUnavailable(
            f"{path.name}: cached query hash does not match the query this code "
            "builds. The cache is stale — recollect rather than reuse it.")
    extract = Extract(**raw)
    return extract


def save_extract(extract: Extract, repo_id: int,
                 root: Path | None = None) -> Path:
    """Write one cache entry ATOMICALLY.

    Collection is resumable per project, which only holds if a half-written
    file can never exist. A run killed mid-write would otherwise leave a
    truncated JSON that fails to parse on resume — and the natural reaction to
    that, deleting it and re-querying, costs money. Write to a temp file in the
    same directory, then rename.
    """
    path = cache_path(extract.repo, date.fromisoformat(extract.snapshot),
                      repo_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(extract.as_dict(), indent=2, sort_keys=True) + "\n")
    tmp.replace(path)                      # atomic within a filesystem
    return path


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------
#
# Raw counts, never normalised. The 0-1 scaling that used to live here existed
# only to feed a composite score, and it carried two arbitrary caps that had to
# be re-tuned the moment the corpus changed. A dossier reports what was
# measured and lets the reader judge it.

def facts_from(extract: Extract | None, bound: date,
               commits_in_clone: int | None = None) -> dict:
    """The adoption block: raw counts, provenance, and an honest caveat.

    A missing extract yields `unavailable`, NEVER zeros. That distinction cost
    this project an entire collection once: a query filtering on the current
    repo name returned all-zero rows for eight of twenty-four repositories, the
    zeros were legal values, and nothing objected until they were compared
    against commit counts.
    """
    completeness = window_completeness(bound)
    if extract is None:
        return {"status": "unavailable",
                "reason": "no cached extract; run with --gharchive and "
                          "BigQuery credentials",
                "distinct_issue_reporters_12mo": None,
                "distinct_non_contributor_issue_reporters_12mo": None,
                "forks_12mo": None, "pushers_to_date": None,
                "source": "gharchive", **completeness}

    rows = extract.rows
    pushers = rows.get("pushers_to_date")
    reporters = rows.get("issue_reporters_12mo")
    prior = rows.get("issue_reporters_prior_12mo")

    out = {
        "status": "collected",
        "distinct_issue_reporters_12mo": reporters,
        "distinct_non_contributor_issue_reporters_12mo":
            rows.get("non_contributor_issue_reporters_12mo"),
        "distinct_issue_reporters_prior_12mo": prior,
        "forks_12mo": rows.get("forks_12mo"),
        "pushers_to_date": pushers,
        "source": "gharchive",
        "windows": {"trailing": [windows(bound)["t_start"].isoformat(),
                                 bound.isoformat()],
                    "prior": [windows(bound)["p_start"].isoformat(),
                              windows(bound)["t_start"].isoformat()]},
        **completeness,
    }

    # Where is the issue tracker? A repository with pushes but no IssuesEvent
    # at all is not a repository nobody reported bugs against — it is one whose
    # bugs were filed somewhere else. MongoDB used JIRA in 2013; Kafka, Puppet
    # and DC/OS likewise. Reporting zero without this caveat reads as absence
    # of users and is simply wrong.
    if pushers and not reporters and not prior:
        out["caveat"] = "off_github_detected"
        out["caveat_detail"] = (
            "pushes found but no IssuesEvent in either window — issues for "
            "this project were tracked outside GitHub at this date. The zero "
            "counts measure GitHub, not adoption.")
    elif pushers:
        out["caveat"] = "issue_tracker_on_github"
    else:
        out["caveat"] = "no_events_found"
        out["caveat_detail"] = (
            "no events of any kind matched this repo id — check the id before "
            "reading anything into the counts")

    # Cross-source guard: two independent records of the same repository must
    # agree that somebody worked on it.
    if commits_in_clone is not None and pushers is not None:
        impossible = commits_in_clone > 0 and pushers == 0
        out["consistency"] = {
            "commits_in_clone": commits_in_clone,
            "pushers_in_gharchive": pushers,
            "ok": not impossible,
            "problem": ("zero events for a repository with "
                        f"{commits_in_clone} commits — the query matched "
                        "nothing" if impossible else None)}

    out["provenance"] = extract.provenance()
    return out
