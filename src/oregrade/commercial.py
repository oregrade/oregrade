"""Commercial-entity facts.

Split sharply by what can and cannot be known at a past date:

  FROM THE CLONE, point-in-time
      FUNDING.yml and its contents, a pricing or enterprise link in the README,
      a commercial licence file alongside the open one. These are in the tree
      at the revision, so they are real evidence about that date.

  FROM THE GITHUB API, PRESENT DAY ONLY
      org account type, current funding metadata. Useful, never point-in-time,
      and labelled `as_of: today` wherever it appears. A dossier for 2014 that
      reported today's org type as a 2014 fact would be exactly the error the
      rest of this tool exists to avoid.

Trademark status is NOT implemented. The plan lists it under --github, but
GitHub does not know about trademarks; it needs a USPTO or WIPO lookup, which
is a different integration with different failure modes. The field is reported
as `unavailable` with that reason rather than quietly omitted.
"""
from __future__ import annotations

import os
import re

FUNDING_PATTERNS = [
    (r"github:\s*\[?\s*[\w-]", "github_sponsors"),
    (r"open_collective:\s*\S", "open_collective"),
    (r"patreon:\s*\S", "patreon"),
    (r"tidelift:\s*\S", "tidelift"),
    (r"custom:\s*\S", "custom_funding_link"),
]

README_COMMERCIAL = [
    (r"/pricing\b|\bpricing\b", "pricing"),
    (r"\benterprise (?:edition|version|plan|tier)\b", "enterprise_tier"),
    (r"\b(?:hosted|managed|cloud) (?:version|offering|service|platform)\b",
     "hosted_offering"),
    (r"\bcontact (?:us for )?sales\b|\bsales@", "sales_contact"),
    (r"\bcommercial licen[cs]", "commercial_licence"),
    (r"\bdual[- ]licen[cs]", "dual_licence"),
]

# "no hidden pricing" is a disavowal, not a pricing page, and Directus 2020
# says exactly that. Guard kept from the experiment, where it was a real bug.
NEGATION = re.compile(
    r"\b(no|not|never|without|free of|hidden|zero|nor)\b[^.;\n]{0,40}$", re.I)


def _negated(text: str, start: int) -> bool:
    return bool(NEGATION.search(text[max(0, start - 60):start]))


def detect(root_entries: list[str], read, readme: str | None) -> dict:
    """Point-in-time commercial evidence from the tree only."""
    evidence, sources = [], []

    for name in root_entries:
        if name.upper().replace(".YAML", ".YML") in ("FUNDING.YML", ".FUNDING.YML"):
            body = read(name) or ""
            sources.append(name)
            for pattern, label in FUNDING_PATTERNS:
                if re.search(pattern, body, re.I):
                    evidence.append({"signal": label, "source": name})
        if re.match(r"^licen[cs]e[-_.](commercial|enterprise|ee)", name, re.I):
            evidence.append({"signal": "commercial_licence_file", "source": name})

    if readme:
        head = readme[:20000]
        for pattern, label in README_COMMERCIAL:
            for match in re.finditer(pattern, head, re.I):
                if _negated(head, match.start()):
                    continue
                start = head.rfind("\n", 0, match.start()) + 1
                end = head.find("\n", match.end())
                line = head[start:end if end != -1 else len(head)]
                evidence.append({"signal": label, "source": "README",
                                 "quote": " ".join(line.split())[:180]})
                break

    return {
        "entity_detected": bool(evidence),
        "entity_basis": "point_in_time_tree_evidence",
        "evidence": evidence or None,
        "files_examined": sources or None,
        "trademark": {
            "status": "unavailable",
            "reason": "needs a USPTO or WIPO lookup; GitHub does not carry "
                      "trademark data and no registry integration exists"},
    }


def present_day(identity, token: str | None = None) -> dict:
    """Current GitHub metadata. NEVER point-in-time — labelled as today's."""
    import json
    import urllib.error
    import urllib.request
    token = token or os.environ.get("GITHUB_TOKEN") or None
    out = {"as_of": "today", "warning":
           "present-day metadata. Not valid for the requested date and must "
           "not be read as evidence about it."}
    if not identity.available:
        out["status"] = "unavailable"
        out["reason"] = identity.error or "repo identity unresolved"
        return out
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "oregrade"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repositories/{identity.repo_id}",
            headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except Exception as exc:
        out["status"] = "unavailable"
        out["reason"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return out
    owner = data.get("owner") or {}
    out.update(status="collected",
               owner_type=owner.get("type"),
               owner_login=owner.get("login"),
               archived=data.get("archived"),
               homepage=data.get("homepage") or None,
               current_full_name=data.get("full_name"))
    return out
