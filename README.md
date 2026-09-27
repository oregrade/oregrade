# oregrade

A point-in-time fact sheet for one open source repository.

```
oregrade dossier grafana/grafana --at 2017-12-31
```

One repository, one date, a structured fact sheet with provenance on every line
and no opinion anywhere in it. Licence and its scope, rights position,
deployment surface, activity, commercial evidence, and — optionally — adoption.

**This does not score anything.** There is no composite, no ranking, no
leaderboard. A score is only useful if it predicts, and this one was built,
pre-registered and tested, and it didn't: AUC 0.286 against a pre-registered
0.75, with five of eight inputs correlating with outcome in the wrong
direction. Shipping it anyway would be the exact failure the writeup documents
in other tools. The evidence is in [`experiment/`](experiment/README.md) and
stays reachable from here.

What survived is everything that was retrieval rather than prediction. Those
are facts about a repository at a date. They are useful whether or not they
correlate with anything, and assembling them by hand is slow and error-prone —
which is the actual product.

## Install

```bash
pip install -e .
oregrade dossier grafana/grafana --at 2017-12-31
```

Or without installing:

```bash
./oregrade dossier grafana/grafana --at 2017-12-31
```

## Run modes

| mode | needs | gives |
|---|---|---|
| default | git, network for the clone | everything below except adoption |
| `--gharchive` | BigQuery credentials, costs money | the adoption block |
| `--github` | optional token | present-day metadata, never point-in-time |
| `--offline` | an existing clone | no network at all |

**The default is git only.** No API keys, no quota, no bill. That property is
what makes the tool adoptable and it is not going to become optional.

Absent a source, the affected block reports `unavailable` and says why. **It
never reports zero.** A zero that means "not collected" is the bug that cost
this project an entire GHArchive collection.

## Output

`--format yaml` (default), `json`, or `markdown`.

```yaml
repo: grafana/grafana
as_of: '2017-12-31'
resolved_at: '2018-01-01T00:00:00+00:00'      # --at is inclusive
repo_id: 15111821                              # rename-invariant
licence:
  spdx: Apache-2.0
  file: LICENSE.md
  confidence: high
  scope: root_only                             # 73 vendored licences ignored
  stability: {status: stable, checked: [-7d, +7d]}
rights:
  copyright_holders_estimate: 685              # distinct commit-author emails
  cla_detected: false
  foundation_owned: false
surface:
  classification: server
  method: heuristic_keyword
  inferred: true                               # the one inferred field
activity:
  commits_to_date: 12775
  top1_commit_share: 0.3767
  momentum_90d: 1.7415
  sponsoring_email_domain: grafana.com
adoption:                                      # only with --gharchive
  distinct_issue_reporters_12mo: 1489
  caveat: issue_tracker_on_github
```

## Three things it gets right that most tools don't

**UTC-pinned revision resolution.** `git rev-list -1 --before=2014-12-31 HEAD`
resolves a bare date in the *runner's local timezone*. On grafana/grafana that
returns three different commits under EDT, UTC and Asia/Tokyo, with three
different root trees. Every bound here carries an explicit `+00:00`.

**Root-only licence detection.** A recursive tree walk finds vendored
dependency licences. hashicorp/terraform at 2016 has 164 licence files, 163 of
them under `vendor/`. The dossier reports the root licence and tells you how
many it ignored.

**Rename-invariant identity.** GitHub records events under the name a
repository had at the time. Any history lookup keyed on today's name silently
misses everything before a rename — and renames cluster around
commercialisation. Everything here keys on `repo.id`.

## Honest limits

- **Surface classification is inferred**, by keyword heuristic, and labelled as
  such on every line it appears. It quotes the text that triggered it and
  returns `unknown` rather than guessing. It is the weakest field in the
  dossier and the obvious place for a real classifier.
- **Trademark is not implemented.** It needs a USPTO or WIPO lookup; GitHub
  does not carry trademark data. The field says `unavailable` and why.
- **Foundation ownership is an org-prefix match**, so a project donated to a
  foundation *after* the date will not show — etcd joined the CNCF in 2018 and
  a 2016 dossier must not say so.
- **`copyright_holders_estimate` is distinct commit-author emails.** An
  over-count where one person used several addresses, an under-count where an
  employer holds the copyright of many.
- **The gates were never falsified.** The benchmark corpus was all resolved
  commercial companies, so nothing ever failed one. Those checks ship as fields,
  never as pass/fail.

## Layout

```
src/pitgit/      point-in-time git toolkit — no oregrade concepts inside
src/oregrade/    dossier assembly, surface, commercial, adoption, CLI
experiment/      the scoring experiment and why the tool doesn't score
tests/
```

`pitgit` reconstructs repository state at any historical date with no API
dependency. It is kept free of oregrade imports so it can be split out as its
own package — a move rather than a rewrite.

## Not in scope

No maintainer contact data, permanently. Harvesting contributor emails from
commit history is a privacy exposure and the fastest way to make the open
source community hostile to a tool that depends on their goodwill. Commit
emails are read transiently to count and group contributors; none is ever
written to output.

No composite score, ranking or leaderboard. No live monitoring or alerting.
