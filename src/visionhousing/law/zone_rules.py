"""Reviewed zoning rules by district, overlay and date: the ``zone_rule`` table.

Plain-language summary
----------------------
A municipality's zoning is not one rule. Each **zoning district** (a mapped
area with its own code, such as Philadelphia's ``RSA-5``) has its own uses and
standards, and an **overlay** (a second map laid over the districts) can change
some of them. Rules also change over time. So a rule here is one row for one
jurisdiction, one district (or ``*`` for every district), one overlay
(``none`` for the base rule) and one **validity interval** (``valid_from`` to
``valid_to``), for one **prototype**: a lot with one existing single-family
home, the lot every benchmark reform starts from.

The rows are in ``assumptions/law/zone_rules.yaml``. This module:

1. **Loads and checks them.** Every rule field must be stated; ``unknown`` is a
   value (nobody read it, or the text does not answer it), never "no". An
   overlay row may say ``inherit`` (the overlay does not change that field). A
   use the text does not list (``not_listed``) counts as prohibited only with
   ``exclusivity_clause: verified``. A row whose start is not stated
   (``valid_from_unknown``) counts only from its ``reviewed_on`` date, as in the
   law timeline (:mod:`visionhousing.law.timeline`).
2. **Resolves the rule of one place on one date** (:func:`resolve`): the base
   row of the district (an exact district code beats ``*``; among equals the
   latest start wins), then each overlay row of an overlay the place lies in,
   in ascending ``precedence`` (then overlay code), each replacing the fields
   it states. No base row means the district is missing from the table: every
   field is unknown.
3. **Sorts the place into an opportunity class per benchmark**
   (:func:`classify`), by comparing what the rule allows today with what the
   benchmark needs (``benchmarks`` in ``assumptions/law/zone_weights.yaml``):

   - ``known_by_right``: allowed by right today and the benchmark changes
     nothing (its pure legalization increment is zero);
   - ``conditions_relaxed``: allowed by right today, but the benchmark drops a
     condition (size, form, owner rule, parking) or a condition was not read;
   - ``newly_eligible``: not allowed today (prohibited, or not listed where a
     verified clause bars unlisted uses); the benchmark allows it;
   - ``discretionary``: allowed today only after a hearing; the benchmark makes
     it by right;
   - ``unresolved``: the rule needed is unknown.

Pure functions, no file writes. The geometry (which lots lie in which district
and overlay) is in :mod:`visionhousing.law.rule_weights`.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

__all__ = [
    "BENCHMARKS",
    "CLASSES",
    "RULES_PATH",
    "RULE_FIELDS",
    "SETTINGS_PATH",
    "Resolved",
    "ZoneRule",
    "change_dates",
    "classify",
    "counts_on",
    "load_rows",
    "load_settings",
    "relevant_overlays",
    "resolve",
    "window_intervals",
    "zone_rule_table",
]

_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = _ROOT / "assumptions" / "law" / "zone_rules.yaml"
SETTINGS_PATH = _ROOT / "assumptions" / "law" / "zone_weights.yaml"

UNKNOWN = "unknown"
INHERIT = "inherit"

RULE_FIELDS: tuple[str, ...] = (
    "use_permission",
    "review_path",
    "units_by_right",
    "multi_unit_review_path",
    "density_far",
    "height_max_ft",
    "lot_min_sf",
    "adu_permission",
    "adu_forms",
    "adu_max_sf",
    "adu_owner_occupancy",
    "adu_max_per_lot",
    "adu_min_lot_sf",
    "parking_min_sf",
    "parking_min_adu",
    "parking_overlay_change",
    "utility_conditions",
    "exclusivity_clause",
)
"""The rule fields every row states (``unknown`` allowed; ``inherit`` only in overlay rows)."""

META_FIELDS: tuple[str, ...] = (
    "rule_id",
    "jurisdiction",
    "district",
    "overlay",
    "prototype",
    "valid_from",
    "valid_from_unknown",
    "valid_to",
    "review_state",
    "doc_id",
    "section_cite",
    "reviewed_on",
)
"""Fields every row must have besides the rule fields."""

REVIEW_STATES = ("verified_code", "guidance", "unread")
ADU_PERMISSIONS = (
    "by_right",
    "special_exception",
    "conditional",
    "prohibited",
    "not_listed",
    UNKNOWN,
)
EXCLUSIVITY = ("verified", "hearing_route", "unread", "none_found")
CLASSES = ("known_by_right", "conditions_relaxed", "newly_eligible", "discretionary", "unresolved")
"""The opportunity classes, in the order shares are reported."""
BENCHMARKS = ("adu", "missing_middle", "tod", "parking")
"""The benchmarks this table answers (fees are not zoning rules)."""


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """The pilot's settings (``assumptions/law/zone_weights.yaml``)."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _day(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).strip()


@dataclass(frozen=True)
class ZoneRule:
    """One reviewed row: one jurisdiction, district, overlay and validity interval."""

    rule_id: str
    jurisdiction: str
    district: str
    overlay: str
    prototype: str
    valid_from: date | None
    valid_from_unknown: bool
    valid_to: date | None
    review_state: str
    reviewed_on: date
    fields: Mapping[str, str]
    maps: tuple[str, ...] = ("*",)
    precedence: int = 0
    meta: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_overlay(self) -> bool:
        """True for a row that changes the base rule inside an overlay."""
        return self.overlay != "none"

    @property
    def counts_from(self) -> date:
        """First day the row counts: its start, or its review date when the start is unknown."""
        if self.valid_from_unknown or self.valid_from is None:
            return self.reviewed_on
        return self.valid_from


def _check(row: Mapping[str, Any], i: int) -> None:
    missing = [k for k in (*META_FIELDS, *RULE_FIELDS) if k not in row]
    if missing:
        raise ValueError(f"zone rule {i} ({row.get('rule_id')}): missing {', '.join(missing)}")
    rid = row["rule_id"]
    overlay = _text(row["overlay"])
    for k in RULE_FIELDS:
        v = _text(row[k])
        if v == "":
            raise ValueError(f"zone rule {rid}: {k} is blank; write 'unknown' when not read")
        if v == INHERIT and overlay == "none":
            raise ValueError(f"zone rule {rid}: {k} is 'inherit' in a base row")
    state = _text(row["review_state"])
    if state not in REVIEW_STATES:
        raise ValueError(f"zone rule {rid}: review_state {state!r} not in {REVIEW_STATES}")
    if state == "unread" and any(
        _text(row[k]) not in (UNKNOWN, INHERIT) for k in RULE_FIELDS if k != "exclusivity_clause"
    ):
        raise ValueError(f"zone rule {rid}: an unread row can only hold 'unknown'")
    perm = _text(row["adu_permission"])
    if perm not in (*ADU_PERMISSIONS, INHERIT):
        raise ValueError(f"zone rule {rid}: adu_permission {perm!r} not in {ADU_PERMISSIONS}")
    excl = _text(row["exclusivity_clause"])
    if excl not in (*EXCLUSIVITY, INHERIT):
        raise ValueError(f"zone rule {rid}: exclusivity_clause {excl!r} not in {EXCLUSIVITY}")
    if bool(row["valid_from_unknown"]) is False and not _text(row["valid_from"]):
        raise ValueError(f"zone rule {rid}: valid_from is empty; set valid_from_unknown: true")
    start, end = _day(row["valid_from"]), _day(row["valid_to"])
    if start and end and end <= start:
        raise ValueError(f"zone rule {rid}: valid_to is not after valid_from")


def load_rows(
    path: Path = RULES_PATH, rows: Sequence[Mapping[str, Any]] | None = None
) -> list[ZoneRule]:
    """Read and check the reviewed rows; an overlay list ``/A,/B`` gives one row per overlay."""
    raw = rows if rows is not None else yaml.safe_load(Path(path).read_text("utf-8"))["rows"]
    out: list[ZoneRule] = []
    seen: set[str] = set()
    for i, row in enumerate(raw):
        _check(row, i)
        overlays = [o.strip() for o in _text(row["overlay"]).split(",") if o.strip()]
        for o in overlays:
            rid = str(row["rule_id"]) if len(overlays) == 1 else f"{row['rule_id']}:{o}"
            if rid in seen:
                raise ValueError(f"zone rule {rid}: duplicate rule_id")
            seen.add(rid)
            meta = {
                k: _text(row.get(k))
                for k in (
                    "doc_id",
                    "section_cite",
                    "evidence_quote",
                    "evidence_offset",
                    "text_sha256",
                    "timeline_record",
                    "exclusions",
                    "note",
                )
            }
            out.append(
                ZoneRule(
                    rule_id=rid,
                    jurisdiction=_text(row["jurisdiction"]),
                    district=_text(row["district"]),
                    overlay=o,
                    prototype=_text(row["prototype"]),
                    valid_from=_day(row["valid_from"]),
                    valid_from_unknown=bool(row["valid_from_unknown"]),
                    valid_to=_day(row["valid_to"]),
                    review_state=_text(row["review_state"]),
                    reviewed_on=_day(row["reviewed_on"]) or date.min,
                    fields={k: _text(row[k]) for k in RULE_FIELDS},
                    maps=tuple(str(m) for m in row.get("maps") or ["*"]),
                    precedence=int(row.get("precedence") or 0),
                    meta=meta,
                )
            )
    return out


def counts_on(rule: ZoneRule, on: date) -> bool:
    """Whether a row is in force on a date (its start, or review date when unknown, to its end)."""
    if on < rule.counts_from:
        return False
    return rule.valid_to is None or on < rule.valid_to


def _map_ok(rule: ZoneRule, map_name: str | None) -> bool:
    return map_name is None or "*" in rule.maps or map_name in rule.maps


@dataclass(frozen=True)
class Resolved:
    """The rule of one place on one date: merged fields and the rows they came from."""

    fields: Mapping[str, str]
    rule_ids: tuple[str, ...]
    review_state: str
    missing: bool

    @property
    def key(self) -> str:
        """The rows used, joined: identifies one zone version."""
        return "+".join(self.rule_ids) if self.rule_ids else "none"


_STATE_RANK = {"verified_code": 0, "guidance": 1, "unread": 2}


def resolve(
    rules: Iterable[ZoneRule],
    jurisdiction: str,
    district: str,
    overlays: Iterable[str],
    on: date,
    *,
    map_name: str | None = None,
) -> Resolved:
    """The rule of a place (jurisdiction, district, overlays) on a date.

    The base row is the one for this district (exact code before ``*``; then the
    latest start). Overlay rows of the overlays given apply on top in ascending
    ``precedence`` and then overlay code, each replacing the fields it states
    (not ``inherit``). No base row gives every field ``unknown`` and
    ``missing=True`` (a district the table does not cover).
    """
    ours = [
        r
        for r in rules
        if r.jurisdiction == jurisdiction and counts_on(r, on) and _map_ok(r, map_name)
    ]
    base = [r for r in ours if not r.is_overlay and r.district in (district, "*")]
    if not base:
        return Resolved({k: UNKNOWN for k in RULE_FIELDS}, (), "unread", True)
    best = max(base, key=lambda r: (r.district != "*", r.counts_from, r.rule_id))
    merged = dict(best.fields)
    used = [best]
    present = set(overlays)
    over = [
        r for r in ours if r.is_overlay and r.overlay in present and r.district in (district, "*")
    ]
    for r in sorted(over, key=lambda r: (r.precedence, r.overlay, r.district != "*", r.rule_id)):
        changed = False
        for k, v in r.fields.items():
            if v != INHERIT:
                merged[k] = v
                changed = True
        if changed:
            used.append(r)
    state = max((r.review_state for r in used), key=lambda s: _STATE_RANK.get(s, 9))
    return Resolved(merged, tuple(r.rule_id for r in used), state, False)


def relevant_overlays(rules: Iterable[ZoneRule], jurisdiction: str) -> set[str]:
    """Overlay codes some row of the jurisdiction names (others cannot change a rule)."""
    return {r.overlay for r in rules if r.jurisdiction == jurisdiction and r.is_overlay}


def change_dates(rules: Iterable[ZoneRule], jurisdiction: str) -> list[date]:
    """Every date on which a row of the jurisdiction starts or stops counting."""
    days: set[date] = set()
    for r in rules:
        if r.jurisdiction != jurisdiction:
            continue
        days.add(r.counts_from)
        if r.valid_to is not None:
            days.add(r.valid_to)
    return sorted(d for d in days if d != date.min)


def window_intervals(
    rules: Iterable[ZoneRule], jurisdiction: str, start: date, end: date
) -> list[tuple[date, date, float]]:
    """The window cut at every rule change: ``(from, to, share of the window's days)``.

    Shares sum to 1. A rule that starts mid-window gives two intervals.
    """
    total = (end - start).days
    cuts = [d for d in change_dates(rules, jurisdiction) if start < d < end]
    edges = [start, *cuts, end]
    return [(a, b, (b - a).days / total) for a, b in itertools.pairwise(edges) if b > a]


# --------------------------------------------------------------------------- classes


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _adu(f: Mapping[str, str], bench: Mapping[str, Any]) -> tuple[str, str]:
    perm = f.get("adu_permission", UNKNOWN)
    excl = f.get("exclusivity_clause", UNKNOWN)
    if perm == "by_right":
        tests: dict[str, bool | None] = {}
        forms = f.get("adu_forms", UNKNOWN)
        tests["detached"] = (
            None
            if forms == UNKNOWN
            else (
                "detached" in [x.strip() for x in forms.split(",")]
                if bench.get("detached_allowed")
                else True
            )
        )
        size = _num(f.get("adu_max_sf", UNKNOWN))
        tests["size"] = (
            None
            if size is None and f.get("adu_max_sf") == UNKNOWN
            else (True if size is None else size >= float(bench.get("min_size_sf", 0)))
        )
        owner = f.get("adu_owner_occupancy", UNKNOWN)
        tests["owner"] = (
            None
            if owner == UNKNOWN
            else (owner in ("no", "none") or bool(bench.get("owner_occupancy")))
        )
        park = _num(f.get("parking_min_adu", UNKNOWN))
        tests["parking"] = None if park is None else park <= float(bench.get("extra_parking", 0))
        failed = [k for k, v in tests.items() if v is False]
        unread = [k for k, v in tests.items() if v is None]
        if failed:
            return "conditions_relaxed", "By right today; the benchmark drops: " + ", ".join(failed)
        if unread:
            return "conditions_relaxed", "By right today; conditions not read: " + ", ".join(unread)
        return "known_by_right", "By right today with every benchmark condition met"
    if perm in ("special_exception", "conditional"):
        return "discretionary", f"Allowed only after a hearing ({perm.replace('_', ' ')})"
    if perm == "prohibited":
        return "newly_eligible", "Prohibited today"
    if perm == "not_listed":
        if excl == "verified":
            return "newly_eligible", "Not listed, and a verified clause bars unlisted uses"
        if excl == "hearing_route":
            return "discretionary", "Not listed; unlisted uses may ask for a hearing"
        return "unresolved", "Not listed, and no clause on unlisted uses was read"
    return "unresolved", "ADU rule not read"


def _middle(f: Mapping[str, str], bench: Mapping[str, Any]) -> tuple[str, str]:
    units = _num(f.get("units_by_right", UNKNOWN))
    if units is None:
        return "unresolved", "Homes allowed by right on the lot not read"
    lo, hi = int(bench.get("units_min", 2)), int(bench.get("units_max", 6))
    if units >= hi:
        return "known_by_right", f"Already {int(units)} homes by right"
    if units >= lo:
        return "conditions_relaxed", f"{int(units)} homes by right; the benchmark adds up to {hi}"
    path = f.get("multi_unit_review_path", UNKNOWN)
    if path in ("special_exception", "conditional"):
        return "discretionary", "Two or more homes only after a hearing"
    return "newly_eligible", "One home by right; the benchmark allows 2 to 6"


def _parking(f: Mapping[str, str], bench: Mapping[str, Any]) -> tuple[str, str]:
    p = _num(f.get("parking_min_sf", UNKNOWN))
    if p is None:
        return "unresolved", "Parking minimum not read"
    if p <= float(bench.get("min_per_unit", 0)):
        return "known_by_right", "No parking minimum today"
    return "conditions_relaxed", f"{p:g} spaces per home today; the benchmark removes the minimum"


def classify(
    fields: Mapping[str, str], benchmark: str, benches: Mapping[str, Any] | None = None
) -> tuple[str, str]:
    """The opportunity class of a resolved rule for one benchmark, and why (plain words)."""
    benches = benches if benches is not None else load_settings()["benchmarks"]
    bench = benches.get(benchmark) or {}
    if benchmark == "adu":
        return _adu(fields, bench)
    if benchmark == "missing_middle":
        return _middle(fields, bench)
    if benchmark == "parking":
        return _parking(fields, bench)
    if benchmark == "tod":
        return "unresolved", "No rule read with height or floor area by distance to a station"
    raise ValueError(f"benchmark {benchmark!r} is not answered by zoning rules")


# --------------------------------------------------------------------------- table

ZONE_RULE_COLUMNS: dict[str, tuple[str, str]] = {
    "rule_id": ("code", "Rule row id (assumptions/law/zone_rules.yaml)"),
    "jurisdiction": ("code", "Municipality: 10-digit state + county + county subdivision code"),
    "district": ("code", "Zoning district code as on the map, or * for every district"),
    "overlay": ("code", "Overlay code the row applies inside, or none for the base rule"),
    "prototype": ("category", "Prototype lot: sf_lot (one existing single-family home)"),
    "maps": ("text", "District maps whose codes the row refers to (* any)"),
    "valid_from": ("date", "First day in force (empty when the source does not say)"),
    "valid_from_unknown": (
        "flag",
        "1 when the start is not stated: the row counts from reviewed_on",
    ),
    "valid_to": ("date", "First day no longer in force (empty when open)"),
    "review_state": (
        "category",
        "verified_code (enacted text read by script), guidance (the jurisdiction's own "
        "guidance, not the code) or unread",
    ),
    "doc_id": ("code", "Document read (law_documents)"),
    "section_cite": ("text", "Section cited, or why the text was not read"),
    "evidence_offset": ("count", "Character offset of the quote in the text read"),
    "text_sha256": ("text", "SHA-256 of the text read (white space collapsed)"),
    "timeline_record": ("code", "Matching record of the law timeline, when there is one"),
    "reviewed_on": ("date", "Date the row was read or re-read"),
    "use_permission": (
        "category",
        "Is the single-family home a permitted use: permitted or unknown",
    ),
    "review_path": ("category", "Approval path of the home: by_right, hearing type or unknown"),
    "units_by_right": ("text", "Homes allowed by right on the lot (number or unknown)"),
    "multi_unit_review_path": ("category", "Approval path for 2 or more homes, or unknown"),
    "density_far": ("text", "Density or floor-area limit, or unknown"),
    "height_max_ft": ("text", "Height limit in feet, or unknown"),
    "lot_min_sf": ("text", "Smallest lot in square feet, or unknown"),
    "adu_permission": (
        "category",
        "ADU: by_right, special_exception, conditional, prohibited, not_listed or unknown",
    ),
    "adu_forms": ("text", "ADU forms allowed: internal, detached, existing_accessory, or unknown"),
    "adu_max_sf": ("text", "Largest ADU in square feet, or unknown"),
    "adu_owner_occupancy": ("category", "Must the owner live on the lot: yes, no or unknown"),
    "adu_max_per_lot": ("text", "ADUs allowed per lot, or unknown"),
    "adu_min_lot_sf": ("text", "Smallest lot for an ADU in square feet, none, or unknown"),
    "parking_min_sf": ("text", "Parking spaces required per single-family home, or unknown"),
    "parking_min_adu": ("text", "Parking spaces required for an ADU, or unknown"),
    "parking_overlay_change": ("text", "How an overlay changes the base parking rule"),
    "utility_conditions": ("text", "Water or sewer conditions in the rule, none, or unknown"),
    "exclusivity_clause": (
        "category",
        "Clause on unlisted uses: verified (they are barred), hearing_route, unread or none_found",
    ),
    "exclusions": ("text", "Lots or cases the rule leaves out"),
}
"""Column units and descriptions of the ``zone_rule`` pack table."""


def zone_rule_table(rules: Iterable[ZoneRule], state_fips: str = "42") -> pd.DataFrame:
    """The ``zone_rule`` pack table: one row per reviewed row of the state (no quotes)."""
    rows = []
    for r in rules:
        if not r.jurisdiction.startswith(state_fips):
            continue
        rows.append(
            {
                "rule_id": r.rule_id,
                "jurisdiction": r.jurisdiction,
                "district": r.district,
                "overlay": r.overlay,
                "prototype": r.prototype,
                "maps": ",".join(r.maps),
                "valid_from": r.valid_from.isoformat() if r.valid_from else "",
                "valid_from_unknown": int(r.valid_from_unknown),
                "valid_to": r.valid_to.isoformat() if r.valid_to else "",
                "review_state": r.review_state,
                "doc_id": r.meta.get("doc_id", ""),
                "section_cite": r.meta.get("section_cite", ""),
                "evidence_offset": r.meta.get("evidence_offset", ""),
                "text_sha256": r.meta.get("text_sha256", ""),
                "timeline_record": r.meta.get("timeline_record", ""),
                "reviewed_on": r.reviewed_on.isoformat(),
                **dict(r.fields),
                "exclusions": r.meta.get("exclusions", ""),
            }
        )
    return pd.DataFrame(rows, columns=list(ZONE_RULE_COLUMNS))
