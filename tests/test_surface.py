"""Surface classification — the one inferred field, and it must say so."""
from oregrade import surface


def test_every_result_is_flagged_as_inferred():
    for readme in (None, "# thing", "# db\n\nA distributed database."):
        out = surface.classify(readme)
        assert out["inferred"] is True
        assert out["method"] == "heuristic_keyword"


def test_unknown_rather_than_a_guess_when_nothing_matches():
    out = surface.classify("# thing\n\nSome words with no signal in them.\n")
    assert out["classification"] == "unknown"
    assert out["confidence"] == "none"


def test_no_readme_is_unknown_and_says_why():
    out = surface.classify(None)
    assert out["classification"] == "unknown"
    assert "no README" in out["note"]


def test_recognises_the_common_surfaces():
    cases = {
        "A distributed SQL database for cloud applications.": "database",
        "Infrastructure as code. Terraform writes state to a state file.": "cli_stateful",
        "An S3-compatible object storage server.": "object_store",
        "Requests is an elegant HTTP library for Python.": "library",
        "A cross-platform desktop application for screen recording.": "desktop_app",
    }
    for text, expected in cases.items():
        assert surface.classify(text)["classification"] == expected, text


def test_every_classification_carries_quotable_evidence():
    out = surface.classify("# Grafana\n\nThe open observability platform. "
                           "Self-host the server yourself.\n")
    assert out["evidence"]
    assert out["classification"] in out["note"] or out["evidence"]


def test_ambiguity_is_reported_not_resolved_silently():
    out = surface.classify("A command-line tool and a desktop application.")
    if out["confidence"] == "low":
        assert "ambiguous" in out["note"].lower() or out["candidates"]


def test_multi_user_deployable_is_a_lookup_not_a_verdict():
    assert surface.classify("A distributed database.")["multi_user_deployable"]
    assert not surface.classify("An HTTP library for Python.")["multi_user_deployable"]


def test_layout_evidence_supplements_prose():
    out = surface.classify("# app\n\nA platform.\n",
                           ["docker-compose.yml", "README.md"])
    assert out["classification"] == "server"
    assert any("docker-compose" in str(c.get("evidence")) or c["surface"] == "server"
               for c in out["candidates"])
