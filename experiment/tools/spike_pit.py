#!/usr/bin/env python3
"""
oregrade spike: can we reconstruct gate-1/gate-2 signals at a past date
using ONLY a git clone? No API keys, no GitHub metadata, no rate limits.

If this works, the backtest is viable. If it doesn't, the design is wrong.
"""
import subprocess, sys, os, json
from collections import Counter
from datetime import datetime

def sh(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True).stdout

def clone(repo, dest):
    """Blobless clone: full commit history, no file contents. Fast and small."""
    if os.path.exists(dest):
        return dest
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", "--quiet",
         f"https://github.com/{repo}.git", dest],
        check=True, capture_output=True, timeout=900)
    return dest

def at_date(path, date):
    """Reconstruct signals as of `date` (YYYY-MM-DD)."""
    before = f"--before={date}"
    out = {}

    log = sh(["git", "log", before, "--pretty=%H|%an|%ae|%ad", "--date=short"], cwd=path)
    rows = [l.split("|") for l in log.strip().split("\n") if l.count("|") == 3]
    if not rows:
        return None

    out["total_commits_to_date"] = len(rows)
    out["first_commit"] = rows[-1][3]
    out["last_commit_before_date"] = rows[0][3]

    # contributor concentration, all-time up to date (by email, lowercased)
    authors = Counter(r[2].lower() for r in rows)
    total = sum(authors.values())
    out["distinct_authors_to_date"] = len(authors)
    out["top1_commit_share"] = round(authors.most_common(1)[0][1] / total, 3)
    out["top3_commit_share"] = round(sum(c for _, c in authors.most_common(3)) / total, 3)

    # trailing 90d velocity before the snapshot date
    d = datetime.strptime(date, "%Y-%m-%d")
    since = (d.replace(year=d.year - 1) if False else d).toordinal() - 90
    since_str = datetime.fromordinal(since).strftime("%Y-%m-%d")
    log90 = sh(["git", "log", before, f"--since={since_str}",
                "--pretty=%ae"], cwd=path)
    a90 = [l.lower() for l in log90.strip().split("\n") if l]
    out["commits_trailing_90d"] = len(a90)
    out["authors_trailing_90d"] = len(set(a90))

    # merge-commit proxy for PR velocity (fails on squash-merge repos)
    m90 = sh(["git", "log", before, f"--since={since_str}", "--merges",
              "--pretty=%H"], cwd=path)
    out["merge_commits_trailing_90d"] = len([l for l in m90.strip().split("\n") if l])

    # license at date — resolve the tree, no checkout needed
    rev = sh(["git", "rev-list", "-1", before, "HEAD"], cwd=path).strip()
    tree = sh(["git", "ls-tree", "--name-only", rev], cwd=path).split("\n")
    lic = [f for f in tree if f.upper().startswith(("LICENSE", "COPYING"))]
    out["license_file_at_date"] = lic[0] if lic else None
    if lic:
        body = sh(["git", "show", f"{rev}:{lic[0]}"], cwd=path)[:4000].upper()
        for name, needle in [
            ("AGPL-3.0", "AFFERO"), ("GPL-3.0", "GNU GENERAL PUBLIC LICENSE"),
            ("Apache-2.0", "APACHE LICENSE"), ("MIT", "MIT LICENSE"),
            ("BSD", "REDISTRIBUTION AND USE IN SOURCE"), ("MPL-2.0", "MOZILLA PUBLIC"),
        ]:
            if needle in body:
                out["license_guess_at_date"] = name
                break
        else:
            out["license_guess_at_date"] = "unknown"
    return out


TARGETS = [
    ("grafana/grafana",     "2014-12-31", "commercialized_success"),
    ("psf/requests",        "2013-12-31", "never_commercialized"),
    ("rethinkdb/rethinkdb", "2014-12-31", "commercialized_failed"),
]

if __name__ == "__main__":
    results = {}
    for repo, date, label in TARGETS:
        dest = "/tmp/" + repo.replace("/", "__")
        try:
            clone(repo, dest)
            r = at_date(dest, date)
            r["_label"] = label
            r["_snapshot"] = date
            results[repo] = r
            print(f"[ok] {repo}", file=sys.stderr)
        except Exception as e:
            results[repo] = {"error": str(e)[:200]}
            print(f"[FAIL] {repo}: {e}", file=sys.stderr)
    print(json.dumps(results, indent=2))
