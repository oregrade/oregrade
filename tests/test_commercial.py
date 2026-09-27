"""Commercial-evidence detection, including the false positives it must not make.

These guards come from the experiment: two of them were real bugs found on the
corpus, and both are the same shape — text that mentions a commercial word
while saying the opposite, or while meaning something technical.
"""
from oregrade import commercial


def read_none(_name):
    return ""


def test_no_evidence_is_reported_as_false_not_missing():
    out = commercial.detect([], read_none, "# thing\n\nA small library.\n")
    assert out["entity_detected"] is False
    assert out["evidence"] is None


def test_funding_yml_is_point_in_time_evidence():
    out = commercial.detect(["FUNDING.yml"],
                            lambda n: "github: [someone]\n", None)
    assert out["entity_detected"] is True
    assert [e["signal"] for e in out["evidence"]] == ["github_sponsors"]
    assert out["files_examined"] == ["FUNDING.yml"]


def test_enterprise_tier_and_pricing_are_detected_with_quotes():
    out = commercial.detect([], read_none,
                            "GitLab Enterprise Edition includes extra features.\n"
                            "See https://about.gitlab.com/pricing/\n")
    signals = {e["signal"] for e in out["evidence"]}
    assert {"enterprise_tier", "pricing"} <= signals
    assert all(e.get("quote") for e in out["evidence"] if e["source"] == "README")


def test_a_disavowal_of_pricing_is_not_commercial_intent():
    """Directus 2020: 'No artificial limitations, vendor lock-in, or hidden pricing'."""
    out = commercial.detect([], read_none,
                            "**Free & open-source.** No artificial limitations, "
                            "vendor lock-in, or hidden pricing.\n")
    assert out["entity_detected"] is False


def test_negation_does_not_swallow_a_genuine_hit():
    out = commercial.detect([], read_none,
                            "There is no vendor lock-in.\n\n## Pricing\n\n"
                            "Plans start at $20/month.\n")
    assert {e["signal"] for e in out["evidence"]} >= {"pricing"}


def test_technical_support_language_is_not_commercial_intent():
    """redis, docker and elasticsearch all say 'support' about features only."""
    for text in ["We support big endian and little endian architectures.\n",
                 "Support for more than one index.\n",
                 "of large-scale operation and support of hundreds of thousands\n"]:
        assert commercial.detect([], read_none, text)["entity_detected"] is False, text


def test_trademark_is_reported_unavailable_not_omitted():
    out = commercial.detect([], read_none, None)
    assert out["trademark"]["status"] == "unavailable"
    assert "USPTO" in out["trademark"]["reason"]


def test_present_day_block_is_labelled_as_today_not_the_date():
    from pitgit.identity import RepoIdentity
    out = commercial.present_day(RepoIdentity(requested="a/b"))
    assert out["as_of"] == "today"
    assert "not valid for the requested date" in out["warning"].lower()
    assert out["status"] == "unavailable"
