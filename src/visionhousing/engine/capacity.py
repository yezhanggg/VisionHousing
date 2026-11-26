"""Practical capacity: screen each reform's homes for sewer, flood zone and steep slope.

Plain-language summary
----------------------
A reform changes what the rules allow and what pencils (nominal capacity).
Some of those lots cannot be built on in practice: there is no public sewer
and the lot cannot take a septic system, the lot is in FEMA's flood zone, or
it is too steep for a small project without major grading. This module turns
the pack's water-and-sewer readiness table (``infra_tract``) and its hazards
table (``hazard_tract``) into a **practical capacity multiplier** per tract
and draw, between 0 and 1, and multiplies each reform's added homes by it.

The benchmark presets never change. The screen runs only when asked:

- ``engine.run(..., capacity_screen=True)``: every reform in the run (and the
  package) is reported screened beside today's numbers
  (``Result.capacity``, :class:`CapacityOutcome`). The "not screened" case is
  today's numbers, draw for draw.
- a reform option ``{"capacity_screen": True}``, or a named variant
  ``<reform>_capacity_screen`` (``assumptions/variants.yaml``): that reform's
  own headline is screened (:func:`screen_result`), so it flows through the
  package, the readouts and the explanation like any variant.

Per tract ``t`` and draw ``d``, with every share between 0 and 1::

    sewer  = verified + outside x septic + constraint x k_constraint
             + estimated_in x (e_in + (1 - e_in) x septic)
             + estimated_out x (e_out + (1 - e_out) x septic)
             + unknown x (served + (1 - served) x septic)
    flood  = 1 - F x (1 - k_flood)
    steep  = 1 - min(1, S x exposure) x (1 - k_steep)
    multiplier = sewer x flood x steep          (wildfire: not in any pack, 1)

The pack measures each screen on its own, not where they coincide, so the
product (independence) is the middle case of a range: with pass shares a, b,
c of the same lots, the share passing all three lies between
max(0, a + b + c - 2) (excluded lots never coincide) and min(a, b, c) (they
coincide as much as they can). Both ends are reported
(``screened_disjoint``, ``screened_nested``). Where a reform's own legal rule
already removed some lots (``legal_exclusions`` in the rules file; starter-home
lots drop lots outside a mapped sewer area), the screen works on the lots that
are left, so nothing is excluded twice.

- ``verified / outside / constraint / unknown``: the tract's homes by sewer
  answer (``scenarios.tod_infrastructure.readiness_shares``). Unknown homes are
  never assumed served: each draw takes ``capacity.unknown_sewer_served_share``
  of them as served and the rest as outside.
- ``estimated_in / estimated_out``: where a state publishes only an
  *estimated* service map (Massachusetts: MassDEP's estimated sewer service
  areas, pack table ``ma_sewer_estimated_tract``), the homes no official
  answer speaks to are split by it: inside an estimated polygon, outside every
  one, or inside only a placeholder circle near a plant (counted as unknown).
  Estimated is its own evidence class, never verified: each draw takes
  ``capacity.estimated_sewer_served_share`` (``e_in``) of the homes inside as
  served and ``capacity.estimated_outside_served_share`` (``e_out``) of those
  outside. Coverage is reported as verified / estimated / unknown.
- ``septic``: ``capacity.septic_buildable_share_single`` for a one-home
  addition (ADU, starter home), ``..._multi`` for a 2-6 unit building or an
  apartment (``assumptions/capacity.yaml``, ``septic_rule``).
- ``F``: the tract's share of homes in FEMA's flood zone (``sfha_share``);
  blank (no digital map) -> the county's mapped share x
  ``capacity.flood_unmapped_factor``; no table -> ``capacity.flood_share_no_data``.
- ``S``: the tract's share of land steeper than 15% (``steep_share``), times
  ``capacity.steep_lot_exposure`` (eligible lots sit on the flatter part);
  no table -> ``capacity.steep_share_no_data``.
- ``k_*``: the share of screened lots still built with mitigation
  (``capacity.flood_buildable_share``, ``capacity.steep_buildable_share``,
  ``capacity.constraint_buildable_share``).

A tract-level reform (ADU, missing middle, TOD) is screened tract by tract. A
county-level reform (parking, fees) adds homes on sites like the baseline's,
which have already passed these tests in practice; only the share
``capacity.county_reform_exposure`` of its homes faces the county's
homes-weighted mean multiplier. Every factor is at most 1, so screening never
adds a home. All values are drawn from the run's seeded streams, so a
screened run is paired with the unscreened one draw by draw.

Which screen binds most: each screen's loss on its own (homes x (1 - that
screen's multiplier)); the one with the largest P50 binds, and the share of
draws in which each screen is largest is reported. Coverage of every screen
(share of tracts and homes measured, the rest drawn from a range) is always
reported, including wildfire at zero.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger import Ledger, LedgerError, sample
from visionhousing.ledger.loader import ROOT, read_yaml

__all__ = [
    "MEASURED_SCREENS",
    "OPTION_SCREEN",
    "PARAMS",
    "SCREENS",
    "CapacityOutcome",
    "CapacitySettings",
    "ScreenArrays",
    "TractInputs",
    "build_capacity",
    "load_settings",
    "multipliers",
    "screen_arrays",
    "screen_result",
    "screen_wanted",
    "tract_inputs",
]

SETTINGS_PATH = ROOT / "assumptions" / "capacity.yaml"
"""The rules file."""

OPTION_SCREEN = "capacity_screen"
"""Reform option (and run option) that asks for the practical capacity screen."""

SCREENS = ("sewer", "flood", "steep", "wildfire")
"""The screens, in report order."""

MEASURED_SCREENS = ("sewer", "flood", "steep")
"""Screens that can remove homes (wildfire has no table in any pack yet)."""

LEGAL_PARTS = ("sewer_outside",)
"""Parts of a screen a reform's own legal rule can already remove (``legal_exclusions``)."""

DEPENDENCE = ("independent", "disjoint", "nested")
"""How the screens combine within a tract, for pass shares a, b, c of the same lots:
``independent`` a x b x c (the reported middle case), ``disjoint`` max(0, a + b + c - 2)
(the excluded lots never coincide: lowest), ``nested`` min(a, b, c) (they coincide
as much as they can: highest)."""

STAGE = "capacity_screened"
"""Funnel stage a screened tract-level reform adds after its own last stage."""

SPAN = "2027-2031"

UNKNOWN_SERVED = "capacity.unknown_sewer_served_share"
SEPTIC_SINGLE = "capacity.septic_buildable_share_single"
SEPTIC_MULTI = "capacity.septic_buildable_share_multi"
CONSTRAINT = "capacity.constraint_buildable_share"
FLOOD_BUILD = "capacity.flood_buildable_share"
FLOOD_UNMAPPED = "capacity.flood_unmapped_factor"
FLOOD_NO_DATA = "capacity.flood_share_no_data"
STEEP_EXPOSURE = "capacity.steep_lot_exposure"
STEEP_BUILD = "capacity.steep_buildable_share"
STEEP_NO_DATA = "capacity.steep_share_no_data"
COUNTY_EXPOSURE = "capacity.county_reform_exposure"
ESTIMATED_IN = "capacity.estimated_sewer_served_share"
ESTIMATED_OUT = "capacity.estimated_outside_served_share"
ESTIMATED_PARAMS = (ESTIMATED_IN, ESTIMATED_OUT)
"""Read only where the pack has an estimated sewer table (``ma_sewer_estimated_tract``)."""
ESTIMATED_TABLE = "ma_sewer_estimated_tract"

PARAMS = (
    UNKNOWN_SERVED,
    SEPTIC_SINGLE,
    SEPTIC_MULTI,
    CONSTRAINT,
    FLOOD_BUILD,
    FLOOD_UNMAPPED,
    FLOOD_NO_DATA,
    STEEP_EXPOSURE,
    STEEP_BUILD,
    STEEP_NO_DATA,
    COUNTY_EXPOSURE,
    ESTIMATED_IN,
    ESTIMATED_OUT,
)
"""Every ledger id the screen reads."""

SCREEN_PARAMS: Mapping[str, tuple[str, ...]] = {
    "sewer": (UNKNOWN_SERVED, SEPTIC_SINGLE, SEPTIC_MULTI, CONSTRAINT, *ESTIMATED_PARAMS),
    "flood": (FLOOD_BUILD, FLOOD_UNMAPPED, FLOOD_NO_DATA),
    "steep": (STEEP_EXPOSURE, STEEP_BUILD, STEEP_NO_DATA),
    "wildfire": (),
}
"""Ledger ids behind each screen (the county exposure is added for county-level reforms)."""

FORMULA = (
    "practical capacity multiplier = sewer x flood x steep slope, per tract and draw; "
    "sewer = verified + outside x septic + constraint x capacity.constraint_buildable_share "
    "+ estimated-inside x (capacity.estimated_sewer_served_share + (1 - that) x septic) "
    "+ estimated-outside x (capacity.estimated_outside_served_share + (1 - that) x septic) "
    "+ unknown x (capacity.unknown_sewer_served_share + (1 - served) x septic); "
    "flood = 1 - flood-zone share x (1 - capacity.flood_buildable_share); "
    "steep = 1 - min(1, steep land share x capacity.steep_lot_exposure) "
    "x (1 - capacity.steep_buildable_share)"
)
"""The multiplier in words (no numbers), for record formulas."""


# ---------------------------------------------------------------- settings


@dataclass(frozen=True)
class CapacitySettings:
    """The rules in ``assumptions/capacity.yaml``.

    Attributes:
        id: File id.
        title: Plain name of the screen.
        question: The question it answers.
        screens: Screen name to ``{"id", "label", "table", "column", "sources"}``.
        septic_rule_id: Id of the septic rule.
        single_families: Families that take the one-home septic share.
        own_sewer_id: Id of the "own sewer screen" rule.
        own_sewer_families: Families whose funnel already screens sewer.
        county_level_id: Id of the county-level rule.
        weight_column: Tract column that weights a county's mean multiplier.
        texts: Sentence templates by name.
    """

    id: str
    title: str
    question: str
    screens: Mapping[str, Mapping[str, Any]]
    septic_rule_id: str
    single_families: tuple[str, ...]
    own_sewer_id: str
    own_sewer_families: tuple[str, ...]
    county_level_id: str
    weight_column: str
    texts: Mapping[str, str] = field(default_factory=dict)
    legal_exclusions_id: str = "capacity.legal_exclusions"
    legal_exclusions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    dependence_id: str = "capacity.dependence"

    def rule_ids(self) -> tuple[str, ...]:
        """Every rule id the file defines."""
        return (
            *(str(v["id"]) for v in self.screens.values()),
            self.septic_rule_id,
            self.own_sewer_id,
            self.county_level_id,
            self.legal_exclusions_id,
            self.dependence_id,
        )


def parse_settings(data: Any) -> CapacitySettings:
    """Check the parsed rules file and build the settings.

    Raises:
        LedgerError: A block or field is missing.
    """
    if not isinstance(data, dict):
        raise LedgerError("capacity.yaml: expected a mapping")
    problems: list[str] = []
    screens = data.get("screens") or {}
    for name in SCREENS:
        entry = screens.get(name) if isinstance(screens, dict) else None
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("label"):
            problems.append(f"screens.{name} needs id and label")
        elif name != "wildfire" and not entry.get("table"):
            problems.append(f"screens.{name} needs a table")
    for block, keys in (
        ("septic_rule", ("id", "single")),
        ("own_sewer_screen", ("id", "families")),
        ("county_level", ("id", "weight_column")),
    ):
        raw = data.get(block)
        if not isinstance(raw, dict) or any(k not in raw for k in keys):
            problems.append(f"{block} needs {', '.join(keys)}")
    texts = data.get("texts") or {}
    for name in (
        "screened",
        "binding",
        "nothing_to_screen",
        "coverage_measured",
        "coverage_unmeasured",
        "coverage_none",
    ):
        if not str(texts.get(name, "")).strip():
            problems.append(f"texts.{name} is missing")
    legal = data.get("legal_exclusions") or {}
    for f, parts in (legal.get("families") or {}).items():
        bad = [x for x in (parts or ()) if x not in LEGAL_PARTS]
        if bad:
            problems.append(f"legal_exclusions.{f}: unknown parts {bad} (known: {LEGAL_PARTS})")
    if problems:
        raise LedgerError("capacity.yaml: " + " | ".join(problems))
    return CapacitySettings(
        id=str(data.get("id", "capacity")),
        title=" ".join(str(data.get("title", "Practical capacity screen")).split()),
        question=" ".join(str(data.get("question", "")).split()),
        screens={
            n: {
                "id": str(screens[n]["id"]),
                "label": str(screens[n]["label"]),
                "table": screens[n].get("table"),
                "column": screens[n].get("column"),
                "sources": tuple(str(x) for x in screens[n].get("sources") or ()),
                "estimated": _estimated_entry(screens[n].get("estimated")),
            }
            for n in SCREENS
        },
        septic_rule_id=str(data["septic_rule"]["id"]),
        single_families=tuple(str(x) for x in data["septic_rule"]["single"] or ()),
        own_sewer_id=str(data["own_sewer_screen"]["id"]),
        own_sewer_families=tuple(str(x) for x in data["own_sewer_screen"]["families"] or ()),
        county_level_id=str(data["county_level"]["id"]),
        weight_column=str(data["county_level"]["weight_column"]),
        texts={k: " ".join(str(v).split()) for k, v in texts.items()},
        legal_exclusions_id=str(legal.get("id", "capacity.legal_exclusions")),
        legal_exclusions={
            str(f): tuple(str(x) for x in (parts or ()))
            for f, parts in (legal.get("families") or {}).items()
        },
        dependence_id=str((data.get("dependence") or {}).get("id", "capacity.dependence")),
    )


def _estimated_entry(raw: Any) -> Mapping[str, Any] | None:
    """The optional ``estimated`` block of a screen (an estimated-evidence table)."""
    if not isinstance(raw, dict) or not raw.get("table"):
        return None
    return {
        "id": str(raw.get("id", "")),
        "label": str(raw.get("label", "estimated service")),
        "table": str(raw["table"]),
        "sources": tuple(str(x) for x in raw.get("sources") or ()),
    }


@lru_cache(maxsize=4)
def _load(path: str, mtime: int) -> CapacitySettings:
    return parse_settings(read_yaml(Path(path)))


def load_settings(path: Path | str | None = None) -> CapacitySettings:
    """Read ``assumptions/capacity.yaml`` (or another file), once per file version."""
    p = Path(path) if path is not None else SETTINGS_PATH
    return _load(str(p), p.stat().st_mtime_ns)


def screen_wanted(family: str, options: Mapping[str, Any] | None) -> bool:
    """True when a reform's options (or its named variant's) ask for the screen."""
    opts = dict(options or {})
    if OPTION_SCREEN in opts:
        return bool(opts[OPTION_SCREEN])
    if not opts.get("variant"):
        return False
    from visionhousing.engine.variants import expand_options

    try:
        return bool(expand_options(family, opts).get(OPTION_SCREEN, False))
    except (KeyError, ValueError):
        return False


# ---------------------------------------------------------------- tract inputs


@dataclass(frozen=True)
class TractInputs:
    """Each tract's screening shares, read once per state context.

    One row per tract of ``StateContext.tracts`` plus a last row of a tract no
    table speaks to (used for any geography the tract table does not list).

    Attributes:
        geoids: Tract ids (the last row is ``""``).
        counties: County id of each row.
        homes: Weight of each row (``county_level.weight_column``; the last row 0).
        verified: Share of homes in a mapped public sewer service area.
        outside: Share outside every service area of a complete county map.
        constraint: Share under a documented sewer constraint.
        unknown: Share no official record speaks to.
        sewer_measured: The tract has an official sewer answer (not unknown, not blank).
        estimated_in: Share of homes with no official answer that lie inside an
            estimated service polygon (0 without an estimated table).
        estimated_out: Share with no official answer outside every estimated
            polygon (0 without an estimated table).
        flood: Share of homes in FEMA's flood zone; NaN where not measured.
        flood_fill: County (else state) homes-weighted mapped share; NaN when no tract has one.
        steep: Share of land steeper than 15%; NaN where not measured.
        steep_fill: County (else state) homes-weighted share; NaN when no tract has one.
        tables: Screen name to whether its pack table is present.
    """

    geoids: np.ndarray
    counties: np.ndarray
    homes: np.ndarray
    verified: np.ndarray
    outside: np.ndarray
    constraint: np.ndarray
    unknown: np.ndarray
    sewer_measured: np.ndarray
    flood: np.ndarray
    flood_fill: np.ndarray
    steep: np.ndarray
    steep_fill: np.ndarray
    tables: Mapping[str, bool]
    estimated_in: np.ndarray | None = None
    estimated_out: np.ndarray | None = None

    def estimated(self) -> bool:
        """True when an estimated sewer table speaks to at least one tract."""
        return bool(self.tables.get("sewer_estimated", False))

    def rows(self, geoids: Sequence[str]) -> np.ndarray:
        """Row numbers of ``geoids``; a geography not in the tract table gets the last row."""
        index = {g: i for i, g in enumerate(self.geoids[:-1])}
        last = len(self.geoids) - 1
        return np.array([index.get(str(g), last) for g in geoids], dtype=int)


def _fill(values: np.ndarray, counties: np.ndarray, homes: np.ndarray) -> np.ndarray:
    """Per tract: the homes-weighted mean of the measured values in its county, else the state's."""
    ok = np.isfinite(values)
    w = np.where(ok, np.maximum(homes, 0.0), 0.0)
    if not ok.any():
        return np.full(values.shape, np.nan)
    state_w = w.sum()
    state = (
        float(np.nansum(np.where(ok, values, 0.0) * w) / state_w)
        if state_w > 0
        else float(np.nanmean(values[ok]))
    )
    frame = pd.DataFrame({"c": counties, "vw": np.where(ok, values, 0.0) * w, "w": w})
    sums = frame.groupby("c")[["vw", "w"]].sum()
    county = (sums["vw"] / sums["w"]).where(sums["w"] > 0)
    out = pd.Series(counties).map(county).to_numpy(dtype=float)
    return np.where(np.isfinite(out), out, state)


def tract_inputs(state: Any, settings: CapacitySettings | None = None) -> TractInputs:
    """The screening shares of every tract (cached on ``state.derived``).

    Args:
        state: The ``StateContext``.
        settings: The rules; read from ``assumptions/capacity.yaml`` when ``None``.

    Returns:
        The inputs.
    """
    s = settings or load_settings()
    key = ("capacity", "inputs", s.id)
    if key in state.derived:
        return state.derived[key]
    from visionhousing.scenarios.extra_common import pack_table
    from visionhousing.scenarios.tod_infrastructure import readiness_shares

    tracts = state.tracts
    geo = tracts["geoid"].astype(str)
    counties = tracts["county"].astype(str).to_numpy()
    raw_homes = (
        tracts[s.weight_column] if s.weight_column in tracts else pd.Series(1.0, index=tracts.index)
    )
    homes = np.clip(pd.to_numeric(raw_homes, errors="coerce").fillna(0.0).to_numpy(float), 0, None)
    infra = pack_table(state, "infra_tract")
    shares = readiness_shares(tracts, infra)
    measured = np.zeros(len(tracts), dtype=bool)
    if infra is not None and "sewer_status" in infra:
        by_geo = infra.assign(geoid=infra["geoid"].astype(str)).set_index("geoid")
        status = geo.map(by_geo["sewer_status"])
        measured = status.isin(["verified", "outside", "constraint"]).to_numpy()
    unknown = shares["unknown"].to_numpy(float)
    est_in = np.zeros(len(tracts))
    est_out = np.zeros(len(tracts))
    est_entry = s.screens["sewer"].get("estimated")
    est = pack_table(state, est_entry["table"]) if est_entry else None
    has_est = est is not None and not est.empty and "est_share" in est
    if has_est:
        e = est.assign(geoid=est["geoid"].astype(str)).drop_duplicates("geoid").set_index("geoid")

        def share_of(name: str) -> np.ndarray:
            if name not in e:
                return np.zeros(len(tracts))
            vals = pd.to_numeric(e[name], errors="coerce")
            return np.clip(np.nan_to_num(geo.map(vals).to_numpy(dtype=float)), 0.0, 1.0)

        listed = geo.isin(e.index).to_numpy()
        inside = share_of("est_share")
        circle = np.minimum(share_of("circle_only_share"), 1.0 - inside)
        outside_e = np.where(listed, np.clip(1.0 - inside - circle, 0.0, 1.0), 0.0)
        # Only the homes no official answer speaks to; placeholder circles stay unknown.
        est_in = unknown * inside
        est_out = unknown * outside_e
        unknown = np.clip(unknown - est_in - est_out, 0.0, 1.0)
    hazard = pack_table(state, "hazard_tract")

    def column(name: str | None) -> np.ndarray:
        if hazard is None or not name or name not in hazard:
            return np.full(len(tracts), np.nan)
        vals = pd.to_numeric(hazard.set_index(hazard["geoid"].astype(str))[name], errors="coerce")
        return np.clip(geo.map(vals).to_numpy(dtype=float), 0.0, 1.0)

    flood = column(s.screens["flood"].get("column"))
    steep = column(s.screens["steep"].get("column"))
    flood_fill = _fill(flood, counties, homes)
    steep_fill = _fill(steep, counties, homes)

    def state_fill(fill: np.ndarray) -> float:
        ok = np.isfinite(fill)
        if not ok.any():
            return np.nan
        w = homes[ok]
        return float((fill[ok] * w).sum() / w.sum()) if w.sum() > 0 else float(fill[ok].mean())

    def plus(arr: np.ndarray, last: Any) -> np.ndarray:
        return np.append(arr, last)

    out = TractInputs(
        geoids=plus(geo.to_numpy(dtype=object), ""),
        counties=plus(counties.astype(object), ""),
        homes=plus(homes, 0.0),
        verified=plus(shares["verified"].to_numpy(float), 0.0),
        outside=plus(shares["outside"].to_numpy(float), 0.0),
        constraint=plus(shares["constraint"].to_numpy(float), 0.0),
        unknown=plus(unknown, 1.0),
        sewer_measured=plus(measured, False),
        flood=plus(flood, np.nan),
        flood_fill=plus(flood_fill, state_fill(flood_fill)),
        steep=plus(steep, np.nan),
        steep_fill=plus(steep_fill, state_fill(steep_fill)),
        tables={
            "sewer": infra is not None and not infra.empty,
            "flood": hazard is not None and bool(np.isfinite(flood).any()),
            "steep": hazard is not None and bool(np.isfinite(steep).any()),
            "wildfire": False,
            "sewer_estimated": bool(has_est),
        },
        estimated_in=plus(est_in, 0.0),
        estimated_out=plus(est_out, 0.0),
    )
    state.derived[key] = out
    return out


# ---------------------------------------------------------------- the multipliers


def _septic_id(family: str, s: CapacitySettings) -> str:
    return SEPTIC_SINGLE if family in s.single_families else SEPTIC_MULTI


def multipliers(
    inp: TractInputs,
    rows: np.ndarray,
    v: Mapping[str, np.ndarray],
    family: str,
    settings: CapacitySettings | None = None,
) -> dict[str, np.ndarray]:
    """Each screen's multiplier for the given tract rows, shape ``(rows, draws)``, in [0, 1].

    Args:
        inp: The tract inputs (:func:`tract_inputs`).
        rows: Row numbers into ``inp`` (:meth:`TractInputs.rows`).
        v: Ledger draws of :data:`PARAMS`, each ``(draws,)``.
        family: The reform family (picks the septic share; may skip the sewer screen).
        settings: The rules.

    Returns:
        Screen name (:data:`SCREENS`) to its multiplier.
    """
    s = settings or load_settings()
    n = len(v[UNKNOWN_SERVED])
    ones = np.ones((len(rows), n))

    def col(a: np.ndarray) -> np.ndarray:
        return np.asarray(a, dtype=float)[rows][:, None]

    def draw(pid: str) -> np.ndarray:
        return np.clip(np.asarray(v[pid], dtype=float), 0.0, None)[None, :]

    out: dict[str, np.ndarray] = {}
    if family in s.own_sewer_families:
        out["sewer"] = ones
    else:
        septic = np.clip(draw(_septic_id(family, s)), 0.0, 1.0)
        served = np.clip(draw(UNKNOWN_SERVED), 0.0, 1.0)
        kcon = np.clip(draw(CONSTRAINT), 0.0, 1.0)
        rest = (
            col(inp.verified)
            + col(inp.constraint) * kcon
            + col(inp.unknown) * (served + (1.0 - served) * septic)
        )
        if inp.estimated() and ESTIMATED_IN in v and ESTIMATED_OUT in v:
            e_in = np.clip(draw(ESTIMATED_IN), 0.0, 1.0)
            e_out = np.clip(draw(ESTIMATED_OUT), 0.0, 1.0)
            rest = (
                rest
                + col(inp.estimated_in) * (e_in + (1.0 - e_in) * septic)
                + col(inp.estimated_out) * (e_out + (1.0 - e_out) * septic)
            )
        if "sewer_outside" in s.legal_exclusions.get(family, ()):
            # The reform's own rule already dropped the lots outside a mapped area;
            # screen only the lots left: rest / (1 - outside).
            left = 1.0 - col(inp.outside)
            out["sewer"] = np.clip(
                np.where(left > 1e-12, rest / np.where(left > 1e-12, left, 1.0), 1.0), 0.0, 1.0
            )
            out["sewer"] = np.broadcast_to(out["sewer"], ones.shape).copy()
        else:
            out["sewer"] = np.clip(rest + col(inp.outside) * septic, 0.0, 1.0)

    def share(
        measured: np.ndarray, fill: np.ndarray, no_data: str, factor: str | None
    ) -> np.ndarray:
        m, f = col(measured), col(fill)
        filled = f * (draw(factor) if factor else 1.0)
        filled = np.where(np.isfinite(f), filled, draw(no_data))
        return np.clip(np.where(np.isfinite(m), m, filled), 0.0, 1.0)

    flood = share(inp.flood, inp.flood_fill, FLOOD_NO_DATA, FLOOD_UNMAPPED)
    out["flood"] = 1.0 - flood * (1.0 - np.clip(draw(FLOOD_BUILD), 0.0, 1.0))
    steep = share(inp.steep, inp.steep_fill, STEEP_NO_DATA, None)
    lot = np.clip(steep * draw(STEEP_EXPOSURE), 0.0, 1.0)
    out["steep"] = 1.0 - lot * (1.0 - np.clip(draw(STEEP_BUILD), 0.0, 1.0))
    out["wildfire"] = ones
    return {k: out[k] for k in SCREENS}


@dataclass(frozen=True)
class ScreenArrays:
    """One reform's screen, by geography (the reform's rows) and draw.

    Attributes:
        multiplier: The combined multiplier applied to the reform's homes.
        screened: Added homes after the screen (``added x multiplier``), the
            screens taken as independent.
        bounds: ``disjoint`` and ``nested``: added homes after the screen when
            the excluded lots never coincide (lowest) or coincide as much as
            they can (highest); see :data:`DEPENDENCE`. ``independent`` is
            ``screened``.
        losses: Screen name to homes that screen removes on its own.
        assumption_ids: Ledger ids behind the screen for this reform.
        source_ids: Sources of the tables read.
        expression: The per-geography expression, in words.
    """

    multiplier: np.ndarray = field(repr=False)
    screened: np.ndarray = field(repr=False)
    losses: Mapping[str, np.ndarray] = field(repr=False)
    assumption_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    expression: str = ""
    bounds: Mapping[str, np.ndarray] = field(default_factory=dict, repr=False)


def _county_mean(arr: np.ndarray, inp: TractInputs, counties: Sequence[str]) -> np.ndarray:
    """Homes-weighted mean of ``arr`` (all tract rows x draws) per county, ``(counties, draws)``.

    A county with no weighted tract takes the state's weighted mean.
    """
    w = inp.homes[:-1]
    data = np.asarray(arr, dtype=float)[:-1]
    total = w.sum()
    state = (w[:, None] * data).sum(axis=0) / total if total > 0 else data.mean(axis=0)
    codes = pd.Index([str(c) for c in counties])
    pos = codes.get_indexer(inp.counties[:-1].astype(str))
    out = np.zeros((len(codes), data.shape[1]))
    weight = np.zeros(len(codes))
    ok = pos >= 0
    np.add.at(out, pos[ok], w[ok, None] * data[ok])
    np.add.at(weight, pos[ok], w[ok])
    has = weight > 0
    out[has] /= weight[has, None]
    out[~has] = state
    return out


def screen_arrays(
    added: np.ndarray,
    level: str,
    geoids: Sequence[str],
    inp: TractInputs,
    v: Mapping[str, np.ndarray],
    family: str,
    settings: CapacitySettings | None = None,
) -> ScreenArrays:
    """Apply the screen to one reform's added homes.

    Args:
        added: Added homes, ``(geographies, draws)``.
        level: ``"tract"`` or ``"county"``.
        geoids: The geographies of ``added``'s rows.
        inp: The tract inputs.
        v: Ledger draws of :data:`PARAMS`.
        family: The reform family.
        settings: The rules.

    Returns:
        The multiplier, the screened homes and each screen's own loss.
    """
    s = settings or load_settings()
    added = np.asarray(added, dtype=float)
    ids = {pid for name in SCREENS for pid in SCREEN_PARAMS[name]}
    ids -= {SEPTIC_SINGLE, SEPTIC_MULTI} - {_septic_id(family, s)}
    if family in s.own_sewer_families:
        ids -= set(SCREEN_PARAMS["sewer"])
    if not inp.estimated():
        ids -= set(ESTIMATED_PARAMS)
    sources = sorted(
        {src for n in MEASURED_SCREENS if inp.tables[n] for src in s.screens[n]["sources"]}
        | set(_estimated_sources(inp, s, family))
    )
    if level == "tract":
        m = multipliers(inp, inp.rows(geoids), v, family, s)
        combined = m["sewer"] * m["flood"] * m["steep"] * m["wildfire"]
        lo_m, hi_m = _frechet(m)
        bounds = {"disjoint": added * lo_m, "nested": added * hi_m}
        losses = {k: added * (1.0 - m[k]) for k in SCREENS}
        expression = f"added units x the tract's {FORMULA}"
    else:
        every = np.arange(len(inp.geoids))
        m = multipliers(inp, every, v, family, s)
        product = m["sewer"] * m["flood"] * m["steep"] * m["wildfire"]
        x = np.clip(np.asarray(v[COUNTY_EXPOSURE], dtype=float), 0.0, 1.0)[None, :]
        county_m = _county_mean(product, inp, geoids)
        combined = 1.0 - x * (1.0 - county_m)
        lo_m, hi_m = _frechet(m)
        bounds = {
            "disjoint": added * np.clip(1.0 - x * (1.0 - _county_mean(lo_m, inp, geoids)), 0, 1),
            "nested": added * np.clip(1.0 - x * (1.0 - _county_mean(hi_m, inp, geoids)), 0, 1),
        }
        losses = {k: added * x * (1.0 - _county_mean(m[k], inp, geoids)) for k in SCREENS}
        ids.add(COUNTY_EXPOSURE)
        expression = (
            "added units x (1 - capacity.county_reform_exposure x (1 - the county's "
            f"homes-weighted mean over its tracts of the {FORMULA}))"
        )
    combined = np.clip(combined, 0.0, 1.0)
    return ScreenArrays(
        multiplier=combined,
        screened=added * combined,
        losses=losses,
        assumption_ids=tuple(sorted(ids)),
        source_ids=tuple(sources),
        expression=expression,
        bounds=bounds,
    )


def _estimated_sources(inp: TractInputs, s: CapacitySettings, family: str) -> tuple[str, ...]:
    """Sources of the estimated sewer table, when it is read for this family."""
    entry = s.screens["sewer"].get("estimated")
    if not inp.estimated() or not entry or family in s.own_sewer_families:
        return ()
    return tuple(entry["sources"])


def _frechet(m: Mapping[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Bounds on the share of lots passing every screen, from each screen's own pass share.

    With pass shares ``a_1 .. a_K`` of the same lots and nothing known about
    where they coincide, the share passing all of them lies between
    ``max(0, a_1 + ... + a_K - (K - 1))`` and ``min(a_1, ..., a_K)`` (the
    Frechet bounds). Independence (the product) is always inside.
    """
    stack = np.stack([np.asarray(m[k], dtype=float) for k in SCREENS])
    k = stack.shape[0]
    lo = np.clip(stack.sum(axis=0) - (k - 1), 0.0, 1.0)
    hi = np.clip(stack.min(axis=0), 0.0, 1.0)
    return lo, hi


def _draws(ledger: Ledger, n: int, seed: int) -> dict[str, np.ndarray]:
    """The screen's ledger draws: the run's seeded stream of each id, so runs pair draw by draw."""
    return sample(ledger, n=n, seed=seed, ids=PARAMS)


def _strip(formula: str) -> str:
    _, sep, tail = formula.partition(" seeded draws of ")
    text = tail if sep else formula
    return text.rsplit(" = ", 1)[0] if " = " in text else text


# ---------------------------------------------------------------- the variant path


def screen_result(res: Any, context: Any, draws: Any, family: str) -> Any:
    """The reform's own result with its headline screened (the variant path).

    Added homes (and net stock, removed homes and baseline homes on the sites)
    are multiplied by the practical capacity multiplier. A tract-level reform
    gets one more funnel stage (``capacity_screened``) after its own last
    stage, so the funnel still ends at the headline. Each screen's own loss and
    the not-screened total are added as records.

    Args:
        res: The adapter's ``ReformResult``.
        context: The ``RunContext``.
        draws: The run's ``Draws``.
        family: The reform family.

    Returns:
        A new ``ReformResult``.
    """
    s = load_settings()
    inp = tract_inputs(context.state, s)
    ledger = res.ledger if res.ledger is not None else draws.ledger
    v = _draws(ledger, draws.n, draws.seed)
    arrs = screen_arrays(res.added, res.level, res.geoids, inp, v, family, s)
    head = res.headline
    ids = tuple(sorted({*head.assumption_ids, *arrs.assumption_ids}))
    sources = tuple(sorted({*head.source_ids, *arrs.source_ids}))
    common = {"assumption_ids": ids, "source_ids": sources, "tier": head.tier}
    roll = "the sum over tracts of " if res.level == "tract" else "the sum over counties of "
    headline = rec.from_draws(
        head.name,
        arrs.screened.sum(axis=0),
        head.unit,
        expression=f"{roll}{arrs.expression}; added units per draw: {_strip(head.formula)}",
        **common,
    )
    step = rec.from_draws(
        f"homes after the practical capacity screen {SPAN}, state",
        arrs.screened.sum(axis=0),
        head.unit,
        expression=f"{roll}{arrs.expression}",
        **common,
    )
    records = dict(res.records)
    records["capacity_not_screened"] = rec.from_draws(
        f"{head.name}, not screened",
        np.asarray(res.added, dtype=float).sum(axis=0),
        head.unit,
        expression=_strip(head.formula),
        assumption_ids=head.assumption_ids,
        source_ids=head.source_ids,
        tier=head.tier,
    )
    for name in MEASURED_SCREENS:
        records[f"capacity_loss_{name}"] = rec.from_draws(
            f"homes the {s.screens[name]['label']} screen removes on its own {SPAN}, state",
            arrs.losses[name].sum(axis=0),
            head.unit,
            expression=f"{roll}added units x (1 - the {name} multiplier)",
            **common,
        )
    records.update(_bound_records(arrs, head.name, head.unit, roll, common, s))
    steps = tuple(res.steps)
    detail = _screen_work_types(res.detail, res.added, arrs.screened, res.level)
    stages = getattr(detail, "stages", None)
    if res.level == "tract":
        if (
            isinstance(stages, Mapping)
            and len(stages) == len(steps)
            and dataclasses.is_dataclass(detail)
        ):
            detail = dataclasses.replace(
                detail, stages={**dict(stages), STAGE: np.ascontiguousarray(arrs.screened.T)}
            )
            steps = (*steps, step)
    else:
        steps = (*steps, step)
    m = arrs.multiplier

    def scaled(a: Any) -> Any:
        return None if a is None else np.asarray(a, dtype=float) * m

    note = (
        "Practical capacity screen on (not the benchmark preset): added homes x the "
        "practical capacity multiplier for sewer service, FEMA flood zone and steep slope "
        "(engine.capacity, assumptions/capacity.yaml); unknown sewer and unmapped flood "
        "answers are drawn from ledger ranges, never assumed to pass; wildfire is not in the pack."
    )
    return dataclasses.replace(
        res,
        added=arrs.screened,
        headline=headline,
        steps=steps,
        records=records,
        assumption_ids=tuple(sorted({*res.assumption_ids, *arrs.assumption_ids})),
        notes=(*res.notes, note),
        detail=detail,
        net_stock=scaled(res.net_stock),
        removed=scaled(res.removed),
        baseline_on_sites=scaled(res.baseline_on_sites),
    )


def _screen_work_types(detail: Any, added: Any, screened: np.ndarray, level: str) -> Any:
    """The module detail with its state work types screened like the headline.

    A module that splits its units by work type (ADU, missing middle) keeps
    ``work_types`` (state per draw) and, where it has them, ``work_shares``
    (each tract's share by work type). With shares, each work type is
    ``sum over tracts of screened units x share``: exact. Without them, each is
    scaled per draw by screened / not-screened state units. Either way the work
    types add up to the screened headline in every draw, so the five record
    types (``engine.outcome.accounting``) keep the module's split.
    """
    given = getattr(detail, "work_types", None)
    if not given or not dataclasses.is_dataclass(detail):
        return detail
    after = np.asarray(screened, dtype=float)  # geographies x draws
    shares = getattr(detail, "work_shares", None) or {}
    out: dict[str, np.ndarray] = {}
    if level == "tract" and shares and set(shares) == set(given):
        for w, share in shares.items():
            sh = np.asarray(share, dtype=float)
            sh = sh[None, :] if sh.ndim == 1 else sh  # draws x tracts
            out[w] = (after.T * sh).sum(axis=1)
    else:
        before = np.asarray(added, dtype=float).sum(axis=0)
        now = after.sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(np.abs(before) > 1e-12, now / np.where(before != 0, before, 1.0), 0.0)
        out = {w: np.asarray(v, dtype=float) * ratio for w, v in given.items()}
    return dataclasses.replace(detail, work_types=out)


# ---------------------------------------------------------------- the package option


@dataclass(frozen=True)
class CapacityOutcome:
    """Every reform in the run (and the package), screened beside today's numbers.

    Attributes:
        title: Plain name.
        question: The question the screen answers.
        reforms: Reform key to its state records: ``not_screened`` (today's
            headline, the same draws), ``screened``, ``change`` (screened - not
            screened, never above 0), ``change_pct``, ``loss_<screen>`` (homes
            one screen removes on its own) and ``screened_disjoint`` /
            ``screened_nested`` (the ends of the dependence range; ``screened``
            takes the screens as independent).
        binding: Reform key to ``{"screen", "label", "share_of_draws", "text",
            "dependence_text"}``: the screen with the largest P50 loss on its
            own, the share of draws in which each screen removes the most, and
            the sentence on the dependence range.
        counties: Reform key to county id to ``{"screened", "change"}`` records.
        package: ``not_screened``, ``screened`` and ``change`` records of the package.
        coverage: Screen name to ``{"id", "label", "measured", "tracts",
            "tracts_measured", "tracts_share", "homes_share", "text"}``.
        texts: Reform key to its rule-written "screened" sentence.
        notes: Plain statements on the rules and limits.
        state_draws: ``<key>`` / ``package`` to screened state draws (for checks).
        county_draws: ``<key>`` / ``package`` to screened county draws.
    """

    title: str
    question: str
    reforms: Mapping[str, Mapping[str, Record]]
    binding: Mapping[str, Mapping[str, Any]]
    counties: Mapping[str, Mapping[str, Mapping[str, Record]]]
    package: Mapping[str, Record]
    coverage: Mapping[str, Mapping[str, Any]]
    texts: Mapping[str, str]
    notes: tuple[str, ...]
    state_draws: Mapping[str, np.ndarray] = field(default_factory=dict, repr=False, compare=False)
    county_draws: Mapping[str, np.ndarray] = field(default_factory=dict, repr=False, compare=False)

    def records(self) -> list[Record]:
        """Every record, in a stable order."""
        out: list[Record] = []
        for key, recs in self.reforms.items():
            out += list(recs.values())
            for by in self.counties.get(key, {}).values():
                out += list(by.values())
        out += list(self.package.values())
        for c in self.coverage.values():
            out += [x for x in c.values() if isinstance(x, Record)]
        return out

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready (records as their model dumps; no draws)."""

        def r(x: Record) -> dict[str, Any]:
            return x.model_dump(mode="json")

        def plain(d: Mapping[str, Any]) -> dict[str, Any]:
            return {k: (r(x) if isinstance(x, Record) else x) for k, x in d.items()}

        return {
            "title": self.title,
            "question": self.question,
            "reforms": {k: {n: r(x) for n, x in m.items()} for k, m in self.reforms.items()},
            "binding": {k: plain(b) for k, b in self.binding.items()},
            "counties": {
                k: {c: {n: r(x) for n, x in by.items()} for c, by in m.items()}
                for k, m in self.counties.items()
            },
            "package": {k: r(x) for k, x in self.package.items()},
            "coverage": {k: plain(c) for k, c in self.coverage.items()},
            "texts": dict(self.texts),
            "notes": list(self.notes),
        }


def _whole(value: float | None) -> str:
    return "not available" if value is None else f"{round(float(value)):,}"


def _pct(value: float) -> str:
    return f"{round(100.0 * float(value))}%"


def _coverage(inp: TractInputs, s: CapacitySettings) -> dict[str, dict[str, Any]]:
    """Share of tracts and homes each screen measures (the rest drawn from a range)."""
    homes = inp.homes[:-1]
    total = float(homes.sum())
    n = len(homes)
    estimated = np.zeros(n)
    if inp.estimated_in is not None and inp.estimated_out is not None:
        estimated = inp.estimated_in[:-1] + inp.estimated_out[:-1]
    measured = {
        "sewer": (inp.sewer_measured[:-1], 1.0 - inp.unknown[:-1] - estimated),
        "flood": (np.isfinite(inp.flood[:-1]), np.isfinite(inp.flood[:-1]).astype(float)),
        "steep": (np.isfinite(inp.steep[:-1]), np.isfinite(inp.steep[:-1]).astype(float)),
        "wildfire": (np.zeros(n, dtype=bool), np.zeros(n)),
    }
    out: dict[str, dict[str, Any]] = {}
    for name in SCREENS:
        entry = s.screens[name]
        tracts_ok, home_share = measured[name]
        has = bool(inp.tables[name])
        t_share = float(tracts_ok.mean()) if n and has else 0.0
        h_share = float((homes * home_share).sum() / total) if total > 0 and has else 0.0
        label = entry["label"]
        label_cap = label[0].upper() + label[1:]
        if has:
            text = s.texts["coverage_measured"].format(
                label_cap=label_cap, homes=_pct(h_share), tracts=_pct(t_share)
            )
        elif name in MEASURED_SCREENS:
            text = s.texts["coverage_unmeasured"].format(label_cap=label_cap)
        else:
            text = s.texts["coverage_none"].format(label_cap=label_cap)
        table = entry["table"] or "none"
        sources = list(entry["sources"]) if has else []
        how = (
            "homes with an official sewer answer (verified, outside or constraint) / all homes"
            if name == "sewer"
            else f"homes in tracts with a {entry.get('column') or name} value / all homes"
        )
        out[name] = {
            "id": entry["id"],
            "label": label,
            "measured": has,
            "table": table,
            "tracts": n,
            "tracts_measured": int(tracts_ok.sum()) if has else 0,
            "tracts_share": rec.quantity(
                f"share of tracts the {label} screen measures",
                t_share,
                "share",
                formula=(
                    f"tracts measured in pack table {table} / all {n:,} tracts = {t_share:.3f}"
                    if has
                    else f"no {label} table in this state's pack = 0"
                ),
                source_ids=sources,
            ),
            "homes_share": rec.quantity(
                f"share of homes the {label} screen measures",
                h_share,
                "share",
                formula=(
                    f"{how} ({s.weight_column}) = {h_share:.3f}"
                    if has
                    else f"no {label} table in this state's pack = 0"
                ),
                source_ids=sources,
            ),
            "text": text,
        }
    out["sewer"].update(_sewer_split(inp, s, homes, total))
    if out["sewer"]["split_text"]:
        out["sewer"]["text"] = f"{out['sewer']['text']} {out['sewer']['split_text']}"
    return out


def _sewer_split(
    inp: TractInputs, s: CapacitySettings, homes: np.ndarray, total: float
) -> dict[str, Any]:
    """Sewer coverage by evidence class: verified (official), estimated, unknown.

    Verified covers every official answer (in a mapped service area, outside a
    complete county map, or under a documented constraint); estimated is a
    published estimate (MassDEP), inside or outside its polygons; unknown is
    the rest, including homes inside only a placeholder circle.
    """
    n = len(homes)
    est_in = np.asarray(inp.estimated_in if inp.estimated_in is not None else np.zeros(n + 1))
    est_out = np.asarray(inp.estimated_out if inp.estimated_out is not None else np.zeros(n + 1))
    parts = {
        "verified": 1.0 - inp.unknown[:-1] - est_in[:-1] - est_out[:-1],
        "estimated": est_in[:-1] + est_out[:-1],
        "estimated_inside": est_in[:-1],
        "unknown": inp.unknown[:-1],
    }
    has = bool(inp.tables["sewer"]) or inp.estimated()
    entry = s.screens["sewer"].get("estimated")
    sources = list(s.screens["sewer"]["sources"]) if inp.tables["sewer"] else []
    if inp.estimated() and entry:
        sources += list(entry["sources"])
    words = {
        "verified": "official answers (verified service, outside a complete county map, or a "
        "documented constraint)",
        "estimated": "a published estimate only (inside or outside an estimated service polygon)",
        "estimated_inside": "inside an estimated service polygon (placeholder circles left out)",
        "unknown": "no answer (including homes inside only a placeholder circle)",
    }
    out: dict[str, Any] = {}
    shares: dict[str, float] = {}
    for name, arr in parts.items():
        val = float((homes * np.clip(arr, 0.0, 1.0)).sum() / total) if total > 0 and has else 0.0
        if name == "unknown" and not has:
            val = 1.0
        shares[name] = val
        out[f"homes_{name}_share"] = rec.quantity(
            f"share of homes whose sewer evidence is {name.replace('_', ' ')}",
            val,
            "share",
            formula=(
                f"homes ({s.weight_column}) with {words[name]} / all homes = {val:.3f}"
                + (
                    f"; estimated table {entry['table']} (its own evidence class, not verified)"
                    if name.startswith("estimated") and inp.estimated() and entry
                    else ""
                )
            ),
            source_ids=sources,
        )
    template = s.texts.get("coverage_sewer_split", "")
    out["split_text"] = (
        template.format(
            verified=_pct(shares["verified"]),
            estimated=_pct(shares["estimated"]),
            inside=_pct(shares["estimated_inside"]),
            unknown=_pct(shares["unknown"]),
        )
        if template and inp.estimated()
        else ""
    )
    return out


def build_capacity(
    keys: Sequence[str],
    outcomes: Mapping[str, Any],
    package: Any,
    run_context: Any,
    shared: Any,
    county_ids: tuple[str, ...],
    settings: CapacitySettings | None = None,
) -> CapacityOutcome:
    """Screen every reform of a run, and the package, beside today's numbers.

    Args:
        keys: Reform keys, in run order.
        outcomes: Key to its ``ReformOutcome`` (with ``result`` still attached).
        package: The ``PackageOutcome``.
        run_context: The ``RunContext``.
        shared: The run's ``Draws``.
        county_ids: County order.
        settings: The rules; read from ``assumptions/capacity.yaml`` when ``None``.

    Returns:
        The outcome.
    """
    from visionhousing.engine.registry import get_reform

    s = settings or load_settings()
    inp = tract_inputs(run_context.state, s)
    v = _draws(shared.ledger, shared.n, shared.seed)
    reforms: dict[str, dict[str, Record]] = {}
    binding: dict[str, dict[str, Any]] = {}
    counties: dict[str, dict[str, dict[str, Record]]] = {}
    texts: dict[str, str] = {}
    state_draws: dict[str, np.ndarray] = {}
    county_draws: dict[str, np.ndarray] = {}
    screened_sum = np.zeros((len(county_ids), shared.n))
    alone_sum = np.zeros((len(county_ids), shared.n))
    all_ids: set[str] = set()
    all_sources: set[str] = set()
    for key in keys:
        o = outcomes[key]
        res = o.result
        family = get_reform(key).family
        arrs = screen_arrays(res.added, res.level, res.geoids, inp, v, family, s)
        by_county = res.county_sum(arrs.screened, county_ids)
        before_c = np.asarray(o.county_draws, dtype=float)
        before = np.asarray(o.state_draws, dtype=float)
        after = by_county.sum(axis=0)
        head = o.added
        ids = sorted({*head.assumption_ids, *arrs.assumption_ids})
        sources = sorted({*head.source_ids, *arrs.source_ids})
        all_ids |= set(ids)
        all_sources |= set(sources)
        common = {"assumption_ids": ids, "source_ids": sources, "tier": head.tier}
        roll = "the sum over tracts of " if res.level == "tract" else "the sum over counties of "
        what = f"added units authorized, {o.title}"
        recs = {
            "not_screened": head,
            "screened": rec.from_draws(
                f"{what}, practical capacity screened {SPAN}, state",
                after,
                "units",
                expression=f"{roll}{arrs.expression}",
                **common,
            ),
            "change": rec.from_draws(
                f"change from the practical capacity screen, {o.title} {SPAN}, state",
                after - before,
                "units",
                expression="screened added units - not-screened added units, in each draw",
                **common,
            ),
            "change_pct": rec.from_draws(
                f"change from the practical capacity screen, {o.title}, % of not screened",
                np.where(
                    np.abs(before) > 1e-9,
                    100.0 * (after - before) / np.where(np.abs(before) > 1e-9, before, 1.0),
                    0.0,
                ),
                "%",
                expression="100 x (screened - not screened) / not screened, in each draw "
                "(0 where the reform adds nothing)",
                **common,
            ),
        }
        loss_state = {}
        for name in MEASURED_SCREENS:
            loss_state[name] = arrs.losses[name].sum(axis=0)
            recs[f"loss_{name}"] = rec.from_draws(
                f"homes the {s.screens[name]['label']} screen removes on its own, "
                f"{o.title} {SPAN}, state",
                loss_state[name],
                "units",
                expression=f"{roll}added units x (1 - the {name} multiplier"
                + (" x county exposure)" if res.level == "county" else ")"),
                **common,
            )
        bound = _bound_records(arrs, f"{what} {SPAN}, state", "units", roll, common, s)
        recs["screened_disjoint"] = bound["capacity_screened_disjoint"]
        recs["screened_nested"] = bound["capacity_screened_nested"]
        reforms[key] = recs
        binding[key] = {
            **_binding(key, o.title, recs, loss_state, s),
            "dependence_text": _dependence_text(recs, s),
        }
        texts[key] = s.texts["screened"].format(
            title=o.title,
            p10=_whole(recs["screened"].p10),
            p50=_whole(recs["screened"].p50),
            p90=_whole(recs["screened"].p90),
            u10=_whole(head.p10),
            u50=_whole(head.p50),
            u90=_whole(head.p90),
        )
        counties[key] = {
            c: {
                "screened": rec.from_draws(
                    f"{what}, practical capacity screened {SPAN}, county {c}",
                    by_county[i],
                    "units",
                    expression=f"the county's {arrs.expression}",
                    **common,
                ),
                "change": rec.from_draws(
                    f"change from the practical capacity screen, {o.title} {SPAN}, county {c}",
                    by_county[i] - before_c[i],
                    "units",
                    expression="screened - not-screened added units in the county, in each draw",
                    **common,
                ),
            }
            for i, c in enumerate(county_ids)
        }
        state_draws[key] = after
        county_draws[key] = by_county
        screened_sum += by_county
        alone_sum += before_c
    pkg: dict[str, Record] = {}
    if package is not None:
        ok = np.abs(alone_sum) > 1e-9
        ratio = np.where(ok, screened_sum / np.where(ok, alone_sum, 1.0), 1.0)
        ratio = np.clip(ratio, 0.0, 1.0)
        pkg_c = ratio * np.asarray(package.county_draws, dtype=float)
        pkg_state = pkg_c.sum(axis=0)
        before = np.asarray(package.state_draws, dtype=float)
        common = {
            "assumption_ids": sorted({*package.combined.assumption_ids, *all_ids}),
            "source_ids": sorted({*package.combined.source_ids, *all_sources}),
            "tier": package.combined.tier,
        }
        pkg = {
            "not_screened": package.combined,
            "screened": rec.from_draws(
                f"combined added units, practical capacity screened {SPAN}, state",
                pkg_state,
                "units",
                expression="the sum over counties of the package's county units x (the sum of "
                "the county's screened reform units / the sum of its not-screened reform "
                "units), in each draw",
                **common,
            ),
            "change": rec.from_draws(
                f"change from the practical capacity screen, package {SPAN}, state",
                pkg_state - before,
                "units",
                expression="screened package - not-screened package, in each draw",
                **common,
            ),
        }
        state_draws["package"] = pkg_state
        county_draws["package"] = pkg_c
    notes = (
        "The five benchmark presets are unchanged: 'not screened' is today's headline, the "
        "same draws. The screen is a named option (capacity_screen), not the headline.",
        "Unknown sewer answers and tracts without a digital flood map are drawn from ledger "
        "ranges (capacity.*); they are never assumed to pass.",
        "Wildfire is not in any state pack yet, so it removes nothing; its coverage is shown as 0.",
        "The screens are measured one at a time, not where they coincide, so 'screened' (the "
        "screens taken as independent) is the middle of a range: screened_disjoint (excluded "
        "lots never coincide) to screened_nested (they coincide as much as they can).",
        "The package's overlap and interactions are scaled by each county's screened share "
        "of its reforms' homes in each draw.",
        "Every capacity.* entry is team judgement (draft). The screen says where building is "
        "harder, not a site finding; it says nothing about spare treatment or pipe capacity.",
    )
    return CapacityOutcome(
        title=s.title,
        question=s.question,
        reforms=reforms,
        binding=binding,
        counties=counties,
        package=pkg,
        coverage=_coverage(inp, s),
        texts=texts,
        notes=notes,
        state_draws=state_draws,
        county_draws=county_draws,
    )


def _bound_records(
    arrs: ScreenArrays,
    name: str,
    unit: str,
    roll: str,
    common: Mapping[str, Any],
    s: CapacitySettings,
) -> dict[str, Record]:
    """Screened homes at the two ends of the dependence range (:data:`DEPENDENCE`)."""
    ids = tuple(sorted({*common["assumption_ids"]}))
    extra = {**common, "assumption_ids": ids}
    words = {
        "disjoint": (
            "excluded lots never coincide (lowest)",
            "max(0, sewer + flood + steep - 2)",
        ),
        "nested": (
            "excluded lots coincide as much as they can (highest)",
            "min(sewer, flood, steep)",
        ),
    }
    out = {}
    for case, (label, form) in words.items():
        out[f"capacity_screened_{case}"] = rec.from_draws(
            f"{name}, practical capacity screened, {label}",
            arrs.bounds[case].sum(axis=0),
            unit,
            expression=(
                f"{roll}added units x {form} of the screen multipliers (Frechet bound; rule "
                f"{s.dependence_id}); the independent case (the product) lies between"
            ),
            **extra,
        )
    return out


def _dependence_text(recs: Mapping[str, Record], s: CapacitySettings) -> str:
    """The rule-written sentence on the dependence range of one reform's screened homes."""
    template = s.texts.get("dependence_range")
    if not template:
        return ""
    return template.format(
        lo=_whole(recs["screened_disjoint"].p50),
        hi=_whole(recs["screened_nested"].p50),
        mid=_whole(recs["screened"].p50),
    )


def _binding(
    key: str,
    title: str,
    recs: Mapping[str, Record],
    loss_state: Mapping[str, np.ndarray],
    s: CapacitySettings,
) -> dict[str, Any]:
    """The screen that binds most for one reform, with its rule-written sentence."""
    names = list(MEASURED_SCREENS)
    stack = np.vstack([loss_state[n] for n in names])  # screens x draws
    any_loss = stack.max(axis=0) > 1e-9
    n_used = int(any_loss.sum())
    top = stack.argmax(axis=0)
    shares = {
        n: (float(np.mean(top[any_loss] == i)) if any_loss.any() else 0.0)
        for i, n in enumerate(names)
    }
    p50 = {n: float(recs[f"loss_{n}"].p50) for n in names}
    if max(p50.values()) <= 1e-9:
        screen = max(names, key=lambda n: (float(recs[f"loss_{n}"].p90), shares[n]))
        if float(recs[f"loss_{screen}"].p90) <= 1e-9:
            return {
                "screen": None,
                "label": None,
                "share_of_draws": shares,
                "text": s.texts["nothing_to_screen"].format(title=title),
            }
    else:
        screen = max(names, key=lambda n: (p50[n], shares[n]))
    r = recs[f"loss_{screen}"]
    label = s.screens[screen]["label"]
    return {
        "screen": screen,
        "label": label,
        "share_of_draws": shares,
        "text": s.texts["binding"].format(
            label=label,
            p10=_whole(r.p10),
            p50=_whole(r.p50),
            p90=_whole(r.p90),
            share=_pct(shares[screen]),
            se=f"{100.0 * np.sqrt(shares[screen] * (1 - shares[screen]) / max(n_used, 1)):.1f}",
        ),
    }
