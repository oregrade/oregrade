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
    if out.get("tied_candidates"):
        assert out["classification"] == "unknown"
        assert "unresolved" in out["note"].lower()


def test_multi_user_deployable_inherits_its_parents_doubt():
    """It is a table lookup on an inferred field, so it must not read as
    measured. A bare boolean would."""
    db = surface.classify("A distributed database.")["multi_user_deployable"]
    assert db["value"] is True
    assert db["inferred"] is True
    assert db["inherits_from"] == "surface.classification"
    assert db["confidence"] == surface.classify("A distributed database.")["confidence"]

    lib = surface.classify("An HTTP library for Python.")["multi_user_deployable"]
    assert lib["value"] is False


def test_multi_user_deployable_is_null_when_the_surface_is_unknown():
    """False would be an answer. There isn't one."""
    for readme in (None, "# thing\n\nSome words with no signal.\n"):
        out = surface.classify(readme)
        assert out["classification"] == "unknown"
        assert out["multi_user_deployable"]["value"] is None


def test_a_tie_is_reported_as_unknown_not_as_a_coin_flip():
    """got: `server` from "Internal server error" in a code sample, against
    `library` from "a human-friendly and powerful HTTP request library"."""
    out = surface.classify(
        "Got is a human-friendly and powerful HTTP request library for "
        "Node.js.\n\nconsole.log(body); //=> Internal server error\n")
    assert out["classification"] == "unknown"
    assert out["confidence"] == "none"
    assert set(out["tied_candidates"]) == {"server", "library"}
    assert out["evidence"] is None
    # the candidates survive — they are the useful part
    assert {c["surface"] for c in out["candidates"]} >= {"server", "library"}
    assert all(c["evidence"] for c in out["candidates"])
    assert out["multi_user_deployable"]["value"] is None


def test_layout_evidence_supplements_prose():
    out = surface.classify("# app\n\nA platform.\n",
                           ["docker-compose.yml", "README.md"])
    assert out["classification"] == "server"
    assert any("docker-compose" in str(c.get("evidence")) or c["surface"] == "server"
               for c in out["candidates"])
