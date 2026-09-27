"""Point-in-time determinism.

The snapshot boundary must not depend on the machine the backtest runs on.
"""
import os
from datetime import date
from pathlib import Path

import pytest

from pitgit import repo as gitpit

CLONES = Path("/tmp/oregrade-clones")


def test_bounds_carry_an_explicit_utc_offset():
    assert gitpit.utc_bound(date(2014, 12, 31)) == "2014-12-31T00:00:00+00:00"


def test_git_runs_with_tz_pinned_to_utc():
    assert gitpit.UTC_ENV["TZ"] == "UTC"


@pytest.mark.parametrize("tz", ["UTC", "America/New_York", "Asia/Tokyo",
                                "Pacific/Kiritimati"])
def test_rev_at_is_identical_under_every_runner_timezone(tz, monkeypatch):
    """A bare --before= date resolves in local time and picks a different commit.

    On grafana/grafana at 2014-12-31 that produced three distinct revisions
    across three timezones, with different root trees and so potentially
    different licences.
    """
    repo = gitpit.Repo(CLONES / "grafana__grafana")
    if not repo.exists():
        pytest.skip("grafana not cloned")
    monkeypatch.setenv("TZ", tz)
    monkeypatch.setattr(gitpit, "UTC_ENV", {**os.environ, "TZ": "UTC"})
    assert repo.rev_at(date(2014, 12, 31)) == \
        "8268c65c57a79435c7d99dc9467c77cbff56163a"


@pytest.mark.parametrize("tz", ["UTC", "Asia/Tokyo"])
def test_commit_day_bucketing_is_timezone_stable(tz, monkeypatch):
    repo = gitpit.Repo(CLONES / "redis__redis")
    if not repo.exists():
        pytest.skip("redis not cloned")
    monkeypatch.setenv("TZ", tz)
    monkeypatch.setattr(gitpit, "UTC_ENV", {**os.environ, "TZ": "UTC"})
    repo._cache.clear()
    commits = repo.commits_before(date(2015, 12, 31))
    assert gitpit.concentration(commits)["top1_commit_share"] == pytest.approx(
        0.7901, abs=1e-4)
    assert len(commits) == 5498
