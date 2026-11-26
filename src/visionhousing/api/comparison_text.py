"""Text forms for the comparisons reply, so the browser never writes a number.

Plain-language summary
----------------------
``engine.comparisons`` returns numbers at full precision: records with P10 /
P50 / P90, shares, ratios and target rows. The screens show text only. This
module adds the text form beside each number, written by the project's one
number writer (``explain.numbers``):

- every range (a dict with numeric ``p10``, ``p50``, ``p90``) gets ``shown``
  (each of the three written) and ``likely`` ("P10 to P90");
- each part gets a one-line ``headline`` written by rule from its own numbers;
- target rows, adoption rows, matrix axes, ablation measures and ranking pairs
  get the words for each value (``text``, ``homes_text``, ``measures_text``,
  ``share_text``...).

Nothing is computed beyond rounding for display, choosing the smallest and
largest cell of the matrix, and counting cases; every value written comes from
the reply itself. The function returns a new dict; the input is not changed.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from visionhousing.explain.numbers import SENTENCE_SIG, TABLE_SIG, count, num, show

__all__ = ["comparison_texts", "homes_text"]

ZERO_HOMES = 0.005
"""Below this many homes a value is written "0" (float noise from paired differences)."""


def _finite(v: Any) -> bool:
    return (
        isinstance(v, int | float) and not isinstance(v, bool) and v == v and abs(v) != float("inf")
    )


def homes_text(value: float, sig: int = TABLE_SIG) -> str:
    """A count of homes as text ("1,230", "0.59", "0"), with a true minus sign."""
    if not _finite(value) or abs(value) < ZERO_HOMES:
        return "0"
    return count(float(value), sig)


def _signed(text: str) -> str:
    return text if text.startswith("\u2212") or text == "0" else f"+{text}"


def _pct(share: Any, sig: int = SENTENCE_SIG, signed: bool = False) -> str:
    """A share (0.031) as a percent ("3.1%"); "not defined" when missing."""
    if not _finite(share):
        return "not defined"
    t = show(float(share), "share", sig)
    if t in ("0%", "\u22120%"):
        t = "0%"
    return _signed(t) if signed else t


def _value(v: float, unit: str, sig: int = TABLE_SIG) -> str:
    if unit in ("units", "homes", ""):
        return homes_text(v, sig)
    if unit == "ratio":
        return _pct(v, SENTENCE_SIG, signed=True)
    return show(float(v), unit, sig)


@lru_cache(maxsize=1)
def _ledger() -> Any:
    from visionhousing.ledger import load_ledger

    return load_ledger()


def _param_value(pid: str, v: Any) -> str:
    """A ledger parameter's value with the ledger's unit ("1.0% a year", "$250/sq ft")."""
    if not _finite(v):
        return ""
    try:
        unit = _ledger()[pid].unit
    except Exception:  # an unknown id: the bare number
        unit = ""
    return show(float(v), unit, TABLE_SIG)


def _ranges(node: Any, unit: str = "units") -> None:
    """Add ``shown`` and ``likely`` to every P10 / P50 / P90 dict under ``node``."""
    if isinstance(node, list):
        for x in node:
            _ranges(x, unit)
        return
    if not isinstance(node, dict):
        return
    u = node.get("unit", unit) if isinstance(node.get("unit"), str) else unit
    if all(_finite(node.get(k)) for k in ("p10", "p50", "p90")):
        shown = {k: _value(node[k], u) for k in ("p10", "p50", "p90")}
        node.setdefault("shown", shown)
        node.setdefault("likely", f"{shown['p10']} to {shown['p90']}")
    for k, v in node.items():
        if k in ("shown", "options", "overrides"):
            continue
        _ranges(v, "ratio" if k == "ratio" else u)


def _range_words(r: dict[str, Any] | None) -> str:
    """The words "about 84 homes (likely range 34 to 174)" from a range with ``shown``."""
    if not r or "shown" not in r:
        return "not available"
    s = r["shown"]
    return f"about {s['p50']} homes (likely range {s['p10']} to {s['p90']})"


# ------------------------------------------------------------------ interactions


def _interactions(i: dict[str, Any]) -> None:
    st = i.get("state") or {}
    solo, pack, net = st.get("solo_sum"), st.get("package"), st.get("net_interaction")
    if solo and pack and net:
        i["headline"] = (
            f"The five reforms run alone add {_range_words(solo)} in 2027-2031; together they add "
            f"{_range_words(pack)}. The difference, worked out in each simulated future, is "
            f"{_signed(net['shown']['p50'])} homes (likely range {net['shown']['p10']} to "
            f"{net['shown']['p90']})."
        )
    # Where each bar sits in the drawing (homes): a start or end bar from 0 to its P50,
    # a step from where the previous bar ended. Drawing positions only; the step P50s
    # need not add up to the package's P50 (the reply's note says why).
    at = 0.0
    for step in (i.get("waterfall") or {}).get("steps", []):
        r = step.get("record") or {}
        if "shown" in r:
            step["signed"] = (
                _signed(r["shown"]["p50"]) if step.get("kind") == "step" else r["shown"]["p50"]
            )
        if _finite(r.get("p50")):
            if step.get("kind") == "step":
                step["bar"] = {"from": at, "to": at + r["p50"]}
            else:
                step["bar"] = {"from": 0.0, "to": r["p50"]}
            at = step["bar"]["to"]


# ------------------------------------------------------------------ dials


def _axis_value(dial: dict[str, Any], x: float) -> str:
    unit = str(dial.get("unit", ""))
    if unit.startswith("share"):
        return show(float(x), "share", SENTENCE_SIG)
    if unit in ("rule", ""):
        return num(float(x), SENTENCE_SIG)
    return f"{num(float(x), SENTENCE_SIG)} {unit}"


def _target_text(dial: dict[str, Any], cell: dict[str, Any], labels: dict[str, str]) -> str:
    status = cell.get("status")
    lab = labels.get(cell.get("level") or "", "")
    if status == "out_of_reach":
        return "out of reach"
    if status == "at_lowest":
        return f"already at the least generous level ({lab})"
    if status == "between" and _finite(cell.get("x")):
        if dial.get("unit") == "rule":
            return f"reached at {lab}"
        return f"about {_axis_value(dial, cell['x'])} (reached by {lab})"
    return "not available"


def _dials(d: dict[str, Any]) -> None:
    for dial in (d.get("dials") or {}).values():
        labels = {lv["key"]: lv.get("label", lv["key"]) for lv in dial.get("levels", [])}
        bench = next((lv for lv in dial.get("levels", []) if lv.get("benchmark")), None)
        if bench and "shown" in (bench.get("record") or {}):
            dial["headline"] = (
                f"At the benchmark ({str(bench['label']).replace(' (benchmark)', '')}): "
                f"{_range_words(bench['record'])} statewide in 2027-2031."
            )
        for lv in dial.get("levels", []):
            if _finite(lv.get("x")):
                lv["x_text"] = (
                    _axis_value(dial, lv["x"])
                    if dial.get("unit") != "rule"
                    else lv.get("label", "")
                )
        es = dial.get("each_step") or {}
        if _finite(es.get("homes")):
            es["homes_text"] = homes_text(es["homes"], SENTENCE_SIG)
        for step in dial.get("steps", []):
            if _finite(step.get("share_of_draws_not_lower")):
                step["share_not_lower_text"] = _pct(step["share_of_draws_not_lower"])
        for row in (dial.get("targets") or {}).get("rows", []):
            if _finite(row.get("homes")):
                row["homes_text"] = homes_text(row["homes"], SENTENCE_SIG)
            for k in ("p50", "p10"):
                if isinstance(row.get(k), dict):
                    row[k]["text"] = _target_text(dial, row[k], labels)
    a = d.get("adoption")
    if a:
        ceil = a.get("ceiling") or {}
        if _finite(ceil.get("p50")) and _finite(ceil.get("p10")):
            ceil["shown"] = {"p10": homes_text(ceil["p10"]), "p50": homes_text(ceil["p50"])}
        if a.get("range") and len(a["range"]) == 2:
            lo, hi = (_param_value(a.get("parameter", ""), v) for v in a["range"])
            a["range_text"] = f"{lo} to {hi}"
        if _finite(a.get("central_rate")):
            a["central_text"] = _param_value(a.get("parameter", ""), a["central_rate"])
        for row in a.get("rows", []):
            if not _finite(row.get("homes")):
                continue
            row["homes_text"] = homes_text(row["homes"], SENTENCE_SIG)
            for k, futures in (("p50", "in the middle future"), ("p10", "in 9 of 10 futures")):
                cell = row.get(k)
                if not isinstance(cell, dict):
                    continue
                if _finite(cell.get("rate")):
                    cell.setdefault("rate_text", _pct(cell["rate"]))
                if cell.get("status") == "out_of_reach" and not cell.get("sentence"):
                    top = ceil.get("shown", {}).get(k)
                    tail = f" (the most it could reach is about {top} ADUs)" if top else ""
                    cell["sentence"] = (
                        f"{row['homes_text']} ADUs in five years ({futures}) is out of reach: "
                        f"even if most feasible lots built one, it falls short{tail}."
                    )


# ------------------------------------------------------------------ matrix


def _matrix(m: dict[str, Any]) -> None:
    for axis in ("rows", "columns"):
        ax = m.get(axis) or {}
        ax["multipliers_text"] = [
            f"\u00d7{num(float(v), SENTENCE_SIG)}" for v in ax.get("multipliers", [])
        ]
        if _finite(ax.get("central")):
            ax["central_text"] = _param_value(ax.get("parameter", ""), ax["central"])
    cells = [c for row in m.get("cells", []) for c in row if isinstance(c, dict) and "shown" in c]
    bench = next((c for c in cells if c.get("benchmark")), None)
    if cells:
        lo_v = min(c["p50"] for c in cells)
        hi_v = max(c["p50"] for c in cells)
        for c in cells:  # shade class 1 (fewest homes) to 6 (most), for the heat grid only
            c["tone"] = 1 + int(5 * (c["p50"] - lo_v) / (hi_v - lo_v)) if hi_v > lo_v else 1
    if bench and cells:
        lo = min(cells, key=lambda c: c["p50"])
        hi = max(cells, key=lambda c: c["p50"])
        rt = (m.get("rows") or {}).get("multipliers_text") or [""]
        ct = (m.get("columns") or {}).get("multipliers_text") or [""]
        m["headline"] = (
            f"At the ledger's take-up and cost, ADUs add {_range_words(bench)}. Across the grid "
            f"(take-up {rt[0]} to {rt[-1]}, cost {ct[0]} to {ct[-1]}) "
            f"the middle runs from {lo['shown']['p50']} to {hi['shown']['p50']} homes."
        )


# ------------------------------------------------------------------ ablation


def _measures(r: dict[str, Any]) -> dict[str, str]:
    def plain(v: Any, fmt: str) -> str:
        if not _finite(v):
            return "not defined"
        if fmt == "times":
            return f"{num(float(v), TABLE_SIG)}\u00d7"
        if fmt == "rho":
            return f"{float(v):.2f}"
        return _pct(v, SENTENCE_SIG)

    return {
        "middle_shift": _pct(r.get("middle_shift"), SENTENCE_SIG, signed=True),
        "range_widening": plain(r.get("range_widening"), "times"),
        "rank_agreement": plain(r.get("rank_agreement"), "rho"),
        "coverage": plain(r.get("coverage"), "pct"),
    }


def _ablation(a: dict[str, Any]) -> None:
    titles = a.get("titles") or {}
    tiers = a.get("tiers") or {}
    for t in tiers.values():
        for r in (t.get("reforms") or {}).values():
            r["measures_text"] = _measures(r)
    for row in (a.get("single_input") or {}).get("rows", []):
        row["score_text"] = _pct(row.get("score"), SENTENCE_SIG)
        row["widening_text"] = (
            f"{num(float(row['widening']), TABLE_SIG)}\u00d7"
            if _finite(row.get("widening"))
            else "not defined"
        )
        for r in (row.get("reforms") or {}).values():
            r["measures_text"] = _measures(r)
    rows = (a.get("single_input") or {}).get("rows") or []
    if rows:
        top = rows[0]
        on = top.get("ranked_on", "package")
        what = "package of all reforms" if on == "package" else on
        a["single_input"]["sentence"] = (
            f"Hidden one at a time, {top.get('title', top.get('input'))} hurts most: "
            f"the middle estimate of the {what} "
            f"moves by {top['score_text']} (paired, draw by draw), and its likely range is "
            f"{top['widening_text']} as wide."
        )
    lowest = min(tiers, key=lambda k: int(k)) if tiers else None
    pick = (tiers.get(lowest, {}).get("reforms") or {}).get("adu") if lowest is not None else None
    if pick and pick.get("sentence"):
        a["headline"] = pick["sentence"]
    prop = a.get("property") or {}
    if "holds" in prop:
        bad = [titles.get(k, k) for k, ok in (prop.get("never_narrower") or {}).items() if not ok]
        prop["sentence"] = (
            "With less data, no likely range got narrower."
            if prop["holds"]
            else "With less data, a likely range got narrower for: "
            + "; ".join(bad)
            + " (percentile noise can do this)."
        )


# ------------------------------------------------------------------ ranking


def _ranking(r: dict[str, Any]) -> None:
    cases = r.get("cases") or []
    if cases and isinstance(r.get("changed_cases"), int):
        r["headline"] = (
            f"The order of the reforms by middle estimate changed in {r['changed_cases']} of "
            f"{len(cases)} cases ({r.get('share_changed', '')}) when one key input moved "
            "to the end of its range."
        )
    for c in cases:
        c["value_text"] = _param_value(c.get("parameter", ""), c.get("value"))
        c["p50_text"] = {k: homes_text(v) for k, v in (c.get("p50") or {}).items() if _finite(v)}
    for inp in r.get("inputs", []):
        for f in inp.get("for", []):
            if _finite(f.get("rho")):
                f["rho_text"] = f"{float(f['rho']):.2f}".replace("-", "\u2212")
    for p in r.get("pairs", []):
        p["share_text"] = _pct(p.get("share_draws_higher_adds_more"))


def comparison_texts(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a comparisons reply with the text form of every number added.

    Args:
        payload: The reply of ``engine.comparisons`` (after ``_clean``).

    Returns:
        The same reply plus ``shown`` / ``likely`` on every range and the text
        fields listed in the module summary.
    """
    out = copy.deepcopy(payload)
    parts = ("interactions", "dials", "matrix", "ablation", "ranking", "market", "tod_factorial")
    for part in parts:
        if isinstance(out.get(part), dict):
            _ranges(out[part])
    if isinstance(out.get("interactions"), dict):
        _interactions(out["interactions"])
    if isinstance(out.get("dials"), dict):
        _dials(out["dials"])
    if isinstance(out.get("matrix"), dict):
        _matrix(out["matrix"])
    if isinstance(out.get("ablation"), dict):
        _ablation(out["ablation"])
    if isinstance(out.get("ranking"), dict):
        _ranking(out["ranking"])
    return out
