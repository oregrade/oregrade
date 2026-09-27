"""Dossier assembly: the contract is that it never lies and never guesses.

The tool's whole claim is provenance on every line and no opinion anywhere, so
these tests assert the absences as hard as the presences.
"""
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

import pitgit
from oregrade import dossier, render

ROOT = Path(__file__).resolve().parent.parent
CLONES = Path("/tmp/oregrade-clones")


def fixture_repo(tmp_path, name, files, commit_date="2015-06-01T12:00:00+00:00"):
    root = tmp_path / name
    root.mkdir(parents=True)
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": commit_date, "GIT_COMMITTER_DATE": commit_date,
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                    capture_output=True, env=env)
    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("commit", "-qm", "fixture")
    return root


def build_offline(tmp_path, files, at=date(2016, 1, 1)):
    clone_dir = tmp_path / "clones"
    fixture_repo(clone_dir, "acme__thing", files)
    return dossier.build("acme/thing", dossier.Options(
        at=at, clone_dir=clone_dir, cache_dir=tmp_path / "cache", offline=True))


# --- the contract ---------------------------------------------------------

def test_no_score_ranking_or_verdict_anywhere(tmp_path):
    doc = build_offline(tmp_path, {"LICENSE": "MIT License\n\nPermission is "
                                              "hereby granted, free of charge,",
                                   "README.md": "# thing\n\nA server.\n"})
    import re
    stripped = {k: v for k, v in doc.items() if k != "clone"}   # paths are noise
    flat = yaml.safe_dump(stripped).lower()
    for banned in ("score", "ranking", "auc", "verdict", "backtested_core",
                   "live_extension", "consolidatable", "weight"):
        assert not re.search(rf"\b{banned}\b", flat), banned


def test_at_is_inclusive_of_the_named_day(tmp_path):
    """`--at 2017-12-31` must include 31 December. The exclusive bound that
    follows is the difference between a root tree that has LICENSE.md and one
    that does not — grafana lost its licence file on exactly that day."""
    doc = build_offline(tmp_path, {"README.md": "# t\n"}, at=date(2016, 1, 1))
    assert doc["as_of"] == "2016-01-01"
    assert doc["resolved_at"] == "2016-01-02T00:00:00+00:00"


def test_every_block_is_present_even_when_a_source_is_absent(tmp_path):
    doc = build_offline(tmp_path, {"README.md": "# t\n"})
    for block in ("licence", "rights", "surface", "activity", "commercial",
                  "adoption", "provenance"):
        assert block in doc, block


def test_adoption_says_not_requested_rather_than_zero(tmp_path):
    doc = build_offline(tmp_path, {"README.md": "# t\n"})
    assert doc["adoption"]["status"] == "not_requested"
    assert "--gharchive" in doc["adoption"]["reason"]
    assert doc["adoption"].get("forks_12mo") is None


def test_surface_is_labelled_inferred(tmp_path):
    doc = build_offline(tmp_path, {"README.md": "# t\n\nA distributed database.\n"})
    assert doc["surface"]["inferred"] is True
    assert doc["surface"]["classification"] == "database"
    assert doc["surface"]["evidence"]


def test_licence_scope_is_root_only_and_counts_what_it_ignored(tmp_path):
    doc = build_offline(tmp_path, {
        "LICENSE": "Mozilla Public License, version 2.0\n1. Definitions\n",
        "vendor/dep/LICENSE": "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3",
        "README.md": "# t\n"})
    assert doc["licence"]["scope"] == "root_only"
    assert doc["licence"]["vendored_licences_present"] == 1
    assert doc["licence"]["spdx"] == "MPL-2.0"


def test_missing_licence_is_none_with_low_confidence_not_omitted(tmp_path):
    doc = build_offline(tmp_path, {"README.md": "# t\n"})
    assert doc["licence"]["spdx"] == "none"
    assert doc["licence"]["confidence"] == "low"


def test_provenance_records_the_three_non_obvious_fixes(tmp_path):
    prov = build_offline(tmp_path, {"README.md": "# t\n"})["provenance"]
    assert prov["timezone"] == "UTC"
    assert "calendar" in prov["window_arithmetic"]
    assert prov["licence_scope"] == "root_only"
    assert prov["modes"] == {"git": True, "gharchive": False, "github": False}


def test_stability_probe_is_labelled_as_looking_forward(tmp_path):
    doc = build_offline(tmp_path, {"README.md": "# t\n", "LICENSE": "MIT License"})
    stability = doc["licence"]["stability"]
    assert stability["status"] in ("stable", "changed_nearby")
    assert "after the date" in stability["note"]
    assert "never an input" in stability["note"]


def test_no_history_at_date_is_a_status_not_a_crash(tmp_path):
    doc = build_offline(tmp_path, {"README.md": "# t\n"}, at=date(2011, 1, 1))
    assert doc["status"] == "no_history_at_date"
    assert "did not exist publicly" in doc["status_note"]


# --- rendering -------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["yaml", "json", "markdown"])
def test_every_format_renders(tmp_path, fmt):
    doc = build_offline(tmp_path, {"README.md": "# t\n\nA server.\n",
                                   "LICENSE": "MIT License"})
    text = render.RENDERERS[fmt](doc)
    assert "acme/thing" in text and len(text) > 200


def test_markdown_keeps_the_caveats_not_just_the_numbers(tmp_path):
    doc = build_offline(tmp_path, {"README.md": "# t\n\nA database.\n",
                                   "LICENSE": "MIT License"})
    text = render.to_markdown(doc)
    assert "INFERRED" in text
    assert "No score, no ranking, no verdict" in text
    assert "vendored" in text.lower()


# --- the real thing --------------------------------------------------------

@pytest.mark.skipif(not (CLONES / "grafana__grafana").exists(),
                    reason="grafana not cloned")
def test_grafana_2017_end_to_end():
    doc = dossier.build("grafana/grafana", dossier.Options(
        at=date(2017, 12, 31), clone_dir=CLONES, offline=True))
    assert doc["status"] == "ok"
    assert doc["licence"]["spdx"] == "Apache-2.0"
    assert doc["licence"]["file"] == "LICENSE.md"
    assert doc["activity"]["commits_to_date"] == 12775
    assert doc["activity"]["sponsoring_email_domain"] == "grafana.com"


@pytest.mark.skipif(not (CLONES / "grafana__grafana").exists(),
                    reason="grafana not cloned")
def test_grafana_end_2014_has_no_root_licence_and_says_why():
    """The one-commit transition: LICENSE.md left the root on 31 Dec 2014 and
    returned a week later. Ten vendored licences sit under Godeps/."""
    doc = dossier.build("grafana/grafana", dossier.Options(
        at=date(2014, 12, 31), clone_dir=CLONES, offline=True))
    assert doc["licence"]["spdx"] == "none"
    assert doc["licence"]["vendored_licences_present"] >= 10
    assert doc["licence"]["stability"]["status"] == "changed_nearby"
