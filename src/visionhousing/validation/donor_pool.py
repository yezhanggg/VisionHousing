"""Donor-pool validation: can other states' permit areas stand in for a small state's error record?

Plain-language summary
----------------------
The baseline's likely range for a state comes from its own past errors: how far
the chosen rule's five-year projections fell from what was really permitted. A
state with few counties (Vermont has 14) has a short, noisy record, so today its
range is widened toward Pennsylvania's record (``small_sample`` in
``assumptions/backtest.yaml``). This module tests a different source of
borrowed errors, a **donor pool**: past cases from counties in *other* states
that looked like the target state at the time (similar permit volume, similar
recent growth, similar share of permits actually reported rather than imputed).

The test holds out one state at a time. For each past start year (*origin*) of
the held-out state, each method builds the state total's P10-P90 range from its
own set of past errors, and the test checks whether the real five-year total
fell inside. A method whose ranges catch the truth about 80% of the time, without
being wider than needed, describes the state's uncertainty well. The methods:

- ``own``: the held-out state's own record;
- ``pa``: Pennsylvania's record alone;
- ``pa_blend``: the current rule (own record blended toward Pennsylvania's);
- ``national_states``: every other state's record, pooled;
- ``national_counties``: every county outside the state, pooled, not matched;
- ``donor``: the matched donor pool alone;
- ``donor_blend``: the current rule with the donor pool in place of Pennsylvania.

The donor rule, the held-out states and the decision rule are fixed in
``assumptions/validation_donor_pool.yaml`` before any score was computed. The
command ``visionhousing validate-donor-pool`` writes the results to
``assumptions/calibration/donor_pool_errors.yaml``; nothing here changes a
baseline run. The baseline functions are used read-only (the rule, its
back-test cases and its ratio quantiles come from
:mod:`visionhousing.baseline`).

:func:`confidence_signals` builds a small payload that keeps two questions
apart: how well the baseline did in past years for a state, and how much outside
evidence exists for each reform type.

Method in full: ``docs/modules/validation_donor_pool.md``.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    project_totals,
    ratio_quantiles,
)
from visionhousing.baseline.contest import contest_rules
from visionhousing.baseline.panel import STRUCTURE_TYPES, to_panel
from visionhousing.baseline.rules import Rule
from visionhousing.baseline.settings import BaselineSettings, settings_from_ledger
from visionhousing.data.sources import STATE_FIPS

__all__ = [
    "OUT_PATH",
    "SETTINGS_PATH",
    "AreaCases",
    "area_cases",
    "area_features",
    "build_document",
    "build_national_panel",
    "confidence_signals",
    "decide",
    "decide_pooled_states",
    "fetch_national",
    "load_settings",
    "method_ends",
    "pooled_states_study",
    "preregistration_sha256",
    "production_errors",
    "rule_and_settings",
    "run_study",
    "score_holdouts",
    "select_donors",
    "select_fresh_states",
    "state_features_at",
    "state_sizes",
    "state_table",
    "summarise",
    "write_document",
]

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "assumptions" / "validation_donor_pool.yaml"
OUT_PATH = ROOT / "assumptions" / "calibration" / "donor_pool_errors.yaml"
BACKTEST_PATH = ROOT / "assumptions" / "backtest.yaml"
PANEL_FILE = "county_year_panel.csv.gz"
FIPS_TO_STATE = {v: k for k, v in STATE_FIPS.items()}
PREREG_KEY = "pooled_states_test"
RULES_BLOCK = "donor_pool_rules"
"""The first test's fixed rules, by name in ``assumptions/preregistration.yaml``."""

HEADER = """\
# Calibration (validation): donor-pool error record for sparse states (generated; do not edit).
# Written by `visionhousing validate-donor-pool` from the Census Building Permits
# Survey county files of every state. Hold-out-state back-tests of the baseline's
# state range, the pre-stated decision, and ratio quantiles a sparse state could
# borrow. The baseline reads none of it unless the decision says "adopt" and the
# engine is wired to it. Method: docs/modules/validation_donor_pool.md.
"""


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Read ``assumptions/validation_donor_pool.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- data


def fetch_national(
    raw: Path,
    years: Iterable[int],
    *,
    refresh: bool = False,
    client: Any = None,
    manifest: Path | None = None,
) -> list[Path]:
    """Download (or reuse) every annual county file into ``raw/bps_national/``.

    The national files are the same ``co{YYYY}a.txt`` files a state pack reads
    (source ``bps_county_annual``); when the pack's cache ``raw/bps/`` already
    holds a year's file it is copied instead of downloaded again.

    Returns:
        The file paths, one per year, ascending.
    """
    from visionhousing.data import manifest as mf
    from visionhousing.data.registry import get_source

    folder = Path(raw) / "bps_national"
    folder.mkdir(parents=True, exist_ok=True)
    source = get_source("bps_county_annual")
    out = []
    for year in sorted(set(int(y) for y in years)):
        dest = folder / f"co{year}a.txt"
        if refresh or not dest.exists():
            cached = Path(raw) / "bps" / dest.name
            if cached.exists() and not refresh:
                shutil.copyfile(cached, dest)
            else:
                if client is None:
                    from visionhousing.data.http import HttpClient

                    client = HttpClient()
                url = source.url(year=year)
                client.download(url, dest)
                path = manifest if manifest is not None else mf.MANIFEST_PATH
                mf.record(mf.entry_for_file(source.id, url, dest, str(year), root=mf.ROOT), path)
        out.append(dest)
    return out


def _read_year(path: Path, year: int, fips: set[str]) -> pd.DataFrame:
    """One county file -> one row per county with units by type, reported units and the flag."""
    from visionhousing.data.sources import bps

    wide = bps.parse_county_annual(Path(path).read_text(encoding="latin-1"), year)
    wide = wide[wide["state_fips"].isin(fips)]
    tidy = bps.tidy(wide)
    permits = bps.permits_county_year(tidy)
    reported = tidy.groupby("geoid")["units_reported"].sum().rename("units_reported")
    permits = permits.merge(reported, left_on="county", right_index=True, how="left")
    return permits


def build_national_panel(
    paths: Sequence[Path], settings: Mapping[str, Any], cache: Path | None = None
) -> pd.DataFrame:
    """Read the yearly county files one at a time into one county-year table.

    Columns: ``county`` (5-digit code), ``state`` (postal code), ``year``, the four
    structure types, ``units_reported`` (units reported by permit offices) and
    ``imputed`` (1 when any part of the county-year was imputed by the Census
    Bureau). Only the states in ``settings["states"]`` are kept. When ``cache`` is
    given the table is saved there (gzip CSV) and read back on the next call.
    """
    if cache is not None and Path(cache).exists():
        table = pd.read_csv(cache, dtype={"county": str, "state": str})
        return table
    fips = {STATE_FIPS[s] for s in settings["states"]}
    parts = []
    for path in paths:
        year = int(Path(path).name[2:6])
        parts.append(_read_year(Path(path), year, fips))
    table = pd.concat(parts, ignore_index=True)
    table["state"] = table["county"].str[:2].map(FIPS_TO_STATE)
    cols = ["county", "state", "year", *STRUCTURE_TYPES, "units_reported", "imputed"]
    table = table[cols].sort_values(["county", "year"]).reset_index(drop=True)
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(cache, index=False, compression="gzip")
    return table


def state_table(panel: pd.DataFrame, state: str) -> pd.DataFrame:
    """The pack-format permit table (``county, year, units_*, imputed``) of one state."""
    return panel[panel["state"] == state].drop(columns=["state", "units_reported"])


# --------------------------------------------------------------------------- cases


@dataclass(frozen=True)
class AreaCases:
    """Back-test cases of one rule, for every county and every state total.

    Attributes:
        counties: One row per county and origin: ``area``, ``state``, ``origin``,
            ``projected``, ``actual``, ``ratio`` and the features ``size``,
            ``growth``, ``completeness`` at that origin.
        states: The same for each state's yearly total (counties added up).
        origins: The origins tested.
        lead: Years from an origin to its first target year.
        horizon: Target years per case.
    """

    counties: pd.DataFrame
    states: pd.DataFrame
    origins: tuple[int, ...]
    lead: int
    horizon: int

    def completed_by(self, origin: int) -> int:
        """Latest past origin whose five target years were all observed at ``origin``."""
        return origin - self.lead - self.horizon + 1


def area_features(
    totals: np.ndarray,
    reported: np.ndarray,
    years: np.ndarray,
    origins: Sequence[int],
    donor: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    """Size, growth and completeness of each area at each origin, from years up to it.

    Args:
        totals: Units (all structure types), shape (areas, years); ``NaN`` = not reported.
        reported: Units reported by permit offices, same shape.
        years: Calendar years, shape (years,).
        origins: Origins, shape (O,).
        donor: The ``donor`` block of the settings.

    Returns:
        ``size``, ``growth`` and ``completeness``, each shape (areas, O).
    """
    n_size = int(donor["size_years"])
    n_recent, n_base = int(donor["growth_recent_years"]), int(donor["growth_base_years"])
    n_comp = int(donor["completeness_years"])
    first = int(years[0])
    shape = (totals.shape[0], len(origins))
    size, growth, comp = (np.full(shape, np.nan) for _ in range(3))

    def window_mean(i_end: int, n: int) -> np.ndarray:
        block = totals[:, max(0, i_end - n + 1) : i_end + 1]
        count = (~np.isnan(block)).sum(axis=1)
        out = np.full(block.shape[0], np.nan)
        np.divide(np.nansum(block, axis=1), count, out=out, where=count > 0)
        return out

    for j, origin in enumerate(origins):
        i = int(origin) - first
        size[:, j] = window_mean(i, n_size)
        recent = window_mean(i, n_recent)
        base = window_mean(i - n_recent, n_base)
        growth[:, j] = np.log((recent + 1.0) / (base + 1.0))
        lo = max(0, i - n_comp + 1)
        t = np.nansum(totals[:, lo : i + 1], axis=1)
        r = np.nansum(
            np.where(np.isnan(totals[:, lo : i + 1]), 0.0, reported[:, lo : i + 1]), axis=1
        )
        c = np.full(t.shape, np.nan)
        np.divide(r, t, out=c, where=t > 0)
        comp[:, j] = np.clip(c, 0.0, 1.0)
    return {"size": size, "growth": growth, "completeness": comp}


def _cases_with_features(
    rule: Rule,
    years: np.ndarray,
    units: np.ndarray,
    reported: np.ndarray,
    origins: Sequence[int],
    lead: int,
    horizon: int,
    donor: Mapping[str, Any],
    names: Sequence[str],
    states: Sequence[str],
) -> pd.DataFrame:
    cases = backtest_cases(rule, years, units, origins, lead, horizon)
    present = ~np.isnan(units).all(axis=2)
    totals = np.where(present, np.nansum(units, axis=2), np.nan)
    feats = area_features(totals, reported, years, origins, donor)
    col = {int(o): j for j, o in enumerate(origins)}
    p = cases["place"].to_numpy()
    j = cases["origin"].map(col).to_numpy()
    out = cases.assign(
        area=np.asarray(names, dtype=object)[p],
        state=np.asarray(states, dtype=object)[p],
        size=feats["size"][p, j],
        growth=feats["growth"][p, j],
        completeness=feats["completeness"][p, j],
    )
    return out.drop(columns="place")[
        [
            "area",
            "state",
            "origin",
            "projected",
            "actual",
            "ratio",
            "size",
            "growth",
            "completeness",
        ]
    ]


def area_cases(
    panel: pd.DataFrame,
    rule: Rule,
    bsettings: BaselineSettings,
    settings: Mapping[str, Any],
) -> AreaCases:
    """Back-test ``rule`` on every county and on every state total of the national panel.

    The lead is the baseline's: first projected year minus the panel's last year.
    Origins are the baseline's (``backtest_origins``), so every case here is a
    case the baseline's own back-test would make.
    """
    years_all = np.arange(int(panel["year"].min()), int(panel["year"].max()) + 1)
    lead = bsettings.first_year - int(years_all[-1])
    if lead < 1:
        raise ValueError("the panel already reaches the first projected year")
    origins = tuple(backtest_origins(years_all, lead, bsettings.horizon, bsettings.min_history))
    donor = settings["donor"]
    p = to_panel(panel)
    rep = (
        panel.assign(county=panel["county"].astype(str))
        .pivot_table(index="county", columns="year", values="units_reported", aggfunc="sum")
        .reindex(index=list(p.counties), columns=list(p.years))
        .to_numpy(dtype=float)
    )
    state_of = panel.drop_duplicates("county").set_index("county")["state"]
    c_states = [str(state_of[c]) for c in p.counties]
    counties = _cases_with_features(
        rule, p.years, p.units, rep, origins, lead, bsettings.horizon, donor, p.counties, c_states
    )
    st_names = sorted(set(c_states))
    s_units = np.full((len(st_names), len(p.years), len(STRUCTURE_TYPES)), np.nan)
    s_rep = np.full((len(st_names), len(p.years)), np.nan)
    c_arr = np.asarray(c_states)
    for k, st in enumerate(st_names):
        mine = c_arr == st
        block = p.units[mine]
        present = ~np.isnan(block).all(axis=0)
        s_units[k] = np.where(present, np.nansum(block, axis=0), np.nan)
        rep_block = np.where(np.isnan(block).all(axis=2), np.nan, rep[mine])
        s_rep[k] = np.nansum(np.nan_to_num(rep_block), axis=0)
    states = _cases_with_features(
        rule, p.years, s_units, s_rep, origins, lead, bsettings.horizon, donor, st_names, st_names
    )
    return AreaCases(counties, states, origins, lead, bsettings.horizon)


# --------------------------------------------------------------------------- methods


def select_donors(
    candidates: pd.DataFrame,
    target: Mapping[str, float],
    donor: Mapping[str, Any],
) -> pd.DataFrame:
    """Pick the donor cases nearest to the target's features (the rule in the settings).

    Args:
        candidates: County cases (``area``, ``origin``, ``ratio``, features),
            already restricted to other states and to the allowed origins.
        target: The target's ``size``, ``growth`` and ``completeness``.
        donor: The ``donor`` block of the settings.

    Returns:
        The chosen cases with an added ``distance`` column, nearest first.
    """
    w = donor["weights"]
    ok = candidates[
        np.isfinite(candidates["ratio"])
        & (candidates["size"] > 0)
        & np.isfinite(candidates["growth"])
        & (candidates["completeness"] >= float(donor["min_completeness"]))
    ]
    if ok.empty or not target["size"] > 0:
        return ok.assign(distance=pd.Series(dtype=float))
    dist = (
        float(w["log_size"]) * np.abs(np.log(ok["size"].to_numpy()) - math.log(target["size"]))
        + float(w["growth"]) * np.abs(ok["growth"].to_numpy() - target["growth"])
        + float(w["completeness"]) * np.abs(ok["completeness"].to_numpy() - target["completeness"])
    )
    ranked = ok.assign(distance=dist).sort_values(["distance", "area", "origin"], kind="mergesort")
    ranked = ranked[ranked.groupby("area").cumcount() < int(donor["max_cases_per_county"])]
    return ranked.head(int(donor["k"]))


def _ends(ratios: np.ndarray) -> tuple[float, float, float] | None:
    """(log P10/P50, log P90/P50, P50) of the ratios, or ``None`` if unusable."""
    r = np.asarray(ratios, dtype=float)
    r = r[np.isfinite(r) & (r > 0)]
    q = ratio_quantiles(r, (0.1, 0.5, 0.9))
    if q is None or not q[1] > 0 or not q[0] > 0:
        return None
    return math.log(q[0] / q[1]), math.log(q[2] / q[1]), float(q[1])


def _blend(own: tuple[float, float, float], prior: tuple[float, float, float], w: float):
    """The small-sample rule: blend each end toward the prior on the log scale, only widen."""
    low = min(own[0], w * own[0] + (1 - w) * prior[0])
    high = max(own[1], w * own[1] + (1 - w) * prior[1])
    return low, high, own[2]


def method_ends(
    cases: AreaCases,
    state: str,
    origin: int,
    variant: str,
    settings: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Every method's range ends for one held-out state and origin.

    Returns:
        ``None`` when some method has fewer than ``min_cases`` ratios; otherwise
        ``{"ends": {method: (low, high, p50_ratio)}, "pool": {method: n}, "w": w}``,
        where ``low`` and ``high`` are log distances of P10 and P90 from the P50.
    """
    if variant == "out_of_time":
        allowed_s = cases.states["origin"] <= cases.completed_by(origin)
        allowed_c = cases.counties["origin"] <= cases.completed_by(origin)
    elif variant == "other_origins":
        # In sample in time (later origins too), but never a case whose five target
        # years overlap the scored case's: |origin' - origin| >= horizon.
        allowed_s = (cases.states["origin"] - origin).abs() >= cases.horizon
        allowed_c = (cases.counties["origin"] - origin).abs() >= cases.horizon
    else:
        raise ValueError(f"unknown variant {variant!r}")
    st, co = cases.states[allowed_s], cases.counties[allowed_c]
    prior = str(settings["prior_state"])
    own_c = co[(co["state"] == state) & np.isfinite(co["ratio"])]
    n_county = len(own_c)
    w = min(1.0, n_county / float(settings["full_weight_cases"]))
    target = cases.states[(cases.states["area"] == state) & (cases.states["origin"] == origin)]
    if target.empty:
        return None
    t = target.iloc[0]
    donors = select_donors(
        co[co["state"] != state],
        {"size": t["size"], "growth": t["growth"], "completeness": t["completeness"]},
        settings["donor"],
    )
    pools = {
        "own": st[st["state"] == state]["ratio"].to_numpy(),
        "pa": st[st["state"] == prior]["ratio"].to_numpy(),
        "national_states": st[st["state"] != state]["ratio"].to_numpy(),
        "national_counties": co[co["state"] != state]["ratio"].to_numpy(),
        "donor": donors["ratio"].to_numpy(),
    }
    min_cases = int(settings["min_cases"])
    ends: dict[str, Any] = {}
    sizes: dict[str, int] = {}
    for name, ratios in pools.items():
        finite = ratios[np.isfinite(ratios) & (ratios > 0)]
        e = _ends(finite)
        if e is None or finite.size < min_cases:
            return None
        ends[name], sizes[name] = e, int(finite.size)
    ends["pa_blend"] = _blend(ends["own"], ends["pa"], w)
    ends["donor_blend"] = _blend(ends["own"], ends["donor"], w)
    ends["states_blend"] = _blend(ends["own"], ends["national_states"], w)
    sizes["pa_blend"], sizes["donor_blend"] = sizes["own"], sizes["own"]
    sizes["states_blend"] = sizes["own"]
    wanted = list(settings["methods"])
    return {
        "ends": {m: ends[m] for m in wanted},
        "pool": {m: sizes[m] for m in wanted},
        "w": w,
        "county_cases": n_county,
    }


def score_holdouts(cases: AreaCases, settings: Mapping[str, Any]) -> pd.DataFrame:
    """Score every method on every held-out state's origins, in both variants.

    Returns:
        One row per variant, state, origin and method: ``projected``, ``actual``,
        ``low`` and ``high`` (P10 and P90 of the range), ``inside``, ``width``
        ((P90 - P10) / P50), ``abs_log_error`` (|log(actual / P50)|, the same for
        every method), ``abs_log_error_corrected`` (with P50 x the method's
        median ratio), ``pool`` (ratios used) and ``w``.
    """
    rows = []
    for variant in settings["variants"]:
        for state in settings["held_out"]:
            mine = cases.states[cases.states["area"] == state]
            for _, case in mine.sort_values("origin").iterrows():
                if not (case["projected"] > 0 and case["actual"] > 0):
                    continue
                got = method_ends(cases, state, int(case["origin"]), variant, settings)
                if got is None:
                    continue
                proj, act = float(case["projected"]), float(case["actual"])
                for m, (low, high, mid) in got["ends"].items():
                    lo, hi = proj * math.exp(low), proj * math.exp(high)
                    rows.append(
                        {
                            "variant": variant,
                            "state": state,
                            "origin": int(case["origin"]),
                            "method": m,
                            "projected": proj,
                            "actual": act,
                            "low": lo,
                            "high": hi,
                            "inside": bool(lo <= act <= hi),
                            "width": math.exp(high) - math.exp(low),
                            "abs_log_error": abs(math.log(act / proj)),
                            "abs_log_error_corrected": abs(math.log(act / (proj * mid))),
                            "pool": got["pool"][m],
                            "w": got["w"],
                        }
                    )
    return pd.DataFrame(rows)


def summarise(scored: pd.DataFrame) -> pd.DataFrame:
    """Coverage, width and error by variant, state and method (plus a pooled row per state set)."""
    if scored.empty:
        return pd.DataFrame()
    g = scored.groupby(["variant", "state", "method"], sort=False)
    out = g.agg(
        cases=("inside", "size"),
        coverage=("inside", "mean"),
        width=("width", "mean"),
        median_abs_log_error=("abs_log_error", "median"),
        median_abs_log_error_corrected=("abs_log_error_corrected", "median"),
        median_pool=("pool", "median"),
        w=("w", "median"),
        first_origin=("origin", "min"),
        last_origin=("origin", "max"),
    ).reset_index()
    return out


def decide(summary: pd.DataFrame, sparse: Iterable[str], settings: Mapping[str, Any]) -> dict:
    """Apply the pre-stated decision rule (``decision`` in the settings)."""
    variant = settings["primary_variant"]
    target = float(settings["target_coverage"])
    band_hi = float(settings["coverage_band"][1])
    cand, cur = settings["decision"]["candidate"], settings["decision"]["current"]
    sparse = [s for s in settings["held_out"] if s in set(sparse)]
    rows = summary[(summary["variant"] == variant) & summary["state"].isin(sparse)]

    def stats(m: str) -> dict[str, float]:
        r = rows[rows["method"] == m]
        if r.empty:
            return {"mean_gap": float("nan"), "coverage": float("nan"), "width": float("nan")}
        n = r["cases"].to_numpy(dtype=float)
        return {
            "mean_gap": float(np.mean(np.abs(r["coverage"] - target))),
            "coverage": float(np.sum(r["coverage"] * n) / n.sum()),
            "width": float(np.sum(r["width"] * n) / n.sum()),
            "states": len(r),
            "cases": int(n.sum()),
        }

    c, k = stats(cand), stats(cur)
    closer = bool(c["mean_gap"] < k["mean_gap"])
    wider = bool(c["width"] > k["width"])
    too_wide = bool(wider and c["coverage"] > band_hi)
    adopt = closer and not too_wide
    if not sparse or rows.empty:
        adopt, reason = False, "no sparse held-out state could be scored; the current rule stays"
    elif adopt:
        reason = (
            f"{cand} coverage is closer to {target:.0%} on average across {c['states']} sparse "
            f"held-out states (mean gap {c['mean_gap']:.3f} vs {k['mean_gap']:.3f})"
            + (
                f" and its wider ranges are needed (pooled coverage {c['coverage']:.1%} is not "
                f"above {band_hi:.0%})"
                if wider
                else f" with ranges no wider (mean width {c['width']:.2f} vs {k['width']:.2f})"
            )
        )
    elif not closer:
        reason = (
            f"{cand} coverage is not closer to {target:.0%} than {cur} (mean gap "
            f"{c['mean_gap']:.3f} vs {k['mean_gap']:.3f}); the current widening stays"
        )
    else:
        reason = (
            f"{cand} is closer to {target:.0%} but wider than needed (mean width "
            f"{c['width']:.2f} vs {k['width']:.2f}, pooled coverage {c['coverage']:.1%} above "
            f"{band_hi:.0%}); the current widening stays"
        )
    return {
        "outcome": "adopt" if adopt else "keep_current",
        "variant": variant,
        "decision_states": sparse,
        "candidate": {"method": cand, **c},
        "current": {"method": cur, **k},
        "closer_to_target": closer,
        "wider_than_current": wider,
        "wider_than_needed": too_wide,
        "reason": reason,
    }


def _state_case_counts(cases: AreaCases) -> dict[str, int]:
    co = cases.counties[np.isfinite(cases.counties["ratio"])]
    return {str(k): int(v) for k, v in co.groupby("state").size().items()}


def production_errors(
    cases: AreaCases,
    settings: Mapping[str, Any],
    last_year: int,
    features: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, Any]]:
    """Ratio quantiles a sparse state would borrow from its donor pool in a real run.

    The target's features are measured at ``last_year`` (the last data year, the
    origin of a real run); donors are every completed county case outside the
    state (origin <= last_year - lead - horizon + 1), chosen by the same rule.
    ``features`` are the state totals' features at ``last_year``
    (:func:`state_features_at`): the last year is not a back-test origin, since
    its target years are still to come.
    """
    counts = _state_case_counts(cases)
    full = float(settings["full_weight_cases"])
    qs = [float(q) for q in settings["quantiles"]]
    co = cases.counties[cases.counties["origin"] <= cases.completed_by(last_year)]
    out: dict[str, dict[str, Any]] = {}
    for state in sorted(counts):
        n = counts[state]
        if n >= full:
            continue
        feat = features.get(state)
        if feat is None:
            continue
        donors = select_donors(co[co["state"] != state], feat, settings["donor"])
        q = ratio_quantiles(donors["ratio"].to_numpy(), qs)
        own = cases.states[cases.states["area"] == state]["ratio"].to_numpy()
        q_own = ratio_quantiles(own[np.isfinite(own)], qs)
        out[state] = {
            "county_cases": n,
            "weight_own": round(min(1.0, n / full), 4),
            "features_at": last_year,
            "size_units_per_year": round(float(feat["size"]), 1),
            "growth_log": round(float(feat["growth"]), 4),
            "completeness": round(float(feat["completeness"]), 4),
            "donor_cases": len(donors),
            "donor_counties": int(donors["area"].nunique()) if len(donors) else 0,
            "donor_states": int(donors["state"].nunique()) if len(donors) else 0,
            "donor_origins": [int(donors["origin"].min()), int(donors["origin"].max())]
            if len(donors)
            else None,
            "ratio_quantiles": {q: round(float(v), 4) for q, v in zip(qs, q, strict=True)}
            if q is not None
            else None,
            "own_state_ratio_quantiles": {
                q: round(float(v), 4) for q, v in zip(qs, q_own, strict=True)
            }
            if q_own is not None
            else None,
        }
    return out


def state_features_at(
    panel: pd.DataFrame, year: int, settings: Mapping[str, Any]
) -> dict[str, dict[str, float]]:
    """Size, growth and completeness of every state total at ``year``."""
    years = np.arange(int(panel["year"].min()), int(panel["year"].max()) + 1)
    tot = panel.assign(total=panel[list(STRUCTURE_TYPES)].sum(axis=1, min_count=1))
    by = tot.groupby(["state", "year"]).agg(
        total=("total", lambda s: s.sum(min_count=1)), reported=("units_reported", "sum")
    )
    states = sorted(tot["state"].unique())
    t = by["total"].unstack("year").reindex(index=states, columns=years).to_numpy(dtype=float)
    r = by["reported"].unstack("year").reindex(index=states, columns=years).to_numpy(dtype=float)
    f = area_features(t, r, years, [int(year)], settings["donor"])
    return {
        s: {k: float(f[k][i, 0]) for k in ("size", "growth", "completeness")}
        for i, s in enumerate(states)
    }


# --------------------------------------------------------------------------- second test


def preregistration_sha256(block: Mapping[str, Any]) -> str:
    """Fingerprint of a pre-registered settings block.

    SHA-256 of the block as canonical JSON (keys sorted, no spaces), so comments
    and layout in the YAML file do not count but any change to a value does.
    The expected values live in ``assumptions/preregistration.yaml``
    (:mod:`visionhousing.validation.preregistration`).
    """
    from visionhousing.validation.preregistration import fingerprint

    return fingerprint(block)


def state_sizes(panel: pd.DataFrame) -> dict[str, float]:
    """Mean yearly units permitted (state total, all structure types) for every state.

    A state-year counts when at least one of its counties reported a figure.
    """
    tot = panel.assign(total=panel[list(STRUCTURE_TYPES)].sum(axis=1, min_count=1))
    by = tot.groupby(["state", "year"])["total"].sum(min_count=1)
    return {str(s): float(v) for s, v in by.groupby("state").mean().items()}


def select_fresh_states(
    counts: Mapping[str, int], sizes: Mapping[str, float], settings: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply the pre-stated rule that picks the second test's held-out states.

    Args:
        counts: Usable county-origin cases per state (whole record).
        sizes: Mean yearly units permitted per state (:func:`state_sizes`).
        settings: The study settings (``states``, ``held_out``,
            ``full_weight_cases`` and the ``pooled_states_test`` block).

    Returns:
        ``{"states": [...chosen, smallest first...], "eligible": [{state, size,
        county_cases}, ...ranked...], "dropped": {reason: [states]}}``. The result
        depends only on the inputs, never on dictionary order.
    """
    test = settings[PREREG_KEY]
    full = float(settings["full_weight_cases"])
    first = set(settings["held_out"])
    exclude = set(test["exclude"])
    dropped: dict[str, list[str]] = {"first_test": [], "excluded": [], "not_sparse": []}
    eligible = []
    for st in sorted(settings["states"]):
        if st in first:
            dropped["first_test"].append(st)
        elif st in exclude:
            dropped["excluded"].append(st)
        elif st not in counts or counts[st] >= full or st not in sizes:
            dropped["not_sparse"].append(st)
        else:
            eligible.append({"state": st, "size": float(sizes[st]), "county_cases": counts[st]})
    eligible.sort(key=lambda r: (r["size"], r["state"]))
    n = int(test["n_states"])
    return {"states": [r["state"] for r in eligible[:n]], "eligible": eligible, "dropped": dropped}


def decide_pooled_states(
    summary: pd.DataFrame, states: Sequence[str], settings: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply the second test's pre-stated decision rule (``pooled_states_test``)."""
    test = settings[PREREG_KEY]
    target = float(settings["target_coverage"])
    band = [float(x) for x in settings["coverage_band"]]
    cand, cur = test["candidate"], test["current"]
    nd = int(test["gap_decimals"])
    margin = float(test["width_margin"])
    rows = summary[(summary["variant"] == test["variant"]) & summary["state"].isin(states)]

    def stats(m: str) -> dict[str, Any]:
        r = rows[rows["method"] == m]
        if r.empty:
            return {"states": 0, "cases": 0, "mean_gap": None, "coverage": None, "width": None}
        n = r["cases"].to_numpy(dtype=float)
        return {
            "states": len(r),
            "cases": int(n.sum()),
            "mean_gap": float(np.mean(np.abs(r["coverage"] - target))),
            "coverage": float(np.sum(r["coverage"] * n) / n.sum()),
            "width": float(np.sum(r["width"] * n) / n.sum()),
        }

    c, k = stats(cand), stats(cur)
    table = {m: stats(m) for m in [cur, cand, *test["context_methods"]]}
    scored = int(c["states"])
    if scored < int(test["min_states_scored"]):
        return {
            "outcome": "inconclusive",
            "states_scored": scored,
            "methods": table,
            "reason": f"only {scored} fresh states could be scored; the current widening stays",
        }
    gc, gk = round(c["mean_gap"], nd), round(k["mean_gap"], nd)
    tie = gc == gk
    beats = gc < gk or (tie and c["width"] < k["width"])
    much_wider = c["width"] > (1 + margin) * k["width"]
    in_band = band[0] <= c["coverage"] <= band[1]
    too_wide = much_wider and not in_band
    adopt = beats and not too_wide
    if adopt:
        reason = (
            f"{cand} beats {cur} on the fresh states (mean gap {gc:.3f} vs {gk:.3f}"
            + (", tie broken by narrower ranges" if tie else "")
            + ") and is not wider than needed"
        )
    elif not beats:
        reason = (
            f"{cand} does not beat {cur} on the fresh states (mean gap {gc:.3f} vs {gk:.3f}"
            + (f"; tie, not narrower: width {c['width']:.2f} vs {k['width']:.2f}" if tie else "")
            + "); the current widening stays"
        )
    else:
        reason = (
            f"{cand} is closer to {target:.0%} (mean gap {gc:.3f} vs {gk:.3f}) but more than "
            f"{margin:.0%} wider (width {c['width']:.2f} vs {k['width']:.2f}) with pooled coverage "
            f"{c['coverage']:.1%} outside {band[0]:.0%}-{band[1]:.0%}; the current widening stays"
        )
        if c["coverage"] < band[0]:
            reason += (
                " (note: coverage is below the band, so the extra width was not more than needed; "
                "the clause as stated rejects it all the same, and the outcome follows the clause)"
            )
    return {
        "outcome": "adopt" if adopt else "keep_current",
        "states_scored": scored,
        "gap_tie": bool(tie),
        "beats_current": bool(beats),
        "wider_than_margin": bool(much_wider),
        "coverage_in_band": bool(in_band),
        "coverage_below_band": bool(c["coverage"] < band[0]),
        "methods": table,
        "reason": reason,
    }


def _state_projection(
    panel: pd.DataFrame, state: str, rule: Rule, origin: int, lead: int, horizon: int
) -> float:
    """The rule's five-year projection of one state's total from ``origin``."""
    p = to_panel(state_table(panel, state))
    present = ~np.isnan(p.units).all(axis=0)
    units = np.where(present, np.nansum(p.units, axis=0), np.nan)[None]
    targets = np.arange(origin + lead, origin + lead + horizon)
    by_type, _, _ = project_totals(rule, p.years, units, origin, targets)
    return float(by_type.sum())


def _implied_range(
    cases: AreaCases,
    panel: pd.DataFrame,
    state: str,
    rule: Rule,
    counts: Mapping[str, int],
    settings: Mapping[str, Any],
) -> dict[str, Any] | None:
    """One state's likely range for a real run, under the current rule and the candidate.

    Uses every case completed by the last data year, the state's own record
    blended toward Pennsylvania's (current) or toward every other state's
    (candidate) with ``w = min(1, county cases / full_weight_cases)``.
    """
    last = int(panel["year"].max())
    st = cases.states[
        (cases.states["origin"] <= cases.completed_by(last)) & np.isfinite(cases.states["ratio"])
    ]
    own = _ends(st[st["area"] == state]["ratio"].to_numpy())
    pa = _ends(st[st["area"] == settings["prior_state"]]["ratio"].to_numpy())
    pool = _ends(st[st["area"] != state]["ratio"].to_numpy())
    if own is None or pa is None or pool is None or state not in counts:
        return None
    w = min(1.0, counts[state] / float(settings["full_weight_cases"]))
    p50 = _state_projection(panel, state, rule, last, cases.lead, cases.horizon)
    first = last + cases.lead
    out: dict[str, Any] = {
        "state": state,
        "years": [first, first + cases.horizon - 1],
        "origin": last,
        "p50_units": round(p50),
        "weight_own": round(w, 4),
        "note": (
            "P50 is the rule's projection of the state total from the national county panel; "
            "the range is P50 x P10/P50 to P50 x P90/P50 of the blended error record."
        ),
    }
    for name, ends in (
        ("own", own),
        ("pa_blend", _blend(own, pa, w)),
        ("states_blend", _blend(own, pool, w)),
    ):
        lo, hi = math.exp(ends[0]), math.exp(ends[1])
        out[name] = {
            "low_ratio": round(lo, 4),
            "high_ratio": round(hi, 4),
            "low_units": round(p50 * lo),
            "high_units": round(p50 * hi),
            "width": round(hi - lo, 4),
        }
    return out


def pooled_states_study(
    cases: AreaCases,
    panel: pd.DataFrame,
    counts: Mapping[str, int],
    settings: Mapping[str, Any],
    rule: Rule,
) -> dict[str, Any]:
    """The second test: pooled state totals for sparse states, on fresh held-out states.

    Picks the fresh states by the pre-stated rule, scores the current rule, the
    candidate and the context methods on them, applies the pre-stated decision,
    and gives the example state's implied range. The block's fingerprint
    (:func:`preregistration_sha256`) goes into the result.
    """
    test = settings[PREREG_KEY]
    chosen = select_fresh_states(counts, state_sizes(panel), settings)
    methods = [test["current"], test["candidate"], *test["context_methods"]]
    sub = {
        **settings,
        "held_out": chosen["states"],
        "variants": [test["variant"]],
        "methods": list(dict.fromkeys(methods)),
    }
    summary = summarise(score_holdouts(cases, sub))
    decision = decide_pooled_states(summary, chosen["states"], settings)
    holdout = {
        s: _records(summary[summary["state"] == s])
        for s in chosen["states"]
        if not summary.empty and s in set(summary["state"])
    }
    example = settings.get("range_example_state")
    return {
        "preregistration": {
            "block": PREREG_KEY,
            "sha256": preregistration_sha256(test),
            "fingerprint": "SHA-256 of the block as JSON, keys sorted, separators (',', ':')",
        },
        "fresh_states": chosen["states"],
        "selection": {
            "eligible": [{**r, "size": round(r["size"], 1)} for r in chosen["eligible"]],
            "dropped": chosen["dropped"],
        },
        "decision": decision,
        "holdout": holdout,
        "implied_range": _implied_range(cases, panel, example, rule, counts, settings)
        if example
        else None,
    }


# --------------------------------------------------------------------------- confidence


def confidence_signals(
    state: str,
    *,
    donor_doc: Mapping[str, Any] | None = None,
    evidence: Any = None,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Two separate confidence signals for one state, for the interface.

    ``baseline_performance`` says how the baseline's ranges did in past years
    (from the hold-out back-test in ``donor_doc``, the generated
    ``donor_pool_errors.yaml``); ``reform_evidence`` says, per reform type, how
    much outside evidence exists (from the cross-state comparison records,
    ``evidence`` = :func:`visionhousing.evidence.records.load_panel`'s result).
    They are never combined into one score: a state can have a well-tested
    baseline and little evidence for a reform, or the reverse.

    Args:
        state: Postal code, for example ``"VT"``.
        donor_doc: The generated results; read from disk when ``None``.
        evidence: The evidence panel; loaded when ``None``.
        settings: The study settings; read from disk when ``None``.

    Returns:
        ``{"state", "baseline_performance", "reform_evidence", "note"}``.
    """
    settings = settings or load_settings()
    if donor_doc is None:
        donor_doc = (
            yaml.safe_load(OUT_PATH.read_text(encoding="utf-8")) if OUT_PATH.exists() else {}
        )
    if evidence is None:
        from visionhousing.evidence.records import load_panel

        evidence = load_panel()
    return {
        "state": state,
        "baseline_performance": _baseline_signal(state, donor_doc or {}, settings),
        "reform_evidence": _evidence_signal(state, evidence, settings),
        "note": (
            "Two separate signals. Baseline performance: how often the baseline's past ranges "
            "caught what was really permitted. Reform evidence: how much outside evidence exists "
            "for each reform type. One does not raise or lower the other."
        ),
    }


def _baseline_signal(
    state: str, doc: Mapping[str, Any], settings: Mapping[str, Any]
) -> dict[str, Any]:
    band = [float(x) for x in settings["coverage_band"]]
    decision = (doc.get("decision") or {}).get("outcome", "keep_current")
    counts = doc.get("county_cases") or {}
    n = counts.get(state)
    full = float(settings["full_weight_cases"])
    w = None if n is None else min(1.0, n / full)
    if w is None:
        source = "unknown"
    elif w >= 1:
        source = "own"
    else:
        source = "donor_pool" if decision == "adopt" else "pennsylvania_prior"
    method = {"own": "own", "donor_pool": "donor_blend", "pennsylvania_prior": "pa_blend"}.get(
        source
    )
    variant = settings["primary_variant"]
    row = next(
        (
            r
            for r in doc.get("holdout", {}).get(variant, {}).get(state, [])
            if r.get("method") == method
        ),
        None,
    )
    coverage = None if row is None else row.get("coverage")
    cases = None if row is None else row.get("cases")
    if source == "unknown":
        grade = "limited"
    elif coverage is not None and band[0] <= coverage <= band[1] and source == "own":
        grade = "back-tested"
    elif source != "own":
        grade = "borrowed"
    else:
        grade = "limited"
    if coverage is None:
        sentence = (
            "This state was not in the hold-out test, so how well its range has done is not "
            "measured here."
            if source != "unknown"
            else "No back-test record for this state."
        )
    else:
        sentence = (
            f"In {cases} past start years, the state's likely range caught the real five-year "
            f"total {round(coverage * cases)} times (a range aims for about 8 in 10)."
        )
    if source == "pennsylvania_prior":
        sentence += (
            " Its range is widened toward Pennsylvania's error record because its own is short."
        )
    elif source == "donor_pool":
        sentence += (
            " Its range is widened toward the errors of comparable permit areas in other states "
            "because its own record is short."
        )
    return {
        "grade": grade,
        "error_source": source,
        "county_cases": n,
        "weight_own": None if w is None else round(w, 4),
        "held_out_coverage": coverage,
        "held_out_cases": cases,
        "method": method,
        "sentence": sentence,
        "source": "assumptions/calibration/donor_pool_errors.yaml",
    }


def _evidence_signal(state: str, panel: Any, settings: Mapping[str, Any]) -> dict[str, Any]:
    grades = settings["confidence"]["evidence_grades"]
    records = list(getattr(panel, "records", ()) or ())
    types = sorted({t for r in records for t in r.policy_types})
    out: dict[str, Any] = {}
    rank = {"causal": 3, "matched": 3, "descriptive": 2}
    for t in types:
        mine = [r for r in records if t in r.policy_types]
        verified = [r for r in mine if r.verified]
        observed = [r for r in verified if r.observed_result is not None]
        best = max((rank.get(r.identification, 0) for r in observed), default=0)
        if best == 3:
            key = next(r.identification for r in observed if rank.get(r.identification) == 3)
        elif best == 2:
            key = "descriptive"
        elif verified:
            key = "law_only"
        else:
            key = "none"
        out[t] = {
            "grade": grades[key],
            "records": len(mine),
            "verified": len(verified),
            "with_observed_result": len(observed),
            "in_state": sum(1 for r in mine if r.state == state),
            "record_ids": sorted(r.id for r in observed)[:10],
        }
    return {"by_reform_type": out, "source": "assumptions/evidence/states/"}


# --------------------------------------------------------------------------- document


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for r in frame.to_dict("records"):
        out.append(
            {
                k: (round(float(v), 4) if isinstance(v, (float, np.floating)) else v)
                for k, v in r.items()
                if k not in ("variant", "state")
            }
        )
    return out


def build_document(
    summary: pd.DataFrame,
    decision: Mapping[str, Any],
    production: Mapping[str, Any],
    counts: Mapping[str, int],
    settings: Mapping[str, Any],
    *,
    as_of: str,
    years: tuple[int, int],
    lead: int,
    horizon: int,
    origins: Sequence[int],
) -> dict[str, Any]:
    """The generated calibration document (results, decision, borrowable quantiles)."""
    from visionhousing.data.registry import get_source

    src = get_source(settings["source"])
    holdout: dict[str, Any] = {}
    for variant in settings["variants"]:
        part = summary[summary["variant"] == variant]
        holdout[variant] = {
            s: _records(part[part["state"] == s])
            for s in settings["held_out"]
            if s in set(part["state"])
        }
    pooled: dict[str, Any] = {}
    sparse = decision["decision_states"]
    for variant in settings["variants"]:
        part = summary[(summary["variant"] == variant) & summary["state"].isin(sparse)]
        rows = {}
        for m, r in part.groupby("method", sort=False):
            n = r["cases"].to_numpy(dtype=float)
            rows[m] = {
                "cases": int(n.sum()),
                "coverage": round(float(np.sum(r["coverage"] * n) / n.sum()), 4),
                "mean_gap": round(
                    float(np.mean(np.abs(r["coverage"] - settings["target_coverage"]))), 4
                ),
                "width": round(float(np.sum(r["width"] * n) / n.sum()), 4),
                "median_abs_log_error": round(float(r["median_abs_log_error"].median()), 4),
            }
        pooled[variant] = rows
    return {
        "version": 1,
        "id": f"donor_pool_errors_{as_of[:7].replace('-', '_')}",
        "label": "Donor-pool error record for sparse states (hold-out-state back-test)",
        "status": "validation",
        "use": (
            "Not read by the baseline. If decision.outcome is 'adopt', the engine may use "
            "sparse_states.<ST>.ratio_quantiles in place of the Pennsylvania prior of the "
            "small-sample widening (assumptions/backtest.yaml, small_sample)."
        ),
        "provenance": {
            "summary": (
                "Hold-out-state back-tests of the baseline's state range (rule "
                f"{settings['rule']}): for each held-out state and past origin, ranges from the "
                "state's own errors, Pennsylvania's, all other states', all other counties', and "
                "a matched pool of counties in other states, scored on coverage of the real "
                "five-year total; and donor-pool ratio quantiles for each sparse state."
            ),
            "measured_on": as_of,
            "method": (
                "National county panel from the BPS annual county files "
                f"{years[0]}-{years[1]} (settings in assumptions/validation_donor_pool.yaml); "
                "back-test cases from visionhousing.baseline (read-only); see "
                "docs/modules/validation_donor_pool.md."
            ),
            "generated_by": "visionhousing validate-donor-pool",
            "sources": [
                {
                    "id": src.id,
                    "citation": f"{src.publisher}, {src.name}, {years[0]}-{years[1]}",
                    "url": src.url_pattern,
                    "licence": src.licence,
                }
            ],
            "limits": [
                "Each held-out state is scored on about a dozen overlapping five-year windows, so "
                "one state's coverage moves in steps of several points.",
                "Origins share the national housing cycle; held-out states' errors move together.",
                "The 2003 county file reports no imputation (reported equals estimated), so "
                "completeness is overstated for windows holding 2003.",
            ],
        },
        "settings": {
            "rule": settings["rule"],
            "lead_years": lead,
            "horizon_years": horizon,
            "origins": [int(min(origins)), int(max(origins))],
            "donor": settings["donor"],
            "held_out": list(settings["held_out"]),
            "full_weight_cases": settings["full_weight_cases"],
            "primary_variant": settings["primary_variant"],
        },
        "decision": dict(decision),
        "pooled": pooled,
        "holdout": holdout,
        "county_cases": dict(sorted(counts.items())),
        "sparse_states": dict(production),
    }


class _Dumper(yaml.SafeDumper):
    pass


def write_document(doc: Mapping[str, Any], path: Path = OUT_PATH) -> Path:
    """Write the document as YAML with the generated-file header; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(dict(doc), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {(float(k) if isinstance(k, np.floating) else k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if not math.isfinite(f) else round(f, 4)
    return v


def run_study(
    panel: pd.DataFrame,
    settings: Mapping[str, Any],
    bsettings: BaselineSettings,
    rule: Rule,
    *,
    as_of: str | None = None,
    check_preregistration: bool = True,
) -> dict[str, Any]:
    """Run the whole study on a county-year panel and return the document.

    Before anything is scored, the study's fixed rules (``donor_pool_rules``
    and, when present, ``pooled_states_test``) are checked against the
    fingerprints in ``assumptions/preregistration.yaml``; a changed rule stops
    the run (:class:`~visionhousing.validation.preregistration.PreregistrationError`).
    ``check_preregistration=False`` is for synthetic test settings only.
    """
    checked = []
    if check_preregistration:
        from visionhousing.validation.preregistration import verify

        checked.append(verify(RULES_BLOCK, settings))
        if PREREG_KEY in settings:
            checked.append(verify(PREREG_KEY, settings))
    cases = area_cases(panel, rule, bsettings, settings)
    last = int(panel["year"].max())
    scored = score_holdouts(cases, settings)
    summary = summarise(scored)
    counts = _state_case_counts(cases)
    full = float(settings["full_weight_cases"])
    sparse = [s for s, n in counts.items() if n < full]
    decision = decide(summary, sparse, settings)
    production = production_errors(cases, settings, last, state_features_at(panel, last, settings))
    second = None
    if PREREG_KEY in settings:
        second = pooled_states_study(cases, panel, counts, settings, rule)
    doc = build_document(
        summary,
        decision,
        production,
        counts,
        settings,
        as_of=as_of or date.today().isoformat(),
        years=(int(panel["year"].min()), last),
        lead=cases.lead,
        horizon=cases.horizon,
        origins=cases.origins,
    )
    if second is not None:
        doc[PREREG_KEY] = second
    doc["preregistration_checks"] = checked
    return _clean(doc)


def rule_and_settings() -> tuple[Rule, BaselineSettings]:
    """The scored rule (by name, from the contest) and the baseline settings, from the ledger."""
    from visionhousing.ledger.loader import load_ledger

    settings = load_settings()
    ledger = load_ledger()
    rules = yaml.safe_load(BACKTEST_PATH.read_text(encoding="utf-8"))
    by_name = {r.name: r for r in contest_rules(ledger, rules)}
    return by_name[settings["rule"]], settings_from_ledger(ledger)
