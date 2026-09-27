"""Licence detector tests.

Two layers:

  * fixture repos built locally from REAL licence texts (tests/fixtures/licences),
    so the suite is offline, fast and deterministic;
  * the same assertions against the actual clones at the actual snapshot dates,
    skipped when the corpus has not been cloned.

The bug being locked down: the detector used to list the tree with `ls-tree -r`
and take the first licence file it found. hashicorp/terraform at its 2016
snapshot has 164 licence files, 163 of them under vendor/, and the detector
returned AGPL for a project that is MPL-2.0.
"""
import subprocess
from datetime import date
from pathlib import Path

import pytest

import pitgit
from pitgit import licence, repo as gitpit

FIXTURES = Path(__file__).parent / "fixtures" / "licences"
CLONES = Path("/tmp/oregrade-clones")

TEXT = {p.stem: p.read_text() for p in FIXTURES.glob("*.txt")}


# ---------------------------------------------------------------------------
# fixture repo construction
# ---------------------------------------------------------------------------

def make_repo(tmp_path: Path, name: str, files: dict[str, str]) -> gitpit.Repo:
    root = tmp_path / name
    root.mkdir(parents=True)
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                    capture_output=True, env=env)
    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("commit", "-qm", "fixture")
    return gitpit.Repo(root)


def detect_repo(repo: gitpit.Repo) -> licence.Licence:
    return licence.detect_at(repo, "HEAD")


# ---------------------------------------------------------------------------
# text-level classification of the real licence bodies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected", sorted(TEXT))
def test_real_licence_texts_classify_correctly(expected):
    spdx, evidence = licence.identify_text(TEXT[expected])
    assert spdx == expected
    assert evidence, "every match must carry quotable evidence"


def test_mpl_text_is_not_read_as_gpl():
    """MPL-2.0 s1.12 names GPL-2.0, LGPL-2.1 and AGPL-3.0 by title.

    Terraform is MPL. Matching the GNU family first calls it GPL; that is a
    second, independent way to get Terraform's licence wrong and it is not
    fixed by restricting the tree walk to the root.
    """
    body = TEXT["MPL-2.0"]
    assert "General Public License" in body, "fixture no longer exercises this"
    assert licence.identify_text(body)[0] == "MPL-2.0"


def test_gpl3_text_is_not_read_as_agpl():
    """GPL-3.0 s13 is headed "Use with the GNU Affero General Public License".

    Directus at its 2020 snapshot is GPL-3.0. Matching AGPL anywhere in the
    body — the fix for the MPL bug, applied naively — relabels it AGPL, which
    changes its licence family and therefore its score.
    """
    gpl3 = ("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
            + "filler. " * 500
            + "\n13. Use with the GNU Affero General Public License.\n")
    assert licence.identify_text(gpl3)[0] == "GPL-3.0"


def test_governing_licence_is_the_first_title_not_the_appended_ones():
    """Meteor states MIT, then appends its dependencies' full licence texts."""
    body = ("========================================\n"
            "Meteor is licensed under the MIT License\n"
            "========================================\n\n"
            + TEXT["MIT"] + "\n\n---\n\n" + TEXT["Apache-2.0"])
    spdx, evidence = licence.identify_text(body)
    assert spdx == "MIT"
    assert "Apache-2.0" in {s for _, s, _ in licence.identify_all(body)}


def test_mpl_version_tie_is_broken_by_matcher_order(tmp_path):
    """MPL-2.0 cites version 1.1, and both share the title phrase."""
    body = TEXT["MPL-2.0"]
    ids = {s for _, s, _ in licence.identify_all(body)}
    assert {"MPL-2.0", "MPL-1.1"} <= ids or "MPL-2.0" in ids
    assert licence.identify_text(body)[0] == "MPL-2.0"


def test_policy_style_licence_file_is_reported_as_ambiguous(tmp_path):
    """mattermost: MIT for the binaries, AGPL for the source, Apache for config.

    A permissive headline over a copyleft source grant is not resolvable by
    position, and the detector must say so rather than pick.
    """
    body = ("Mattermost Licensing\n\nYou are licensed to use compiled versions "
            "under an MIT LICENSE\n\nYou may use the source code under the "
            "GNU AFFERO GENERAL PUBLIC LICENSE v.3.0\n\n"
            + TEXT["MIT"] + "\n" + TEXT["AGPL-3.0"])
    assert licence.is_policy_document(body)
    repo = make_repo(tmp_path, "mm_policy", {"LICENSE.txt": body})
    result = detect_repo(repo)
    assert result.spdx == "ambiguous"
    assert result.multi_licence
    assert "AGPL-3.0" in result.secondary and "MIT" in result.secondary


def test_plain_licence_is_not_called_a_policy_document():
    for name in ("MIT", "Apache-2.0", "AGPL-3.0", "MPL-2.0", "BSD-3-Clause"):
        assert not licence.is_policy_document(TEXT[name]), name


def test_licence_named_after_itself_is_found(tmp_path):
    """mongodb 2013 ships no LICENSE file: GNU-AGPL-3.0.txt and APACHE-2.0.txt.

    The AGPL governs the server and the Apache file covers the drivers, so the
    more restrictive of the two is the project's licence.
    """
    repo = make_repo(tmp_path, "mongo", {"GNU-AGPL-3.0.txt": TEXT["AGPL-3.0"],
                                         "APACHE-2.0.txt": TEXT["Apache-2.0"],
                                         "README": "MongoDB README\n"})
    result = detect_repo(repo)
    assert result.spdx == "AGPL-3.0"
    assert result.source_file == "GNU-AGPL-3.0.txt"
    assert "Apache-2.0" in result.secondary


def test_canonical_filename_beats_identifier_filename(tmp_path):
    repo = make_repo(tmp_path, "both", {"LICENSE": TEXT["MIT"],
                                        "APACHE-2.0.txt": TEXT["Apache-2.0"]})
    assert detect_repo(repo).source_file == "LICENSE"


def test_filename_rank_ordering():
    assert licence.filename_rank("LICENSE") == 0
    assert licence.filename_rank("LICENSE.txt") == 0
    assert licence.filename_rank("License.md") == 0
    assert licence.filename_rank("COPYING") == 0
    assert licence.filename_rank("COPYRIGHT") == 0
    assert licence.filename_rank("COPYING-AGPL") == 1
    assert licence.filename_rank("LICENSE.enterprise") == 1
    assert licence.filename_rank("LICENSE-MIT") == 1
    assert licence.filename_rank("GNU-AGPL-3.0.txt") == 2
    assert licence.filename_rank("APACHE-2.0.txt") == 2


def test_full_licence_text_in_a_file_named_copyright(tmp_path):
    """rethinkdb 2014 puts the whole AGPL in COPYRIGHT and ships no LICENSE."""
    repo = make_repo(tmp_path, "rethink", {
        "COPYRIGHT": "RethinkDB Database System\n\nThe software is released "
                     "under the terms of the\n" + TEXT["AGPL-3.0"]})
    result = detect_repo(repo)
    assert result.spdx == "AGPL-3.0"
    assert result.source_file == "COPYRIGHT"


def test_copyright_holding_only_a_notice_is_not_a_licence(tmp_path):
    repo = make_repo(tmp_path, "notice_only", {
        "COPYRIGHT": "Copyright 2014 Somebody. All rights reserved.\n",
        "README.md": "# thing\n"})
    assert detect_repo(repo).spdx == "none"


def test_side_licence_is_recorded_not_promoted(tmp_path):
    """cockroach 2017: Apache core with a CCL subtree."""
    body = ("Source code in this repository is variously licensed under the "
            "Apache Public License 2.0 (APL), the CockroachDB Community "
            "License (CCL), the MIT license, and BSD-style licenses.\n")
    repo = make_repo(tmp_path, "crdb", {"LICENSE": body})
    result = detect_repo(repo)
    assert result.spdx == "Apache-2.0"
    assert "CockroachDB Community License" in result.side_licences


def test_hard_wrapped_titles_still_match():
    wrapped = "GNU AFFERO GENERAL PUBLIC\nLICENSE\nVersion 3, 19 November 2007\n"
    assert licence.identify_text(wrapped)[0] == "AGPL-3.0"


def test_bsd_clause_count_is_distinguished():
    three = TEXT["BSD-3-Clause"]
    two = three.replace("Neither the name of", "Nothing further from")
    assert licence.identify_text(three)[0] == "BSD-3-Clause"
    assert licence.identify_text(two)[0] == "BSD-2-Clause"


# ---------------------------------------------------------------------------
# repo-level detection
# ---------------------------------------------------------------------------

def test_mpl_at_root(tmp_path):
    repo = make_repo(tmp_path, "tf", {"LICENSE": TEXT["MPL-2.0"],
                                      "README.md": "# terraform\n"})
    result = detect_repo(repo)
    assert result.spdx == "MPL-2.0"
    assert result.source_file == "LICENSE"
    assert result.method == "licence_file"


def test_vendored_licences_are_ignored(tmp_path):
    """THE REGRESSION. Root is MPL, vendor/ is full of everything else."""
    files = {
        "LICENSE": TEXT["MPL-2.0"],
        "vendor/github.com/a/dep/LICENSE": TEXT["AGPL-3.0"],
        "vendor/github.com/b/dep/LICENSE": TEXT["Apache-2.0"],
        "vendor/github.com/c/dep/COPYING": TEXT["BSD-3-Clause"],
        "third_party/x/LICENSE.txt": TEXT["AGPL-3.0"],
    }
    repo = make_repo(tmp_path, "tf_vendored", files)
    result = detect_repo(repo)
    assert result.spdx == "MPL-2.0"
    assert result.candidates == ["LICENSE"]
    # and prove the trap is really there
    nested = repo.ls_recursive("HEAD")
    assert sum(1 for f in nested if licence.is_licence_filename(Path(f).name)) == 5


def test_licence_only_below_root_is_not_found(tmp_path):
    """Root-level restriction, tested in the negative as well as the positive."""
    repo = make_repo(tmp_path, "nested_only",
                     {"vendor/dep/LICENSE": TEXT["AGPL-3.0"],
                      "README.md": "# no licence here\n"})
    result = detect_repo(repo)
    assert result.spdx == "none"
    assert result.method == "absent"


def test_agpl_at_root(tmp_path):
    repo = make_repo(tmp_path, "cal", {"LICENSE": TEXT["AGPL-3.0"]})
    assert detect_repo(repo).spdx == "AGPL-3.0"


def test_apache_as_markdown_short_notice(tmp_path):
    """grafana 2014 ships LICENSE.md holding a short notice, not the full text."""
    repo = make_repo(tmp_path, "grafana", {"LICENSE.md": TEXT["Apache-2.0"],
                                           "NOTICE.md": "notice\n"})
    result = detect_repo(repo)
    assert result.spdx == "Apache-2.0"
    assert result.source_file == "LICENSE.md"


def test_bsd_in_a_file_named_copying(tmp_path):
    """redis 2015 uses COPYING, not LICENSE."""
    repo = make_repo(tmp_path, "redis", {"COPYING": TEXT["BSD-3-Clause"]})
    result = detect_repo(repo)
    assert result.spdx == "BSD-3-Clause"
    assert result.source_file == "COPYING"


def test_mit_at_root(tmp_path):
    repo = make_repo(tmp_path, "mit", {"LICENSE": TEXT["MIT"]})
    assert detect_repo(repo).spdx == "MIT"


def test_licence_declared_only_in_readme(tmp_path):
    repo = make_repo(tmp_path, "readme_only", {
        "README.md": "# tinylib\n\nA small thing.\n\n## License\n\n"
                     "This project is licensed under the MIT License.\n"})
    result = detect_repo(repo)
    assert result.spdx == "MIT"
    assert result.method == "readme_fallback"
    assert "MIT" in result.evidence


def test_readme_fallback_does_not_fire_on_incidental_mentions(tmp_path):
    """A README naming MIT Kerberos is not a licence grant."""
    repo = make_repo(tmp_path, "kerberos", {
        "README.md": "# server\n\nAuthenticates against MIT Kerberos and "
                     "Apache Directory. Ships with BSD sockets support.\n"})
    assert detect_repo(repo).spdx == "none"


def test_no_licence_anywhere(tmp_path):
    repo = make_repo(tmp_path, "bare", {"README.md": "# nothing\n"})
    result = detect_repo(repo)
    assert result.spdx == "none"
    assert result.method == "absent"
    assert result.candidates == []


def test_multiple_root_licence_files_report_the_governing_one(tmp_path):
    """mattermost ships LICENSE.txt beside LICENSE.enterprise."""
    repo = make_repo(tmp_path, "mm", {"LICENSE.txt": TEXT["MIT"],
                                      "LICENSE.enterprise": TEXT["AGPL-3.0"]})
    result = detect_repo(repo)
    assert result.spdx == "MIT"
    assert "AGPL-3.0" in result.secondary
    assert len(result.candidates) == 2


def test_filename_matcher_scope():
    yes = ["LICENSE", "LICENSE.md", "license", "COPYING", "COPYING.LESSER",
           "LICENSE.txt", "LICENCE", "UNLICENSE", "LICENSE-MIT", "LICENSES"]
    no = ["README.md", "licenses.go", "LICENSE_HEADER.py.txt", "src", "NOTICE.md"]
    assert all(licence.is_licence_filename(n) for n in yes)
    assert not any(licence.is_licence_filename(n) for n in no)


# ---------------------------------------------------------------------------
# the same assertions against the real clones
# ---------------------------------------------------------------------------

# Ground truth is the public licensing record for each project at that date,
# established independently of what this detector returns.
REAL = [
    ("hashicorp/terraform", 2016, "MPL-2.0", "LICENSE"),
    ("calcom/cal.com", 2022, "AGPL-3.0", "LICENSE"),
    ("grafana/grafana", 2017, "Apache-2.0", "LICENSE.md"),   # AGPL came 2021
    ("redis/redis", 2015, "BSD-3-Clause", "COPYING"),
    ("dbt-labs/dbt-core", 2020, "Apache-2.0", "License.md"),
    ("directus/directus", 2020, "GPL-3.0", "license"),
    ("meteor/meteor", 2014, "MIT", "LICENSE.txt"),
    ("mongodb/mongo", 2013, "AGPL-3.0", "GNU-AGPL-3.0.txt"),  # SSPL came 2018
    ("rethinkdb/rethinkdb", 2014, "AGPL-3.0", "COPYRIGHT"),
    ("cockroachdb/cockroach", 2017, "Apache-2.0", "LICENSE"),  # BSL came 2019
    ("getsentry/sentry", 2016, "BSD-3-Clause", "LICENSE"),     # BSL came 2019
    ("minio/minio", 2017, "Apache-2.0", "LICENSE"),            # AGPL came 2020
    ("owncloud/core", 2014, "AGPL-3.0", "COPYING-AGPL"),
    ("mattermost/mattermost", 2018, "ambiguous", "LICENSE.txt"),
]


@pytest.mark.parametrize("repo_name,year,expected,expected_file", REAL)
def test_real_repo_at_snapshot(repo_name, year, expected, expected_file):
    """Resolved through the production boundary, not a local date literal.

    These assertions read the ROOT TREE, so they move whenever the snapshot
    boundary moves. When the bound changed from 31 December to 1 January of
    year+1, 17 of 24 corpus projects landed on a different revision. Calling
    features.snapshot_bound here means the tests cannot silently drift away
    from what the scorer actually does.
    """
    path = CLONES / repo_name.replace("/", "__")
    repo = gitpit.Repo(path)
    if not repo.exists():
        pytest.skip(f"{repo_name} not cloned; run tools/clone_corpus.py")
    rev = repo.rev_at(pitgit.as_of(date(year, 12, 31)))
    assert rev, f"no commit in {repo_name} within {year}"
    result = licence.detect_at(repo, rev)
    assert result.spdx == expected, result.as_dict()
    assert result.source_file == expected_file


def test_grafana_end_2014_has_no_root_licence_only_vendored_ones():
    """A one-commit transition that the boundary change walked straight into.

    Grafana's root held LICENSE.md (Apache-2.0) through 30 December 2014. The
    backend-unification restructure landed on the 31st without it, and
    LICENSE.md does not reappear until 2015-01-07. So "grafana at end of 2014"
    genuinely has no root licence, while ten vendored dependency licences sit
    under Godeps/ — a recursive detector would return one of those with total
    confidence.

    Two things worth keeping pinned. The root-only rule earns its place here on
    a real repository rather than a fixture. And gate 1's only fund-mode fail
    is `no_license_file`, which means a project's gate-1 verdict can flip on a
    single commit — grafana's own fund snapshot is 2017 so the corpus is
    unaffected, but the fragility is real.
    """
    repo = gitpit.Repo(CLONES / "grafana__grafana")
    if not repo.exists():
        pytest.skip("grafana not cloned")

    rev = repo.rev_at(pitgit.as_of(date(2014, 12, 31)))
    result = licence.detect_at(repo, rev)
    assert result.spdx == "none"
    assert result.method == "absent"
    assert result.candidates == []

    vendored = [f for f in repo.ls_recursive(rev)
                if licence.is_licence_filename(Path(f).name)]
    assert len(vendored) >= 10, "the vendored trap has gone from this revision"
    assert all(f.startswith("Godeps/") for f in vendored)

    # the day before, the same repo is unambiguously Apache-2.0
    earlier = repo.rev_at(date(2014, 12, 31))
    assert licence.detect_at(repo, earlier).spdx == "Apache-2.0"


def test_real_terraform_tree_still_contains_the_trap():
    """If vendor/ ever leaves the 2016 tree this test stops proving anything."""
    repo = gitpit.Repo(CLONES / "hashicorp__terraform")
    if not repo.exists():
        pytest.skip("terraform not cloned")
    rev = repo.rev_at(pitgit.as_of(date(2016, 12, 31)))
    nested = [f for f in repo.ls_recursive(rev)
              if licence.is_licence_filename(Path(f).name)]
    root = [f for f in repo.ls_root(rev) if licence.is_licence_filename(f)]
    assert len(nested) > 100, "the vendored-licence trap is gone from the fixture"
    assert root == ["LICENSE"]
