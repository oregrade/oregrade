"""Assemble one repository's dossier at one date.

A structured fact sheet with provenance on every line and no opinion anywhere
in it. There is no score, no ranking and no verdict — run-003 established that
the composite score did not predict (AUC 0.286 against a pre-registered 0.75,
five of eight inputs correlating with outcome in the wrong direction), and
shipping it anyway would be the failure the writeup documents in other tools.

What survives is the retrieval half: facts about a repository at a date, which
are useful whether or not they correlate with anything, and which are slow and
error-prone to assemble by hand.

Three run modes, degrading in that order:

    git only    the default. Clone, reconstruct, report. No credentials.
    --gharchive adds the adoption block. Needs BigQuery and costs money.
    --github    adds present-day metadata, never claimed as point-in-time.

Anything unavailable says `unavailable` and why. Nothing ever defaults to zero.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pitgit

from . import commercial, gharchive, rights, surface

DEFAULT_CACHE = Path(os.environ.get(
    "OREGRADE_CACHE", Path.home() / ".cache" / "oregrade"))
STABILITY_WINDOW_DAYS = 7


@dataclass
class Options:
    at: date
    gharchive: bool = False
    github: bool = False
    cache_dir: Path = DEFAULT_CACHE
    clone_dir: Path | None = None
    token: str | None = None
    offline: bool = False


def _read_root(repo: pitgit.Repo, rev: str):
    entries = repo.ls_root(rev)

    def read(path: str) -> str:
        return repo.show(rev, path, limit=200_000)

    return entries, read


def _readme(entries, read) -> tuple[str | None, str | None]:
    for name in entries:
        if name.upper().startswith("README"):
            body = read(name)
            if body:
                return body, name
    return None, None


def _licence_block(repo: pitgit.Repo, rev: str, bound: date) -> dict:
    result = pitgit.detect_at(repo, rev)
    entries = repo.ls_root(rev)
    nested = [f for f in repo.ls_recursive(rev)
              if pitgit.licence.is_licence_filename(Path(f).name)
              and "/" in f]

    block = {
        "spdx": result.spdx,
        "file": result.source_file,
        "method": result.method,
        "evidence": result.evidence or None,
        "scope": "root_only",
        "scope_note": ("vendored and sub-directory licences are deliberately "
                       "ignored. terraform at 2016 has 164 licence files, 163 "
                       "of them under vendor/, and a recursive walk reports "
                       "one of those."),
        "root_licence_files": result.candidates or None,
        "vendored_licences_present": len(nested),
        "ambiguous": result.spdx == "ambiguous",
        "secondary_ids": result.secondary or None,
        "side_licences": result.side_licences or None,
    }
    if result.spdx == "ambiguous":
        block["ambiguity_note"] = (
            "the root licence file states policy rather than granting one "
            "licence — different terms for binaries, source and subdirectories "
            "— and no positional rule resolves it. Read the file.")
    block["confidence"] = (
        "high" if result.method == "licence_file" and not result.multi_licence
        else "low" if result.spdx in ("none", "ambiguous")
        else "medium")

    # Robustness probe. Deliberately reads a revision AFTER the date: it does
    # not feed any point-in-time fact, it only warns that the reading sits next
    # to a change. grafana's root licence vanished on 31 December 2014 and came
    # back a week later, so "stable" is worth knowing.
    neighbours = {}
    for label, offset in (("minus_7d", -STABILITY_WINDOW_DAYS),
                          ("plus_7d", STABILITY_WINDOW_DAYS)):
        probe_rev = repo.rev_at(bound + timedelta(days=offset))
        neighbours[label] = (pitgit.detect_at(repo, probe_rev).spdx
                             if probe_rev else None)
    changed = {k: v for k, v in neighbours.items()
               if v is not None and v != result.spdx}
    block["stability"] = {
        "status": "stable" if not changed else "changed_nearby",
        "checked": [f"-{STABILITY_WINDOW_DAYS}d", f"+{STABILITY_WINDOW_DAYS}d"],
        "neighbours": neighbours,
        "note": ("the +7d probe intentionally looks after the date. It is a "
                 "robustness indicator, never an input to any field above."),
    }
    if changed:
        block["stability"]["differs_at"] = changed
    return block


def _activity_block(commits, bound: date) -> dict:
    out = {"commits_to_date": len(commits)}
    out.update(pitgit.concentration(commits))
    out.update(pitgit.velocity(commits, bound))
    out.update(pitgit.new_authors(commits, bound))
    domain = pitgit.vendor_domain(commits)
    out.update(pitgit.repo_module.external_ratio(commits, bound, domain))
    out.update(pitgit.project_age(commits, bound))
    out["sponsoring_email_domain"] = out.pop("vendor_domain", domain)
    out["sponsoring_domain_note"] = (
        "modal non-freemail domain among the top committers. Absent where the "
        "core team commits from personal addresses, which is itself the answer "
        "for a project with no corporate centre of gravity.")
    out.pop("merge_commits_trailing_90d", None)
    return out


def build(repo_name: str, options: Options) -> dict:
    """The whole dossier. Never raises on a missing source; reports it."""
    bound = pitgit.as_of(options.at)
    clone_dir = Path(options.clone_dir or (options.cache_dir / "clones"))
    dest = clone_dir / repo_name.replace("/", "__")

    doc: dict = {
        "repo": repo_name,
        "as_of": options.at.isoformat(),
        "resolved_at": pitgit.utc_bound(bound),
        "resolution_note": (
            f"`--at {options.at.isoformat()}` means everything that had "
            f"happened by the end of that day, so the exclusive bound is "
            f"{pitgit.utc_bound(bound)}."),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    identity = pitgit.RepoIdentity(requested=repo_name)
    if not options.offline:
        identity = pitgit.resolve_identity(repo_name, token=options.token)
    doc["repo_id"] = identity.repo_id
    doc["identity"] = identity.as_dict()
    if identity.repo_id is None:
        doc["identity"]["note"] = (
            "unresolved. repo.id is what makes history lookups survive "
            "renames; without it the adoption block cannot run.")

    clone_result = pitgit.clone(repo_name, dest)
    doc["clone"] = clone_result
    repo = pitgit.Repo(dest)
    if clone_result["status"] == "clone_unavailable" or not repo.exists():
        doc["status"] = "clone_unavailable"
        doc["status_note"] = (
            "the repository could not be cloned. That is a finding in itself — "
            "it may have been taken private, deleted, or closed — not an error "
            "to retry past.")
        return doc

    rev = repo.rev_at(bound)
    if rev is None:
        doc["status"] = "no_history_at_date"
        doc["status_note"] = (
            f"the repository has no commit before {pitgit.utc_bound(bound)}; "
            "it did not exist publicly at this date.")
        return doc

    doc["status"] = "ok"
    doc["revision"] = rev
    commits = repo.commits_before(bound)
    entries, read = _read_root(repo, rev)
    readme, readme_file = _readme(entries, read)

    doc["licence"] = _licence_block(repo, rev, bound)
    doc["rights"] = rights.detect(repo_name, entries, read, commits)
    doc["surface"] = surface.classify(readme, entries)
    doc["surface"]["readme_file"] = readme_file
    doc["activity"] = _activity_block(commits, bound)
    doc["commercial"] = commercial.detect(entries, read, readme)

    if options.github and not options.offline:
        doc["commercial"]["present_day"] = commercial.present_day(
            identity, token=options.token)

    if options.gharchive:
        if identity.repo_id is None:
            doc["adoption"] = {
                "status": "unavailable",
                "reason": "repo id unresolved; a name-keyed query would miss "
                          "everything before any rename"}
        else:
            extract = None
            try:
                extract = gharchive.load_extract(
                    repo_name, bound, identity.repo_id,
                    root=options.cache_dir / "gharchive")
            except gharchive.GHArchiveUnavailable as exc:
                extract = None
                doc.setdefault("warnings", []).append(str(exc))
            doc["adoption"] = gharchive.facts_from(
                extract, bound, commits_in_clone=len(commits))
    else:
        doc["adoption"] = {
            "status": "not_requested",
            "reason": "run with --gharchive to collect it (needs BigQuery "
                      "credentials and costs money on deep history)"}

    doc["provenance"] = {
        "git_reconstruction": "blobless_clone",
        "timezone": "UTC",
        "revision_resolution": "explicit +00:00 bound, TZ=UTC subprocesses",
        "window_arithmetic": "calendar years, not 365 days",
        "licence_scope": "root_only",
        "identity_source": identity.source if identity.available else "unresolved",
        "consistency_checks_passed": _checks(doc),
        "modes": {"git": True, "gharchive": options.gharchive,
                  "github": options.github},
    }
    return doc


def _checks(doc: dict) -> list[str]:
    passed = []
    adoption = doc.get("adoption") or {}
    consistency = adoption.get("consistency")
    if consistency and consistency.get("ok"):
        passed.append("commits_vs_pushers")
    if doc.get("licence", {}).get("stability", {}).get("status") == "stable":
        passed.append("licence_stable_within_7d")
    if doc.get("identity", {}).get("repo_id"):
        passed.append("repo_id_resolved")
    return passed
