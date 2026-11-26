"""Shared helpers for the law facts in the ``zoning``, ``plans`` and ``state_law`` sections.

Plain-language summary
----------------------
Reads the law tables of the pack (``muni_zoning_rules``, ``law_documents``,
``county_land_use``, ``state_law_provisions``; see :mod:`visionhousing.law`)
and turns one municipality's rules into short facts. Every fact names the
document it was read from (its source id), the section, the date it was read,
how (by hand, or drafted by the rule-based reader) and whether a person has
verified it. A municipality nobody has read says "not read yet", never "no".
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from visionhousing.profile.context import ProfileContext
from visionhousing.profile.model import Fact

__all__ = [
    "ADU_WORDS",
    "doc_source",
    "muni_geoids",
    "rule_facts",
    "rules_table",
]

ADU_WORDS = {
    "by_right": "allowed by right",
    "conditional": "allowed as a conditional use",
    "special_exception": "allowed by special exception",
    "prohibited": "not allowed",
    "not_addressed": "not addressed by the ordinance",
    "unknown": "not read yet (unknown)",
}
"""Plain words for each ADU rule."""

RULE_SOURCE = "pa_municipal_ordinances"


def rules_table(ctx: ProfileContext) -> pd.DataFrame:
    """The ``muni_zoning_rules`` table with text codes (raises MissingDataError when absent)."""
    key = "_law_rules"
    if key not in ctx._cache:
        t = ctx.need("muni_zoning_rules").copy()
        t["geoid"] = t["geoid"].astype(str).str.zfill(10)
        ctx._cache[key] = t
    return ctx._cache[key]


def doc_source(ctx: ProfileContext, doc_id: Any) -> str:
    """The source id of a document in ``law_documents`` (the municipal ordinances by default)."""
    docs = ctx.table("law_documents")
    if docs is None or not isinstance(doc_id, str) or not doc_id:
        return RULE_SOURCE
    hit = docs.loc[docs["doc_id"] == doc_id, "source_id"]
    sid = str(hit.iloc[0]) if len(hit) else RULE_SOURCE
    return sid if sid in ctx.registry else RULE_SOURCE


def muni_geoids(munis: pd.DataFrame) -> pd.Series:
    """10-digit municipality codes (state, county, county subdivision) of ``municipal_planning``."""
    return munis["county"].astype(str).str.zfill(5) + munis["cousub"].astype(str).str.zfill(5)


def _how(row: pd.Series) -> str:
    method = str(row.get("method") or "")
    if method == "unread":
        return ""
    who = "read by hand" if method == "hand" else "drafted by the rule-based reader"
    when = row.get("reviewed_on")
    status = str(row.get("status") or "draft")
    parts = [str(row.get("section_cite") or "").strip(), who]
    if isinstance(when, str) and when:
        parts.append(f"checked {when}")
    parts.append("verified" if status == "verified" else "draft, not yet verified")
    return "; ".join(p for p in parts if p)


def _num_text(v: Any, unit: str) -> tuple[float | None, str]:
    x = pd.to_numeric(v, errors="coerce")
    if pd.isna(x):
        return None, "not read yet (unknown)"
    x = float(x)
    if unit == "square feet":
        return x, f"{x:,.0f} sq ft"
    return x, (f"{x:g} per home")


def rule_facts(ctx: ProfileContext, key: str, row: pd.Series, name: str) -> list[Fact]:
    """Facts for one municipality's rules: ADU rule, smallest lot, parking, housing types."""
    source = doc_source(ctx, row.get("doc_id"))
    how = _how(row)
    unread = str(row.get("method") or "unread") == "unread"
    year = str(row.get("reviewed_on") or "") or ("not read" if unread else "date not recorded")
    vintage = ctx.vintage(source)
    note_bits = [how] if how else ["no one has read this municipality's ordinance yet"]
    if isinstance(row.get("note"), str) and row.get("note"):
        note_bits.append(str(row["note"]))
    if isinstance(row.get("evidence_quote"), str) and row.get("evidence_quote"):
        note_bits.append(f'quote: "{row["evidence_quote"]}"')
    note = ". ".join(note_bits)

    def fact(fid: str, label: str, value: Any, unit: str, text: str) -> Fact:
        return Fact(
            id=f"{key}.{fid}",
            label=label,
            value=value,
            unit=unit,
            text=text,
            source=source,
            vintage=vintage,
            year=year,
            measured_at="municipality",
            note=note,
        )

    status = str(row.get("adu_status") or "unknown")
    adu_text = ADU_WORDS.get(status, status)
    extras = []
    for col, yes, no in (
        ("adu_detached", "detached allowed", "detached not allowed"),
        ("adu_owner_occupancy", "owner must live on the lot", "no owner-occupancy rule"),
    ):
        v = pd.to_numeric(row.get(col), errors="coerce")
        if not pd.isna(v):
            extras.append(yes if v else no)
    if extras and status != "unknown":
        adu_text += f" ({'; '.join(extras)})"
    if how:
        adu_text += f" [{how}]"
    out = [fact("adu_rule", f"ADU rule in {name}", status, "text", adu_text)]
    lot, lot_text = _num_text(row.get("min_lot_sf_smallest_res_district"), "square feet")
    out.append(
        fact("min_lot", f"Smallest residential minimum lot in {name}", lot, "square feet", lot_text)
    )
    for col, label in (
        ("parking_min_per_unit_sf", "single-family home"),
        ("parking_min_per_unit_mf", "apartment"),
    ):
        v, t = _num_text(row.get(col), "spaces")
        if v is not None:
            t = f"{v:g} spaces per {label}"
        out.append(fact(col, f"Parking minimum per {label} in {name}", v, "spaces per unit", t))
    types = row.get("permitted_types")
    if isinstance(types, str) and types:
        words = ", ".join(t.replace("_", " ") for t in types.split(";"))
        out.append(
            fact("permitted_types", f"Housing types permitted in {name}", types, "text", words)
        )
    return out
