"""Permit-stage timing: issue counts, stage lags and a Kaplan-Meier estimate.

Plain-language summary
----------------------
This module turns the two Philadelphia extracts (see
:mod:`visionhousing.data.sources.phl_permit_timing`) into a small table that
the engine can read from ``assumptions/calibration/permit_lag_phl.yaml``.

Terms used here:

- **Stage lag**: days from the zoning permit being issued to the
  new-construction building permit being issued. It is a real, observed part
  of the approval path, but it is not the full application-to-issue time:
  the city's open table has no application date.
- **Right-censoring**: a zoning permit with no building permit yet. We know
  only that the wait is *at least* the days since the zoning permit was
  issued; the true wait is cut off ("censored") on the right.
- **Kaplan-Meier estimate**: the standard way to estimate the share of a
  group that has reached an event by each day when some members are still
  waiting. At every day on which some permits were issued, the share still
  waiting is multiplied by (1 - issued that day / still waiting that day).
  Censored cases count as "still waiting" until their last observed day,
  then drop out. A quantile (for example P50, the median) is the first day
  on which the estimated share issued reaches that level. If the curve never
  gets there (many zoning permits never lead to a building permit), the
  quantile is left blank rather than guessed.

Three tables come out:

1. ``issue_counts``: new-construction building permits and their homes, by
   year and by month, for each project group (1 home, 2-4, 5 or more,
   ADU-like).
2. ``stage_lag_issued``: the stage lag for building permits issued each year,
   by group. Every case here has an end date, so Kaplan-Meier equals the plain
   sample quantiles. It describes permits that got through, not those that
   did not.
3. ``stage_lag_cohort``: each year's new-structure zoning permits followed
   forward to the read date, with Kaplan-Meier quantiles, the share censored
   and the share with a building permit after 180, 365, 730 and 1,095 days.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.data.sources.phl_permit_timing import (
    SOURCE_ID,
    TERMS_URL,
    TimingSettings,
    building_query,
    query_hash,
    zoning_query,
)

__all__ = [
    "KaplanMeier",
    "build_table",
    "issue_counts",
    "kaplan_meier",
    "label_groups",
    "stage_lag_cohort",
    "stage_lag_issued",
    "write_table",
]

ADU_GROUP = "adu_like"
ALL_GROUP = "all_new_homes"


@dataclass(frozen=True)
class KaplanMeier:
    """A Kaplan-Meier curve: event days and the estimated share reached by each."""

    times: np.ndarray
    reached: np.ndarray
    n: int
    events: int
    max_followup: float

    def quantile(self, p: float) -> int | None:
        """First day on which the estimated share reached is at least ``p``, else ``None``."""
        hit = np.nonzero(self.reached >= p - 1e-12)[0]
        return int(self.times[hit[0]]) if hit.size else None

    def reached_by(self, day: float) -> float | None:
        """Estimated share reached by ``day``; ``None`` when no case was followed that long."""
        if self.n == 0 or day > self.max_followup:
            return None
        idx = np.searchsorted(self.times, day, side="right") - 1
        return float(self.reached[idx]) if idx >= 0 else 0.0


def kaplan_meier(durations: Any, observed: Any) -> KaplanMeier:
    """Estimate the Kaplan-Meier curve from durations (days) and event flags.

    Args:
        durations: Days to the event, or to the last day seen for censored cases.
        observed: ``True`` where the event happened, ``False`` where censored.

    Returns:
        The curve. With no censoring, its quantiles equal the sample's lower
        quantiles (the smallest value with at least that share at or below it).
    """
    d = np.asarray(durations, dtype=float)
    e = np.asarray(observed, dtype=bool)
    if d.shape != e.shape:
        raise ValueError("durations and observed must have the same length")
    keep = ~np.isnan(d)
    d, e = d[keep], e[keep]
    if d.size == 0:
        return KaplanMeier(np.array([]), np.array([]), 0, 0, 0.0)
    times = np.unique(d[e])
    order = np.sort(d)
    at_risk = d.size - np.searchsorted(order, times, side="left")
    ev_days = np.sort(d[e])
    events = np.searchsorted(ev_days, times, side="right") - np.searchsorted(
        ev_days, times, side="left"
    )
    surviving = np.cumprod(1.0 - events / at_risk)
    return KaplanMeier(times, 1.0 - surviving, int(d.size), int(e.sum()), float(d.max()))


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def _flag(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(["true", "t", "1"])


def label_groups(building: pd.DataFrame, s: TimingSettings) -> pd.DataFrame:
    """Add ``group`` (unit group, ``adu_like`` or blank) and date columns to building rows."""
    b = building.copy()
    b["issued"] = _dates(b["issued"])
    b["zoning_issued"] = _dates(b["zoning_issued"])
    b["units"] = pd.to_numeric(b["units"], errors="coerce").fillna(0).astype(int)
    b["adu_like"] = _flag(b["adu_like"])
    new_home = b["permittype"].isin(s.building_types) & b["typeofwork"].isin(s.building_work_types)
    group = pd.Series("", index=b.index, dtype=object)
    for name, (lo, hi) in s.unit_groups.items():
        inside = new_home & (b["units"] >= lo) & (b["units"] <= (hi if hi is not None else 10**9))
        group[inside & ~b["adu_like"]] = name
    group[b["adu_like"]] = ADU_GROUP
    b["group"] = group
    b["year"] = b["issued"].dt.year
    return b[b["group"] != ""].reset_index(drop=True)


def _groups(s: TimingSettings) -> list[str]:
    return [*s.unit_groups, ADU_GROUP, ALL_GROUP]


def _members(b: pd.DataFrame, group: str) -> pd.Series:
    if group == ALL_GROUP:
        return b["group"] != ADU_GROUP
    return b["group"] == group


def issue_counts(b: pd.DataFrame, s: TimingSettings) -> dict[str, Any]:
    """Permits and homes by year, and permits by month, for each group."""
    years = list(range(s.first_year, s.last_year + 1))
    by_year: dict[str, Any] = {}
    by_month: dict[str, Any] = {}
    for g in _groups(s):
        rows = b[_members(b, g) & b["year"].isin(years)]
        by_year[g] = {
            int(y): {
                "permits": int((rows["year"] == y).sum()),
                "homes": int(rows.loc[rows["year"] == y, "units"].sum()),
            }
            for y in years
        }
        months = rows.groupby([rows["year"], rows["issued"].dt.month]).size()
        by_month[g] = {int(y): [int(months.get((y, m), 0)) for m in range(1, 13)] for y in years}
    return {"by_year": by_year, "by_month": by_month}


def _summary(km: KaplanMeier, s: TimingSettings) -> dict[str, Any]:
    return {
        "n": km.n,
        **{f"p{round(q * 100):02d}": km.quantile(q) for q in s.quantiles},
    }


def stage_lag_issued(b: pd.DataFrame, s: TimingSettings) -> dict[str, Any]:
    """Zoning-to-building stage lag (days) for permits issued each year, by group."""
    years = list(range(s.first_year, s.last_year + 1))
    out: dict[str, Any] = {}
    for g in _groups(s):
        rows = b[_members(b, g) & b["year"].isin(years)]
        lag = (rows["issued"] - rows["zoning_issued"]).dt.days
        linked = lag.notna()
        dropped = int((lag < 0).sum())
        ok = linked & (lag >= 0)
        per: dict[str, Any] = {}
        for y in [*years, "all"]:
            sel = ok if y == "all" else ok & (rows["year"] == y)
            n_year = len(rows) if y == "all" else int((rows["year"] == y).sum())
            km = kaplan_meier(lag[sel], np.ones(int(sel.sum()), dtype=bool))
            per[str(y)] = {
                **_summary(km, s),
                "permits": n_year,
                "linked_share": _share(int(sel.sum()), n_year),
            }
        out[g] = {"negative_lag_dropped": dropped, "by_year": per}
    return out


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 3)


def stage_lag_cohort(z: pd.DataFrame, s: TimingSettings, as_of: date) -> dict[str, Any]:
    """Kaplan-Meier on new-structure zoning permits, by the year zoning was issued."""
    z = z.copy()
    z["zoning_issued"] = _dates(z["zoning_issued"])
    z["building_issued"] = _dates(z["building_issued"])
    z = z[_flag(z["new_structure"]) & z["zoning_issued"].notna()]
    end = pd.Timestamp(as_of)
    z["event"] = z["building_issued"].notna()
    z["days"] = np.where(
        z["event"],
        (z["building_issued"] - z["zoning_issued"]).dt.days,
        (end - z["zoning_issued"]).dt.days,
    )
    dropped = int((z["days"] < 0).sum())
    z = z[z["days"] >= 0]
    z["year"] = z["zoning_issued"].dt.year
    years = list(range(s.first_year, s.last_year + 1))
    out: dict[str, Any] = {}
    for y in [*years, "all"]:
        rows = z[z["year"].isin(years)] if y == "all" else z[z["year"] == y]
        km = kaplan_meier(rows["days"], rows["event"])
        censored = rows[~rows["event"]]
        closed = censored["status"].astype(str).str.lower().isin(s.closed_statuses)
        out[str(y)] = {
            **_summary(km, s),
            "with_building_permit": km.events,
            "censored_share": _share(len(censored), len(rows)),
            "censored_zoning_closed_share": _share(int(closed.sum()), len(censored)),
            "reached_by_days": {
                int(h): (None if (r := km.reached_by(h)) is None else round(r, 3))
                for h in s.horizons_days
            },
        }
    return {"negative_lag_dropped": dropped, "by_zoning_year": out}


def _read_on(frames: dict[str, pd.DataFrame]) -> str:
    dates = sorted(
        {str(v) for f in frames.values() if "read_on" in f for v in f["read_on"].dropna()}
    )
    return dates[-1] if dates else date.today().isoformat()


def build_table(frames: dict[str, pd.DataFrame], s: TimingSettings) -> dict[str, Any]:
    """Build the whole calibration document from the two extracts."""
    read_on = _read_on(frames)
    b = label_groups(frames["building"], s)
    q_build, q_zone = building_query(s), zoning_query(s)
    return {
        "version": 1,
        "id": f"permit_lag_phl_{read_on[:7].replace('-', '_')}",
        "state": "PA",
        "place": {"name": "Philadelphia", "county": "42101"},
        "status": "stage_lag_observed; application_to_issue_illustrative",
        "label": "Philadelphia permit-stage timing (zoning issued to building permit issued)",
        "provenance": {
            "summary": "Days from a zoning permit being issued to the new-construction "
            "building permit being issued, with Kaplan-Meier handling of zoning permits "
            "still waiting, and new-home building permits issued by month and year.",
            "measured_on": read_on,
            "method": "Two server-side queries on the City's permits table (settings in "
            "assumptions/permit_timing.yaml); Kaplan-Meier estimate in "
            "visionhousing.features.permit_timing; see docs/modules/permit_timing.md.",
            "generated_by": "visionhousing permit-timing",
            "sources": [
                {
                    "id": SOURCE_ID,
                    "citation": "City of Philadelphia, Department of Licenses and Inspections, "
                    "building and zoning permits (Carto table permits)",
                    "url": TERMS_URL,
                    "licence": "City of Philadelphia open data licence (OpenDataPhilly); "
                    "only counts and day quantiles are stored",
                }
            ],
            "query_sha256": {"building": query_hash(q_build), "zoning": query_hash(q_zone)},
            "limits": [
                "No application date is published, so application-to-issue time is not "
                "measured; any such lag built on this table is illustrative.",
                "Building permits issued before the late-2019 change of record system carry "
                "no zoning link, so 2019 and much of 2020 have few linked cases.",
                "Censored zoning permits include projects that will never be built and "
                "building permits not linked to their zoning permit, so the cohort curve "
                "flattens and its upper quantiles are blank.",
                "ADU-like permits are found by text and are very few.",
            ],
        },
        "fields": {
            "application_date": "absent: the open permits table has no application or "
            "received date, so application-to-issue time is not measured",
            "issue_date": "permitissuedate (every issued permit)",
            "zoning_link": "zoningpermitjobid on building permits (Eclipse system, "
            "late 2019 on); earlier permits have no link",
            "units": "numberofunits on building permits; not on zoning permits",
            "adu_like": "text match on approvedscopeofwork (pattern in the settings)",
        },
        "measure": {
            "stage": "zoning permit issued -> new-construction building permit issued",
            "unit": "days",
            "quantiles": "Kaplan-Meier; blank where the curve never reaches the level",
            "use": "observed for this stage; any application-to-issue lag built on it "
            "is illustrative",
        },
        "issue_counts": issue_counts(b, s),
        "stage_lag_issued": stage_lag_issued(b, s),
        "stage_lag_cohort": stage_lag_cohort(frames["zoning"], s, date.fromisoformat(read_on)),
    }


HEADER = """\
# Calibration table: Philadelphia permit-stage timing (generated; do not edit).
# Written by `visionhousing permit-timing` from the City's open permit table.
# The table has no application date, so this is the time from the zoning
# permit being issued to the new-construction building permit being issued,
# not application to issue. Pending zoning permits are right-censored at the
# read date and handled with a Kaplan-Meier estimate. Blank (null) quantiles
# mean the curve never reached that level. Method: docs/modules/permit_timing.md.
"""


class _Flow(dict):
    """A small mapping written on one line."""


class _FlowList(list):
    """A short list of numbers written on one line."""


class _Dumper(yaml.SafeDumper):
    """Safe YAML writer that keeps small number rows on one line."""


_Dumper.add_representer(
    _Flow, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", v, flow_style=True)
)
_Dumper.add_representer(
    _FlowList, lambda d, v: d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=True)
)


def _is_number(value: Any) -> bool:
    return value is None or (isinstance(value, int | float) and not isinstance(value, bool))


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        out = {k: _clean(v) for k, v in value.items()}
        return _Flow(out) if out and all(_is_number(v) for v in out.values()) else out
    if isinstance(value, list):
        items = [_clean(v) for v in value]
        return _FlowList(items) if items and all(_is_number(v) for v in items) else items
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def write_table(doc: dict[str, Any], path: Path) -> Path:
    """Write the document as YAML with a short header; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(_clean(doc), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path
