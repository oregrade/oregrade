# Superseded: the repo.name collection

24 extracts, 1.174 TB billed, collected 2026-08-16. Retained as the record of a
real defect, never read by anything.

These queries filtered `repo.name = '<current name>'`. GHArchive stores the repo
name as of the event, so every project renamed between its snapshot and today
matched **zero events**: elasticsearch, redis, chef, etcd-io, mattermost,
dbt-core, owncloud and rethinkdb — eight of twenty-four, each with five figures
of commits in its clone.

The zeros were legal values. They scored as absent adoption and nothing
objected. The bias was not random either: renames cluster around
commercialisation (opscode → chef, fishtown-analytics → dbt-labs, coreos →
etcd-io), so the missing rows correlate with the thing being predicted.

Replaced by queries filtering `repo.id`, which is invariant across renames, and
by the cross-source guard in `features.gharchive_consistency`.
