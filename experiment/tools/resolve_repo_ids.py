#!/usr/bin/env python3
"""Resolve each corpus project to its stable numeric GitHub repo id.

    python3 tools/resolve_repo_ids.py            # writes benchmarks/repo_ids.yaml

WHY THIS EXISTS

GHArchive records `repo.name` as it was AT THE TIME OF THE EVENT. Filtering
history by a project's current name therefore finds nothing for any period
before it was renamed. The first collection did exactly that and eight of the
twenty-four projects matched zero events — not few, zero — including
elasticsearch, redis and chef, each with five figures of commits.

`repo.id` is permanent and survives every rename, so the queries filter on it.

The id is fetched from the GitHub REST API, which follows renames: asking for
`docker/docker` returns 301 to `moby/moby` and yields the same id the 2015
events carry. One unauthenticated call per project, well inside the 60/hour
anonymous limit.

The resolved ids are committed to benchmarks/repo_ids.yaml so a collection run
is reproducible without network access.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmarks" / "repo_ids.yaml"
API = "https://api.github.com/repos/{}"


def resolve(repo: str) -> dict:
    request = urllib.request.Request(
        API.format(repo), headers={"Accept": "application/vnd.github+json",
                                   "User-Agent": "oregrade-repo-id-resolver"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    return {"repo_id": data["id"], "current_full_name": data["full_name"]}


def main() -> int:
    corpus = yaml.safe_load((ROOT / "benchmarks" / "projects.yaml").read_text())
    resolved, failed = {}, []

    for project in corpus["projects"]:
        repo = project["repo"]
        try:
            info = resolve(repo)
        except urllib.error.HTTPError as exc:
            failed.append((repo, f"HTTP {exc.code}"))
            print(f"  {repo:26} FAILED HTTP {exc.code}")
            time.sleep(0.4)
            continue
        renamed = info["current_full_name"].lower() != repo.lower()
        resolved[repo] = {"repo_id": info["repo_id"],
                          "current_full_name": info["current_full_name"]}
        print(f"  {repo:26} id={info['repo_id']:<12} now "
              f"{info['current_full_name']}{'   <- renamed since' if renamed else ''}")
        time.sleep(0.4)

    OUT.write_text(
        "# Stable numeric GitHub repo ids for the corpus.\n"
        "#\n"
        "# GHArchive records repo.name as of the event, so filtering history by a\n"
        "# project's CURRENT name misses everything recorded under a former one.\n"
        "# repo.id is permanent. Queries filter on it; see src/gharchive.py.\n"
        "#\n"
        "# current_full_name is recorded for audit only. It detects a rename that\n"
        "# happened AFTER today's name was adopted (docker/docker -> moby/moby) but\n"
        "# NOT one that happened before (elasticsearch/elasticsearch ->\n"
        "# elastic/elasticsearch, where today's name is already the new one). Only\n"
        "# the id is invariant, which is the whole reason it is used.\n"
        f"# Resolved {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        "by tools/resolve_repo_ids.py\n"
        + yaml.safe_dump({"repo_ids": resolved}, sort_keys=False))

    print(f"\nresolved {len(resolved)}, failed {len(failed)} -> "
          f"{OUT.relative_to(ROOT)}")
    for repo, why in failed:
        print(f"  {repo}: {why}  (expected for projects that are no longer public)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
