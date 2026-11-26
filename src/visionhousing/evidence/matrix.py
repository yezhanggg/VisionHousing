"""The completeness matrix: for every state and DC, which policy types have been reviewed.

Plain-language summary
----------------------
The matrix has one row per state (the 50 states and DC) and one column per
policy type (twelve, see :data:`~visionhousing.evidence.schema.POLICY_TYPES`).
Each cell takes one of four values:

- ``reviewed``: at least one **verified** policy record of that type exists for
  the state (its citation and dates were read on an official page) **and** at
  least one of the verified records is an enacted law (status ``enacted``,
  ``legally_effective``, ``stayed``, ``locally_implemented`` or
  ``practically_usable``, :data:`ENACTED_STATUSES`);
- ``reviewed_no_enacted_law``: the official text was read, but every verified
  record of that type is a pending or lapsed bill, a struck-down law, or
  another record that is not law in force (``not_law``, ``unknown``). The state
  was looked at and has no enacted law of that type in this panel;
- ``discovered_unreviewed``: something of that type was found (an unverified
  record, or a candidate from a tracker or search) but nobody has yet read the
  official text;
- ``none_found``: nothing was found in the sources searched so far. This is
  **not** a statement that the state has no such law; it only says what this
  panel has looked at.

A record counts toward every policy type it lists. City records (for example
Minneapolis) count toward their state's row, with the jurisdiction named in
the cell detail. Study records do not make a cell ``reviewed`` (a study is not
a law), but they are counted in the cell's ``studies`` number so the interface
can show where outcome evidence exists.

:func:`build_matrix` makes the matrix; :func:`write_outputs` saves it as a CSV
(one row per state and type, with the record ids) and a small JSON for the
interface. Both go to ``data/outputs/``, which is not published.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from visionhousing.evidence.records import Panel
from visionhousing.evidence.schema import POLICY_TYPES, STATES

__all__ = [
    "CELL_VALUES",
    "ENACTED_STATUSES",
    "OUT_DIR",
    "build_matrix",
    "matrix_summary",
    "write_outputs",
]

CELL_VALUES = ("reviewed", "reviewed_no_enacted_law", "discovered_unreviewed", "none_found")
"""The four cell values, strongest first."""

ENACTED_STATUSES = (
    "enacted",
    "legally_effective",
    "stayed",
    "locally_implemented",
    "practically_usable",
)
"""Implementation statuses of an enacted law (signed, whether or not yet in force or paused
by a court). A ``stayed`` law is enacted; a ``struck_down`` one is void and does not count."""

OUT_DIR = Path("data") / "outputs"
CSV_NAME = "evidence_states_matrix.csv"
JSON_NAME = "evidence_states_matrix.json"


def build_matrix(panel: Panel) -> dict[str, dict[str, dict[str, Any]]]:
    """Return ``{state: {policy_type: cell}}`` for every state and DC and every type.

    Each cell has ``status`` (one of :data:`CELL_VALUES`), ``verified`` and
    ``unverified`` (record ids), ``enacted`` (the verified ids that are enacted
    law, :data:`ENACTED_STATUSES`), ``studies`` (study ids), ``discovered`` (number
    of tracker or search candidates) and ``jurisdictions`` (places named in the
    policy records).
    """
    cells: dict[str, dict[str, dict[str, Any]]] = {
        st: {
            pt: {
                "status": "none_found",
                "verified": [],
                "unverified": [],
                "enacted": [],
                "studies": [],
                "discovered": 0,
                "jurisdictions": [],
            }
            for pt in POLICY_TYPES
        }
        for st in STATES
    }
    for rec in panel.records:
        if rec.state not in cells:  # multi-state studies (US) have no row
            continue
        for pt in rec.policy_types:
            cell = cells[rec.state][pt]
            if rec.kind == "study":
                cell["studies"].append(rec.id)
                continue
            cell["verified" if rec.verified else "unverified"].append(rec.id)
            if rec.verified and rec.implementation_status in ENACTED_STATUSES:
                cell["enacted"].append(rec.id)
            if rec.jurisdiction not in cell["jurisdictions"]:
                cell["jurisdictions"].append(rec.jurisdiction)
    for entry in panel.discovery:
        cells[entry.state][entry.policy_type]["discovered"] += 1
    for row in cells.values():
        for cell in row.values():
            if cell["enacted"]:
                cell["status"] = "reviewed"
            elif cell["verified"]:
                cell["status"] = "reviewed_no_enacted_law"
            elif cell["unverified"] or cell["discovered"]:
                cell["status"] = "discovered_unreviewed"
    return cells


def matrix_summary(cells: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Counts of cells by status, overall and by policy type, and states with any review.

    ``states_with_review`` lists states with at least one ``reviewed`` cell (an
    enacted law read); ``states_with_any_reading`` adds those whose only read
    records are bills or other records that are not enacted law.
    """
    overall = Counter(c["status"] for row in cells.values() for c in row.values())
    by_type = {pt: dict(Counter(cells[st][pt]["status"] for st in cells)) for pt in POLICY_TYPES}
    return {
        "states": len(cells),
        "policy_types": len(POLICY_TYPES),
        "cells": sum(overall.values()),
        "by_status": {v: overall.get(v, 0) for v in CELL_VALUES},
        "by_type": {pt: {v: by_type[pt].get(v, 0) for v in CELL_VALUES} for pt in POLICY_TYPES},
        "states_with_review": sorted(
            st for st, row in cells.items() if any(c["status"] == "reviewed" for c in row.values())
        ),
        "states_with_any_reading": sorted(
            st
            for st, row in cells.items()
            if any(c["status"] in ("reviewed", "reviewed_no_enacted_law") for c in row.values())
        ),
        "states_with_anything": sorted(
            st
            for st, row in cells.items()
            if any(c["status"] != "none_found" for c in row.values())
        ),
    }


def compact(cells: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """The small form for the interface: one status letter string per state, plus a key.

    ``R`` reviewed (an enacted law read), ``r`` reviewed with no enacted law (only
    bills or other records that are not law), ``d`` discovered but unreviewed,
    ``.`` none found, in the order of ``types``.
    """
    letter = {
        "reviewed": "R",
        "reviewed_no_enacted_law": "r",
        "discovered_unreviewed": "d",
        "none_found": ".",
    }
    return {
        "types": list(POLICY_TYPES),
        "key": {v: k for k, v in letter.items()},
        "rows": {
            st: "".join(letter[cells[st][pt]["status"]] for pt in POLICY_TYPES) for st in cells
        },
    }


def write_outputs(panel: Panel, out_dir: Path | None = None) -> tuple[Path, Path]:
    """Write the CSV (state and type rows) and the small JSON; return their paths."""
    out = Path(out_dir) if out_dir is not None else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    cells = build_matrix(panel)
    csv_path, json_path = out / CSV_NAME, out / JSON_NAME
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["state", "policy_type", "status", "verified_records", "enacted_records",
             "unverified_records", "studies", "discovered_candidates", "jurisdictions"]
        )  # fmt: skip
        for st, row in cells.items():
            for pt, c in row.items():
                w.writerow(
                    [st, pt, c["status"], ";".join(c["verified"]), ";".join(c["enacted"]),
                     ";".join(c["unverified"]),
                     ";".join(c["studies"]), c["discovered"], ";".join(c["jurisdictions"])]
                )  # fmt: skip
    payload = {"summary": matrix_summary(cells), "panel": panel.summary(), **compact(cells)}
    json_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return csv_path, json_path
