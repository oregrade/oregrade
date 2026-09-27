# experiment — why oregrade does not score

Archived, not deleted. This is the evidence for the central design decision of
the 1.0 tool: a composite commercial-viability score was specified,
pre-registered, built, and tested, and it did not predict. Keeping it reachable
matters more than keeping it running.

Nothing here is loaded by the CLI.

## The result

`runs/run-002-preregistration.yaml`, committed 2026-08-15 before any comparison
to outcomes, fixed the metric and the bar in advance:

- **primary** AUC separating `commercialized_success` (n=6) from
  `commercialized_modest` (n=7), on backtested-core scores, ties at 0.5
- **threshold** 0.75
- **stop rule** if the threshold is missed, the finding is recorded and
  published; no retuning

`runs/run-003-evaluation.json` is the result:

| | |
|---|---|
| primary AUC | **0.286** |
| threshold | 0.75 — **missed** |
| 95% bootstrap CI | [0.048, 0.619], includes 0.5 |
| pairs ordered correctly | 12 of 42 |
| secondary (success vs failed) | 0.500, exactly chance |
| vintage confound | weak — Spearman 0.10 on the primary subset |

The point estimate is **below chance**, not merely short of the bar. The stop
rule was applied: nothing was retuned afterwards.

## Why it failed

`runs/run-003-diagnostic.txt` decomposes every scored project into per-input
contributions. Two findings:

**Five of eight inputs correlate with the outcome in the wrong direction.** The
only one pointing the right way is the managed-service surface table, which is
a hand-authored constant rather than a measurement of the project.

**The input that drives the ranking hardest is one of the wrong-way ones.**
Distinct issue reporters correlates +0.72 with the final score and −0.31 with
the label.

The contributor-concentration inversion is the clearest single error. Fund mode
scores concentration as key-man risk, but in this corpus the successes are
*more* concentrated than the modest outcomes (median top-1 commit share 0.323
against 0.194).

## What the corpus could and could not test

It could test scoring. It structurally **could not test the gates**: every
entry was selected for being a resolved commercial open source company, so
licences, liveness, commercial surfaces and entities are present by
construction. No project ever failed a gate. The gate architecture is
unfalsified, not validated, and the 1.0 tool reports those checks as fields
rather than as pass/fail for exactly that reason.

## Methodology limitations, self-reported

- **The label split happened in the same session as the scorer build.**
  Splitting `labels.yaml` required reading it, and that occurred in the session
  that wrote the scoring code. Nothing was tuned against it and the structural
  guard meant the code never read it — but a person having seen the labels while
  writing thresholds is the exposure the protocol exists to prevent, and "I
  didn't use it" is the claim it is designed not to have to trust.
- **Coverage-based exclusion may correlate with outcome.** The 0.30 floor was
  pre-registered blind; one project (sourcegraph) was excluded, for being
  uncloneable rather than for coverage.

## Bugs found along the way

Each of these was silent, and each is fixed in the shipping tool:

1. **Licence detector, five distinct defects** — MPL-2.0 read as GPL (the MPL
   text names every GNU licence), GPL-3.0 read as AGPL (§13 is headed "Use with
   the GNU Affero General Public License"), Meteor read as Apache (its LICENSE
   appends its dependencies' texts), RethinkDB read as unlicensed (its grant is
   in `COPYRIGHT`), MongoDB matched nothing (it ships `GNU-AGPL-3.0.txt`, not
   `LICENSE`).
2. **Timezone-dependent revision resolution** — `--before=2014-12-31` resolves
   in the runner's local timezone and returned three different commits under
   three timezones.
3. **Snapshot boundary dropped two days** — an inclusive date needs the
   exclusive bound that follows it.
4. **`repo.name` history lookups** — GHArchive stores the name as of the event,
   so eight of twenty-four projects matched zero rows. The zeros were legal
   values and nothing objected. Renames cluster around commercialisation, so the
   missing data was not missing at random.
5. **Window-bounded committer set** — "never pushed as of the snapshot"
   implemented as "never pushed in the last two years".

## Layout

```
rubric/default.yaml              the rubric at 0.3.0, uncalibrated, never re-tuned
benchmarks/projects.yaml         scoring inputs, 25 projects
benchmarks/outcomes.yaml         resolved labels — evaluate.py was the only reader
benchmarks/repo_ids.yaml         rename-invariant ids
runs/README.md                   the run ledger and what each run showed
runs/run-00{1,2,3}.json          three scoring runs; only run-003 was confirmatory
runs/run-002-preregistration.yaml  committed before outcomes were opened
runs/run-003-evaluation.json     the result
runs/run-003-diagnostic.txt      per-input decomposition of the failure
run_scoring.py, evaluate.py      the scorer and the single permitted label reader
tools/                           corpus collection and analysis scripts
tests/                           the scoring-era tests, including the leak guard
```

The code here imports modules that no longer exist at those paths. It is a
record, not a runnable artefact.
