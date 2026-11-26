"""The ``visionhousing evidence-states`` command: check records, write the matrix, probe a city.

Plain-language summary
----------------------
Three actions:

- ``check`` (the default) loads every comparison record and prints the counts:
  records, verified, unverified, and each unverified record's reason;
- ``matrix`` also writes the completeness matrix to
  ``data/outputs/evidence_states_matrix.csv`` and ``.json`` (not published);
- ``phl-fields`` reads the field list of Philadelphia's public permit layer and
  counts how many rows fill each date field (needs the network).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visionhousing.evidence.matrix import OUT_DIR, build_matrix, matrix_summary, write_outputs
from visionhousing.evidence.records import load_panel

__all__ = ["add_parser", "run"]


def add_parser(sub: argparse._SubParsersAction) -> None:
    """Add the ``evidence-states`` sub-command to the main parser."""
    p = sub.add_parser(
        "evidence-states",
        help="Check the cross-state comparison records and write the completeness matrix",
    )
    p.add_argument(
        "action", nargs="?", default="check", choices=["check", "matrix", "phl-fields"],
        help="check (default), matrix (write CSV and JSON), phl-fields (live field check)",
    )  # fmt: skip
    p.add_argument("--out", type=Path, default=OUT_DIR, help="folder for the matrix files")


def run(args: argparse.Namespace) -> int:
    """Run the chosen action; return the exit code."""
    if args.action == "phl-fields":
        from visionhousing.evidence.phl_fields import check_fields

        print(json.dumps(check_fields(), indent=1))
        return 0
    panel = load_panel()
    s = panel.summary()
    print(
        f"evidence-states: {s['records']} records ({s['verified']} verified, "
        f"{s['unverified']} unverified) in {len(panel.files)} files; "
        f"{s['discovery_candidates']} discovery candidates in {s['discovery_states']} states"
    )
    for r in panel.records:
        if not r.verified:
            print(f"  unverified {r.id}: {r.verification_note[:140]}")
    m = matrix_summary(build_matrix(panel))
    print(
        f"matrix: {m['cells']} cells; " + ", ".join(f"{k} {v}" for k, v in m["by_status"].items())
    )
    if args.action == "matrix":
        csv_path, json_path = write_outputs(panel, args.out)
        print(f"wrote {csv_path} and {json_path}")
    return 0
