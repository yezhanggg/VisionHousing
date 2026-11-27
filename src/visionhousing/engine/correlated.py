"""Uncertainty structure: conservation checks and a correlated-uncertainty sensitivity.

Plain-language summary
----------------------
The benchmark draws every uncertain input on its own, and places the baseline
at one statewide housing-cycle position per draw: in a weak draw every county
is weak together. Two questions follow, and this module answers both without
changing any benchmark number.

**1. Do the pieces add up in every draw?** For the baseline, each reform, the
package and the total, the state equals the sum of its counties and the
five-year total equals the sum of its years, draw by draw
(:func:`conservation`). The state's range is calibrated on state totals,
separately from the county ranges, so it is worth saying how the two meet: in
each draw every county's baseline is its own P50 times the state's multiplier
for that draw. So the state draw is exactly the sum of the county draws, and
the state's P10 / P50 / P90 are read off those sums; no percentile is ever
added. The price is that, inside the draws, each county swings by the state's
proportion, not by its own record's (wider) range; :func:`county_reconciliation`
measures that gap.

**2. What if uncertainty is shared?** Named sensitivity runs, beside the
benchmark (settings in ``assumptions/uncertainty_structure.yaml``):

* ``regional`` (macro shock + regional residuals): the statewide position
  stays, and each county group (the baseline's place type) also gets its own
  multiplier per draw, with a spread measured from the shipped rule's past
  group-versus-state errors (:func:`regional_sigmas`). In the shown version
  (*reconciled*) the multipliers are rescaled in each draw so the state total
  equals the benchmark's: counties trade places inside the calibrated state
  range. The *unreconciled* version is kept as a diagnostic only.
* ``correlated`` (high cost + tight credit + slow review): one common factor
  per draw; listed ledger inputs follow it (each keeps exactly its own values,
  only their order across draws changes; :func:`factor_ranks`), and the
  baseline's position follows it the other way, so a costly, tight-credit,
  slow-review draw is also a weak-cycle draw for the baseline and every reform.
  The *adverse corner* is the subset of draws with the factor in its top tail.
* ``control``: the same reordering with no common factor; it should differ
  from the benchmark by sampling noise only.

Each scenario is a full engine run (:func:`run_scenario`) with two small hooks
(``ledger.sampling.joint_ranks`` and ``engine.montecarlo.baseline_scale_hook``)
active only inside it. ``visionhousing uncertainty-structure`` writes
``assumptions/calibration/uncertainty_structure.yaml``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from visionhousing.engine import records as rec
from visionhousing.engine.montecarlo import baseline_scale_hook, scale_at
from visionhousing.ledger import stream
from visionhousing.ledger.loader import ROOT
from visionhousing.ledger.sampling import joint_ranks

__all__ = [
    "RESULTS_PATH",
    "SCENARIOS",
    "SETTINGS_PATH",
    "conservation",
    "county_reconciliation",
    "factor_ranks",
    "load_settings",
    "ranks_of",
    "reconcile",
    "regional_multipliers",
    "regional_sigmas",
    "run_scenario",
    "state_report",
]

SETTINGS_PATH = ROOT / "assumptions" / "uncertainty_structure.yaml"
RESULTS_PATH = ROOT / "assumptions" / "calibration" / "uncertainty_structure.yaml"
SCENARIOS = ("benchmark", "regional", "regional_unreconciled", "correlated", "control")
FACTOR_STREAM = "engine.correlated.factor"
SOURCE = "assumptions/uncertainty_structure.yaml"


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Read ``assumptions/uncertainty_structure.yaml``."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# --------------------------------------------------------------------------- conservation


def _gap(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.max(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float))))


def conservation(result: Any) -> dict[str, dict[str, float]]:
    """Largest per-draw break of state = sum of counties and five years = sum of years.

    Args:
        result: An engine ``Result``.

    Returns:
        Quantity -> ``{"state_vs_counties", "years_vs_five_year_state",
        "years_vs_five_year_counties"}`` (units; 0 means it holds exactly).
    """
    out: dict[str, dict[str, float]] = {}

    def check(name: str, state: np.ndarray, counties: np.ndarray, annual: Any) -> None:
        row = {"state_vs_counties": _gap(state, np.asarray(counties).sum(axis=0))}
        if annual is not None:
            row["years_vs_five_year_state"] = _gap(
                np.asarray(annual.state_draws).sum(axis=1), state
            )
            row["years_vs_five_year_counties"] = _gap(
                np.asarray(annual.county_draws).sum(axis=2), counties
            )
        out[name] = row

    check("baseline", result.baseline_draws, result.baseline_county_draws, result.baseline_annual)
    for key, o in result.reforms.items():
        check(key, o.state_draws, o.county_draws, o.annual)
    if result.reform_ids:
        check("package", result.package.state_draws, result.package.county_draws,
              result.package_annual)  # fmt: skip
        check(
            "total",
            result.baseline_draws + result.package.state_draws,
            result.baseline_county_draws + result.package.county_draws,
            result.total_annual,
        )
    return out


def county_reconciliation(result: Any) -> dict[str, Any]:
    """How the county draws meet the separately calibrated state range.

    Each county's draws are its P50 times the state's multiplier, so their
    P10 / P90 ratio to the P50 is the state's. The county's own record (from its
    pool's past errors) is usually wider. Reported: the state's spread ratios,
    and across counties with a positive P50 the median and range of
    (record width) / (width of its draws).
    """
    b = result.baseline
    c = np.asarray(result.baseline_county_draws, dtype=float)
    ratios = []
    for i, cid in enumerate(result.county_ids):
        r = result.baseline_counties.get(cid)
        if r is None or not r.p50 or r.p50 <= 0:
            continue
        lo, hi = np.percentile(c[i], [10, 90])
        if hi - lo <= 0:
            continue
        ratios.append((float(r.p90) - float(r.p10)) / (hi - lo))
    arr = np.array(ratios)
    return {
        "state_p10_over_p50": float(b.p10) / float(b.p50) if b.p50 else math.nan,
        "state_p90_over_p50": float(b.p90) / float(b.p50) if b.p50 else math.nan,
        "counties": int(arr.size),
        "record_width_over_draw_width": {
            "median": float(np.median(arr)) if arr.size else math.nan,
            "min": float(arr.min()) if arr.size else math.nan,
            "max": float(arr.max()) if arr.size else math.nan,
            "share_wider": float((arr > 1).mean()) if arr.size else math.nan,
        },
    }


# --------------------------------------------------------------------------- regional residuals


def regional_sigmas(context: Any, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Spread of each county group's past error relative to the state's (shipped rule).

    Returns:
        ``{"rule", "groups": {group: {"sigma", "origins", "counties", "pooled"}},
        "pooled_sigma", "group_of": {county: group}}``.
    """
    from visionhousing.baseline.contest import place_info_for_context, run_contest
    from visionhousing.baseline.panel import to_panel
    from visionhousing.baseline.places import load_place_rules

    reg = settings.get("regional", {})
    min_origins = int(reg.get("min_origins", 5))
    ddof = int(reg.get("ddof", 1))
    info = place_info_for_context(context)
    contest = run_contest(context.permits, context.ledger, load_place_rules(), info)
    panel = to_panel(context.permits)
    groups = np.array(contest.info.groups, dtype=object)
    cases = contest.cases
    ok = cases[np.isfinite(cases["actual"]) & (cases["projected"] > 0)]
    resid: dict[str, list[float]] = {str(g): [] for g in dict.fromkeys(groups.tolist())}
    for _, at in ok.groupby("origin"):
        a_all, p_all = float(at["actual"].sum()), float(at["projected"].sum())
        if a_all <= 0 or p_all <= 0:
            continue
        g_of = groups[at["place"].to_numpy(dtype=int)]
        for g in resid:
            sel = g_of == g
            a, p = float(at["actual"][sel].sum()), float(at["projected"][sel].sum())
            if a > 0 and p > 0:
                resid[g].append(math.log(a / p) - math.log(a_all / p_all))
    every = [x for v in resid.values() for x in v]
    pooled = float(np.std(every, ddof=ddof)) if len(every) > ddof else 0.0
    out = {}
    for g, v in resid.items():
        own = len(v) >= min_origins
        out[g] = {
            "sigma": float(np.std(v, ddof=ddof)) if own else pooled,
            "origins": len(v),
            "counties": int((groups == g).sum()),
            "pooled": not own,
        }
    return {
        "rule": contest.rule.name,
        "grouping": contest.info.source,
        "groups": out,
        "pooled_sigma": pooled,
        "group_of": {str(c): str(g) for c, g in zip(panel.counties, groups, strict=True)},
    }


def regional_multipliers(
    county_ids: Sequence[str],
    group_of: Mapping[str, str],
    sigmas: Mapping[str, float],
    n: int,
    seed: int,
) -> np.ndarray:
    """Mean-one lognormal multiplier per county group and draw, ``(counties, n)``.

    A county with no group (no permit record) gets 1.
    """
    z = {
        g: stream(seed, f"engine.correlated.regional.{g}").standard_normal(n)
        for g in sorted(set(group_of.values()))
    }
    out = np.ones((len(county_ids), n))
    for i, c in enumerate(county_ids):
        g = group_of.get(str(c))
        if g is None:
            continue
        s = float(sigmas.get(g, 0.0))
        out[i] = np.exp(s * z[g] - 0.5 * s * s)
    return out


def reconcile(mult: np.ndarray, p50: np.ndarray) -> np.ndarray:
    """Rescale county multipliers in each draw so the P50-weighted mean is 1.

    Then sum over counties of P50 x multiplier equals the sum of P50s in every
    draw, so the state total is unchanged by the residuals.
    """
    w = np.asarray(p50, dtype=float)
    total = w.sum()
    if total <= 0:
        return np.ones_like(mult)
    mean = (w[:, None] * mult).sum(axis=0) / total
    return mult / mean[None, :]


# --------------------------------------------------------------------------- common factor


def ranks_of(latent: np.ndarray) -> np.ndarray:
    """Rank (0 to n - 1) of each draw's latent value."""
    return np.argsort(np.argsort(latent, kind="stable"), kind="stable")


def factor_ranks(
    settings: Mapping[str, Any], n: int, seed: int, *, loading: float | None = None
) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray, dict[str, int]]:
    """The common factor, each listed input's ranks, and the baseline position's ranks.

    Args:
        settings: The settings file.
        n: Draws.
        seed: Seed.
        loading: Override the file's loading (0 = the control).

    Returns:
        ``(factor, ranks by ledger id, baseline ranks, sign by ledger id)``.
    """
    cor = settings["correlated"]
    rho = float(cor["loading"] if loading is None else loading)
    if not 0.0 <= rho <= 1.0:
        raise ValueError("loading must be between 0 and 1")
    factor = stream(seed, FACTOR_STREAM).standard_normal(n)
    rest = math.sqrt(max(0.0, 1.0 - rho * rho))
    signs: dict[str, int] = {}
    for comp in cor["components"].values():
        for pid, sign in comp.items():
            signs[str(pid)] = int(sign)
    ranks = {}
    for pid, sign in signs.items():
        e = stream(seed, f"engine.correlated.own.{pid}").standard_normal(n)
        ranks[pid] = ranks_of(sign * rho * factor + rest * e)
    e = stream(seed, "engine.correlated.own.baseline").standard_normal(n)
    base = ranks_of(int(cor["baseline_sign"]) * rho * factor + rest * e)
    return factor, ranks, base, signs


# --------------------------------------------------------------------------- runs


def run_scenario(
    context: Any,
    scenario: str,
    reforms: Sequence[str],
    *,
    seed: int,
    draws: int,
    settings: Mapping[str, Any],
    regional: Mapping[str, Any] | None = None,
) -> tuple[Any, np.ndarray | None]:
    """One engine run under a named scenario (``SCENARIOS``).

    Returns:
        ``(result, factor)``; the factor is ``None`` except for ``correlated``
        and ``control``.
    """
    from visionhousing.engine.core import compute, prepare

    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    factor = None
    with ExitStack() as stack:
        if scenario in ("regional", "regional_unreconciled"):
            reg = regional if regional is not None else regional_sigmas(context, settings)
            run_context, _ = prepare(context, seed=seed, draws=draws)
            ids = run_context.county_ids
            p50 = np.array(
                [
                    float(run_context.baseline.counties[c].p50)  # type: ignore[arg-type]
                    if c in run_context.baseline.counties
                    else 0.0
                    for c in ids
                ]
            )
            sig = {g: v["sigma"] for g, v in reg["groups"].items()}
            mult = regional_multipliers(ids, reg["group_of"], sig, draws, seed)
            if scenario == "regional":
                mult = reconcile(mult, p50)

            def hook(u, low, high, nc, mult=mult):
                return scale_at(u, low, high)[None, :] * mult

            stack.enter_context(baseline_scale_hook(hook))
        elif scenario in ("correlated", "control"):
            factor, ranks, base, _ = factor_ranks(
                settings, draws, seed, loading=0.0 if scenario == "control" else None
            )
            stack.enter_context(joint_ranks(seed, draws, ranks))

            def hook(u, low, high, nc, base=base):
                return np.broadcast_to(scale_at(np.sort(u)[base], low, high), (nc, u.size)).copy()

            stack.enter_context(baseline_scale_hook(hook))
        result = compute(context, list(reforms), seed=seed, draws=draws)
    return result, factor


# --------------------------------------------------------------------------- summaries


def _record(name: str, values: np.ndarray, expression: str, ids: Sequence[str] = ()) -> dict:
    r = rec.from_draws(
        name,
        np.asarray(values, dtype=float),
        "units",
        expression=expression,
        assumption_ids=sorted(set(ids)),
        source_ids=("bps_county_annual",),
        tier=0,
    )
    return {"p10": r.p10, "p50": r.p50, "p90": r.p90, "formula": r.formula,
            "assumption_ids": list(r.assumption_ids)}  # fmt: skip


def _series(result: Any) -> dict[str, np.ndarray]:
    out = {"baseline": np.asarray(result.baseline_draws, dtype=float)}
    for k, o in result.reforms.items():
        out[k] = np.asarray(o.state_draws, dtype=float)
    if result.reform_ids:
        out["package"] = np.asarray(result.package.state_draws, dtype=float)
        out["total"] = out["baseline"] + out["package"]
    return out


def _ids(result: Any, key: str) -> tuple[str, ...]:
    if key == "baseline":
        return tuple(result.baseline.assumption_ids)
    if key in result.reforms:
        return tuple(result.reforms[key].assumption_ids)
    if key in ("package", "total"):
        return tuple(result.package.combined.assumption_ids)
    return ()


def ranking(series: Mapping[str, np.ndarray], reforms: Sequence[str]) -> dict[str, Any]:
    """Order by P50, the share of draws in which each reform adds the most, and pairs."""
    keys = [k for k in reforms if k in series]
    p50 = {k: float(np.percentile(series[k], 50)) for k in keys}
    order = sorted(keys, key=lambda k: (-p50[k], k))
    stack = np.vstack([series[k] for k in keys])
    first = np.argmax(stack, axis=0)
    n = stack.shape[1]
    pairs = {
        f"{a}>{b}": float(np.mean(series[a] > series[b]))
        for i, a in enumerate(order)
        for b in order[i + 1 :]
    }
    return {
        "order_by_p50": order,
        "first_share": {k: float(np.mean(first == i)) for i, k in enumerate(keys)},
        "pair_share": pairs,
        "mc_se_at_half": math.sqrt(0.25 / n),
    }


def _diagnostic_draws(result: Any, percentiles: Sequence[float]) -> list[int]:
    b = np.asarray(result.baseline_draws, dtype=float)
    out = []
    for q in percentiles:
        target = np.percentile(b, float(q))
        out.append(int(np.argmin(np.abs(b - target))))
    return out


def _draw_diagnostics(
    result: Any, draws_at: Sequence[int], group_of: Mapping[str, str] | None
) -> list[dict[str, Any]]:
    rows = []
    c = np.asarray(result.baseline_county_draws, dtype=float)
    ann = result.baseline_annual
    for d in draws_at:
        row: dict[str, Any] = {
            "draw": int(d),
            "state_baseline": float(result.baseline_draws[d]),
            "sum_of_counties": float(c[:, d].sum()),
            "state_minus_counties": float(result.baseline_draws[d] - c[:, d].sum()),
        }
        if ann is not None:
            yrs = np.asarray(ann.state_draws)[d]
            row["years"] = {int(y): float(v) for y, v in zip(ann.years, yrs, strict=True)}
            row["five_year_minus_sum_of_years"] = float(result.baseline_draws[d] - yrs.sum())
        if result.reform_ids:
            pk = np.asarray(result.package.county_draws)[:, d]
            row["package_state_minus_counties"] = float(result.package.state_draws[d] - pk.sum())
        if group_of:
            tot: dict[str, float] = {}
            for i, cid in enumerate(result.county_ids):
                g = group_of.get(str(cid), "no permit record")
                tot[g] = tot.get(g, 0.0) + float(c[i, d])
            row["baseline_by_group"] = dict(sorted(tot.items()))
        rows.append(row)
    return rows


def _group_ranges(result: Any, group_of: Mapping[str, str], label: str) -> dict[str, Any]:
    c = np.asarray(result.baseline_county_draws, dtype=float)
    idx: dict[str, list[int]] = {}
    for i, cid in enumerate(result.county_ids):
        idx.setdefault(group_of.get(str(cid), "no permit record"), []).append(i)
    return {
        g: _record(
            f"baseline units 2027-2031, county group {g}, {label}",
            c[ix].sum(axis=0),
            "sum of the group's county baseline units, in each draw",
            result.baseline.assumption_ids,
        )
        for g, ix in sorted(idx.items())
    }


def state_report(
    context: Any,
    *,
    reforms: Sequence[str] | None = None,
    settings: Mapping[str, Any] | None = None,
    seed: int | None = None,
    draws: int | None = None,
    scenarios: Sequence[str] = SCENARIOS,
) -> dict[str, Any]:
    """Run the scenarios for one state and summarise them (see the module docstring).

    ``reforms=[]`` runs the baseline only (the conservation check and the
    regional baseline). Every range is read from the scenario's own draws.
    """
    settings = dict(settings or load_settings())
    run = settings.get("run", {})
    seed = int(run.get("seed", 0) if seed is None else seed)
    draws = int(run.get("draws", 2000) if draws is None else draws)
    reforms = list(run.get("reforms", []) if reforms is None else reforms)
    tol = float(settings.get("conservation", {}).get("tolerance_units", 1e-6))
    pct = settings.get("conservation", {}).get("diagnostic_draw_percentiles", [10, 50, 90])
    reg = regional_sigmas(context, settings)
    group_of = reg["group_of"]
    out: dict[str, Any] = {
        "seed": seed,
        "draws": draws,
        "reforms": reforms,
        "regional_residuals": {k: v for k, v in reg.items() if k != "group_of"},
        "scenarios": {},
    }
    bench_series: dict[str, np.ndarray] | None = None
    draws_at: list[int] = []
    for name in scenarios:
        result, factor = run_scenario(
            context, name, reforms, seed=seed, draws=draws, settings=settings, regional=reg
        )
        cons = conservation(result)
        worst = max((v for row in cons.values() for v in row.values()), default=0.0)
        series = _series(result)
        if name == "benchmark":
            bench_series = series
            draws_at = _diagnostic_draws(result, pct)
            out["county_reconciliation"] = county_reconciliation(result)
        label = name.replace("_", " ")
        block: dict[str, Any] = {
            "conservation": {"by_quantity": cons, "worst_units": worst, "holds": worst <= tol},
            "ranges": {
                k: _record(
                    f"{k} units 2027-2031, {label} scenario",
                    v,
                    f"the {k} state total in each draw of the {label} scenario ({SOURCE}, {name})",
                    _ids(result, k),
                )
                for k, v in series.items()
            },
            "diagnostic_draws": _draw_diagnostics(result, draws_at, group_of),
            "group_ranges": _group_ranges(result, group_of, label),
        }
        if reforms:
            block["ranking"] = ranking(series, reforms)
        if factor is not None:
            cut = float(np.quantile(factor, float(settings["correlated"]["adverse_quantile"])))
            sel = factor >= cut
            block["adverse_corner"] = {
                "draws": int(sel.sum()),
                "ranges": {
                    k: _record(
                        f"{k} units 2027-2031, {label} scenario, adverse corner",
                        v[sel],
                        f"the {k} state total in the draws whose common factor is in its top "
                        f"{100 - 100 * float(settings['correlated']['adverse_quantile']):g}% "
                        f"({SOURCE}, {name})",
                        _ids(result, k),
                    )
                    for k, v in series.items()
                },
            }
            if reforms:
                block["adverse_corner"]["ranking"] = ranking(
                    {k: v[sel] for k, v in series.items()}, reforms
                )
            led = result.draws_obj.ledger
            _, _, _, signs = factor_ranks(settings, draws, seed)
            block["moved_inputs"] = {
                pid: ("fixed in the ledger, not moved" if led[pid].shape == "fixed" else "moved")
                for pid in signs
                if pid in led
            }
        if bench_series is not None and name != "benchmark":
            block["vs_benchmark"] = {
                k: {
                    "p50_change": float(np.percentile(v, 50) - np.percentile(bench_series[k], 50)),
                    "width_ratio": _width_ratio(v, bench_series[k]),
                }
                for k, v in series.items()
                if k in bench_series
            }
            if reforms:
                b_order = ranking(bench_series, reforms)["order_by_p50"]
                block["vs_benchmark"]["ranking_order_same"] = (
                    block["ranking"]["order_by_p50"] == b_order
                )
        out["scenarios"][name] = block
        del result
    return out


def _width_ratio(a: np.ndarray, b: np.ndarray) -> float:
    wa = float(np.percentile(a, 90) - np.percentile(a, 10))
    wb = float(np.percentile(b, 90) - np.percentile(b, 10))
    return wa / wb if wb > 0 else math.nan


# --------------------------------------------------------------------------- files and command


HEADER = """\
# Uncertainty structure (generated; do not edit). Written by
# `visionhousing uncertainty-structure`. Conservation checks for every state and
# the named correlated-uncertainty sensitivities (macro shock + regional
# residuals; high cost + tight credit + slow review). The benchmark headlines are
# unchanged. Settings: assumptions/uncertainty_structure.yaml. Method:
# docs/modules/correlated.md.
"""


def _clean(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return round(v, 6) if abs(v) < 1 else round(v, 2)
    return v


def write_results(states: Mapping[str, Any], path: Path = RESULTS_PATH) -> Path:
    """Merge per-state reports into the results file (other states are kept)."""
    doc: dict[str, Any] = {}
    if path.is_file():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    doc.setdefault("version", 1)
    doc["settings"] = SOURCE
    doc.setdefault("states", {})
    for s, rep in states.items():
        doc["states"][s] = _clean(rep)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + text, encoding="utf-8")
    return path


def add_parser(sub: Any) -> None:
    """Add ``uncertainty-structure`` to the command line."""
    p = sub.add_parser(
        "uncertainty-structure",
        help="Conservation checks and correlated-uncertainty sensitivities (writes the results)",
    )
    p.add_argument("--state", required=True, help="state code (TL = Testland)")
    p.add_argument(
        "--baseline-only", action="store_true", help="no reforms (conservation + regional baseline)"
    )
    p.add_argument("--draws", type=int, default=None, help="draws (default: the settings file's)")
    p.add_argument("--out", type=Path, default=RESULTS_PATH, help="results file")


def command(args: Any) -> int:
    """Run the scenarios for one state, print the headline ranges, write the results."""
    from visionhousing.engine.context import load_context

    ctx = load_context(args.state.upper())
    rep = state_report(ctx, reforms=[] if args.baseline_only else None, draws=args.draws)
    for name, block in rep["scenarios"].items():
        cons = "holds" if block["conservation"]["holds"] else "BROKEN"
        parts = [
            f"{k} {r['p10']:,.0f}-{r['p90']:,.0f} (P50 {r['p50']:,.0f})"
            for k, r in block["ranges"].items()
        ]
        print(f"{args.state.upper()} {name}: conservation {cons}; " + "; ".join(parts))
    print(f"wrote {write_results({args.state.upper(): rep}, args.out)}")
    return 0
