"""Rename-invariant repository identity.

A repository's NAME is not a stable key. GitHub records events under the name
the repository had at the time, so any history lookup keyed on today's name
silently misses everything from before a rename — and renames are not rare:
eight of twenty-four projects in the experiment corpus had been renamed between
their snapshot and today, and every one of them returned zero rows.

`repo.id` is permanent. It survives renames, transfers between orgs, and the
vacated path being reused by an unrelated repository — which is exactly what
happened to `antirez/redis`, a path now occupied by a different project with a
different id while the real Redis lives at id 156018.

The GitHub REST API follows renames, so asking it for a historical name returns
the same id: `docker/docker` 301s to `moby/moby` and yields 7691631, the id its
2015 events carry.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

API = "https://api.github.com/repos/{}"
BY_ID = "https://api.github.com/repositories/{}"
USER_AGENT = "pitgit-identity"


@dataclass
class RepoIdentity:
    requested: str
    repo_id: int | None = None
    current_full_name: str | None = None
    renamed_since_requested: bool | None = None
    resolved_at: str | None = None
    source: str = "github_api"
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.repo_id is not None

    def as_dict(self) -> dict:
        out = {"requested": self.requested, "repo_id": self.repo_id,
               "current_full_name": self.current_full_name,
               "renamed_since_requested": self.renamed_since_requested,
               "resolved_at": self.resolved_at, "source": self.source}
        if self.error:
            out["error"] = self.error
        if self.notes:
            out["notes"] = self.notes
        return out


def _get(url: str, token: str | None, timeout: int):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return json.load(r)


def resolve_identity(repo: str, token: str | None = None,
                     timeout: int = 20) -> RepoIdentity:
    """Resolve `owner/name` to its permanent numeric id.

    Never raises. A repository that 404s is a finding — it may have been
    deleted or taken private, which for sourcegraph/sourcegraph was precisely
    the outcome being studied — so the failure is recorded and the caller
    decides what to do about it.

    Unauthenticated requests are limited to 60/hour, which is ample for a
    single dossier; pass a token for bulk use.
    """
    token = token or os.environ.get("GITHUB_TOKEN") or None
    identity = RepoIdentity(requested=repo)
    try:
        data = _get(API.format(repo), token, timeout)
    except urllib.error.HTTPError as exc:
        identity.error = f"HTTP {exc.code}"
        if exc.code == 404:
            identity.notes.append(
                "not resolvable today — deleted, renamed into a path that no "
                "longer redirects, or made private. Historical data may still "
                "exist under an id this lookup cannot recover.")
        return identity
    except Exception as exc:                        # network, DNS, timeout
        identity.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        return identity

    identity.repo_id = data["id"]
    identity.current_full_name = data["full_name"]
    identity.renamed_since_requested = (
        data["full_name"].lower() != repo.lower())
    identity.resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if identity.renamed_since_requested:
        identity.notes.append(
            f"{repo} now redirects to {data['full_name']}; the id is unchanged "
            "and covers both names")
    return identity
