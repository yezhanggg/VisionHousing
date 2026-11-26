"""Public files explain rules in words, not by internal planning ids.

The team keeps its planning notes outside the repository. Codes from those
notes (decision numbers like ``D-07``, task numbers like ``P4-05``, job numbers
like ``job T3``, or short codes like ``decision H8``) mean nothing to a reader of the
code, the documents or the website. Every rule is named by what it says instead,
for example "the major-stop rule: rail stations, and bus stops with at least 64
weekday departures".

This test reads every text file under ``src/``, ``assumptions/``, ``docs/`` and
``web/js/``, plus the hand-written top-level documents, and fails on any such id.

Two kinds of look-alike text are allowed, precisely:

- web addresses (bill pages such as ``.../legislation/H0707/``) are removed
  before the search;
- ``H1 2026`` (first half of a year) and the Census table name ``table H1``.

A bare ``H<number>`` is not flagged: local ordinances use such codes for
use classes (for example an "H1 Multigenerational Apartment").

``PENDING`` lists files other work is still changing. They are checked by a
separate expected-failure test until those strings are rewritten; then the list
is emptied and the expected-failure mark removed.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCAN_DIRS = ("src", "assumptions", "docs", "web/js")
SCAN_FILES = ("README.md", "DISCLOSURES.md", "DATA_PROVENANCE.md")
SKIP_PARTS = {"__pycache__", "node_modules", "vendor"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".js", ".mjs", ".css", ".html", ".json", ".txt"}

# Files still being edited by other work (none left: every file is checked).
PENDING: tuple[str, ...] = ()

PRIVATE_ID = re.compile(
    r"\bD-\d{2}\b"  # decision numbers
    r"|\bP\d-\d{2}\b"  # task numbers
    r"|\b(?:[Dd]ecision|[Rr]ule|[Jj]ob|[Tt]ask)\s+H\d+\b"  # short codes such as "decision H8"
    r"|\b(?:backlog|job|task)\s+[A-Z]\d+(?:-\d{2})?\b"  # "job T3", "task P4-08"
)
URL = re.compile(r"https?://\S+")
ALLOWED = (
    re.compile(r"\bH1 20\d\d\b"),  # first half of a year
    re.compile(r"\btable H1\b"),  # Census redistricting table H1
)


def _public_files() -> list[Path]:
    files = [ROOT / f for f in SCAN_FILES if (ROOT / f).is_file()]
    for d in SCAN_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and p.suffix in TEXT_SUFFIXES and not SKIP_PARTS & set(p.parts):
                files.append(p)
    return files


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _is_pending(rel: str) -> bool:
    return any(rel == x or (x.endswith("/") and rel.startswith(x)) for x in PENDING)


def find_ids(text: str) -> list[tuple[int, str]]:
    """Return (line number, id) for every private-looking id in ``text``."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        line = URL.sub("", line)
        for rx in ALLOWED:
            line = rx.sub("", line)
        hits += [(n, m.group(0)) for m in PRIVATE_ID.finditer(line)]
    return hits


def _scan(pending: bool) -> list[str]:
    found = []
    for p in _public_files():
        rel = _rel(p)
        if _is_pending(rel) != pending:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [f"{rel}:{n}: {hit}" for n, hit in find_ids(text)]
    return found


def test_patterns_catch_ids_and_allow_look_alikes():
    assert [h for _, h in find_ids("rule D-07; see P4-05 and job T3; decision H8")] == [
        "D-07",
        "P4-05",
        "job T3",
        "decision H8",
    ]
    clean = (
        "https://legislature.idaho.gov/sessioninfo/2026/legislation/H0707/ "
        "265 for H1 2026; the Census redistricting table H1; R-20 districts; "
        "H1 Multigenerational Apartment; "
        "24 V.S.A. § 4412(1)(D); Tier 2; tract T1"
    )
    assert find_ids(clean) == []


def test_public_files_have_no_private_ids():
    found = _scan(pending=False)
    assert not found, "Replace these planning ids with plain words:\n" + "\n".join(found)


def test_pending_files_have_no_private_ids():
    found = _scan(pending=True)
    assert not found, "Still to rewrite:\n" + "\n".join(found)
