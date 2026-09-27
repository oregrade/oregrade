"""Point-in-time reconstruction from a blobless clone.

Everything here answers "what did this repo look like on DATE" using only a
`git clone --filter=blob:none --no-checkout`. No API keys, no rate limits.
Derived from tools/spike_pit.py, which proved the approach on three repos.

Commit author emails are used transiently to count and group contributors.
They are never returned by any function here and never reach a run file — see
the project README on maintainer contact data.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# Every git invocation runs with TZ=UTC, and every date bound is passed with an
# explicit UTC offset. Both are required, and neither is paranoia.
#
# `git rev-list -1 --before=2014-12-31 HEAD` resolves the bare date in the
# RUNNER'S local timezone. On grafana/grafana that returns three different
# revisions under EDT, UTC and Asia/Tokyo — with three different root trees and
# so, potentially, three different licences. A tool whose whole claim is
# point-in-time reconstruction cannot have its snapshot boundary move with the
# laptop it runs on.
#
# TZ=UTC additionally pins `--date=short`, which otherwise renders each commit
# in the timezone the committer happened to be in, making day bucketing for the
# velocity windows depend on contributor geography.
UTC_ENV = {**os.environ, "TZ": "UTC"}


def utc_bound(cutoff: date) -> str:
    """A date as an unambiguous instant: 00:00:00 UTC on that day.

    Every cutoff in this codebase is EXCLUSIVE — `--before` and `day < cutoff`
    both mean strictly earlier: `as_of(d)` turns a date the caller means
    INCLUSIVELY into the exclusive instant that follows it.
    """
    return f"{cutoff.isoformat()}T00:00:00+00:00"


def as_of(day: date) -> date:
    """The exclusive bound for a date the caller means inclusively.

    `--at 2017-12-31` means "everything that had happened by the end of 31
    December 2017", so the bound is 2018-01-01T00:00:00+00:00. Getting this
    wrong silently drops the last day, which on grafana/grafana is the
    difference between a root tree that has LICENSE.md and one that does not.
    """
    return day + timedelta(days=1)


def minus_years(d: date, n: int) -> date:
    """Exact calendar-year arithmetic, leap-safe.

    Used for the 12-month windows in preference to 365 days, which drifts a day
    per leap year and would stop the trailing window aligning to a calendar
    year once the snapshot bound became 1 January.
    """
    try:
        return d.replace(year=d.year - n)
    except ValueError:                       # 29 February
        return d.replace(year=d.year - n, day=28)

FREEMAIL = {
    "gmail.com", "users.noreply.github.com", "googlemail.com", "hotmail.com",
    "yahoo.com", "outlook.com", "protonmail.com", "proton.me", "me.com",
    "icloud.com", "live.com", "fastmail.com", "gmx.de", "gmx.net", "web.de",
    "mail.ru", "yandex.ru", "qq.com", "163.com", "126.com", "aol.com",
    "posteo.de", "riseup.net", "pm.me", "hey.com", "zoho.com",
}


class GitError(RuntimeError):
    pass


@dataclass
class Commit:
    sha: str
    email: str
    day: date


@dataclass
class Repo:
    """A local blobless clone, queried as of a date."""
    path: Path
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.path = Path(self.path)

    # -- plumbing ---------------------------------------------------------
    def _run(self, args, check=False, limit=None):
        proc = subprocess.run(["git", *args], cwd=self.path, capture_output=True,
                              text=True, errors="replace", timeout=600,
                              env=UTC_ENV)
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()[:300]}")
        return proc.stdout[:limit] if limit else proc.stdout

    def exists(self) -> bool:
        return (self.path / "HEAD").exists() or (self.path / ".git" / "HEAD").exists()

    # -- history ----------------------------------------------------------
    def commits_before(self, cutoff: date) -> list[Commit]:
        """All commits reachable from HEAD with committer date < cutoff."""
        key = ("commits", cutoff)
        if key not in self._cache:
            # short-local renders in TZ (pinned to UTC above) rather than in
            # each committer's own timezone.
            out = self._run(["log", f"--before={utc_bound(cutoff)}",
                             "--pretty=%H|%ae|%cd", "--date=short-local"],
                            check=True)
            rows = []
            for line in out.splitlines():
                parts = line.split("|")
                if len(parts) != 3:
                    continue
                try:
                    day = date.fromisoformat(parts[2])
                except ValueError:
                    continue
                rows.append(Commit(parts[0], parts[1].strip().lower(), day))
            self._cache[key] = rows
        return self._cache[key]

    def rev_at(self, cutoff: date) -> str | None:
        out = self._run(["rev-list", "-1", f"--before={utc_bound(cutoff)}",
                         "HEAD"]).strip()
        return out or None

    # -- trees ------------------------------------------------------------
    def ls_root(self, rev: str) -> list[str]:
        """Root-level entries only. NEVER pass -r here — see src/licence.py."""
        out = self._run(["ls-tree", "--name-only", rev])
        return [f for f in out.splitlines() if f]

    def ls_recursive(self, rev: str) -> list[str]:
        out = self._run(["ls-tree", "-r", "--name-only", rev])
        return [f for f in out.splitlines() if f]

    def show(self, rev: str, path: str, limit: int = 200_000) -> str:
        return self._run(["show", f"{rev}:{path}"], limit=limit)


# ---------------------------------------------------------------------------
# feature helpers — all take the commit list produced above
# ---------------------------------------------------------------------------

def window(commits: list[Commit], end: date, days: int) -> list[Commit]:
    start = end - timedelta(days=days)
    return [c for c in commits if start <= c.day < end]


def concentration(commits: list[Commit]) -> dict:
    """Top-1 / top-3 commit share over all history to the cutoff."""
    if not commits:
        return {"authors_to_date": 0, "top1_commit_share": None,
                "top3_commit_share": None}
    authors = Counter(c.email for c in commits)
    total = sum(authors.values())
    top = authors.most_common(3)
    return {
        "authors_to_date": len(authors),
        "top1_commit_share": round(top[0][1] / total, 4),
        "top3_commit_share": round(sum(n for _, n in top) / total, 4),
    }


def velocity(commits: list[Commit], cutoff: date) -> dict:
    """Trailing-90d commit count and momentum against the prior 90d.

    Momentum is the ratio trailing/prior. A prior window of zero commits has no
    defined ratio, so it is reported as None rather than as an infinite gain.
    """
    recent = window(commits, cutoff, 90)
    prior_end = cutoff - timedelta(days=90)
    prior = window(commits, prior_end, 90)
    ratio = round(len(recent) / len(prior), 4) if prior else None
    return {
        "commits_trailing_90d": len(recent),
        "commits_prior_90d": len(prior),
        "momentum_90d": ratio,
        "authors_trailing_90d": len({c.email for c in recent}),
        "merge_commits_trailing_90d": None,  # set by features.py; unreliable
    }


def new_authors(commits: list[Commit], cutoff: date) -> dict:
    """Authors whose FIRST commit falls inside the trailing 12 months."""
    first_seen: dict[str, date] = {}
    for c in commits:
        if c.email not in first_seen or c.day < first_seen[c.email]:
            first_seen[c.email] = c.day
    start = minus_years(cutoff, 1)
    fresh = [e for e, d in first_seen.items() if start <= d < cutoff]
    return {"new_authors_trailing_12mo": len(fresh),
            "new_author_ratio_12mo": (round(len(fresh) / len(first_seen), 4)
                                      if first_seen else None)}


def vendor_domain(commits: list[Commit], top_n: int = 5) -> str | None:
    """Best guess at the sponsoring organisation's email domain.

    Modal non-freemail domain among the top-N all-time committers. Returns None
    when the core team commits from personal addresses, which is itself the
    answer for a project with no corporate centre of gravity.
    """
    if not commits:
        return None
    ranked = [e for e, _ in Counter(c.email for c in commits).most_common(top_n)]
    domains = Counter()
    for email in ranked:
        _, _, dom = email.partition("@")
        if dom and dom not in FREEMAIL:
            domains[dom] += 1
    return domains.most_common(1)[0][0] if domains else None


def external_ratio(commits: list[Commit], cutoff: date, domain: str | None) -> dict:
    """Share of trailing-12mo commits from outside the sponsoring domain.

    Undefined (None) when no sponsoring domain is identifiable — reporting 1.0
    there would claim a fully external contributor base on no evidence.
    """
    start = minus_years(cutoff, 1)
    recent = [c for c in commits if start <= c.day < cutoff]
    if not recent:
        return {"external_contributor_ratio": None, "vendor_domain": domain,
                "commits_trailing_12mo": 0}
    if domain is None:
        return {"external_contributor_ratio": None, "vendor_domain": None,
                "commits_trailing_12mo": len(recent)}
    outside = sum(1 for c in recent if not c.email.endswith("@" + domain))
    return {"external_contributor_ratio": round(outside / len(recent), 4),
            "vendor_domain": domain,
            "commits_trailing_12mo": len(recent)}


def project_age(commits: list[Commit], cutoff: date) -> dict:
    if not commits:
        return {"first_commit": None, "last_commit_before_cutoff": None,
                "age_years_at_snapshot": None, "days_since_last_commit": None}
    days = [c.day for c in commits]
    first, last = min(days), max(days)
    return {
        "first_commit": first.isoformat(),
        "last_commit_before_cutoff": last.isoformat(),
        "age_years_at_snapshot": round((cutoff - first).days / 365.25, 2),
        "days_since_last_commit": (cutoff - last).days,
    }


# ---------------------------------------------------------------------------
# acquisition
# ---------------------------------------------------------------------------

def clone(repo: str, dest: Path, timeout: int = 1800) -> dict:
    """Blobless, no-checkout clone: full commit graph, file contents on demand.

    Small and fast — the whole commit history without the blobs — which is what
    makes point-in-time reconstruction practical on large repositories. Blobs
    are fetched lazily when a tree is actually read, so a licence lookup costs
    one round trip rather than a full checkout.

    Returns a record rather than raising, because a repository that can no
    longer be cloned is a FINDING, not an error: the dossier reports
    `clone_unavailable` and carries on.
    """
    dest = Path(dest)
    if (dest / "HEAD").exists() or (dest / ".git" / "HEAD").exists():
        return {"status": "already_present", "path": str(dest)}
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", "--quiet",
         f"https://github.com/{repo}.git", str(dest)],
        capture_output=True, text=True, timeout=timeout,
        env={**UTC_ENV, "GIT_TERMINAL_PROMPT": "0"})
    if proc.returncode == 0:
        return {"status": "ok", "path": str(dest)}
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    return {"status": "clone_unavailable", "path": str(dest),
            "stderr": proc.stderr.strip()[-400:]}
