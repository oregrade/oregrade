"""Rights facts — reported as measurements, never as a verdict."""
from datetime import date

from oregrade import rights
from pitgit.repo import Commit


def commits(n):
    return [Commit(f"sha{i}", f"dev{i}@example.invalid", date(2015, 1, 1))
            for i in range(n)]


def test_no_verdict_field_is_emitted():
    """`consolidatable: unlikely` is an opinion. The inputs to it are here."""
    out = rights.detect("a/b", [], lambda n: "", commits(5))
    assert "consolidatable" not in out
    assert {"copyright_holders_estimate", "cla_detected", "foundation_owned"} <= set(out)


def test_holder_count_is_distinct_author_emails_with_its_basis_stated():
    out = rights.detect("a/b", [], lambda n: "", commits(7))
    assert out["copyright_holders_estimate"] == 7
    assert "over-count" in out["copyright_holders_basis"]


def test_cla_detected_from_contributing():
    out = rights.detect("a/b", ["CONTRIBUTING.md"],
                        lambda n: "Please sign the CLA before contributing.",
                        commits(3))
    assert out["cla_detected"] is True
    assert out["cla_files_examined"] == ["CONTRIBUTING.md"]


def test_dco_alone_is_not_a_cla():
    """A DCO certifies origin. It transfers no rights, and conflating them
    would report a rights position the project does not have."""
    out = rights.detect("a/b", ["CONTRIBUTING.md"],
                        lambda n: "All commits require a DCO sign-off.",
                        commits(3))
    assert out["cla_detected"] is False
    assert any("DCO" in m for m in out["cla_evidence"])


def test_foundation_ownership_is_org_prefix_only_and_says_so():
    assert rights.detect("apache/kafka", [], lambda n: "", commits(1))["foundation_owned"]
    out = rights.detect("etcd-io/etcd", [], lambda n: "", commits(1))
    assert out["foundation_owned"] is False
    assert "2018" in out["foundation_basis"]
