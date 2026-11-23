"""Reform 1, accessory dwelling units (ADUs): added units by tract, as a funnel.

Plain-language summary
----------------------
The reform lets an owner add one small home (an ADU) on a lot that already has
a single-family house. This module estimates how many ADUs would be added in
each census tract over the five years 2027-2031, as a likely range.

It works as a funnel. Each step keeps a share of the lots from the step before:

1. **Eligible lots** ``E``: lots with a home of a kind the scenario reaches
   (census units-in-structure counts, turned into lots), scaled down by the
   census-to-real-lot correction, minus lots where an ADU is already
   effectively legal, minus places the scenario leaves out.
2. **Lots with room** = ``E x f``. ``f`` is the share of lots with room for a
   600 sq ft unit, by kind of home and area type, from the calibration table
   ``assumptions/calibration/adu_fit.yaml``. Attached homes have a far lower ``f``.
3. **Feasible lots** = lots with room ``x p``. ``p`` is the share where the
   ADU pays: the tract's distribution of gross rents (published ACS brackets)
   is compared with the pro forma's break-even rent, using
   :func:`visionhousing.features.brackets.share_above_many`. Owners who build
   for family use whatever the rent are added: ``p = p_rent + s x (1 - p_rent)``.
4. **ADUs built** = feasible lots ``x h x sum of ramp(t) x n``. ``h`` is the
   yearly take-up among feasible lots, ``ramp(t)`` scales the first years
   (permits climb after a reform), ``n = 1`` home per ADU.
5. **Net added units** = ADUs built ``x (1 - x)``; ``x`` is the share that
   would have been built anyway (displacement).

Every ledger value is drawn many times (Monte Carlo, seeded), so every step
is a likely range: P10, P50 and P90. All arrays are draws x tracts.

Where the rent test meets the brackets
--------------------------------------
An ADU on a lot is assumed to rent like a one-bedroom there, priced above the
census median because it is new:
``ADU rent = gross rent x one-bedroom ratio x new-unit premium``. So an ADU pays
where the tract's gross rent is at or above
``break-even rent / (one-bedroom ratio x new-unit premium)``, and ``p_rent`` is
the share of the tract's cash renters above that line. The one-bedroom ratio is
the tract's own ``median_rent_b1 / median_gross_rent`` where both exist, else
the ledger's ``adu.rent_ratio_1br``. A tract with no cash renters in the brackets
falls back to its one-bedroom median: ``p_rent`` is 1 if
``median_rent_b1 x premium`` covers the break-even, else 0.

Scenario hooks
--------------
:func:`scope_from_scenario` reads a scenario from ``assumptions/scenarios.yaml``:
the benchmark (detached and attached single-family lots), HB 2186 (adds 2-4 unit
lots) and SB 1346 (detached only; Philadelphia and Pittsburgh left out; its
zero connection fee is a ledger override in the scenario file). Pittsburgh is a
city inside Allegheny County, so until the tract-to-municipality split exists
it is removed as a documented share of every Allegheny tract
(``adu.pittsburgh_share_of_allegheny_lots``, flagged as a stub).

Files and purity
----------------
:func:`load_fit_table` and :func:`load_legal_share` read the two calibration
files. :func:`adu_funnel` is pure: tables and ledger in, result out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.features import brackets as br
from visionhousing.ledger.loader import ROOT, Ledger, LedgerError, read_yaml
from visionhousing.ledger.sampling import sample
from visionhousing.ledger.scenarios import Scenario
from visionhousing.proforma import adu_proforma
from visionhousing.proforma.adu import INPUTS as PROFORMA_INPUTS

__all__ = [
    "AREA_TYPES",
    "BUILDING_TYPES",
    "FIT_PATH",
    "LEGAL_PATH",
    "STAGES",
    "AduResult",
    "AduScope",
    "FitCell",
    "FitTable",
    "LegalShare",
    "PlaceScope",
    "adu_funnel",
    "legal_share_override",
    "load_fit_table",
    "load_legal_share",
    "parse_fit_table",
    "parse_legal_share",
    "ramp_sum",
    "scope_from_scenario",
]

FIT_PATH = ROOT / "assumptions" / "calibration" / "adu_fit.yaml"
LEGAL_PATH = ROOT / "assumptions" / "calibration" / "adu_legal_share.yaml"

BUILDING_TYPES = ("sf_detached", "sf_attached", "two_to_four")
"""Kinds of existing lots an ADU scenario can reach (names as in scenarios.yaml)."""

AREA_TYPES = ("urban", "suburban", "rural")
"""Tract area types used by the fit table."""

BASES = ("measured", "partly_measured", "extrapolated")
"""How a fit cell was obtained."""

FALLBACK_FIT = {
    "sf_detached": "adu.fit_share_sf_detached",
    "sf_attached": "adu.fit_share_sf_attached",
    "two_to_four": "adu.fit_share_two_to_four",
}
"""Ledger entries used for a tract whose area type is not in the fit table."""

STAGES = ("eligible", "fit", "feasible", "built", "net")
"""Funnel stages, in order."""

STAGE_NAMES = {
    "eligible": "eligible lots",
    "fit": "lots with room for an ADU",
    "feasible": "lots where an ADU is feasible",
    "built": "ADUs built {years}",
    "net": "net added units {years}",
}

STAGE_UNITS = {
    "eligible": "lots",
    "fit": "lots",
    "feasible": "lots",
    "built": "units",
    "net": "units",
}

DATA_SOURCES = {
    "eligible": ("acs5_summary_file",),
    "feasible": ("acs5_summary_file",),
}
"""Registry ids of the data behind each stage (ACS units in structure B25024;
gross rent B25063 and B25064; rent by bedrooms B25031)."""

LOT_PARAMS = (
    "adu.lot_proxy_adjustment",
    "adu.homes_per_building_two_family",
    "adu.homes_per_building_three_four",
)
LEGAL_PARAM = "adu.already_legal_share"
FIT_PARAM = "adu.fit_range_position"
RENT_PARAMS = ("adu.rent_ratio_1br", "adu.new_unit_rent_premium")
NONFIN_PARAM = "adu.nonfinancial_share"
TAKEUP_PARAMS = (
    "adu.takeup_feasible_annual",
    "adu.ramp_first_year_share",
    "adu.ramp_years_to_full",
    "adu.units_per_lot",
)
DISPLACEMENT_PARAM = "adu.displacement_share"
YEAR_PARAMS = ("baseline.first_year", "baseline.horizon_years")
EVIDENCE_PARAM = "adu.evidence_permits_per_100k_permissive_outside_ca"

COST_FACTOR_COLUMN = "cost_factor"
"""Optional tract column: regional construction cost factor (1 = average)."""

_CHUNK_ROWS = 200_000
"""Largest draws x tracts block handed to the bracket function at once."""


# ---------------------------------------------------------------- calibration tables


@dataclass(frozen=True)
class FitCell:
    """One cell of the fit table.

    Attributes:
        central: Central share of lots with room.
        low: Likely low.
        high: Likely high.
        basis: ``measured``, ``partly_measured`` or ``extrapolated``.
        evidence: Plain statement of where the numbers come from.
    """

    central: float
    low: float
    high: float
    basis: str
    evidence: str


@dataclass(frozen=True)
class FitTable:
    """The physical-fit calibration table.

    Attributes:
        id: Table id, carried into result records as a source id.
        cells: ``(building type, area type)`` to its cell.
        provenance: The plain provenance sentence.
        source_ids: Ids of the data sources behind the measurements.
    """

    id: str
    cells: dict[tuple[str, str], FitCell]
    provenance: str
    source_ids: tuple[str, ...]

    def shares(
        self, building_type: str, area_types: np.ndarray, position: np.ndarray
    ) -> np.ndarray:
        """Return fit shares for one building type, draws x tracts.

        ``position`` p in [-1, 1] maps to ``central + p (high - central)`` when
        p >= 0 and ``central + p (central - low)`` when p < 0.

        Args:
            building_type: One of :data:`BUILDING_TYPES`.
            area_types: Area type per tract, shape ``(tracts,)``.
            position: One position per draw, shape ``(draws,)``.

        Returns:
            Shares, shape ``(draws, tracts)``. ``NaN`` for an area type the
            table does not hold.
        """
        pos = np.asarray(position, dtype=float)[:, None]
        out = np.full((pos.shape[0], len(area_types)), np.nan)
        for area in AREA_TYPES:
            cell = self.cells.get((building_type, area))
            mask = np.asarray(area_types) == area
            if cell is None or not mask.any():
                continue
            up = cell.central + pos * (cell.high - cell.central)
            down = cell.central + pos * (cell.central - cell.low)
            value = np.where(pos >= 0, up, down)
            out[:, mask] = np.broadcast_to(value, (pos.shape[0], 1))
        return out


def parse_fit_table(data: Any, name: str = "adu_fit.yaml") -> FitTable:
    """Check parsed fit-table data and build a :class:`FitTable`.

    Args:
        data: The parsed YAML.
        name: File name for error messages.

    Returns:
        The table.

    Raises:
        LedgerError: A cell is missing, out of order or outside 0..1.
    """
    if not isinstance(data, dict) or not isinstance(data.get("cells"), dict):
        raise LedgerError(f"{name}: expected a 'cells' mapping")
    prov = data.get("provenance") or {}
    summary = str(prov.get("summary", "")).strip()
    if not summary:
        raise LedgerError(f"{name}: needs provenance.summary")
    cells: dict[tuple[str, str], FitCell] = {}
    problems = []
    for btype in BUILDING_TYPES:
        for area in AREA_TYPES:
            raw = (data["cells"].get(btype) or {}).get(area)
            where = f"{btype}.{area}"
            if not isinstance(raw, dict):
                problems.append(f"{where}: missing")
                continue
            try:
                cell = FitCell(
                    central=float(raw["central"]),
                    low=float(raw["low"]),
                    high=float(raw["high"]),
                    basis=str(raw["basis"]),
                    evidence=str(raw.get("evidence", "")).strip(),
                )
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"{where}: {exc}")
                continue
            if not 0.0 <= cell.low <= cell.central <= cell.high <= 1.0:
                problems.append(f"{where}: need 0 <= low <= central <= high <= 1")
            if cell.basis not in BASES:
                problems.append(f"{where}: basis must be one of {BASES}")
            if not cell.evidence:
                problems.append(f"{where}: needs an evidence note")
            cells[(btype, area)] = cell
    if problems:
        raise LedgerError(f"{name}: " + " | ".join(problems))
    source_ids = tuple(str(s["id"]) for s in prov.get("sources", ()))
    return FitTable(
        id=str(data.get("id", "adu_fit")),
        cells=cells,
        provenance=" ".join(summary.split()),
        source_ids=source_ids,
    )


def load_fit_table(path: Path | str | None = None) -> FitTable:
    """Load ``assumptions/calibration/adu_fit.yaml`` (or another file)."""
    path = Path(path) if path is not None else FIT_PATH
    return parse_fit_table(read_yaml(path), name=path.name)


@dataclass(frozen=True)
class PlaceScope:
    """A place a scenario treats differently, mapped to tracts.

    Attributes:
        name: Place name, as in ``excluded_places`` of a scenario.
        county: County code whose tracts hold the place.
        tract_share: Share of each of those tracts' lots inside the place.
        tract_share_parameter: Ledger id giving that share instead (drawn).
    """

    name: str
    county: str
    tract_share: float | None = None
    tract_share_parameter: str | None = None


@dataclass(frozen=True)
class LegalShare:
    """The already-legal share for one state, and the bills' place scope.

    Attributes:
        id: Table id.
        state: State code.
        parameter: The ledger id it overrides.
        central: Central share.
        low: Likely low.
        high: Likely high.
        shape: Range shape.
        how: Plain sentences on how each end was built.
        places: Place name to its scope.
    """

    id: str
    state: str
    parameter: str
    central: float
    low: float
    high: float
    shape: str
    how: str
    places: dict[str, PlaceScope] = field(default_factory=dict)


def parse_legal_share(data: Any, name: str = "adu_legal_share.yaml") -> LegalShare:
    """Check parsed legal-share data and build a :class:`LegalShare`.

    Raises:
        LedgerError: The range is missing or out of order, or a place is malformed.
    """
    try:
        rng = data["range"]
        low, central, high = float(rng["low"]), float(rng["central"]), float(rng["high"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerError(f"{name}: needs range.low, range.central and range.high") from exc
    if not 0.0 <= low <= central <= high <= 1.0:
        raise LedgerError(f"{name}: need 0 <= low <= central <= high <= 1")
    places = {}
    for pname, raw in (data.get("place_scope") or {}).items():
        share = raw.get("tract_share")
        param = raw.get("tract_share_parameter")
        if (share is None) == (param is None):
            raise LedgerError(f"{name}: place '{pname}' needs tract_share or a parameter, not both")
        places[pname] = PlaceScope(
            name=pname,
            county=str(raw["county"]),
            tract_share=None if share is None else float(share),
            tract_share_parameter=param,
        )
    how = " ".join(" ".join(str(v).split()) for v in (rng.get("how") or {}).values())
    return LegalShare(
        id=str(data.get("id", "adu_legal_share")),
        state=str(data.get("state", "")),
        parameter=str(data.get("parameter", LEGAL_PARAM)),
        central=central,
        low=low,
        high=high,
        shape=str(rng.get("shape", "pert")),
        how=how,
        places=places,
    )


def load_legal_share(path: Path | str | None = None) -> LegalShare:
    """Load ``assumptions/calibration/adu_legal_share.yaml`` (or another file)."""
    path = Path(path) if path is not None else LEGAL_PATH
    return parse_legal_share(read_yaml(path), name=path.name)


def legal_share_override(legal: LegalShare) -> dict[str, dict[str, Any]]:
    """Return the ledger override that puts the state's already-legal range in place.

    Apply it with :func:`visionhousing.ledger.apply_overrides` for a run of
    ``legal.state``. The ledger entry stays the knob users can change.

    Args:
        legal: The state's legal-share table.

    Returns:
        ``{parameter id: fields to replace}``.
    """
    return {
        legal.parameter: {
            "central": legal.central,
            "low": legal.low,
            "high": legal.high,
            "shape": legal.shape,
            "kind": "calibrated",
            "rationale": f"From calibration table {legal.id}. {legal.how}",
        }
    }


# ---------------------------------------------------------------- scenario hooks


@dataclass(frozen=True)
class AduScope:
    """What an ADU scenario reaches.

    Attributes:
        name: Scenario id.
        building_types: Kinds of lots reached.
        excluded: Places left out.
    """

    name: str
    building_types: tuple[str, ...]
    excluded: tuple[PlaceScope, ...] = ()

    def __post_init__(self) -> None:
        """Check the building types."""
        unknown = sorted(set(self.building_types) - set(BUILDING_TYPES))
        if unknown or not self.building_types:
            raise ValueError(f"ADU scope needs building types from {BUILDING_TYPES}")


def scope_from_scenario(
    scenario_id: str, scenario: Scenario, legal: LegalShare | None = None
) -> AduScope:
    """Turn a scenario from ``scenarios.yaml`` into an ADU scope.

    Args:
        scenario_id: The scenario id, for example ``"pa_sb1346"``.
        scenario: The checked scenario.
        legal: The table holding the place scope; needed if the scenario
            excludes places.

    Returns:
        The scope. ``five_plus`` lots are never reached by an ADU scenario.

    Raises:
        ValueError: The scenario does not turn on the ADU reform, or names a
            place the table does not map.
    """
    if "adu" not in scenario.reforms:
        raise ValueError(f"scenario '{scenario_id}' does not turn on the ADU reform")
    types = tuple(t for t in scenario.building_types if t in BUILDING_TYPES)
    excluded = []
    for place in scenario.excluded_places:
        if legal is None or place.name not in legal.places:
            raise ValueError(f"scenario '{scenario_id}': no tract mapping for '{place.name}'")
        excluded.append(legal.places[place.name])
    return AduScope(name=scenario_id, building_types=types, excluded=tuple(excluded))


# ---------------------------------------------------------------- the funnel


def ramp_sum(first_year_share: Any, years_to_full: Any, horizon: int) -> np.ndarray:
    """Sum of the yearly ramp factors over the horizon.

    ``ramp(t) = min(1, r1 + (1 - r1) (t - 1) / (Y - 1))`` for year t = 1..horizon,
    with ``r1`` the first-year share and ``Y`` the years to the full rate. With
    ``Y <= 1`` every year is at the full rate.

    Args:
        first_year_share: ``r1`` (number or array of draws).
        years_to_full: ``Y`` (number or array of draws).
        horizon: Number of years.

    Returns:
        ``(..., horizon)`` array of yearly factors; sum the last axis for the total.
    """
    r1 = np.asarray(first_year_share, dtype=float)[..., None]
    y = np.asarray(years_to_full, dtype=float)[..., None]
    t = np.arange(1, horizon + 1, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        climb = r1 + (1.0 - r1) * (t - 1.0) / (y - 1.0)
    return np.where(y <= 1.0, 1.0, np.clip(climb, 0.0, 1.0))


@dataclass(frozen=True)
class AduResult:
    """What :func:`adu_funnel` returns.

    Attributes:
        scope: The scenario's scope.
        geoids: Tract codes, shape ``(tracts,)``.
        counties: County code per tract.
        stages: Stage name to draws x tracts arrays (see :data:`STAGES`).
        feasible_share: ``p`` per draw and tract.
        breakeven_rent: Break-even monthly rent per draw (and tract).
        years: Calendar years counted.
        net_by_year: State net added units per draw and year, ``(draws, years)``.
        records: State-total records: one per stage, plus ``per_100k``.
        seed: Seed used.
    """

    scope: AduScope
    geoids: np.ndarray
    counties: np.ndarray
    stages: dict[str, np.ndarray]
    feasible_share: np.ndarray
    breakeven_rent: np.ndarray
    years: tuple[int, ...]
    net_by_year: np.ndarray
    records: dict[str, Record]
    seed: int

    def funnel(self) -> list[Record]:
        """Return the state funnel: eligible, fit, feasible, built, net."""
        return [self.records[s] for s in STAGES]

    def tract_table(self) -> pd.DataFrame:
        """Return P10 / P50 / P90 of every stage for every tract."""
        out = pd.DataFrame({"geoid": self.geoids, "county": self.counties})
        for stage in STAGES:
            p10, p50, p90 = np.percentile(self.stages[stage], [10, 50, 90], axis=0)
            out[f"{stage}_p10"], out[f"{stage}_p50"], out[f"{stage}_p90"] = p10, p50, p90
        return out

    def county_records(self, stage: str = "net") -> dict[str, Record]:
        """Return one record per county for a stage, summed over its tracts per draw."""
        base = self.records[stage]
        out = {}
        for county in sorted(set(self.counties)):
            draws = self.stages[stage][:, self.counties == county].sum(axis=1)
            out[county] = rec.from_draws(
                f"{_label(stage, self.years)}, county {county}",
                draws,
                base.unit,
                expression=f"the sum over the county's tracts of {_expression(stage, self.years)}",
                assumption_ids=base.assumption_ids,
                source_ids=base.source_ids,
                tier=base.tier,
            )
        return out


_EXPRESSIONS = {
    "eligible": (
        "eligible lots = sum over building types in scope of [census homes of that type "
        "/ homes per building] x lot-count correction x (1 - already-legal share) "
        "x share of the tract inside the scenario's places"
    ),
    "fit": (
        "lots with room = sum over building types of eligible lots x fit share f(type, area type)"
    ),
    "feasible": (
        "feasible lots = lots with room x p, where p = p_rent + family-use share x (1 - p_rent) "
        "and p_rent = share of the tract's cash rents at or above break-even rent / "
        "(one-bedroom ratio x new-unit premium)"
    ),
    "built": (
        "ADUs built = feasible lots x yearly take-up h x sum over {years} of ramp(t) "
        "x ADUs per lot n"
    ),
    "net": "net added units = ADUs built x (1 - displacement share x)",
}


def _period(years: Sequence[int]) -> str:
    return f"{years[0]}-{years[-1]}" if years else ""


def _label(stage: str, years: Sequence[int]) -> str:
    return STAGE_NAMES[stage].format(years=_period(years))


def _expression(stage: str, years: Sequence[int]) -> str:
    return _EXPRESSIONS[stage].format(years=_period(years))


def _column(tracts: pd.DataFrame, name: str) -> np.ndarray:
    if name not in tracts:
        raise KeyError(f"tract table needs column '{name}'")
    return tracts[name].to_numpy(dtype=float)


def _rent_share(
    counts: np.ndarray, threshold: np.ndarray, *, top_rule: str = "lower_bound"
) -> np.ndarray:
    """Share of cash rents at or above ``threshold`` (draws x tracts), via brackets."""
    draws, tracts = threshold.shape
    out = np.empty((draws, tracts))
    step = max(1, _CHUNK_ROWS // max(tracts, 1))
    for start in range(0, draws, step):
        block = threshold[start : start + step]
        rows = block.shape[0]
        res = br.share_above_many(
            np.tile(counts, (rows, 1)), br.RENT, block.reshape(-1), top_rule=top_rule
        )
        out[start : start + rows] = res.value.reshape(rows, tracts)
    return out


def _needed_ids(scope: AduScope, ledger: Ledger) -> list[str]:
    ids = [
        *LOT_PARAMS,
        LEGAL_PARAM,
        FIT_PARAM,
        *FALLBACK_FIT.values(),
        *RENT_PARAMS,
        NONFIN_PARAM,
        *TAKEUP_PARAMS,
        DISPLACEMENT_PARAM,
        *(i.key for i in PROFORMA_INPUTS if i.is_parameter),
    ]
    ids += [p.tract_share_parameter for p in scope.excluded if p.tract_share_parameter]
    for pid in ids:
        ledger[pid]  # raises a clear LedgerError if missing
    return list(dict.fromkeys(ids))


def adu_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    fit: FitTable,
    scope: AduScope,
    *,
    draws: int = 2000,
    seed: int = 0,
) -> AduResult:
    """Run the ADU funnel for every tract and every Monte Carlo draw.

    Args:
        tracts: Tract table in the state-pack schema (Testland column names):
            ``geoid``, ``county``, ``area_type``, ``population``,
            ``units_1_detached``, ``units_1_attached``, ``units_2``,
            ``units_3_4``, ``median_gross_rent``, ``median_rent_b1`` and the
            gross-rent bracket columns. Optional ``cost_factor``.
        ledger: The ledger, with the scenario's overrides already applied
            (``scenario_ledger``) and, for Pennsylvania, the legal-share override.
        fit: The fit calibration table.
        scope: What the scenario reaches (:func:`scope_from_scenario`).
        draws: Number of Monte Carlo draws.
        seed: Seed; the same seed gives the same result.

    Returns:
        An :class:`AduResult`.

    Raises:
        KeyError: A tract column is missing.
        LedgerError: A ledger parameter is missing.
    """
    ids = _needed_ids(scope, ledger)
    v = sample(ledger, n=draws, seed=seed, ids=ids)
    col = {k: a[:, None] for k, a in v.items()}  # (draws, 1) columns
    n_tracts = len(tracts)
    first_year = int(ledger[YEAR_PARAMS[0]].central)
    horizon = int(ledger[YEAR_PARAMS[1]].central)
    years = tuple(range(first_year, first_year + horizon))

    # 1. Eligible lots, per building type.
    adj = col["adu.lot_proxy_adjustment"]
    homes = {
        "sf_detached": _column(tracts, "units_1_detached")[None, :],
        "sf_attached": _column(tracts, "units_1_attached")[None, :],
        "two_to_four": _column(tracts, "units_2")[None, :]
        / col["adu.homes_per_building_two_family"]
        + _column(tracts, "units_3_4")[None, :] / col["adu.homes_per_building_three_four"],
    }
    counties = tracts["county"].astype(str).to_numpy()
    inside = np.ones((draws, n_tracts))
    for place in scope.excluded:
        share = (
            col[place.tract_share_parameter]
            if place.tract_share_parameter
            else np.full((draws, 1), place.tract_share)
        )
        inside = np.where(counties[None, :] == place.county, inside * (1.0 - share), inside)
    keep = adj * (1.0 - col[LEGAL_PARAM]) * inside
    eligible_by_type = {t: np.clip(homes[t], 0, None) * keep for t in scope.building_types}

    # 2. Lots with room.
    area = (
        tracts["area_type"].astype(str).to_numpy()
        if "area_type" in tracts
        else np.array([""] * n_tracts)
    )
    unknown_area = ~np.isin(area, AREA_TYPES)
    fit_lots = np.zeros((draws, n_tracts))
    for t, lots in eligible_by_type.items():
        f = fit.shares(t, area, v[FIT_PARAM])
        f = np.where(np.isnan(f), col[FALLBACK_FIT[t]], f)
        fit_lots = fit_lots + lots * f
    eligible = sum(eligible_by_type.values())

    # 3. Feasible share p.
    gross = _column(tracts, "median_gross_rent")
    b1 = _column(tracts, "median_rent_b1")
    with np.errstate(divide="ignore", invalid="ignore"):
        own_ratio = b1 / gross
    has_ratio = np.isfinite(own_ratio) & (own_ratio > 0)
    ratio = np.where(has_ratio[None, :], own_ratio[None, :], col["adu.rent_ratio_1br"])
    premium = col["adu.new_unit_rent_premium"]
    cost_factor = (
        _column(tracts, COST_FACTOR_COLUMN)[None, :] if COST_FACTOR_COLUMN in tracts else 1.0
    )
    proforma = adu_proforma(
        {k: col[k] for k in ids},
        {
            "rent_monthly": np.nan_to_num(gross)[None, :] * ratio * premium,
            "cost_factor": cost_factor,
        },
    )
    breakeven = np.broadcast_to(proforma.lines["breakeven_rent"], (draws, n_tracts))
    counts = tracts[list(br.RENT.columns)].to_numpy(dtype=float)
    p_rent = _rent_share(counts, breakeven / (ratio * premium))
    fallback = np.where(np.isfinite(b1)[None, :] & (b1[None, :] * premium >= breakeven), 1.0, 0.0)
    p_rent = np.where(np.isnan(p_rent), fallback, p_rent)
    family = col[NONFIN_PARAM]
    p = p_rent + family * (1.0 - p_rent)
    feasible = fit_lots * p

    # 4-5. Take-up, ramp, units, displacement.
    ramp = ramp_sum(v["adu.ramp_first_year_share"], v["adu.ramp_years_to_full"], horizon)
    yearly = col["adu.takeup_feasible_annual"] * col["adu.units_per_lot"]  # (draws, 1)
    built = np.minimum(feasible * yearly * ramp.sum(axis=1)[:, None], feasible)
    net = built * (1.0 - col[DISPLACEMENT_PARAM])
    state_feasible = feasible.sum(axis=1)
    net_by_year = state_feasible[:, None] * yearly * ramp * (1.0 - col[DISPLACEMENT_PARAM])

    stages = {
        "eligible": eligible,
        "fit": fit_lots,
        "feasible": feasible,
        "built": built,
        "net": net,
    }
    population = float(np.nansum(_column(tracts, "population")))
    if not unknown_area.any():
        ids = [i for i in ids if i not in FALLBACK_FIT.values()]
    records = _state_records(stages, ledger, fit, scope, ids, population, years)
    return AduResult(
        scope=scope,
        geoids=tracts["geoid"].astype(str).to_numpy(),
        counties=counties,
        stages=stages,
        feasible_share=p,
        breakeven_rent=breakeven,
        years=years,
        net_by_year=net_by_year,
        records=records,
        seed=seed,
    )


def _stage_ids(scope: AduScope, all_ids: Sequence[str]) -> dict[str, list[str]]:
    lots = [*LOT_PARAMS, LEGAL_PARAM]
    lots += [p.tract_share_parameter for p in scope.excluded if p.tract_share_parameter]
    fit = [*lots, FIT_PARAM, *FALLBACK_FIT.values()]
    proforma = [i.key for i in PROFORMA_INPUTS if i.is_parameter]
    feasible = [*fit, *RENT_PARAMS, NONFIN_PARAM, *proforma]
    built = [*feasible, *TAKEUP_PARAMS, *YEAR_PARAMS]
    net = [*built, DISPLACEMENT_PARAM]
    out = {"eligible": lots, "fit": fit, "feasible": feasible, "built": built, "net": net}
    return {k: sorted(set(ids) & (set(all_ids) | set(YEAR_PARAMS))) for k, ids in out.items()}


def _state_records(
    stages: Mapping[str, np.ndarray],
    ledger: Ledger,
    fit: FitTable,
    scope: AduScope,
    ids: Sequence[str],
    population: float,
    years: Sequence[int],
) -> dict[str, Record]:
    horizon = len(years)
    stage_ids = _stage_ids(scope, ids)
    out: dict[str, Record] = {}
    data_sources: list[str] = []
    types = ", ".join(scope.building_types)
    for stage in STAGES:
        data_sources += DATA_SOURCES.get(stage, ())
        sources = set(data_sources)
        if stage != "eligible":
            sources |= {fit.id, *fit.source_ids}
        inputs = [rec.from_parameter(pid, ledger[pid]) for pid in stage_ids[stage]]
        out[stage] = rec.from_draws(
            f"{_label(stage, years)} ({scope.name}: {types})",
            stages[stage].sum(axis=1),
            STAGE_UNITS[stage],
            expression=f"the sum over tracts of {_expression(stage, years)}",
            inputs=inputs,
            source_ids=sorted(sources),
            tier=0,
        )
    if population > 0:
        per_100k = stages["net"].sum(axis=1) / horizon / population * 100_000
        evidence = rec.from_parameter(EVIDENCE_PARAM, ledger[EVIDENCE_PARAM])
        net = out["net"]
        out["per_100k"] = rec.from_draws(
            f"net added ADUs per 100,000 residents per year ({scope.name})",
            per_100k,
            "permits per 100k people per year",
            expression=(
                f"net added units / {horizon} years / residents ({population:,.0f}) x 100,000; "
                f"compare with {evidence.formula}"
            ),
            inputs=[evidence],
            assumption_ids=net.assumption_ids,
            source_ids=net.source_ids,
            tier=0,
        )
    return out
