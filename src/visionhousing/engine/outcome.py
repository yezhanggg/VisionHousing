"""The outcome contract: one unit, one window, years, gross and net, and the as-of date.

Plain-language summary
----------------------
Every headline the engine shows is **additional units authorized by building
permits, 2027-2031**: the Census Bureau's Building Permits Survey measure that
the baseline projects, and, for a reform, the difference from the baseline in
the same unit. This module adds what a reader needs to trust that unit:

1. **Years.** Each five-year total is split into 2027, 2028, ... 2031 in every
   draw. A reform's split follows its adoption curve, a start delay and a ramp
   (ledger ids in ``assumptions/outcome.yaml``, ``timing``)::

       year value(t) = five-year total x ramp(t) / sum of ramp

   so the five yearly values add up to the total in every draw, by
   construction. The baseline's split follows its own projection: each
   county's yearly point projections, scaled to the county's draw. Years are
   kept for the state, every county and (for the tract-level reforms) every
   tract.
2. **Gross and net.** For a reform that redevelops sites (missing middle,
   transit-oriented density) the headline is added authorizations; the net
   change in the housing stock subtracts the reform's share of the homes
   demolished. Both are records, with the homes removed and the baseline's own
   authorizations on the sites.
3. **As of.** Every result carries the as-of date, the vintage of each data
   input (version and access date from the pack manifest), the policy vintage
   (when the law tables were last checked), how enacted rules with a later
   effective date are handled, and the economic conditions held constant.
4. **Audit.** For each reform in the run, what its final stage counts
   (authorizations, starts, completions or stock) and whether a conversion was
   needed.
5. **Five record types** (:func:`accounting`), on every reform: (a) the
   baseline in the permit survey's definition; (b) additional comparable
   authorizations, split by work type (stand-alone or attached new building,
   addition, conversion, uncertain); (c) locally authorized units outside the
   survey's definition, a companion line marked as a bound; (d) authorized net
   change if completed; (e) the completion and timing ledger. The work types
   add up to the headline in every draw. :func:`site_account` turns the four
   event fields of a redeveloped site (policy and counterfactual new units and
   demolitions) into the gross and net accounts.

Nothing here changes a five-year total. Everything is pure except
:func:`load_rules` and :func:`vintages`, which read files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger import Ledger, sample
from visionhousing.ledger.loader import ROOT
from visionhousing.scenarios.adu import ramp_sum, year_shares

__all__ = [
    "RULES_PATH",
    "Accounting",
    "AnnualSeries",
    "AnnualTable",
    "SiteAccount",
    "accounting",
    "annual_series",
    "baseline_year_shares",
    "contract",
    "gross_net_records",
    "load_rules",
    "reform_year_shares",
    "site_account",
    "vintages",
]

RULES_PATH = ROOT / "assumptions" / "outcome.yaml"

TRACT_BLOCK = 256
"""Tracts summarised at a time in :func:`tract_table` (bounds memory; numbers unchanged)."""

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
"""An access date the manifest records as a date (others, such as "not yet fetched", are text)."""


@lru_cache(maxsize=4)
def _read(path: str, mtime: float) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_rules(path: Path | str | None = None) -> dict[str, Any]:
    """Read ``assumptions/outcome.yaml`` (cached until the file changes)."""
    p = Path(path) if path is not None else RULES_PATH
    return _read(str(p), p.stat().st_mtime)


# ---------------------------------------------------------------- tables


@dataclass(frozen=True)
class AnnualTable:
    """P10 / P50 / P90 per geography and year, with one formula and its ids.

    Attributes:
        level: ``"county"`` or ``"tract"``.
        geoids: Geography ids, in row order.
        years: Calendar years, in column order.
        p10: Shape ``(geographies, years)``.
        p50: Same shape.
        p90: Same shape.
        formula: How every cell was made, in words.
        assumption_ids: Ledger ids behind the values.
        source_ids: Sources behind them.
    """

    level: str
    geoids: tuple[str, ...]
    years: tuple[int, ...]
    p10: np.ndarray = field(repr=False)
    p50: np.ndarray = field(repr=False)
    p90: np.ndarray = field(repr=False)
    formula: str = ""
    assumption_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: geoids, years, P10 / P50 / P90 (rows = geographies), formula, ids."""
        return {
            "level": self.level,
            "geoids": list(self.geoids),
            "years": list(self.years),
            "p10": np.round(self.p10, 3).tolist(),
            "p50": np.round(self.p50, 3).tolist(),
            "p90": np.round(self.p90, 3).tolist(),
            "formula": self.formula,
            "assumption_ids": list(self.assumption_ids),
            "source_ids": list(self.source_ids),
        }


def _table(
    level: str,
    geoids: Sequence[str],
    years: Sequence[int],
    draws: np.ndarray,
    formula: str,
    ids: Sequence[str],
    sources: Sequence[str],
) -> AnnualTable:
    """Summarise ``(geographies, draws, years)`` draws into an :class:`AnnualTable`."""
    if draws.shape[0] == 0:
        empty = np.zeros((0, len(years)))
        return AnnualTable(level, (), tuple(years), empty, empty, empty, formula, (), ())
    p10, p50, p90 = np.percentile(draws, [10, 50, 90], axis=1)
    return _summary(level, geoids, years, (p10, p50, p90), draws.shape[1], formula, ids, sources)


def _summary(
    level: str,
    geoids: Sequence[str],
    years: Sequence[int],
    pct: tuple[np.ndarray, np.ndarray, np.ndarray],
    n: int,
    formula: str,
    ids: Sequence[str],
    sources: Sequence[str],
) -> AnnualTable:
    """An :class:`AnnualTable` from its P10 / P50 / P90 and the number of draws ``n``."""
    p10, p50, p90 = pct
    return AnnualTable(
        level=level,
        geoids=tuple(map(str, geoids)),
        years=tuple(int(y) for y in years),
        p10=p10,
        p50=p50,
        p90=p90,
        formula=f"P10, P50, P90 of {n:,} seeded draws, per {level} and year, of {formula}",
        assumption_ids=tuple(sorted(set(ids))),
        source_ids=tuple(sorted(set(sources))),
    )


# ---------------------------------------------------------------- year shares


def reform_year_shares(
    family: str,
    ledger: Ledger,
    n: int,
    seed: int,
    given: np.ndarray | None = None,
    horizon: int | None = None,
    rules: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Each year's share of a reform's five-year total, per draw, and the ledger ids behind it.

    A tract-level reform module works its shares out with its take-up (``given``).
    For the others the engine draws the family's start delay and ramp entries
    (``timing`` in ``assumptions/outcome.yaml``) from the run's seeded streams.

    Args:
        family: Reform family (``"adu"``, ``"fees"``, ...).
        ledger: The ledger the reform used.
        n: Number of draws.
        seed: The run's seed.
        given: Shares the reform module computed, ``(draws, years)``, or ``None``.
        horizon: Years in the window (default: the ledger's ``baseline.horizon_years``).
        rules: ``assumptions/outcome.yaml`` (default: read it).

    Returns:
        ``(shares (draws, years), assumption ids)``. Rows add to 1.
    """
    rules = rules if rules is not None else load_rules()
    timing = dict(rules.get("timing", {}).get(family, {}))
    ids = tuple(
        timing[k] for k in ("start_delay", "first_year_share", "years_to_full") if k in timing
    )
    ids = tuple(i for i in ids if i in ledger)
    if given is not None:
        return np.asarray(given, dtype=float), ids
    h = int(horizon if horizon is not None else ledger["baseline.horizon_years"].central)
    if not timing or timing.get("first_year_share") not in ledger:
        return np.full((n, h), 1.0 / h), ()
    v = sample(ledger, n=n, seed=seed, ids=list(ids))
    delay = v.get(str(timing.get("start_delay")), np.zeros(n))
    ramp = ramp_sum(v[timing["first_year_share"]], v[timing["years_to_full"]], h, delay)
    return year_shares(ramp), ids


def baseline_year_shares(
    annual: pd.DataFrame, county_ids: Sequence[str], years: Sequence[int]
) -> np.ndarray:
    """Each county's yearly share of its five-year point projection, ``(counties, years)``.

    A county whose projection is zero (or that is not in the table) gets equal
    shares; its total is zero, so its years are zero either way.
    """
    h = len(years)
    out = np.full((len(county_ids), h), 1.0 / h)
    if annual is None or annual.empty:
        return out
    table = annual.assign(county=annual["county"].astype(str)).pivot_table(
        index="county", columns="year", values="units", aggfunc="sum"
    )
    table = table.reindex(index=list(map(str, county_ids)), columns=list(years)).fillna(0.0)
    units = table.to_numpy(dtype=float)
    total = units.sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, units / np.where(total > 0, total, 1.0), out)


# ---------------------------------------------------------------- series


@dataclass(frozen=True)
class AnnualSeries:
    """One quantity (baseline, a reform, the package) split into the years 2027-2031.

    Attributes:
        key: ``"baseline"``, a reform key, ``"package"`` or ``"total"``.
        label: What the values are (for example "added units authorized by
            building permits, ADUs").
        years: Calendar years.
        state: One state record per year.
        counties: County P10 / P50 / P90 per year (:class:`AnnualTable`).
        county_draws: ``(counties, draws, years)``; summed over years it equals
            the five-year county draws exactly.
        state_draws: ``(draws, years)``; summed over years it equals the
            five-year state draws exactly.
        shares: The year shares used, ``(draws, years)`` (a reform) or
            ``(counties, years)`` (the baseline).
        assumption_ids: Ledger ids of the adoption curve (and of the five-year total).
        timing: ``{"mechanism", "shifted_into_window", "newly_feasible", "note"}``:
            whether a mechanism moves authorizations in time, and how many it
            moves (state P10 / P50 / P90 records).
    """

    key: str
    label: str
    years: tuple[int, ...]
    state: tuple[Record, ...]
    counties: AnnualTable
    county_draws: np.ndarray = field(repr=False, compare=False)
    state_draws: np.ndarray = field(repr=False, compare=False)
    shares: np.ndarray = field(repr=False, compare=False)
    assumption_ids: tuple[str, ...] = ()
    timing: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: the yearly state records, the county table and the timing split."""
        return {
            "key": self.key,
            "label": self.label,
            "years": list(self.years),
            "state": [r.model_dump(mode="json") for r in self.state],
            "counties": self.counties.to_dict(),
            "assumption_ids": list(self.assumption_ids),
            "timing": {
                k: (v.model_dump(mode="json") if isinstance(v, Record) else v)
                for k, v in self.timing.items()
            },
        }


def annual_series(
    key: str,
    label: str,
    county_draws: np.ndarray,
    shares: np.ndarray,
    county_ids: Sequence[str],
    years: Sequence[int],
    *,
    expression: str,
    assumption_ids: Sequence[str],
    source_ids: Sequence[str],
    tier: int | None,
    per_county_shares: bool = False,
    timing_note: str | None = None,
    by_year: np.ndarray | None = None,
    how: str | None = None,
) -> AnnualSeries:
    """Split five-year draws into years and summarise them.

    Args:
        key: Series key.
        label: What the values are.
        county_draws: Five-year values, ``(counties, draws)``.
        shares: ``(draws, years)`` (one curve per draw) or, with
            ``per_county_shares``, ``(counties, years)`` (one curve per county).
        county_ids: County ids, in row order.
        years: Calendar years.
        expression: How the five-year value was made (words).
        assumption_ids: Ledger ids.
        source_ids: Sources.
        tier: Data tier.
        per_county_shares: See ``shares``.
        timing_note: Text for the "shifted into the window" split; ``None``
            reads it from ``assumptions/outcome.yaml``.
        by_year: Already split values ``(counties, draws, years)`` (a package:
            each part split by its own curve); ``county_draws`` and ``shares``
            are then ignored except for their labels.
        how: Words for how ``by_year`` was split (with ``by_year``).

    Returns:
        The series. Years add up to the five-year draws in every draw.
    """
    c = np.asarray(county_draws, dtype=float)
    s = np.asarray(shares, dtype=float)
    if by_year is not None:
        by_year = np.asarray(by_year, dtype=float)
        how = how or "each part's own year shares"
    elif per_county_shares:
        by_year = c[:, :, None] * s[:, None, :]
        how = "each county's yearly share of its own point projection"
    else:
        by_year = c[:, :, None] * s[None, :, :]
        how = "the draw's year share ramp(t) / sum of ramp (start delay and ramp)"
    state = by_year.sum(axis=0)
    ids = tuple(sorted(set(assumption_ids)))
    records = tuple(
        rec.from_draws(
            f"{label} in {year}, state",
            state[:, h],
            "units",
            expression=f"({expression}) x {how} for {year}, summed over counties",
            assumption_ids=ids,
            source_ids=source_ids,
            tier=tier,
        )
        for h, year in enumerate(years)
    )
    table = _table(
        "county",
        county_ids,
        years,
        by_year,
        f"({expression}) x {how}",
        ids,
        source_ids,
    )
    note = timing_note
    if note is None:
        note = " ".join(str(load_rules().get("shifted_into_window", {}).get("note", "")).split())
    total = state.sum(axis=1)
    timing: dict[str, Any] = {"mechanism": False, "note": note}
    if key not in ("baseline", "total"):
        zero = rec.from_draws(
            f"{label}: authorizations shifted into 2027-{years[-1]}, state",
            np.zeros_like(total),
            "units",
            expression="no mechanism in this reform moves a permit in time, so 0",
            tier=tier,
        )
        newly = rec.from_draws(
            f"{label}: newly feasible authorizations {years[0]}-{years[-1]}, state",
            total,
            "units",
            expression=f"the five-year total less authorizations shifted into the window (0); "
            f"{expression}",
            assumption_ids=ids,
            source_ids=source_ids,
            tier=tier,
        )
        timing.update(shifted_into_window=zero, newly_feasible=newly)
    out = AnnualSeries(
        key=key,
        label=label,
        years=tuple(int(y) for y in years),
        state=records,
        counties=table,
        county_draws=by_year,
        state_draws=state,
        shares=s,
        assumption_ids=ids,
        timing=timing,
    )
    return out


def tract_table(
    added: np.ndarray,
    shares: np.ndarray,
    geoids: Sequence[str],
    years: Sequence[int],
    *,
    expression: str,
    assumption_ids: Sequence[str],
    source_ids: Sequence[str],
    rows: np.ndarray | None = None,
) -> AnnualTable:
    """Years per tract: ``added (tracts, draws) x shares (draws, years)``, summarised.

    ``rows`` limits the table to some tracts (for example one county's).
    """
    a = np.asarray(added, dtype=float)
    g = np.asarray(list(map(str, geoids)))
    if rows is not None:
        a, g = a[rows], g[rows]
    formula = f"({expression}) x the draw's year share ramp(t) / sum of ramp"
    if a.shape[0] == 0:
        return _table("tract", [], years, np.zeros((0, 1, len(years))), formula, (), ())
    sh = np.asarray(shares, dtype=float)
    # Tracts are summarised a block at a time, so the tracts x draws x years
    # product never exists in full (a large state's would take gigabytes).
    # Each tract's percentiles depend only on its own draws: same numbers.
    parts = [
        np.percentile(a[i : i + TRACT_BLOCK, :, None] * sh[None, :, :], [10, 50, 90], axis=1)
        for i in range(0, a.shape[0], TRACT_BLOCK)
    ]
    p = np.concatenate(parts, axis=1)
    return _summary(
        "tract",
        g.tolist(),
        years,
        (p[0], p[1], p[2]),
        a.shape[1],
        formula,
        assumption_ids,
        source_ids,
    )


# ---------------------------------------------------------------- site events


@dataclass(frozen=True)
class SiteAccount:
    """Gross and net accounts of a set of sites, from one set of event fields.

    Attributes:
        gross_additional: Additional gross authorizations: (policy new units -
            counterfactual new units) x (1 - displacement).
        demolitions_caused: Policy demolitions - counterfactual demolitions.
        net_if_completed: Authorized net change if completed: additional gross
            authorizations - demolitions caused.
    """

    gross_additional: Any
    demolitions_caused: Any
    net_if_completed: Any


def site_account(
    policy_units: Any,
    policy_demolished: Any,
    counterfactual_units: Any,
    counterfactual_demolished: Any,
    displacement: Any = 0.0,
) -> SiteAccount:
    """Gross and net accounts from the events on the same sites in both arms.

    Four event fields, the same in every reform that redevelops sites: the new
    units the policy project authorizes and the homes it demolishes; the new
    units and demolitions the counterfactual (the baseline) would have had on
    the same sites. Displacement takes off the share of added units that would
    have been permitted elsewhere anyway; it moves no demolition.

    ::

        gross additional = (policy units - counterfactual units) x (1 - displacement)
        net if completed = gross additional - (policy demolished - counterfactual demolished)

    Worked cases (the accounting fixture): policy 4 units and 1 demolition
    against a counterfactual of 1 replacement unit and 1 demolition gives 3 and
    3; against a counterfactual that leaves the house untouched (0 and 0) it
    gives 4 and 3. An already-permitted duplex under a two-unit ceiling (2 and
    1 in both arms) gives 0 and 0.

    Works on numbers or numpy arrays of the same shape.
    """
    gross = (np.asarray(policy_units, dtype=float) - counterfactual_units) * (
        1.0 - np.asarray(displacement, dtype=float)
    )
    caused = np.asarray(policy_demolished, dtype=float) - counterfactual_demolished
    return SiteAccount(gross_additional=gross, demolitions_caused=caused,
                       net_if_completed=gross - caused)  # fmt: skip


# ---------------------------------------------------------------- gross and net


def gross_net_records(
    res: Any, county_ids: Sequence[str], added_state: Record, what: str
) -> tuple[dict[str, Record], dict[str, Record]]:
    """State records of added authorizations, net added homes, homes removed, baseline on sites.

    Args:
        res: The reform's ``ReformResult``.
        county_ids: County order.
        added_state: The reform's headline state record (added authorizations).
        what: Reform title, for record names.

    Returns:
        ``(state records by name, county net-stock records by county id)``. A
        reform that removes no home has ``net_stock`` equal to the headline and
        no county records (they would repeat the headline's).
    """
    span = "2027-2031"
    out: dict[str, Record] = {"authorized": added_state}
    if res.net_stock is None:
        out["net_stock"] = added_state.model_copy(
            update={
                "name": f"net added homes {span}, {what}, state",
                "formula": "no home is removed, so net added homes = added units authorized: "
                + added_state.formula,
            }
        )
        return out, {}
    ids = tuple(added_state.assumption_ids)
    module = getattr(res.detail, "records", {})
    extra = tuple(module["net"].assumption_ids) if "net" in module else ()
    ids = tuple(sorted({*ids, *extra}))
    net = np.asarray(res.net_stock, dtype=float)
    out["net_stock"] = rec.from_draws(
        f"net added homes {span}, {what}, state",
        net.sum(axis=0),
        "units",
        expression=(
            "the sum over tracts of added units authorized - (1 - baseline replacement "
            "chance q) x existing homes demolished"
        ),
        assumption_ids=ids,
        source_ids=added_state.source_ids,
        tier=added_state.tier,
    )
    for name in ("removed", "baseline_on_sites"):
        if name in module:
            out[name] = module[name]
    county = res.county_sum(net, tuple(county_ids))
    counties = {
        c: rec.from_draws(
            f"net added homes {span}, {what}, county {c}",
            county[i],
            "units",
            expression="the sum over the county's tracts of added units authorized - (1 - q) "
            "x existing homes demolished",
            assumption_ids=ids,
            source_ids=added_state.source_ids,
            tier=added_state.tier,
        )
        for i, c in enumerate(county_ids)
    }
    return out, counties


# ---------------------------------------------------------------- the five record types


@dataclass(frozen=True)
class Accounting:
    """The five record types every reform result carries (``assumptions/outcome.yaml``).

    Attributes:
        records: Record name to its state record. ``baseline_bps`` (a);
            ``work_<type>``, ``comparable``, ``comparable_with_uncertain`` and
            ``headline_minus_comparable`` (b); ``local_outside_bps`` (c);
            ``net_if_completed``, ``demolitions_caused``, ``removed``,
            ``counterfactual_removed``, ``counterfactual_on_sites`` (d);
            ``timing_shifted_into_window``, ``timing_newly_feasible``,
            ``timing_moved_out_of_window`` (e).
        kinds: Record type (``a`` ... ``e``) to its label, text and record names.
        status: Plain statuses: the local line's capture status, observed
            completions, modelled deliveries, the baseline's flags.
        headline: What the headline counts against the comparable record:
            ``{"is_comparable", "scope", "text"}``.
        work_types: Work type to ``{"label", "bps"}``.
    """

    records: Mapping[str, Record]
    kinds: Mapping[str, Any] = field(default_factory=dict)
    status: Mapping[str, str] = field(default_factory=dict)
    headline: Mapping[str, Any] = field(default_factory=dict)
    work_types: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: records (model dumps), kinds, statuses, headline scope, work types."""
        return {
            "records": {k: r.model_dump(mode="json") for k, r in self.records.items()},
            "kinds": {k: dict(v) for k, v in self.kinds.items()},
            "status": dict(self.status),
            "headline": dict(self.headline),
            "work_types": {k: dict(v) for k, v in self.work_types.items()},
        }


def _words(x: Any) -> str:
    return " ".join(str(x or "").split())


def accounting(
    family: str,
    title: str,
    headline: Record,
    headline_draws: np.ndarray,
    res: Any,
    baseline: Record | None,
    gross_net: Mapping[str, Record],
    annual: AnnualSeries | None,
    *,
    rules: Mapping[str, Any] | None = None,
) -> Accounting:
    """Build the five record types of one reform, every number from its own draws.

    Args:
        family: Reform family (picks the default work type and local records).
        title: Reform title, for record names.
        headline: The reform's headline state record (added units authorized).
        headline_draws: The headline per draw, state, ``(draws,)``.
        res: The reform's ``ReformResult`` (its module detail may carry
            ``work_types``: work type to state draws that add up to the headline).
        baseline: The run's baseline state record, or ``None``.
        gross_net: The reform's gross/net records (:func:`gross_net_records`).
        annual: The reform's yearly series (its timing split), or ``None``.
        rules: ``assumptions/outcome.yaml`` (default: read it).

    Returns:
        The :class:`Accounting`. The work types add up to the headline in every
        draw; ``comparable`` is the sum of the comparable work types per draw;
        no percentile is ever added or divided.
    """
    rules = rules if rules is not None else load_rules()
    kinds_rules = rules.get("records", {})
    types = dict(rules.get("work_types", {}))
    span = "2027-2031"
    head = np.asarray(headline_draws, dtype=float)
    ids = tuple(headline.assumption_ids)
    sources = tuple(headline.source_ids)
    tier = headline.tier
    module = dict(getattr(res, "records", {}) or {})
    detail = getattr(res, "detail", None)
    given = dict(getattr(detail, "work_types", None) or {})
    work_note = ""
    given_sum = sum(np.asarray(v, dtype=float) for v in given.values()) if given else None
    if given and not np.allclose(given_sum, head):
        # The module's work types no longer add up to the headline (a later step
        # changed the headline without them). Never reclassify silently: keep the
        # module's split, scaled per draw to the headline, and say so.
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = np.where(given_sum > 0, head / np.where(given_sum > 0, given_sum, 1.0), 0.0)
        given = {w: np.asarray(v, dtype=float) * scale for w, v in given.items()}
        work_note = (
            "The module's work types did not add up to the headline; each was scaled per "
            "draw by headline / sum of work types, keeping the module's split."
        )
    if given and np.allclose(sum(np.asarray(v, dtype=float) for v in given.values()), head):
        work = {w: np.asarray(v, dtype=float) for w, v in given.items()}
        work_ids = sorted({i for k, r in module.items() if k.startswith("work_")
                           for i in r.assumption_ids} | set(ids))  # fmt: skip
        split = "the module's work-type stages (each tract's units split by work type)"
        if work_note:
            split += " (scaled per draw to the headline)"
    else:
        if given:
            work_note = (
                "The module's work types did not add up to the headline and could not be "
                "scaled (a draw with no work-type units), so every unit takes the family's "
                "default work type."
            )
        default = str(rules.get("family_work_type", {}).get(family, "uncertain"))
        work = {default: head}
        work_ids = sorted(ids)
        split = f"every added unit of this reform is of one work type ({default})"
    out: dict[str, Record] = {}

    # (a) The baseline in the survey's definition.
    if baseline is not None:
        out["baseline_bps"] = baseline.model_copy(
            update={"name": f"baseline BPS-compatible authorizations {span}, state"}
        )

    # (b) Additional comparable authorizations, by work type.
    zero = np.zeros_like(head)
    for w, spec in types.items():
        arr = work.get(w, zero)
        out[f"work_{w}"] = rec.from_draws(
            f"additional authorizations, {spec.get('label', w)}, {span}, {title}, state",
            arr,
            "units",
            expression=(f"added units authorized of this work type: {split}" if w in work
                        else "no added unit of this work type in this reform, so 0"),
            assumption_ids=work_ids if w in work else (),
            source_ids=sources if w in work else (),
            tier=tier,
        )  # fmt: skip

    def total(kind: str) -> np.ndarray:
        return sum((work.get(w, zero) for w, sp in types.items() if sp.get("bps") == kind), zero)

    comparable, uncertain, outside = total("comparable"), total("uncertain"), total("outside")
    names = {k: [w for w, sp in types.items() if sp.get("bps") == k] for k in
             ("comparable", "uncertain", "outside")}  # fmt: skip
    out["comparable"] = rec.from_draws(
        f"additional comparable authorizations {span}, {title}, state",
        comparable,
        "units",
        expression="in each draw, the sum of the work types the Building Permits Survey counts ("
        + ", ".join(names["comparable"])
        + "), policy minus baseline in the same draw",
        assumption_ids=work_ids,
        source_ids=sources,
        tier=tier,
    )
    out["comparable_with_uncertain"] = rec.from_draws(
        f"additional comparable authorizations, upper bound with uncertain classifications "
        f"{span}, {title}, state",
        comparable + uncertain,
        "units",
        expression="in each draw, comparable work types + work types whose classification is "
        "uncertain (" + ", ".join(names["uncertain"]) + ")",
        assumption_ids=work_ids,
        source_ids=sources,
        tier=tier,
    )
    out["headline_minus_comparable"] = rec.from_draws(
        f"headline less additional comparable authorizations {span}, {title}, state",
        head - comparable,
        "units",
        expression="in each draw, added units authorized (the headline) - additional "
        "comparable authorizations (uncertain and outside-the-survey work types)",
        assumption_ids=work_ids,
        source_ids=sources,
        tier=tier,
    )

    # (c) Locally authorized units outside the survey definition (companion line).
    local_from = dict(rules.get("family_local_records", {}).get(family, {}) or {})
    c_rules = kinds_rules.get("local_outside_bps", {})
    if local_from:
        name = next(iter(local_from.values()))
        found = module.get(name)
        if found is not None:
            out["local_outside_bps"] = found.model_copy(
                update={"name": f"additional locally authorized units outside the survey "
                        f"definition {span}, {title}, state (module record {name})"}
            )  # fmt: skip
    if "local_outside_bps" not in out:
        out["local_outside_bps"] = rec.from_draws(
            f"additional locally authorized units outside the survey definition {span}, "
            f"{title}, state",
            outside,
            "units",
            expression="in each draw, the sum of the work types the Building Permits Survey "
            "leaves out (" + ", ".join(names["outside"]) + ")",
            assumption_ids=work_ids if outside.any() else (),
            source_ids=sources if outside.any() else (),
            tier=tier,
        )
    has_local = bool(outside.any()) or bool(local_from)

    # (d) Authorized net change if completed.
    net = gross_net.get("net_stock")
    net_draws = head
    if getattr(res, "net_stock", None) is not None:
        net_draws = np.asarray(res.net_stock, dtype=float).sum(axis=0)
    if net is not None:
        out["net_if_completed"] = net.model_copy(
            update={"name": f"authorized net change if completed {span}, {title}, state"}
        )
    out["demolitions_caused"] = rec.from_draws(
        f"existing homes demolished because of the reform {span}, {title}, state",
        head - net_draws,
        "units",
        expression="in each draw, added units authorized - authorized net change if completed "
        "(policy demolitions - the counterfactual's demolitions on the same sites)",
        assumption_ids=net.assumption_ids if net is not None else ids,
        source_ids=sources,
        tier=tier,
    )
    removed = getattr(res, "removed", None)
    if removed is not None:
        policy = np.asarray(removed, dtype=float).sum(axis=0)
        base_ids = gross_net["removed"].assumption_ids if "removed" in gross_net else ids
        out["removed"] = rec.from_draws(
            f"existing homes the policy projects demolish {span}, {title}, state",
            policy,
            "units",
            expression="the sum over sites of homes demolished by the policy projects",
            assumption_ids=base_ids,
            source_ids=sources,
            tier=tier,
        )
        out["counterfactual_removed"] = rec.from_draws(
            f"existing homes the counterfactual demolishes on the same sites {span}, {title}, "
            "state",
            policy - (head - net_draws),
            "units",
            expression="in each draw, policy demolitions - demolitions caused by the reform",
            assumption_ids=sorted({*base_ids, *(net.assumption_ids if net else ())}),
            source_ids=sources,
            tier=tier,
        )
    if "baseline_on_sites" in gross_net:
        out["counterfactual_on_sites"] = gross_net["baseline_on_sites"].model_copy(
            update={"name": f"counterfactual new units on the same sites {span}, {title}, state"}
        )

    # (e) Completion and timing ledger.
    e_rules = kinds_rules.get("timing", {})
    timing = dict(annual.timing) if annual is not None else {}
    for key in ("shifted_into_window", "newly_feasible"):
        if isinstance(timing.get(key), Record):
            out[f"timing_{key}"] = timing[key]
    out["timing_moved_out_of_window"] = rec.from_draws(
        f"authorizations moved past {span[-4:]} by a timing mechanism, {title}, state",
        zero,
        "units",
        expression="no mechanism in this reform pushes a permit past the window, so 0",
        tier=tier,
    )

    kinds = {}
    groups = {
        "a": ["baseline_bps"],
        "b": [*(f"work_{w}" for w in types), "comparable", "comparable_with_uncertain",
              "headline_minus_comparable"],
        "c": ["local_outside_bps"],
        "d": ["net_if_completed", "demolitions_caused", "removed", "counterfactual_removed",
              "counterfactual_on_sites"],
        "e": ["timing_shifted_into_window", "timing_newly_feasible",
              "timing_moved_out_of_window"],
    }  # fmt: skip
    by_kind = {str(v.get("kind")): k for k, v in kinds_rules.items()}
    for kind, keys in groups.items():
        spec = kinds_rules.get(by_kind.get(kind, ""), {})
        kinds[kind] = {
            "name": by_kind.get(kind, kind),
            "label": _words(spec.get("label")),
            "text": _words(spec.get("text")),
            "records": [k for k in keys if k in out],
        }
    is_comparable = bool(np.allclose(head, comparable))
    scope = "comparable" if is_comparable else "all work types"
    text = (
        "The headline equals the additional comparable authorizations."
        if is_comparable
        else "The headline counts every work type of this reform, including units the "
        "Building Permits Survey may leave out (uncertain classifications and conversions). "
        "The record 'comparable' beside it is the survey-definition effect; the difference, "
        "drawn per draw, is 'headline_minus_comparable'."
    )
    status = {
        "local_outside_bps": _words(
            c_rules.get("status_unknown_capture") if has_local else c_rules.get("status_none")
        ),
        "observed_completions": _words(e_rules.get("observed_completions")),
        "modelled_deliveries": _words(e_rules.get("modelled_deliveries")),
        "moved_out_of_window": _words(e_rules.get("moved_out_note")),
        "baseline_flags": _words(kinds_rules.get("baseline_bps", {}).get("flags")),
    }
    if work_note:
        status["work_types"] = work_note
    return Accounting(
        records=out,
        kinds=kinds,
        status=status,
        headline={"is_comparable": is_comparable, "scope": scope, "text": text},
        work_types={
            w: {"label": _words(sp.get("label")), "bps": str(sp.get("bps"))}
            for w, sp in types.items()
        },
    )


# ---------------------------------------------------------------- vintages and contract


def _pack_folder(state: str) -> Path | None:
    from visionhousing.engine.context import pack_dir

    folder = pack_dir(state)
    return folder if folder.is_dir() else None


def vintages(state: str, as_of: str, *, folder: Path | None = None) -> dict[str, Any]:
    """Data and policy vintages of a state's pack, read from its manifest and law tables.

    Args:
        state: State code.
        as_of: The as-of date (ISO), to flag inputs fetched or checked after it.
        folder: The pack folder to read (default: the state's pack).

    Returns:
        ``{"data": [{source_id, version, access_date, after_as_of}], "policy": {...}}``.
        A pack without a manifest (Testland) lists nothing and says so.
    """
    folder = Path(folder) if folder is not None and Path(folder).is_dir() else _pack_folder(state)
    data: list[dict[str, Any]] = []
    manifest = None if folder is None else folder / "pack_manifest.json"
    if manifest is not None and manifest.is_file():
        rows = json.loads(manifest.read_text(encoding="utf-8")).get("provenance", [])
        for row in rows:
            access = row.get("access_date")
            data.append(
                {
                    "source_id": row.get("id"),
                    "version": row.get("version"),
                    "access_date": access,
                    "after_as_of": bool(_ISO.match(str(access or ""))) and str(access)[:10] > as_of,
                }
            )
    policy = _policy_vintage(folder, as_of)
    note = (
        ""
        if data
        else "This pack has no manifest (a synthetic or hand-built pack); no vintages are listed."
    )
    return {"data": data, "policy": policy, "note": note}


def _policy_vintage(folder: Path | None, as_of: str) -> dict[str, Any]:
    """When the state and municipal law tables were last checked, and pending or future rules."""
    out: dict[str, Any] = {
        "law_snapshot_date": None,
        "state_provisions_checked": None,
        "municipal_rules_reviewed": None,
        "enacted_future_effective": [],
        "pending_bills": [],
    }
    if folder is None:
        out["note"] = "No law tables in this pack: current law is the ledger's statewide values."
        return out
    prov = _read_table(folder, "state_law_provisions")
    muni = _read_table(folder, "muni_zoning_rules")
    dates = []
    if prov is not None and "checked_on" in prov:
        checked = prov["checked_on"].dropna().astype(str)
        if len(checked):
            out["state_provisions_checked"] = checked.max()
            dates.append(checked.max())
        status = prov.get("status", pd.Series(dtype=str)).astype(str)
        for _, row in prov[status.str.lower() != "enacted"].iterrows():
            out["pending_bills"].append(
                {
                    "provision_id": str(row.get("provision_id")),
                    "bill_id": None if pd.isna(row.get("bill_id")) else str(row.get("bill_id")),
                    "status": str(row.get("status")),
                    "last_action_date": None
                    if pd.isna(row.get("last_action_date"))
                    else str(row.get("last_action_date")),
                }
            )
        if "effective_date" in prov:
            eff = prov[(status.str.lower() == "enacted") & prov["effective_date"].notna()]
            for _, row in eff.iterrows():
                if str(row["effective_date"]) > as_of:
                    out["enacted_future_effective"].append(
                        {
                            "provision_id": str(row.get("provision_id")),
                            "effective_date": str(row["effective_date"]),
                        }
                    )
    if muni is not None and "reviewed_on" in muni:
        reviewed = muni["reviewed_on"].dropna().astype(str)
        if len(reviewed):
            out["municipal_rules_reviewed"] = reviewed.max()
            dates.append(reviewed.max())
    out["law_snapshot_date"] = max(dates) if dates else None
    out["note"] = (
        "Bills not enacted by the as-of date are scenarios, never baseline."
        if out["pending_bills"]
        else ""
    )
    return out


def _read_table(folder: Path, name: str) -> pd.DataFrame | None:
    for candidate in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
        if candidate.is_file():
            return pd.read_csv(candidate, dtype=str)
    return None


def contract(
    state: str,
    reform_families: Mapping[str, str],
    *,
    baseline_correction: Mapping[str, Any] | None = None,
    synthetic: bool = False,
    rules: Mapping[str, Any] | None = None,
    folder: Path | None = None,
) -> dict[str, Any]:
    """The outcome contract a run carries (JSON-ready).

    Args:
        state: State code.
        reform_families: Reform key to its family, for the reforms in the run.
        baseline_correction: The bias-correction decision the baseline applied.
        synthetic: True for Testland.
        rules: ``assumptions/outcome.yaml`` (default: read it).
        folder: The pack folder the context was read from (default: the state's pack).

    Returns:
        ``{as_of, measure, window, vintages, effective_dates, hold_constant,
        audit, baseline_correction}``.
    """
    rules = rules if rules is not None else load_rules()
    as_of = str(rules["as_of"])
    measure = dict(rules["measure"])
    audit = rules.get("audit", {})

    def words(x: Any) -> Any:
        return " ".join(x.split()) if isinstance(x, str) else x

    rows = {"baseline": {k: words(v) for k, v in audit.get("baseline", {}).items()}}
    for key, fam in reform_families.items():
        rows[key] = {k: words(v) for k, v in audit.get(fam, {}).items()}
    eff = dict(rules.get("effective_dates", {}))
    hold = dict(rules.get("hold_constant", {}))
    return {
        "as_of": as_of,
        "measure": {k: words(v) for k, v in measure.items()},
        "window": "2027-2031",
        "vintages": vintages(state, as_of, folder=folder)
        if not synthetic
        else {"data": [], "policy": {}, "note": "Testland is synthetic; it has no vintages."},
        "effective_dates": {
            "rule": words(eff.get("rule", "")),
            "start_delay_ids": dict(eff.get("start_delay_ids", {})),
        },
        "hold_constant": {
            "text": words(hold.get("text", "")),
            "assumption_ids": list(hold.get("assumption_ids", [])),
        },
        "audit": rows,
        "baseline_correction": dict(baseline_correction or {}),
    }
