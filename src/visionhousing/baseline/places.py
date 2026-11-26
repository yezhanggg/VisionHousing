"""Back-test error by kind of place: where the baseline has been right or wrong.

Plain-language summary
----------------------
The rolling-origin back-test (:mod:`visionhousing.baseline.backtest`) gives one
*case* per county and past start year: what the chosen rule projected for five
years, and what was actually permitted. The skill table adds all cases up. This
module splits them by kind of place, so a reader can see whether the baseline is
weaker in some places than others:

- **county size** (the terciles the baseline already uses for its ranges);
- **county type** (the "places like this one" groups of
  :mod:`visionhousing.features.typology`, pack table ``typology_county``);
- **urban or rural** (share of the county's homes in Census urban areas);
- **Qualified Census Tract share** (higher or lower than the state's median
  county, pack table ``incentives_county``);
- **named counties** (for Pennsylvania: Philadelphia, Allegheny, the rest).

For each group it reports three things:

1. **Signed error**: ``log(projected / actual)`` for each case. Above zero the
   rule projected too much (it "ran high"), below zero too little. The median
   is turned back into a percent, ``exp(median) - 1``, for the sentences.
2. **Median absolute error**: the median of ``|log(projected / actual)|``,
   the same measure the skill table uses (0.10 means "typically about 10% off").
3. **Interval coverage**: the share of cases whose actual total fell inside the
   P10-P90 range. Each case's range is built only from *other* counties' errors
   at earlier origins whose five target years had all been observed by then
   (``backtest.leave_one_county_out``), so this is an out-of-sample test.
   A well-judged 80% range catches about 8 cases in 10.

A group with fewer counties or cases than the minimums in
``assumptions/backtest.yaml`` is still shown, flagged "few cases", never hidden.
Plain sentences ("The baseline runs 12% high in rural counties; its ranges
caught the real total in 150 of 190 back-test cases") are written by rule from
these numbers. A grouping whose input table is missing (a state with less
complete data) is listed as not available, with the reason.

It also gives each county's own median signed error, for the county error map,
and names the worst groups for the "what the model cannot tell you" lists.

Everything except :func:`read_pack_table` and :func:`place_backtest_for_context`
is pure: tables in, tables out.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    class_label,
    lead_years,
    leave_one_county_out,
    origin_size_classes,
    size_classes,
)
from visionhousing.baseline.panel import to_panel
from visionhousing.baseline.rules import PlaceInfo, Rule
from visionhousing.baseline.settings import INTERVAL, BaselineSettings
from visionhousing.ledger.loader import ROOT

__all__ = [
    "DIMENSION_TITLES",
    "RULES_PATH",
    "PlaceBacktest",
    "badly_off_named",
    "county_errors",
    "county_groups",
    "county_note",
    "county_notes_for_state",
    "error_by_group",
    "findings",
    "group_stats",
    "limits_sentence",
    "load_place_rules",
    "place_backtest",
    "place_backtest_for_context",
    "read_pack_table",
    "scored_cases",
    "weakest_groups",
]

RULES_PATH = ROOT / "assumptions" / "backtest.yaml"

DIMENSION_TITLES: dict[str, str] = {
    "all": "All counties",
    "size_class": "County size (recent permits)",
    "typology": "County type (places like this one)",
    "urban_rural": "Urban or rural",
    "qct": "Share of Qualified Census Tracts",
    "named": "Named counties",
}
"""Headings of the groupings (labels, not numbers)."""

QCT_LABELS = ("higher share", "lower share")


def load_place_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """Read ``assumptions/backtest.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- cases


def scored_cases(
    cases: pd.DataFrame,
    classes: np.ndarray | Mapping[int, np.ndarray],
    lead: int | None = None,
    horizon: int | None = None,
) -> pd.DataFrame:
    """Add each case's signed error and whether its out-of-sample range caught the actual.

    Args:
        cases: Output of ``backtest_cases`` for counties.
        classes: Size class of each county, or origin -> size classes at that
            origin (for the leave-one-county-out ranges).
        lead: Years from origin to the first target year; with ``horizon`` the
            ranges use earlier origins only (``backtest.leave_one_county_out``).
        horizon: Target years.

    Returns:
        The cases with a usable ratio (projection above zero), with added columns
        ``signed_error`` (``log(projected / actual)``; ``NaN`` when the actual is
        zero), ``low``, ``high`` (``NaN`` when no range could be built) and
        ``covered`` (1.0 inside the range, 0.0 outside, ``NaN`` with no range).
    """
    out = cases[np.isfinite(cases["ratio"])].copy()
    both = (out["actual"] > 0) & (out["projected"] > 0)
    signed = np.full(len(out), np.nan)
    signed[both.to_numpy()] = np.log(
        out.loc[both, "projected"].to_numpy() / out.loc[both, "actual"].to_numpy()
    )
    out["signed_error"] = signed
    ranged = leave_one_county_out(cases, classes, lead, horizon)
    out["low"] = ranged["low"].reindex(out.index)
    out["high"] = ranged["high"].reindex(out.index)
    has = out["low"].notna()
    inside = (out["actual"] >= out["low"]) & (out["actual"] <= out["high"])
    out["covered"] = np.where(has, inside.astype(float), np.nan)
    return out


# --------------------------------------------------------------------------- groups


def _urban_share(tracts: pd.DataFrame, rules: Mapping[str, Any]) -> pd.Series | None:
    """Share of each county's homes in urban areas, or ``None`` if the tracts cannot say."""
    t = tracts.assign(county=tracts["county"].astype(str))
    if "housing_units" not in t.columns:
        return None
    homes = pd.to_numeric(t["housing_units"], errors="coerce")
    if homes.isna().all():
        return None
    if "urban_unit_share" in t.columns and t["urban_unit_share"].notna().any():
        urban = pd.to_numeric(t["urban_unit_share"], errors="coerce")
    elif "area_type" in t.columns:
        kinds = {str(k) for k in rules["urban_rural"]["urban_area_types"]}
        urban = t["area_type"].astype(str).isin(kinds).astype(float)
        urban[t["area_type"].isna()] = np.nan
    else:
        return None
    ok = homes.notna() & urban.notna()
    num = (homes * urban)[ok].groupby(t.loc[ok, "county"]).sum()
    den = homes[ok].groupby(t.loc[ok, "county"]).sum()
    return (num / den.where(den > 0)).dropna()


def county_groups(
    counties: Sequence[str],
    classes: np.ndarray,
    n_classes: int,
    rules: Mapping[str, Any],
    *,
    state: str,
    tracts: pd.DataFrame | None = None,
    typology: pd.DataFrame | None = None,
    incentives: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Put every county in one group of each grouping that the data allow.

    Args:
        counties: County ids, in panel order.
        classes: Size class of each county (``backtest.size_classes``).
        n_classes: Number of size classes.
        rules: ``assumptions/backtest.yaml``.
        state: State code (for the named counties).
        tracts: Tract table (``county``, ``housing_units`` and ``urban_unit_share``
            or ``area_type``), or ``None``.
        typology: Pack table ``typology_county`` (``geoid``, ``type_id``,
            ``type_name``), or ``None``.
        incentives: Pack table ``incentives_county`` (``county``, ``tracts``,
            ``qct_tracts``), or ``None``.

    Returns:
        ``(groups, dimensions)``: a frame indexed by county with one column per
        available grouping (the group label, ``None`` where the county has no
        value), and one entry per grouping in ``rules["dimensions"]`` order:
        ``key``, ``title``, ``available``, ``reason`` (why not available, or
        ``""``), ``groups`` (labels in display order) and ``phrases`` (label to
        the words used after "in", such as "rural counties").
    """
    ids = [str(c) for c in counties]
    groups = pd.DataFrame(index=pd.Index(ids, name="county"))
    dims: list[dict[str, Any]] = []

    def add(
        key: str,
        labels: Mapping[str, str] | None,
        order: list[str],
        phrases: dict[str, str],
        reason: str = "",
    ) -> None:
        if labels is None:
            dims.append(
                {
                    "key": key,
                    "title": DIMENSION_TITLES[key],
                    "available": False,
                    "reason": reason,
                    "groups": [],
                    "phrases": {},
                }
            )
            return
        groups[key] = [labels.get(c) for c in ids]
        present = [g for g in order if g in set(groups[key].dropna())]
        dims.append(
            {
                "key": key,
                "title": DIMENSION_TITLES[key],
                "available": True,
                "reason": "",
                "groups": present,
                "phrases": {g: phrases[g] for g in present},
            }
        )

    for key in rules["dimensions"]:
        if key == "size_class":
            order = [class_label(k, n_classes) for k in range(n_classes)]
            labels = {c: class_label(int(classes[i]), n_classes) for i, c in enumerate(ids)}
            add(key, labels, order, {g: f"{g} counties" for g in order})
        elif key == "typology":
            if typology is None or typology.empty or "type_name" not in typology.columns:
                add(key, None, [], {}, "the state pack has no county typology table")
                continue
            t = typology.assign(geoid=typology["geoid"].astype(str))
            t = t.sort_values(["type_id", "geoid"]) if "type_id" in t.columns else t
            order = list(dict.fromkeys(t["type_name"].astype(str)))
            labels = dict(zip(t["geoid"], t["type_name"].astype(str), strict=True))
            add(key, labels, order, {g: f"'{g}' counties" for g in order})
        elif key == "urban_rural":
            share = None if tracts is None else _urban_share(tracts, rules)
            if share is None or share.empty:
                add(key, None, [], {}, "the tract table has no urban-area share or area type")
                continue
            cut = float(rules["urban_rural"]["rural_below"])
            labels = {c: ("rural" if v < cut else "urban") for c, v in share.items()}
            add(
                key,
                labels,
                ["urban", "rural"],
                {"urban": "urban counties", "rural": "rural counties"},
            )
        elif key == "qct":
            need = {"county", "tracts", "qct_tracts"}
            if incentives is None or incentives.empty or not need <= set(incentives.columns):
                add(key, None, [], {}, "the state pack has no incentives table")
                continue
            inc = incentives.assign(county=incentives["county"].astype(str)).set_index("county")
            den = pd.to_numeric(inc["tracts"], errors="coerce")
            share = pd.to_numeric(inc["qct_tracts"], errors="coerce") / den.where(den > 0)
            share = share.dropna().reindex([c for c in ids if c in share.index]).dropna()
            if share.empty:
                add(key, None, [], {}, "no county has a Qualified Census Tract count")
                continue
            if str(rules["qct"]["split"]) != "median":
                raise ValueError("qct.split must be 'median'")
            mid = float(share.median())
            hi, lo = QCT_LABELS
            labels = {c: (hi if v > mid else lo) for c, v in share.items()}
            add(
                key,
                labels,
                [hi, lo],
                {
                    hi: "counties with a higher share of Qualified Census Tracts",
                    lo: "counties with a lower share of Qualified Census Tracts",
                },
            )
        elif key == "named":
            named = {
                str(k): str(v)
                for k, v in (rules.get("named_counties") or {}).get(state.upper(), {}).items()
            }
            named = {k: v for k, v in named.items() if k in ids}
            if not named:
                add(key, None, [], {}, f"no named counties for {state.upper()}")
                continue
            rest = str(rules["rest_label"])
            labels = {c: named.get(c, rest) for c in ids}
            order = [*named.values(), rest]
            add(key, labels, order, {**{v: v for v in named.values()}, rest: f"the {rest}"})
        else:
            raise ValueError(f"unknown grouping {key!r} in backtest.yaml")
    return groups, dims


# --------------------------------------------------------------------------- statistics


def group_stats(cases: pd.DataFrame) -> dict[str, Any]:
    """Error and coverage of one set of scored cases.

    Returns:
        ``cases`` (with a signed error), ``median_signed`` (log), ``typical_pct``
        (``exp(median_signed) - 1``), ``median_abs`` (median of the absolute log
        error), ``interval_cases`` (cases with a range: M), ``covered_cases``
        (cases inside it: N) and ``coverage`` (N / M). Empty parts are ``NaN``.
    """
    s = cases["signed_error"].to_numpy(dtype=float)
    s = s[np.isfinite(s)]
    c = cases["covered"].to_numpy(dtype=float)
    c = c[np.isfinite(c)]
    med = float(np.median(s)) if s.size else math.nan
    return {
        "cases": int(s.size),
        "median_signed": med,
        "typical_pct": math.expm1(med) if s.size else math.nan,
        "median_abs": float(np.median(np.abs(s))) if s.size else math.nan,
        "interval_cases": int(c.size),
        "covered_cases": int(c.sum()),
        "coverage": float(c.mean()) if c.size else math.nan,
    }


def _pct(x: float) -> str:
    return f"{round(abs(x) * 100):.0f}%"


def _direction(pct: float, about: float) -> str:
    if not math.isfinite(pct):
        return "has no scored cases"
    if abs(pct) < about:
        return "is about right on average"
    return f"runs {_pct(pct)} {'high' if pct > 0 else 'low'}"


def _texts(st: Mapping[str, Any], about: float) -> dict[str, str]:
    """Short text forms of a group's numbers, for tables (the screen only shows these)."""
    p = st["typical_pct"]
    if not math.isfinite(p):
        err = "no cases"
    elif abs(p) < about:
        err = f"about right ({'+' if p >= 0 else '-'}{_pct(p)})"
    else:
        err = f"{_pct(p)} {'high' if p > 0 else 'low'}"
    m = st["median_abs"]
    caught = (
        f"{st['covered_cases']} of {st['interval_cases']} ({_pct(st['coverage'])})"
        if st["interval_cases"]
        else "no ranges"
    )
    return {
        "error_text": err,
        "abs_text": _pct(math.expm1(m)) if math.isfinite(m) else "no cases",
        "caught_text": caught,
    }


def _sentence(row: Mapping[str, Any], about: float) -> str:
    where = "across all counties" if row["dimension"] == "all" else f"in {row['phrase']}"
    text = f"The baseline {_direction(row['typical_pct'], about)} {where}"
    if row["interval_cases"]:
        text += (
            f"; its ranges caught the real total in {row['covered_cases']} of "
            f"{row['interval_cases']} back-test cases"
        )
    text += "."
    if row["small"]:
        n = row["counties"]
        noun = "county" if n == 1 else "counties"
        text += f" Few cases ({n} {noun}): read this as a few places, not a pattern."
    return text


def error_by_group(
    scored: pd.DataFrame,
    groups: pd.DataFrame,
    dims: Sequence[Mapping[str, Any]],
    counties: Sequence[str],
    rules: Mapping[str, Any],
) -> pd.DataFrame:
    """Error and coverage for all counties and for every group of every grouping.

    Args:
        scored: Output of :func:`scored_cases` (``place`` indexes ``counties``).
        groups: Groups frame of :func:`county_groups`.
        dims: Groupings of :func:`county_groups`.
        counties: County ids, in panel order.
        rules: ``assumptions/backtest.yaml`` (minimums and the "about right" band).

    Returns:
        One row for all counties, then one per group: ``dimension``, ``title``,
        ``group``, ``phrase``, ``counties``, the :func:`group_stats` columns,
        ``small`` (below ``min_counties`` or ``min_cases``) and ``finding``.
    """
    ids = np.array([str(c) for c in counties])
    county_of = ids[scored["place"].to_numpy(dtype=int)] if len(scored) else np.array([])
    min_counties, min_cases = int(rules["min_counties"]), int(rules["min_cases"])
    about = float(rules["about_right"])
    rows = []

    def row(dim: str, title: str, group: str, phrase: str, members: set[str]) -> None:
        mask = np.isin(county_of, list(members)) if len(scored) else np.array([], dtype=bool)
        st = group_stats(scored[mask])
        r = {
            "dimension": dim,
            "title": title,
            "group": group,
            "phrase": phrase,
            "counties": len(members),
            **st,
        }
        r["small"] = bool(len(members) < min_counties or st["cases"] < min_cases)
        r["finding"] = _sentence(r, about)
        r.update(_texts(st, about))
        rows.append(r)

    row("all", DIMENSION_TITLES["all"], "all counties", "all counties", set(ids))
    for d in dims:
        if not d["available"]:
            continue
        col = groups[d["key"]]
        for g in d["groups"]:
            row(d["key"], d["title"], g, d["phrases"][g], set(col.index[col == g]))
    return pd.DataFrame(rows)


def county_errors(
    scored: pd.DataFrame,
    counties: Sequence[str],
    rules: Mapping[str, Any],
    names: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Each county's own median signed error, with its map class.

    The classes are symmetric around zero, from ``map_breaks`` in
    ``assumptions/backtest.yaml`` (on ``typical_pct``): ``well_low``, ``low``,
    ``near``, ``high``, ``well_high``; ``nodata`` with no scored case.

    Returns:
        ``(table, classes)``: one row per county (``county``, ``name``, the
        :func:`group_stats` columns, ``cls``), and the classes in order with
        their ``id`` and ``label``.
    """
    small, big = (float(x) for x in rules["map_breaks"])
    classes = [
        {"id": "well_low", "label": f"runs {_pct(big)} or more low"},
        {"id": "low", "label": f"runs {_pct(small)} to {_pct(big)} low"},
        {"id": "near", "label": f"within {_pct(small)}"},
        {"id": "high", "label": f"runs {_pct(small)} to {_pct(big)} high"},
        {"id": "well_high", "label": f"runs {_pct(big)} or more high"},
    ]
    names = names or {}
    rows = []
    for i, c in enumerate(counties):
        st = group_stats(scored[scored["place"] == i])
        p = st["typical_pct"]
        if not math.isfinite(p):
            cls = "nodata"
        elif p <= -big:
            cls = "well_low"
        elif p <= -small:
            cls = "low"
        elif p < small:
            cls = "near"
        elif p < big:
            cls = "high"
        else:
            cls = "well_high"
        rows.append(
            {
                "county": str(c),
                "name": names.get(str(c), str(c)),
                **st,
                **_texts(st, float(rules["about_right"])),
                "cls": cls,
            }
        )
    return pd.DataFrame(rows), classes


def findings(table: pd.DataFrame, rules: Mapping[str, Any]) -> list[str]:
    """The headline sentences, by rule.

    1. All counties together.
    2. For each grouping, the group with the largest signed error (either way),
       among groups not flagged "few cases" (all groups if every one is).
    3. If the largest signed error of any group is in a group flagged "few
       cases" (one named county, say), that group too, with its flag.
    4. The group whose range caught the actual least often, against the share
       an 80% range should catch.
    """
    out = [str(table.iloc[0]["finding"])]
    rest = table[table["dimension"] != "all"]
    for _, part in rest.groupby("dimension", sort=False):
        pool = part[~part["small"]] if (~part["small"]).any() else part
        pool = pool[np.isfinite(pool["median_signed"])]
        if len(part) < 2 or pool.empty:
            continue
        worst = pool.loc[pool["median_signed"].abs().idxmax()]
        out.append(str(worst["finding"]))
    signed = rest[np.isfinite(rest["median_signed"])]
    if not signed.empty:
        top = signed.loc[signed["median_signed"].abs().idxmax()]
        if top["small"]:
            out.append(str(top["finding"]))
    ok = rest[~rest["small"] & np.isfinite(rest["coverage"])]
    if not ok.empty:
        w = ok.loc[ok["coverage"].idxmin()]
        aim = INTERVAL[1] - INTERVAL[0]
        counts = f"{w['covered_cases']} of {w['interval_cases']} cases ({_pct(w['coverage'])})"
        if w["coverage"] < aim:
            out.append(
                f"Its ranges were least reliable in {w['phrase']}: they caught the real total "
                f"in {counts}, against the {_pct(aim)} a P10-P90 range aims for."
            )
        else:
            out.append(
                f"Even where its ranges did worst, in {w['phrase']}, they caught the real total "
                f"in {counts}, at or above the {_pct(aim)} a P10-P90 range aims for."
            )
    return list(dict.fromkeys(out))


def weakest_groups(table: pd.DataFrame, n: int) -> pd.DataFrame:
    """The ``n`` groups with the largest median absolute error, "few cases" groups left out."""
    rest = table[(table["dimension"] != "all") & ~table["small"]]
    rest = rest[np.isfinite(rest["median_abs"])]
    return rest.sort_values(
        ["median_abs", "dimension", "group"], ascending=[False, True, True]
    ).head(n)


def badly_off_named(table: pd.DataFrame, rules: Mapping[str, Any]) -> pd.DataFrame:
    """Named counties flagged "few cases" whose typical error is still far off.

    One named county is always a "few cases" group, so :func:`weakest_groups`
    leaves it out. A single county that the baseline misses by a wide margin
    (``name_few_cases_beyond`` in ``assumptions/backtest.yaml``, as a share) is
    still worth naming in the limits. Largest error first.
    """
    beyond = rules.get("name_few_cases_beyond")
    if beyond is None:
        return table.iloc[0:0]
    rows = table[
        (table["dimension"] == "named")
        & table["small"]
        & np.isfinite(table["typical_pct"])
        & (table["counties"] == 1)
    ]
    rows = rows[rows["typical_pct"].abs() >= float(beyond)]
    return rows.reindex(rows["typical_pct"].abs().sort_values(ascending=False).index)


def limits_sentence(weak: pd.DataFrame, far_off: pd.DataFrame | None = None) -> str | None:
    """One sentence, with no figures, naming where the baseline has been weakest.

    ``weak`` is :func:`weakest_groups`; ``far_off`` (optional) is
    :func:`badly_off_named`: single named counties that are "few cases" but far
    off, named first and said to be one county. Group names with a digit are
    skipped, so the sentence never carries a number that is not cited. ``None``
    when there is nothing to name.
    """
    parts = []
    for _, r in (far_off if far_off is not None else weak.iloc[0:0]).iterrows():
        if any(ch.isdigit() for ch in str(r["phrase"])):
            continue
        way = "too high" if r["median_signed"] > 0 else "too low"
        parts.append(f"{r['phrase']} (one county, but far off: it has tended to run {way})")
    for _, r in weak.iterrows():
        if any(ch.isdigit() for ch in str(r["phrase"])):
            continue
        way = "too high" if r["median_signed"] > 0 else "too low"
        parts.append(f"{r['phrase']} (where it has tended to run {way})")
    if not parts:
        return None
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return (
        f"Where the baseline itself has been least accurate in past years: {joined}. "
        "Read the change in those places with extra care; the Method page shows the back-test."
    )


def county_note(counties: pd.DataFrame, county: str, rules: Mapping[str, Any]) -> str | None:
    """One plain sentence on how the baseline did for one county in the back-test.

    "In past years, this tool's baseline for this county ran about 53% low; its
    ranges caught the real total in 5 of 21 years." Built from the county's row
    of :func:`county_errors`; ``None`` when the county has no scored case. Each
    case is one past start year, so the count is in years.
    """
    hit = counties[counties["county"].astype(str) == str(county)]
    if hit.empty:
        return None
    r = hit.iloc[0]
    pct = float(r["typical_pct"])
    if not math.isfinite(pct):
        return None
    about = float(rules["about_right"])
    if abs(pct) < about:
        how = f"was about right on average (within {_pct(about)})"
    else:
        how = f"ran about {_pct(pct)} {'high' if pct > 0 else 'low'}"
    text = f"In past years, this tool's baseline for this county {how}"
    if int(r["interval_cases"]):
        text += (
            f"; its ranges caught the real total in {int(r['covered_cases'])} of "
            f"{int(r['interval_cases'])} years"
        )
    return text + "."


def county_notes_for_state(state: str) -> dict[str, str]:
    """:func:`county_note` for every county of a state's default run (cached by the engine).

    Loads the state's context, picks the baseline rule the default run uses and
    reads the back-test by place. Returns an empty dict when the state cannot
    be loaded.
    """
    from visionhousing.baseline.project import baseline_from_ledger
    from visionhousing.engine.core import _context

    try:
        ctx = _context(state)
    except FileNotFoundError:
        return {}
    key = ("notes", ctx.state, ctx.data_hash, ctx.ledger.hash())
    if key in _NOTES:
        return _NOTES[key]
    from visionhousing.baseline.contest import place_info_for_context

    baseline, _ = baseline_from_ledger(ctx.permits, ctx.ledger, places=place_info_for_context(ctx))
    pb = place_backtest_for_context(ctx, baseline.rule)
    rules = load_place_rules()
    out = {}
    for c in pb.counties["county"].astype(str):
        note = county_note(pb.counties, c, rules)
        if note:
            out[c] = note
    _NOTES[key] = out
    return out


_NOTES: dict[tuple[str, ...], dict[str, str]] = {}


# --------------------------------------------------------------------------- one call


@dataclass(frozen=True)
class PlaceBacktest:
    """The back-test broken down by kind of place, for one state and rule.

    Attributes:
        rule: Name of the rule tested (the one the baseline uses).
        groups: Output of :func:`error_by_group`.
        dimensions: Groupings of :func:`county_groups` (with "not available" ones).
        counties: Table of :func:`county_errors`.
        map_classes: The county map classes, in order.
        findings: Headline sentences (:func:`findings`).
        weakest: Output of :func:`weakest_groups`.
        limits: Output of :func:`limits_sentence`.
    """

    rule: str
    groups: pd.DataFrame = field(repr=False)
    dimensions: list[dict[str, Any]] = field(repr=False)
    counties: pd.DataFrame = field(repr=False)
    map_classes: list[dict[str, Any]] = field(repr=False)
    findings: list[str] = field(repr=False)
    weakest: pd.DataFrame = field(repr=False)
    limits: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (``NaN`` becomes ``None``)."""

        def recs(frame: pd.DataFrame) -> list[dict[str, Any]]:
            return [
                {
                    k: (
                        None
                        if isinstance(v, float) and not math.isfinite(v)
                        else (v.item() if hasattr(v, "item") else v)
                    )
                    for k, v in r.items()
                }
                for r in frame.to_dict(orient="records")
            ]

        return {
            "rule": self.rule,
            "measure": {
                "signed_error": "log(projected / actual); above zero the baseline ran high",
                "typical_pct": "exp(median signed error) - 1",
                "median_abs": "median of |log(projected / actual)|",
                "coverage": "share of cases inside the P10-P90 range built from other counties",
            },
            "dimensions": [{k: v for k, v in d.items() if k != "phrases"} for d in self.dimensions],
            "groups": recs(self.groups),
            "counties": recs(self.counties),
            "map_classes": self.map_classes,
            "findings": self.findings,
            "weakest": recs(
                self.weakest[["dimension", "group", "phrase", "median_abs", "median_signed"]]
            )
            if len(self.weakest)
            else [],
            "limits": self.limits,
        }


def place_backtest(
    table: pd.DataFrame,
    rule: Rule,
    settings: BaselineSettings,
    rules: Mapping[str, Any],
    *,
    state: str,
    tracts: pd.DataFrame | None = None,
    typology: pd.DataFrame | None = None,
    incentives: pd.DataFrame | None = None,
    names: Mapping[str, str] | None = None,
    info: PlaceInfo | None = None,
) -> PlaceBacktest:
    """Back-test ``rule`` and break its errors down by kind of place (pure).

    Args:
        table: Tidy permit table (county x year x structure type).
        rule: The rule the baseline uses.
        settings: Baseline settings from the ledger.
        rules: ``assumptions/backtest.yaml``.
        state: State code.
        tracts: Tract table, or ``None`` (see :func:`county_groups`).
        typology: Pack table ``typology_county``, or ``None``.
        incentives: Pack table ``incentives_county``, or ``None``.
        names: County id to name, for the map table.
        info: Place types and homes (for the cross-sectional rules), or ``None``.

    Returns:
        The breakdown.
    """
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    cases = backtest_cases(rule, panel.years, panel.units, origins, lead, settings.horizon, info)
    at_origin = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    scored = scored_cases(cases, at_origin, lead, settings.horizon)
    groups, dims = county_groups(
        panel.counties,
        classes,
        settings.n_classes,
        rules,
        state=state,
        tracts=tracts,
        typology=typology,
        incentives=incentives,
    )
    by_group = error_by_group(scored, groups, dims, panel.counties, rules)
    per_county, map_classes = county_errors(scored, panel.counties, rules, names)
    weak = weakest_groups(by_group, int(rules["limits_worst_groups"]))
    far_off = badly_off_named(by_group, rules)
    return PlaceBacktest(
        rule=rule.name,
        groups=by_group,
        dimensions=dims,
        counties=per_county,
        map_classes=map_classes,
        findings=findings(by_group, rules),
        weakest=weak,
        limits=limits_sentence(weak, far_off),
    )


# --------------------------------------------------------------------------- file access


def read_pack_table(folder: Path | None, name: str) -> pd.DataFrame | None:
    """Read ``<name>.csv`` (or ``.csv.gz``) from a state pack, or ``None`` if it is not there."""
    if folder is None:
        return None
    for file in (f"{name}.csv", f"{name}.csv.gz"):
        path = Path(folder) / file
        if path.is_file():
            return pd.read_csv(path, dtype={"geoid": str, "county": str})
    return None


_CACHE: dict[tuple[str, ...], PlaceBacktest] = {}


def place_backtest_for_context(
    ctx: Any, rule_name: str, folder: Path | None = None
) -> PlaceBacktest:
    """The breakdown for a loaded state context (reads the optional pack tables once).

    Args:
        ctx: A ``StateContext``.
        rule_name: Name of the rule the baseline uses.
        folder: The state's pack folder (default: ``engine.context.pack_dir``).

    Returns:
        The breakdown, cached by state, data, ledger and rule.
    """
    from visionhousing.baseline.contest import all_rules, place_info_for_context
    from visionhousing.baseline.settings import settings_from_ledger
    from visionhousing.engine.context import pack_dir

    held = getattr(ctx, "derived", {}).get(("pack_folder",))  # the folder it was read from
    if folder is None and held is not None:
        folder = held
    folder = Path(folder) if folder is not None else pack_dir(ctx.state)
    key = (ctx.state, ctx.data_hash, ctx.ledger.hash(), rule_name, str(folder))
    if key in _CACHE:
        return _CACHE[key]
    rule = next(r for r in all_rules(ctx.ledger, load_place_rules()) if r.name == rule_name)
    counties = read_pack_table(folder, "counties")
    typology = read_pack_table(folder, "typology_county")
    if typology is None and counties is not None:
        typology = _typology_on_the_fly(ctx, counties)
    names = (
        dict(zip(counties["geoid"].astype(str), counties["name"].astype(str), strict=True))
        if counties is not None and "name" in counties.columns
        else None
    )
    out = place_backtest(
        ctx.permits,
        rule,
        settings_from_ledger(ctx.ledger),
        load_place_rules(),
        state=ctx.state,
        tracts=ctx.tracts,
        typology=typology,
        incentives=read_pack_table(folder, "incentives_county"),
        names=names,
        info=place_info_for_context(ctx),
    )
    _CACHE[key] = out
    return out


def _typology_on_the_fly(ctx: Any, counties: pd.DataFrame) -> pd.DataFrame | None:
    """Work the county typology out from the pack's own tables (as the profile does)."""
    from visionhousing.features.typology import typology_tables

    try:
        made = typology_tables(
            {"tracts": ctx.tracts, "counties": counties, "permits_county_year": ctx.permits}
        )
    except (ValueError, KeyError, IndexError):
        return None
    return made.get("typology_county")
