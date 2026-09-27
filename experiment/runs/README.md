# Run ledger

Every scoring run is recorded here with what changed and why. Runs are never
edited after the fact except to add status metadata; a changed rubric produces a
new run file, not a revised one.

| run | rubric | status | coverage | n scored | note |
|-----|--------|--------|----------|----------|------|
| `gate_run_001.json` | pre-0.1.0 | superseded | — | 34 | gates only, form mode, flat layout |
| `run-001.json` | 0.1.0 | **exploratory, permanent** | 0.335 | 24 | first blind run, git-only |
| `run-002.json` | 0.2.0 | **exploratory** | 0.320 | 24 | post-run-001 decisions |
| `run-003.json` | 0.3.0 | **exploratory** | 0.290 | 24 | split rubric; GHArchive uncollected, all 25 below the floor |
| `run-004` | — | not started | — | — | inverted corpus, gate falsification |

`run-002-preregistration.yaml` is committed and closed. It governs the
confirmatory scoring run — whichever run first satisfies it — and must not be
edited. `target_coverage` 0.36 and `threshold` 0.75 AUC are fixed.

**Numbering note.** The post-run-001 memo assigned `run-003` to the inverted
gate corpus; the post-run-002 instruction assigned it to the confirmatory
scoring run. The later instruction wins, so the inverted corpus is now
`run-004`. Flagged because the two documents disagree in writing.

## Why run-003 is exploratory

Coverage is 0.290 per project, below the pre-registered `project_floor` of 0.30.
Under the coverage criterion that excludes all 25 projects, which exceeds
`max_exclusions` of 3, so **the run fails rather than the projects** — the
criterion's own stated behaviour.

The cause is single and known: the GHArchive adoption inputs are not collected.
This machine has no BigQuery client, no gcloud, and no credentials. The
collector is written, tested and cached-by-design; it has never been run.

Coverage arithmetic, all from `rubric/default.yaml`:

| | adoption | maintainer | substitution | category | total |
|---|---|---|---|---|---|
| dimension weight | 30 | 30 | 25 | 15 | 100 |
| core share | 0.80 | 0.60 | 0.50 | 0.00 | **54.5** |
| supplied, git only | 0.10 | 0.45 | 0.50 | 0.00 | **29.0** |
| supplied, with GHArchive | 0.80 | 0.60 | 0.50 | 0.00 | **54.5** |

So collecting GHArchive takes coverage from 0.290 to 0.545 and clears the 0.36
bar with margin — which is what the decision memo predicted and why the bar was
not raised. `tests/test_scoring_contract.py::test_a_populated_cache_makes_the_run_confirmatory`
proves the plumbing end to end against a fixture cache.

Note that git-only coverage *fell* from run-002's 0.320 to 0.290. That is not a
regression: `production_mentions` dropped from 0.20 to 0.10 of the adoption
dimension when the GHArchive inputs took the weight. The rubric got better and
the uncollected run got worse, which is the correct direction for both.

## Snapshot boundaries are pinned to UTC

Found while fixing the GHArchive query, and it affected every run before this
one. `git rev-list -1 --before=2014-12-31 HEAD` resolves a bare date in the
**runner's local timezone**. On grafana/grafana that returns three different
revisions:

| TZ | revision at "2014-12-31" |
|----|--------------------------|
| America/New_York | `ad4cf37` |
| UTC | `ad4cf37` |
| Asia/Tokyo | `2b02c87` |
| explicit `+00:00` | `8268c65` |

Different revisions mean different root trees and so, potentially, different
licences — on a tool whose entire claim is point-in-time reconstruction. All
date bounds now carry an explicit `T00:00:00+00:00`, and every git subprocess
runs with `TZ=UTC` so that `--date=short-local` buckets commits by UTC day
rather than by whatever timezone each committer was in.

`tests/test_gitpit.py` asserts identical results under four runner timezones.
run-001 and run-002 were computed without this and their feature values are
machine-dependent to within about a day at each boundary — another reason both
are marked permanently exploratory.

**Snapshot semantics, changed after run-003's first build.** `fund_snapshot:
2017` now resolves to `2018-01-01T00:00:00+00:00` EXCLUSIVE, so the snapshot
includes all of 2017. The previous reading — 2017-12-31T00:00:00Z — silently
dropped 30 and 31 December. Changed while every downstream number is
exploratory and nothing is calibrated; after a confirmatory run it would have
invalidated it.

Effect on the corpus: 15 of 24 projects pick up between 1 and 6 additional
commits and land on a different revision. Nothing large, but it moves the root
tree for those projects, which is exactly the kind of silent drift a licence
detector notices before a human does.

A consequence worth having: with a 1 January bound and exact calendar-year
arithmetic (`minus_years`, not 365 days), the trailing 12-month window is
precisely calendar 2017 and the prior window precisely calendar 2016, with no
leap-year drift between projects of different vintages.

## Reproducibility of the external source

Everything except adoption comes from a local clone and recomputes from cold
with git alone. GHArchive does not, so:

- scoring reads `cache/gharchive/` and never calls BigQuery;
- each cache entry carries the exact SQL, its SHA-256, the extraction timestamp
  and the bytes billed;
- the cache key embeds the query hash, so editing the query invalidates the
  cache instead of silently reusing rows from a different question;
- entries are written atomically (temp file then rename), so a truncated cache
  file cannot exist;
- `tools/collect_gharchive.py --verify` re-derives every key and fails on drift.

### Collection is resumable per project

One project is one query written to its own cache file the moment it returns.
A run killed at project 20 of 25 leaves 19 complete entries and a resume
rescans none of them. This matters because the whole collection is roughly
0.65 TB against a **1 TB monthly free allowance** — one complete run per
calendar month, so a resume that rescanned would cost a month.

Consequently the collector **aborts on the first failure** rather than
continuing. A schema error on an early monthly table would otherwise burn the
month's allowance discovering the same thing twenty-five times. `--budget-tb`
stops the run before it crosses the free tier; `--only owner/repo` collects one
project for a cheap first test.

### Scan volume

Two scans per project, because they need different columns:

| scan | tables | columns |
|------|--------|---------|
| window months (the two calendar years) | 600 | `type`, `actor.login`, `created_at`, + `repo.name` in WHERE |
| history months (epoch → window start) | 1151 | `actor.login`, + `repo.name`, `type` in WHERE |

History months exist only to build the committer set, and every event in them
precedes the snapshot by construction, so `created_at` is never read.

**Measured by dry run: 1.174 TB across the 24 collectable projects, about
$7.34** at $6.25/TB, roughly $1.09 of it beyond the 1 TB monthly free
allowance. Collect with `--budget-tb 1.2`.

The first full dry run priced 1.296 TB because it included
`sourcegraph/sourcegraph` at 121.7 GB. That project is `clone_unavailable` — no
clone, so no git features, so it is excluded from every run whatever GHArchive
returns. The collector now filters on `features.clone_available`, the same
predicate the scorer uses to decide `clone_status`, so the two cannot form
different opinions about what is worth paying for. The skip is printed with its
avoided cost rather than being silent, and a test asserts that no query text
ever mentions an uncloneable project.

Per-project cost by bound year, from the measured baseline:

| bound | tables | GB | projects |
|-------|--------|-----|----------|
| 2014 | 35 | 6.7 | 3 |
| 2015 | 47 | 12.6 | 5 |
| 2016 | 59 | 24.1 | 4 |
| 2017 | 71 | 41.7 | 3 |
| 2018 | 83 | 63.6 | 5 |
| 2019 | 95 | 88.9 | 1 |
| 2020 | 107 | 121.7 | 1 (+1 skipped) |
| 2021 | 119 | 170.4 | 2 |

That is well above the 0.60–0.72 TB estimated here before the dry run. The
estimate extrapolated from a single measured month at 411 MB and assumed
monthly tables were roughly uniform in size. They are not: cost per project
runs from 6.7 GB at bound 2014 (35 tables) to 170.4 GB at bound 2021 (119
tables), a 25x spread that a per-month average cannot represent. The estimate
was wrong in the direction that costs money, which is the direction to be
wrong-and-checked rather than wrong-and-trusted.

Not bounding the history window to bring it down — that reintroduces the
window-bounded committer bug, and it would also split committers and reporters
across two identity namespaces, forcing an email-to-login join that degrades
worst in exactly the pre-2015 era the deep scans cover.

### The repo.name collection was invalid

The first full collection — 24 projects, 1.174 TB, no query failures — filtered
`repo.name = '<current name>'`. **GHArchive stores the repo name as of the
event**, so every project renamed between its snapshot and today matched *zero*
events. Eight of twenty-four:

| project | commits in clone | GHArchive pushers |
|---|---|---|
| elastic/elasticsearch | 12,173 | 0 |
| redis/redis | 5,498 | 0 |
| chef/chef | 12,737 | 0 |
| etcd-io/etcd | 10,392 | 0 |
| mattermost/mattermost | 10,576 | 0 |
| dbt-labs/dbt-core | 4,413 | 0 |
| owncloud/core | 22,110 | 0 |
| rethinkdb/rethinkdb | 28,856 | 0 |

Zeros are legal values. They scored as absent adoption, no stage objected, and
the run would have reached evaluation looking complete. Worse, the bias is not
random: renames cluster around commercialisation — `opscode/chef` → `chef/chef`,
`fishtown-analytics/dbt` → `dbt-labs/dbt-core`, `coreos/etcd` → `etcd-io/etcd`,
`mattermost/platform` → `mattermost/mattermost` — so the missing rows correlate
with the thing being predicted.

Two fixes:

- **Queries filter `repo.id`**, which is permanent across renames. Ids are
  resolved once by `tools/resolve_repo_ids.py` against the GitHub API (which
  follows renames: `docker/docker` 301s to `moby/moby` and yields the same id
  the 2015 events carry) and committed to `benchmarks/repo_ids.yaml`.
- **A cross-source guard**, `features.gharchive_consistency`. Two independent
  records of the same repository must agree on whether anyone worked on it, so
  commits-in-clone > 0 with pushers-in-GHArchive == 0 is flagged as impossible
  and surfaced as a run-level blocker. One comparison, and it catches a whole
  class of silently-wrong external join.

The superseded extracts are kept under
`cache/gharchive/_superseded_repo_name_query/` as the record of the defect, and
are never read.

### Detecting a projection regression

The first version of the check compared GB-per-month against a flat 411 MB and
flagged anything above 3x. It produced two false positives — dbt-core and
directus, the two deepest scans — because GHArchive monthly tables grow by more
than an order of magnitude across the period, so a scan reaching back to 2011
averages a completely different rate from one starting in 2019. The flat
threshold was measuring snapshot depth, not projection width.

Scan cost is a **pure function of the bound year**: projects sharing one scan
exactly the same set of monthly tables, and those tables are historical and
immutable — `201403` will never change size. So the check uses two comparisons
and no rate at all:

1. **Cohort.** Projects in the same bound year must price identically, within
   1%. One project deviating from its own cohort is what a projection
   regression actually looks like.
2. **Baseline.** Each bound year has a recorded absolute expectation in
   `cache/gharchive/scan_baseline.json`, seeded with the two measured cohorts
   and extended automatically. This catches a regression that hits every
   project at once — adding `payload` — which the cohort check cannot see
   because it scales every cohort together.

A whole cohort moving against its baseline means the query changed, not the
data.

## Why run-001 is permanently exploratory

Coverage was 0.335, so evaluating it would test roughly `maintainer_profile`
alone rather than the rubric. It also predates the rubric 0.2.0 corrections, the
surface table and the entity date fix. It stays on disk as the record of the
first blind run and must never be cited as a result.

## Why run-002 is also exploratory

Coverage 0.320 against a target of 0.36, and it predates the rubric split. Its
report also contained an error: it claimed adoption could not be improved by
GHArchive. It can — `IssuesEvent` and `ForkEvent` carry actor identity from
2011, so a person who files a bug and never commits is a reconstructable user.
That correction is what rubric 0.3.0 implements.

The bar cannot be cleared by adding dependent counts, download trends or cloud
catalogue lookups. Those are current-state only, and reading current state onto
a 2014 snapshot is the specific error the whole point-in-time design exists to
avoid. The pre-registration closes that door explicitly.

## The gates are unfalsified, not validated

**No project has ever failed a gate in this corpus.** In run-002, 24 of 25
cleared all four evaluated gates; the twenty-fifth could not be cloned. Gate 2
requires 3 commits in the trailing 90 days against a corpus minimum of 80 — two
and a half orders of magnitude of headroom.

This is a property of the benchmark, not evidence about the gates. Every entry
was selected for being a resolved commercial open source company, so licences,
liveness, commercial surfaces and entities are present by construction. The
corpus can test scoring. It structurally cannot test gating, and no threshold
change fixes that.

The gate architecture is the central claim of the project and it currently has
no evidence behind it. Nothing published may imply otherwise.

Testing it needs an inverted corpus: a wide, unselected sample of open source
projects where most are expected to fail — drawn from, say, repos above a low
star floor in a given year, without conditioning on outcome. That is `run-003`
and a separate piece of work. It must not be attempted inside the outcome
corpus.

## What is tested

- **Licence detection** — `tests/test_licence.py`, against fixture repos built
  from real licence texts and against the real clones at their snapshot dates.
  Five distinct misclassifications were found and fixed after run-001.
- **Scoring contract** — `tests/test_scoring_contract.py`: no weight
  redistribution, no bare score, no silent default for an unmapped surface.
- **Gates and the entity date fix** — `tests/test_gates.py`.
- **The blind separation** — `tests/test_no_outcome_leak.py`: nothing under
  `src/` may reference the outcome file or name a result label.
