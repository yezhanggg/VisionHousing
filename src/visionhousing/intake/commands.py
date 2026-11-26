"""Command-line entry points: ``visionhousing state-readiness``, ``state-intake`` and ``reproduce``.

Plain-language summary
----------------------
- ``visionhousing state-readiness --state XX`` prints the five intake and
  release steps with pass / fail / unknown and the checks behind each
  (:mod:`visionhousing.intake.readiness`). ``--json`` prints the same as JSON.
- ``visionhousing state-intake --state XX`` prints a short summary of the
  intake manifest; ``--out FILE`` writes the full manifest as JSON
  (:mod:`visionhousing.intake.manifest`).
- ``visionhousing reproduce ID`` prints the command, seed and hashes behind
  one record id such as ``PA:adu:reform_results.adu.added``
  (:mod:`visionhousing.intake.reproduce`).

None of them fetches anything; they read the built pack, the repository's
records and the static site's saved runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

__all__ = ["COMMANDS", "add_parser", "run"]

COMMANDS = ("state-readiness", "state-intake", "reproduce")


def add_parser(sub: Any) -> None:
    """Add the three subcommands to the main parser's subparsers."""
    rd = sub.add_parser(
        "state-readiness", help="Check a state against the five-step intake and release standard"
    )
    rd.add_argument("--state", default="PA", help="two-letter state code (default PA; TL = test)")
    rd.add_argument("--json", action="store_true", help="print JSON instead of text")
    it = sub.add_parser("state-intake", help="Summarise or write a state's input intake manifest")
    it.add_argument("--state", default="PA", help="two-letter state code (default PA; TL = test)")
    it.add_argument("--out", type=Path, default=None, help="write the full manifest JSON here")
    rp = sub.add_parser("reproduce", help="Show how to reproduce one number (by record id)")
    rp.add_argument("record_id", help="for example PA:adu:reform_results.adu.added")


def run(args: argparse.Namespace) -> int:
    """Run the chosen subcommand; return the exit code."""
    if args.command == "state-readiness":
        from visionhousing.intake.readiness import readiness, to_text

        report = readiness(args.state)
        print(json.dumps(report, indent=1) if args.json else to_text(report), end="")
        return 0
    if args.command == "state-intake":
        from visionhousing.intake.manifest import build_manifest, summary_lines

        try:
            manifest = build_manifest(args.state)
        except FileNotFoundError as err:
            print(f"state-intake: {err}", file=sys.stderr)
            return 1
        print("\n".join(summary_lines(manifest)))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(manifest, indent=1, default=str) + "\n", "utf-8")
            print(f"wrote {args.out}")
        return 0
    from visionhousing.intake.reproduce import ReproduceError, reproduce

    try:
        print(json.dumps(reproduce(args.record_id), indent=1))
    except ReproduceError as err:
        print(f"reproduce: {err}", file=sys.stderr)
        return 1
    return 0
