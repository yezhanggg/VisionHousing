"""Project choice and a shared market: the package option ``market``.

Plain-language summary
----------------------
Today's package adds the reforms up with two fixed rules: ADUs keep their lots
and a ledger share of them leaves the missing-middle pool, and nothing limits
how many homes all the reforms can build together in one area. This module
offers two named alternatives, asked for with the package option ``market``
(``engine.run(..., market=...)``). The benchmark package never changes.

``"choice"`` - **project choice on the same lots, in the same draws.**
A single-family lot that more than one reform reaches is used once, for the
use worth most:

- an **ADU** (the owner keeps the house; worth the ADU's value to the owner:
  the pro forma's value test, value-uplift share x the home's value - the
  ADU's total cost, lot by lot, ``scenarios.adu.AduLotDetail``),
- a **2-6 unit building** (the best of the duplex / fourplex / six-unit
  prototypes on that lot, ``scenarios.prototypes``; worth its land bid),
- a **conventional new house** (one bigger home; the same pro forma, ledger
  ``market.conventional_home_size_sqft``),
- or **retain / defer** (nothing beats the existing use, or the chosen project
  is not started inside 2027-2031: take-up).

Per tract and draw, on the lots both the ADU and the missing-middle rules reach
(the ADU-exposed share ``c`` of missing middle's eligible lots), with ``pA`` the
share where an ADU pays, ``f_alone`` the share where a 2-6 unit building
pencils, ``f_conv`` where it pencils and outbids a conventional house, and
``f_win`` where it also beats the ADU's value to the owner::

    ADU        pA x (1 - f_win)
    2-6 units  (1 - pA) x f_conv + pA x f_win
    new house  (1 - pA) x (f_alone - f_conv)
    retain     (1 - pA) x (1 - f_alone)

These four shares add up to 1 on every lot, so a lot is counted once. Whether
an ADU pays and whether a building pencils are taken as independent within a
tract (the ADU test reads rents, the building test reads home values). The
ADU loses the share ``f_win`` of its homes on lots missing middle can reach;
missing middle is rerun with the rivals (``scenarios.missing_middle``,
``rivals``). The old overlap rule (``missing_middle.adu_lot_overlap_share``) is
not used: choice replaces it. Every other reform and every pro-forma
interaction is kept as in today's package.

``"choice+capacity"`` - **the same, with a shared market limit.** Builders,
lenders and buyers in a group of counties (the pack's typology groups) can take
on only so many homes a year. The cap is
``market.capacity_peak_multiplier x`` the group's highest yearly permit total in
the recent window (``assumptions/market.yaml``). The room for the package each
year is the cap minus the baseline's permits that year (never below 0). When
the package wants more, every reform and county keeps the same share of what
it wants that year (proportional); the rest waits for next year's room (a soft
cap). Homes still waiting after 2031 are pushed past the window. The
no-constraint case (project choice alone) is always reported beside it.

Outputs, kept apart (``MarketOutcome.records``), for today / choice / capacity:
local gain (homes authorized in the area before displacement), statewide net
authorizations (the headline measure), net stock change (after demolition),
stock losses (homes demolished because of the reforms), fee effects (the fee
cut's own homes and its lift to other reforms), substitution (lots and homes
that switch use), shifted years (moved later, pushed past 2031), and the lot
ledger of the shared lots.

Cross-state displacement (a project moving to another state) is a limitation,
not modelled.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.adapters import OPTION_LOT_DETAIL, OPTION_RIVALS
from visionhousing.engine.packages import interaction_options
from visionhousing.engine.records import Record
from visionhousing.engine.registry import Draws, ReformResult, RunContext, get_reform
from visionhousing.ledger import LedgerError, sample
from visionhousing.ledger.loader import ROOT, read_yaml
from visionhousing.scenarios.missing_middle import HOME_COLUMNS, ZONED_PARAM, MiddleRivals

__all__ = [
    "MARKET_MODES",
    "MULTIPLIER_ID",
    "MarketOutcome",
    "MarketSettings",
    "build_market",
    "capacity_allocate",
    "load_settings",
]

SETTINGS_PATH = ROOT / "assumptions" / "market.yaml"
"""The rules file."""

MARKET_MODES = ("choice", "choice+capacity")
"""The package option's values (``None`` = today's package rules)."""

MULTIPLIER_ID = "market.capacity_peak_multiplier"
"""Ledger id of the capacity cap as a multiple of the past peak."""

CONVENTIONAL_ID = "market.conventional_home_size_sqft"
"""Ledger id of the conventional rebuild's floor area."""

PERMIT_SOURCE = "bps_county_annual"
"""Registry id of the county permit series the past peak is read from."""

SPAN = "2027-2031"
_GROUPINGS = ("typology", "state")
_ALLOCATIONS = ("proportional",)


# ---------------------------------------------------------------- settings


@dataclass(frozen=True)
class MarketSettings:
    """The rules in ``assumptions/market.yaml``.

    Attributes:
        id: File id.
        modes: Mode name to ``{"id", "title", "question"}``.
        choice_id: Id of the choice rule.
        middle_method: How a 2-6 unit building is priced (``prototypes``).
        conventional: Compare with a conventional single-family rebuild.
        capacity_id: Id of the capacity rule.
        group_by: ``typology`` or ``state``.
        ungrouped: Group name for a county with no typology row.
        peak_window_years: Years of the permit table the past peak is read from.
        allocation: How room is shared (``proportional``).
        carry_over: Units over the cap wait for the next year (else they are lost).
        texts: Sentence templates by name.
    """

    id: str
    modes: Mapping[str, Mapping[str, str]]
    choice_id: str
    middle_method: str
    conventional: bool
    capacity_id: str
    group_by: str
    ungrouped: str
    peak_window_years: int
    allocation: str
    carry_over: bool
    texts: Mapping[str, str] = field(default_factory=dict)


def parse_settings(data: Any) -> MarketSettings:
    """Check the parsed rules file and build the settings.

    Raises:
        LedgerError: A block or field is missing or has a value the engine does not know.
    """
    if not isinstance(data, dict):
        raise LedgerError("market.yaml: expected a mapping")
    problems: list[str] = []
    modes = data.get("modes") or {}
    for m in MARKET_MODES:
        entry = modes.get(m) if isinstance(modes, dict) else None
        if not isinstance(entry, dict) or not all(
            entry.get(k) for k in ("id", "title", "question")
        ):
            problems.append(f"modes.{m} needs id, title and question")
    choice = data.get("choice") or {}
    capacity = data.get("capacity") or {}
    for block, keys in (
        (choice, ("id", "middle_method", "conventional_rebuild")),
        (
            capacity,
            ("id", "group_by", "ungrouped", "peak_window_years", "allocation", "carry_over"),
        ),
    ):
        for k in keys:
            if k not in block:
                problems.append(f"missing setting {k}")
    if choice.get("middle_method") not in (None, "prototypes"):
        problems.append("choice.middle_method must be 'prototypes'")
    if capacity.get("group_by") not in (None, *_GROUPINGS):
        problems.append(f"capacity.group_by must be one of {_GROUPINGS}")
    if capacity.get("allocation") not in (None, *_ALLOCATIONS):
        problems.append(f"capacity.allocation must be one of {_ALLOCATIONS}")
    window = capacity.get("peak_window_years")
    if window is not None and (not isinstance(window, int) or window < 1):
        problems.append("capacity.peak_window_years must be a whole number >= 1")
    if problems:
        raise LedgerError("market.yaml: " + " | ".join(problems))
    return MarketSettings(
        id=str(data.get("id", "market")),
        modes={m: {k: " ".join(str(v).split()) for k, v in modes[m].items()} for m in MARKET_MODES},
        choice_id=str(choice["id"]),
        middle_method=str(choice["middle_method"]),
        conventional=bool(choice["conventional_rebuild"]),
        capacity_id=str(capacity["id"]),
        group_by=str(capacity["group_by"]),
        ungrouped=str(capacity["ungrouped"]),
        peak_window_years=int(capacity["peak_window_years"]),
        allocation=str(capacity["allocation"]),
        carry_over=bool(capacity["carry_over"]),
        texts={k: " ".join(str(v).split()) for k, v in (data.get("texts") or {}).items()},
    )


def load_settings(path: Path | str | None = None) -> MarketSettings:
    """Read ``assumptions/market.yaml`` (or another file)."""
    return parse_settings(read_yaml(Path(path) if path is not None else SETTINGS_PATH))


# ---------------------------------------------------------------- capacity


@dataclass(frozen=True)
class CapacityResult:
    """What :func:`capacity_allocate` returns.

    Attributes:
        kept: Units authorized inside the window, ``(parts, counties, draws, years)``.
        on_time: Units authorized in the year the package wanted them, ``(parts, counties, draws)``.
        shifted: Units authorized in a later year inside the window, same shape.
        pushed: Units still waiting after the last year, same shape.
        room: Room for the package per group, draw and year, ``(groups, draws, years)``.
        used: Units the package used per group, draw and year (never above ``room``).
    """

    kept: np.ndarray
    on_time: np.ndarray
    shifted: np.ndarray
    pushed: np.ndarray
    room: np.ndarray
    used: np.ndarray


def capacity_allocate(
    wanted: np.ndarray,
    group_of: np.ndarray,
    room: np.ndarray,
    *,
    carry_over: bool = True,
) -> CapacityResult:
    """Share each group's yearly room among parts and counties, proportionally.

    Args:
        wanted: Units the package wants, ``(parts, counties, draws, years)``;
            negative values pass through untouched (they use no room).
        group_of: Group index of each county, ``(counties,)``.
        room: Room per group, draw and year, ``(groups, draws, years)``, >= 0.
        carry_over: Units over the room wait for the next year (soft cap);
            else they are dropped (counted as pushed past the window).

    Returns:
        The allocation. In every group, draw and year the units used never
        exceed the room, and kept + pushed = wanted (positive part) in total.
    """
    w = np.asarray(wanted, dtype=float)
    pos = np.clip(w, 0.0, None)
    neg = w - pos
    parts, counties, draws, years = w.shape
    groups = room.shape[0]
    onehot = np.zeros((groups, counties))
    onehot[np.asarray(group_of, dtype=int), np.arange(counties)] = 1.0
    kept = np.zeros_like(w)
    on_time = np.zeros((parts, counties, draws))
    shifted = np.zeros((parts, counties, draws))
    used = np.zeros((groups, draws, years))
    carry = np.zeros((parts, counties, draws))
    for y in range(years):
        new = pos[..., y]
        want = new + carry
        want_g = np.einsum("gc,pcd->gd", onehot, want)
        with np.errstate(divide="ignore", invalid="ignore"):
            factor_g = np.where(want_g > room[..., y], room[..., y] / want_g, 1.0)
        factor = factor_g[np.asarray(group_of, dtype=int)]  # (counties, draws)
        got = want * factor[None]
        kept[..., y] = got + neg[..., y]
        on_time += new * factor[None]
        shifted += carry * factor[None]
        used[..., y] = np.einsum("gc,pcd->gd", onehot, got)
        carry = (want - got) if carry_over else np.zeros_like(want)
    pushed = pos.sum(axis=-1) - on_time - shifted
    return CapacityResult(kept, on_time, shifted, np.clip(pushed, 0.0, None), room, used)


def _groups(context: Any, county_ids: Sequence[str], s: MarketSettings) -> list[str]:
    types = getattr(context, "county_types", None) or {}
    if s.group_by == "state" or not types:
        return ["the whole state"] * len(county_ids)
    return [str(types.get(c, s.ungrouped)) for c in county_ids]


def _past_peaks(
    permits: Any, county_ids: Sequence[str], group_names: Sequence[str], window: int
) -> dict[str, tuple[float, int, int, int]]:
    """Group name to ``(peak yearly permits, peak year, first year, last year)`` of the window."""
    table = permits.copy()
    cols = [c for c in table.columns if str(c).startswith("units_")]
    table["county"] = table["county"].astype(str)
    table["total"] = table[cols].sum(axis=1)
    last = int(table["year"].max())
    first = last - window + 1
    table = table[table["year"] >= first]
    group = dict(zip(county_ids, group_names, strict=True))
    table = table[table["county"].isin(group)]
    table["group"] = table["county"].map(group)
    by_year = table.groupby(["group", "year"])["total"].sum()
    out = {}
    for g in dict.fromkeys(group_names):
        if g in by_year.index.get_level_values(0):
            series = by_year.loc[g]
            out[g] = (float(series.max()), int(series.idxmax()), first, last)
        else:
            out[g] = (0.0, last, first, last)
    return out


# ---------------------------------------------------------------- result


@dataclass(frozen=True)
class MarketOutcome:
    """The package under a market option, beside today's package.

    Attributes:
        mode: ``"choice"`` or ``"choice+capacity"``.
        title: Plain name of the mode.
        question: The question it answers.
        cases: Case names in order: ``today``, ``choice`` and, with capacity,
            ``capacity`` (``choice`` is then the disclosed no-constraint case).
        measures: Case to measure name to its state record (``local_gain``,
            ``statewide_net``, ``net_stock``, ``stock_losses``, ``fee_effects``).
        parts: Case to reform key to its state record (its homes in the package).
        substitution: Name to state record (lots and homes that switch use).
        lots: Lot ledger of the shared lots, name to state record.
        timing: Capacity only: ``on_time``, ``shifted_later``, ``pushed_past``.
        bridge: Today -> choice (-> capacity), state records in order.
        counties: Case to county id to the statewide-net record.
        groups: Capacity groups (name, counties, past peak and year, window,
            the cap, room used, share of the package's homes kept inside the
            window, share of draws where the cap binds in some year).
        texts: Rule-written sentences, by name.
        notes: Plain statements on the rules and limits.
        state_draws: Case to statewide net authorizations by draw.
        draws: The state draws behind the other records (see the field).
        county_draws: Case to statewide net authorizations by county and draw.
    """

    mode: str
    title: str
    question: str
    cases: tuple[str, ...]
    measures: Mapping[str, Mapping[str, Record]]
    parts: Mapping[str, Mapping[str, Record]]
    substitution: Mapping[str, Record]
    lots: Mapping[str, Record]
    timing: Mapping[str, Record]
    bridge: tuple[Record, ...]
    counties: Mapping[str, Mapping[str, Record]]
    groups: tuple[Mapping[str, Any], ...]
    texts: Mapping[str, str]
    notes: tuple[str, ...]
    state_draws: Mapping[str, np.ndarray] = field(repr=False, compare=False)
    county_draws: Mapping[str, np.ndarray] = field(repr=False, compare=False)
    draws: Mapping[str, np.ndarray] = field(default_factory=dict, repr=False, compare=False)
    """State draws behind the records, by name: ``parts.<case>.<key>``, ``lots.<name>``,
    ``substitution.<name>``, ``timing.<name>``, ``bridge.<i>`` (for checks)."""

    def records(self) -> list[Record]:
        """Every record, in a stable order."""
        out: list[Record] = []
        for c in self.cases:
            out += list(self.measures[c].values()) + list(self.parts[c].values())
            out += list(self.counties.get(c, {}).values())
        out += [*self.substitution.values(), *self.lots.values(), *self.timing.values()]
        out += list(self.bridge)
        for g in self.groups:
            out += [g["room_used"], g["kept_share"], g["cap"]]
        return out

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready (records as their model dumps; no draws)."""

        def r(x: Record) -> dict[str, Any]:
            return x.model_dump(mode="json")

        return {
            "mode": self.mode,
            "title": self.title,
            "question": self.question,
            "cases": list(self.cases),
            "measures": {c: {k: r(x) for k, x in m.items()} for c, m in self.measures.items()},
            "parts": {c: {k: r(x) for k, x in m.items()} for c, m in self.parts.items()},
            "substitution": {k: r(x) for k, x in self.substitution.items()},
            "lots": {k: r(x) for k, x in self.lots.items()},
            "timing": {k: r(x) for k, x in self.timing.items()},
            "bridge": [r(x) for x in self.bridge],
            "counties": {c: {k: r(x) for k, x in m.items()} for c, m in self.counties.items()},
            "groups": [
                {k: (r(v) if isinstance(v, Record) else v) for k, v in g.items()}
                for g in self.groups
            ],
            "texts": dict(self.texts),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------- the build


def _merged_interactions(key: str, keys: Sequence[str], ledger: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for change in interaction_options(key, keys, ledger).values():
        out.update(change)
    return out


def _kept(res: ReformResult | None) -> np.ndarray:
    """``1 - displacement share`` per draw as ``(1, draws)``, or 1."""
    detail = getattr(res, "detail", None)
    k = getattr(detail, "kept_share", None)
    if k is None:
        return np.ones((1, 1))
    return np.asarray(k, dtype=float).reshape(1, -1)


def _loss_alone(res: ReformResult | None, county_ids: tuple[str, ...]) -> np.ndarray | None:
    """Homes demolished because of the reform (added - net stock), by county and draw."""
    if res is None or res.net_stock is None:
        return None
    return res.county_sum(np.asarray(res.added) - np.asarray(res.net_stock), county_ids)


def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.abs(den) > 0, num / np.where(np.abs(den) > 0, den, 1.0), 0.0)


def build_market(
    mode: str,
    *,
    keys: tuple[str, ...],
    chosen: Mapping[str, Mapping[str, Any]],
    outcomes: Mapping[str, Any],
    extras: Mapping[str, Any],
    overlap: tuple[np.ndarray, tuple[str, ...]] | None,
    run_context: RunContext,
    shared: Draws,
    package: Any,
    baseline_by_year: np.ndarray,
    years: tuple[int, ...],
    settings: MarketSettings | None = None,
) -> MarketOutcome:
    """Build the package under a market option (pure: no files except the rules file).

    Args:
        mode: ``"choice"`` or ``"choice+capacity"``.
        keys: Reform keys in the package.
        chosen: Reform key to its options.
        outcomes: Reform key to its outcome (with its yearly split).
        extras: The package's interactions (``engine.core._interactions``).
        overlap: Today's ADU-lot overlap by county, with its ids, or ``None``.
        run_context: The run context.
        shared: The run's draws.
        package: Today's package (``engine.core.PackageOutcome``).
        baseline_by_year: Baseline units by county, draw and year.
        years: Calendar years.
        settings: The rules; read from ``assumptions/market.yaml`` when ``None``.

    Returns:
        The :class:`MarketOutcome`.
    """
    if mode not in MARKET_MODES:
        raise ValueError(f"market must be one of {MARKET_MODES}, got {mode!r}")
    s = settings or load_settings()
    county_ids = run_context.county_ids
    n = shared.n
    zero = np.zeros((len(county_ids), n))
    families = {get_reform(k).family: k for k in keys}
    adu_key, mm_key = families.get("adu"), families.get("missing_middle")
    fee_key = families.get("fees")
    base_ids = set(package.combined.assumption_ids)
    sources = sorted({*package.combined.source_ids})
    tier = package.combined.tier

    # Today's package, part by part (each reform with its interactions, less the overlap).
    today = {k: np.array(outcomes[k].county_draws, dtype=float) for k in keys}
    for name, (d, _ids, _rows) in extras.items():
        today[name.rsplit(" -> ", 1)[-1]] = today[name.rsplit(" -> ", 1)[-1]] + np.asarray(d)
    if overlap is not None and mm_key is not None:
        today[mm_key] = today[mm_key] - overlap[0]
    choice = {k: v.copy() for k, v in today.items()}
    kept = {k: _kept(outcomes[k].result) for k in keys}
    loss_today = {}
    for k in keys:
        alone = _loss_alone(outcomes[k].result, county_ids)
        if alone is not None:
            loss_today[k] = alone * _ratio(today[k], np.asarray(outcomes[k].county_draws))
    loss_choice = dict(loss_today)

    ids = set(base_ids)
    sub: dict[str, np.ndarray] = {}
    lots: dict[str, np.ndarray] = {}
    bridge: list[tuple[str, np.ndarray, str]] = []
    if mm_key is not None:
        mm_alone = outcomes[mm_key].result
        mm_scope_types = tuple(mm_alone.detail.scope.building_types)
        tracts = run_context.state.tracts
        adu_detail = adu_res = None
        n_t = len(tracts)
        a = np.zeros((n, n_t))
        uplift = np.zeros((n, 1))
        cost = np.full((n, n_t), np.inf)
        exposed_pencil = np.zeros((n, n_t))
        exposed = np.zeros((n, n_t))
        if adu_key is not None:
            opts = {**dict(chosen[adu_key]), **_merged_interactions(adu_key, keys, shared.ledger)}
            opts[OPTION_LOT_DETAIL] = True
            adu_res = get_reform(adu_key).adapter(run_context, shared, opts)
            adu_detail = adu_res.detail
            d = adu_detail.lot_detail
            homes = {
                t: np.clip(tracts[HOME_COLUMNS[t]].to_numpy(dtype=float), 0, None)
                for t in mm_scope_types
            }
            total = sum(homes.values())
            weight = {t: _ratio(h, total) for t, h in homes.items()}
            pencil = sum(weight[t][None, :] * d.pencil[t] for t in mm_scope_types if t in d.pencil)
            exposed = d.exposed
            a = exposed * pencil
            exposed_pencil = pencil
            uplift, cost = d.uplift, d.total_cost
            ids |= set(adu_res.headline.assumption_ids)
        mm_opts = {
            **dict(chosen[mm_key]),
            **_merged_interactions(mm_key, keys, shared.ledger),
            "method": s.middle_method,
            OPTION_RIVALS: MiddleRivals(
                adu_share=a, adu_uplift=uplift, adu_cost=cost, conventional=s.conventional
            ),
        }
        mm_res = get_reform(mm_key).adapter(run_context, shared, mm_opts)
        ch = mm_res.detail.choice
        ids |= set(mm_res.headline.assumption_ids) | {CONVENTIONAL_ID}
        to_c = lambda arr: mm_res.county_sum(np.asarray(arr).T, county_ids)  # noqa: E731
        no_overlap_today = today[mm_key] + (overlap[0] if overlap is not None else 0.0)
        alone_c = to_c(ch.authorized_alone)
        conv_c = to_c(ch.authorized_conventional)
        mm_choice_c = mm_res.county_draws(county_ids)
        choice[mm_key] = mm_choice_c
        alone_loss = _loss_alone(mm_res, county_ids)
        if alone_loss is not None:
            loss_choice[mm_key] = alone_loss
        if overlap is not None:
            bridge.append(
                (
                    "today's overlap rule undone",
                    overlap[0],
                    "missing-middle homes today's package removes for lots ADUs use "
                    "(missing_middle.adu_lot_overlap_share); project choice replaces "
                    "this rule",
                )
            )
        lift = _merged_interactions(mm_key, keys, shared.ledger)
        mix_c = no_overlap_today
        if overlap is not None and lift:
            # Today's fee and parking lift on missing middle was worked out on the run
            # without the ADU lots; on those lots it is its own step.
            mix_res = get_reform(mm_key).adapter(
                run_context, shared, {**dict(chosen[mm_key]), **lift}
            )
            mix_c = mix_res.county_draws(county_ids)
            bridge.append(
                (
                    "fee and parking lift on the lots ADUs use",
                    mix_c - no_overlap_today,
                    "missing middle with the benchmark mix and the package's fee and "
                    "parking changes on every lot - (today's missing-middle part + "
                    "today's overlap): the lift today's package works out without the "
                    "lots ADUs use, added back on those lots",
                )
            )
        bridge.append(
            (
                "2-6 unit building priced as the best prototype per lot",
                alone_c - mix_c,
                "missing middle with the best of duplex / fourplex / six-unit on each "
                "lot (no rival) - missing middle with the benchmark mix, same draws, "
                "same lots and the same fee and parking changes",
            )
        )
        bridge.append(
            (
                "2-6 unit lots won by a conventional new house",
                conv_c - alone_c,
                "missing middle with the conventional rival - missing middle with no rival",
            )
        )
        sub["middle_to_conventional_lots"] = to_c(ch.lots_to_conventional)
        sub["middle_to_conventional_units"] = alone_c - conv_c
        if adu_key is not None and adu_detail is not None:
            bridge.append(
                (
                    "2-6 unit lots where the ADU is worth more",
                    mm_choice_c - conv_c,
                    "missing middle with both rivals - missing middle with the "
                    "conventional rival only",
                )
            )
            sub["middle_to_adu_lots"] = to_c(ch.lots_to_adu)
            sub["middle_to_adu_units"] = conv_c - mm_choice_c
            zoned = sample(mm_res.ledger, n=n, seed=shared.seed, ids=[ZONED_PARAM])[ZONED_PARAM]
            zoned = zoned[:, None]
            d = adu_detail.lot_detail
            on_types = sum(d.unit_share[t] for t in mm_scope_types if t in d.unit_share)
            lost_share = on_types * zoned * ch.win_share  # share of each tract's ADUs that go
            lost_units = to_c(adu_detail.stages["net"] * lost_share)
            lost_lots = to_c(adu_detail.stages["built"] * lost_share)
            if tuple(adu_res.geoids) != tuple(mm_res.geoids):
                raise ValueError("the ADU and missing-middle reruns have other tracts")
            adu_full = adu_res.county_draws(county_ids)
            # Today's ADU part, less the homes on lots the building wins.
            choice[adu_key] = today[adu_key] - lost_units
            sub["adu_to_middle_lots"] = lost_lots
            sub["adu_to_middle_units"] = lost_units
            bridge.append(
                (
                    "ADU lots where a 2-6 unit building is worth more",
                    -lost_units,
                    "ADUs on lots missing middle reaches x share of those lots where "
                    "the best prototype pencils and beats the ADU's value to the owner "
                    "and a conventional new house",
                )
            )
            if not np.allclose(adu_full, today[adu_key], rtol=1e-9, atol=1e-6):
                raise ValueError("the ADU rerun for project choice does not match the package")
            # Lot ledger on the shared lots (eligible for both rules), by chosen use.
            shared_lots = mm_res.detail.stages["eligible"] * exposed
            pa = exposed_pencil
            f_alone, f_conv, f_win = ch.feasible_alone, ch.feasible_conventional, ch.win_share
            lots["shared"] = to_c(shared_lots)
            lots["adu"] = to_c(shared_lots * pa * (1.0 - f_win))
            lots["middle"] = to_c(shared_lots * ((1.0 - pa) * f_conv + pa * f_win))
            lots["conventional"] = to_c(shared_lots * (1.0 - pa) * (f_alone - f_conv))
            lots["retain"] = to_c(shared_lots * (1.0 - pa) * (1.0 - f_alone))
            adu_rate = np.minimum(np.asarray(adu_detail.takeup_factor, dtype=float), 1.0)
            started = lots["adu"] * adu_rate.reshape(1, -1) + lots[
                "middle"
            ] * ch.redevelopment_rate.reshape(1, -1)
            lots["deferred"] = lots["adu"] + lots["middle"] - started
        kept[mm_key] = _kept(mm_res)

    # Cases: today, choice, and capacity.
    cases: dict[str, dict[str, np.ndarray]] = {"today": today, "choice": choice}
    losses: dict[str, dict[str, np.ndarray]] = {"today": loss_today, "choice": loss_choice}
    timing: dict[str, np.ndarray] = {}
    groups_out: list[dict[str, Any]] = []
    full_share: float | None = None
    if mode == "choice+capacity":
        cap = _capacity(
            keys, choice, outcomes, run_context, shared, baseline_by_year, years, s, kept
        )
        total_kept = cap["kept"]  # part -> (counties, draws)
        cases["capacity"] = total_kept
        losses["capacity"] = {
            k: v * _ratio(total_kept[k], choice[k]) for k, v in loss_choice.items()
        }
        timing = cap["timing"]
        groups_out = cap["groups"]
        full_share = cap["full_share"]
        ids |= {MULTIPLIER_ID}
        bridge.append(
            (
                "homes moved past 2031 by the capacity limit",
                -timing["pushed_past"],
                "homes the package wants that wait for room past 2031: each group's "
                "yearly room = market.capacity_peak_multiplier x past peak - baseline",
            )
        )

    common = {
        "assumption_ids": sorted(i for i in ids if i in shared.ledger),
        "source_ids": sources,
        "tier": tier,
    }
    cap_common = {**common, "source_ids": sorted({*sources, PERMIT_SOURCE})}

    def state(name: str, arr: np.ndarray, unit: str, expression: str, **kw: Any) -> Record:
        args = {**common, **kw}
        data = np.asarray(arr, dtype=float)
        data = data.sum(axis=0) if data.ndim == 2 else data
        return rec.from_draws(f"{name} {SPAN}, state", data, unit, expression=expression, **args)

    titles = {
        "today": "today's package rules",
        "choice": "project choice",
        "capacity": "project choice with the capacity limit",
    }
    measures: dict[str, dict[str, Record]] = {}
    parts: dict[str, dict[str, Record]] = {}
    counties: dict[str, dict[str, Record]] = {}
    state_draws: dict[str, np.ndarray] = {}
    county_draws: dict[str, np.ndarray] = {}
    for case, p in cases.items():
        kw = cap_common if case == "capacity" else {}
        t = titles[case]
        net = sum(p.values(), zero.copy())
        local = sum((p[k] / kept[k] for k in keys), zero.copy())
        loss = sum(losses[case].values(), zero.copy())
        fee = p[fee_key] if fee_key is not None else zero
        state_draws[case] = net.sum(axis=0)
        county_draws[case] = net
        measures[case] = {
            "local_gain": state(
                f"local gain (homes authorized where built, before displacement), {t}",
                local,
                "units",
                "the sum over reforms of each reform's homes / (1 - its "
                "displacement share), in each draw",
                **kw,
            ),
            "statewide_net": state(
                f"statewide net added units authorized, {t}",
                net,
                "units",
                "the sum over reforms of each reform's homes in the package, "
                "after displacement, in each draw",
                **kw,
            ),
            "net_stock": state(
                f"net change in the housing stock, {t}",
                net - loss,
                "units",
                "statewide net added units - homes demolished because of the reforms, in each draw",
                **kw,
            ),
            "stock_losses": state(
                f"existing homes demolished because of the reforms, {t}",
                loss,
                "units",
                "the sum over reforms that redevelop lots of their homes "
                "authorized - their net stock change (today's package: the "
                "reform alone scaled by its share kept in the package)",
                **kw,
            ),
            "fee_effects": state(
                f"homes from the fee cut's own effect, {t}",
                fee,
                "units",
                "the fee reform's added units in the package (its lift to "
                "ADU and 2-6 unit projects is inside their parts)",
                **kw,
            ),
        }
        parts[case] = {
            k: state(
                f"added units authorized, {outcomes[k].title}, {t}",
                p[k],
                "units",
                "the reform's homes in the package (with its pro-forma interactions)",
                **kw,
            )
            for k in keys
        }
        counties[case] = {
            c: rec.from_draws(
                f"statewide net added units authorized, {t} {SPAN}, county {c}",
                net[i],
                "units",
                expression="the sum over reforms of the "
                "county's homes in the package, in each draw",
                **{**common, **kw},
            )
            for i, c in enumerate(county_ids)
        }
    if extras and fee_key is not None:
        lift = sum(
            (np.asarray(d) for nm, (d, _i, _r) in extras.items() if nm.startswith("fees ->")),
            zero.copy(),
        )
        measures["today"]["fee_lift"] = state(
            "homes the fee cut adds to ADU and 2-6 unit projects, today's package rules",
            lift,
            "units",
            "the sum of the fees -> ADU and fees -> missing middle interactions",
        )

    sub_names = {
        "adu_to_middle_lots": (
            "lots that would have added an ADU and take a 2-6 unit building",
            "lots",
        ),
        "adu_to_middle_units": (
            "ADU homes given up on lots that take a 2-6 unit building",
            "units",
        ),
        "middle_to_adu_lots": (
            "lots where a 2-6 unit building pencils but the owner adds an ADU",
            "lots",
        ),
        "middle_to_adu_units": (
            "2-6 unit homes given up on lots that keep the house and add an ADU",
            "units",
        ),
        "middle_to_conventional_lots": (
            "lots where a 2-6 unit building pencils but a conventional new house bids more",
            "lots",
        ),
        "middle_to_conventional_units": (
            "2-6 unit homes given up to conventional new houses",
            "units",
        ),
    }
    substitution = {
        k: state(
            sub_names[k][0],
            v,
            sub_names[k][1],
            "per tract and draw, the lots or homes that switch use under project choice "
            "(rule market.choice), summed over tracts",
        )
        for k, v in sub.items()
    }
    lot_names = {
        "shared": "single-family lots both the ADU and the missing-middle rules reach",
        "adu": "shared lots whose best use is an ADU",
        "middle": "shared lots whose best use is a 2-6 unit building",
        "conventional": "shared lots whose best use is a conventional new house",
        "retain": "shared lots where nothing beats keeping the house as it is",
        "deferred": "shared lots whose chosen project does not start inside 2027-2031",
    }
    lot_records = {
        k: rec.from_draws(
            f"{lot_names[k]}, state",
            v.sum(axis=0),
            "lots",
            expression="per tract and draw: shared lots x the share of each use "
            "(ADU pA(1-f_win); building (1-pA)f_conv + pA f_win; new house "
            "(1-pA)(f_alone-f_conv); retain (1-pA)(1-f_alone)), summed",
            **common,
        )
        for k, v in lots.items()
    }
    timing_names = {
        "on_time": "homes authorized in the year the package wanted them",
        "shifted_later": "homes moved to a later year inside 2027-2031 by the capacity limit",
        "pushed_past": "homes pushed past 2031 by the capacity limit",
    }
    timing_records = {
        k: state(
            timing_names[k],
            v,
            "units",
            "per county group, year and draw: room = market.capacity_peak_multiplier x "
            "past peak - baseline permits; every reform and county keeps the same share "
            "of what it wants; the rest waits for the next year (rule market.capacity)",
            **cap_common,
        )
        for k, v in timing.items()
    }
    bridge_records = [
        state(
            f"bridge: {name}",
            arr,
            "units",
            expression,
            **(cap_common if "capacity" in name else {}),
        )
        for name, arr, expression in bridge
    ]
    mode_entry = s.modes[mode]
    texts = _texts(s, mode, mode_entry, measures, substitution, timing_records)
    if full_share is not None and s.texts.get("full_market"):
        texts["full_market"] = s.texts["full_market"].format(share=f"{round(100 * full_share)}%")
    notes = (
        "Project choice replaces today's ADU-lot overlap rule: each shared lot is counted once, "
        "for its best use (rule market.choice).",
        "Whether an ADU pays and whether a 2-6 unit building pencils are taken as independent "
        "within a tract; the conventional new house competes with the 2-6 unit building only.",
        "The ADU's value to the owner uses the pro forma's value test (value-uplift share x home "
        "value - ADU cost) on each lot's home value; whether the ADU pays uses rents.",
        "Moving to another state is not modelled (a limit of this test).",
        *(
            (
                "The capacity limit is a stress test, not a forecast: its cap is a ledger range "
                "over the past peak, and the no-constraint case is reported beside it.",
            )
            if mode == "choice+capacity"
            else ()
        ),
    )
    return MarketOutcome(
        mode=mode,
        title=mode_entry["title"],
        question=mode_entry["question"],
        cases=tuple(cases),
        measures=measures,
        parts=parts,
        substitution=substitution,
        lots=lot_records,
        timing=timing_records,
        bridge=tuple(bridge_records),
        counties=counties,
        groups=tuple(groups_out),
        texts=texts,
        notes=notes,
        state_draws=state_draws,
        county_draws=county_draws,
        draws={
            **{
                f"parts.{c}.{k}": np.asarray(v).sum(axis=0)
                for c, p in cases.items()
                for k, v in p.items()
            },
            **{f"lots.{k}": v.sum(axis=0) for k, v in lots.items()},
            **{f"substitution.{k}": np.asarray(v).sum(axis=0) for k, v in sub.items()},
            **{f"timing.{k}": np.asarray(v).sum(axis=0) for k, v in timing.items()},
            **{f"bridge.{i}": np.asarray(a).sum(axis=0) for i, (_n, a, _e) in enumerate(bridge)},
        },
    )


def _capacity(
    keys: tuple[str, ...],
    choice: Mapping[str, np.ndarray],
    outcomes: Mapping[str, Any],
    run_context: RunContext,
    shared: Draws,
    baseline_by_year: np.ndarray,
    years: tuple[int, ...],
    s: MarketSettings,
    kept_shares: Mapping[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Fit the package's homes into each county group's yearly room.

    The cap and the room are counts of permits in the area, so the package
    takes room with its **local** permits: each reform's homes after
    displacement / its kept share (1 - displacement share), the "local gain".
    What fits is scaled back by the same kept share, so the capacity case, the
    timing and the bridge stay in statewide net homes.
    """
    county_ids = run_context.county_ids
    n = shared.n
    safe = {}
    for k in keys:
        ks = np.broadcast_to(
            np.asarray((kept_shares or {}).get(k, np.ones((1, 1))), dtype=float), (1, n)
        )
        safe[k] = np.where(ks > 1e-9, ks, 1.0)  # (1, draws); no homes kept, nothing to scale
    scale = np.stack([safe[k] for k in keys])  # (parts, 1, draws)
    names = _groups(run_context.state, county_ids, s)
    order = list(dict.fromkeys(names))
    group_of = np.array([order.index(g) for g in names], dtype=int)
    peaks = _past_peaks(run_context.state.permits, county_ids, names, s.peak_window_years)
    m = np.asarray(shared.values[MULTIPLIER_ID], dtype=float)  # (draws,)
    peak = np.array([peaks[g][0] for g in order])
    onehot = np.zeros((len(order), len(county_ids)))
    onehot[group_of, np.arange(len(county_ids))] = 1.0
    base_g = np.einsum("gc,cdy->gdy", onehot, np.asarray(baseline_by_year, dtype=float))
    cap = peak[:, None] * m[None, :]  # (groups, draws)
    room = np.clip(cap[:, :, None] - base_g, 0.0, None)
    wanted = np.stack(
        [
            (np.asarray(choice[k]) / safe[k])[:, :, None]
            * np.asarray(outcomes[k].annual.shares)[None]
            for k in keys
        ]
    )  # (parts, counties, draws, years): local permits
    res = capacity_allocate(wanted, group_of, room, carry_over=s.carry_over)
    kept = {k: res.kept[i].sum(axis=-1) * safe[k] for i, k in enumerate(keys)}
    timing = {
        "on_time": (res.on_time * scale).sum(axis=0),
        "shifted_later": (res.shifted * scale).sum(axis=0),
        "pushed_past": (res.pushed * scale).sum(axis=0),
    }
    groups = []
    for gi, g in enumerate(order):
        peak_v, peak_year, first, last = peaks[g]
        members = [c for c, x in zip(county_ids, names, strict=True) if x == g]
        room_total = room[gi].sum(axis=-1)
        used_total = res.used[gi].sum(axis=-1)
        wanted_total = np.clip(wanted[:, group_of == gi], 0.0, None).sum(axis=(0, 1, 3))
        binds = np.any(res.used[gi] >= room[gi] - 1e-9, axis=-1) & np.any(
            wanted[:, group_of == gi].sum(axis=(0, 1)) > 1e-9, axis=-1
        )
        groups.append(
            {
                "name": g,
                "counties": members,
                "past_peak": peak_v,
                "past_peak_year": peak_year,
                "window": [first, last],
                "binding_share": float(np.mean(binds)),
                "cap": rec.from_draws(
                    f"yearly permit cap, {g}",
                    cap[gi],
                    "units per year",
                    expression=f"market.capacity_peak_multiplier x the group's highest yearly "
                    f"permit total {first}-{last} ({peak_v:,.0f} in {peak_year})",
                    assumption_ids=[MULTIPLIER_ID],
                    source_ids=[PERMIT_SOURCE],
                    tier=0,
                ),
                "room_used": rec.from_draws(
                    f"share of the room the package uses {SPAN}, {g}",
                    np.where(room_total > 0, _ratio(used_total, room_total), 1.0),
                    "share",
                    expression="local permits the package is given (homes / kept share) / "
                    "(cap - baseline permits), "
                    "summed over the years; 1 where the baseline alone fills the cap (no room)",
                    assumption_ids=[MULTIPLIER_ID],
                    source_ids=[PERMIT_SOURCE],
                    tier=0,
                ),
                "kept_share": rec.from_draws(
                    f"share of the package's homes authorized inside {SPAN}, {g}",
                    np.where(wanted_total > 0, _ratio(used_total, wanted_total), 1.0),
                    "share",
                    expression="local permits the package is given inside the window / local "
                    "permits it wants (homes / kept share), summed over the group's counties, "
                    "reforms and years",
                    assumption_ids=[MULTIPLIER_ID],
                    source_ids=[PERMIT_SOURCE],
                    tier=0,
                ),
            }
        )
    adds = np.clip(wanted, 0.0, None).sum(axis=(0, 3))  # (counties, draws)
    adds_g = np.einsum("gc,cd->gd", onehot, adds) > 1e-9
    no_room = room.sum(axis=-1) <= 0
    full_share = float(np.mean(np.any(adds_g & no_room, axis=0)))
    return {"kept": kept, "timing": timing, "groups": groups, "full_share": full_share}


def _whole(value: float | None) -> str:
    """Homes and lots are whole things: the nearest whole number, with commas."""
    return "not available" if value is None else f"{round(float(value)):,}"


def _texts(
    s: MarketSettings,
    mode: str,
    entry: Mapping[str, str],
    measures: Mapping[str, Mapping[str, Record]],
    substitution: Mapping[str, Record],
    timing: Mapping[str, Record],
) -> dict[str, str]:
    def fill(name: str, r: Record, **extra: str) -> str | None:
        template = s.texts.get(name)
        if not template:
            return None
        return template.format(p10=_whole(r.p10), p50=_whole(r.p50), p90=_whole(r.p90), **extra)

    last = "capacity" if mode == "choice+capacity" else "choice"
    out = {
        "combined": fill(
            "combined",
            measures[last]["statewide_net"],
            mode_title_lower=entry["title"][0].lower() + entry["title"][1:],
        ),
    }
    for name, key in (
        ("adu_to_middle", "adu_to_middle_lots"),
        ("middle_to_adu", "middle_to_adu_lots"),
        ("middle_to_conventional", "middle_to_conventional_lots"),
    ):
        if key in substitution:
            out[name] = fill(name, substitution[key])
    if mode == "choice+capacity":
        out["shifted_later"] = fill("shifted_later", timing["shifted_later"])
        out["pushed_past"] = fill("pushed_past", timing["pushed_past"])
        out["no_constraint"] = fill("no_constraint", measures["choice"]["statewide_net"])
    return {k: v for k, v in out.items() if v}
