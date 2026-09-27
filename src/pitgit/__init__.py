"""pitgit — reconstruct the state of a git repository at a historical date.

A blobless clone plus a date. No API keys, no rate limits, no credentials.

Three things in here are non-obvious, and each was a real bug before it was a
fix:

  UTC-PINNED REVISION RESOLUTION
      `git rev-list -1 --before=2014-12-31 HEAD` resolves a bare date in the
      RUNNER'S local timezone. On grafana/grafana that returns three different
      commits under EDT, UTC and Asia/Tokyo, with three different root trees.
      Every bound here carries an explicit +00:00 and every subprocess runs
      TZ=UTC, so the same date means the same commit on every machine.

  CALENDAR WINDOW ARITHMETIC
      A "trailing 12 months" computed as 365 days drifts one day per leap year,
      so two projects of different vintages get windows of different lengths.
      `minus_years` does exact calendar arithmetic instead.

  ROOT-ONLY LICENCE DETECTION
      A recursive tree walk finds vendored dependency licences. hashicorp/
      terraform at 2016 has 164 licence files, 163 of them under vendor/, and a
      recursive detector confidently reports the wrong one. See licence.py for
      the full list of what that module gets right and why.

Nothing in this package knows what it is being used for. It is intended to be
extractable as a standalone library with no changes beyond the packaging.
"""
from . import licence, repo as repo_module
from .repo import (FREEMAIL, Commit, GitError, Repo, as_of, clone,
                   concentration, minus_years, new_authors, project_age,
                   utc_bound, velocity, vendor_domain, window)
from .licence import Licence, detect, detect_at, identify_all, identify_text
from .identity import RepoIdentity, resolve_identity

__all__ = ["Repo", "Commit", "GitError", "utc_bound", "as_of", "clone",
           "minus_years", "window",
           "concentration", "velocity", "new_authors", "project_age",
           "vendor_domain", "FREEMAIL",
           "Licence", "detect", "detect_at", "identify_text", "identify_all",
           "RepoIdentity", "resolve_identity"]
