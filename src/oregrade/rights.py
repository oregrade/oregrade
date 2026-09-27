"""Rights position as facts: who holds copyright, is there a CLA, who owns it.

These were gate-1 inputs. They are reported here as measurements with no
pass/fail attached, because whether a given rights position is workable is a
judgement that depends on the buyer, the price and the lawyer — none of which
this tool knows about.

One field the plan asked for is deliberately absent: `consolidatable`. A value
of "unlikely" is an opinion, and the brief for this tool is that it contains
none. The inputs to that opinion — holder count, CLA presence, foundation
ownership — are all here, so anyone who wants the verdict can form it.
"""
from __future__ import annotations

import re

CLA_MARKERS = [
    (r"contributor licen[cs]e agreement", "contributor licence agreement"),
    (r"\bsign the cla\b", "sign the CLA"),
    (r"\bcla[- ]?(?:bot|assistant)\b", "CLA bot"),
    (r"copyright assignment", "copyright assignment"),
    (r"\bdco\b|developer certificate of origin", "DCO (not a CLA — no rights transfer)"),
]

# Foundation ownership is a point-in-time fact. etcd donated to the CNCF in
# 2018, so a 2016 dossier must NOT call it foundation-owned. Org prefixes are
# used only where the org itself is the foundation; anything date-dependent
# needs the donation date, which is why this table is short and conservative.
FOUNDATION_ORGS = {
    "apache": "Apache Software Foundation",
    "eclipse": "Eclipse Foundation",
    "cncf": "Cloud Native Computing Foundation",
    "openjs-foundation": "OpenJS Foundation",
    "finos": "FINOS",
}

CONTRIB_FILES = ("CONTRIBUTING", "CLA", "DCO")


def detect(repo: str, root_entries: list[str], read, commits) -> dict:
    """Rights facts at the resolved revision.

    `read(path)` returns a root-level file's contents; `commits` is the list of
    commits up to the bound, used only to count distinct authors.
    """
    org = repo.split("/")[0].lower()
    foundation = FOUNDATION_ORGS.get(org)

    text, sources = "", []
    for name in root_entries:
        base = name.upper()
        if any(base.startswith(prefix) for prefix in CONTRIB_FILES):
            body = read(name)
            if body:
                text += body[:20000].lower()
                sources.append(name)

    matched = [label for pattern, label in CLA_MARKERS
               if re.search(pattern, text, re.I)]
    dco_only = matched and all("DCO" in m for m in matched)

    holders = len({c.email for c in commits}) if commits else 0
    return {
        "copyright_holders_estimate": holders,
        "copyright_holders_basis": (
            "distinct commit-author email addresses up to the date. An "
            "over-count where one person used several addresses, an "
            "under-count where an employer holds the copyright of many."),
        "cla_detected": bool(matched) and not dco_only,
        "cla_evidence": matched or None,
        "cla_files_examined": sources or None,
        "foundation_owned": foundation is not None,
        "foundation": foundation,
        "foundation_basis": (
            "organisation-prefix match only. A project donated to a foundation "
            "AFTER this date will not show here, and should not — etcd joined "
            "the CNCF in 2018 and a 2016 dossier must not say so."),
    }
