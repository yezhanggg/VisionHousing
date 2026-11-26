"""California APR study: stage attrition, elapsed time, ADU rates and comparison records.

Plain-language summary
----------------------
This module turns the compact APR frames from
:mod:`visionhousing.validation.apr_data` into three generated files:

1. ``assumptions/evidence/california_apr.yaml``: comparison records (what
   changed, where, when, the outcome, its denominator and horizon, how strong
   the identification is, and the caveats), plus the stage tables behind them.
2. ``assumptions/calibration/apr_timing_donor_ca.yaml``: ranges for the time
   from application to building permit and from permit to completion, and the
   share of applications that reach each stage within one to five years. They
   are measured in California and are **transferred**: a Pennsylvania scenario
   may use them for a timing or attrition sensitivity, never as a fact about
   Pennsylvania.
3. ``assumptions/calibration/adu_rate_plausibility.yaml``: VisionHousing's
   Pennsylvania ADU result expressed as ADUs per 1,000 eligible lots per year,
   next to California's observed ADU permits per 1,000 one-unit detached homes.

Terms used here:

- **Stage**: application submitted (Table A), entitlement approved, building
  permit issued, completed (certificate of occupancy) (Table A2).
- **Project**: a jurisdiction, an assessor parcel number (digits only) and a
  unit category. Table A2 reports a project again each year it reaches a new
  stage, so its rows are joined on this key.
- **Attrition**: the share of applications that has *not* reached a later stage
  by a given time. One minus the "reached" share.
- **Right-censoring**: a project still waiting when the data end (31 December of
  the last reporting year). Its wait is at least the time seen so far.
- **Kaplan-Meier estimate**: the share that has reached a stage by each day,
  allowing for censored projects (see :func:`visionhousing.features.permit_timing.kaplan_meier`).
- **Identification quality**: ``descriptive`` (a before/after or a plain
  comparison), ``matched`` (compared with a peer chosen on its earlier trend),
  ``causal`` (a design that rules out other explanations; none here).

Every rule (groups, link key, jurisdiction choice) lives in
``assumptions/validation_california.yaml`` and was fixed before the post-2019
outcomes were computed. The jurisdiction choice sees only pre-period rates.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.registry import get_source
from visionhousing.features.permit_timing import KaplanMeier, kaplan_meier
from visionhousing.validation.apr_data import AprSettings

__all__ = [
    "STATEWIDE",
    "a2_projects",
    "adu_rates",
    "assign_groups",
    "build_documents",
    "comparison_records",
    "donor_ranges",
    "link_applications",
    "pa_adu_rate",
    "select_jurisdictions",
    "stage_summary",
    "stage_tables",
    "write_yaml",
]

STATEWIDE = "CALIFORNIA"
EVENT_STAGES = ("ent", "bp", "co")
STAGE_NAMES = {
    "ent": "entitlement approved",
    "bp": "building permit issued",
    "co": "completed (certificate of occupancy)",
}


# --------------------------------------------------------------------------- groups


def assign_groups(
    unit_cat: pd.Series,
    streamlining: pd.Series,
    project_type: pd.Series | None,
    groups: Sequence[Mapping[str, Any]],
) -> pd.Series:
    """Give each row exactly one project group (first matching rule wins).

    A rule matches when every condition it names holds: ``unit_cats`` (the APR
    unit category is in the list) and, together, ``streamlining_values`` /
    ``streamlining_pattern`` / ``project_type`` (any one of these approval-path
    tests). Rows matching no rule get ``""``.
    """
    cat = unit_cat.astype(str)
    flag = streamlining.astype(str)
    ptype = (
        project_type.astype(str)
        if project_type is not None
        else pd.Series("", index=unit_cat.index, dtype=object)
    )
    out = pd.Series("", index=unit_cat.index, dtype=object)
    free = pd.Series(True, index=unit_cat.index)
    for rule in groups:
        ok = pd.Series(True, index=unit_cat.index)
        if "unit_cats" in rule:
            ok &= cat.isin(list(rule["unit_cats"]))
        path_tests = []
        if "streamlining_values" in rule:
            path_tests.append(flag.isin(list(rule["streamlining_values"])))
        if "streamlining_pattern" in rule:
            path_tests.append(flag.str.contains(str(rule["streamlining_pattern"]), regex=True))
        if "project_type" in rule:
            path_tests.append(ptype == str(rule["project_type"]))
        if path_tests:
            any_path = path_tests[0]
            for t in path_tests[1:]:
                any_path = any_path | t
            ok &= any_path
        hit = ok & free
        out[hit] = str(rule["id"])
        free &= ~hit
    return out


def _key(frame: pd.DataFrame) -> pd.Series:
    """Project key; blank when the parcel number is missing (such rows never join)."""
    key = frame["juris"].astype(str) + "|" + frame["apn"].astype(str) + "|"
    key = key + frame["unit_cat"].astype(str)
    return key.where(frame["apn"].astype(str) != "", "")


# --------------------------------------------------------------------------- linking


def link_applications(a: pd.DataFrame, a2: pd.DataFrame, s: AprSettings) -> pd.DataFrame:
    """Follow each Table A application to its first later stage dates in Table A2.

    Duplicate applications (same project and submission date reported twice) are
    kept once. For each stage the first Table A2 date on or after the
    application date is taken. ``ever_linked`` says whether the project appears
    in Table A2 at all (any date), a check on how well the join works.
    """
    apps = a.copy()
    apps["key"] = _key(apps)
    apps["group"] = assign_groups(
        apps["unit_cat"], apps["streamlining"], apps["project_type"], s["groups"]
    )
    keyed = apps["key"] != ""
    dup = keyed & apps.duplicated(["key", "app_date"])
    apps = apps[~dup].reset_index(drop=True)
    apps["app_id"] = np.arange(len(apps))
    rows = a2.assign(key=_key(a2))
    rows = rows[rows["key"] != ""][["key", "ent_date", "bp_date", "co_date"]]
    joined = apps.loc[apps["key"] != "", ["app_id", "key", "app_date"]].merge(rows, on="key")
    for stage in EVENT_STAGES:
        col = f"{stage}_date"
        later = joined[joined[col] >= joined["app_date"]]
        first = later.groupby("app_id")[col].min()
        apps[col] = apps["app_id"].map(first)
    apps["ever_linked"] = apps["app_id"].isin(joined["app_id"].unique())
    apps["duplicates_dropped"] = int(dup.sum())
    return apps


def a2_projects(a2: pd.DataFrame, s: AprSettings) -> pd.DataFrame:
    """Collapse Table A2 rows into projects: first date of each stage, homes, group.

    Rows without a usable parcel number stay as their own project.
    """
    rows = a2.copy()
    rows["key"] = _key(rows)
    blank = rows["key"] == ""
    rows.loc[blank, "key"] = "row:" + rows.index[blank].astype(str)
    rows["group"] = assign_groups(rows["unit_cat"], rows["streamlining"], None, s["groups"])
    rows["units"] = rows[["ent_units", "bp_units", "co_units"]].max(axis=1)
    agg = rows.groupby("key", sort=False).agg(
        juris=("juris", "first"),
        unit_cat=("unit_cat", "first"),
        ent_date=("ent_date", "min"),
        bp_date=("bp_date", "min"),
        co_date=("co_date", "min"),
        units=("units", "max"),
        first_year=("year", "min"),
    )
    # A project is streamlined if any of its rows says so: group from all rows.
    grp = rows.assign(rank=rows["group"].map(_group_rank(s))).groupby("key", sort=False)["rank"]
    order = [str(g["id"]) for g in s["groups"]] + [""]
    agg["group"] = grp.min().map(lambda r: order[int(r)])
    agg["juris"] = agg["juris"].astype(str)
    return agg.reset_index()


def _group_rank(s: AprSettings) -> dict[str, int]:
    ids = [str(g["id"]) for g in s["groups"]]
    return {**{g: i for i, g in enumerate(ids)}, "": len(ids)}


# --------------------------------------------------------------------------- Kaplan-Meier


def _km(start: pd.Series, end: pd.Series, censor: date, first: date) -> tuple[KaplanMeier, int]:
    """Kaplan-Meier for start -> end; no end date means waiting until ``censor``.

    Only starts on or after ``first`` are used (earlier starts were not followed
    from the beginning). Cases whose end date is before their start are dropped
    and counted.
    """
    ok = start.notna() & (start >= pd.Timestamp(first))
    start, end = start[ok], end[ok]
    bad = end.notna() & (end < start)
    start, end = start[~bad], end[~bad]
    event = end.notna()
    stop = end.where(event, pd.Timestamp(censor))
    days = (stop - start).dt.days.astype(float)
    return kaplan_meier(days.to_numpy(), event.to_numpy()), int(bad.sum())


def stage_summary(start: pd.Series, end: pd.Series, s: AprSettings) -> dict[str, Any]:
    """Counts, Kaplan-Meier day quantiles and shares reached by 1-5 years for one stage."""
    km, dropped = _km(start, end, s.censor_date, date(s.first_year, 1, 1))
    small = km.n < int(s["min_projects"])
    per_year = float(s["days_per_year"])
    out: dict[str, Any] = {
        "projects": km.n,
        "reached": km.events,
        "censored_share": None if km.n == 0 else round(1 - km.events / km.n, 3),
        "end_before_start_dropped": dropped,
        "days": {
            f"p{round(q * 100):02d}": None if small else km.quantile(q) for q in s["quantiles"]
        },
        "reached_by_years": {},
    }
    for h in s["horizons_years"]:
        r = None if small else km.reached_by(int(h) * per_year)
        out["reached_by_years"][int(h)] = None if r is None else round(r, 3)
    return out


def _status_shares(apps: pd.DataFrame) -> dict[str, Any]:
    counts = apps["status"].astype(str).str.lower().value_counts()
    n = len(apps)
    return {
        k: (None if n == 0 else round(int(counts.get(k, 0)) / n, 3))
        for k in ("approved", "pending", "withdrawn", "disapproved")
    }


def stage_tables(
    apps: pd.DataFrame, projects: pd.DataFrame, places: Sequence[str], s: AprSettings
) -> dict[str, Any]:
    """Stage tables for the state and each chosen place, by project group.

    For applications (Table A, cohorts from the first study year): application
    to entitlement, to building permit and to completion. For Table A2 projects:
    entitlement to permit and permit to completion.
    """
    groups = [str(g["id"]) for g in s["groups"]]
    out: dict[str, Any] = {}
    for place in [STATEWIDE, *places]:
        a = apps if place == STATEWIDE else apps[apps["juris"].astype(str) == place]
        p = projects if place == STATEWIDE else projects[projects["juris"] == place]
        per_group: dict[str, Any] = {}
        for g in groups:
            ag = a[a["group"] == g]
            pg = p[p["group"] == g]
            per_group[g] = {
                "applications": len(ag),
                "application_units": int(ag["units"].sum()),
                "ever_linked_share": None
                if len(ag) == 0
                else round(float(ag["ever_linked"].mean()), 3),
                "reported_status_share": _status_shares(ag),
                "application_to_entitlement": stage_summary(ag["app_date"], ag["ent_date"], s),
                "application_to_permit": stage_summary(ag["app_date"], ag["bp_date"], s),
                "application_to_completion": stage_summary(ag["app_date"], ag["co_date"], s),
                "entitlement_to_permit": stage_summary(pg["ent_date"], pg["bp_date"], s),
                "permit_to_completion": stage_summary(pg["bp_date"], pg["co_date"], s),
            }
        out[place] = per_group
    return out


def cohort_table(apps: pd.DataFrame, group: str, s: AprSettings) -> dict[str, Any]:
    """Statewide application-to-permit summary for one group, by application year."""
    a = apps[apps["group"] == group]
    years = a["app_date"].dt.year
    return {
        int(y): stage_summary(a.loc[years == y, "app_date"], a.loc[years == y, "bp_date"], s)
        for y in s.years
    }


# --------------------------------------------------------------------------- ADU rates


def adu_rates(a2: pd.DataFrame, lots: pd.DataFrame, s: AprSettings) -> pd.DataFrame:
    """ADU homes permitted per 1,000 one-unit detached homes, by jurisdiction and year.

    Count rule: the homes on Table A2 rows with unit category ADU whose building
    permit date falls in the reporting year (rows carrying an earlier date are
    not counted again). The state row counts every jurisdiction, including
    unincorporated county areas; place rows exist only for incorporated cities
    with a Census place of the same name.
    """
    adu = a2[(a2["unit_cat"].astype(str) == "ADU") & a2["bp_date"].notna()]
    adu = adu[adu["bp_date"].dt.year == adu["year"]]
    by = adu.groupby([adu["juris"].astype(str), "year"], observed=True)["bp_units"].sum()
    state = adu.groupby("year")["bp_units"].sum()
    rows = [{"juris": j, "year": int(y), "adu_permits": int(v)} for (j, y), v in by.items()] + [
        {"juris": STATEWIDE, "year": int(y), "adu_permits": int(v)} for y, v in state.items()
    ]
    frame = pd.DataFrame(rows, columns=["juris", "year", "adu_permits"])
    den = lots[lots["incorporated"]].drop_duplicates("juris", keep=False)
    den = pd.concat([den, lots[lots["juris"] == STATEWIDE]]).drop_duplicates("juris")
    frame = frame.merge(den[["juris", "lots"]], on="juris", how="inner")
    full = pd.MultiIndex.from_product([frame["juris"].unique(), s.years], names=["juris", "year"])
    frame = (
        frame.set_index(["juris", "year"])
        .reindex(full)
        .reset_index()
        .assign(
            adu_permits=lambda f: f["adu_permits"].fillna(0).astype(int),
            lots=lambda f: f.groupby("juris")["lots"].transform("max"),
        )
    )
    frame["per_1000_lots"] = 1000 * frame["adu_permits"] / frame["lots"]
    return frame


def select_jurisdictions(
    pre_rates: pd.DataFrame, s: AprSettings
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the stated choice rule; sees only pre-period rates.

    Returns:
        The chosen places (focal then peers), each with its role and reason,
        and the candidate table used.
    """
    rule = s["selection"]
    pre_years = [int(y) for y in rule["pre_years"]]
    if not set(pre_rates["year"].unique()) <= set(pre_years):
        raise ValueError("the selection step may only see pre-period years")
    big = pre_rates[
        (pre_rates["lots"] >= float(rule["min_lots"])) & (pre_rates["juris"] != STATEWIDE)
    ]
    pre = big.groupby("juris").agg(lots=("lots", "max"), rate=("per_1000_lots", "mean"))
    focal = [f for f in rule["focal"] if f["name"] in pre.index]
    tolerance = float(rule["peer_tolerance"])
    taken = {f["name"] for f in focal}
    chosen: list[dict[str, Any]] = []
    peers: list[dict[str, Any]] = []
    for f in focal:
        own = float(pre.loc[f["name"], "rate"])
        chosen.append(
            {
                "name": f["name"],
                "role": "focal",
                "pathway": f["pathway"],
                "change": f["change"],
                "change_date": str(f["change_date"]),
                "source_url": f["source_url"],
                "pre_period_rate": round(own, 3),
                "lots": int(pre.loc[f["name"], "lots"]),
            }
        )
        rest = pre.drop(index=sorted(taken))
        gap = ((rest["rate"] - own).abs() / own) if own > 0 else rest["rate"] * np.inf
        gap = gap[gap <= tolerance].sort_values()
        if gap.empty:
            chosen[-1]["peer"] = None
            chosen[-1]["peer_note"] = (
                f"no candidate within {tolerance:.0%} of its pre-period rate; "
                "compared with its own earlier years only"
            )
            continue
        p = str(gap.index[0])
        taken.add(p)
        chosen[-1]["peer"] = p
        peers.append(
            {
                "name": p,
                "role": "peer",
                "peer_of": f["name"],
                "pathway": "state-law ADU pathway; local programme not reviewed",
                "change": "none recorded",
                "change_date": None,
                "source_url": None,
                "pre_period_rate": round(float(pre.loc[p, "rate"]), 3),
                "lots": int(pre.loc[p, "lots"]),
                "reason": f"closest pre-period ADU permit rate to {f['name'].title()} "
                f"({own:.3f} per 1,000 lots; gap {float(gap.iloc[0]):.1%})",
            }
        )
    chosen = chosen + peers
    candidates = {
        str(j): {"lots": int(r["lots"]), "pre_period_rate": round(float(r["rate"]), 3)}
        for j, r in pre.sort_values("lots", ascending=False).iterrows()
    }
    return chosen, candidates


def _mean(values: pd.Series) -> float | None:
    return None if values.empty else round(float(values.mean()), 3)


def rate_table(rates: pd.DataFrame, places: Sequence[str]) -> dict[str, Any]:
    """Per-year ADU permits and rate for the state and the chosen places."""
    out: dict[str, Any] = {}
    for place in [STATEWIDE, *places]:
        r = rates[rates["juris"] == place].sort_values("year")
        out[place] = {
            "lots": None if r.empty else int(r["lots"].iloc[0]),
            "adu_permits": {
                int(y): int(v) for y, v in zip(r["year"], r["adu_permits"], strict=True)
            },
            "per_1000_lots": {
                int(y): round(float(v), 3)
                for y, v in zip(r["year"], r["per_1000_lots"], strict=True)
            },
        }
    return out


# --------------------------------------------------------------------------- records


def _period_rate(rates: pd.DataFrame, place: str, years: Sequence[int]) -> float | None:
    r = rates[(rates["juris"] == place) & rates["year"].isin([int(y) for y in years])]
    return _mean(r["per_1000_lots"])


def comparison_records(
    rates: pd.DataFrame,
    chosen: Sequence[Mapping[str, Any]],
    tables: Mapping[str, Any],
    s: AprSettings,
) -> list[dict[str, Any]]:
    """Comparison records: what changed, where, date, outcome, denominator, horizon, quality."""
    change = s["statewide_change"]
    pre, post = list(change["pre_years"]), list(change["post_years"])
    horizon = f"{post[0]}-{post[-1]} vs {pre[0]}-{pre[-1]}"
    den = "one-unit detached homes (ACS 2020-2024 5-year, B25024_002)"
    records: list[dict[str, Any]] = []
    st_pre, st_post = _period_rate(rates, STATEWIDE, pre), _period_rate(rates, STATEWIDE, post)
    records.append(
        {
            "id": "ca_apr.adu.statewide_2020",
            "what_changed": change["what"],
            "where": "California, every reporting city and county",
            "date": str(change["date"]),
            "outcome": {
                "measure": "ADU homes permitted per 1,000 one-unit detached homes per year",
                "before": st_pre,
                "after": st_post,
                "change": None if st_pre is None or st_post is None else round(st_post - st_pre, 3),
            },
            "denominator": den,
            "horizon": horizon,
            "identification": "descriptive",
            "caveats": [
                "Before/after only: rents, interest rates, the pandemic and later state laws "
                "(2023, 2024) changed at the same time.",
                "The 2018-2019 base already follows the 2017 state ADU laws.",
                "Jurisdictions report themselves; late or missing reports lower a year's count.",
            ],
        }
    )
    for c in (c for c in chosen if c["role"] == "focal"):
        f_pre, f_post = _period_rate(rates, c["name"], pre), _period_rate(rates, c["name"], post)
        peer = c.get("peer")
        if peer is None:
            records.append(
                {
                    "id": f"ca_apr.adu.{_slug(c['name'])}_before_after",
                    "what_changed": f"{c['change']} ({c['pathway']})",
                    "where": f"{c['name'].title()} (no peer with a similar pre-period rate)",
                    "date": c["change_date"],
                    "outcome": {
                        "measure": "ADU homes permitted per 1,000 one-unit detached homes per year",
                        "before": f_pre,
                        "after": f_post,
                        "change": None
                        if f_pre is None or f_post is None
                        else round(f_post - f_pre, 3),
                        "statewide_change": None
                        if st_pre is None or st_post is None
                        else round(st_post - st_pre, 3),
                    },
                    "denominator": den,
                    "horizon": horizon,
                    "identification": "descriptive",
                    "caveats": [
                        c.get("peer_note", "no peer"),
                        "The state law change of 2020 applies here too; the statewide change "
                        "is shown for scale, not as a control.",
                        "The local change date is taken from the city's own pages; where it "
                        "says 'not verified' it was not confirmed for this study.",
                    ],
                }
            )
            continue
        p_pre, p_post = _period_rate(rates, peer, pre), _period_rate(rates, peer, post)
        vals = [f_pre, f_post, p_pre, p_post]
        did = None if None in vals else round((f_post - f_pre) - (p_post - p_pre), 3)
        records.append(
            {
                "id": f"ca_apr.adu.{_slug(c['name'])}_vs_{_slug(peer)}",
                "what_changed": f"{c['change']} ({c['pathway']})",
                "where": f"{c['name'].title()} compared with {peer.title()} "
                "(peer chosen on the pre-period rate)",
                "date": c["change_date"],
                "outcome": {
                    "measure": "ADU homes permitted per 1,000 one-unit detached homes per year",
                    "focal_before": f_pre,
                    "focal_after": f_post,
                    "peer_before": p_pre,
                    "peer_after": p_post,
                    "difference_in_change": did,
                },
                "denominator": den,
                "horizon": horizon,
                "identification": "matched",
                "caveats": [
                    "Matched on two pre-period years only; no test of parallel trends.",
                    "Both cities had the 2020 state law change; the difference reflects "
                    "local pathway, market and reporting together.",
                    "The local change date is taken from the city's own pages; where it "
                    "says 'not verified' it was not confirmed for this study.",
                ],
            }
        )
    state = tables[STATEWIDE]

    def reach(g: str, stage: str, years: int) -> Any:
        return state[g][stage]["reached_by_years"].get(years)

    records.append(
        {
            "id": "ca_apr.sb9.take_through",
            "what_changed": "SB 9 (2021): by-right duplex and urban lot split on "
            "single-family lots",
            "where": "California, every reporting city and county",
            "date": "2022-01-01",
            "outcome": {
                "measure": "share of applications with a building permit within 1 / 2 / 3 years "
                "(Kaplan-Meier, censored at the end of the data)",
                "sb9": [reach("sb9", "application_to_permit", y) for y in (1, 2, 3)],
                "mf_2_4_not_sb9": [reach("mf_2_4", "application_to_permit", y) for y in (1, 2, 3)],
                "single_family": [
                    reach("single_family", "application_to_permit", y) for y in (1, 2, 3)
                ],
                "sb9_applications": state["sb9"]["applications"],
            },
            "denominator": "applications reported in Table A (application cohorts 2018 on; "
            "SB 9 from 2022)",
            "horizon": "1-3 years after application",
            "identification": "descriptive",
            "caveats": [
                "SB 9 is known only from the APR's streamlining field; jurisdictions that leave "
                "it blank hide SB 9 projects among single-family and 2-4 projects.",
                "Applications that never join a Table A2 row count as not permitted, so all "
                "shares are lower bounds.",
            ],
        }
    )
    records.append(
        {
            "id": "ca_apr.mf.ministerial_vs_other",
            "what_changed": "Streamlined or ministerial approval of 5+ home projects "
            "(SB 35 2017, AB 2011 2022, SB 423 2023 and local ministerial paths)",
            "where": "California, every reporting city and county",
            "date": "2018-01-01",
            "outcome": {
                "measure": "application to building permit: median days and share permitted "
                "within 2 years",
                "ministerial_median_days": state["ministerial_5_plus"]["application_to_permit"][
                    "days"
                ]["p50"],
                "other_median_days": state["mf_5_plus_other"]["application_to_permit"]["days"][
                    "p50"
                ],
                "ministerial_within_2y": reach("ministerial_5_plus", "application_to_permit", 2),
                "other_within_2y": reach("mf_5_plus_other", "application_to_permit", 2),
            },
            "denominator": "5+ home applications reported in Table A",
            "horizon": "application cohorts 2018 on, followed to the end of the data",
            "identification": "descriptive",
            "caveats": [
                "Projects choose their path: streamlined projects differ in size, place and "
                "affordability, so the gap is not the effect of the path.",
                "Applications that never join a Table A2 row count as not permitted.",
            ],
        }
    )
    return records


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")


# --------------------------------------------------------------------------- donor ranges


def _range(values: Sequence[Any]) -> dict[str, Any] | None:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return {"low": round(min(vals), 3), "high": round(max(vals), 3), "n": len(vals)}


def donor_ranges(tables: Mapping[str, Any], s: AprSettings) -> dict[str, Any]:
    """Ranges across the state and the chosen places, per group, for E4/E6 sensitivities."""
    groups = [str(g["id"]) for g in s["groups"]]
    out: dict[str, Any] = {}
    stages = ("application_to_permit", "permit_to_completion", "application_to_entitlement")
    for g in groups:
        entry: dict[str, Any] = {}
        for stage in stages:
            cells = [tables[p][g][stage] for p in tables]
            entry[stage] = {
                "median_days": _range([c["days"]["p50"] for c in cells]),
                "p75_days": _range([c["days"]["p75"] for c in cells]),
                "reached_by_years": {
                    int(h): _range([c["reached_by_years"].get(int(h)) for c in cells])
                    for h in s["horizons_years"]
                },
            }
        out[g] = entry
    return out


# --------------------------------------------------------------------------- plausibility


def _steps_from_run(path: Path, reform: str) -> dict[str, dict[str, Any]]:
    from visionhousing.export_static import read_saved

    data = read_saved(Path(path))
    res = data["reform_results"][reform]
    records = res.get("records") or {}
    return {
        k: {kk: v.get(kk) for kk in ("name", "p10", "p50", "p90", "unit")}
        for k, v in records.items()
        if isinstance(v, dict)
    } | {
        "_meta": {
            "state": data.get("state"),
            "seed": data.get("seed"),
            "draws": data.get("draws"),
            "hashes": data.get("hashes"),
        }
    }


def _steps_from_testland(reform: str, draws: int) -> dict[str, dict[str, Any]]:
    from visionhousing.engine.context import load_context
    from visionhousing.engine.core import compute

    result = compute(load_context("TL"), [reform], draws=draws)
    records = result.reforms[reform].result.records
    out = {
        k: {"name": r.name, "p10": r.p10, "p50": r.p50, "p90": r.p90, "unit": r.unit}
        for k, r in records.items()
    }
    out["_meta"] = {"state": "TL", "seed": 0, "draws": draws, "hashes": None}
    return out


def pa_adu_rate(s: AprSettings, root: Path = mf.ROOT) -> dict[str, Any]:
    """The model's ADU result as ADUs per 1,000 eligible lots per year (P10, P50, P90).

    Rate = ADUs built over the horizon (P10/P50/P90) / P50 eligible lots /
    years x 1,000. Reads the first saved run that exists, else runs Testland.
    """
    rule = s["plausibility"]
    source = None
    for rel in rule["run_files"]:
        if (root / rel).exists():
            steps, source = _steps_from_run(root / rel, rule["reform"]), rel
            break
    else:
        steps, source = (
            _steps_from_testland(rule["reform"], int(rule["fallback_draws"])),
            "testland",
        )
    eligible = steps[rule["eligible_step"]]
    built = steps[rule["built_step"]]
    lots = float(eligible["p50"])
    years = int(rule["years"])
    rate = {q: round(1000 * float(built[q]) / lots / years, 3) for q in ("p10", "p50", "p90")}
    return {
        "run": source,
        "state": steps["_meta"]["state"],
        "seed": steps["_meta"]["seed"],
        "draws": steps["_meta"]["draws"],
        "hashes": steps["_meta"]["hashes"],
        "eligible_lots_p50": round(lots),
        "eligible_name": eligible["name"],
        "adus_built": {q: round(float(built[q])) for q in ("p10", "p50", "p90")},
        "built_name": built["name"],
        "years": years,
        "per_1000_eligible_lots_per_year": rate,
        "formula": f"rate = ADUs built over {years} years (P10/P50/P90) / P50 eligible lots "
        f"/ {years} x 1,000",
    }


# --------------------------------------------------------------------------- documents


def _source_entry(source_id: str, url_values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    src = get_source(source_id)
    return {
        "id": src.id,
        "citation": f"{src.publisher}, {src.name}",
        "url": src.url(**(url_values or {})),
        "licence": src.licence,
    }


def _sources(s: AprSettings) -> list[dict[str, Any]]:
    return [
        _source_entry(s["sources"]["table_a"]),
        _source_entry(s["sources"]["table_a2"]),
        _source_entry(s["sources"]["acs_table"], {"table": s["acs_table_name"]}),
    ]


LIMITS = [
    "Self-reported by each city and county; HCD does not verify the reports. Missing or "
    "late reports lower counts; revisions change past years in later downloads.",
    "Applications are joined to later stages by jurisdiction, parcel number and unit "
    "category. Applications that never join count as not reaching the stage, so reached "
    "shares are lower bounds; the share that ever joins is reported beside each figure.",
    "Several projects on one parcel with the same unit category (for example two ADUs "
    "years apart) merge into one project.",
    "Entitlement dates are reported only where a discretionary approval exists; ADUs and "
    "ministerial projects mostly have none.",
    "Completion is a certificate of occupancy reported in the APR; some jurisdictions "
    "report it unevenly, so permit-to-completion shares are lower bounds.",
    "The lot denominator is one-unit detached homes in one ACS period (2020-2024) for "
    "every year; unincorporated county areas get no place rate.",
]


def _provenance(s: AprSettings, as_of: str, summary: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "measured_on": as_of,
        "method": "Table A and Table A2 streamed in chunks (settings in "
        "assumptions/validation_california.yaml); projects joined on jurisdiction, parcel "
        "number and unit category; Kaplan-Meier in visionhousing.features.permit_timing; "
        "see docs/modules/validation_california.md.",
        "generated_by": "visionhousing validate-california",
        "sources": _sources(s),
        "limits": LIMITS,
    }


def build_documents(
    a: pd.DataFrame,
    a2: pd.DataFrame,
    lots: pd.DataFrame,
    s: AprSettings,
    *,
    as_of: str,
    root: Path = mf.ROOT,
    plausibility: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the evidence, donor and plausibility documents from the compact frames."""
    rates = adu_rates(a2, lots, s)
    pre_years = [int(y) for y in s["selection"]["pre_years"]]
    chosen, candidates = select_jurisdictions(rates[rates["year"].isin(pre_years)], s)
    places = [c["name"] for c in chosen]
    apps = link_applications(a, a2, s)
    projects = a2_projects(a2, s)
    tables = stage_tables(apps, projects, places, s)
    records = comparison_records(rates, chosen, tables, s)
    rate_rows = rate_table(rates, places)
    linked = {
        "applications": len(apps),
        "duplicates_dropped": int(apps["duplicates_dropped"].iloc[0]) if len(apps) else 0,
        "with_parcel_number": round(float((apps["key"] != "").mean()), 3) if len(apps) else None,
        "ever_linked_share": round(float(apps["ever_linked"].mean()), 3) if len(apps) else None,
        "table_a2_rows": len(a2),
        "table_a2_projects": len(projects),
    }
    groups = {str(g["id"]): str(g["label"]) for g in s["groups"]}
    evidence = {
        "version": 1,
        "id": f"california_apr_{as_of[:7].replace('-', '_')}",
        "state": "CA",
        "label": "California APR: stage attrition, elapsed time and ADU permit rates",
        "use": "evidence and validation only; not a Pennsylvania input",
        "provenance": _provenance(
            s,
            as_of,
            "Application, entitlement, permit and completion stages from California's "
            "Housing Element Annual Progress Reports, by project group, for the state and "
            "jurisdictions chosen by a stated rule; ADU permits per 1,000 one-unit detached homes.",
        ),
        "groups": groups,
        "selection": {
            "rule": "Cities with at least "
            f"{int(s['selection']['min_lots']):,} one-unit detached homes; focal cities named for "
            "a documented pathway difference; each focal city's peer = the candidate with the "
            f"closest pre-period ({pre_years[0]}-{pre_years[-1]}) ADU permit rate, within "
            f"{float(s['selection']['peer_tolerance']):.0%}, else no peer. Only pre-period rates "
            "were passed to the selection step.",
            "chosen": chosen,
            "candidates": candidates,
        },
        "link_quality": linked,
        "records": records,
        "adu_rates": rate_rows,
        "stage_tables": tables,
        "adu_application_cohorts_statewide": cohort_table(apps, "adu", s),
    }
    donor = {
        "version": 1,
        "id": f"apr_timing_donor_ca_{as_of[:7].replace('-', '_')}",
        "state": "CA",
        "label": "Transferred timing and attrition ranges from California's APR "
        "(for sensitivities)",
        "status": "transferred",
        "use": "Ranges a Pennsylvania timing (E4) or financing (E6) scenario may borrow for a "
        "sensitivity, labelled 'transferred from California'. Not measured in Pennsylvania.",
        "provenance": _provenance(
            s,
            as_of,
            "Low-high ranges across California and the chosen cities of the median and 75th "
            "percentile days, and the share reached within 1-5 years, for application to "
            "permit, permit to completion and application to entitlement, by project group.",
        ),
        "places": [STATEWIDE, *places],
        "groups": groups,
        "ranges": donor_ranges(tables, s),
    }
    plaus = None
    if plausibility is not None:
        st = rate_rows[STATEWIDE]["per_1000_lots"]
        place_rates = [v for p in places for v in rate_rows[p]["per_1000_lots"].values()]
        plaus = {
            "version": 1,
            "id": f"adu_rate_plausibility_{as_of[:7].replace('-', '_')}",
            "state": plausibility.get("state"),
            "label": "ADU rate plausibility: VisionHousing result vs California observed rates",
            "status": "comparison; California rates are context, not a target",
            "provenance": {
                **_provenance(
                    s,
                    as_of,
                    "VisionHousing's ADU result per 1,000 eligible lots per year next to "
                    "California ADU permits per 1,000 one-unit detached homes per year.",
                ),
                "limits": [
                    *LIMITS[-1:],
                    "The model's eligible lots include attached single-family homes; "
                    "California's denominator counts detached homes only, so the model's "
                    "rate is lower than a like-for-like rate.",
                    "California pairs strong state ADU law with very high rents; its rates "
                    "are an upper reference, not an expectation for Pennsylvania.",
                ],
            },
            "model": dict(plausibility),
            "california": {
                "statewide_by_year": st,
                "statewide_range": _range(list(st.values())),
                "chosen_places_range": _range(place_rates),
                "chosen_places": {p: rate_rows[p]["per_1000_lots"] for p in places},
                "unit": "ADU homes permitted per 1,000 one-unit detached homes per year",
            },
            "ratio_model_p50_to_statewide_mean": None
            if not st
            else round(
                plausibility["per_1000_eligible_lots_per_year"]["p50"]
                / float(np.mean(list(st.values()))),
                3,
            ),
        }
    return {"evidence": evidence, "donor": donor, "plausibility": plaus}


# --------------------------------------------------------------------------- writing


HEADERS = {
    "evidence": """\
# Evidence: California Housing Element Annual Progress Reports (generated; do not edit).
# Written by `visionhousing validate-california`. Comparison records, stage tables
# and ADU permit rates for California and jurisdictions chosen by a stated rule.
# Evidence only: nothing here is a Pennsylvania input. Blank (null) figures mean
# too few projects or a Kaplan-Meier curve that never reached that level.
# Method: docs/modules/validation_california.md.
""",
    "donor": """\
# Calibration (transferred): California timing and attrition ranges (generated; do not edit).
# Written by `visionhousing validate-california`. Measured in California and
# labelled transferred: a Pennsylvania scenario may use these ranges only as a
# sensitivity. Method: docs/modules/validation_california.md.
""",
    "plausibility": """\
# Plausibility table: VisionHousing ADU rate vs California (generated; do not edit).
# Written by `visionhousing validate-california`. Read by the plausibility card.
# Method: docs/modules/validation_california.md.
""",
}


class _Flow(dict):
    """A small mapping written on one line."""


class _Dumper(yaml.SafeDumper):
    """Safe YAML writer that keeps small number rows on one line."""


_Dumper.add_representer(
    _Flow, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", v, flow_style=True)
)


def _is_number(value: Any) -> bool:
    return value is None or (isinstance(value, int | float) and not isinstance(value, bool))


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        out = {(k.item() if isinstance(k, np.generic) else k): _clean(v) for k, v in value.items()}
        small = out and len(out) <= 8 and all(_is_number(v) for v in out.values())
        return _Flow(out) if small else out
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        return _clean(value.item())
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def write_yaml(doc: Mapping[str, Any], path: Path, header: str) -> Path:
    """Write one document as YAML with a short header; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(
        _clean(dict(doc)), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100
    )
    path.write_text(header + "\n" + body, encoding="utf-8")
    return path
