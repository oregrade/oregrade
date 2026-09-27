"""THE ONE HARD RULE, enforced structurally.

Nothing under src/ may read benchmarks/outcomes.yaml. The rubric's only real
claim is that it was built without knowledge of the results and then tested
against them; scoring code that can see the results gets tuned toward them by
iteration alone. The check is a string scan rather than an import graph because
the failure mode it guards against is a one-line `open()` added in a hurry.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
BANNED = "outcomes"
# The plural is the filename. Match the singular too when it appears as a data
# access rather than as English prose, e.g. `outcome_resolved` or `["label"]`.
BANNED_TOKENS = [
    re.compile(r"outcomes", re.I),
    re.compile(r"outcome_resolved", re.I),
    re.compile(r"label_provisional", re.I),
    re.compile(r"commercialized_(success|modest|failed)", re.I),
    re.compile(r"never_commercialized", re.I),
]


def python_files():
    return sorted(SRC.rglob("*.py")) + sorted(SRC.rglob("*.yaml")) \
        + sorted(SRC.rglob("*.json"))


def test_src_directory_exists():
    assert SRC.is_dir(), "src/ is missing"
    assert python_files(), "src/ contains no files to scan"


def test_no_file_under_src_mentions_outcomes():
    offenders = []
    for path in python_files():
        text = path.read_text(errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if BANNED in line.lower():
                offenders.append(f"{path.relative_to(SRC.parent)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "src/ must never reference the outcome file:\n" + "\n".join(offenders))


def test_no_file_under_src_names_an_outcome_label():
    """Stronger than the letter of the rule: label *values* are results too."""
    offenders = []
    for path in python_files():
        text = path.read_text(errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in BANNED_TOKENS:
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(SRC.parent)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "src/ must not contain result labels:\n" + "\n".join(offenders))


def test_the_scan_would_actually_catch_a_leak(tmp_path):
    """A test that always passes is not a test. Prove the scan bites."""
    leak = tmp_path / "leaky.py"
    leak.write_text("data = yaml.safe_load(open('benchmarks/outcomes.yaml'))\n")
    assert BANNED in leak.read_text().lower()
    assert any(p.search(leak.read_text()) for p in BANNED_TOKENS)
