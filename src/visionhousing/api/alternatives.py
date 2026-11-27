"""Alternatives at a glance: each benchmark headline and every named alternative beside it.

Plain-language summary
----------------------
Each benchmark reform has one headline: the engine's likely range of added
units authorized by building permit over 2027-2031, with what it counts (its
scope). Beside it sit the **named alternatives** the tool has built for that
reform: named variants (``assumptions/variants.yaml``, for example the verified
parking minimums or the budget-capped ADU program), the practical-capacity
screen with its joint bounds, the package's project-choice and
building-capacity options, and the Census-comparable scope of the headline.
This module works all of them out once per state and writes a compact table.

For each alternative the table holds:

- its added units as P10 / P50 / P90 of its own draws;
- the **paired difference from the benchmark**: alternative minus benchmark,
  draw by draw (both runs use the same seed and the same draws), and only then
  the P10 / P50 / P90 of that difference. Percentiles are never added or
  subtracted;
- the share of draws in which the alternative is below or above the benchmark,
  with its Monte Carlo standard error ``sqrt(p (1 - p) / n)``;
- a one-line reason (the variant's own question, its first sentence, or the
  words in ``assumptions/alternatives.yaml``);
- a status: ``built``, ``not_built`` (with the reason it is not) or
  ``needs_local_app`` (the static site does not hold it).

Every number shown is text written here on the server, from the engine's draws,
so the browser, the hearing brief and the deck show the same words. Which rows
each reform lists is set in ``assumptions/alternatives.yaml``; every other
variant of the reform family is appended, so no named alternative is left out.

Where it is kept. The whole state's table is computed together (one benchmark
run of the five reforms with the capacity screen, two package runs with the
market option, and one run per variant) and kept in the comparisons disk cache
(:mod:`visionhousing.api.comparison_cache`, part ``alternatives``). It is read
back only while the pack, calibration, ledger, code and every assumptions file
are unchanged, so a stale table is never served.

Interfaces
----------
- ``GET /api/v1/states/{st}/alternatives?r=adu`` returns one reform's headline
  and rows (all reforms without ``r``). The first ask after a change computes
  the table (a few minutes for a large state; the address then answers 504 and
  keeps working, and the next ask is instant).
- ``python -m visionhousing.api.alternatives --state PA [--out FILE]`` computes
  the table once and keeps it on disk.
- :func:`write_static` saves ``ST/alternatives.json`` for the static site, from
  the disk cache only, and marks ``alternatives: true`` in the state's index.

Transit status. When a reform's statewide range is not shown (the transit
status rule, :mod:`visionhousing.api.status_view`), its headline and rows show
only the measured contribution's middle, never a range.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from fastapi import Request, Response

from visionhousing.ledger.loader import ROOT

__all__ = [
    "PART",
    "SETTINGS_PATH",
    "STATIC_FILE",
    "build_alternatives",
    "compact",
    "draw_share",
    "hearing_rows",
    "load_settings",
    "paired_row",
    "payload",
    "reform_view",
    "register",
    "write_static",
]

PART = "alternatives"
"""The name the table is kept under in the comparisons disk cache."""

STATIC_FILE = "alternatives.json"
"""The static export's file, in the state's folder."""

SETTINGS_PATH = ROOT / "assumptions" / "alternatives.yaml"
BENCHMARKS = ("adu", "missing_middle", "tod", "parking", "fees")
VERSION = 1
"""Layout version of the payload (bumped when a field changes meaning)."""

_TOL = 1e-9
"""A paired difference smaller than this (in units) counts as equal, not below or above."""


# ------------------------------------------------------------------ settings


@lru_cache(maxsize=4)
def _read(path: str, mtime: int) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_settings(path: Path | str | None = None) -> dict[str, Any]:
    """Read ``assumptions/alternatives.yaml`` (cached until the file changes)."""
    p = Path(path or SETTINGS_PATH)
    return _read(str(p), p.stat().st_mtime_ns)


def _words(text: Any) -> str:
    return " ".join(str(text or "").split())


def _first_sentence(text: Any) -> str:
    """The first sentence of a variant's question (its one-line reason)."""
    t = _words(text)
    for end in ("? ", ". "):
        i = t.find(end)
        if i > 0:
            return t[: i + 1]
    return t


# ------------------------------------------------------------------ text


def _n(value: float, *, signed: bool = False) -> str:
    """A count of homes as the tables write it (three significant figures)."""
    from visionhousing.explain.numbers import TABLE_SIG, count

    if abs(value) < 1e-6:  # a difference of float rounding, not of homes
        value = 0.0
    text = count(float(value), TABLE_SIG)
    if signed and value > 0 and text not in ("0",):
        return "+" + text
    return text


def _pct(p: float) -> str:
    """A share of draws as text, never rounded to 0% or 100% unless exactly so."""
    v = 100.0 * float(p)
    if p <= 0:
        return "0%"
    if p >= 1:
        return "100%"
    if v < 1:
        return "under 1%"
    if v > 99:
        return "over 99%"
    return f"{v:.0f}%"


def draw_share(mask: np.ndarray) -> dict[str, Any]:
    """Share of draws where ``mask`` holds, its Monte Carlo standard error and its text."""
    n = int(mask.size)
    p = float(mask.mean()) if n else 0.0
    se = math.sqrt(p * (1.0 - p) / n) if n else 0.0
    return {"share": p, "se": se, "text": _pct(p), "se_text": f"{100 * se:.1f} points"}


def _summary(arr: np.ndarray, *, signed: bool = False, hidden: bool = False) -> dict[str, Any]:
    p10, p50, p90 = (float(q) for q in np.percentile(arr, [10, 50, 90]))
    mid = _n(p50, signed=signed)
    rng = "not shown" if hidden else f"{_n(p10, signed=signed)} to {_n(p90, signed=signed)}"
    return {"p10": p10, "p50": p50, "p90": p90, "mid": mid, "range": rng}


def paired_row(
    alt_draws: Sequence[float] | np.ndarray,
    bench_draws: Sequence[float] | np.ndarray,
    *,
    hidden: bool = False,
) -> dict[str, Any]:
    """The numbers of one alternative against the benchmark, from paired draws.

    ``diff`` is computed draw by draw (alternative minus benchmark, same seed and
    draws) before any percentile is taken. With ``hidden`` (a statewide range not
    shown) only the middles are written as text.

    Returns:
        ``{"added", "diff", "below", "above", "same_every_draw"}``.
    """
    a = np.asarray(alt_draws, dtype=float)
    b = np.asarray(bench_draws, dtype=float)
    if a.shape != b.shape or a.ndim != 1 or a.size == 0:
        raise ValueError("alternative and benchmark draws must be paired one-dimensional arrays")
    diff = a - b
    return {
        "added": _summary(a, hidden=hidden),
        "diff": _summary(diff, signed=True, hidden=hidden),
        "below": draw_share(diff < -_TOL),
        "above": draw_share(diff > _TOL),
        "same_every_draw": bool(np.all(np.abs(diff) <= _TOL)),
    }


# ------------------------------------------------------------------ engine work


def _comparable_draws(outcome: Any, rules: Mapping[str, Any]) -> np.ndarray | None:
    """Census-comparable added units per draw, rebuilt exactly as the accounting builds them.

    ``engine.outcome.accounting`` sums the comparable work types per draw; this repeats
    that sum on the same draws. ``None`` when the module gives no work-type draws.
    """
    head = np.asarray(outcome.state_draws, dtype=float)
    detail = getattr(getattr(outcome, "result", None), "detail", None)
    given = {k: np.asarray(v, dtype=float) for k, v in
             dict(getattr(detail, "work_types", None) or {}).items()}  # fmt: skip
    if not given:
        return None
    total = sum(given.values())
    if not np.allclose(total, head):
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = np.where(total > 0, head / np.where(total > 0, total, 1.0), 0.0)
        given = {k: v * scale for k, v in given.items()}
        if not np.allclose(sum(given.values()), head):
            return None
    types = dict(rules.get("work_types") or {})
    zero = np.zeros_like(head)
    return sum((given.get(w, zero) for w, sp in types.items() if sp.get("bps") == "comparable"),
               zero)  # fmt: skip


def _hidden(outcome: Any) -> bool:
    status = getattr(getattr(outcome, "result", None), "status", None) or {}
    return (status.get("statewide") or {}).get("shown") is False


def _ids(*records: Any) -> tuple[list[str], list[str]]:
    a: set[str] = set()
    s: set[str] = set()
    for r in records:
        if r is None:
            continue
        a.update(getattr(r, "assumption_ids", ()) or ())
        s.update(getattr(r, "source_ids", ()) or ())
    return sorted(a), sorted(s)


def _beyond(bench: Any, *records: Any) -> tuple[list[str], list[str]]:
    """Ledger and source ids the alternative's records add to the benchmark's headline.

    The headline's own ids are in the run's record (``reform_results.<key>.added``); a row
    lists only what the alternative adds, so the table stays small and still traceable.
    """
    a, s = _ids(*records)
    ba, bs = _ids(bench)
    return sorted(set(a) - set(ba)), sorted(set(s) - set(bs))


def _row_ok(base: dict[str, Any], nums: dict[str, Any], status: str = "built") -> dict[str, Any]:
    return {**base, "status": status, **nums}


def _row_absent(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**base, "status": "not_built", "absent": _words(reason)}


Runner = Callable[..., Any]


def build_alternatives(
    ctx: Any,
    *,
    seed: int = 0,
    draws: int | None = None,
    settings: Mapping[str, Any] | None = None,
    run_fn: Runner | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Work out the state's whole table: every benchmark headline and every alternative.

    Args:
        ctx: The loaded state (``engine.context.load_context``).
        seed: Seed (the benchmark's).
        draws: Draws (default ``ledger.DEFAULT_DRAWS``).
        settings: ``assumptions/alternatives.yaml`` (default: read it).
        run_fn: The engine's ``run`` (tests pass a counting wrapper).
        progress: Called with a short line after each run.

    Returns:
        The payload (see :func:`reform_view` for one reform's part).
    """
    from visionhousing.engine import run as engine_run
    from visionhousing.engine.outcome import load_rules
    from visionhousing.engine.variants import load_variants
    from visionhousing.ledger import DEFAULT_DRAWS

    cfg = settings or load_settings()
    n = int(draws or DEFAULT_DRAWS)
    run = run_fn or engine_run
    say = progress or (lambda _m: None)
    state = ctx.state
    have = getattr(ctx, "scenarios", None) or BENCHMARKS
    keys = [k for k in BENCHMARKS if k in have]

    def go(reforms: Any, **kw: Any) -> Any:
        return run(state, reforms, None, seed=seed, draws=n, context=ctx, use_cache=False, **kw)

    bench = go(keys, capacity_screen=True)
    say(f"{state}: benchmarks with the capacity screen")
    markets: dict[str, Any] = {}
    for mode in ("choice", "choice+capacity"):
        try:
            markets[mode] = go(keys, market=mode).market
        except (ValueError, KeyError, RuntimeError) as err:
            markets[mode] = err
        say(f"{state}: package with market {mode}")
    corr = _correlated_run(ctx, keys, seed, n, cfg)
    say(f"{state}: correlated sensitivity")
    known = load_variants()
    rules = load_rules()
    kinds = cfg.get("kinds") or {}
    reforms: dict[str, Any] = {}
    for key in keys:
        o = bench.reforms[key]
        b = np.asarray(o.state_draws, dtype=float)
        hidden = _hidden(o)
        scope = dict(getattr(o.accounting, "headline", {}) or {}) if o.accounting else {}
        head = _summary(b, hidden=hidden)
        rows: list[dict[str, Any]] = []
        plan = list((cfg.get("reforms", {}).get(key) or {}).get("rows") or [])
        common = list(cfg.get("common") or [])
        named = {str(r["variant"]) for r in plan if r.get("variant")}
        skip = {str(v) for r in (*plan, *common) for v in (r.get("skip") or [])}
        extra = [{"variant": name} for name, v in known.items()
                 if v.reform == key and name not in named and name not in skip]  # fmt: skip
        # Listed rows first, then the other variants of the family, then the common rows.
        for spec in (*plan, *extra, *common):
            if spec.get("states") and state not in spec["states"]:
                continue
            row = _one_row(
                spec, key, o, b, hidden, bench, markets, known, kinds, rules, go, scope, corr
            )
            if row is not None:
                rows.append(row)
                say(f"{state} {key}: {row['id']} {row['status']}")
        added_ids, added_sources = _ids(o.added)
        reforms[key] = {
            "reform": key,
            "title": o.title,
            "headline": {
                **head,
                "hidden": hidden,
                "scope": _words(scope.get("scope") or ""),
                "scope_text": _words(scope.get("text") or ""),
                "what": "added units authorized by building permit, 2027-2031, state",
                "ref": f"reform_results.{key}.added",
                "assumption_count": len(added_ids),
                "source_ids": added_sources,
            },
            "rows": rows,
            "summary": _reform_summary(o.title, head, rows, hidden),
        }
    return {
        "version": VERSION,
        "state": state,
        "seed": seed,
        "draws": n,
        "synthetic": bool(getattr(bench, "synthetic", False)),
        "hashes": {k: str(getattr(bench.hashes, k, "")) for k in ("code", "data", "assumptions")},
        "formula": _words(cfg.get("formula")),
        "rule_id": str(cfg.get("id", "alternatives.settings")),
        "statuses": dict(cfg.get("statuses") or {}),
        "reforms": reforms,
    }


def _one_row(
    spec: Mapping[str, Any],
    key: str,
    o: Any,
    b: np.ndarray,
    hidden: bool,
    bench: Any,
    markets: Mapping[str, Any],
    known: Mapping[str, Any],
    kinds: Mapping[str, Any],
    rules: Mapping[str, Any],
    go: Callable[..., Any],
    scope: Mapping[str, Any],
    corr: Any = None,
) -> dict[str, Any] | None:
    """One row of the table, or ``None`` when it does not apply (comparable = headline)."""
    if spec.get("variant"):
        name = str(spec["variant"])
        v = known.get(name)
        base = {"id": name, "kind": "variant",
                "title": v.title if v else _words(spec.get("title") or name),
                "reason": _first_sentence(v.question) if v else ""}  # fmt: skip
        if v is None:
            return _row_absent(base, spec.get("absent") or "Not built: no variant of this name.")
        try:
            res = go({key: {"variant": name}})
        except (ValueError, KeyError, FileNotFoundError, RuntimeError) as err:
            return _row_absent(base, f"Does not run for this state: {_words(err)[:160]}")
        vo = res.reforms[key]
        nums = paired_row(vo.state_draws, b, hidden=hidden)
        a, s = _beyond(o.added, vo.added)
        ids = sorted({f"variants.{name}", *v.overrides.keys(), *v.overrides.values(), *a})
        return _row_ok(base, {**nums, "assumption_ids": ids, "source_ids": s})
    kind = str(spec.get("kind", ""))
    if kind == "planned":
        base = {"id": str(spec.get("id", "planned")), "kind": "planned",
                "title": _words(spec.get("title")), "reason": ""}  # fmt: skip
        return _row_absent(base, spec.get("absent") or "Not built.")
    if kind == "capacity":
        words = kinds.get("capacity") or {}
        base = {"id": "capacity_screen", "kind": kind, "title": _words(words.get("title")),
                "reason": _words(words.get("reason"))}  # fmt: skip
        cap = getattr(bench, "capacity", None)
        draws = None if cap is None else (cap.state_draws or {}).get(key)
        if draws is None:
            return _row_absent(base, "Not built: the capacity screen gave no draws here.")
        nums = paired_row(draws, b, hidden=hidden)
        recs = (cap.reforms or {}).get(key) or {}
        lo, hi = recs.get("screened_disjoint"), recs.get("screened_nested")
        if lo is not None and hi is not None and lo.p50 is not None and hi.p50 is not None:
            ends = sorted((float(lo.p50), float(hi.p50)))
            nums["bounds"] = {
                "low_p50": ends[0],
                "high_p50": ends[1],
                "text": f"joint bounds, middle {_n(ends[0])} to {_n(ends[1])}",
            }
        a, s = _beyond(o.added, recs.get("screened"), lo, hi)
        return _row_ok(base, {**nums, "assumption_ids": a, "source_ids": s})
    if kind == "market":
        mode = str(spec.get("mode", "choice"))
        words = kinds.get("market_choice" if mode == "choice" else "market_capacity") or {}
        base = {
            "id": f"market_{mode.replace('+', '_')}",
            "kind": kind,
            "title": _words(words.get("title")),
            "reason": _words(words.get("reason")),
        }
        m = markets.get(mode)
        if m is None or isinstance(m, Exception):
            why = _words(m)[:160] if isinstance(m, Exception) else "no market result"
            return _row_absent(base, f"Not built for this state: {why}")
        case = "choice" if mode == "choice" else "capacity"
        draws = (getattr(m, "draws", {}) or {}).get(f"parts.{case}.{key}")
        if draws is None:
            return _row_absent(base, "Not built: the package option gave no part for this reform.")
        nums = paired_row(draws, b, hidden=hidden)
        rec = ((getattr(m, "parts", {}) or {}).get(case) or {}).get(key)
        a, s = _beyond(o.added, rec)
        # The part's record names every input of the five-reform package; the row keeps the
        # market option's own entries (the package run's record holds the rest).
        mode_id = "market.modes.choice" if mode == "choice" else "market.modes.choice_capacity"
        a = sorted({mode_id, *(i for i in a if i.startswith("market."))})
        return _row_ok(base, {**nums, "assumption_ids": a, "source_ids": s})
    if kind == "correlated":
        words = kinds.get("correlated") or {}
        base = {"id": "correlated_stress", "kind": kind, "title": _words(words.get("title")),
                "reason": _words(words.get("reason"))}  # fmt: skip
        if corr is None or isinstance(corr, Exception):
            why = _words(corr)[:160] if isinstance(corr, Exception) else "no correlated run"
            return _row_absent(base, f"Not built for this state: {why}")
        co = corr.reforms.get(key)
        if co is None:
            return _row_absent(base, "Not built: the correlated run gave no draws here.")
        a, s = _beyond(o.added, co.added)
        a = sorted({"uncertainty_structure.correlated", *a})
        return _row_ok(base, {**paired_row(co.state_draws, b, hidden=hidden),
                              "assumption_ids": a, "source_ids": s})  # fmt: skip
    if kind == "comparable":
        if scope.get("is_comparable", True):
            return None
        words = kinds.get("comparable") or {}
        base = {"id": "census_comparable", "kind": kind, "title": _words(words.get("title")),
                "reason": _words(words.get("reason"))}  # fmt: skip
        comp = _comparable_draws(o, rules)
        rec = (o.accounting.records or {}).get("comparable") if o.accounting else None
        if (
            comp is None
            or rec is None
            or not np.isclose(float(np.percentile(comp, 50)), float(rec.p50), rtol=1e-9, atol=1e-6)
        ):
            return _row_absent(base, "Not built: the work-type draws are not available.")
        a, s = _beyond(o.added, rec)
        return _row_ok(base, {**paired_row(comp, b, hidden=hidden),
                              "assumption_ids": a, "source_ids": s})  # fmt: skip
    return None


def _correlated_run(ctx: Any, keys: Sequence[str], seed: int, draws: int, cfg: Mapping) -> Any:
    """The correlated sensitivity run of the five reforms, or the error that stopped it."""
    if not any(r.get("kind") == "correlated" for r in cfg.get("common") or []):
        return None
    try:
        from visionhousing.engine import correlated

        settings = correlated.load_settings()
        result, _factor = correlated.run_scenario(
            ctx, "correlated", list(keys), seed=seed, draws=draws, settings=settings
        )
    except (ImportError, ValueError, KeyError, RuntimeError, FileNotFoundError) as err:
        return err
    return result


def _reform_summary(
    title: str, head: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], hidden: bool
) -> dict[str, Any]:
    """The one-line summary the deck shows: headline, how many alternatives, widest swing."""
    built = [r for r in rows if r["status"] == "built"]
    widest = max(built, key=lambda r: abs(r["diff"]["p50"]), default=None)
    headline = (
        f"Measured contribution only: middle {head['mid']}"
        if hidden
        else f"{head['mid']} ({head['range']})"
    )
    moves = widest is not None and widest["diff"]["mid"] not in ("0", "+0")
    swing = (
        f"{widest['title']}: {widest['diff']['mid']} at the middle"
        if moves
        else "No alternative moves the middle"
    )
    return {
        "headline": headline,
        "built": len(built),
        "not_built": len(rows) - len(built),
        "count_text": f"{len(built)} built, {len(rows) - len(built)} not built",
        "widest": widest["id"] if moves else None,
        "widest_text": swing,
    }


# ------------------------------------------------------------------ cache and views


def _clean(value: Any) -> Any:
    from visionhousing.api.comparison_cache import _clean as clean

    return clean(value)


def payload(
    ctx: Any,
    *,
    seed: int = 0,
    draws: int | None = None,
    compute: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """The state's table: from the disk cache, or computed and kept.

    Returns ``None`` when it is not on disk and ``compute`` is false.
    """
    from visionhousing.api.comparison_cache import default_cache, fingerprint
    from visionhousing.ledger import DEFAULT_DRAWS

    n = int(draws or DEFAULT_DRAWS)
    cache = default_cache()
    shared = fingerprint(ctx, seed=seed, draws=n) if cache is not None else ""
    if cache is not None:
        hit = cache.load(ctx.state, shared, PART)
        if hit is not None:
            return hit["value"]
    if not compute:
        return None
    body = _clean(build_alternatives(ctx, seed=seed, draws=n, progress=progress))
    if cache is not None:
        cache.save(ctx.state, shared, PART, {"state": ctx.state, "seed": seed, "draws": n}, body)
    return body


def reform_view(body: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """One reform's part of the table, with the shared fields, or ``None``."""
    part = (body.get("reforms") or {}).get(key)
    if part is None:
        return None
    shared = {k: body[k] for k in ("version", "state", "seed", "draws", "formula", "rule_id",
                                   "statuses") if k in body}  # fmt: skip
    return {**shared, **part}


def hearing_rows(view: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The hearing brief's appendix: the headline line and one short row per alternative."""
    if not view:
        return None
    head = view["headline"]
    words = view.get("statuses") or {}
    rows = []
    for r in view.get("rows") or []:
        if r["status"] == "built":
            rows.append([r["title"], f"{r['added']['mid']} ({r['added']['range']})",
                         f"{r['diff']['mid']} ({r['diff']['range']})",
                         words.get("built", "Built")])  # fmt: skip
        else:
            rows.append([r["title"], "-", "-", words.get(r["status"], r["status"])])
    return {
        "headline": f"Benchmark: {view['summary']['headline']}; "
        + (head.get("scope_text") or head.get("what") or ""),
        "rows": rows,
        "note": view.get("formula", ""),
    }


STATIC_DROP = ("p10", "p90", "se", "low_p50", "high_p50")
"""Numeric fields the static file leaves out (the screens read the written text and the
P50s; the ids and sources stay, so every number is still traceable)."""


def compact(value: Any) -> Any:
    """The table without :data:`STATIC_DROP` fields, for the static site's size budget."""
    if isinstance(value, Mapping):
        return {k: compact(v) for k, v in value.items() if k not in STATIC_DROP}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def write_static(body: Mapping[str, Any], state_dir: Path, *, small: bool = True) -> int:
    """Save the table as ``state_dir/alternatives.json``; mark ``alternatives: true`` in the index.

    With ``small`` (the default) the P10 / P90 numbers and standard errors are left out
    (their text stays): about 8-9 KB gzip per state instead of 10-12 KB.

    Returns:
        The file's size in bytes.
    """
    state_dir = Path(state_dir)
    data = _clean(dict(body))
    if small:
        data = compact(data)
    text = json.dumps(data, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    (state_dir / STATIC_FILE).write_text(text, encoding="utf-8")
    index = state_dir / "index.json"
    if index.is_file():
        idx = json.loads(index.read_text(encoding="utf-8"))
        idx["alternatives"] = True
        index.write_text(json.dumps(idx, indent=1), encoding="utf-8")
    return len(text.encode("utf-8"))


# ------------------------------------------------------------------ the address


def register(app: Any, *, api: str, error: Any, context: Any, timed: Any) -> None:
    """Add ``GET {api}/states/{st}/alternatives`` to ``app`` (before its catch-all route)."""
    from visionhousing.api.models import LIMITS
    from visionhousing.api.runner import Runner as JobKey
    from visionhousing.engine.context import TESTLAND
    from visionhousing.ledger import DEFAULT_DRAWS

    @app.get(f"{api}/states/{{st}}/alternatives")
    def alternatives(st: str, request: Request) -> Response:
        """Each benchmark reform's headline and its named alternatives (one reform with r=)."""
        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        params = dict(request.query_params)
        unknown = sorted(set(params) - {"r", "seed", "draws"})
        if unknown:
            raise error(400, "invalid_query", f"unknown query fields {unknown[:5]}")
        try:
            seed = int(params.get("seed", 0))
            draws = int(params.get("draws", DEFAULT_DRAWS))
        except ValueError:
            raise error(400, "invalid_query", "seed and draws must be whole numbers") from None
        if seed < 0 or not 1 <= draws <= LIMITS["draws"]:
            raise error(400, "invalid_query", f"seed >= 0 and 1 <= draws <= {LIMITS['draws']}")
        key = params.get("r", "")
        if key and key not in BENCHMARKS:
            raise error(400, "invalid_reform", "r must be one of the five benchmark reforms")
        ctx = context(st)
        if ctx.state != TESTLAND and (seed != 0 or draws != DEFAULT_DRAWS):
            raise error(400, "default_sampling_only",
                        f"this address answers only the default seed (0) and draws "
                        f"({DEFAULT_DRAWS:,})")  # fmt: skip
        job = JobKey.key(PART, ctx.state, ctx.data_hash, ctx.calibration_hash, ctx.ledger.hash(),
                         seed, draws)  # fmt: skip
        body = timed(job, lambda: payload(ctx, seed=seed, draws=draws))
        if key:
            view = reform_view(body, key)
            if view is None:
                raise error(404, "reform_not_found", f"no benchmark {key} for {ctx.state}")
            out: Any = view
        else:
            out = body
        data = json.dumps(_clean(out), allow_nan=False, separators=(",", ":"))
        return Response(data.encode(), media_type="application/json")


# ------------------------------------------------------------------ command line


def main(argv: Iterable[str] | None = None) -> int:
    """Compute (or read back) a state's table, print its summary, optionally write it."""
    from visionhousing.engine.context import load_context

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state", default="PA", help="state code (default PA)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--draws", type=int, default=None)
    p.add_argument("--out", type=Path, default=None, help="also write the table as JSON here")
    p.add_argument("--static", type=Path, default=None,
                   help="also save ST/alternatives.json into this static data folder")  # fmt: skip
    args = p.parse_args(list(argv) if argv is not None else None)
    try:
        ctx = load_context(str(args.state).upper())
        body = payload(ctx, seed=args.seed, draws=args.draws,
                       progress=lambda m: print(m, file=sys.stderr))  # fmt: skip
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as err:
        print(f"alternatives: {err}", file=sys.stderr)
        return 1
    if body is None:
        return 1
    for key, part in body["reforms"].items():
        s = part["summary"]
        print(f"{body['state']} {key}: {s['headline']}; {s['count_text']}; {s['widest_text']}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(body, allow_nan=False, separators=(",", ":")),
                            encoding="utf-8")  # fmt: skip
        print(f"alternatives: wrote {args.out}")
    if args.static is not None:
        size = write_static(body, Path(args.static) / body["state"])
        print(f"alternatives: saved {body['state']}/{STATIC_FILE} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
