"""What a reform or bill changes, place by place, and which ordinance to read next.

Plain-language summary
----------------------
A state reform changes different things in different places. Where ADUs are
already allowed by right, an ADU bill adds nothing; where a hearing is needed,
it removes the hearing; where they are prohibited, it makes them legal; and
where nobody has read the local rule, we do not know. This module gives every
county (and every municipality with a law row) one of four answers for the
reform shown:

- **already allowed**: the rule in force already gives what the reform gives;
- **newly allowed**: the rule in force does not allow it, the reform does;
- **allowed, condition changed**: allowed today, but behind a hearing, a size,
  occupancy or parking condition, or a fee that the reform removes or cuts;
- **unresolved**: no verified rule in force answers the question, so the engine
  uses its statewide fallback range.

A place a bill does not reach (Philadelphia and Pittsburgh under a planning-code
bill, places with no zoning under a bill that amends zoning) is **outside** and
left out of the denominator. The four shares of the denominator add up to 100%.

Which rows count as read is the law layer's own test
(:func:`visionhousing.law.inforce.read_flags`: verified and in force on the
run's as-of date), and the ADU categories are the law layer's
(:func:`visionhousing.law.coverage.adu_category`). Nothing is re-decided here.
"Unknown stays unknown": a row that does not answer the question is unresolved,
never "no".

Each county carries: its answer; the shares of each class in its denominator
(single-family lots for ADUs and missing middle, residents for parking and
fees, homes near a major stop for transit density); the state provision and its
effective date; its municipal rows (section, dates, the district rule read);
whether the engine uses its readings or the statewide fallback (the 50% resident
bar); the model stage each class changes; the **legal additional capacity**
(a low-to-high range: known increments only, up to known plus every unresolved
lot at the reform's increment) and the **additional units authorized** (the
engine's own county range for the reform run alone). Statewide: each class's
share with its denominator, the share outside, and the share of the added homes
that sits in unresolved places: in each draw, the county added homes x the
county's unresolved share, summed over counties, over the state's added homes in
that draw; reported as P10 / P50 / P90 of those draws (percentiles are never
added or divided).

**Read next** (:func:`read_next`) takes the highest-ranked unread review units
from :func:`visionhousing.law.priorities.review_priorities` and runs the engine
twice for each: once with the unit read as permissive and once as restrictive
(the rows in ``assumptions/law_changes.yaml``), on the same seed and draws, for
the reform and for one other option (for example the fee cut). It reports the
change in statewide added homes (in each draw, the run with the reading minus
the run with today's readings, same seed and draws; P10 / P50 / P90 of that
difference), whether the preferred option (more added homes,
middle estimate) would switch, the county's reading coverage after the reading,
and the review effort from the priority score. Results are kept in the
comparisons disk cache, keyed by the pack, ledger, code and assumptions.

Interface addresses (registered by :func:`register`):

- ``GET /api/v1/states/{st}/law-changes?r=KEY``: the payload for one reform.
  Opt-in ``&view=districts`` (or ``districts_guidance``): in the zoning-district
  sample (Philadelphia, Pittsburgh, Frankstown) each municipality's single answer
  is replaced by its mix of district answers from the pack's ``rule_area_weights``
  (:func:`district_view`); the default view is unchanged.
- ``GET /api/v1/states/{st}/law-changes/read-next?r=KEY``: the bounding runs.

Static site: :func:`write_static` writes ``<ST>/law_changes/<KEY>.json`` (the
exporter calls it when the folder has room). Command:
``python -m visionhousing.api.law_changes --state PA [--estimate] [--read-next]``.

Settings: ``assumptions/law_changes.yaml``. Every number is worked out here on
the server, with its formula and source and assumption ids; the browser only
shows it.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from fastapi import Request, Response

from visionhousing.explain import show
from visionhousing.explain.numbers import TABLE_SIG

__all__ = [
    "CLASSES",
    "OUTSIDE",
    "SETTINGS_PATH",
    "build_payload",
    "classify_munis",
    "district_view",
    "load_settings",
    "read_next",
    "read_tables",
    "register",
    "size_estimate",
    "write_static",
]

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "assumptions" / "law_changes.yaml"
CLASSES = ("already_allowed", "changed_condition", "newly_allowed", "unresolved")
"""The four answers, from "nothing changes" to "we do not know"."""
OUTSIDE = "outside"
"""A place the bill does not reach (not one of the four answers; left out of the denominator)."""
PART = "law_read_next"
"""Name the read-next results are kept under in the comparisons disk cache."""
TABLE_NAMES = ("muni_zoning_rules", "municipal_planning", "state_law_provisions", "counties")
EXTRA_TABLES = {
    "typology_tract": {"geoid": str},
    "tract_municipalities": {"tract": str, "county": str, "cousub": str},
}
"""Tables the review-priority score also reads (typed as its command reads them)."""
LAW_SOURCE = "pa_municipal_ordinances"
DISTRICT_TABLE = "rule_area_weights"
"""Pack table of the zoning-district sample, read only by the opt-in district view."""
VIEWS = ("default", "districts", "districts_guidance")
"""``default``: the municipal answers (the shipped page). ``districts``: each sample
municipality's answer replaced by its zoning-district mix, verified code only;
``districts_guidance``: also the jurisdiction's published guidance (a sensitivity).
Settings in ``assumptions/law/zone_scope.yaml`` (``law_changes``)."""
BLANK = ("", "nan", "none", "unknown")


# ------------------------------------------------------------------ inputs


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """``assumptions/law_changes.yaml``."""
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def read_tables(folder: Path) -> dict[str, pd.DataFrame]:
    """The pack tables this module reads (each one optional), as text columns.

    Read as text, the way the engine's context reads the law tables, so a
    rebuilt :class:`~visionhousing.law.hooks.LocalLaw` matches the engine's.
    """
    out: dict[str, pd.DataFrame] = {}
    for name in TABLE_NAMES:
        for path in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
            if path.is_file():
                out[name] = pd.read_csv(path, dtype=str)
                break
    for name, dtypes in EXTRA_TABLES.items():
        for path in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
            if path.is_file():
                out[name] = pd.read_csv(path, dtype=dtypes)
                break
    for path in (folder / f"{DISTRICT_TABLE}.csv.gz", folder / f"{DISTRICT_TABLE}.csv"):
        if path.is_file():  # read only by the opt-in district view
            out[DISTRICT_TABLE] = pd.read_csv(path, dtype=str)
            break
    return out


def _text(v: Any) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none") else t


def _float(v: Any) -> float | None:
    t = _text(v)
    if t.lower() in BLANK:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _num(
    value: float | None,
    unit: str,
    formula: str,
    sources: list[str] | tuple[str, ...] = (),
    assumptions: list[str] | tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """One shown number: its value, text, formula and ids (``None`` stays ``None``)."""
    if value is None or not np.isfinite(value):
        return None
    return {
        "value": float(value),
        "unit": unit,
        "shown": show(float(value), unit, TABLE_SIG),
        "formula": formula,
        "source_ids": sorted(set(sources)),
        "assumption_ids": sorted(set(assumptions)),
    }


def _range(
    low: float, high: float, unit: str, formula: str, sources: Any = (), assumptions: Any = ()
) -> dict[str, Any]:
    return {
        "low": float(low),
        "high": float(high),
        "unit": unit,
        "shown": {
            "low": show(float(low), unit, TABLE_SIG),
            "high": show(float(high), unit, TABLE_SIG),
        },
        "text": (
            show(float(low), unit, TABLE_SIG)
            if np.isclose(low, high)
            else f"{show(float(low), unit, TABLE_SIG)} to {show(float(high), unit, TABLE_SIG)}"
        ),
        "formula": formula,
        "source_ids": sorted(set(sources)),
        "assumption_ids": sorted(set(assumptions)),
    }


def _draw_num(
    draws: np.ndarray,
    unit: str,
    formula: str,
    sources: list[str] | tuple[str, ...] = (),
    assumptions: list[str] | tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """A per-draw quantity as a shown number: ``value`` is the P50, plus P10 and P90.

    The percentiles are taken of the draws themselves (never of other
    percentiles). ``None`` when there are no finite draws.
    """
    arr = np.asarray(draws, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    p10, p50, p90 = (float(q) for q in np.percentile(arr, [10, 50, 90]))
    return {
        "value": p50,
        "unit": unit,
        "shown": show(p50, unit, TABLE_SIG),
        "formula": f"P10, P50, P90 of {arr.size:,} seeded draws of {formula}",
        "source_ids": sorted(set(sources)),
        "assumption_ids": sorted(set(assumptions)),
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "range": {
            k: show(v, unit, TABLE_SIG) for k, v in (("p10", p10), ("p50", p50), ("p90", p90))
        },
    }


def _state_draws(outcome: Any) -> np.ndarray | None:
    """Statewide added homes per draw of one reform outcome (``None`` when not carried)."""
    d = getattr(outcome, "state_draws", None)
    return None if d is None else np.asarray(d, dtype=float).ravel()


def _record(r: Any) -> dict[str, Any]:
    """An engine record as the screens read it: P10/P50/P90, text, formula, ids."""
    return {
        "name": r.name,
        "unit": r.unit,
        "p10": float(r.p10),
        "p50": float(r.p50),
        "p90": float(r.p90),
        "shown": {k: show(float(getattr(r, k)), r.unit, TABLE_SIG) for k in ("p10", "p50", "p90")},
        "formula": r.formula,
        "source_ids": list(r.source_ids),
        "assumption_ids": list(r.assumption_ids),
    }


def _param(ctx: Any, pid: str) -> Any:
    try:
        return ctx.ledger[pid]
    except (KeyError, TypeError, AttributeError):
        return None


def _fallback_text(ctx: Any, pids: list[str]) -> str:
    """The ledger fallback in words ("adu.already_legal_share: 5% to 25%, central 10%")."""
    parts = []
    for pid in pids:
        p = _param(ctx, pid)
        if p is None:
            continue
        lo, mid, hi = (
            show(float(getattr(p, k)), p.unit, TABLE_SIG) for k in ("low", "central", "high")
        )
        rng = mid if lo == hi else f"{lo} to {hi}, central {mid}"
        parts.append(f"{p.label} ({pid}): {rng}")
    return "; ".join(parts)


# ------------------------------------------------------------------ municipalities


def _munis(tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame | None:
    """Every municipality with its rule row, county, residents and lot weight."""
    rules = tables.get("muni_zoning_rules")
    munis = tables.get("municipal_planning")
    if rules is None or munis is None or rules.empty:
        return None
    m = munis.copy()
    m["geoid"] = m["county"].astype(str).str.zfill(5) + m["cousub"].astype(str).str.zfill(5)
    m["county"] = m["geoid"].str[:5]
    m["residents"] = pd.to_numeric(m["population"], errors="coerce").fillna(0.0)
    r = rules.copy()
    r["geoid"] = r["geoid"].astype(str)
    keep = [c for c in r.columns if c not in ("name", "zoning_regime", "dced_id")]
    out = m[["geoid", "county", "name", "zoning_regime", "residents"]].merge(
        r[keep], on="geoid", how="left"
    )
    out["sf_lots"] = pd.to_numeric(out.get("sf_lots"), errors="coerce").fillna(0.0)
    return out


def _scope_outside(
    m: pd.DataFrame, bill: Mapping[str, Any] | None, outside_mpc: set[str]
) -> pd.Series:
    """True for municipalities the bill does not reach (its ``scope`` column)."""
    if not bill:
        return pd.Series(False, index=m.index)
    scope = _text(bill.get("scope"))
    out = pd.Series(False, index=m.index)
    if scope in ("all_zoned", "mpc_zoned"):
        out |= m["zoning_regime"].astype(str).eq("none")
    if scope in ("mpc", "mpc_zoned"):
        out |= m["geoid"].isin(outside_mpc)
    return out


def _flag_word(v: Any) -> str:
    t = _text(v).lower()
    if t in ("1", "1.0", "true", "yes"):
        return "yes"
    if t in ("0", "0.0", "false", "no"):
        return "no"
    return "not read"


def _rule_words(row: Mapping[str, Any], rule: str) -> str:
    """The district rule read, in words, for the card."""
    if rule == "adu_category":
        status = _text(row.get("adu_status")).replace("_", " ") or "unknown"
        bits = [f"ADU: {status}"]
        if _text(row.get("adu_status")) == "not_addressed":
            clause = _text(row.get("unlisted_uses")).replace("_", " ") or "not read"
            bits.append(f"clause on unlisted uses: {clause}")
        bits.append(f"detached allowed: {_flag_word(row.get('adu_detached'))}")
        bits.append(f"owner must live there: {_flag_word(row.get('adu_owner_occupancy'))}")
        if _text(row.get("occupancy_restriction")):
            bits.append(f"occupancy: {_text(row.get('occupancy_restriction')).replace('_', ' ')}")
        return "; ".join(bits)
    if rule == "max_units":
        units = _text(row.get("max_units_by_right_sf_district")) or "not read"
        lot = _text(row.get("min_lot_sf_smallest_res_district"))
        words = f"homes allowed by right in the single-family district: {units}"
        return words + (f"; smallest residential lot: {lot} sq ft" if lot else "")
    if rule == "parking_min":
        sf = _text(row.get("parking_min_per_unit_sf")) or "not read"
        mf = _text(row.get("parking_min_per_unit_mf")) or "not read"
        return f"parking minimum per single-family home: {sf}; per apartment: {mf}"
    if rule == "impact_fee":
        fee = _text(row.get("transport_impact_fee_ordinance")) or "not read"
        return f"transportation impact fee ordinance: {fee}"
    return "No reading covers this question (height or floor area near transit)."


def _full_gain(rule: str, proposed: float | None) -> float:
    """Added legal units on a lot whose rule is unknown, at most: the reform's full gain.

    For missing middle a single-family lot already has one home, so the gain is the
    reform's top unit count less one; for ADUs it is the ADUs allowed per lot.
    """
    if not proposed:
        return 0.0
    return float(proposed) - 1.0 if rule == "max_units" else float(proposed)


def classify_munis(
    m: pd.DataFrame,
    spec: Mapping[str, Any],
    *,
    proposed: float | None,
    bill: Mapping[str, Any] | None = None,
    outside_mpc: set[str] | None = None,
    hooks: dict[str, Any] | None = None,
    as_of: date | str | None = None,
) -> pd.DataFrame:
    """Each municipality's answer for one reform, with its capacity increment per weight.

    Adds ``read`` (verified and in force), ``answer`` (one of :data:`CLASSES` or
    ``outside``), ``why`` (plain words), ``cap_low`` / ``cap_high`` (added legal
    units per lot: equal for a known answer; 0 to the reform's increment for an
    unresolved one) and ``rule_text``. ``proposed`` is the reform's per-lot
    unit count (``adu.units_per_lot``, or ``missing_middle.max_units``); ``None``
    when the reform adds no legal units (parking, fees, transit density).
    """
    from visionhousing.law.coverage import adu_category
    from visionhousing.law.inforce import read_flags, resolve_as_of
    from visionhousing.law.rights import legal_increment

    when = resolve_as_of(as_of)
    out = m.copy().reset_index(drop=True)
    read, adu_read = read_flags(out, when, hooks)
    out["read"] = read.to_numpy()
    rule = str(spec["rule"])
    answers, whys, lo, hi = [], [], [], []
    outside = _scope_outside(out, bill, outside_mpc or set())
    for i, row in out.iterrows():
        rec = row.to_dict()
        is_read = bool(out.at[i, "read"])
        if bool(outside.iloc[i]):
            answers.append(OUTSIDE)
            regime = _text(rec.get("zoning_regime"))
            whys.append(
                "No zoning ordinance on record for the bill to change."
                if regime == "none"
                else "Outside the Municipalities Planning Code, which this bill amends."
            )
            lo.append(0.0)
            hi.append(0.0)
            continue
        status = _text(rec.get("status"))
        if not is_read:
            if status == "verified":
                why = f"Verified, but not in force on {when.isoformat()} (dates unknown or later)."
            elif _text(rec.get("method")) in ("", "unread"):
                why = "Not read."
            else:
                why = "Read, but the reading is a draft (not verified)."
        else:
            why = ""
        cls, c_lo, c_hi = "unresolved", 0.0, _full_gain(rule, proposed)
        if rule == "adu_category":
            if bool(adu_read.iloc[i]):
                cat = adu_category(rec, hooks, as_of=when)
                cls = {
                    "already_permitted": "already_allowed",
                    "by_right_conditions": "changed_condition",
                    "discretionary": "changed_condition",
                    "prohibited": "newly_allowed",
                }.get(cat, "unresolved")
                gain = float(proposed or 0.0) if cat in ("discretionary", "prohibited") else 0.0
                c_lo = c_hi = gain
                why = {
                    "already_permitted": "By right, detached allowed, no owner or occupancy rule.",
                    "by_right_conditions": (
                        "By right, but with a condition the reform drops (attached only, owner "
                        "rule, relatives only, or conditions not read)."
                    ),
                    "discretionary": (
                        "Allowed only after a hearing (conditional use or special exception); the "
                        "reform makes it by right."
                    ),
                    "prohibited": (
                        "Prohibited, or not listed where a verified clause bars unlisted uses."
                    ),
                }.get(cat, why)
            elif is_read:
                why = (
                    "Read, but the reading does not answer the ADU question (not addressed, with "
                    "no clause on unlisted uses read)."
                )
        elif rule == "max_units" and is_read:
            cur = _float(rec.get("max_units_by_right_sf_district"))
            if cur is not None and proposed:
                inc = legal_increment(int(cur), int(proposed))
                if not inc:
                    cls, c_lo, c_hi = "already_allowed", 0.0, 0.0
                    why = (
                        f"Already allows {int(cur)} homes by right, "
                        f"at least the reform's {int(proposed)}."
                    )
                elif int(cur) <= 1:
                    cls = "newly_allowed"
                    c_lo = c_hi = float(proposed) - float(cur)
                    why = (
                        f"Single-family only; the reform newly allows {inc[0]} to {inc[-1]} homes."
                    )
                else:
                    cls = "changed_condition"
                    c_lo = c_hi = float(proposed) - float(cur)
                    why = (
                        f"Already allows {int(cur)} homes by right; "
                        f"only {inc[0]} to {inc[-1]} are new."
                    )
            else:
                why = (
                    "Read, but the by-right unit count in the single-family district was not read."
                )
        elif rule == "parking_min" and is_read:
            sf, mf = (
                _float(rec.get("parking_min_per_unit_sf")),
                _float(rec.get("parking_min_per_unit_mf")),
            )
            known = [v for v in (sf, mf) if v is not None]
            if known and all(v == 0 for v in known):
                cls, why = "already_allowed", "No parking minimum read (zero)."
            elif any(v > 0 for v in known):
                cls, why = (
                    "changed_condition",
                    "A parking minimum above zero is read; the reform removes it.",
                )
            else:
                why = "Read, but the parking minimums were not read."
            c_lo = c_hi = 0.0
        elif rule == "impact_fee" and is_read:
            fee = _text(rec.get("transport_impact_fee_ordinance")).lower()
            if fee in ("1", "1.0", "true", "yes", "0", "0.0", "false", "no"):
                cls, why = "changed_condition", "Fees read here; the reform cuts them."
            else:
                why = "Read, but no reading answers the fee question yet."
            c_lo = c_hi = 0.0
        elif rule == "not_read" and is_read:
            why = "Read, but no reading covers height or floor area near transit."
        if cls == "unresolved" and proposed is None:
            c_lo = c_hi = 0.0
        answers.append(cls)
        whys.append(why)
        lo.append(c_lo)
        hi.append(c_hi)
    out["answer"] = answers
    out["why"] = whys
    out["cap_low"] = lo
    out["cap_high"] = hi
    out["rule_text"] = [_rule_words(r, rule) for r in out.to_dict("records")]
    return out


# ------------------------------------------------------------------ payload


def _answer(shares: Mapping[str, float], order: list[str]) -> str:
    """The county's map answer: the class with the largest share (ties in ``order``)."""
    if not shares or sum(shares.values()) <= 0:
        return OUTSIDE
    best = max(shares.values())
    for c in order:
        if np.isclose(shares.get(c, 0.0), best):
            return c
    return "unresolved"


def _bill_row(tables: Mapping[str, pd.DataFrame], bill: str | None) -> dict[str, Any] | None:
    prov = tables.get("state_law_provisions")
    if not bill or prov is None:
        return None
    hit = prov[prov["provision_id"].astype(str) == bill]
    return None if hit.empty else {k: _text(v) for k, v in hit.iloc[0].items()}


def _provision_block(
    ctx: Any, key: str, spec: Mapping[str, Any], tables: Mapping[str, pd.DataFrame]
) -> dict[str, Any]:
    """The state law cited: the bill (with its status and effective date) or the benchmark."""
    prov = tables.get("state_law_provisions")
    current = []
    if prov is not None:
        for pid in spec.get("state_provisions") or []:
            hit = prov[prov["provision_id"].astype(str) == pid]
            if not hit.empty:
                r = hit.iloc[0]
                current.append(
                    {
                        "provision_id": pid,
                        "citation": _text(r.get("citation")),
                        "status": _text(r.get("status")),
                        "effective_date": _text(r.get("effective_date")) or None,
                        "summary": _text(r.get("summary_own_words")),
                        "url": _text(r.get("url")),
                        "source_id": _text(r.get("source_id")),
                        "checked_on": _text(r.get("checked_on")),
                    }
                )
    bill = _bill_row(tables, spec.get("bill"))
    scen = getattr(ctx, "scenarios", {}).get(key) if spec.get("bill") else None
    if bill:
        eff = bill.get("effective_date") or None
        status = bill.get("status", "")
        when = (
            f"Effective {eff}."
            if eff
            else (
                f"No effective date: {status or 'not enacted'} (last action "
                f"{bill.get('last_action_date') or 'unknown'}: "
                f"{bill.get('last_action') or 'not recorded'})."
            )
        )
        return {
            "kind": "bill",
            "provision_id": spec.get("bill"),
            "citation": bill.get("citation"),
            "sections": list(getattr(scen, "notes", []) or []),
            "status": status,
            "effective_date": eff,
            "effective_text": when,
            "scope": bill.get("scope"),
            "url": bill.get("url"),
            "source_id": bill.get("source_id"),
            "checked_on": bill.get("checked_on"),
            "current_law": current,
        }
    return {
        "kind": "benchmark",
        "provision_id": None,
        "citation": "Benchmark reform as the Challenge defines it",
        "sections": [],
        "status": "hypothetical",
        "effective_date": None,
        "effective_text": (
            "A benchmark is not a bill: it has no section or effective date. The model applies it "
            "from the start of the five-year window."
        ),
        "scope": "all",
        "url": None,
        "source_id": None,
        "checked_on": None,
        "current_law": current,
    }


def county_answer_coverage(
    rules: pd.DataFrame | None,
    munis: pd.DataFrame | None,
    rule: str,
    hooks: dict[str, Any] | None = None,
    as_of: date | str | None = None,
) -> dict[str, float]:
    """Per county, the residents' share whose rule the engine's hook counts as read.

    The ADU hook counts only rows that answer the ADU question
    (:func:`visionhousing.law.hooks.county_adu_legal_share`, ``coverage``); the
    parking hook counts any verified rule in force
    (:func:`visionhousing.law.coverage.county_coverage`). A county qualifies
    for local values at ``min_coverage`` of residents (``hooks.yaml``).
    """
    if rules is None or munis is None or rule not in ("adu_category", "parking_min"):
        return {}
    from visionhousing.law.coverage import county_coverage
    from visionhousing.law.hooks import county_adu_legal_share

    if rule == "adu_category":
        t = county_adu_legal_share(rules, munis, (0.0, 0.0, 0.0), hooks, as_of=as_of)
        return {str(c): float(v) for c, v in zip(t["county"], t["coverage"], strict=True)}
    t = county_coverage(rules, munis, hooks, as_of=as_of)
    return {str(c): float(v) for c, v in zip(t["county"], t["coverage_residents"], strict=True)}


def _coverage_words(rule: str) -> str:
    return (
        "residents under a verified reading in force that answers the ADU question"
        if rule == "adu_category"
        else "residents under a verified reading in force"
    )


def _engine_use(
    ctx: Any, spec: Mapping[str, Any], county: str, coverage: Mapping[str, float]
) -> dict[str, Any]:
    """Whether the engine uses this county's readings for the reform, in words."""
    law = getattr(ctx, "local_law", None)
    rule = spec["rule"]
    if law is None or rule not in ("adu_category", "parking_min"):
        return {
            "uses_readings": False,
            "coverage_residents": None,
            "text": (
                "The engine uses the statewide range here: "
                "it takes no local reading for this reform."
            ),
        }
    share = float(coverage.get(county, 0.0))
    used = county in (law.adu_coefficients() if rule == "adu_category" else law.parking_factors())
    bar = show(float(law.min_coverage), "share", TABLE_SIG)
    pct = show(share, "share", TABLE_SIG)
    return {
        "uses_readings": bool(used),
        "coverage_residents": _num(
            share,
            "share",
            f"{_coverage_words(rule)} / county residents",
            [LAW_SOURCE, "census_subcounty_pop"],
        ),
        "text": (
            f"The engine uses this county's readings: they cover {pct} of its residents (the "
            f"bar is {bar})."
            if used
            else (
                f"The engine keeps the statewide range here: the readings it can use cover {pct} "
                f"of residents, below the {bar} bar."
            )
        ),
    }


def _county_frame(ctx: Any, tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """County ids and names, with tract-level weights (lots, homes, homes near a stop)."""
    t = ctx.tracts.copy()
    t["county"] = t["county"].astype(str)
    zero = pd.Series(0.0, index=t.index)
    agg = {
        "sf_lots": pd.to_numeric(t.get("units_1_detached", zero), errors="coerce"),
        "housing_units": pd.to_numeric(t.get("housing_units", zero), errors="coerce"),
        "residents": pd.to_numeric(t.get("population", zero), errors="coerce"),
    }
    if "transit_units_d07" in t.columns:
        agg["station_homes"] = pd.to_numeric(t["transit_units_d07"], errors="coerce")
    frame = pd.DataFrame({k: v.fillna(0.0) for k, v in agg.items()})
    frame["county"] = t["county"].to_numpy()
    out = frame.groupby("county").sum().reset_index()
    names: dict[str, str] = {}
    counties = tables.get("counties")
    if counties is not None and "name" in counties.columns:
        names = dict(zip(counties["geoid"].astype(str), counties["name"].astype(str), strict=False))
    out["name"] = [names.get(str(c), str(c)) for c in out["county"]]
    return out


def build_payload(
    ctx: Any,
    key: str,
    tables: Mapping[str, pd.DataFrame],
    result: Any = None,
    *,
    settings: dict[str, Any] | None = None,
    hooks: dict[str, Any] | None = None,
    as_of: date | str | None = None,
    view: str = "default",
) -> dict[str, Any]:
    """The "what this law changes" payload of one reform for a loaded state.

    Args:
        ctx: The state context (tracts, ledger, local law, scenarios).
        key: Reform or bill key (a key of ``reforms`` in the settings).
        tables: :func:`read_tables` of the state's pack (any may be missing:
            then every county is unresolved, counted from the tract table).
        result: The engine run of the reform alone (for the units authorized);
            ``None`` leaves those values empty.
        settings: ``assumptions/law_changes.yaml`` (read when not given).
        hooks: ``assumptions/law/hooks.yaml`` (the law layer's default when not given).
        as_of: The run's as-of date (default: ``assumptions/outcome.yaml``).
        view: One of :data:`VIEWS`; anything but ``default`` adds the district
            view (:func:`district_view`) and a ``view`` block.

    Raises:
        KeyError: ``key`` is not in the settings.
        ValueError: ``view`` is not one of :data:`VIEWS`.
    """
    from visionhousing.law.inforce import resolve_as_of

    if view not in VIEWS:
        raise ValueError(f"law-changes view must be one of {VIEWS}, got {view!r}")

    settings = settings or load_settings()
    spec = settings["reforms"][key]
    when = resolve_as_of(as_of)
    order = list(settings.get("answer_tie_order") or CLASSES)
    den_id = str(spec["denominator"])
    den = dict(settings["denominators"][den_id])
    rule = str(spec["rule"])
    cap_pid = spec.get("capacity_param")
    cap_param = _param(ctx, cap_pid) if cap_pid else None
    proposed = float(cap_param.central) if cap_param is not None else None
    fallback_ids = list(spec.get("fallback_params") or [])
    cond_ids = list(spec.get("condition_params") or [])
    law_ids = fallback_ids + cond_ids + ([cap_pid] if cap_pid else [])
    fallback_words = _fallback_text(ctx, fallback_ids)
    bill = _bill_row(tables, spec.get("bill"))
    outside_mpc: set[str] = set()
    if bill:
        try:
            from visionhousing.law.state import load_provisions

            prov = load_provisions()
            if str(prov.get("state", "")).upper() == str(ctx.state).upper():
                outside_mpc = {str(g) for g in prov.get("outside_mpc", [])}
        except (OSError, ValueError, KeyError):
            outside_mpc = set()
    counties = _county_frame(ctx, tables)
    munis = _munis(tables)
    # Transit density counts homes near a stop per county; its municipal rows are
    # listed with their residents.
    weight_col = "sf_lots" if den_id == "sf_lots" else "residents"
    muni_den = den if den_id != "station_homes" else dict(settings["denominators"]["residents"])
    den_sources = list(den.get("source_ids") or [])
    if den_id == "station_homes" and "station_homes" not in counties.columns:
        den["label"] = "homes (this pack has no transit catchment)"
        den["meaning"] = (
            "Housing units; the pack has no transit catchment column, so every home counts."
        )
        counties["station_homes"] = counties["housing_units"]

    classified = None
    if munis is not None and weight_col is not None:
        classified = classify_munis(
            munis,
            spec,
            proposed=proposed,
            bill=bill,
            outside_mpc=outside_mpc,
            as_of=when,
            hooks=hooks,
        )
        classified["weight"] = pd.to_numeric(classified[weight_col], errors="coerce").fillna(0.0)
    split, view_block = classified, None
    if view != "default":
        split, view_block = district_view(
            classified, tables, rule=rule, proposed=proposed, view=view, weight_col=weight_col,
            den=den,
        )  # fmt: skip
    coverage = county_answer_coverage(
        tables.get("muni_zoning_rules"), tables.get("municipal_planning"), rule, hooks, when
    )
    stage = dict(spec.get("stages") or {})
    units_by_county: dict[str, Any] = {}
    draws_by_county: dict[str, np.ndarray] = {}
    state_units = None
    if result is not None and key in getattr(result, "reforms", {}):
        outcome = result.reforms[key]
        units_by_county = {str(c): r for c, r in outcome.counties.items()}
        state_units = _record(outcome.added)
        cd = getattr(outcome, "county_draws", None)
        if cd is not None:
            cd = np.asarray(cd, dtype=float)
            draws_by_county = {str(c): cd[i] for i, c in enumerate(outcome.counties)}

    rows = []
    totals = dict.fromkeys((*CLASSES, OUTSIDE), 0.0)
    cap_lo_total = cap_hi_total = 0.0
    unres_draws: np.ndarray | None = None
    all_draws: np.ndarray | None = None
    for c in counties.itertuples(index=False):
        county = str(c.county)
        parts = dict.fromkeys((*CLASSES, OUTSIDE), 0.0)
        mrows: list[dict[str, Any]] = []
        cap_lo = cap_hi = 0.0
        g = classified[classified["county"] == county] if classified is not None else None
        if g is not None and len(g) and den_id != "station_homes":
            gs = split[split["county"] == county]  # the classified rows unless a district view
            for cls in (*CLASSES, OUTSIDE):
                parts[cls] = float(gs.loc[gs["answer"] == cls, "weight"].sum())
            cap_lo = float((gs["weight"] * gs["cap_low"]).sum())
            cap_hi = float((gs["weight"] * gs["cap_high"]).sum())
            for r in g[g["method"].fillna("unread").astype(str) != "unread"].to_dict("records"):
                mrows.append(_muni_row(r, rule, stage, muni_den, when))
        else:
            base = float(getattr(c, den_id if den_id in counties.columns else "sf_lots"))
            if den_id == "station_homes":
                parts["unresolved"] = base
                parts[OUTSIDE] = max(0.0, float(c.housing_units) - base)
                if g is not None:
                    for r in g[g["method"].fillna("unread").astype(str) != "unread"].to_dict(
                        "records"
                    ):
                        mrows.append(_muni_row(r, rule, stage, muni_den, when))
            else:
                parts["unresolved"] = base
                cap_hi = base * _full_gain(rule, proposed)
        inside = sum(parts[k] for k in CLASSES)
        shares = {k: (parts[k] / inside if inside > 0 else 0.0) for k in CLASSES}
        answer = _answer(shares, order) if inside > 0 else OUTSIDE
        for k in parts:
            totals[k] += parts[k]
        cap_lo_total += cap_lo
        cap_hi_total += cap_hi
        unit_rec = units_by_county.get(county)
        cdraws = draws_by_county.get(county)
        if cdraws is not None:
            all_draws = cdraws.copy() if all_draws is None else all_draws + cdraws
            part = cdraws * shares["unresolved"]
            unres_draws = part if unres_draws is None else unres_draws + part
        share_sources = [*den_sources, LAW_SOURCE] if classified is not None else den_sources
        rows.append(
            {
                "fips": county,
                "name": str(c.name),
                "answer": answer,
                "denominator": _num(
                    inside,
                    den["unit"],
                    f"{den['label']} inside the reform's reach, "
                    "summed over the county's municipalities",
                    share_sources,
                ),
                "outside": _num(
                    parts[OUTSIDE],
                    den["unit"],
                    f"{den['label']} the reform does not reach",
                    share_sources,
                ),
                "classes": {
                    k: {
                        "count": _num(
                            parts[k],
                            den["unit"],
                            f"{den['label']} whose answer is '{k}'",
                            share_sources,
                        ),
                        "share": _num(
                            shares[k] if inside > 0 else None,
                            "share",
                            f"{den['label']} answered '{k}' / {den['label']} inside the "
                            "reform's reach",
                            share_sources,
                        ),
                    }
                    for k in CLASSES
                },
                "observed": _observed(shares, inside, fallback_words, fallback_ids),
                "engine": _engine_use(ctx, spec, county, coverage),
                "stage": {k: stage.get(k, "") for k in (*CLASSES,)},
                "capacity": None
                if proposed is None
                else _range(
                    cap_lo,
                    cap_hi,
                    "units",
                    "low: lots with a known gain x added units per lot; high: plus every "
                    "unresolved lot "
                    f"at the reform's full gain ({cap_pid} = "
                    f"{show(float(proposed), cap_param.unit, TABLE_SIG)}"
                    f"{', less the one home already there' if rule == 'max_units' else ''})",
                    share_sources,
                    [cap_pid],
                ),
                "units": None if unit_rec is None else _record(unit_rec),
                "municipalities": mrows,
            }
        )

    inside_total = sum(totals[k] for k in CLASSES)
    state_sources = [*den_sources, LAW_SOURCE] if classified is not None else den_sources
    state_classes = {
        k: {
            "count": _num(
                totals[k],
                den["unit"],
                f"{den['label']} answered '{k}', summed over counties",
                state_sources,
            ),
            "share": _num(
                totals[k] / inside_total if inside_total > 0 else None,
                "share",
                f"{den['label']} answered '{k}' / {den['label']} "
                "inside the reform's reach, statewide",
                state_sources,
            ),
        }
        for k in CLASSES
    }
    unresolved_result = None
    if all_draws is not None and unres_draws is not None:
        ok = all_draws > 0
        if ok.any():
            unresolved_result = _draw_num(
                unres_draws[ok] / all_draws[ok],
                "share",
                "(the sum over counties of county added homes x the county's unresolved share "
                f"of {den['label']}) / statewide added homes, in each draw with added homes",
                [*state_sources, *(state_units or {}).get("source_ids", [])],
                (state_units or {}).get("assumption_ids", []),
            )
    words = {c["id"]: c for c in settings["classes"]}
    scen = getattr(ctx, "scenarios", {}).get(key)
    out = {
        "state": ctx.state,
        "reform": key,
        "title": getattr(scen, "title", key) if scen is not None else key,
        "as_of": when.isoformat(),
        "rule": rule,
        "classes": [
            {"id": k, "word": words[k]["word"], "meaning": words[k]["meaning"]} for k in CLASSES
        ],
        "outside_class": dict(settings["outside"]),
        "denominator": {"id": den_id, **den},
        "provision": _provision_block(ctx, key, spec, tables),
        "fallback": {"text": fallback_words, "assumption_ids": fallback_ids},
        "statewide": {
            "denominator": _num(
                inside_total,
                den["unit"],
                f"{den['label']} inside the reform's reach, statewide",
                state_sources,
            ),
            "outside": _num(
                totals[OUTSIDE],
                den["unit"],
                f"{den['label']} the reform does not reach, statewide",
                state_sources,
            ),
            "classes": state_classes,
            "unresolved_share": state_classes["unresolved"]["share"],
            "unresolved_result_share": unresolved_result,
            "capacity": None
            if proposed is None
            else _range(
                cap_lo_total,
                cap_hi_total,
                "units",
                "sum of the counties' legal additional capacity (low and high ends)",
                state_sources,
                [cap_pid],
            ),
            "capacity_note": None
            if proposed is not None
            else (
                "This reform relaxes a condition or a cost; it adds no legal homes per lot, so it "
                "has no legal capacity figure."
            ),
            "units": state_units,
        },
        "readings": {
            "municipal_rows": int(sum(len(r["municipalities"]) for r in rows)),
            "source_id": LAW_SOURCE if classified is not None else None,
            "note": None
            if classified is not None
            else "This state's pack has no municipal law table: every county is unresolved.",
        },
        "law_assumption_ids": law_ids,
        "counties": rows,
    }
    if view_block is not None:
        out["view"] = view_block
    return out


def district_view(
    classified: pd.DataFrame | None,
    tables: Mapping[str, pd.DataFrame],
    *,
    rule: str,
    proposed: float | None,
    view: str,
    weight_col: str,
    den: Mapping[str, Any],
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Replace each sample municipality's answer by its zoning-district mix.

    For a municipality of the pack's ``rule_area_weights`` sample, its row
    (weight ``w``) becomes one row per district class ``k`` with weight
    ``w x share_k``, ``share_k`` its lots (homes, for a residents
    denominator) in class ``k`` over all its lots, from the window's zone rows
    (:func:`visionhousing.engine.zone_scope.jurisdiction_mix`). A class that
    rests on a row the view does not count is unresolved. Capacity per lot:
    known by right and conditions relaxed 0; newly eligible and discretionary
    the reform's full gain; unresolved 0 to the full gain. Places outside the
    bill stay outside; every other municipality keeps its own answer.

    Returns the rows the shares and capacity are counted from, and the
    payload's ``view`` block (which municipalities moved, from which answer, to
    which mix).
    """
    from visionhousing.engine import zone_scope as zs

    settings = zs.load_settings()
    lc = settings["law_changes"]
    review = list(settings["views"][lc["views"][view]]["review_states"])
    words = str(settings["views"][lc["views"][view]]["words"])
    bench = (lc.get("benchmarks") or {}).get(rule)
    raw = tables.get(DISTRICT_TABLE)
    block: dict[str, Any] = {
        "id": view,
        "words": f"Zoning-district view ({words})",
        "table": DISTRICT_TABLE,
        "benchmark": bench,
        "municipalities": [],
        "note": None,
    }
    if classified is None or bench is None or raw is None or raw.empty:
        block["note"] = (
            "No district answers for this reform or pack: every municipality keeps its own answer."
        )
        return classified, block
    table = zs.typed_table(raw)
    mix = zs.jurisdiction_mix(table, str(bench), review, weight=str(lc["weights"][weight_col]))
    full = _full_gain(rule, proposed)
    gain = {
        "known_by_right": (0.0, 0.0),
        "conditions_relaxed": (0.0, 0.0),
        "newly_eligible": (full, full),
        "discretionary": (full, full),
        "unresolved": (0.0, full),
    }
    sources = [*list(den.get("source_ids") or []), LAW_SOURCE, *settings.get("source_ids", [])]
    sample = set(mix["jurisdiction"].astype(str))
    keep, add = [], []
    for row in classified.to_dict("records"):
        gid = str(row["geoid"])
        if gid not in sample or row["answer"] == OUTSIDE:
            keep.append(row)
            continue
        m = mix[mix["jurisdiction"] == gid]
        own = float(row.get("weight") or 0.0)
        parts = []
        for r in m.itertuples(index=False):
            lo, hi = gain[str(r.opportunity_class)]
            add.append(
                {
                    **row,
                    "answer": r.answer,
                    "weight": own * float(r.share),
                    "cap_low": lo,
                    "cap_high": hi,
                    "why": f"District view: {r.why}",
                }
            )
            parts.append(
                {
                    "class": str(r.opportunity_class),
                    "answer": str(r.answer),
                    "share": _num(
                        float(r.share),
                        "share",
                        f"{lc['weights'][weight_col]} of the municipality's zone rows in class "
                        f"'{r.opportunity_class}' / all its zone rows ({DISTRICT_TABLE}, window "
                        "2027-2031)",
                        sources,
                    ),
                    "why": str(r.why),
                }
            )
        block["municipalities"].append(
            {
                "geoid": gid,
                "name": _text(row.get("name")),
                "municipal_answer": row["answer"],
                "denominator": _num(
                    own, den["unit"], f"{den['label']} of this municipality", sources
                ),
                "districts": parts,
            }
        )
    out = pd.DataFrame([*keep, *add], columns=classified.columns)
    return out, block


def _observed(
    shares: Mapping[str, float], inside: float, fallback: str, ids: list[str]
) -> dict[str, Any]:
    """Observed (read) share versus fallback, in words."""
    unres = shares.get("unresolved", 0.0)
    read = 1.0 - unres if inside > 0 else 0.0
    kind = "observed" if unres <= 0 and inside > 0 else ("fallback" if read <= 0 else "mixed")
    pct = show(read, "share", TABLE_SIG)
    text = {
        "observed": (
            f"Observed: every part of the denominator has a verified reading in force ({pct})."
        ),
        "fallback": (
            "Fallback: no verified reading in force; the engine uses "
            f"{fallback or 'its statewide range'}."
        ),
        "mixed": (
            f"Observed for {pct} of the denominator; the rest uses the fallback: "
            f"{fallback or 'the statewide range'}."
        ),
    }[kind]
    return {
        "kind": kind,
        "read_share": _num(read, "share", "1 - the county's unresolved share", [LAW_SOURCE], []),
        "text": text,
        "assumption_ids": ids if kind != "observed" else [],
    }


def _muni_row(
    r: Mapping[str, Any], rule: str, stage: Mapping[str, str], den: Mapping[str, Any], when: date
) -> dict[str, Any]:
    """One municipality with a law row: its answer, the rule read, its section and dates."""
    quote = _text(r.get("evidence_quote"))
    return {
        "geoid": str(r["geoid"]),
        "name": _text(r.get("name")),
        "answer": r["answer"],
        "why": r["why"],
        "rule_text": r["rule_text"],
        "section": _text(r.get("section_cite")) or None,
        "document": _text(r.get("doc_id")) or None,
        "status": _text(r.get("status")),
        "method": _text(r.get("method")),
        "in_force": bool(r.get("read")),
        "dates": {
            "effective_date": _text(r.get("effective_date")) or None,
            "adopted_on": _text(r.get("adopted_on")) or None,
            "in_force_by": _text(r.get("in_force_by")) or None,
            "reviewed_on": _text(r.get("reviewed_on")) or None,
            "as_of": when.isoformat(),
        },
        "denominator": _num(
            float(r.get("weight") or 0.0),
            den["unit"],
            f"{den['label']} of this municipality",
            list(den.get("source_ids") or []),
        ),
        "stage": stage.get(r["answer"], ""),
        "quote": quote[:300] or None,
    }


# ------------------------------------------------------------------ read next


def _treat(
    rules: pd.DataFrame, geoids: set[str], fields: Mapping[str, Any], effective: str
) -> pd.DataFrame:
    """A copy of the rules with the unit's rows set to one bounding reading."""
    out = rules.copy()
    hit = out["geoid"].astype(str).isin(geoids)
    for col, val in {**fields, "status": "verified", "method": "hand", "effective_date": effective,
                     "adopted_on": "", "in_force_by": "", "unlisted_uses": ""}.items():  # fmt: skip
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].astype(object)
        out.loc[hit, col] = val
    return out


def _unit_geoids(unit: str, munis: pd.DataFrame, read_geoids: set[str]) -> set[str]:
    m = munis.copy()
    m["geoid"] = m["county"].astype(str).str.zfill(5) + m["cousub"].astype(str).str.zfill(5)
    if unit.startswith("county:"):
        c = unit.split(":", 1)[1]
        hit = m[(m["geoid"].str[:5] == c) & (m["zoning_regime"].astype(str) == "county")]
        return set(hit["geoid"]) - read_geoids
    return {unit}


def _run_dict(result: Any, key: str) -> dict[str, Any] | None:
    """The shape :func:`visionhousing.law.priorities.adu_yield_ratio` reads, from a run."""
    if result is None or key not in getattr(result, "reforms", {}):
        return None
    o = result.reforms[key]
    return {
        "reform_results": {
            "adu": {
                "added": {"p50": float(o.added.p50)},
                "counties": {str(c): {"p50": float(r.p50)} for c, r in o.counties.items()},
            }
        }
    }


def read_next(
    ctx: Any,
    key: str,
    tables: Mapping[str, pd.DataFrame],
    run_fn: Callable[[Any, str], Any],
    *,
    settings: dict[str, Any] | None = None,
    hooks: dict[str, Any] | None = None,
    as_of: date | str | None = None,
    top: int | None = None,
) -> dict[str, Any]:
    """Bounding runs for the top unread review units (see the module summary).

    Args:
        ctx: The state context.
        key: The reform shown.
        tables: :func:`read_tables` of the pack.
        run_fn: ``run_fn(context, reform_key)`` returns the engine result of that
            reform alone on that context (same seed and draws every call).
        settings: ``assumptions/law_changes.yaml`` (read when not given).
        hooks: ``assumptions/law/hooks.yaml`` (read when not given).
        as_of: The run's as-of date (default: ``assumptions/outcome.yaml``).
        top: How many units (default ``read_next.top``).
    """
    from visionhousing.law.hooks import load_hooks, local_law_for_engine
    from visionhousing.law.inforce import read_flags, resolve_as_of
    from visionhousing.law.priorities import review_priorities

    settings = settings or load_settings()
    spec = settings["reforms"][key]
    rn = settings["read_next"]
    when = resolve_as_of(as_of)
    hooks = hooks or load_hooks()
    rule = str(spec["rule"])
    other = (rn.get("other_option") or {}).get(key)
    options = [key] + ([other] if other else [])
    bounded = rule in (rn.get("treatments") or {})
    rules, munis = tables.get("muni_zoning_rules"), tables.get("municipal_planning")
    head = {
        "state": ctx.state,
        "reform": key,
        "options": options,
        "decision_unit": "statewide added homes, middle estimate (P50 of the draws)",
        "seed": int(rn.get("seed", 0)),
        "draws": int(rn.get("draws", 2000)),
        "as_of": when.isoformat(),
        "bounded": bounded,
        "units": [],
    }
    if rules is None or munis is None or rules.empty:
        return {
            **head,
            "note": (
                "This state's pack has no municipal law table, so there is nothing to rank yet."
            ),
        }
    current = {o: run_fn(ctx, o) for o in options}
    now = {o: _record(current[o].reforms[o].added) for o in options}
    now_draws = {o: _state_draws(current[o].reforms[o]) for o in options}
    head["current"] = now
    adu_like = key if rule == "adu_category" else None
    ranked = review_priorities(
        rules,
        munis,
        counties=tables.get("counties"),
        typology_tract=tables.get("typology_tract"),
        tract_munis=tables.get("tract_municipalities"),
        adu_run=_run_dict(current[adu_like], adu_like) if adu_like else None,
        hooks=hooks,
        as_of=when,
    )
    ranked = ranked[ranked["score"] > 0].head(int(top or rn.get("top", 5)))
    read, _ = read_flags(rules.reset_index(drop=True), when, hooks)
    read_geoids = set(rules["geoid"].astype(str)[read.to_numpy()])
    if not bounded:
        head["note"] = (
            "The engine takes no local reading for this reform yet, so a reading changes no "
            "number; the list shows the review order and effort only."
        )
    uses_law = {
        k for k, v in settings["reforms"].items() if v["rule"] in (rn.get("treatments") or {})
    }
    pref_now = max(options, key=lambda o: now[o]["p50"])
    labels = rn.get("labels") or {}
    head["preferred_now"] = pref_now
    head["labels"] = {o: str(labels.get(o, o)) for o in options}
    for row in ranked.to_dict("records"):
        unit = str(row["unit_id"])
        item: dict[str, Any] = {
            "rank": int(row["rank"]),
            "unit_id": unit,
            "names": row["names"],
            "county": row["county"],
            "county_name": row["county_name"],
            "municipalities": int(row["municipalities"]),
            "unknown_lots": _num(
                float(row["unknown_lots"]),
                "lots",
                "single-family lots in the unit whose rule is not read",
                ["acs5_api", "census_baf_2020_mcd"],
            ),
            "score": _num(
                float(row["score"]),
                "",
                "unknown lots x sensitivity x representativeness / effort (law.priorities)",
                [LAW_SOURCE],
                [],
            ),
            "effort": {
                "value": row["effort"],
                "basis": str(row["effort_basis"]).replace("_", " "),
                "text": (
                    f"Effort {show(float(row['effort']), '', TABLE_SIG)} "
                    f"({str(row['effort_basis']).replace('_', ' ')}; 1 = "
                    "one own-site PDF with a text layer)."
                ),
            },
            "source_url": row.get("source_url") or None,
            "note": row.get("note") or None,
        }
        if bounded:
            geoids = _unit_geoids(unit, munis, read_geoids)
            bounds: dict[str, Any] = {}
            for side in ("permissive", "restrictive"):
                fields = rn["treatments"][rule][side]
                treated = _treat(rules, geoids, fields, str(rn["treated_effective_date"]))
                law = local_law_for_engine(treated, munis, hooks, as_of=when)
                ctx2 = replace(ctx, local_law=law)
                vals = {}
                side_draws = None
                for o in options:
                    if o in uses_law:
                        out_o = run_fn(ctx2, o).reforms[o]
                        vals[o] = _record(out_o.added)
                        if o == key:
                            side_draws = _state_draws(out_o)
                    else:
                        vals[o] = now[o]
                        if o == key:
                            side_draws = now_draws[o]
                share = county_answer_coverage(treated, munis, rule, hooks, when).get(
                    str(row["county"]), 0.0
                )
                used = law is not None and str(row["county"]) in (
                    law.adu_coefficients() if rule == "adu_category" else law.parking_factors()
                )
                base_draws = now_draws[key]
                if side_draws is None or base_draws is None or side_draws.shape != base_draws.shape:
                    raise ValueError("read-next needs the per-draw statewide results of both runs")
                pref = max(options, key=lambda o, v=vals: v[o]["p50"])
                bounds[side] = {
                    "added": vals,
                    "change": _draw_num(
                        side_draws - base_draws,
                        "units",
                        f"statewide added homes with the unit read as {side} - statewide added "
                        "homes with today's readings, in the same draw (same seed and draws)",
                        vals[key]["source_ids"] + [LAW_SOURCE],
                        vals[key]["assumption_ids"],
                    ),
                    "preferred": pref,
                    "county_coverage": _num(
                        share,
                        "share",
                        f"{_coverage_words(rule)} / county residents, after this reading",
                        [LAW_SOURCE, "census_subcounty_pop"],
                    ),
                    "engine_uses_county": bool(used),
                }
            item["bounds"] = bounds
            switch = any(b["preferred"] != pref_now for b in bounds.values())
            item["switch"] = switch
            lo = min(b["change"]["value"] for b in bounds.values())
            hi = max(b["change"]["value"] for b in bounds.values())
            item["impact"] = _range(
                lo,
                hi,
                "units",
                "the lower and the higher of the two bounds' changes in statewide added homes "
                "(each the P50 of its per-draw change)",
                [LAW_SOURCE],
                [],
            )
            label = {o: str(labels.get(o, o)) for o in options}
            if switch:
                item["switch_text"] = (
                    "Reading this ordinance could switch the preferred option: "
                    + "; ".join(
                        f"read as {s}, {label[b['preferred']]} adds more" for s, b in bounds.items()
                    )
                    + "."
                )
            elif not any(b["engine_uses_county"] for b in bounds.values()):
                item["switch_text"] = (
                    "No change and no switch: even after this reading, the readings the engine "
                    "can use in this county stay below the coverage bar, so it keeps the "
                    "statewide range there."
                )
            else:
                item["switch_text"] = (
                    f"No switch: {label[pref_now]} adds more homes under both readings."
                )
        head["units"].append(item)
    return head


# ------------------------------------------------------------------ cache, static, command


def read_next_cached(
    ctx: Any, key: str, tables: Mapping[str, pd.DataFrame], run_fn: Any, *, compute: bool = True
) -> Any:
    """:func:`read_next` from the comparisons disk cache, or computed and kept.

    Returns ``None`` when it is not on disk and ``compute`` is false.
    """
    from visionhousing.api.comparison_cache import _clean, default_cache, fingerprint

    rn = load_settings()["read_next"]
    cache = default_cache()
    shared = (
        fingerprint(ctx, seed=int(rn["seed"]), draws=int(rn["draws"])) if cache is not None else ""
    )
    part = f"{PART}_{key}"
    if cache is not None:
        hit = cache.load(ctx.state, shared, part)
        if hit is not None:
            return hit["value"]
    if not compute:
        return None
    value = _clean(read_next(ctx, key, tables, run_fn))
    if cache is not None:
        cache.save(ctx.state, shared, part, {"state": ctx.state, "reform": key}, value)
    return value


def default_run_fn(seed: int = 0, draws: int = 2000) -> Callable[[Any, str], Any]:
    """``run_fn`` for :func:`read_next`: the engine, uncached.

    Uncached because a changed context keeps its hashes.
    """
    from visionhousing.engine.core import run

    def run_fn(ctx: Any, key: str) -> Any:
        return run(ctx.state, [key], seed=seed, draws=draws, context=ctx, use_cache=False)

    return run_fn


def _dumps(value: Any) -> bytes:
    from visionhousing.api.comparison_cache import _clean

    return json.dumps(_clean(value), allow_nan=False, separators=(",", ":")).encode()


def write_static(
    ctx: Any,
    out_dir: Path,
    results: Mapping[str, Any],
    tables: Mapping[str, pd.DataFrame],
    *,
    with_read_next: bool = True,
) -> dict[str, int]:
    """Write ``<out_dir>/<ST>/law_changes/<KEY>.json`` per reform; return the bytes written.

    ``results`` maps a reform key to its default run (the exporter's own runs).
    Read-next results are added only when they are already in the disk cache
    (no engine runs here). The exporter adds ``"law_changes": [keys]`` to the
    state's index.
    """
    settings = load_settings()
    folder = Path(out_dir) / ctx.state / settings["static"]["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for key in settings["reforms"]:
        res = results.get(key)
        if res is None:
            continue
        body = {"changes": build_payload(ctx, key, tables, res, settings=settings)}
        if with_read_next:
            body["read_next"] = read_next_cached(ctx, key, tables, None, compute=False)
        data = _dumps(body)
        (folder / f"{key}.json").write_bytes(data)
        sizes[key] = len(data)
    return sizes


def size_estimate(
    ctx: Any, tables: Mapping[str, pd.DataFrame], results: Mapping[str, Any]
) -> dict[str, Any]:
    """Raw and gzip bytes of each reform's static file (as :func:`write_static`), unwritten."""
    settings = load_settings()
    out: dict[str, Any] = {}
    for key in settings["reforms"]:
        if key not in results:
            continue
        body = {"changes": build_payload(ctx, key, tables, results[key], settings=settings)}
        body["read_next"] = read_next_cached(ctx, key, tables, None, compute=False)
        data = _dumps(body)
        out[key] = {"raw": len(data), "gzip": len(gzip.compress(data, 9))}
    out["total_gzip"] = sum(v["gzip"] for v in out.values() if isinstance(v, dict))
    out["total_raw"] = sum(v["raw"] for v in out.values() if isinstance(v, dict))
    return out


def _keys_for(
    ctx: Any, settings: Mapping[str, Any], tables: Mapping[str, pd.DataFrame]
) -> list[str]:
    """Reforms this state can show: the benchmarks, and a bill only where its row exists."""
    keys = []
    for k, spec in settings["reforms"].items():
        if spec.get("bill") and _bill_row(tables, spec["bill"]) is None:
            continue
        keys.append(k)
    return keys


def register(
    app: Any, *, api: str, error: Any, context: Any, execute: Any, timed: Any, folder: Any
) -> None:
    """Add the two ``law-changes`` addresses to ``app``."""
    from visionhousing.api.models import LIMITS, RunRequest
    from visionhousing.api.runner import Runner

    def checked(st: str, request: Request) -> tuple[Any, str, dict[str, pd.DataFrame]]:
        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        key = str(request.query_params.get("r", "adu"))
        ctx = context(st)
        tables = read_tables(folder(ctx.state))
        if key not in _keys_for(ctx, load_settings(), tables):
            raise error(
                400, "invalid_reform", f"no law-change view for '{key[:40]}' in {ctx.state}"
            )
        return ctx, key, tables

    @app.get(f"{api}/states/{{st}}/law-changes")
    def law_changes(st: str, request: Request) -> Response:
        """What the reform changes in each county and municipality (four answers)."""
        ctx, key, tables = checked(st, request)
        view = str(request.query_params.get("view", "default"))
        if view not in VIEWS:
            raise error(400, "invalid_view", f"no law-change view '{view[:40]}'")
        _rk, result = execute(RunRequest(state=ctx.state, reforms=[key], explain=False))
        parts = ["law_changes", ctx.state, ctx.data_hash, ctx.calibration_hash, ctx.ledger.hash()]
        if view != "default":  # the default key is unchanged
            from visionhousing.engine.zone_scope import table_fingerprint

            parts += [view, table_fingerprint(folder=folder(ctx.state)) or "no-table"]
        rkey = Runner.key(*parts, key)
        body = timed(rkey, lambda: {"changes": build_payload(ctx, key, tables, result, view=view)})
        return Response(_dumps(body), media_type="application/json")

    @app.get(f"{api}/states/{{st}}/law-changes/read-next")
    def law_read_next(st: str, request: Request) -> Response:
        """The bounding runs for the top unread ordinances (slow the first time; cached)."""
        ctx, key, tables = checked(st, request)
        rn = load_settings()["read_next"]
        run_fn = default_run_fn(int(rn["seed"]), int(rn["draws"]))
        rkey = Runner.key(
            PART, ctx.state, ctx.data_hash, ctx.calibration_hash, ctx.ledger.hash(), key
        )
        body = timed(rkey, lambda: {"read_next": read_next_cached(ctx, key, tables, run_fn)})
        return Response(_dumps(body), media_type="application/json")


def main(argv: list[str] | None = None) -> int:
    """The command.

    ``python -m visionhousing.api.law_changes --state PA [--estimate] [--read-next] [--out D]``.
    """
    from visionhousing.engine.context import load_context, pack_dir
    from visionhousing.engine.core import run

    p = argparse.ArgumentParser(
        description="What each reform changes, place by place; read-next bounds"
    )
    p.add_argument("--state", default="PA")
    p.add_argument(
        "--estimate", action="store_true", help="print the static files' sizes (raw, gzip)"
    )
    p.add_argument(
        "--read-next", action="store_true", help="compute and cache the read-next bounds"
    )
    p.add_argument(
        "--out", type=Path, default=None, help="write the static files under this data folder"
    )
    args = p.parse_args(argv)
    try:
        ctx = load_context(args.state.upper())
    except (FileNotFoundError, ValueError) as err:
        print(f"law-changes: {err}", file=sys.stderr)
        return 1
    tables = read_tables(pack_dir(ctx.state))
    settings = load_settings()
    keys = _keys_for(ctx, settings, tables)
    results = {k: run(ctx.state, [k], context=ctx) for k in keys}
    if args.read_next:
        rn = settings["read_next"]
        run_fn = default_run_fn(int(rn["seed"]), int(rn["draws"]))
        for k in keys:
            value = read_next_cached(ctx, k, tables, run_fn)
            print(f"read-next {k}: {len(value.get('units', []))} units bounded")
    if args.estimate:
        print(json.dumps(size_estimate(ctx, tables, results), indent=1))
    if args.out:
        print(json.dumps(write_static(ctx, args.out, results, tables), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
