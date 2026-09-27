"""Deployment-surface classification from the README and repo layout.

This is the one INFERRED field in the dossier and it is labelled as such
everywhere it appears. Everything else is measured; this is a guess with its
evidence attached.

The experiment used hand annotations for surface, so no classifier was ever
built. What is here is a transparent keyword-and-layout heuristic: it quotes
the line that triggered it, reports a confidence, and returns `unknown` rather
than guessing when nothing matches. It is deliberately easy to overrule — the
whole point of printing the evidence is that a human can check it in one
glance and disagree.

A stronger classifier is an obvious improvement and an obvious place for an LLM
call. It is not here because an unlabelled guess dressed up as a measurement is
worse than an honest `unknown`.
"""
from __future__ import annotations

import re

# Ordered most-specific first. Each rule is (surface, pattern, strength).
# 2 = a phrase that names the thing; 1 = a weaker hint. These are match
# strengths for picking between candidates, NOT scoring weights — this tool
# has none, and calling them weights invites the wrong reading.
RULES: list[tuple[str, str, int]] = [
    ("database", r"\b(database|datastore|key[- ]value store|sql engine|"
                 r"olap|oltp|document store|time[- ]series database)\b", 2),
    ("object_store", r"\b(object storage|s3[- ]compatible|blob store)\b", 2),
    ("proxy", r"\b(reverse proxy|load balancer|api gateway|service mesh)\b", 2),
    ("queue", r"\b(message broker|message queue|pub/sub system|event streaming)\b", 2),
    ("scheduler", r"\b(job scheduler|workflow orchestrat|cron service)\b", 2),
    ("collector", r"\b(metrics collector|log shipper|telemetry agent)\b", 2),
    ("agent", r"\b(monitoring agent|node agent|sidecar)\b", 2),
    ("daemon", r"\b(daemon|runtime engine|container engine)\b", 2),
    ("server", r"\b(self[- ]host|server|web (?:application|app|ui)|dashboard|"
               r"platform|admin panel|headless cms)\b", 1),
    ("framework_hosted", r"\b(web framework|application framework|"
                         r"full[- ]stack framework)\b", 2),
    ("cli_stateful", r"\b(state file|manages state|terraform|infrastructure as "
                     r"code|migration tool)\b", 2),
    ("cli_stateless", r"\b(command[- ]line (?:tool|utility)|\bcli\b)\b", 1),
    ("desktop_app", r"\b(desktop (?:app|application)|gui application|"
                    r"cross[- ]platform app)\b", 2),
    ("library", r"\b(library|sdk|client bindings|npm package|"
                r"import this (?:package|module))\b", 1),
]

# Layout evidence, checked against root-level entries. Weaker than prose but
# harder to fake: a Dockerfile plus a docker-compose is a deployable service.
LAYOUT = [
    ("server", {"docker-compose.yml", "docker-compose.yaml", "helm", "charts"}, 1),
    ("library", {"setup.py", "pyproject.toml", "package.json"}, 0),
]

PAY_NOT_TO_RUN = {"server", "daemon", "database", "object_store", "proxy",
                  "queue", "scheduler", "collector", "agent",
                  "framework_hosted", "cli_stateful"}


def classify(readme: str | None, root_entries: list[str] | None = None) -> dict:
    """Returns the surface, quoted evidence, method and confidence.

    Never raises, never guesses silently. `unknown` is a legitimate answer and
    is returned whenever nothing matched.
    """
    result = {"classification": "unknown", "evidence": None,
              "method": "heuristic_keyword", "confidence": "none",
              "inferred": True,
              "candidates": [],
              "note": "INFERRED, not measured. Check the quoted evidence."}
    if not readme:
        result["note"] = ("no README at this date — surface cannot be "
                          "classified. INFERRED field, left unknown.")
        return result

    head = readme[:8000]
    scores: dict[str, int] = {}
    quotes: dict[str, str] = {}
    for surface, pattern, strength in RULES:
        match = re.search(pattern, head, re.I)
        if not match:
            continue
        scores[surface] = scores.get(surface, 0) + strength
        if surface not in quotes:
            start = head.rfind("\n", 0, match.start()) + 1
            end = head.find("\n", match.end())
            line = head[start:end if end != -1 else len(head)]
            quotes[surface] = " ".join(line.split())[:180]

    for surface, names, strength in LAYOUT:
        if root_entries and strength and (set(root_entries) & names):
            scores[surface] = scores.get(surface, 0) + strength
            quotes.setdefault(surface, f"root contains {sorted(set(root_entries) & names)}")

    if not scores:
        return result

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    result.update(
        classification=top,
        evidence=quotes.get(top),
        candidates=[{"surface": s, "match_strength": w,
                     "evidence": quotes.get(s)} for s, w in ranked[:3]],
        confidence=("high" if top_score >= 2 and top_score > runner_up
                    else "medium" if top_score > runner_up else "low"),
        multi_user_deployable=top in PAY_NOT_TO_RUN)
    if top_score == runner_up:
        result["note"] = (f"INFERRED, and ambiguous: {ranked[0][0]} and "
                          f"{ranked[1][0]} scored equally. Check the evidence.")
    return result
