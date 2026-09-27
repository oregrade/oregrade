#!/usr/bin/env python3
"""Collect GHArchive adoption signals into cache/gharchive/.

    python3 tools/collect_gharchive.py --dry-run     # print queries + cost, no billing
    python3 tools/collect_gharchive.py               # run and cache
    python3 tools/collect_gharchive.py --verify      # check cache without querying

Scoring NEVER calls BigQuery. It reads the cache only, so a run is reproducible
from the committed cache files with no credentials and no billing. `--verify`
re-derives every cache key from the query this code builds and fails on drift,
which is what makes that claim checkable rather than asserted.

Requires `google-cloud-bigquery` and application-default credentials against a
project with billing. The `githubarchive` dataset is public; the query is
charged to the caller.

Measured cost is 1.174 TB across the 24 collectable projects — about $7.34, of
which roughly $1.09 falls outside the 1 TB monthly free allowance. Per project
it ranges from 6.7 GB (bound 2014, 35 monthly tables) to 170.4 GB (bound 2021,
119 tables), because GHArchive monthly tables grow by more than an order of
magnitude over the period. Collect with `--budget-tb 1.2`; always `--dry-run`
first, which bills nothing.

Projects with no clone are skipped. They have no git features and are never
scored, so their adoption signals would be bought for a row that is excluded
anyway — `sourcegraph/sourcegraph` alone was 121.7 GB of the original 1.296 TB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import features, gharchive  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def corpus_projects():
    return yaml.safe_load(
        (ROOT / "benchmarks" / "projects.yaml").read_text())["projects"]


def skipped_targets():
    """Projects excluded from collection because they have no clone.

    A project without a clone has no git features, so it is never scored and
    never reaches the corpus — buying its adoption signals is money spent on a
    row that is excluded anyway. sourcegraph/sourcegraph is the standing case:
    it stopped being publicly cloneable, which is the outcome it was labelled
    for, and it was priced at 121.7 GB in the first full dry run.
    """
    return [(p["repo"], features.snapshot_bound(p["fund_snapshot"]))
            for p in corpus_projects()
            if not features.clone_available(p["repo"])]


def targets(only=None):
    out = [(p["repo"], features.snapshot_bound(p["fund_snapshot"]))
           for p in corpus_projects()
           if features.clone_available(p["repo"])]
    return [t for t in out if t[0] == only] if only else out


def client_or_none():
    try:
        from google.cloud import bigquery
    except ImportError:
        return None, ("google-cloud-bigquery is not installed "
                      "(pip install google-cloud-bigquery)")
    try:
        return bigquery.Client(), None
    except Exception as exc:                      # credentials, project, billing
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"


PRICE_PER_TB = 6.25
FREE_TIER_TB = 1.0

# --- projection-regression detection ----------------------------------------
#
# The first version compared GB-per-month against a flat 0.411 and flagged
# anything above 3x. That produced two false positives — dbt-core and directus,
# the two deepest scans — for a reason that is obvious in hindsight: GHArchive
# monthly tables grow enormously over time, so a scan reaching back to 2011
# averages a completely different GB/month from one starting in 2019. The flat
# threshold was measuring snapshot depth, not projection width.
#
# What makes a sharp test possible is that scan cost is a PURE FUNCTION OF THE
# BOUND YEAR. Every project sharing a bound year scans exactly the same set of
# monthly tables, and those tables are historical and immutable — 201403 will
# never change size. So two checks, neither of which involves a rate:
#
#   1. COHORT: projects sharing a bound year must price identically. A
#      projection regression on one project shows as a deviation from its own
#      cohort, which is what a regression actually looks like.
#   2. BASELINE: each bound year has a recorded absolute expectation. This
#      catches a regression that hits every project at once — adding `payload`
#      to the projection — which the cohort check cannot see, because it
#      scales every cohort together.
#
# Seeded from the 2026-08-15 dry run and extended automatically as new bound
# years are observed. A cohort with no baseline is recorded, not flagged.
BASELINE_PATH = ROOT / "cache" / "gharchive" / "scan_baseline.json"
COHORT_TOLERANCE = 0.01      # 1% — same table set, so identical in practice
BASELINE_TOLERANCE = 0.10    # 10% — allows for BigQuery statistics drift


def query_shape() -> str:
    """Fingerprint of everything that determines what a query SCANS.

    A baseline is only meaningful against the query shape it was measured with.
    Swapping the filter from `repo.name` to `repo.id` moved every cohort by
    17-32% — a variable-length STRING column replaced by a fixed-width INTEGER
    — with the table list byte-identical. That is a shape change, not drift,
    and reporting it as DRIFT would fail every future run until someone
    hand-edited the file.

    Comments are excluded: they change nothing about the scan.
    """
    material = "\n".join([WINDOW_BLOCK, HISTORY_BLOCK,
                          gharchive.WINDOW_PROJECTION,
                          gharchive.HISTORY_PROJECTION,
                          "|".join(gharchive.EVENT_TYPES)])
    return hashlib.sha256(material.encode()).hexdigest()[:16]


WINDOW_BLOCK = gharchive.WINDOW_BLOCK
HISTORY_BLOCK = gharchive.HISTORY_BLOCK


def load_baseline() -> tuple[dict, str | None]:
    """Returns (bytes_by_bound_year, shape_it_was_measured_against)."""
    if not BASELINE_PATH.exists():
        return {}, None
    raw = json.loads(BASELINE_PATH.read_text())
    return ({int(k): v for k, v in raw["bytes_by_bound_year"].items()},
            raw.get("query_shape"))


def save_baseline(observed: dict, note: str, archive_previous: bool = True) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if archive_previous and BASELINE_PATH.exists():
        previous = json.loads(BASELINE_PATH.read_text())
        stamp = previous.get("recorded_utc", "unknown").replace(":", "")[:15]
        archive = BASELINE_PATH.with_name(f"scan_baseline.{stamp}.superseded.json")
        archive.write_text(json.dumps(
            {**previous, "superseded_utc": datetime.now(timezone.utc)
             .isoformat(timespec="seconds"),
             "superseded_because": note}, indent=2) + "\n")
    BASELINE_PATH.write_text(json.dumps({
        "note": note,
        "query_shape": query_shape(),
        "recorded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bytes_by_bound_year": {str(k): v for k, v in sorted(observed.items())},
    }, indent=2) + "\n")


AB_TEMPLATE = """\
SELECT type, actor.login AS login, created_at
FROM `githubarchive.month.{month}`
WHERE {filter}
  AND type IN ('PushEvent', 'IssuesEvent', 'ForkEvent')
"""


def do_ab_price(client, month: str, repo: str = "grafana/grafana"):
    """Price ONE month twice: same table, same projection, different filter.

    Isolates the filter column as the cause of the cohort drift. Everything
    else is byte-identical, so whatever the two prices differ by IS the
    difference between reading a variable-length STRING column and a
    fixed-width INTEGER one.

    Worth stating plainly, because it changes what this can prove: BigQuery
    bills on COLUMNS SCANNED, not rows matched. A filter that matches nothing
    costs exactly the same as one that matches everything. So this test can
    confirm the price delta is the filter swap — but it cannot tell you whether
    the new filter finds any rows. Only a probe collection can do that.
    """
    from google.cloud import bigquery
    repo_id = gharchive.repo_id_for(repo)
    variants = {"repo.name": f"repo.name = '{repo}'",
                "repo.id": f"repo.id = {repo_id}"}
    prices = {}
    for label, clause in variants.items():
        sql = AB_TEMPLATE.format(month=month, filter=clause)
        job = client.query(sql, job_config=bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False))
        prices[label] = job.total_bytes_processed
        print(f"  {label:10} {job.total_bytes_processed / 1e9:8.3f} GB")

    name_b, id_b = prices["repo.name"], prices["repo.id"]
    delta = (id_b - name_b) / name_b if name_b else 0
    print(f"\n  month {month}: repo.id is {delta:+.1%} against repo.name")
    baseline, _ = load_baseline()
    era = int(month[:4]) + 1          # a month belongs to the bound year after it
    if era in baseline:
        print(f"  (bound-{era} cohort drift was reported separately; the two "
              "should be the same order of magnitude)")
    print("\n  NOTE: cost is a function of columns scanned, not rows matched. "
          "This isolates the\n  filter swap as the cause of the price change. "
          "It does NOT show the filter finds rows —\n  for that, collect one "
          "project and check pushers_to_date.")
    return 0


def report_skipped():
    """Name what is not being collected, and why. Never a silent omission."""
    skipped = skipped_targets()
    if not skipped:
        return
    baseline, _ = load_baseline()
    saved = sum(baseline.get(bound.year, 0) for _, bound in skipped)
    print(f"skipping {len(skipped)} project(s) with no clone — no git features, "
          f"so never scored:")
    for repo, bound in skipped:
        was = baseline.get(bound.year)
        print(f"  {repo:26} bound {bound.year}"
              + (f"   avoids {was / 1e9:.1f} GB" if was else ""))
    if saved:
        print(f"  avoided {saved / 1e12:.3f} TB (${saved / 1e12 * PRICE_PER_TB:.2f})\n")
    else:
        print()


def do_dry_run(client, only=None, reseed=False):
    """Price every query without billing anything.

    This also resolves the pre-2015 schema question for free: if the early
    monthly tables use a flatter legacy layout, the dry run fails on that
    specific table name and the projection can be branched before a cent is
    spent.
    """
    from google.cloud import bigquery
    todo = targets(only)
    report_skipped()
    measured, failures = {}, []

    for repo, bound in todo:
        query = gharchive.build_query(repo, bound)
        history, window = gharchive.month_split(bound)
        try:
            job = client.query(query, job_config=bigquery.QueryJobConfig(
                dry_run=True, use_query_cache=False))
        except Exception as exc:
            msg = str(exc)[:300]
            failures.append((repo, msg))
            print(f"{repo:26} bound {bound.year}  FAILED  {msg}")
            continue
        measured[repo] = {"bound_year": bound.year,
                          "bytes": job.total_bytes_processed,
                          "history": len(history), "window": len(window)}
        print(f"{repo:26} bound {bound.year}  {len(history):3}h+{len(window):2}w  "
              f"{job.total_bytes_processed / 1e9:8.1f} GB")

    total = sum(m["bytes"] for m in measured.values())
    print(f"\ntotal {total / 1e12:.3f} TB   ${total / 1e12 * PRICE_PER_TB:.2f} "
          f"at ${PRICE_PER_TB}/TB")
    over = total / 1e12 - FREE_TIER_TB
    print(f"free tier {FREE_TIER_TB} TB — "
          + (f"OVER by {over:.3f} TB (${over * PRICE_PER_TB:.2f} billable)"
             if over > 0 else "within allowance"))

    if failures:
        print(f"\nFAILED: {len(failures)} of {len(todo)} queries did not "
              "resolve. If the message names an early monthly table or an "
              "unrecognised field, the pre-2015 tables use the legacy schema "
              "and the projection needs a per-era branch. Nothing was billed.")
        for repo, msg in failures[:3]:
            print(f"  {repo}: {msg}")
        return 1

    return report_projection_check(measured, only=only, reseed=reseed)


def report_projection_check(measured: dict, only=None, reseed=False) -> int:
    """Cohort consistency, then absolute baseline. Neither uses a rate."""
    baseline, baseline_shape = load_baseline()
    shape = query_shape()
    shape_changed = bool(baseline and baseline_shape and baseline_shape != shape)
    if shape_changed:
        # Old counts describe a different query. Never compare against them —
        # with or without --reseed-baseline.
        if reseed:
            print(f"\n  re-seeding: query shape {baseline_shape} -> {shape}; "
                  "previous values will be archived, not compared")
        else:
            print(f"\nBASELINE SHAPE MISMATCH\n"
                  f"  baseline measured against query shape {baseline_shape}\n"
                  f"  this code builds query shape          {shape}\n\n"
                  "  The recorded byte counts describe a different query and "
                  "cannot\n  be compared to this one. Cohort consistency is "
                  "still checked below;\n  the absolute baseline is not.\n\n"
                  "  Re-run with --reseed-baseline once the new shape is "
                  "verified.")
        baseline = {}
    elif baseline and baseline_shape is None:
        print("\n  note: baseline predates query-shape tracking; treating its "
              "values as comparable.")
    by_year = {}
    for repo, m in measured.items():
        by_year.setdefault(m["bound_year"], []).append((repo, m["bytes"]))

    problems = []
    print("\nprojection check — cost is a pure function of bound year")
    print(f"{'bound':>5} {'n':>2} {'GB':>9}  {'vs baseline':>12}  status")
    for year in sorted(by_year):
        cohort = by_year[year]
        sizes = [b for _, b in cohort]
        expected = max(set(sizes), key=sizes.count)      # the cohort's mode

        outliers = [(r, b) for r, b in cohort
                    if abs(b - expected) > expected * COHORT_TOLERANCE]
        for repo, got in outliers:
            problems.append(
                f"{repo}: {got / 1e9:.1f} GB against a bound-{year} cohort of "
                f"{expected / 1e9:.1f} GB — one project deviating from its own "
                "cohort is what a projection regression looks like")

        status, delta = "ok", ""
        if year in baseline:
            drift = (expected - baseline[year]) / baseline[year]
            delta = f"{drift:+7.1%}"
            if abs(drift) > BASELINE_TOLERANCE:
                status = "DRIFT"
                problems.append(
                    f"bound {year}: {expected / 1e9:.1f} GB against a recorded "
                    f"baseline of {baseline[year] / 1e9:.1f} GB ({drift:+.1%}). "
                    "These tables are immutable, so a whole cohort moving means "
                    "the query changed, not the data.")
        else:
            status, delta = "new", "    n/a"
        if outliers:
            status = "COHORT SPLIT"
        print(f"{year:>5} {len(cohort):>2} {expected / 1e9:9.1f}  {delta:>12}  {status}")

    if problems:
        print("\nPROJECTION CHECK FAILED")
        for p in problems:
            print(f"  - {p}")
        print("\nCheck src/gharchive.py WINDOW_PROJECTION and HISTORY_PROJECTION "
              "before spending anything. Nothing was billed.")
        return 1

    if not only:
        observed = {y: max(set(b for _, b in c), key=[b for _, b in c].count)
                    for y, c in by_year.items()}
        existing, _ = load_baseline()
        # Merge, never clobber. A new bound year is recorded on sight; an
        # existing value is only replaced with --reseed-baseline, so a stray
        # invocation cannot quietly redefine what "correct" means.
        if reseed or shape_changed:
            merged, note = observed, (
                f"re-seeded against query shape {shape}; previous values "
                "archived alongside")
        else:
            merged = {**observed, **existing}
            note = "measured by tools/collect_gharchive.py --dry-run"
            protected = {y for y in observed if y in existing}
            if protected:
                print(f"  {len(protected)} existing bound-year baseline(s) left "
                      "unchanged; --reseed-baseline to replace them")
        if merged != existing:
            save_baseline(merged, note)
        try:
            shown = BASELINE_PATH.relative_to(ROOT)
        except ValueError:
            shown = BASELINE_PATH
        print(f"\nbaseline recorded for {len(observed)} bound years -> {shown}")
    return 0


def do_collect(client, force=False, only=None, budget_tb=FREE_TIER_TB):
    """Collect, one project per query, resumable.

    Each project is a single query written to its own cache file the moment it
    returns, so a run killed at project 20 of 25 leaves 19 complete entries and
    a resume rescans none of them. At 0.65 TB against a 1 TB monthly free
    allowance there is room for exactly one full collection per calendar month,
    which is why the budget is tracked and why failures abort rather than
    continuing — a schema error on an early table would otherwise burn the
    month's allowance discovering the same thing 25 times.
    """
    from google.cloud import bigquery
    todo = targets(only)
    report_skipped()
    written = skipped = 0
    billed = 0

    for i, (repo, bound) in enumerate(todo, 1):
        if not force and gharchive.load_extract(repo, bound):
            print(f"[{i:2}/{len(todo)}] cached   {repo}")
            skipped += 1
            continue

        query = gharchive.build_query(repo, bound)
        history, window = gharchive.month_split(bound)
        try:
            job = client.query(query, job_config=bigquery.QueryJobConfig(
                use_query_cache=False))
            rows = [dict(r) for r in job.result()]
        except Exception as exc:
            print(f"\n[{i:2}/{len(todo)}] FAILED   {repo}\n  {type(exc).__name__}: "
                  f"{str(exc)[:400]}", file=sys.stderr)
            print(f"\nAborting. {written} projects are cached and will be "
                  f"skipped on resume; {billed / 1e12:.3f} TB billed so far.\n"
                  "Re-run the same command after fixing the cause — nothing "
                  "already collected is rescanned.", file=sys.stderr)
            return 1

        billed += job.total_bytes_billed or 0
        notes = [f"{len(window)} window tables (4 columns) + {len(history)} "
                 f"history tables (3 columns), enumerated from "
                 f"{gharchive.GHARCHIVE_EPOCH.isoformat()}"]
        completeness = gharchive.window_completeness(bound)
        if not completeness["prior_window_complete"]:
            notes.append("prior 12mo window predates the GHArchive epoch; "
                         "issue_reporter_growth_yoy is withheld, not estimated")
        if bound.year <= 2015:
            notes.append("scan reaches pre-2015 monthly tables; only "
                         "repo.name/actor.login/type/created_at are relied on")

        extract = gharchive.Extract(
            repo=repo, snapshot=bound.isoformat(),
            rows=rows[0] if rows else {},
            query=query,
            query_sha256=hashlib.sha256(query.encode()).hexdigest(),
            extracted_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            bytes_billed=job.total_bytes_billed,
            notes=notes)
        path = gharchive.save_extract(extract)
        written += 1
        print(f"[{i:2}/{len(todo)}] ok       {repo:26} "
              f"{(job.total_bytes_billed or 0) / 1e9:7.1f} GB  "
              f"running {billed / 1e12:.3f} TB  -> {path.name}")

        if billed / 1e12 > budget_tb:
            print(f"\nSTOPPED: {billed / 1e12:.3f} TB billed exceeds the "
                  f"{budget_tb} TB budget. {written} collected, "
                  f"{len(todo) - i} remaining.\n"
                  "Resume next month, or re-run with --budget-tb to override.",
                  file=sys.stderr)
            return 3

    print(f"\nwritten {written}, already cached {skipped}, "
          f"billed {billed / 1e12:.3f} TB")
    return 0


def do_verify():
    """Cache integrity without touching BigQuery."""
    ok = missing = stale = 0
    for repo, snapshot in targets():
        try:
            extract = gharchive.load_extract(repo, snapshot)
        except gharchive.GHArchiveUnavailable as exc:
            print(f"[STALE]   {repo:26} {exc}")
            stale += 1
            continue
        if extract is None:
            print(f"[missing] {repo:26} {gharchive.cache_path(repo, snapshot).name}")
            missing += 1
            continue
        print(f"[ok]      {repo:26} extracted {extract.extracted_utc}")
        ok += 1
    print(f"\nok {ok}, missing {missing}, stale {stale} of {len(targets())}")
    return 0 if (missing == 0 and stale == 0) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="price every query without billing anything")
    ap.add_argument("--verify", action="store_true",
                    help="check cache integrity, no BigQuery")
    ap.add_argument("--force", action="store_true",
                    help="re-query projects that are already cached")
    ap.add_argument("--only", metavar="owner/repo",
                    help="collect or price a single project")
    ap.add_argument("--ab-price", metavar="YYYYMM",
                    help="price one month twice, repo.name vs repo.id, to "
                         "isolate the filter column as the cost difference")
    ap.add_argument("--reseed-baseline", action="store_true",
                    help="record the observed bytes as the new baseline for "
                         "the current query shape, archiving the previous file")
    ap.add_argument("--budget-tb", type=float, default=FREE_TIER_TB,
                    help=f"stop after this much billed (default {FREE_TIER_TB},"
                         " the monthly free allowance)")
    args = ap.parse_args()

    if args.verify:
        return do_verify()

    client, problem = client_or_none()
    if client is None:
        print(f"BigQuery unavailable: {problem}\n", file=sys.stderr)
        print("Scoring will proceed without the GHArchive inputs and will "
              "record them as uncollected. To collect them:\n"
              "  pip install google-cloud-bigquery\n"
              "  gcloud auth application-default login\n"
              "  gcloud config set project <a project with billing>\n"
              "  python3 tools/collect_gharchive.py --dry-run",
              file=sys.stderr)
        return 2

    if args.ab_price:
        return do_ab_price(client, args.ab_price)
    if args.dry_run:
        return do_dry_run(client, only=args.only, reseed=args.reseed_baseline)
    return do_collect(client, force=args.force, only=args.only,
                      budget_tb=args.budget_tb)


if __name__ == "__main__":
    sys.exit(main())
