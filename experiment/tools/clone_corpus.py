#!/usr/bin/env python3
"""Blobless clone of every in-scope repo. Failures are recorded, never dropped.

    python3 tools/clone_corpus.py [--extra owner/repo ...]

Writes runs/clone_log.json: one record per repo with status, elapsed seconds,
on-disk size and, for failures, the stderr tail. A repo that cannot be cloned is
a finding (sourcegraph/sourcegraph is the known case), so it stays in the log
with status `clone_unavailable` and flows through the run as such.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CLONE_ROOT = Path("/tmp/oregrade-clones")
TIMEOUT = 1800


def dest_for(repo):
    return CLONE_ROOT / repo.replace("/", "__")


def clone(repo):
    dest = dest_for(repo)
    rec = {"repo": repo, "path": str(dest)}
    if (dest / "HEAD").exists() or (dest / ".git" / "HEAD").exists():
        rec["status"] = "already_present"
        return rec
    if dest.exists():
        shutil.rmtree(dest)
    t0 = time.time()
    proc = subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", "--quiet",
         f"https://github.com/{repo}.git", str(dest)],
        capture_output=True, text=True, timeout=TIMEOUT,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin",
             "HOME": str(Path.home())})
    rec["elapsed_s"] = round(time.time() - t0, 1)
    if proc.returncode == 0:
        rec["status"] = "ok"
    else:
        rec["status"] = "clone_unavailable"
        rec["stderr"] = proc.stderr.strip()[-500:]
        if dest.exists():
            shutil.rmtree(dest)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extra", nargs="*", default=[],
                    help="repos to clone beyond the corpus (licence fixtures)")
    args = ap.parse_args()

    corpus = yaml.safe_load((ROOT / "benchmarks" / "projects.yaml").read_text())
    repos = [p["repo"] for p in corpus["projects"]] + args.extra

    CLONE_ROOT.mkdir(parents=True, exist_ok=True)
    log = []
    for i, repo in enumerate(repos, 1):
        rec = clone(repo)
        if rec["status"] in ("ok", "already_present"):
            du = subprocess.run(["du", "-sm", rec["path"]], capture_output=True,
                                text=True)
            rec["size_mb"] = int(du.stdout.split()[0]) if du.stdout.split() else None
        log.append(rec)
        print(f"[{i:2}/{len(repos)}] {rec['status']:18} {repo} "
              f"{rec.get('size_mb', '')}", flush=True)

    out = ROOT / "runs" / "clone_log.json"
    out.write_text(json.dumps(log, indent=2) + "\n")
    failed = [r["repo"] for r in log if r["status"] == "clone_unavailable"]
    print(f"\nok={sum(1 for r in log if r['status'] in ('ok', 'already_present'))} "
          f"unavailable={len(failed)} {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
