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
   effectively legal, minus places the scenario leaves out. The already-legal
   share is one statewide range, except in counties whose municipal rules
   have been read and verified for enough residents: there it is
   ``a + b x`` the statewide draw (``local_legal``; see
   :mod:`visionhousing.law.hooks`). Where those counties' rules also come
   as lot-weighted legal-status categories (``local_categories``), the share
   is ``already permitted + (1 - c) x by right with conditions + unknown x``
   the statewide draw, so only lots where the benchmark changes the status
   quo stay exposed (``c`` = ``adu.conditions_exposure_share``).
2. **Lots with room** = ``E x f``. ``f`` is the share of lots with room for a
   600 sq ft unit, by kind of home and area type, from the calibration table
   ``assumptions/calibration/adu_fit.yaml``. Attached homes have a far lower ``f``.
3. **Feasible lots** = lots with room ``x p``. ``p`` is the share where the
   ADU pays: the tract's distribution of gross rents (published ACS brackets)
   is compared with the pro forma's break-even rent, using
   :func:`visionhousing.features.brackets.share_above_many`. Owners who build
   for family use whatever the rent are added: ``p = p_rent + s x (1 - p_rent)``.
4. **ADUs authorized** = feasible lots ``x h x sum of ramp(t) x n``. ``h`` is
   the yearly take-up among feasible lots (its evidence is counted in permits),
   ``ramp(t)`` scales the first years (permits climb after a reform; it is 0
   before ``adu.start_delay_years``), ``n = 1`` home per ADU.
5. **Added units authorized** = ADUs authorized ``x (1 - x)``; ``x`` is the
   share that would have been permitted anyway (displacement). This is the
   headline (:data:`HEADLINE_STAGE`). An ADU removes no home, so it is also the
   net change in the housing stock.

Work types. The benchmark allows detached and semi-detached ADUs, and the
permit statistics treat them differently, so the funnel carries three work
types from the lots-with-room step on (records ``work_<type>_<stage>``):
a **detached ADU** is a new building of its own (the Building Permits Survey
counts it); an **attached ADU** is a new attached structure or an addition,
depending on how the permit office classes it (uncertain); an ADU **made
inside the house** is a conversion (the survey leaves it out). Each kind of
lot sends its lots with room to the work types by ledger shares
(``adu.detached_form_share_*``, ``adu.conversion_share_of_attached_forms``);
the later stages keep the split, so the work types add up to every stage in
every draw (:attr:`AduResult.work_types`). ``form_detached`` and
``form_semi_detached`` sum them into the benchmark's two forms.

Size. The 600 sq ft in the benchmark is a legal capacity condition
(``adu.min_size_sqft``; the fit test places a 20 x 30 ft footprint); the
economic prototype the pro forma prices has its own floor area
(``adu.unit_size_sqft``, 600-900 sq ft). Records ``legal_size_condition`` and
``prototype_floor_area`` keep them apart.

Other extra records (no change to any stage): the pro forma at average
regional cost (``installed_cost``, ``installed_utility_cost``, ``owner_cash``,
``owner_monthly_cost``). The financing and full-cost variants are in
``assumptions/variants.yaml``.

Years. The five-year total is split into 2027-2031 by the ramp:
``year share(t) = ramp(t) / sum of ramp``, the same in every tract of a draw,
so the five yearly values add up to the total in every draw
(:attr:`AduResult.year_share`). Permit statistics may not count every ADU (see
the ADU limits text).

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
city inside Allegheny County, so it is removed tract by tract: the engine
(:mod:`visionhousing.engine.place_scope`) measures each Allegheny tract's share
of 2020 housing units inside the city from the pack's ``tract_municipalities``
table and passes it in the scope (``PlaceScope.by_tract``); the funnel keeps
``1 - share`` of each tract's lots. A pack without that table falls back to the
flat judged share of every Allegheny tract
(``adu.pittsburgh_share_of_allegheny_lots``).

Files and purity
----------------
:func:`load_fit_table` and :func:`load_legal_share` read the two calibration
files; :func:`load_legal_shares` reads every state's already-legal table
(``assumptions/calibration/adu_legal_share*.yaml`` and ``already_legal_*.yaml``),
keyed by state. A state's table can also replace other "already legal" ledger
entries (its ``also`` block; empty for every state today). Vermont's missing
middle no longer comes from this table: it uses the dated § 4412 floor
(:mod:`visionhousing.engine.law_floor`).
:func:`adu_funnel` is pure: tables and ledger in, result out.
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
from visionhousing.scenarios import adu_levers

__all__ = [
    "AREA_TYPES",
    "BUILDING_TYPES",
    "FIT_PATH",
    "HEADLINE_STAGE",
    "LEGAL_PATH",
    "NET_STOCK_STAGE",
    "STAGES",
    "WORK_TYPES",
    "AduLotDetail",
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
    "load_legal_shares",
    "parse_fit_table",
    "parse_legal_share",
    "ramp_sum",
    "scope_from_scenario",
    "start_delay",
    "year_shares",
]

FIT_PATH = ROOT / "assumptions" / "calibration" / "adu_fit.yaml"
LEGAL_PATH = ROOT / "assumptions" / "calibration" / "adu_legal_share.yaml"
LEGAL_GLOBS = ("adu_legal_share*.yaml", "already_legal_*.yaml")
"""File names of the per-state already-legal tables in ``assumptions/calibration/``."""

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

HEADLINE_STAGE = "net"
"""The stage that is the headline: added units authorized by building permits."""

NET_STOCK_STAGE = "net"
"""The stage that is the net change in the housing stock (no home is removed for an ADU)."""

STAGE_NAMES = {
    "eligible": "eligible lots",
    "fit": "lots with room for an ADU",
    "feasible": "lots where an ADU is feasible",
    "built": "ADUs authorized by building permits {years}",
    "net": "added units authorized by building permits {years}",
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
DELAY_PARAM = "adu.start_delay_years"
"""Optional: years after the window opens before permits respond (0 if the ledger lacks it)."""
DISPLACEMENT_PARAM = "adu.displacement_share"
CONDITIONS_PARAM = "adu.conditions_exposure_share"
"""Share of 'by right with conditions' lots the benchmark changes (read only with categories)."""
FORM_PARAMS = {
    "sf_detached": "adu.detached_form_share_detached_lots",
    "sf_attached": "adu.detached_form_share_attached_lots",
    "two_to_four": "adu.detached_form_share_attached_lots",
}
"""Kind of lot to the ledger id of the share of its ADUs built as a detached building."""
CONVERSION_PARAM = "adu.conversion_share_of_attached_forms"
"""Share of the ADUs not built detached that are made inside the house (the rest attached)."""
WORK_TYPES = ("new_standalone", "uncertain", "conversion")
"""ADU work types, carried through the funnel's stages (``assumptions/outcome.yaml``):
a detached ADU is a new building of its own; an attached ADU is a new attached
structure or an addition, depending on how the permit office classes it
(uncertain); an ADU made inside the house is a conversion."""
WORK_STAGES = ("fit", "feasible", "built", "net")
"""Funnel stages split by work type."""
SIZE_PARAMS = ("adu.min_size_sqft", "adu.unit_size_sqft")
"""The legal size condition (fixed, the benchmark) and the economic prototype's floor area."""
CATEGORY_KEYS = (
    "already_permitted",
    "by_right_conditions",
    "discretionary",
    "prohibited",
    "unknown",
)
"""ADU legal-status categories (``visionhousing.law.coverage.CATEGORIES``)."""
YEAR_PARAMS = ("baseline.first_year", "baseline.horizon_years")
EVIDENCE_PARAM = "adu.evidence_permits_per_100k_permissive_outside_ca"

COST_FACTOR_COLUMN = "cost_factor"
"""Optional tract column: regional construction cost factor (1 = average)."""


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

        ``position`` p (normally in [-1, 1]; a borrowed calibration may be
        widened past it, see ``assumptions/transfer.yaml``) maps to
        ``central + p (high - central)`` when p >= 0 and
        ``central + p (central - low)`` when p < 0, kept between 0 and 1.

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
            # A widened (borrowed) range may reach past a cell's low or high: keep 0..1.
            value = np.clip(np.where(pos >= 0, up, down), 0.0, 1.0)
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
        tract_share_parameter: Ledger id giving that share instead (drawn). For a
            place given by ``cousub`` it is only the fallback for a pack without
            the tract-to-municipality table.
        cousub: Census county subdivision code (five digits) of a place that is
            part of a county, or ``None``. The engine
            (:mod:`visionhousing.engine.place_scope`) turns it into ``by_tract``.
        by_tract: Measured share of each tract's homes inside the place, as
            ``(tract geoid, share)`` pairs; when set it replaces ``tract_share``
            and the parameter. County tracts not listed count as outside.
        share_sources: Data source ids behind ``by_tract``.
    """

    name: str
    county: str
    tract_share: float | None = None
    tract_share_parameter: str | None = None
    cousub: str | None = None
    by_tract: tuple[tuple[str, float], ...] | None = None
    share_sources: tuple[str, ...] = ()


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
        also: Other ledger entries the table replaces for its state:
            parameter id to ``{low, central, high, shape, how}``.
        sources: The table's sources, as ledger source entries
            (``id``, ``citation`` and ``url`` when given).
        kind: Ledger kind the table's ranges carry (``calibrated`` unless the
            table says otherwise; Vermont's ranges are ``judgement``).
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
    also: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: tuple[dict[str, str], ...] = ()
    kind: str = "calibrated"


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
            cousub=None if raw.get("cousub") is None else str(raw["cousub"]).zfill(5),
        )
    how = " ".join(" ".join(str(v).split()) for v in (rng.get("how") or {}).values())
    also: dict[str, dict[str, Any]] = {}
    for pid, raw in (data.get("also") or {}).items():
        try:
            lo, ce, hi = float(raw["low"]), float(raw["central"]), float(raw["high"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError(f"{name}: also.{pid} needs low, central and high") from exc
        if not 0.0 <= lo <= ce <= hi <= 1.0:
            raise LedgerError(f"{name}: also.{pid} needs 0 <= low <= central <= high <= 1")
        also[str(pid)] = {
            "low": lo,
            "central": ce,
            "high": hi,
            "shape": str(raw.get("shape", "pert")),
            "how": " ".join(" ".join(str(v).split()) for v in (raw.get("how") or {}).values()),
        }
    sources = tuple(
        {
            "id": str(src["id"]),
            "citation": " ".join(str(src.get("citation", src["id"])).split()),
            **({"url": str(src["url"])} if src.get("url") else {}),
        }
        for src in ((data.get("provenance") or {}).get("sources") or [])
    )
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
        also=also,
        sources=sources,
        kind=str(data.get("kind", "calibrated")),
    )


def load_legal_share(path: Path | str | None = None) -> LegalShare:
    """Load ``assumptions/calibration/adu_legal_share.yaml`` (or another file)."""
    path = Path(path) if path is not None else LEGAL_PATH
    return parse_legal_share(read_yaml(path), name=path.name)


def legal_share_paths(folder: Path | str | None = None) -> list[Path]:
    """The per-state already-legal tables in ``folder`` (default ``assumptions/calibration``)."""
    folder = Path(folder) if folder is not None else LEGAL_PATH.parent
    found = {p for pattern in LEGAL_GLOBS for p in folder.glob(pattern) if p.is_file()}
    return sorted(found)


def load_legal_shares(folder: Path | str | None = None) -> dict[str, LegalShare]:
    """Load every state's already-legal table, keyed by upper-case state code.

    Raises:
        LedgerError: Two tables name the same state, or a table is malformed.
    """
    out: dict[str, LegalShare] = {}
    for path in legal_share_paths(folder):
        table = parse_legal_share(read_yaml(path), name=path.name)
        code = table.state.upper()
        if code in out:
            raise LedgerError(f"{path.name}: a second already-legal table for state {code}")
        out[code] = table
    return out


def legal_share_override(legal: LegalShare) -> dict[str, dict[str, Any]]:
    """Return the ledger override that puts the state's already-legal range in place.

    Apply it with :func:`visionhousing.ledger.apply_overrides` for a run of
    ``legal.state``. The ledger entry stays the knob users can change.

    Args:
        legal: The state's legal-share table.

    Returns:
        ``{parameter id: fields to replace}``.
    """
    extra: dict[str, Any] = {"kind": legal.kind}
    if legal.sources:
        extra["source"] = [dict(s) for s in legal.sources]
    out: dict[str, dict[str, Any]] = {
        legal.parameter: {
            "central": legal.central,
            "low": legal.low,
            "high": legal.high,
            "shape": legal.shape,
            **extra,
            "rationale": f"From calibration table {legal.id}. {legal.how}",
        }
    }
    for pid, r in legal.also.items():
        out[pid] = {
            "central": r["central"],
            "low": r["low"],
            "high": r["high"],
            "shape": r["shape"],
            **extra,
            "rationale": f"From calibration table {legal.id}. {r['how']}",
        }
    return out


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


def ramp_sum(
    first_year_share: Any, years_to_full: Any, horizon: int, delay: Any = 0.0
) -> np.ndarray:
    """The yearly ramp factors over the horizon (an adoption curve).

    With no delay, ``ramp(t) = min(1, r1 + (1 - r1) (t - 1) / (Y - 1))`` for
    year t = 1..horizon, with ``r1`` the first-year share and ``Y`` the years to
    the full rate. With ``Y <= 1`` every year is at the full rate.

    A start delay ``d`` (years after the window opens) shifts the curve: year
    ``t`` (the span ``t - 1`` to ``t``) is active for ``a = clip(t - d, 0, 1)``
    of the year and sits at step ``k = max(1, ceil(t - d))`` of the ramp, so its
    factor is ``a x ramp(k)``. ``d = 0`` gives the plain ramp.

    Args:
        first_year_share: ``r1`` (number or array of draws).
        years_to_full: ``Y`` (number or array of draws).
        horizon: Number of years.
        delay: ``d`` (number or array of draws), at least 0.

    Returns:
        ``(..., horizon)`` array of yearly factors; sum the last axis for the total.
    """
    r1 = np.asarray(first_year_share, dtype=float)[..., None]
    y = np.asarray(years_to_full, dtype=float)[..., None]
    d = np.maximum(np.asarray(delay, dtype=float), 0.0)[..., None]
    t = np.arange(1, horizon + 1, dtype=float)
    active = np.clip(t - d, 0.0, 1.0)
    k = np.maximum(1.0, np.ceil(t - d))
    with np.errstate(divide="ignore", invalid="ignore"):
        climb = r1 + (1.0 - r1) * (k - 1.0) / (y - 1.0)
    return active * np.where(y <= 1.0, 1.0, np.clip(climb, 0.0, 1.0))


def year_shares(ramp: np.ndarray) -> np.ndarray:
    """Each year's share of the five-year total: ``ramp(t) / sum of ramp`` (rows add to 1).

    A row whose ramp is all zero (a delay past the window) gets equal shares;
    its total is zero, so every year is zero either way.

    Args:
        ramp: ``(draws, years)`` factors from :func:`ramp_sum`.

    Returns:
        ``(draws, years)`` shares.
    """
    ramp = np.asarray(ramp, dtype=float)
    total = ramp.sum(axis=-1, keepdims=True)
    even = np.full_like(ramp, 1.0 / ramp.shape[-1])
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, ramp / np.where(total > 0, total, 1.0), even)


def start_delay(ledger: Ledger, pid: str, n: int, seed: int) -> np.ndarray:
    """Draws of a start-delay entry, or zeros when the ledger has no such entry."""
    if pid not in ledger:
        return np.zeros(n)
    return sample(ledger, n=n, seed=seed, ids=[pid])[pid]


@dataclass(frozen=True)
class AduLotDetail:
    """Per-lot shares project choice reads (``engine.market``); draws x tracts arrays.

    Attributes:
        exposed: Share of a tract's lots the rule reaches: (1 - already-legal
            share) x share inside the scenario x lot reach (the eligible step
            without the census-to-lot correction).
        pencil: Building type to the share of its eligible lots where an ADU
            pays: lots with room share x feasible share ``p``.
        unit_share: Building type to its share of the tract's ADUs (its lots
            with room / all lots with room; ``p`` is the same for every type).
        uplift: Value-uplift share (value an ADU adds / host home value), ``(draws, 1)``.
        total_cost: The ADU's total cost per lot ($), draws x tracts. With the
            uplift it gives the ADU's value to the owner on a lot with home
            value ``V``: ``max(uplift x V - total cost, 0)`` (the pro forma's
            value test).
    """

    exposed: np.ndarray
    pencil: Mapping[str, np.ndarray]
    unit_share: Mapping[str, np.ndarray]
    uplift: np.ndarray
    total_cost: np.ndarray


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
        net_by_year: State added units authorized per draw and year, ``(draws, years)``
            (the five-year total x :attr:`year_share`).
        records: State-total records: one per stage, plus ``per_100k``.
        seed: Seed used.
        year_share: Each year's share of the five-year total per draw,
            ``(draws, years)``; rows add to 1 (the same for every tract).
        takeup_factor: Per draw, take-up x units per lot x the ramp's sum over
            the years, shape ``(draws, 1)`` (cached for :meth:`late_stages`).
        kept_share: Per draw, 1 - displacement share, shape ``(draws, 1)``.
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
    year_share: np.ndarray | None = None
    takeup_factor: np.ndarray | None = None
    kept_share: np.ndarray | None = None
    levers: Any = None
    """The two-lever case (:class:`visionhousing.scenarios.adu_levers.LeverResult`), or None."""
    lot_detail: AduLotDetail | None = None
    """Per-lot shares for project choice; only when the funnel ran with ``lot_detail=True``."""
    work_shares: Mapping[str, np.ndarray] = field(default_factory=dict)
    """Work type to its share of each tract's units, draws x tracts (they add to 1);
    lets a later step that scales tracts (the practical capacity screen) split its
    screened units by work type exactly."""
    work_types: Mapping[str, np.ndarray] = field(default_factory=dict)
    """Work type (:data:`WORK_TYPES`) to its added units authorized per draw, state,
    shape ``(draws,)``; they add up to the ``net`` stage's state total in every draw.
    Empty when the ledger lacks the form shares."""

    def late_stages(self, takeup_multiplier: float = 1.0) -> np.ndarray:
        """Net added ADUs (draws x tracts) from the cached feasible stage.

        The last two stages again, with the take-up rate times
        ``takeup_multiplier``: built = min(feasible x take-up x units per lot x
        ramp x m, feasible); net = built x (1 - displacement). With ``m = 1``
        this is the ``net`` stage (same draws).
        """
        if self.takeup_factor is None or self.kept_share is None:
            raise ValueError("this result has no cached late-stage factors")
        m = float(takeup_multiplier)
        if m < 0:
            raise ValueError("the take-up multiplier cannot be negative")
        feasible = self.stages["feasible"]
        built = np.minimum(feasible * (self.takeup_factor * m), feasible)
        return built * self.kept_share

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
        "ADUs authorized = feasible lots x yearly take-up h x sum over {years} of ramp(t) "
        "x ADUs per lot n"
    ),
    "net": (
        "added units authorized = ADUs authorized x (1 - displacement share x) "
        "(no home removed, so also the net added homes)"
    ),
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
    """Share of cash rents at or above ``threshold`` (draws x tracts), via brackets.

    Uses :func:`visionhousing.features.brackets.share_above_grid`, which reads the
    tract counts once instead of copying them for every draw.
    """
    return br.share_above_grid(counts, br.RENT, threshold, top_rule=top_rule)


def _place_param(place: PlaceScope) -> str | None:
    """The ledger id a place's share is drawn from, or ``None`` (fixed or measured)."""
    return None if place.by_tract is not None else place.tract_share_parameter


def _place_shares(place: PlaceScope, geoids: np.ndarray, draws: int, col: Mapping) -> np.ndarray:
    """Share of each tract inside a place: measured per tract, else one drawn or fixed share."""
    if place.by_tract is not None:
        by = dict(place.by_tract)
        return np.array([by.get(str(g), 0.0) for g in geoids], dtype=float)[None, :]
    if place.tract_share_parameter:
        return col[place.tract_share_parameter]
    return np.full((draws, 1), place.tract_share)


def _place_records(
    scope: AduScope,
    homes: Mapping[str, np.ndarray],
    counties: np.ndarray,
    geoids: np.ndarray,
    draws: int,
) -> dict[str, Record]:
    """One record per place measured tract by tract: its county-wide share of homes in scope.

    Keyed ``place_<name>_share`` (lower case, spaces to ``_``). The share weighs
    each county tract's measured share by the tract's homes of the scenario's
    building types (single-family detached for SB 1346); it is the number the
    flat fallback parameter stood in for.
    """
    out: dict[str, Record] = {}
    types = ", ".join(scope.building_types)
    for place in scope.excluded:
        if place.by_tract is None:
            continue
        mask = counties == place.county
        weight = sum(np.clip(homes[t], 0, None)[0] for t in scope.building_types)[mask]
        share = _place_shares(place, geoids, draws, {})[0][mask]
        total = float(weight.sum())
        value = float((weight * share).sum() / total) if total > 0 else 0.0
        whole = int((share >= 1.0).sum())
        part = int(((share > 0) & (share < 1.0)).sum())
        key = "place_" + place.name.lower().replace(" ", "_") + "_share"
        out[key] = rec.from_draws(
            f"Share of county {place.county}'s homes in scope ({types}) inside {place.name}",
            np.full(draws, value),
            "share",
            expression=(
                f"sum over the county's tracts of homes in scope x share of the tract's 2020 "
                f"housing units inside {place.name} (county subdivision {place.cousub}) / sum of "
                f"homes in scope; {whole} of {int(mask.sum())} tracts wholly inside, {part} split"
            ),
            source_ids=sorted({*DATA_SOURCES["eligible"], *place.share_sources}),
            tier=0,
        )
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
    ids += [_place_param(p) for p in scope.excluded if _place_param(p)]
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
    local_legal: Mapping[str, tuple[float, float]] | None = None,
    local_categories: Mapping[str, Mapping[str, float]] | None = None,
    lot_reach: np.ndarray | None = None,
    lot_detail: bool = False,
    legal_rule: Any | None = None,
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
        local_legal: County to ``(a, b)``: in that county's tracts the
            already-legal share of a draw ``s`` is ``a + b x s`` instead of
            ``s`` (from verified municipal rules, :mod:`visionhousing.law.hooks`).
            Other counties use ``s``.
        local_categories: County to its lot-weighted shares of the ADU
            legal-status categories (``already_permitted``,
            ``by_right_conditions``, ``discretionary``, ``prohibited``,
            ``unknown``; :mod:`visionhousing.law.coverage`). In that county's
            tracts the already-legal share of a draw is
            ``already_permitted + (1 - c) x by_right_conditions + unknown x s``
            with ``c`` = ``adu.conditions_exposure_share``: only lots where the
            benchmark changes the status quo stay exposed. It takes precedence
            over ``local_legal`` for the counties it names.
        lot_reach: Share of each tract's eligible lots the rule reaches (one
            value per tract, 0-1); ``None`` means all of them (the benchmark).
            Every later stage scales with it.
        lot_detail: Keep the per-lot shares project choice reads
            (:class:`AduLotDetail`). No stage changes.
        legal_rule: A per-tract already-legal rule with ``ids`` (ledger ids
            it draws) and ``share(col)`` (draws x tracts), used **in place of**
            the statewide range and any local rule (Massachusetts variant
            ``ma_adu_state_floor``, :class:`visionhousing.law.ma_adu.StateFloorRule`).
            A rule with ``blends_base`` is given the share worked out above as
            ``base`` and keeps it outside its own tracts (Pennsylvania variants
            ``adu_zoning_districts`` and ``adu_zoning_districts_guidance``,
            :class:`visionhousing.engine.zone_scope.DistrictRule`); its optional
            ``records(lots, legal, col)`` adds records, and ``record_key`` /
            ``record_name`` / ``record_expression`` / ``source_ids`` name its share record.
            ``None`` (the benchmark) changes nothing.

    Returns:
        An :class:`AduResult`.

    Raises:
        KeyError: A tract column is missing.
        LedgerError: A ledger parameter is missing.
    """
    ids = _needed_ids(scope, ledger)
    local_categories = {
        str(c): dict(sh) for c, sh in (local_categories or {}).items() if CONDITIONS_PARAM in ledger
    }
    extra = [CONDITIONS_PARAM] if local_categories else []
    extra += [FORM_PARAMS[t] for t in scope.building_types if FORM_PARAMS[t] in ledger]
    extra += [i for i in (CONVERSION_PARAM, *SIZE_PARAMS) if i in ledger]
    extra += list(legal_rule.ids) if legal_rule is not None else []
    v = sample(ledger, n=draws, seed=seed, ids=[*ids, *dict.fromkeys(extra)])
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
    geoids = tracts["geoid"].astype(str).to_numpy()
    for place in scope.excluded:
        share = np.clip(_place_shares(place, geoids, draws, col), 0.0, 1.0)
        inside = np.where(counties[None, :] == place.county, inside * (1.0 - share), inside)
    legal = np.broadcast_to(col[LEGAL_PARAM], (draws, n_tracts))
    if local_legal:
        a = np.array([local_legal.get(c, (0.0, 1.0))[0] for c in counties], dtype=float)
        b = np.array([local_legal.get(c, (0.0, 1.0))[1] for c in counties], dtype=float)
        legal = np.clip(a[None, :] + b[None, :] * col[LEGAL_PARAM], 0.0, 1.0)
    if local_categories:
        legal = np.array(legal, dtype=float)
        for county, sh in local_categories.items():
            mask = counties == county
            if not mask.any():
                continue
            share = {k: float(sh.get(k) or 0.0) for k in CATEGORY_KEYS}
            cat_legal = (
                share["already_permitted"]
                + (1.0 - col[CONDITIONS_PARAM]) * share["by_right_conditions"]
                + share["unknown"] * col[LEGAL_PARAM]
            )
            legal[:, mask] = np.clip(cat_legal, 0.0, 1.0)
    if legal_rule is not None:
        # A rule that blends (zoning-district variants) keeps the share above outside its tracts.
        blend = bool(getattr(legal_rule, "blends_base", False))
        legal = legal_rule.share(col, base=legal) if blend else legal_rule.share(col)
    keep = adj * (1.0 - legal) * inside
    if lot_reach is not None:
        keep = keep * np.clip(np.asarray(lot_reach, dtype=float), 0.0, 1.0)[None, :]
    eligible_by_type = {t: np.clip(homes[t], 0, None) * keep for t in scope.building_types}

    # 2. Lots with room.
    area = (
        tracts["area_type"].astype(str).to_numpy()
        if "area_type" in tracts
        else np.array([""] * n_tracts)
    )
    unknown_area = ~np.isin(area, AREA_TYPES)
    fit_lots = np.zeros((draws, n_tracts))
    fit_by_type: dict[str, np.ndarray] = {}
    fit_share_by_type: dict[str, np.ndarray] = {}
    for t, lots in eligible_by_type.items():
        f = fit.shares(t, area, v[FIT_PARAM])
        f = np.where(np.isnan(f), col[FALLBACK_FIT[t]], f)
        if lot_detail:
            fit_share_by_type[t] = np.broadcast_to(f, (draws, n_tracts))
        fit_by_type[t] = lots * f
        fit_lots = fit_lots + fit_by_type[t]
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
    adu_rent = np.nan_to_num(gross)[None, :] * ratio * premium
    proforma = adu_proforma(
        {k: col[k] for k in ids}, {"rent_monthly": adu_rent, "cost_factor": cost_factor}
    )
    breakeven = np.broadcast_to(proforma.lines["breakeven_rent"], (draws, n_tracts))
    counts = tracts[list(br.RENT.columns)].to_numpy(dtype=float)
    family = col[NONFIN_PARAM]

    def p_at(be: np.ndarray) -> np.ndarray:
        p_rent = _rent_share(counts, be / (ratio * premium))
        fallback = np.where(np.isfinite(b1)[None, :] & (b1[None, :] * premium >= be), 1.0, 0.0)
        p_rent = np.where(np.isnan(p_rent), fallback, p_rent)
        return p_rent + family * (1.0 - p_rent)

    p = p_at(breakeven)
    feasible = fit_lots * p

    # 4-5. Take-up, ramp, units, displacement.
    ramp = ramp_sum(
        v["adu.ramp_first_year_share"],
        v["adu.ramp_years_to_full"],
        horizon,
        start_delay(ledger, DELAY_PARAM, draws, seed),
    )
    yearly = col["adu.takeup_feasible_annual"] * col["adu.units_per_lot"]  # (draws, 1)
    built = np.minimum(feasible * yearly * ramp.sum(axis=1)[:, None], feasible)
    net = built * (1.0 - col[DISPLACEMENT_PARAM])
    share = year_shares(ramp)
    takeup_factor = np.asarray(yearly * ramp.sum(axis=1)[:, None], dtype=float)
    # The ADU lever cases (timing, financing, budget-capped program): None, so no
    # change, for the benchmark. The budget-capped program also reaches lots where an
    # ADU is already legal (worked out only when asked) and reports the detached share.

    def fit_lots_old() -> np.ndarray:
        """Lots with room where an ADU is already legal (same tracts, same draws)."""
        old_keep = adj * legal * inside
        out = np.zeros((draws, n_tracts))
        for t in scope.building_types:
            f = fit.shares(t, area, v[FIT_PARAM])
            f = np.where(np.isnan(f), col[FALLBACK_FIT[t]], f)
            out = out + np.clip(homes[t], 0, None) * old_keep * f
        return out

    shares = _work_shares(fit_by_type, fit_lots, col)
    levers = adu_levers.apply_levers(
        ledger, draws=draws, seed=seed, years=years, counties=counties, col=col,
        lines=proforma.lines, breakeven=breakeven, rent=adu_rent, fit_lots=fit_lots, p_at=p_at,
        p=p, yearly=yearly, ramp=ramp, delay=start_delay(ledger, DELAY_PARAM, draws, seed),
        ramp_fn=ramp_sum, base_year_share=share, net=net, fit_lots_old=fit_lots_old,
        comparable_share=None if shares is None else shares["new_standalone"],
    )  # fmt: skip
    if levers is not None:
        feasible, built, net, p = levers.feasible, levers.built, levers.net, levers.p
        share, takeup_factor = levers.year_share, levers.takeup_factor
    net_by_year = net.sum(axis=1)[:, None] * share
    detail = None
    if lot_detail:
        with np.errstate(divide="ignore", invalid="ignore"):
            unit_share = {
                t: np.where(fit_lots > 0, a / np.where(fit_lots > 0, fit_lots, 1.0), 0.0)
                for t, a in fit_by_type.items()
            }
        detail = AduLotDetail(
            exposed=np.broadcast_to(keep / np.where(adj > 0, adj, 1.0), (draws, n_tracts)).copy(),
            pencil={t: f * p for t, f in fit_share_by_type.items()},
            unit_share=unit_share,
            uplift=np.asarray(col["adu.value_uplift_share"], dtype=float),
            total_cost=np.broadcast_to(proforma.lines["total_cost"], (draws, n_tracts)).copy(),
        )

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
    if DELAY_PARAM in ledger:
        ids = [*ids, DELAY_PARAM]
    if local_categories:
        ids = [*ids, CONDITIONS_PARAM]
    if legal_rule is not None:
        # The rule's ids replace the statewide range's (a rule that still draws it lists it).
        keep = [i for i in ids if i != LEGAL_PARAM]
        ids = [*keep, *(i for i in legal_rule.ids if i not in keep)]
    records = _state_records(stages, ledger, fit, scope, ids, population, years)
    work = _work_type_stages(
        fit_by_type, fit_lots, {"fit": fit_lots, "feasible": feasible, "built": built, "net": net},
        col, ledger,
    )  # fmt: skip
    records.update(_work_type_records(work, col, ledger, records, years))
    records.update(_place_records(scope, homes, counties, geoids, draws))
    records.update(_size_records(col, ledger))
    records.update(_installed_cost_records(col, ids, ledger))
    if levers is not None:
        records.update(adu_levers.lever_stage_records(records, levers, ledger))
    if local_categories:
        records.update(
            _exposure_records(eligible_by_type, counties, local_categories, ledger, records)
        )
    if legal_rule is not None:
        lots = sum(np.clip(homes[t], 0, None) for t in scope.building_types) * adj * inside
        total = lots.sum(axis=1)
        mean = np.where(total > 0, (lots * legal).sum(axis=1) / np.where(total > 0, total, 1), 0)
        key = getattr(legal_rule, "record_key", "already_legal_share_state_floor")
        records[key] = rec.from_draws(
            getattr(legal_rule, "record_name", "share of reached lots where the benchmark ADU is "
                    "already legal under the state floor"),
            mean, "share",
            expression=getattr(
                legal_rule, "record_expression",
                "sum over tracts of lots x legal_rule share / sum of lots, in each draw",
            ),
            assumption_ids=list(legal_rule.ids),
            source_ids=list(getattr(legal_rule, "source_ids", ())),
        )  # fmt: skip
        if hasattr(legal_rule, "records"):
            records.update(legal_rule.records(lots, legal, col))
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
        year_share=share,
        takeup_factor=takeup_factor,
        kept_share=np.asarray(1.0 - col[DISPLACEMENT_PARAM], dtype=float),
        levers=levers,
        lot_detail=detail,
        work_types={w: by["net"] for w, by in work.items()},
        work_shares=(_work_shares(fit_by_type, fit_lots, col) or {}) if work else {},
    )


def _work_shares(
    fit_by_type: Mapping[str, np.ndarray],
    fit_lots: np.ndarray,
    col: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray] | None:
    """Each work type's share of a tract's lots with room, draws x tracts (they add to 1).

    ``detached = sum over kinds of lot of lots with room of that kind x share
    built detached for that kind / all lots with room``; the rest is split into
    conversions (``c`` = ``adu.conversion_share_of_attached_forms``) and
    attached ADUs (``1 - c``). ``None`` when the ledger lacks a share.
    """
    if any(FORM_PARAMS[t] not in col for t in fit_by_type) or CONVERSION_PARAM not in col:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        safe = np.where(fit_lots > 0, fit_lots, 1.0)
    detached = np.zeros_like(fit_lots)
    for t, lots in fit_by_type.items():
        detached = detached + np.where(fit_lots > 0, lots / safe, 0.0) * col[FORM_PARAMS[t]]
    rest = 1.0 - detached
    c = col[CONVERSION_PARAM]
    return {"new_standalone": detached, "uncertain": rest * (1.0 - c), "conversion": rest * c}


def _work_type_stages(
    fit_by_type: Mapping[str, np.ndarray],
    fit_lots: np.ndarray,
    stages: Mapping[str, np.ndarray],
    col: Mapping[str, np.ndarray],
    ledger: Ledger,
) -> dict[str, dict[str, np.ndarray]]:
    """Work type to stage to its state total per draw, ``(draws,)``.

    Each kind of lot sends its lots with room to the three work types by the
    ledger shares; every later stage keeps the tract's split (the rent test,
    take-up and displacement are the same for every form in a tract), so in
    each tract ``stage of a work type = stage x the work type's share`` and
    the work types add up to the stage in every draw. A measured fit or cost
    per form would enter here, at the lots-with-room step.
    """
    shares = _work_shares(fit_by_type, fit_lots, col)
    if shares is None:
        return {}
    return {
        w: {stage: (stages[stage] * share).sum(axis=1) for stage in WORK_STAGES}
        for w, share in shares.items()
    }


WORK_WORDS = {
    "new_standalone": "detached ADUs (a new building of its own)",
    "uncertain": "attached ADUs (a new attached structure or an addition)",
    "conversion": "ADUs made inside the house (conversions)",
}
WORK_STAGE_WORDS = {
    "fit": ("lots with room, for", "lots"),
    "feasible": ("feasible lots, for", "lots"),
    "built": ("ADUs authorized", "units"),
    "net": ("added units authorized", "units"),
}


def _work_type_records(
    work: Mapping[str, Mapping[str, np.ndarray]],
    col: Mapping[str, np.ndarray],
    ledger: Ledger,
    records: Mapping[str, Record],
    years: Sequence[int],
) -> dict[str, Record]:
    """Records of every funnel stage by work type, and the two-form summary.

    ``work_<type>_<stage>`` for the three work types and the stages lots with
    room, feasible lots, ADUs authorized and added units authorized.
    ``form_detached`` and ``form_semi_detached`` (attached plus inside the
    house) split the ADUs authorized into the benchmark's two forms; they add
    up to ``built`` in every draw.
    """
    if not work:
        return {}
    period = _period(years)
    share_ids = sorted({FORM_PARAMS[t] for t in FORM_PARAMS if FORM_PARAMS[t] in col})
    share_ids += [CONVERSION_PARAM]
    out: dict[str, Record] = {}
    split = (
        "x the work type's share of the tract's lots with room (detached: lots with room of "
        "each kind x share built detached for that kind / all lots with room; the rest: "
        "x c made inside the house, x (1 - c) attached, c = adu.conversion_share_of_attached_forms)"
    )
    for w, by in work.items():
        for stage in WORK_STAGES:
            base = records[stage]
            words, unit = WORK_STAGE_WORDS[stage]
            name = (
                f"{words} {WORK_WORDS[w]}"
                if stage in ("fit", "feasible")
                else f"{words}, {WORK_WORDS[w]}, {period}"
            )
            out[f"work_{w}_{stage}"] = rec.from_draws(
                name,
                by[stage],
                unit,
                expression=f"the sum over tracts of {_STAGE_NAMES_PLAIN[stage]} {split}",
                assumption_ids=sorted({*base.assumption_ids, *share_ids}),
                source_ids=base.source_ids,
                tier=0,
            )
    built = records["built"]
    detached = work["new_standalone"]["built"]
    semi = work["uncertain"]["built"] + work["conversion"]["built"]
    for key, arr, words in (
        ("form_detached", detached, "detached ADUs (a separate small building)"),
        ("form_semi_detached", semi, "semi-detached ADUs (attached to or inside the house)"),
    ):
        out[key] = rec.from_draws(
            f"{words} authorized {period}",
            arr,
            "units",
            expression=(
                "the sum over tracts of ADUs authorized x the share built detached "
                + ("(lots with room of each kind x share built detached for that kind / all "
                   "lots with room)" if key == "form_detached" else
                   "subtracted from 1 (attached plus made inside the house)")
            ),
            assumption_ids=sorted({*built.assumption_ids, *share_ids}),
            source_ids=built.source_ids,
            tier=0,
        )  # fmt: skip
    return out


_STAGE_NAMES_PLAIN = {
    "fit": "lots with room",
    "feasible": "feasible lots",
    "built": "ADUs authorized",
    "net": "added units authorized",
}


def _size_records(col: Mapping[str, np.ndarray], ledger: Ledger) -> dict[str, Record]:
    """The legal size condition and the economic prototype's floor area, as separate records.

    The law (``adu.min_size_sqft``, fixed) says what may be built; the fit test
    places a footprint of that size. The pro forma prices a prototype whose
    floor area is drawn from ``adu.unit_size_sqft``; changing it changes cost,
    not what the law allows.
    """
    legal_id, proto_id = SIZE_PARAMS
    if legal_id not in col or proto_id not in col:
        return {}
    legal = ledger[legal_id]
    proto = ledger[proto_id]
    return {
        "legal_size_condition": rec.from_draws(
            "legal size condition: smallest ADU the benchmark law allows",
            col[legal_id][:, 0],
            legal.unit,
            expression=f"ledger {legal_id} (fixed, the benchmark): a legal capacity condition, "
            "used by the fit test as a 20 x 30 ft footprint",
            assumption_ids=[legal_id],
            source_ids=legal.source_ids,
        ),
        "prototype_floor_area": rec.from_draws(
            "economic prototype: floor area of the ADU the pro forma prices",
            col[proto_id][:, 0],
            proto.unit,
            expression=f"draws of ledger {proto_id} (the prototype, versioned apart from the "
            "legal condition)",
            assumption_ids=[proto_id],
            source_ids=proto.source_ids,
        ),
    }


def _installed_cost_records(
    col: Mapping[str, np.ndarray], ids: Sequence[str], ledger: Ledger
) -> dict[str, Record]:
    """Full installed cost of one ADU and the owner's cash, at average regional cost."""
    pf_ids = [i.key for i in PROFORMA_INPUTS if i.is_parameter]
    res = adu_proforma(
        {k: col[k][:, 0] for k in pf_ids},
        {"rent_monthly": np.zeros(1), "cost_factor": 1.0},
    )
    lines = res.lines
    out = {}
    specs = (
        (
            "installed_cost",
            "total_cost",
            "full installed cost of one ADU, average regional cost",
            "$/unit",
            "floor area x cost per sq ft + site work and utility runs + soft cost + permit fees "
            "+ utility connection fees + parking cost (regional cost factor 1)",
        ),
        (
            "installed_utility_cost",
            None,
            "site work, utility runs and connection fees of one ADU",
            "$/unit",
            "site work and utility runs + utility connection fees",
        ),
        (
            "owner_cash",
            "owner_cash",
            "owner's cash needed for one ADU (cost not borrowed)",
            "$/unit",
            "full installed cost x (1 - loan share)",
        ),
        (
            "owner_monthly_cost",
            "monthly_cost",
            "owner's monthly cost of one ADU (loan payment + cost of own cash)",
            "$/month",
            "loan amount x payment factor at (30-year rate + home-equity spread) + owner cash x "
            "payment factor at the return on owner cash, over the loan term",
        ),
    )
    for key, line, name, unit, expression in specs:
        if line is None:
            values = np.broadcast_to(
                np.asarray(lines["site_work"], dtype=float)
                + np.asarray(lines["connection_fees"], dtype=float),
                col[pf_ids[0]][:, 0].shape,
            )
            used = ["adu.site_utility_cost", "adu.utility_connection_fee"]
        else:
            values = np.broadcast_to(
                np.asarray(lines[line], dtype=float).reshape(-1), (col[pf_ids[0]].shape[0],)
            )
            used = pf_ids
        used = [i for i in used if i in ledger]
        out[key] = rec.from_draws(
            name,
            np.array(values, dtype=float),
            unit,
            expression=expression,
            assumption_ids=sorted(used),
            source_ids=sorted({s for i in used for s in ledger[i].source_ids}),
        )
    return out


def _exposure_records(
    eligible_by_type: Mapping[str, np.ndarray],
    counties: np.ndarray,
    local_categories: Mapping[str, Mapping[str, float]],
    ledger: Ledger,
    records: Mapping[str, Record],
) -> dict[str, Record]:
    """Eligible lots in counties whose ADU rules are read by legal-status category."""
    eligible = sum(eligible_by_type.values())
    mask = np.isin(counties, list(local_categories))
    if not mask.any():
        return {}
    base = records["eligible"]
    lots = eligible[:, mask].sum(axis=1)
    return {
        "exposure_read_counties": rec.from_draws(
            "eligible lots in counties whose ADU rules are read by legal-status category",
            lots,
            "lots",
            expression=(
                "eligible lots in those counties; there the already-legal share = already "
                "permitted + (1 - c) x by right with conditions + unknown x statewide share, so "
                "lots where ADUs are discretionary or prohibited, and c of the 'by right with "
                "conditions' lots, stay exposed (lot-weighted shares, assumptions/law/hooks.yaml)"
            ),
            assumption_ids=sorted({*base.assumption_ids, CONDITIONS_PARAM}),
            source_ids=sorted({*base.source_ids, "pa_municipal_ordinances"}),
            tier=0,
        )
    }


def _stage_ids(scope: AduScope, all_ids: Sequence[str]) -> dict[str, list[str]]:
    lots = [*LOT_PARAMS, LEGAL_PARAM, CONDITIONS_PARAM]
    lots += [_place_param(p) for p in scope.excluded if _place_param(p)]
    fit = [*lots, FIT_PARAM, *FALLBACK_FIT.values()]
    proforma = [i.key for i in PROFORMA_INPUTS if i.is_parameter]
    feasible = [*fit, *RENT_PARAMS, NONFIN_PARAM, *proforma]
    built = [*feasible, *TAKEUP_PARAMS, DELAY_PARAM, *YEAR_PARAMS]
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
        if stage == "eligible":
            data_sources += [s for p in scope.excluded if p.by_tract for s in p.share_sources]
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
            f"added ADUs authorized per 100,000 residents per year ({scope.name})",
            per_100k,
            "permits per 100k people per year",
            expression=(
                f"added units authorized / {horizon} years / residents ({population:,.0f}) "
                "x 100,000; "
                f"compare with {evidence.formula}"
            ),
            inputs=[evidence],
            assumption_ids=net.assumption_ids,
            source_ids=net.source_ids,
            tier=0,
        )
    return out
