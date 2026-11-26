"""Plausibility data: each reform's rate beside rates observed where similar reforms passed.

Plain-language summary
----------------------
"8,000 ADUs" means little until it is set beside what happened elsewhere. This
module turns a reform's result into a rate and lists the observed rates next
to it. It computes nothing in the browser: the interface only draws what this
returns.

**The model's rate** comes from the engine run's own records (the run the
interface already holds, or a saved run). For each measure in
``assumptions/plausibility.yaml``:

- ``per_1000_lots_year``: added units authorized 2027-2031 / the reform's
  eligible lots / 5 years x 1,000, worked out **in each Monte Carlo draw** (the
  same draw's added units and eligible lots, both read from the run's per-draw
  arrays), then P10 / P50 / P90 over the draws. It is never a percentile of one
  divided by a percentile of the other. This is the "homes per 1,000 eligible
  lots per year" rate. A saved run (``to_dict()``) keeps no draws, so it has no
  per-lot rate (``missing``).
- ``per_100k_residents_year``: the run's own record of added units per
  100,000 residents per year.

A reform that acts on cost rather than on lots (parking, fees) has no per-lot
rate; its rate is ``not_applicable`` with the reason, in the value-state words
of :mod:`visionhousing.intake.value_state`.

**Observed rates** are read from the evidence files, each point with its place,
period and caveat:

- ``assumptions/evidence/california_apr.yaml``: the California APR comparison
  records (rate after each reform; ADU homes permitted per 1,000 one-unit
  detached homes per year);
- ``assumptions/calibration/adu_rate_plausibility.yaml``: the lowest and
  highest statewide California year;
- ``assumptions/evidence.yaml``: findings whose unit is a per-100,000-residents
  rate, for places with the reform (the wording filter is in the settings);
- ``assumptions/evidence/states/``: comparison records whose observed result is
  in one of those units.

The same value from two files (the Pew rate is in both libraries) is kept once.

**The band** is the lowest to highest observed rate in the same measure. When
our likely range (P10 to P90) sits wholly below or above the band, or our P50
sits outside it, a sentence written by rule says so and gives the reason
stated for that reform and side in ``assumptions/plausibility.yaml``. With no
observed rate in a measure, the band is ``missing`` and the sentence says so.

The output is evidence for a reader, never an input to a run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import yaml

__all__ = ["SETTINGS_PATH", "load_settings", "observed_rates", "plausibility", "reform_rate"]

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "assumptions" / "plausibility.yaml"
"""The plausibility settings file."""


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Read ``assumptions/plausibility.yaml``."""
    path = Path(path) if path is not None else SETTINGS_PATH
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read(rel: str, root: Path) -> Any:
    path = root / rel
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _fmt(x: float | None) -> str:
    if x is None:
        return "n/a"
    ax = abs(x)
    if ax >= 10:
        return f"{x:,.0f}"
    if ax >= 1:
        return f"{x:.1f}"
    return f"{x:.2f}"


def _shown(rec: Mapping[str, Any], keys: Sequence[str]) -> dict[str, str]:
    """Text forms of a record's numbers, in the words the sentence uses (no rounding later)."""
    return {k: _fmt(rec.get(k)) for k in keys if rec.get(k) is not None}


def _axis(
    rate: Mapping[str, Any],
    band: Mapping[str, Any] | None,
    pts: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """The strip's ends: lowest and highest value it places, log scale past the set ratio.

    A linear strip starts at zero (or below, for a negative value); a log strip
    starts at its lowest value. The ratio is ``axis_log_ratio`` in the settings.
    """
    vals = [rate.get(q) for q in ("p10", "p50", "p90")]
    if band is not None:
        vals += [band.get("low"), band.get("high")]
    vals += [p.get("value") for p in pts]
    vals = [float(v) for v in vals if v is not None and np.isfinite(v)]
    if not vals:
        return {"lo": None, "hi": None, "log": False, "shown": {}}
    lo, hi = min(vals), max(vals)
    log = lo > 0 and hi / lo > float(settings.get("axis_log_ratio", 20))
    if not log:
        lo = min(0.0, lo)
    return {"lo": lo, "hi": hi, "log": bool(log), "shown": {"lo": _fmt(lo), "hi": _fmt(hi)}}


def _short(text: Any, limit: int = 320) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(". ", 1)[0]
    return cut + ("." if not cut.endswith(".") else "")


# --------------------------------------------------------------------------- the run


def _is_result(run: Any) -> bool:
    return hasattr(run, "to_dict") and hasattr(run, "reforms")


def _lookup(run: Any, reform: str, spec: str) -> Mapping[str, Any] | None:
    """One record of a reform (``added`` or ``records.<name>``) as a dict, or ``None``."""
    if _is_result(run):
        o = run.reforms.get(reform)
        if o is None:
            return None
        if spec == "added":
            return o.added.model_dump(mode="json")
        if spec.startswith("records."):
            r = o.records.get(spec.split(".", 1)[1])
            return None if r is None else r.model_dump(mode="json")
        return None
    from visionhousing.explain.ids import resolve

    try:
        node = resolve(run, f"reform_results.{reform}.{spec}")
    except KeyError:
        return None
    return node if isinstance(node, Mapping) else None


def _draw_pair(
    run: Any, reform: str, numerator: str, denominator: str
) -> tuple[np.ndarray, np.ndarray] | None:
    """Per-draw state totals of the numerator and the denominator, or ``None``.

    Read-only from the engine ``Result``: ``added`` is the reform's own
    per-draw state total (``ReformOutcome.state_draws``); ``records.<stage>``
    is the reform module's stage array of that name (draws x tracts), summed
    over tracts in each draw (the draws the record summarises). A saved run
    (``to_dict()``) keeps no draws, so it gives ``None``.
    """
    if not _is_result(run):
        return None
    o = run.reforms.get(reform)
    if o is None:
        return None

    def one(spec: str) -> np.ndarray | None:
        if spec == "added":
            return np.asarray(o.state_draws, dtype=float)
        if spec.startswith("records."):
            stages = getattr(getattr(o.result, "detail", None), "stages", None)
            arr = None if not isinstance(stages, Mapping) else stages.get(spec.split(".", 1)[1])
            return None if arr is None else np.asarray(arr, dtype=float).sum(axis=1)
        return None

    n, d = one(numerator), one(denominator)
    if n is None or d is None or n.shape != d.shape:
        return None
    return n, d


def _q(rec: Mapping[str, Any]) -> dict[str, float | None]:
    if rec.get("p50") is not None:
        return {q: rec.get(q) for q in ("p10", "p50", "p90")}
    v = rec.get("value")
    return {"p10": v, "p50": v, "p90": v}


def reform_rate(
    run: Any, reform: str, measure: str, settings: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The model's rate for one reform in one measure, from the run's records.

    Returns:
        ``{measure, label, p10, p50, p90, state, formula, inputs}``; ``state``
        is ``observed``, ``true_zero``, ``missing`` (the reform is not in the
        run) or ``not_applicable`` (no eligible lots, so no rate).
    """
    from visionhousing.intake.value_state import OBSERVED

    settings = settings if settings is not None else load_settings()
    m = settings["measures"][measure]
    num = _lookup(run, reform, m["numerator"])
    out: dict[str, Any] = {"measure": measure, "label": m["label"]}
    if num is None:
        return {**out, "p10": None, "p50": None, "p90": None, "state": "missing",
                "formula": f"no {m['numerator']} record for {reform} in this run"}  # fmt: skip
    years = int(settings.get("years", 5)) if m.get("per_year") else 1
    scale = float(m.get("scale", 1))
    nq = _q(num)
    inputs = [{"name": num.get("name"), "unit": num.get("unit"), **nq}]
    if m.get("denominator"):
        den = _lookup(run, reform, m["denominator"])
        if den is None or not _q(den)["p90"]:
            return {**out, "p10": None, "p50": None, "p90": None, "state": "not_applicable",
                    "formula": f"no eligible {m['denominator']} for {reform}: no rate"}  # fmt: skip
        none = {"p10": None, "p50": None, "p90": None}
        pair = _draw_pair(run, reform, m["numerator"], m["denominator"])
        if pair is None:
            why = (
                f"the rate is worked out draw by draw and this run keeps no per-draw "
                f"{m['numerator']} and {m['denominator']} arrays (a saved run keeps records "
                "only); run the scenario to get it"
            )
            return {**out, **none, "state": "missing", "formula": why}
        n_draws, d_draws = pair
        inputs.append({"name": den.get("name"), "unit": den.get("unit"), **_q(den)})
        ok = d_draws > 0
        per_draw = n_draws[ok] / d_draws[ok] / years * scale
        if per_draw.size == 0:
            why = f"no eligible {m['denominator']} for {reform} in any draw: no rate"
            return {**out, **none, "state": "not_applicable", "formula": why}
        p10, p50, p90 = np.percentile(per_draw, [10, 50, 90])
        rate = {"p10": float(p10), "p50": float(p50), "p90": float(p90)}
        formula = (
            f"{num.get('name')} / {den.get('name')} / {years} years x {scale:,.0f}, worked out "
            f"in each of {per_draw.size:,} draws (the same draw's added units and eligible "
            "lots), then P10 / P50 / P90 over the draws"
        )
    else:
        rate = {q: (None if v is None else v * scale) for q, v in nq.items()}
        formula = f"the run's record: {num.get('name')}"
    state = "true_zero" if (rate["p90"] or 0) == 0 and (rate["p10"] or 0) == 0 else OBSERVED
    rounded = {q: (None if v is None else round(v, 4)) for q, v in rate.items()}
    return {**out, **rounded, "state": state, "formula": formula, "inputs": inputs}


# --------------------------------------------------------------------------- observed


def _apr_points(cfg: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
    data = _read(cfg["file"], root) or {}
    out = []
    for r in data.get("records", []):
        o = r.get("outcome") or {}
        if o.get("measure") != cfg.get("unit_text"):
            continue
        caveat = " ".join([*(r.get("caveats") or [])[:2], _short(cfg.get("caveat_extra"))])
        common = {"source": cfg["file"], "id": r["id"], "measure": cfg["measure"],
                  "reform": cfg["reform"], "period": r.get("horizon"),
                  "identification": r.get("identification"), "caveat": caveat.strip(),
                  "what_changed": _short(r.get("what_changed"), 200)}  # fmt: skip
        where = str(r.get("where", ""))
        focal, _, peer = where.partition(" compared with ")
        peer = peer.split(" (")[0]
        if o.get("after") is not None:
            out.append({**common, "place": where, "value": float(o["after"])})
        if o.get("focal_after") is not None:
            out.append({**common, "place": f"{focal} (city with the local reform)",
                        "value": float(o["focal_after"])})  # fmt: skip
        if o.get("peer_after") is not None:
            out.append({**common, "place": f"{peer or where} (peer city, state law only)",
                        "value": float(o["peer_after"])})  # fmt: skip
    return out


def _plaus_points(cfg: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
    data = _read(cfg["file"], root) or {}
    ca = data.get("california") or {}
    years = ca.get("statewide_by_year") or {}
    if not years:
        return []
    limits = (data.get("provenance") or {}).get("limits") or []
    caveat = " ".join(_short(x, 200) for x in limits[1:3])
    out = []
    for pick in (min, max):
        y = pick(years, key=lambda k: years[k])
        out.append({"source": cfg["file"], "id": f"{data.get('id')}.statewide.{y}",
                    "measure": cfg["measure"], "reform": cfg["reform"],
                    "place": f"California statewide, {y}", "value": float(years[y]),
                    "period": str(y), "identification": "descriptive",
                    "caveat": caveat})  # fmt: skip
    return out


def _walk_findings(node: Any) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for key in ("findings", "after", "before"):
        for f in node.get(key) or []:
            if isinstance(f, Mapping) and "unit" in f and "value" in f:
                out.append(f)
    return out


def _library_points(cfg: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
    data = _read(cfg["file"], root) or {}
    units = cfg.get("units") or {}
    words = [w.lower() for w in cfg.get("include_measure_words") or []]
    out = []
    for eid, e in (data.get("entries") or {}).items():
        if not isinstance(e, Mapping):
            continue
        for f in _walk_findings(e):
            measure = units.get(str(f.get("unit")))
            text = str(f.get("measure", "")).lower()
            if measure is None or (words and not any(w in text for w in words)):
                continue
            out.append({"source": cfg["file"], "id": eid, "measure": measure,
                        "reform": e.get("reform"), "place": f"{e.get('place')}: {f.get('measure')}",
                        "value": float(f["value"]), "period": str(f.get("period")),
                        "identification": e.get("entry_type"),
                        "caveat": _short(e.get("comparability"))})  # fmt: skip
    return out


def _record_points(cfg: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
    folder = root / cfg["folder"]
    units = cfg.get("units") or {}
    out = []
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for r in data.get("records") or []:
            o = r.get("observed_result") or {}
            measure = units.get(str(o.get("unit")))
            if measure is None or o.get("value") is None:
                continue
            for reform in r.get("policy_types") or []:
                out.append({"source": f"{cfg['folder']}/{path.name}", "id": r["id"],
                            "measure": measure, "reform": reform,
                            "place": f"{r.get('jurisdiction')}: {o.get('unit')}",
                            "value": float(o["value"]), "period": r.get("horizon"),
                            "identification": r.get("identification"),
                            "verified": bool(r.get("verified")),
                            "caveat": " ".join(r.get("caveats") or [])[:400]})  # fmt: skip
    return out


def observed_rates(
    reform: str, settings: Mapping[str, Any] | None = None, root: Path | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Observed rates for one reform, by measure (same value from two files kept once)."""
    settings = settings if settings is not None else load_settings()
    root = root or ROOT
    readers = {
        "california_apr": _apr_points,
        "adu_rate_plausibility": _plaus_points,
        "evidence_library": _library_points,
        "comparison_records": _record_points,
    }
    points: list[dict[str, Any]] = []
    for name, cfg in (settings.get("observed") or {}).items():
        if name in readers:
            points += readers[name](cfg, root)
    out: dict[str, list[dict[str, Any]]] = {}
    seen: dict[tuple[str, float], dict[str, Any]] = {}
    for p in points:
        if p.get("reform") != reform:
            continue
        key = (p["measure"], round(p["value"], 3))
        if key in seen:
            seen[key].setdefault("also_in", []).append(f"{p['source']} ({p['id']})")
            continue
        seen[key] = p
        out.setdefault(p["measure"], []).append(p)
    for pts in out.values():
        pts.sort(key=lambda p: p["value"])
    return out


# --------------------------------------------------------------------------- the card data


def _position(rate: Mapping[str, Any], low: float, high: float) -> str:
    p10, p50, p90 = rate["p10"], rate["p50"], rate["p90"]
    if p90 < low:
        return "below"
    if p10 > high:
        return "above"
    if p50 < low:
        return "partly_below"
    if p50 > high:
        return "partly_above"
    return "inside"


def why_of(cfg: Mapping[str, Any], side: str) -> str:
    """The stated reason for a result below or above the observed band."""
    return str((cfg.get("outside_band") or {}).get(side, ""))


def _sentence(reform: str, rate: Mapping[str, Any], band: Mapping[str, Any] | None,
              cfg: Mapping[str, Any]) -> str:  # fmt: skip
    label = rate["label"]
    ours = f"{_fmt(rate['p10'])} to {_fmt(rate['p90'])} {label} (middle {_fmt(rate['p50'])})"
    span = f"{_fmt(rate['p10'])} to {_fmt(rate['p90'])}"
    mid = f"{_fmt(rate['p50'])} {label}"
    if rate.get("state") == "true_zero":
        zero = f"Our result is zero {label} in every likely case"
        if band is None:
            return zero + "; no observed rate in this unit was found to set it beside."
        return (
            f"{zero}, below all of the observed rates ({_fmt(band['low'])} to "
            f"{_fmt(band['high'])}). Why: " + _short(why_of(cfg, "below"))
        )
    if band is None:
        return (
            f"Our likely range is {ours}. No observed rate in this unit was found in the "
            "evidence files, so there is nothing to set it beside yet."
        )
    seen = (
        f"the observed rates ({_fmt(band['low'])} to {_fmt(band['high'])}, "
        f"{band['n']} observation{'s' if band['n'] != 1 else ''})"
    )
    why = cfg.get("outside_band") or {}
    pos = band["position"]
    if pos == "below":
        return f"Our likely range, {ours}, sits below all of {seen}. Why: " + _short(
            why.get("below")
        )
    if pos == "above":
        return f"Our likely range, {ours}, sits above all of {seen}. Why: " + _short(
            why.get("above")
        )
    if pos == "partly_below":
        return (
            f"Our middle estimate, {mid}, sits below {seen}, though the likely range "
            f"({span}) reaches into them. Why: " + _short(why.get("below"))
        )
    if pos == "partly_above":
        return (
            f"Our middle estimate, {mid}, sits above {seen}, though the likely range "
            f"({span}) reaches into them. Why: " + _short(why.get("above"))
        )
    return f"Our likely range, {ours}, sits within {seen}."


def plausibility(
    run: Any,
    reforms: Sequence[str] | None = None,
    settings: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """The plausibility card's data for one run.

    Args:
        run: An engine ``Result`` or its ``to_dict()`` (a saved run).
        reforms: Reform keys (default: every reform in the run that the
            settings list).
        settings: ``assumptions/plausibility.yaml`` (read when not given).
        root: Repository folder (for the evidence files).

    Returns:
        ``{state, seed, draws, hashes, reforms: {key: {measures: [{measure,
        label, model, band, observed, sentence}], note}}, settings_id}``.
    """
    settings = settings if settings is not None else load_settings()
    if _is_result(run):
        state, seed, draws = run.state, run.seed, run.draws
        hashes = dict(zip(("code", "data", "assumptions"), run.hashes.as_tuple(), strict=True))
        in_run = list(run.reform_ids)
    else:
        state, seed, draws = run.get("state"), run.get("seed"), run.get("draws")
        hashes = dict(run.get("hashes") or {})
        in_run = list((run.get("reform_results") or {}).keys())
    keys = list(reforms) if reforms is not None else in_run
    out: dict[str, Any] = {}
    for key in keys:
        cfg = (settings.get("reforms") or {}).get(key)
        if cfg is None:
            out[key] = {"measures": [], "state": "not_applicable",
                        "note": f"no plausibility settings for '{key}'"}  # fmt: skip
            continue
        if not cfg.get("measures"):
            out[key] = {"measures": [], "state": "not_applicable",
                        "note": " ".join(str(cfg.get("no_rate", "")).split())}  # fmt: skip
            continue
        obs = observed_rates(key, settings, root)
        rows = []
        for measure in cfg["measures"]:
            rate = reform_rate(run, key, measure, settings)
            pts = obs.get(measure, [])
            band = None
            if pts:
                low, high = pts[0]["value"], pts[-1]["value"]
                band = {"low": low, "high": high, "n": len(pts), "state": "observed",
                        "shown": {"low": _fmt(low), "high": _fmt(high)}}  # fmt: skip
            if band is not None and rate["state"] in ("observed", "true_zero"):
                band["position"] = _position(rate, band["low"], band["high"])
            elif band is not None:
                band["position"] = None
            if rate["state"] in ("observed", "true_zero"):
                sentence = _sentence(key, rate, band, cfg)
            else:
                sentence = f"No model rate: {rate['formula']}."
            rate = {**rate, "shown": _shown(rate, ("p10", "p50", "p90"))}
            pts = [{**p, "shown": _fmt(p["value"])} for p in pts]
            rows.append({"measure": measure, "label": rate["label"], "model": rate,
                         "band": band or {"state": "missing", "n": 0},
                         "observed": pts, "sentence": sentence,
                         "axis": _axis(rate, band, pts, settings)})  # fmt: skip
        out[key] = {"measures": rows, "state": "observed"}
    return {
        "state": state,
        "seed": seed,
        "draws": draws,
        "hashes": hashes,
        "settings_id": settings.get("id"),
        "reforms": out,
        "note": (
            "Evidence for a reader, never an input to a run. Observed rates come from other "
            "places and rules; each carries its caveat."
        ),
    }
