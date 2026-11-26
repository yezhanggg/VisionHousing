"""Command-line actions for the law tables (``visionhousing law-draft``, ``law-tables``).

Plain-language summary
----------------------
- ``law-draft``: read one ordinance PDF that a municipality publishes on its
  own website, draft its zoning facts by rule and write them, with the quote
  and offset behind each value, to a review CSV. The rows are drafts: a person
  checks each one and copies what holds into ``assumptions/law/municipal_rules.yaml``
  as a verified row. Addresses on commercial code hosts are refused.
- ``law-tables``: build the four law tables and add them to an existing state
  pack (``data/packs/<STATE>``), rewriting its data dictionary and manifest; in
  every state (also those without municipal tables) it adds or refreshes
  ``law_provision_version`` from ``assumptions/law/timeline.yaml``.
- ``law-priorities``: rank the unread municipalities by the value of reading
  them next (:mod:`visionhousing.law.priorities`) and write the list to a CSV
  (default ``data/outputs/law_priorities_<STATE>.csv``, not part of the repository).
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["law_draft", "law_priorities", "law_tables"]


def law_draft(pdf: Path, url: str, geoid: str, out: Path, doc_id: str = "") -> int:
    """Draft one municipality's rules from ``pdf`` (downloaded from ``url``) into ``out``."""
    from visionhousing.law.extract import (
        BlockedSourceError,
        check_source,
        draft_rule,
        extract_facts,
        write_review_csv,
    )
    from visionhousing.law.text import pdf_text

    try:
        check_source(url)
    except BlockedSourceError as err:
        print(f"law-draft: {err}", file=sys.stderr)
        return 2
    if Path(pdf).suffix.lower() != ".pdf" or not Path(pdf).is_file():
        print(f"law-draft: {pdf} is not a PDF file", file=sys.stderr)
        return 2
    try:
        doc = pdf_text(pdf)
    except ValueError as err:
        print(f"law-draft: {err}", file=sys.stderr)
        return 2
    if doc.unreadable_pages:
        pages = ", ".join(str(p) for p in doc.unreadable_pages)
        print(f"law-draft: pages the reader could not read (left empty): {pages}")
    facts = extract_facts(doc)
    row = draft_rule(facts, geoid=geoid, doc_id=doc_id or Path(pdf).stem.lower())
    write_review_csv([(row, facts)], out)
    known = ", ".join(facts.known) or "nothing"
    print(f"law-draft: drafted {known} (confidence {row.confidence}); review {out}")
    return 0


def law_tables(state: str, raw: Path, packs: Path) -> int:
    """Add the law tables to the built pack of ``state``.

    Every state gets ``law_provision_version`` (from the timeline records); a
    pack with a ``municipal_planning`` table (Pennsylvania) also gets the four
    municipal and state law tables.
    """
    from visionhousing.law.tables import add_law_to_pack, add_provision_versions

    folder = Path(packs) / state.upper()
    if not (folder / "pack_manifest.json").is_file():
        print(f"law-tables: {folder} is not a built pack; build the pack first", file=sys.stderr)
        return 1
    if (folder / "municipal_planning.csv.gz").is_file():
        built = add_law_to_pack(folder, Path(raw))
        for note in built.notes:
            print(f"law-tables: {note}")
        for name, frame in built.tables.items():
            print(f"law-tables: {name}: {len(frame):,} rows")
    else:
        print(f"law-tables: {folder} has no municipal_planning table; provision versions only")
    versions = add_provision_versions(folder, Path(raw))
    print(f"law-tables: law_provision_version: {len(versions):,} rows")
    return 0


def law_priorities(state: str, packs: Path, out: Path | None, top: int = 30) -> int:
    """Write the review-priority list of ``state`` to ``out`` and print the first ``top``."""
    import pandas as pd

    from visionhousing.law.hooks import load_hooks
    from visionhousing.law.priorities import load_priorities, read_adu_run, review_priorities
    from visionhousing.law.tables import pack_weights

    code = state.upper()
    folder = Path(packs) / code
    need = ["muni_zoning_rules", "municipal_planning"]
    missing = [n for n in need if not (folder / f"{n}.csv.gz").is_file()]
    if missing:
        print(
            f"law-priorities: {folder} has no {', '.join(missing)}; run law-tables", file=sys.stderr
        )
        return 1

    def read(name: str, **kw):
        path = folder / f"{name}.csv.gz"
        return pd.read_csv(path, **kw) if path.is_file() else None

    rules = read("muni_zoning_rules", dtype={"geoid": str})
    munis = read("municipal_planning", dtype={"county": str, "cousub": str, "dced_id": str})
    if "sf_lots" not in rules.columns or rules["sf_lots"].isna().all():
        weights = pack_weights(folder)
        if weights is None:
            print("law-priorities: the pack has no lot weights; run law-tables", file=sys.stderr)
            return 1
        rules = rules.drop(columns=["sf_lots", "res_land_ha"], errors="ignore").merge(
            weights, on="geoid", how="left"
        )
    params = load_priorities()
    run_path = Path(str(params.get("adu_run", "")).format(state=code))
    if not run_path.is_absolute():
        run_path = Path(__file__).resolve().parents[3] / run_path
    ranked = review_priorities(
        rules,
        munis,
        counties=read("counties", dtype={"geoid": str}),
        typology_tract=read("typology_tract", dtype={"geoid": str}),
        tract_munis=read(
            "tract_municipalities", dtype={"tract": str, "county": str, "cousub": str}
        ),
        adu_run=read_adu_run(run_path),
        params=params,
        hooks=load_hooks(),
    )
    out = Path(out) if out else Path("data/outputs") / f"law_priorities_{code}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(out, index=False)
    cols = ["rank", "unit_id", "county_name", "names", "unknown_lots", "sensitivity",
            "representativeness", "effort", "score"]  # fmt: skip
    print(ranked[cols].head(top).to_string(index=False))
    print(f"law-priorities: {len(ranked):,} review units ranked; wrote {out}")
    return 0
