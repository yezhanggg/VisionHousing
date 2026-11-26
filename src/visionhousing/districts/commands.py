"""The ``visionhousing districts`` command: add district tables and outlines to a built pack.

Plain-language summary
----------------------
``visionhousing districts --state PA`` fetches (or reuses) the Census block
equivalency files and district outlines for both chambers, makes the weights
and the district list, writes them into ``data/packs/PA/``, and prints every
file's size, the pack's total against its size budget, and how many districts
carry a split-share caveat.
"""

from __future__ import annotations

import sys
from pathlib import Path

from visionhousing.data.pack import PACK_BUDGET_BYTES

__all__ = ["districts_cmd"]


def districts_cmd(state: str, raw: Path, packs: Path) -> int:
    """Add the district tables to the built pack of ``state``; print sizes. Returns an exit code."""
    from visionhousing.districts.build import add_districts
    from visionhousing.districts.results import SPLIT_PARAMETER
    from visionhousing.ledger import load_ledger

    folder = Path(packs) / state.upper()
    if not (folder / "tracts.csv.gz").is_file():
        print(f"districts: {folder} has no tracts table; build the pack first", file=sys.stderr)
        return 1
    built = add_districts(folder, Path(raw))
    for path, size in sorted(built.files.items()):
        print(f"{path:<40} {size:>10,} bytes")
    added = sum(built.files.values())
    print(f"district files: {added:,} bytes")
    used = built.pack_bytes / PACK_BUDGET_BYTES
    print(f"pack total: {built.pack_bytes:,} of {PACK_BUDGET_BYTES:,} bytes ({used:.1%})")
    threshold = float(load_ledger()[SPLIT_PARAMETER].central)
    table = built.tables["districts"]
    for chamber, grp in table.groupby("chamber"):
        flagged = int((grp["split_share"] > threshold).sum())
        median = grp["split_share"].median()
        print(
            f"{chamber}: {len(grp)} districts, split share median {median:.2f}, "
            f"{flagged} above the caveat threshold {threshold:g}"
        )
    return 0
