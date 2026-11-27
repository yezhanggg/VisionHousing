"""Readouts made from the paired draws: draw shares, shares of the baseline, and costs.

Plain-language summary
----------------------
In every Monte Carlo draw the baseline and each reform are worked out from the
**same** sampled inputs: the same position of the baseline in its likely range,
the same value of every shared ledger parameter. So the reform's added homes in
one draw are a *paired difference*: "with the reform" minus "without it", in
the same simulated future. This module turns those pairs into readouts. Every
readout is a record (formula, inputs, assumption ids, sources), like any other
number of the engine.

Paired readouts (every reform)
    - **Share of the modeled draws in which the reform adds at least N
      homes**: the state total of added homes is N or more, for each threshold
      N in the Assumption Ledger (``explain.chance_threshold_<i>_units``). The
      record keys keep their old name (``chance_at_least_<i>``); the words
      say "in X% of the modeled draws", never "chance". Each share comes with
      its Monte Carlo standard error, sqrt(p (1 - p) / draws)
      (``chance_at_least_<i>_se``): about 1.1 percentage points for a share
      near 60% at 2,000 draws. It measures draw-to-draw noise only, before
      structural or data uncertainty, and shares are printed no finer than it
      allows (:func:`share_percent`).
    - **Added homes as a share of the baseline**, per draw, by county (the
      state figure is the reform's ``pct_change``).
    - **Widths**: the width of the baseline's likely range (P90 - P10) and the
      width of the reform's added homes, and their ratio. The explanation layer
      writes the sentence "The future level of building is uncertain; the
      reform's effect on it is much less so" from these, by rule
      (``explain.paired_much_less_ratio``).

What it costs (by reform)
    - **Fee cut.** A fee cut applies to every permitted home, not only the
      extra ones. In each draw and county::

          revenue given up = fee cut per unit x (baseline homes + added homes)
          cost per added home = revenue given up / added homes

      County revenue adds up to the state in every draw. Cost per added home
      is summarised only over draws where the reform adds homes, and the
      share of such draws is shown in the record's name. When that share is
      below the ledger minimum (``fees.cost_per_added_min_share``), or no draw
      adds homes, the readout is a "not defined" text, never infinity.
      Two variants: an assumed state fee level
      (``fees.pa_assumed_fee_per_unit_judgement``, a labelled draft judgement) instead
      of the benchmark $50,000, and a fee cut limited to one group of similar
      counties (the "places like this one" county groups). The fee cut works
      county by county, so the targeted cut needs no new run: its added homes
      are the same counties' added homes.
      The fiscal screen (:func:`fiscal_split`) splits the money: waived on
      homes built anyway, waived on the added homes (together the revenue
      given up), fees still paid by the added homes, replacement funding
      (none by default) and the net change. It also writes the fiscal
      identity per paired draw: revenue change = (F' - F) x B + F' x A
      (B baseline paying units, A added paying units, F and F' the fee before
      and after), the net loss per added home, and the share of draws that
      add no homes (the revenue is lost in those draws too). Variant
      ``fees_funded_backfill`` sets the replacement share to 1 (the state
      pays); recipients and years are not split (texts say so). Under fee deferral (variant
      ``fees_deferral``) :func:`deferral_readouts` gives the public cost
      (time value and collection loss) instead of a revenue given up.
    - **Parking reform.** No public cost. The private saving per home the
      reform reaches is spaces avoided x cost per space (both from the ledger,
      as the parking reform used them), baseline-weighted, in each draw.
    - **ADUs, missing middle, transit-oriented density.** No direct public
      cost; administrative (and, for transit-oriented density, infrastructure)
      cost is not modelled. These are plain texts.

All functions are pure (arrays and records in, records out) except
:func:`build_readouts`, which may rerun the fee reform once with the state fee
level (no file access).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger import Ledger, sample
from visionhousing.scenarios.fees import load_cost_settings

__all__ = [
    "COST_TEXTS",
    "MUCH_LESS_ID",
    "NOT_DEFINED",
    "THRESHOLD_IDS",
    "build_readouts",
    "chance_at_least",
    "cost_per_added",
    "cost_readouts",
    "deferral_readouts",
    "fiscal_split",
    "group_sums",
    "local_per_unit",
    "paired_readouts",
    "parking_readouts",
    "width",
]

SPAN = "2027-2031"
THRESHOLD_IDS: tuple[str, ...] = tuple(f"explain.chance_threshold_{i}_units" for i in (1, 2, 3, 4))
"""Ledger ids of the "chance the reform adds at least N homes" thresholds."""
MUCH_LESS_ID = "explain.paired_much_less_ratio"
"""Ledger id: the width ratio under which the effect is "much less" uncertain than the level."""

FEE_TOTAL = "fees.fee_total_per_unit"
FEE_CUT = "fees.fee_cut_share"
FEE_STATE = "fees.pa_assumed_fee_per_unit_judgement"
REPLACEMENT = "fees.replacement_funding_share"
DEFER_MONTHS = "fees.deferral_months"
LOAN_RATE = "fees.construction_loan_rate"
PUBLIC_RATE = "fees.public_discount_rate"
COLLECTION_LOSS = "fees.deferral_collection_loss_share"
MIN_SHARE = "fees.cost_per_added_min_share"
PARKING_COST_IDS = (
    "parking.cost_per_space_surface",
    "parking.cost_per_space_structured",
    "parking.cost_per_space_underground",
    "parking.mix_structured_transit_rich",
    "parking.mix_underground_transit_rich",
    "parking.mix_structured_elsewhere",
    "parking.mix_underground_elsewhere",
    "parking.mix_surface_attached",
)
PARKING_SPACE_IDS = (
    "parking.required_spaces_per_unit_multifamily",
    "parking.required_spaces_per_unit_attached",
    "parking.share_still_built_transit_rich",
    "parking.share_still_built_elsewhere",
)

NOT_DEFINED = (
    "Cost per added home is not defined: the reform adds no homes in any simulated future, "
    "so there is nothing to divide the revenue given up by."
)
"""The text given instead of a cost per added home when added homes are zero in every draw."""


def not_defined_text(share: float, min_share: float) -> str:
    """The "not defined" text: no draw adds homes, or too few do (share below the minimum)."""
    if share <= 0:
        return NOT_DEFINED
    return (
        f"Cost per added home is not defined: the reform adds homes in only {share:.0%} of "
        f"the modeled draws, below the {min_share:.0%} minimum (ledger {MIN_SHARE}), so a "
        "cost per added home would describe a minority of outcomes."
    )


def min_added_share(ledger: Ledger | None) -> float:
    """The ledger minimum share of draws with added homes (0 when the ledger lacks it)."""
    return float(ledger[MIN_SHARE].central) if ledger is not None and MIN_SHARE in ledger else 0.0


COST_TEXTS = {
    "adu": (
        "No direct public cost: the reform changes what may be built, not what anyone is "
        "paid. The administrative cost of permits and inspections is not modelled."
    ),
    "missing_middle": (
        "No direct public cost: the reform changes what may be built, not what anyone is "
        "paid. The administrative cost of permits and inspections is not modelled."
    ),
    "tod": (
        "No direct public cost: the reform changes what may be built near transit, not what "
        "anyone is paid. Administrative cost and the cost of added infrastructure (water, "
        "sewer, transit capacity) are not modelled."
    ),
    "parking": (
        "No direct public cost: parking minimums are removed, not paid for. The saving goes "
        "to builders (below); administrative cost is not modelled."
    ),
    "fees": (
        "The public cost is the fee revenue given up. The cut applies to every permitted "
        "home, including the homes that would be built anyway."
    ),
}
"""Plain cost framing per reform family (fixed words, no figures)."""


# ---------------------------------------------------------------- pure helpers


def width(draws: np.ndarray) -> float:
    """P90 minus P10 of some draws (the same percentile rule as ``records.from_draws``)."""
    p10, p90 = np.percentile(np.asarray(draws, dtype=float), [10, 90])
    return float(p90 - p10)


def chance_at_least(draws: np.ndarray, threshold: float) -> float:
    """Share of draws whose value is at least ``threshold`` (0 to 1)."""
    arr = np.asarray(draws, dtype=float)
    if arr.size == 0:
        raise ValueError("need at least one draw")
    return float(np.mean(arr >= threshold))


def mc_standard_error(share: float, n: int) -> float:
    """Monte Carlo standard error of a share of ``n`` independent draws: sqrt(p (1 - p) / n).

    It measures only how much the share would move with another set of
    ``n`` draws; it says nothing about structural or data uncertainty.
    """
    if n <= 0:
        raise ValueError("need at least one draw")
    p = min(max(float(share), 0.0), 1.0)
    return float(np.sqrt(p * (1.0 - p) / n))


def share_percent(share: float, n: int) -> str:
    """A share of draws as a percent at meaningful precision for ``n`` draws.

    Whole percents when the Monte Carlo standard error is at least half a
    percentage point (for example 2,000 draws and a share near 50%) or the
    share is exactly 0 or 1, else one decimal. So a share is never printed
    more finely than the draws support.
    """
    se_points = 100.0 * mc_standard_error(share, n)
    pct = 100.0 * float(share)
    if se_points >= 0.5 or float(share) in (0.0, 1.0):
        return f"{pct:.0f}%"
    return f"{pct:.1f}%"


def draw_share_words(share: float, n: int) -> str:
    """``in 60% of the modeled draws (Monte Carlo standard error 1.1 points, 2,000 draws)``."""
    se = 100.0 * mc_standard_error(share, n)
    return (
        f"in {share_percent(share, n)} of the modeled draws (Monte Carlo standard error "
        f"{se:.1f} percentage points, {n:,} draws)"
    )


def cost_per_added(revenue: np.ndarray, added: np.ndarray) -> np.ndarray:
    """Revenue given up divided by added homes, per draw; ``nan`` where nothing is added.

    Args:
        revenue: Revenue given up per draw ($).
        added: Added homes per draw.

    Returns:
        $ per added home; ``nan`` (never infinity) where added homes are 0 or less.
    """
    rev = np.asarray(revenue, dtype=float)
    add = np.asarray(added, dtype=float)
    ok = add > 0
    out = np.full(np.broadcast(rev, add).shape, np.nan)
    np.divide(rev, np.where(ok, add, 1.0), out=out, where=ok)
    return out


def group_sums(
    county_draws: np.ndarray, county_ids: Sequence[str], groups: Mapping[str, str]
) -> dict[str, np.ndarray]:
    """Sum county draws within each group, draw by draw.

    Args:
        county_draws: Shape ``(counties, draws)``.
        county_ids: County ids in row order.
        groups: County id to group name (counties without a group are left out).

    Returns:
        Group name to its draws, groups in order of first appearance by name.
    """
    arr = np.asarray(county_draws, dtype=float)
    out: dict[str, np.ndarray] = {}
    for name in sorted({groups[c] for c in county_ids if c in groups}):
        rows = [i for i, c in enumerate(county_ids) if groups.get(c) == name]
        out[name] = arr[rows].sum(axis=0)
    return out


def _slug(text: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "_" for ch in text)
    return "_".join(p for p in out.split("_") if p)


def _value(name: str, value: float, unit: str, formula: str, ids, sources=(), tier=None) -> Record:
    return Record(
        name=name,
        value=float(value),
        unit=unit,
        formula=formula,
        assumption_ids=tuple(sorted(set(ids))),
        source_ids=tuple(sorted(set(sources))),
        tier=tier,
    )


def _sources(ledger: Ledger, ids: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({s.id for i in ids if i in ledger for s in ledger[i].source}))


# ---------------------------------------------------------------- paired readouts


def paired_readouts(
    title: str,
    added: Record,
    state_draws: np.ndarray,
    county_draws: np.ndarray,
    base_state: np.ndarray,
    base_county: np.ndarray,
    county_ids: Sequence[str],
    ledger: Ledger,
    baseline: Record,
) -> tuple[dict[str, Record], dict[str, dict[str, Record]]]:
    """The paired readouts of one reform.

    Args:
        title: The reform's plain name.
        added: The reform's state record of added units (for its ids and sources).
        state_draws: Added units by draw (state).
        county_draws: Added units by county and draw.
        base_state: Baseline units by draw (state), the same draws.
        base_county: Baseline units by county and draw.
        county_ids: County ids in row order.
        ledger: The run's ledger (thresholds and the width rule).
        baseline: The baseline's state record (for its ids and sources).

    Returns:
        ``(state records, {"share_of_baseline": county id -> record})``.
    """
    n = int(np.asarray(state_draws).size)
    what = f"added units, {title} {SPAN}"
    ids = sorted({*added.assumption_ids, *baseline.assumption_ids})
    sources = sorted({*added.source_ids, *baseline.source_ids})
    out: dict[str, Record] = {}
    for i, pid in enumerate(THRESHOLD_IDS, start=1):
        if pid not in ledger:
            continue
        n_min = float(ledger[pid].central)
        share = chance_at_least(state_draws, n_min)
        se = mc_standard_error(share, n)
        out[f"chance_at_least_{i}"] = _value(
            f"share of the modeled draws in which the reform adds at least "
            f"{rec.format_number(n_min)} homes, {title}",
            share,
            "share",
            (
                f"share of {n:,} paired draws in which {what} (state) is at least "
                f"{rec.format_number(n_min, 'units')} (ledger {pid}) = "
                f"{rec.format_number(share)}: {draw_share_words(share, n)}"
            ),
            [*added.assumption_ids, pid],
            added.source_ids,
            added.tier,
        )
        out[f"chance_at_least_{i}_se"] = _value(
            f"Monte Carlo standard error of that share, {title}",
            se,
            "share",
            (
                f"sqrt(p x (1 - p) / draws) = sqrt({rec.format_number(share)} x "
                f"(1 - {rec.format_number(share)}) / {n:,}) = {se:.4f}; draw-to-draw noise "
                "only, before structural or data uncertainty"
            ),
            [*added.assumption_ids, pid],
            added.source_ids,
            added.tier,
        )
    b_width = width(base_state)
    a_width = width(state_draws)
    out["baseline_width"] = _value(
        "width of the baseline's likely range (P90 minus P10), state",
        b_width,
        "units",
        (
            f"P90 {rec.MINUS} P10 of {n:,} seeded draws of baseline units {SPAN}, state = "
            f"{rec.format_number(b_width, 'units')}"
        ),
        baseline.assumption_ids,
        baseline.source_ids,
        baseline.tier,
    )
    out["added_width"] = _value(
        f"width of the likely range of added units (P90 minus P10), {title}, state",
        a_width,
        "units",
        (
            f"P90 {rec.MINUS} P10 of the same {n:,} draws of {what} (state, paired with the "
            f"baseline draw by draw) = {rec.format_number(a_width, 'units')}"
        ),
        added.assumption_ids,
        added.source_ids,
        added.tier,
    )
    if b_width > 0:
        ratio = rec.div(
            out["added_width"], out["baseline_width"], name=f"width ratio, {title}", unit="ratio"
        )
        out["width_ratio"] = ratio.model_copy(
            update={"assumption_ids": tuple(sorted({*ratio.assumption_ids, MUCH_LESS_ID}))}
        )
    share = np.where(
        base_county > 0, 100.0 * county_draws / np.where(base_county > 0, base_county, 1.0), 0.0
    )
    counties = {
        c: rec.from_draws(
            f"{what}, % of baseline, county {c}",
            share[i],
            "% of baseline",
            expression=(
                "100 x the county's added units / the county's baseline units in the same draw "
                "(0 where the county's baseline is 0)"
            ),
            assumption_ids=ids,
            source_ids=sources,
            tier=added.tier,
        )
        for i, c in enumerate(county_ids)
    }
    return out, {"share_of_baseline": counties}


# ---------------------------------------------------------------- fees


def _cost_records(
    label: str,
    revenue: np.ndarray,
    added: np.ndarray,
    *,
    ids: Sequence[str],
    sources: Sequence[str],
    key: str,
    min_share: float = 0.0,
) -> tuple[dict[str, Record], str | None]:
    """Revenue and cost-per-added-home records for one geography or variant.

    The cost record is made only when the share of draws that add homes is at
    least ``min_share`` (and above zero); otherwise the text says why not.
    """
    n = revenue.size
    out = {
        f"revenue_given_up{key}": rec.from_draws(
            f"fee revenue given up {SPAN}, {label}",
            revenue,
            "$",
            expression="fee cut per unit x (baseline units + added units)",
            assumption_ids=ids,
            source_ids=sources,
            tier=0,
        )
    }
    cost = cost_per_added(revenue, added)
    ok = np.isfinite(cost)
    share = float(ok.mean()) if n else 0.0
    if not ok.any() or share < min_share:
        return out, not_defined_text(share, min_share)
    used = "" if ok.all() else f" (the {int(ok.sum()):,} of {n:,} draws in which it adds homes)"
    out[f"cost_per_added_home{key}"] = rec.from_draws(
        f"public cost per added home, {label} (homes added {draw_share_words(share, n)})",
        cost[ok],
        "$/unit",
        expression=(
            f"fee revenue given up / added units, in each draw{used}; shown only when at "
            f"least {min_share:.0%} of draws add homes (ledger {MIN_SHARE})"
        ),
        assumption_ids=sorted({*ids, MIN_SHARE}) if min_share > 0 else ids,
        source_ids=sources,
        tier=0,
    )
    return out, None


def cost_readouts(
    title: str,
    added: Record,
    county_draws: np.ndarray,
    base_county: np.ndarray,
    county_ids: Sequence[str],
    cut_per_unit: np.ndarray,
    *,
    fee_id: str,
    ledger: Ledger,
    groups: Mapping[str, str] | None = None,
    variant: tuple[str, np.ndarray, np.ndarray] | None = None,
    fiscal_seed: int = 0,
    local: Mapping[str, np.ndarray] | None = None,
) -> tuple[dict[str, Record], dict[str, dict[str, Record]], dict[str, str]]:
    """What a fee cut costs: revenue given up and cost per added home.

    Also the fiscal split (:func:`fiscal_split`): revenue waived on homes that
    would be built anyway, revenue waived on the added homes, the fees the
    added homes still pay, replacement funding, and the net change.

    Args:
        title: The reform's plain name.
        added: The reform's state record (ids and sources).
        county_draws: Added units by county and draw.
        base_county: Baseline units by county and draw (the same draws).
        county_ids: County ids in row order.
        cut_per_unit: Fee cut per unit in each draw ($), shape ``(draws,)``.
        fee_id: Ledger id of the fee level used.
        ledger: The reform's ledger.
        groups: County id to group name, for the targeted variant (optional).
        variant: ``(label, cut per unit by draw, added units by county and draw)``
            of a second fee level (optional).
        fiscal_seed: The run's seed, for the fee level and replacement share draws.
        local: For the documented local view, per-unit values by county and
            draw (shape ``(counties, draws)``): ``fee`` and ``cut`` of the
            baseline homes, ``fee_added`` and ``cut_added`` of the added homes
            (see :func:`local_per_unit`). ``cut_per_unit`` is then ignored.

    Returns:
        ``(state records, {"revenue_given_up" | "cost_per_added_home": county ->
        record}, texts)``. ``texts["cost_per_added_home"]`` is the "not
        defined" text when no draw adds homes, or fewer than the ledger minimum
        share (``fees.cost_per_added_min_share``) do.
    """
    ids = sorted({*added.assumption_ids, fee_id, FEE_CUT})
    sources = sorted({*added.source_ids, *_sources(ledger, [fee_id, FEE_CUT]), "bps_county_annual"})
    if local is not None:
        cut = np.asarray(local["cut"], dtype=float)
        cut_a = np.asarray(local["cut_added"], dtype=float)
        rev_c = cut * base_county + cut_a * county_draws
    else:
        cut = np.asarray(cut_per_unit, dtype=float)[None, :]
        cut_a = cut
        rev_c = cut * (base_county + county_draws)
    state_rev = rev_c.sum(axis=0)
    state_add = county_draws.sum(axis=0)
    floor = min_added_share(ledger)
    out, missing = _cost_records(
        "state", state_rev, state_add, ids=ids, sources=sources, key="", min_share=floor
    )
    texts = {"cost_per_added_home": missing} if missing else {}
    if local is not None:
        b_tot = base_county.sum(axis=0)
        avg_cut = np.where(
            b_tot > 0, (cut * base_county).sum(axis=0) / np.where(b_tot > 0, b_tot, 1.0), 0.0
        )
        cut_rec = rec.from_draws(
            "fee cut per unit, baseline-weighted state average",
            avg_cut,
            "$/unit",
            expression=(
                "sum over counties of baseline units x (fees per unit x cut share) / baseline "
                "units; fees per unit documented where a city's schedules are complete, else "
                f"ledger {fee_id}"
            ),
            assumption_ids=ids,
            source_ids=sources,
        )
        to_added = (cut_a * county_draws).sum(axis=0)
    else:
        cut_rec = rec.from_draws(
            "fee cut per unit",
            cut[0],
            "$/unit",
            expression=f"fees per unit (ledger {fee_id}) x cut share (ledger {FEE_CUT})",
            assumption_ids=[fee_id, FEE_CUT],
            source_ids=_sources(ledger, [fee_id, FEE_CUT]),
        )
        to_added = cut[0] * state_add
    out["fee_cut_per_unit"] = cut_rec
    share_new = np.where(state_rev > 0, to_added / np.where(state_rev > 0, state_rev, 1.0), 0.0)
    out["share_to_added_homes"] = rec.from_draws(
        "share of the revenue given up that goes to the added homes",
        share_new,
        "share",
        expression=(
            "fee cut per unit x added units / revenue given up (the rest goes to homes that "
            "would be built anyway)"
        ),
        assumption_ids=ids,
        source_ids=sources,
        tier=0,
    )
    if local is not None:
        fiscal = fiscal_split(
            cut,
            np.asarray(local["fee"], dtype=float),
            base_county,
            county_draws,
            ledger,
            ids=ids,
            sources=sources,
            seed=fiscal_seed,
            cut_added=cut_a,
            fee_added=np.asarray(local["fee_added"], dtype=float),
        )
    else:
        fiscal = fiscal_split(
            cut[0],
            fee_per_unit(ledger, fee_id, state_add.size, seed=fiscal_seed),
            base_county.sum(axis=0),
            state_add,
            ledger,
            ids=ids,
            sources=sources,
            seed=fiscal_seed,
        )
    out.update(fiscal)
    fiscal_words = load_cost_settings()["fiscal"]
    texts["fiscal_by_recipient"] = " ".join(str(fiscal_words["recipients_text"]).split())
    texts["fiscal_by_year"] = " ".join(str(fiscal_words["years_text"]).split())
    counties: dict[str, dict[str, Record]] = {"revenue_given_up": {}, "cost_per_added_home": {}}
    for i, c in enumerate(county_ids):
        recs, _ = _cost_records(
            f"county {c}", rev_c[i], county_draws[i], ids=ids, sources=sources, key="",
            min_share=floor,
        )  # fmt: skip
        counties["revenue_given_up"][c] = recs["revenue_given_up"]
        if "cost_per_added_home" in recs:
            counties["cost_per_added_home"][c] = recs["cost_per_added_home"]
    if groups:
        rev_g = group_sums(rev_c, county_ids, groups)
        add_g = group_sums(county_draws, county_ids, groups)
        for name in rev_g:
            recs, _ = _cost_records(
                f"fee cut limited to '{name}' counties",
                rev_g[name],
                add_g[name],
                ids=ids,
                sources=sources,
                key=f"_targeted_{_slug(name)}",
                min_share=floor,
            )
            out.update(recs)
    if variant is not None:
        label, v_cut, v_added = variant
        v_ids = sorted({*ids, FEE_STATE})
        v_sources = sorted({*sources, *_sources(ledger, [FEE_STATE])})
        v_rev = np.asarray(v_cut, dtype=float)[None, :] * (base_county + v_added)
        recs, v_missing = _cost_records(
            label, v_rev.sum(axis=0), v_added.sum(axis=0), ids=v_ids, sources=v_sources,
            key="_state_fees", min_share=floor,
        )  # fmt: skip
        out.update(recs)
        out["fee_cut_per_unit_state_fees"] = rec.from_draws(
            "fee cut per unit, state fee level",
            np.asarray(v_cut, dtype=float),
            "$/unit",
            expression=f"assumed state fees per unit, judgement (ledger {FEE_STATE}) x cut share",
            assumption_ids=[FEE_STATE, FEE_CUT],
            source_ids=_sources(ledger, [FEE_STATE, FEE_CUT]),
        )
        if v_missing:
            texts["cost_per_added_home_state_fees"] = v_missing
    return out, counties, texts


def local_per_unit(
    detail: Any, county_ids: Sequence[str], fallback_fee: np.ndarray, cut_share: np.ndarray
) -> dict[str, np.ndarray]:
    """Per-unit fee and cut by county and draw for the documented local view.

    The fee reform's detail holds fees per unit by county, structure type and
    draw (``per_unit["fee"]``), with the baseline units it reached
    (``per_unit["base"]``) and the added units (``added``). Each county's fee
    per baseline home is the baseline-weighted average over its structure
    types; per added home, the added-weighted average (the added homes' mix of
    types differs). Counties the reform did not reach (no tracts) take the
    fallback (the labelled state judgement), so every baseline home pays one
    fee level.

    Args:
        detail: The fee reform's ``ChannelBResult`` (local view).
        county_ids: The run's county ids, in row order.
        fallback_fee: Judgement fee per unit by draw, shape ``(draws,)``.
        cut_share: Cut share by draw, shape ``(draws,)``.

    Returns:
        ``fee``, ``cut``, ``fee_added``, ``cut_added``, each ``(counties, draws)``.
    """
    fb = np.asarray(fallback_fee, dtype=float)
    share = np.asarray(cut_share, dtype=float)
    fee_b = np.broadcast_to(fb, (len(county_ids), fb.size)).copy()
    fee_a = fee_b.copy()
    fee = np.asarray(detail.per_unit["fee"], dtype=float)
    base = np.asarray(detail.per_unit["base"], dtype=float)
    base = np.broadcast_to(base, fee.shape)
    add = np.asarray(detail.added, dtype=float)
    rows = {str(c): i for i, c in enumerate(county_ids)}
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = fee.mean(axis=1)
        wb = base.sum(axis=1)
        fb_c = np.where(wb > 0, (fee * base).sum(axis=1) / np.where(wb > 0, wb, 1.0), mean)
        wa = add.sum(axis=1)
        fa_c = np.where(wa > 0, (fee * add).sum(axis=1) / np.where(wa > 0, wa, 1.0), fb_c)
    for k, c in enumerate(detail.county_ids):
        if str(c) in rows:
            fee_b[rows[str(c)]] = fb_c[k]
            fee_a[rows[str(c)]] = fa_c[k]
    return {
        "fee": fee_b,
        "cut": fee_b * share[None, :],
        "fee_added": fee_a,
        "cut_added": fee_a * share[None, :],
    }


def fee_per_unit(ledger: Ledger, fee_id: str, n: int, *, seed: int) -> np.ndarray:
    """Fees per unit before the reform, by draw (the fee level the reform starts from)."""
    return sample(ledger, n=n, seed=seed, ids=[fee_id])[fee_id]


def fiscal_split(
    cut: np.ndarray,
    fee: np.ndarray,
    base: np.ndarray,
    added: np.ndarray,
    ledger: Ledger,
    *,
    ids: Sequence[str],
    sources: Sequence[str],
    seed: int = 0,
    cut_added: np.ndarray | None = None,
    fee_added: np.ndarray | None = None,
) -> dict[str, Record]:
    """The fiscal screen of a fee cut, state totals by draw.

    ``cut``, ``fee``, ``base`` and ``added`` are by draw, shape ``(draws,)``,
    or by county and draw, shape ``(counties, draws)``, summed over counties
    (the documented local view, where fees differ by county). ``cut_added``
    and ``fee_added`` are the per-unit cut and fee of the added homes when
    they differ from the baseline's (the added homes' mix of building types
    differs); by default the same as ``cut`` and ``fee``.

    In each draw::

        waived on homes built anyway  = cut per unit x baseline units
        waived on added homes         = cut per unit x added units
        (the two add up to the revenue given up)
        paid by added homes           = (fees per unit - cut per unit) x added units
        replacement funding           = replacement share x revenue given up
        net change in fee revenue     = - waived on homes built anyway
                                        + paid by added homes + replacement funding

    The added homes would not exist without the reform, so the fees they
    still pay are new revenue; the fees waived on them were never collected
    before. Replacement funding is ``fees.replacement_funding_share`` (none by
    default; 1 in the variant ``fees_funded_backfill``).

    The fiscal identity, with B baseline units, A added units, F the fee
    before and F' = F - cut after (same collection timing, no backfill)::

        revenue before  = F x B
        revenue after   = F' x (B + A)
        revenue change  = (F' - F) x B + F' x A
        net loss per added home = - revenue change / A   (draws with A > 0)

    Example: B = 100, A = 5, F = $50,000, F' = $25,000 gives $5.0m before,
    $2.625m after, a $2.375m decline, $475,000 per added home. The share of
    draws with A <= 0 is reported beside it: the revenue is lost there too.
    """
    cut = np.asarray(cut, dtype=float)
    fee = np.asarray(fee, dtype=float)
    base = np.asarray(base, dtype=float)
    added = np.asarray(added, dtype=float)
    cut_a = cut if cut_added is None else np.asarray(cut_added, dtype=float)
    fee_a = fee if fee_added is None else np.asarray(fee_added, dtype=float)

    def total(x: np.ndarray) -> np.ndarray:
        return x.sum(axis=0) if x.ndim == 2 else x

    waived_base = total(cut * base)
    waived_added = total(cut_a * added)
    paid = total((fee_a - cut_a) * added)
    n = waived_base.size
    share = (
        sample(ledger, n=n, seed=seed, ids=[REPLACEMENT])[REPLACEMENT]
        if REPLACEMENT in ledger
        else np.zeros(n)
    )
    replacement = share * (waived_base + waived_added)
    net = -waived_base + paid + replacement
    r_ids = sorted({*ids, *([REPLACEMENT] if REPLACEMENT in ledger else [])})
    common = {"source_ids": sources, "tier": 0}
    before = total(fee * base)
    after = total((fee - cut) * base) + paid
    change = after - before  # = (F' - F) x B + F' x A, the fiscal identity
    added = total(added)
    gain = added > 0
    zero = 1.0 - float(gain.mean()) if n else 0.0
    extra: dict[str, Record] = {
        "fiscal_revenue_before": rec.from_draws(
            f"fee revenue with no cut {SPAN} (F x B)",
            before,
            "$",
            expression="fees per unit F x baseline units B (the homes that would be built anyway)",
            assumption_ids=ids,
            **common,
        ),
        "fiscal_revenue_after": rec.from_draws(
            f"fee revenue with the cut {SPAN} (F' x (B + A))",
            after,
            "$",
            expression="fees per unit after the cut F' x (baseline units B + added units A)",
            assumption_ids=ids,
            **common,
        ),
        "fiscal_revenue_change": rec.from_draws(
            f"change in fee revenue before any replacement {SPAN}",
            change,
            "$",
            expression=(
                "(F' - F) x B + F' x A, in each paired draw (gross loss on the homes built "
                "anyway plus fees paid by the added homes; no backfill, same collection timing)"
            ),
            assumption_ids=ids,
            **common,
        ),
        "fiscal_zero_gain_share": _value(
            "share of the modeled draws in which the fee cut adds no homes",
            zero,
            "share",
            (
                f"share of {n:,} paired draws with added units A <= 0 = {zero:.4f}: "
                f"{draw_share_words(zero, n) if n else 'no draws'}; the revenue is lost in "
                "those draws too, so the cost per added home below is conditional on a gain"
            ),
            ids,
            sources,
            0,
        ),
    }
    if gain.any():
        per = -change[gain] / added[gain]
        extra["fiscal_net_loss_per_added_home"] = rec.from_draws(
            f"net fee revenue lost per added home (homes added {draw_share_words(1 - zero, n)})",
            per,
            "$/unit",
            expression=(
                "- ((F' - F) x B + F' x A) / A, in each draw where the cut adds homes (A > 0); "
                "read beside the share of draws that add none"
            ),
            assumption_ids=ids,
            **common,
        )
    return extra | {
        "fiscal_waived_on_baseline": rec.from_draws(
            f"fee revenue waived on homes that would be built anyway {SPAN}",
            waived_base,
            "$",
            expression="fee cut per unit x baseline units",
            assumption_ids=ids,
            **common,
        ),
        "fiscal_waived_on_added": rec.from_draws(
            f"fee revenue waived on the added homes {SPAN}",
            waived_added,
            "$",
            expression=(
                "fee cut per unit x added units (with the line above, the revenue given up)"
            ),
            assumption_ids=ids,
            **common,
        ),
        "fiscal_paid_by_added": rec.from_draws(
            f"fees still paid by the added homes {SPAN} (new revenue)",
            paid,
            "$",
            expression="(fees per unit - fee cut per unit) x added units",
            assumption_ids=ids,
            **common,
        ),
        "fiscal_replacement": rec.from_draws(
            f"replacement funding {SPAN}",
            replacement,
            "$",
            expression="replacement share x revenue given up (none unless a user sets the share)",
            assumption_ids=r_ids,
            **common,
        ),
        "fiscal_net_change": rec.from_draws(
            f"net change in fee revenue to the places that charge fees {SPAN}",
            net,
            "$",
            expression=(
                "- revenue waived on homes built anyway + fees still paid by the added homes "
                "+ replacement funding"
            ),
            assumption_ids=r_ids,
            **common,
        ),
    }


def deferral_readouts(
    added: Record,
    county_draws: np.ndarray,
    base_county: np.ndarray,
    ledger: Ledger,
    *,
    seed: int,
) -> dict[str, Record]:
    """The fiscal line of fee deferral: time value given up, no permanent revenue loss.

    In each draw::

        fees deferred      = fees per unit x (baseline units + added units)
        time value lost    = fees deferred x (1 - 1 / (1 + public rate) ^ (months / 12))
        collection loss    = fees deferred x share never collected
        public cost        = time value lost + collection loss
        paid by added homes = fees per unit x added units x (1 - share never collected)
        net change in fee revenue (present value) = paid by added homes - public cost
    """
    ids = [FEE_TOTAL, DEFER_MONTHS, PUBLIC_RATE, COLLECTION_LOSS]
    ids = [i for i in ids if i in ledger]
    n = county_draws.shape[1]
    v = sample(ledger, n=n, seed=seed, ids=ids)
    fee = v[FEE_TOTAL]
    base = np.asarray(base_county, dtype=float).sum(axis=0)
    add = np.asarray(county_draws, dtype=float).sum(axis=0)
    deferred = fee * (base + add)
    discount = 1.0 - 1.0 / (1.0 + v[PUBLIC_RATE]) ** (v[DEFER_MONTHS] / 12.0)
    time_cost = deferred * discount
    loss = deferred * v[COLLECTION_LOSS]
    public = time_cost + loss
    paid = fee * add * (1.0 - v[COLLECTION_LOSS])
    all_ids = sorted({*ids, *added.assumption_ids})
    sources = sorted({*added.source_ids, *_sources(ledger, ids), "bps_county_annual"})
    common = {"assumption_ids": all_ids, "source_ids": sources, "tier": 0}
    out = {
        "deferral_fees_deferred": rec.from_draws(
            f"fees deferred to occupancy {SPAN}",
            deferred,
            "$",
            expression="fees per unit x (baseline units + added units)",
            **common,
        ),
        "deferral_public_cost": rec.from_draws(
            f"public cost of deferral {SPAN} (time value and collection loss, no fee given up)",
            public,
            "$",
            expression=(
                "fees deferred x (1 - 1 / (1 + public discount rate) ^ (deferral months / 12)) "
                "+ fees deferred x share never collected"
            ),
            **common,
        ),
        "deferral_paid_by_added": rec.from_draws(
            f"fees paid by the added homes {SPAN} (new revenue)",
            paid,
            "$",
            expression="fees per unit x added units x (1 - share never collected)",
            **common,
        ),
        "deferral_net_change": rec.from_draws(
            f"net change in fee revenue, present value {SPAN}",
            paid - public,
            "$",
            expression="fees paid by the added homes - public cost of deferral",
            **common,
        ),
    }
    cost = cost_per_added(public, add)
    ok = np.isfinite(cost)
    share = float(ok.mean()) if ok.size else 0.0
    floor = min_added_share(ledger)
    if ok.any() and share >= floor:
        out["deferral_cost_per_added_home"] = rec.from_draws(
            f"public cost per added home, fee deferral (homes added "
            f"{draw_share_words(share, ok.size)})",
            cost[ok],
            "$/unit",
            expression=(
                "public cost of deferral / added units, in each draw where it adds homes; "
                f"shown only when at least {floor:.0%} of draws add homes (ledger {MIN_SHARE})"
            ),
            **{**common, "assumption_ids": sorted({*all_ids, MIN_SHARE}) if floor > 0 else all_ids},
        )
    return out


# ---------------------------------------------------------------- parking


def parking_readouts(
    title: str,
    added: Record,
    per_unit: Mapping[str, np.ndarray],
    added_ctd: np.ndarray,
    res_counties: Sequence[str],
    county_ids: Sequence[str],
    ledger: Ledger,
) -> tuple[dict[str, Record], dict[str, dict[str, Record]]]:
    """The builders' saving from the parking reform (no public cost).

    Args:
        title: The reform's plain name.
        added: The reform's state record (ids and sources).
        per_unit: ``base``, ``saving``, ``avoided`` and ``required`` by county,
            structure type and draw (from ``scenarios.parking``).
        added_ctd: Added units by county, structure type and draw.
        res_counties: County ids of the rows of those arrays.
        county_ids: The run's county ids.
        ledger: The reform's ledger.

    Returns:
        ``(state records, {"private_saving_total": county -> record})``.
    """
    reached = np.asarray(per_unit["required"], dtype=float) > 0
    base = np.where(reached, np.asarray(per_unit["base"], dtype=float), 0.0)
    saving = np.asarray(per_unit["saving"], dtype=float)
    avoided = np.asarray(per_unit["avoided"], dtype=float)
    homes = base.sum(axis=(0, 1))
    with np.errstate(divide="ignore", invalid="ignore"):
        avoided_w = np.where(homes > 0, (base * avoided).sum(axis=(0, 1)) / homes, 0.0)
        saving_w = np.where(homes > 0, (base * saving).sum(axis=(0, 1)) / homes, 0.0)
        per_space = np.where(avoided_w > 0, saving_w / np.where(avoided_w > 0, avoided_w, 1), 0.0)
    space_ids = [i for i in PARKING_SPACE_IDS if i in ledger]
    cost_ids = [i for i in PARKING_COST_IDS if i in ledger]
    ids = sorted({*space_ids, *cost_ids})
    sources = sorted({*_sources(ledger, ids), "bps_county_annual", "acs5_summary_file"})
    out = {
        "spaces_avoided_per_home": rec.from_draws(
            "parking spaces avoided per home the reform reaches",
            avoided_w,
            "spaces/unit",
            expression=(
                "max(v, old minimum) - max(v, minimum left), with v the spaces a project "
                "provides with no minimum (benchmark: v = spaces required x share still "
                "built), baseline-weighted over counties and 2-unit, 3-4-unit and 5+-unit "
                "buildings"
            ),
            assumption_ids=[*space_ids, "parking.transit_rich_min_share"]
            if "parking.transit_rich_min_share" in ledger
            else space_ids,
            source_ids=_sources(ledger, space_ids),
            tier=0,
        ),
        "cost_per_space_avoided": rec.from_draws(
            "cost per parking space avoided",
            per_space,
            "$/space",
            expression="the mix of surface, structured and underground spaces avoided, costed",
            assumption_ids=cost_ids,
            source_ids=_sources(ledger, cost_ids),
            tier=0,
        ),
        "private_saving_per_home": rec.from_draws(
            "private saving per home the reform reaches",
            saving_w,
            "$/unit",
            expression="spaces avoided per home x cost per space avoided, baseline-weighted",
            assumption_ids=ids,
            source_ids=sources,
            tier=0,
        ),
    }
    total_c = (np.where(reached, saving, 0.0) * (base + np.asarray(added_ctd, dtype=float))).sum(
        axis=1
    )
    rows = {c: i for i, c in enumerate(map(str, res_counties))}
    full = np.zeros((len(county_ids), total_c.shape[-1]))
    for j, c in enumerate(county_ids):
        if c in rows:
            full[j] = total_c[rows[c]]
    t_ids = sorted({*ids, *added.assumption_ids})
    t_sources = sorted({*sources, *added.source_ids})
    expression = (
        "saving per unit x (baseline units + added units) in the building types the reform reaches"
    )
    out["private_saving_total"] = rec.from_draws(
        f"private saving to builders {SPAN}, state",
        full.sum(axis=0),
        "$",
        expression=f"the sum over counties of {expression}",
        assumption_ids=t_ids,
        source_ids=t_sources,
        tier=0,
    )
    counties = {
        c: rec.from_draws(
            f"private saving to builders {SPAN}, county {c}",
            full[j],
            "$",
            expression=expression,
            assumption_ids=t_ids,
            source_ids=t_sources,
            tier=0,
        )
        for j, c in enumerate(county_ids)
    }
    return out, {"private_saving_total": counties}


# ---------------------------------------------------------------- one reform


def build_readouts(
    key: str,
    family: str,
    outcome: Any,
    base_state: np.ndarray,
    base_county: np.ndarray,
    county_ids: Sequence[str],
    ledger: Ledger,
    baseline: Record,
    *,
    seed: int,
    rerun: Any = None,
    groups: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Every readout of one reform, as fields for ``ReformOutcome``.

    Args:
        key: Reform key.
        family: Reform family (``adu``, ``fees``, ...).
        outcome: The reform's ``ReformOutcome`` (run alone).
        base_state: Baseline units by draw.
        base_county: Baseline units by county and draw.
        county_ids: The run's county ids.
        ledger: The run's ledger (thresholds).
        baseline: The baseline's state record.
        seed: The run's seed (fee levels are resampled from the reform's ledger).
        rerun: For the fee cut, a function ``options -> ReformResult`` that reruns
            it with other options (used for the state fee level), or ``None``.
        groups: County id to group name (targeted fee cut), or ``None``.

    Returns:
        ``{"readouts", "readout_counties", "readout_texts"}``.
    """
    state_draws = np.asarray(outcome.state_draws, dtype=float)
    county_draws = np.asarray(outcome.county_draws, dtype=float)
    records, counties = paired_readouts(
        outcome.title, outcome.added, state_draws, county_draws, base_state, base_county,
        county_ids, ledger, baseline,
    )  # fmt: skip
    texts: dict[str, str] = {}
    if family in COST_TEXTS:
        texts["cost"] = COST_TEXTS[family]
    res = outcome.result
    detail = getattr(res, "detail", None)
    r_ledger = res.ledger if res is not None and res.ledger is not None else ledger
    n = state_draws.size
    window = (getattr(res, "records", None) or {}).get("baseline_window_factor")
    if window is not None:  # a baseline-window sensitivity: the fiscal B is the scaled baseline
        base_county = np.asarray(base_county, dtype=float) * float(window.value)
    if family == "fees" and detail is not None and getattr(detail, "view", "") == "deferral":
        records.update(
            deferral_readouts(outcome.added, county_draws, base_county, r_ledger, seed=seed)
        )
    elif family == "fees" and detail is not None and getattr(detail, "view", "") == "local":
        params = sample(r_ledger, n=n, seed=seed, ids=[FEE_STATE, FEE_CUT])
        local = local_per_unit(detail, county_ids, params[FEE_STATE], params[FEE_CUT])
        more, more_c, more_t = cost_readouts(
            outcome.title, outcome.added, county_draws, base_county, county_ids,
            params[FEE_STATE] * params[FEE_CUT], fee_id=FEE_STATE, ledger=r_ledger,
            groups=groups, fiscal_seed=seed, local=local,
        )  # fmt: skip
        records.update(more)
        counties.update(more_c)
        texts.update(more_t)
    elif family == "fees" and detail is not None:
        view = str(getattr(detail, "view", outcome.options.get("view", "benchmark")))
        fee_id = FEE_TOTAL if view == "benchmark" else FEE_STATE
        params = sample(r_ledger, n=n, seed=seed, ids=[fee_id, FEE_CUT])
        cut = params[fee_id] * params[FEE_CUT]
        variant = None
        if view == "benchmark" and rerun is not None and FEE_STATE in r_ledger:
            other = rerun({**dict(outcome.options), "view": "state"})
            v_params = sample(r_ledger, n=n, seed=seed, ids=[FEE_STATE, FEE_CUT])
            variant = (
                "state fee level (draft judgement)",
                v_params[FEE_STATE] * v_params[FEE_CUT],
                other.county_draws(tuple(county_ids)),
            )
        more, more_c, more_t = cost_readouts(
            outcome.title, outcome.added, county_draws, base_county, county_ids, cut,
            fee_id=fee_id, ledger=r_ledger, groups=groups, variant=variant, fiscal_seed=seed,
        )  # fmt: skip
        records.update(more)
        counties.update(more_c)
        texts.update(more_t)
    elif family == "parking" and detail is not None and getattr(detail, "per_unit", None):
        more, more_c = parking_readouts(
            outcome.title, outcome.added, detail.per_unit, detail.added, detail.county_ids,
            county_ids, r_ledger,
        )  # fmt: skip
        records.update(more)
        counties.update(more_c)
    return {"readouts": records, "readout_counties": counties, "readout_texts": texts}
