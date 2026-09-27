"""Licence detection at a point in time, from a blobless clone.

THE REGRESSION THIS GUARDS
--------------------------
The first version listed the tree with `ls-tree -r` and matched the first
licence file it found anywhere in the repo. On hashicorp/terraform that meant a
vendored dependency's licence under vendor/, and Terraform — MPL-2.0 — was
classified AGPL. Gate 1 acted on it.

Two rules follow, and both are tested:

  1. Only root-level tree entries are ever considered. `Repo.ls_root` exists so
     that no caller can accidentally pass -r.
  2. Detection is ordered most-specific-first. AGPL text contains the phrase
     "GNU General Public License"; LGPL text contains it too. Matching GPL
     before AGPL/LGPL silently mislabels both.

Where a project declares its licence only in the README (older C projects and
single-file libraries do this), the README is read as a fallback and the result
is marked `method: readme_fallback` so a human can see it was inferred rather
than read off a licence file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_BYTES = 80_000

DOC_EXT = ("md", "txt", "rst", "html", "markdown", "adoc")
# Source files whose names begin "licence": licenses.go, license.py, and the
# LICENSE_HEADER.py.txt templates several repos ship. None is a licence grant.
CODE_EXT = {"go", "py", "js", "ts", "tsx", "jsx", "rb", "c", "h", "cc", "cpp",
            "java", "rs", "php", "sh", "pl", "swift", "kt", "cs", "json",
            "yaml", "yml", "toml", "xml", "gradle", "mk", "bzl", "in", "am"}
# COPYRIGHT is included because rethinkdb — and a fair number of older C and
# C++ projects — put the full licence grant there and ship no LICENSE file at
# all. A COPYRIGHT holding nothing but a copyright line classifies as "none"
# and falls through to the README, so including it costs nothing.
LICENCE_FILE_RE = re.compile(
    r"^(licen[cs]es?|copying|copyright|unlicen[cs]e)([-_.][a-z0-9+.\-]+)?$", re.I)

# mongodb 2013 has no LICENSE file at all: its root holds GNU-AGPL-3.0.txt and
# APACHE-2.0.txt, the AGPL governing the server and the Apache file covering
# the drivers. Files named after the licence itself are common in that era and
# a name-only matcher misses every one of them.
IDENTIFIER_FILE_RE = re.compile(
    r"^(gnu[-_.])?(a?gpl|lgpl|apache|mit|bsd|mpl|epl|isc|cc0|artistic|"
    r"unlicen[cs]e|wtfpl|zlib)[-_.0-9a-z+]*$", re.I)

# When a repo ships several root licence files and none has a canonical name,
# the most restrictive one governs the project as a whole and the permissive
# ones cover subcomponents. That is the mongo case and the ownCloud case.
RESTRICTIVENESS = {"source_available": 4, "copyleft": 3, "weak_copyleft": 2,
                   "permissive": 1, "none": 0}

# Each entry is (spdx, [title_phrase, ...extra_required_phrases]). All phrases
# must be present; the FIRST one is the title and its position decides which
# match wins.
#
# Position, not list order, is the discriminator, because licence texts cite
# each other and every fixed ordering gets some real repo wrong:
#
#   MPL-2.0 s1.12 defines "Secondary License" as the GPL, the LGPL and the
#   AGPL by full title — so GNU-first calls Terraform (MPL) a GPL project.
#   GPL-3.0 s13 is headed "Use with the GNU Affero General Public License" —
#   so AGPL-first calls Directus (GPL-3.0) an AGPL project.
#   Meteor's LICENSE.txt states MIT and then appends the full Apache text of
#   its bundled dependencies — so Apache-before-MIT calls Meteor Apache.
#
# In all three the governing licence is the one whose title appears first; the
# citations and appendices come later. That rule is what is implemented.
MATCHERS: list[tuple[str, list[str]]] = [
    ("MPL-2.0", ["MOZILLA PUBLIC LICENSE", "VERSION 2.0"]),
    ("MPL-1.1", ["MOZILLA PUBLIC LICENSE", "VERSION 1.1"]),
    ("AGPL-3.0", ["GNU AFFERO GENERAL PUBLIC LICENSE"]),
    ("AGPL-3.0", ["AFFERO GENERAL PUBLIC LICENSE"]),
    ("LGPL-3.0", ["GNU LESSER GENERAL PUBLIC LICENSE", "VERSION 3"]),
    ("LGPL-2.1", ["GNU LESSER GENERAL PUBLIC LICENSE"]),
    ("LGPL-2.1", ["GNU LIBRARY GENERAL PUBLIC LICENSE"]),
    ("GPL-3.0", ["GNU GENERAL PUBLIC LICENSE", "VERSION 3"]),
    ("GPL-2.0", ["GNU GENERAL PUBLIC LICENSE", "VERSION 2"]),
    ("Apache-2.0", ["APACHE LICENSE", "VERSION 2.0"]),
    # cockroach 2017 writes "Apache Public License 2.0". Not the SPDX name, but
    # unambiguously Apache-2.0 and the only licence that phrase ever means.
    ("Apache-2.0", ["APACHE PUBLIC LICENSE"]),
    ("BSL-1.1", ["BUSINESS SOURCE LICENSE"]),
    ("SSPL-1.0", ["SERVER SIDE PUBLIC LICENSE"]),
    ("Elastic-2.0", ["ELASTIC LICENSE 2.0"]),
    ("EPL-2.0", ["ECLIPSE PUBLIC LICENSE", "V. 2.0"]),
    ("EPL-1.0", ["ECLIPSE PUBLIC LICENSE"]),
    ("ISC", ["PERMISSION TO USE, COPY, MODIFY, AND/OR DISTRIBUTE"]),
    ("Unlicense", ["THIS IS FREE AND UNENCUMBERED SOFTWARE RELEASED INTO THE PUBLIC DOMAIN"]),
    ("MIT", ["MIT LICENSE"]),
    ("MIT", ["PERMISSION IS HEREBY GRANTED, FREE OF CHARGE"]),
    ("BSD", ["REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS"]),
    ("BSD", ["REDISTRIBUTION AND USE IN SOURCE"]),
]

# Source-available side-licences that sit beside an open one. Recorded, never
# promoted to primary: the CCL covers cockroach's ccl/ subtree, not the repo.
SIDE_LICENCE_MARKERS = {
    "CockroachDB Community License": r"COCKROACHDB COMMUNITY LICENSE",
    "Enterprise Edition licence": r"ENTERPRISE EDITION LICENSE",
}

BSD_3_MARK = "NEITHER THE NAME OF"
BSD_4_MARK = "ALL ADVERTISING MATERIALS MENTIONING FEATURES"

COPYLEFT = {"AGPL-3.0", "GPL-3.0", "GPL-2.0", "LGPL-3.0", "LGPL-2.1"}
WEAK_COPYLEFT = {"MPL-2.0", "MPL-1.1", "EPL-1.0", "EPL-2.0"}
PERMISSIVE = {"MIT", "Apache-2.0", "BSD", "BSD-2-Clause", "BSD-3-Clause",
              "BSD-4-Clause", "ISC", "Unlicense"}
SOURCE_AVAILABLE = {"BSL-1.1", "SSPL-1.0", "Elastic-2.0"}

# A README states a licence rather than reproducing it, so the full-text
# matchers above mostly miss. These short declarations are what to look for.
README_DECLARATIONS: list[tuple[str, str]] = [
    ("AGPL-3.0", r"\bAGPL(?:[- ]?v?3(?:\.0)?)?\b"),
    ("AGPL-3.0", r"\bAffero\s+General\s+Public\s+Licen[cs]e\b"),
    ("LGPL-2.1", r"\bLGPL\b"),
    ("GPL-3.0", r"\bGPL(?:[- ]?v?3(?:\.0)?)\b"),
    ("GPL-2.0", r"\bGPL(?:[- ]?v?2(?:\.0)?)\b"),
    ("MPL-2.0", r"\b(?:MPL|Mozilla\s+Public\s+Licen[cs]e)[- ]?v?2(?:\.0)?\b"),
    ("Apache-2.0", r"\bApache(?:\s+Licen[cs]e)?[,\s]*(?:version\s*)?v?2(?:\.0)?\b"),
    ("BSD-3-Clause", r"\b(?:3[- ]clause\s+BSD|BSD[- ]3[- ]clause)\b"),
    ("BSD-2-Clause", r"\b(?:2[- ]clause\s+BSD|BSD[- ]2[- ]clause)\b"),
    ("BSD", r"\bBSD\s+licen[cs]e\b"),
    ("ISC", r"\bISC\s+licen[cs]e\b"),
    ("MIT", r"\bMIT\s+licen[cs]e\b"),
    ("MIT", r"\blicen[cs]ed?\s+under\s+(?:the\s+)?MIT\b"),
]

# Only trust a README declaration if it sits near licence-ish language, so that
# "works with MIT Kerberos" in a feature list is not read as a licence grant.
README_CONTEXT = re.compile(
    r"licen[cs]e|licen[cs]ed|copyright|copying|public\s+domain", re.I)


@dataclass
class Licence:
    spdx: str                       # SPDX-ish id, or "none"
    source_file: str | None         # root-level path the id came from
    method: str                     # licence_file | readme_fallback | absent
    evidence: str = ""              # quoted line a human can check in one glance
    candidates: list[str] = field(default_factory=list)   # root licence files
    secondary: list[str] = field(default_factory=list)    # other ids at root
    side_licences: list[str] = field(default_factory=list)  # named side terms
    multi_licence: bool = False   # three or more distinct ids at root

    @property
    def is_copyleft(self) -> bool:
        return self.spdx in COPYLEFT

    @property
    def is_weak_copyleft(self) -> bool:
        return self.spdx in WEAK_COPYLEFT

    @property
    def is_permissive(self) -> bool:
        return self.spdx in PERMISSIVE

    @property
    def is_source_available(self) -> bool:
        return self.spdx in SOURCE_AVAILABLE

    def as_dict(self) -> dict:
        return {"spdx": self.spdx, "source_file": self.source_file,
                "method": self.method, "evidence": self.evidence,
                "root_licence_files": self.candidates,
                "secondary_ids": self.secondary,
                "side_licences": self.side_licences,
                "multi_licence": self.multi_licence}


def is_licence_filename(name: str) -> bool:
    stem = name.strip()
    for ext in DOC_EXT:                       # LICENSE_HEADER.py.txt -> ...py
        if stem.lower().endswith("." + ext):
            stem = stem[: -(len(ext) + 1)]
            break
    if stem.rsplit(".", 1)[-1].lower() in CODE_EXT and "." in stem:
        return False
    return bool(LICENCE_FILE_RE.match(stem) or IDENTIFIER_FILE_RE.match(stem))


def filename_rank(name: str) -> int:
    """How strongly a filename claims to be THE licence of the project.

    0  LICENSE, LICENCE, COPYING, COPYRIGHT (+ .md/.txt/.rst)
    1  a qualified variant: COPYING-AGPL, LICENSE.enterprise, LICENSE-MIT
    2  named after the licence itself: GNU-AGPL-3.0.txt, APACHE-2.0.txt

    Rank 1 exists for the side-grant case. A repo with LICENSE.txt beside
    LICENSE.enterprise is governed by the former; the enterprise file is an
    alternative offer, not the project's licence.
    """
    stem = name.strip()
    for ext in DOC_EXT:
        if stem.lower().endswith("." + ext):
            stem = stem[: -(len(ext) + 1)]
            break
    m = LICENCE_FILE_RE.match(stem)
    if not m:
        return 2
    return 1 if m.group(2) else 0


def family(spdx: str) -> str:
    if spdx in COPYLEFT:
        return "copyleft"
    if spdx in WEAK_COPYLEFT:
        return "weak_copyleft"
    if spdx in SOURCE_AVAILABLE:
        return "source_available"
    if spdx in PERMISSIVE:
        return "permissive"
    return "none"


def _normalise(text: str) -> str:
    """Collapse all whitespace and upper-case.

    Licence files are hard-wrapped at 70-ish columns, so the phrase being
    matched routinely straddles a newline: the MPL-2.0 text breaks "GNU Affero
    General Public / License" across two lines. Matching against raw lines
    misses those and lands on whichever phrase happened not to wrap.
    """
    return " ".join(text.split()).upper()


def _quote_around(text: str, needle: str) -> str:
    """A short quotation containing the matched phrase, for a human to check."""
    flat = " ".join(text.split())
    idx = flat.upper().find(needle)
    if idx == -1:
        return flat[:160]
    return flat[max(0, idx - 40):idx + len(needle) + 60].strip()


def _resolve_bsd(spdx: str, flat: str) -> str:
    if spdx != "BSD":
        return spdx
    if BSD_4_MARK in flat:
        return "BSD-4-Clause"
    return "BSD-3-Clause" if BSD_3_MARK in flat else "BSD-2-Clause"


def identify_all(text: str) -> list[tuple[int, str, str]]:
    """Every licence identifiable in `text`, as (title_position, spdx, needle).

    Sorted earliest title first, ties broken by MATCHERS order — never
    alphabetically. MPL-2.0 and MPL-1.1 share the title "Mozilla Public
    License", and the MPL-2.0 text cites version 1.1, so both match at the same
    position; an alphabetical tie-break calls Terraform MPL-1.1.
    """
    flat = _normalise(text)
    found: dict[str, tuple[int, int, str]] = {}
    for order, (spdx, needles) in enumerate(MATCHERS):
        if not all(n in flat for n in needles):
            continue
        spdx = _resolve_bsd(spdx, flat)
        pos = flat.find(needles[0])
        if spdx not in found or (pos, order) < found[spdx][:2]:
            found[spdx] = (pos, order, needles[0])
    ranked = sorted((pos, order, spdx, needle)
                    for spdx, (pos, order, needle) in found.items())
    return [(pos, spdx, needle) for pos, _, spdx, needle in ranked]


def is_policy_document(text: str) -> bool:
    """True when a licence file states policy rather than granting one licence.

    mattermost's LICENSE.txt is the case: MIT for the binaries Mattermost Inc
    compiles, AGPL-3.0 for the source, Apache-2.0 for the config directories,
    plus a commercial option. The headline licence is permissive and the source
    licence is copyleft, so the first-title rule that resolves Meteor and
    Terraform gives the wrong answer here.

    Rather than add a rule tuned to one repository, the detector reports that
    it cannot resolve the file, and the scorer drops the licence-dependent
    inputs for that project the same way it drops any other missing input.
    """
    ids = {spdx for _, spdx, _ in identify_all(text)}
    if not ids:
        return False
    leading = identify_all(text)[0][1]
    return (family(leading) == "permissive"
            and any(family(i) == "copyleft" for i in ids))


def identify_text(text: str) -> tuple[str, str]:
    """Classify a licence text by its governing licence. Returns (spdx, evidence)."""
    matches = identify_all(text)
    if not matches:
        return "none", ""
    _, spdx, needle = matches[0]
    return spdx, _quote_around(text, needle)


def identify_readme(text: str) -> tuple[str, str]:
    """Classify a README's stated licence. Returns (spdx, evidence)."""
    full, evidence = identify_text(text)
    if full != "none":
        return full, evidence
    for spdx, pattern in README_DECLARATIONS:
        for m in re.finditer(pattern, text, re.I):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            window = text[max(0, m.start() - 200):m.end() + 200]
            if README_CONTEXT.search(window):
                return spdx, " ".join(line.split())[:160]
    return "none", ""


def detect(root_entries: list[str], read) -> Licence:
    """Detect the licence from ROOT-LEVEL entries only.

    `root_entries` must come from a non-recursive tree listing. `read(path)`
    returns that file's contents. Both are injected so the detector can be
    tested against fixture repos without a network.
    """
    names = [e for e in root_entries if e]
    licence_files = sorted(n for n in names if is_licence_filename(n))

    ids: list[tuple[str, str, str]] = []   # (spdx, file, evidence)
    all_in_files: set[str] = set()
    side: list[str] = []
    policy_files: set[str] = set()
    for name in licence_files:
        body = read(name)
        if not body:
            continue
        body = body[:MAX_BYTES]
        flat = _normalise(body)
        for label, marker in SIDE_LICENCE_MARKERS.items():
            if marker in flat and label not in side:
                side.append(label)
        matches = identify_all(body)
        all_in_files.update(spdx for _, spdx, _ in matches)
        if matches:
            _, spdx, needle = matches[0]
            ids.append((spdx, name, _quote_around(body, needle)))
            if is_policy_document(body):
                policy_files.add(name)

    if ids:
        # Selection between root licence files, in order:
        #   1. filename_rank — a plain LICENSE outranks LICENSE.enterprise,
        #      which outranks GNU-AGPL-3.0.txt;
        #   2. among equals, the most restrictive family governs. mongodb 2013
        #      ships GNU-AGPL-3.0.txt and APACHE-2.0.txt at the same rank: the
        #      AGPL covers the server and the Apache file covers the drivers,
        #      so the project is AGPL;
        #   3. then the shortest, plainest filename.
        ids.sort(key=lambda t: (filename_rank(t[1]),
                                -RESTRICTIVENESS[family(t[0])],
                                len(t[1]), t[1].lower()))
        spdx, name, evidence = ids[0]
        secondary = sorted(all_in_files - {spdx})
        if name in policy_files:
            return Licence("ambiguous", name, "licence_file",
                           evidence, candidates=licence_files,
                           secondary=sorted(all_in_files), side_licences=side,
                           multi_licence=True)
        return Licence(spdx, name, "licence_file", evidence,
                       candidates=licence_files, secondary=secondary,
                       side_licences=side,
                       multi_licence=len(all_in_files) >= 3)

    for name in names:
        if name.upper().startswith("README"):
            body = read(name)
            if not body:
                continue
            spdx, evidence = identify_readme(body[:MAX_BYTES])
            if spdx != "none":
                return Licence(spdx, name, "readme_fallback", evidence,
                               candidates=licence_files)

    return Licence("none", None, "absent", candidates=licence_files)


def detect_at(repo, rev: str) -> Licence:
    """Detect the licence in `repo` at `rev`. Root level only, by construction."""
    entries = repo.ls_root(rev)

    def read(path: str) -> str:
        return repo.show(rev, path, limit=MAX_BYTES)

    return detect(entries, read)
