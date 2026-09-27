"""Dossier output: YAML, JSON, or a readable markdown fact sheet.

The markdown form is the one a person reads. It keeps every provenance note,
because a fact sheet whose caveats are only in the machine-readable version is
a fact sheet designed to be misquoted.
"""
from __future__ import annotations

import json

import yaml


def to_yaml(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, width=88, allow_unicode=True)


def to_json(doc: dict) -> str:
    return json.dumps(doc, indent=2, default=str) + "\n"


def _row(label, value, width=34):
    if value is None:
        value = "—"
    return f"| {label:<{width}} | {value} |"


def to_markdown(doc: dict) -> str:
    out: list[str] = []
    add = out.append

    add(f"# {doc['repo']}")
    add("")
    add(f"**As of {doc['as_of']}** — {doc['resolution_note']}")
    add("")
    if doc.get("status") != "ok":
        add(f"## Status: `{doc.get('status')}`")
        add("")
        add(doc.get("status_note", ""))
        return "\n".join(out) + "\n"

    add("## Identity")
    add("")
    add(f"- repo id: `{doc.get('repo_id')}` (rename-invariant)")
    add(f"- current name: `{doc.get('current_full_name')}`")
    if doc.get("renamed_since_requested"):
        add(f"- **renamed since**: requested `{doc['repo']}`")
    for note in doc.get("identity_notes") or []:
        add(f"- _{note}_")
    add(f"- revision at date: `{doc.get('revision', '')[:12]}`")
    add("")

    lic = doc.get("licence", {})
    add("## Licence")
    add("")
    add("| field | value |")
    add("| --- | --- |")
    add(_row("spdx", f"`{lic.get('spdx')}`"))
    add(_row("file", lic.get("file")))
    add(_row("method", lic.get("method")))
    add(_row("confidence", lic.get("confidence")))
    add(_row("scope", lic.get("scope")))
    add(_row("vendored licences ignored", lic.get("vendored_licences_present")))
    add(_row("ambiguous", lic.get("ambiguous")))
    add(_row("stability (±7d)", lic.get("stability", {}).get("status")))
    add("")
    if lic.get("evidence"):
        add(f"> {lic['evidence']}")
        add("")
    if lic.get("ambiguity_note"):
        add(f"**Ambiguous.** {lic['ambiguity_note']}")
        add("")
    add(f"_{lic.get('scope_note', '')}_")
    add("")

    rights_block = doc.get("rights", {})
    add("## Rights")
    add("")
    add("| field | value |")
    add("| --- | --- |")
    add(_row("copyright holders (estimate)",
             rights_block.get("copyright_holders_estimate")))
    add(_row("CLA detected", rights_block.get("cla_detected")))
    add(_row("foundation owned", rights_block.get("foundation_owned")))
    add(_row("foundation", rights_block.get("foundation")))
    add("")
    add(f"_{rights_block.get('copyright_holders_basis', '')}_")
    add("")

    surf = doc.get("surface", {})
    add("## Surface — INFERRED")
    add("")
    add(f"- classification: `{surf.get('classification')}` "
        f"(confidence {surf.get('confidence')}, method `{surf.get('method')}`)")
    if surf.get("evidence"):
        add(f"- evidence: _{surf['evidence']}_")
    if surf.get("tied_candidates"):
        add(f"- **tied**: {', '.join(f'`{t}`' for t in surf['tied_candidates'])}"
            " — no classification given")
    for cand in surf.get("candidates") or []:
        if surf.get("tied_candidates"):
            add(f"  - `{cand['surface']}` (strength {cand['match_strength']}): "
                f"_{cand['evidence']}_")
    deployable = surf.get("multi_user_deployable") or {}
    value = deployable.get("value")
    add(f"- multi-user deployable: "
        f"`{'null' if value is None else str(value).lower()}` "
        f"(inferred from surface, confidence {deployable.get('confidence')})")
    add(f"- {surf.get('note', '')}")
    add("")

    act = doc.get("activity", {})
    add("## Activity")
    add("")
    add("| field | value |")
    add("| --- | --- |")
    for key in ("commits_to_date", "commits_trailing_90d", "commits_prior_90d",
                "momentum_90d", "authors_to_date", "authors_trailing_90d",
                "top1_commit_share", "top3_commit_share",
                "new_authors_trailing_12mo", "external_contributor_ratio",
                "age_years_at_snapshot", "days_since_last_commit",
                "first_commit", "sponsoring_email_domain"):
        if key in act:
            add(_row(key, act[key]))
    add("")
    if act.get("external_contributor_ratio_basis"):
        add(f"_external_contributor_ratio: {act['external_contributor_ratio_basis']}_")
        add("")

    com = doc.get("commercial", {})
    add("## Commercial")
    add("")
    add(f"- entity detected at date: **{com.get('entity_detected')}** "
        f"(basis: {com.get('entity_basis')})")
    for item in com.get("evidence") or []:
        quote = f" — _{item['quote']}_" if item.get("quote") else ""
        add(f"  - `{item['signal']}` from {item['source']}{quote}")
    trademark = com.get("trademark", {})
    add(f"- trademark: `{trademark.get('status')}` — {trademark.get('reason', '')}")
    if com.get("present_day"):
        pd = com["present_day"]
        add(f"- present-day (as of today, **not** {doc['as_of']}): "
            f"owner_type `{pd.get('owner_type')}`, archived `{pd.get('archived')}`")
    add("")

    ado = doc.get("adoption", {})
    add("## Adoption")
    add("")
    if ado.get("status") != "collected":
        add(f"`{ado.get('status')}` — {ado.get('reason', '')}")
    else:
        add("| field | value |")
        add("| --- | --- |")
        for key in ("distinct_issue_reporters_12mo",
                    "distinct_non_contributor_issue_reporters_12mo",
                    "forks_12mo", "pushers_to_date"):
            add(_row(key, ado.get(key)))
        add("")
        add(f"- caveat: `{ado.get('caveat')}`")
        if ado.get("caveat_detail"):
            add(f"  - _{ado['caveat_detail']}_")
    add("")

    prov = doc.get("provenance", {})
    add("## Provenance")
    add("")
    for key, value in prov.items():
        add(f"- {key}: `{value}`")
    add("")
    add("---")
    add("")
    add("_No score, no ranking, no verdict. This tool reports what it "
        "measured and where each figure came from._")
    return "\n".join(out) + "\n"


RENDERERS = {"yaml": to_yaml, "json": to_json, "markdown": to_markdown,
             "md": to_markdown}
