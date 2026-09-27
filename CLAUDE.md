# oregrade — Claude Code working brief

A point-in-time fact sheet for one open source repository. One repo, one date,
structured facts with provenance on every line and no opinion anywhere in it.

```
oregrade dossier grafana/grafana --at 2017-12-31
```

> **This brief replaced the scoring-era one at 1.0.** The previous version is
> kept verbatim at `experiment/CLAUDE.md.scoring-era`. It described a rubric and
> a composite score that no longer exist, and a brief that contradicts the code
> is worse than no brief.

---

## THE HARD RULE

**No score. No ranking. No leaderboard. No composite of any kind.**

This is not a stylistic preference, it is the finding. A commercial-viability
score was specified, pre-registered, built and tested against 25 resolved
outcomes. It returned AUC 0.286 against a pre-registered threshold of 0.75 —
below chance — with five of eight inputs correlating with the outcome in the
wrong direction. The stop rule was applied and nothing was retuned.

The evidence lives in `experiment/` and must stay reachable from the repo root.
Do not revive the scorer, do not add a "provisional" score, do not average
fields into an index. If a score returns, it returns behind a new
pre-registration with its own threshold committed in advance.

The related rule the experiment enforced structurally — that nothing in `src/`
may read `benchmarks/outcomes.yaml` — is now moot, because there is nothing to
tune and the label file is archived. It is worth understanding before touching
`experiment/`: see `experiment/CLAUDE.md.scoring-era`.

---

## What the tool must never do

- **Report zero for something it did not measure.** Absent a source, the block
  says `unavailable` and why. A zero that meant "not collected" cost this
  project an entire GHArchive collection, and the zeros were legal values that
  nothing objected to until they were cross-checked against commit counts.
- **Present an inferred field as a measured one.** Surface classification is a
  keyword heuristic. It is labelled `inferred: true`, carries the text that
  triggered it, and returns `unknown` rather than guessing.
- **Present present-day data as point-in-time.** `--github` output is labelled
  `as_of: today` with an explicit warning. A 2014 dossier reporting today's org
  type as a 2014 fact is the error the whole tool exists to avoid.
- **Handle maintainer contact data.** Permanently out. Commit emails are read
  transiently to count and group contributors; none is ever written to output.

## Three fixes that are load-bearing

Each was a real bug, each is tested, and each is easy to undo by accident.

1. **UTC-pinned revision resolution.** `--before=<bare date>` resolves in the
   runner's local timezone; grafana returns three different commits under three
   timezones. Every bound carries `+00:00`; every git subprocess runs `TZ=UTC`.
2. **Calendar window arithmetic.** 365-day windows drift a day per leap year,
   so projects of different vintages get windows of different lengths.
3. **Root-only licence detection.** terraform at 2016 has 164 licence files,
   163 under `vendor/`. Never walk the tree recursively to find a licence.

`--at DATE` is **inclusive** of that day; the bound is the instant after it.
Getting this wrong silently drops the last day of the range.

## Layout

```
src/pitgit/      point-in-time git toolkit. NO oregrade imports — it is meant to
                 be split out as its own package, so keep the boundary clean.
src/oregrade/    dossier assembly, surface, rights, commercial, adoption, CLI
experiment/      the scoring experiment. Archived, never loaded by the CLI.
tests/
```

## Out of scope

- any composite score, ranking or leaderboard — see above
- maintainer contact data, permanently
- live monitoring or alerting, until someone other than the author runs the CLI
  twice
- web UI, ecosystem-wide crawling

## Known gaps, stated in the README and not to be quietly closed

Trademark lookup is unimplemented and says so. Foundation ownership is an
org-prefix match and will miss a post-dated donation. `copyright_holders_estimate`
is distinct commit-author emails, which over- and under-counts in known ways.
The gates were never falsified because the benchmark corpus could not falsify
them. If any of these is fixed, fix the caveat too.
