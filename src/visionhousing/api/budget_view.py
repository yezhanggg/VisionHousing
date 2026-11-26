"""The ADU budget-and-target grid as text: what the panel and the question box show.

Plain-language summary
----------------------
The engine's budget grid (:func:`visionhousing.scenarios.adu_budget.budget_grid`) holds
numbers: for each tested option (law change only, program only, both; every appropriation
and award size) the added ADU authorizations, the net public cost, the share of modeled
draws meeting each target, and so on. A screen must never write those numbers itself, so
this module writes every one of them as text once, here, on the server:

- :func:`grid_view` gives each cell a key (``arm|appropriation|award``), its words (from
  ``assumptions/cards.yaml``, ``budget_target``), and the shown text of every number:
  the five-year added homes (P10 / P50 / P90 and "likely" range), the net public cost, the
  upfront appropriation, the units moved in time, the owners served, and for each tested
  target the share of draws meeting it with its Monte Carlo standard error. It also names
  each cell's key lever (the law change or the program money) by comparing the cell's own
  middle values, and lists the tested values and the accepted-share choices as text.
- :func:`write_static` saves ``{grid, view}`` for the static site as
  ``ST/adu-budget-grid.json`` and marks ``adu_budget_grid: true`` in ``ST/index.json``
  (called by the static exporter; the browser shows "needs the local app" without it).

The question of which tested options meet a target within a budget is answered by
:func:`visionhousing.scenarios.adu_budget.grid_lookup` (and its browser twin
``gridLookup`` in ``web/js/lib/budget.js``, checked equal by a test). Neither interpolates.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["STATIC_FILE", "cell_key", "grid_view", "write_static"]

STATIC_FILE = "adu-budget-grid.json"
"""The static site's file of the grid and its text, inside the state's folder."""


def _plain(value: float) -> str:
    v = float(value)
    return str(int(v)) if v.is_integer() else repr(v)


def cell_key(cell: Mapping[str, Any]) -> str:
    """``arm|appropriation|award`` with plain numbers (``both|20000000|25000``).

    The browser twin (``cellKey`` in ``web/js/lib/budget.js``) writes the same key.
    """
    return f"{cell['arm']}|{_plain(cell['appropriation'])}|{_plain(cell['award'])}"


def _settings() -> dict[str, Any]:
    from visionhousing.api.cards import load_cards

    return dict(load_cards().get("budget_target") or {})


def _show(value: float | None, unit: str) -> str:
    from visionhousing.explain import show
    from visionhousing.explain.numbers import TABLE_SIG

    if value is None:
        return ""
    if unit in ("units", "awards"):
        return show(float(value), "", TABLE_SIG)
    return show(float(value), unit, TABLE_SIG)


def _range(rec: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A grid record (P10 / P50 / P90) with its shown text; ``None`` when absent."""
    if not isinstance(rec, Mapping) or rec.get("p50") is None:
        return None
    unit = str(rec.get("unit") or "")
    shown = {k: _show(rec.get(k), unit) for k in ("p10", "p50", "p90")}
    return {
        "name": rec.get("name", ""),
        "unit": unit,
        "p10": rec.get("p10"),
        "p50": rec.get("p50"),
        "p90": rec.get("p90"),
        "shown": shown,
        "likely": f"{shown['p10']} to {shown['p90']}",
        "formula": rec.get("formula", ""),
    }


def _moved_note(rec: Mapping[str, Any] | None) -> str:
    """What the sign of "taken from later years" means for this cell."""
    if not isinstance(rec, Mapping) or rec.get("p50") is None:
        return ""
    if float(rec["p50"]) < 0:
        return (
            "Negative: against no change this option has more applications after 2031, not "
            "fewer, so none are taken from later years in the middle estimate."
        )
    if float(rec["p50"]) == 0:
        return "None taken from later years in the middle estimate."
    return "Owners the money only speeds up: their sites are missing from the years after 2031."


def _money(value: float) -> str:
    return "none" if float(value) == 0 else _show(float(value), "$")


def _share(share: float, se: float, met: int, n: int) -> dict[str, Any]:
    from visionhousing.engine.readouts import share_percent

    pct = share_percent(float(share), int(n))
    se_text = f"{100.0 * float(se):.1f} percentage points"
    return {
        "share": float(share),
        "mc_se": float(se),
        "draws_met": int(met),
        "share_text": pct,
        "se_text": se_text,
        "draws_text": f"{int(met):,} of {int(n):,} draws",
        "text": f"{pct} of the modeled draws (Monte Carlo standard error {se_text})",
    }


def _key_lever(
    cell: Mapping[str, Any], law: Mapping[str, Any] | None, words: Mapping[str, str]
) -> dict[str, Any]:
    """The lever that carries more of the cell's middle added homes, and why."""
    arm = cell["arm"]
    if arm == "law_only":
        return {"id": "law", "word": words.get("law", "law"), "text": "No program money."}
    if arm == "finance_only":
        return {
            "id": "program",
            "word": words.get("program", "program"),
            "text": "Today's law: the program reaches only lots where an ADU is already legal.",
        }
    prog = _range(cell.get("program_added"))
    alone = _range((law or {}).get("added"))
    if prog is None or alone is None:
        return {"id": "", "word": "", "text": "Not in the grid."}
    lever = "law" if float(alone["p50"]) >= float(prog["p50"]) else "program"
    return {
        "id": lever,
        "word": words.get(lever, lever),
        "text": (
            f"The law change alone adds {alone['shown']['p50']} (middle); the program adds "
            f"{prog['shown']['p50']} on top of it (middle, same draws)."
        ),
    }


def grid_view(grid: Mapping[str, Any], cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Every number of the grid as shown text, with the panel's words.

    Args:
        grid: The grid (``budget_grid`` / ``grid_payload`` output).
        cfg: The ``budget_target`` block of ``assumptions/cards.yaml`` (default: read it).

    Returns:
        ``{"id", "scope", "options", "accept_levels", "flags", "reference", "cells",
        "notes", "uncapped_note"}``; each cell has its ``key`` and shown text.
    """
    cfg = dict(cfg if cfg is not None else _settings())
    s = grid["settings"]
    n = int(grid["draws"])
    arm_words = dict(cfg.get("arm_words") or {})
    lever_words = dict(cfg.get("lever_words") or {})
    targets = [float(t) for t in s["targets"]]
    law = next((c for c in grid["cells"] if c["arm"] == "law_only"), None)
    cells = []
    for c in grid["cells"]:
        per_target = {}
        for t in targets:
            k = f"{t:g}"
            m = c["target_met"][k]
            mono = grid.get("monotone", {}).get(f"{c['arm']}|{float(c['award']):g}|{k}")
            per_target[k] = {
                **_share(m["share"], m["mc_se"], m.get("draws_met", 0), n),
                "pareto": bool((c.get("pareto") or {}).get(k)),
                "monotone": mono,
            }
        cost_per = _range(c.get("cost_per_added_comparable"))
        cells.append(
            {
                "key": cell_key(c),
                "arm": c["arm"],
                "arm_word": arm_words.get(c["arm"], c["arm"]),
                "appropriation": float(c["appropriation"]),
                "award": float(c["award"]),
                "label": c.get("label", ""),
                "appropriation_text": _money(c["appropriation"]),
                "award_text": _money(c["award"]),
                "added": _range(c.get("added")),
                "comparable": _range(c.get("added_comparable")),
                "moved": _range(c.get("moved_from_later")),
                "moved_note": _moved_note(c.get("moved_from_later")),
                "net_cost": _range(c.get("net_public_cost")),
                "served": _range(c.get("applicants_served")),
                "windfall": _range(c.get("awards_to_owners_building_anyway")),
                "unused": _range(c.get("unused_funds")),
                "cost_per_comparable": cost_per,
                "zero_gain_text": _show(float(c.get("zero_gain_share", 0.0)), "share"),
                "program_added": _range(c.get("program_added")),
                "key_lever": _key_lever(c, law, lever_words),
                "targets": per_target,
            }
        )
    ref = grid.get("reference") or {}
    flags = dict(cfg.get("flag_words") or {})
    return {
        "id": str(cfg.get("id", "budget_target")),
        "grid_id": grid.get("id", ""),
        "state": grid.get("state", ""),
        "seed": grid.get("seed"),
        "draws": n,
        "draws_text": f"{n:,}",
        "variant": grid.get("variant", ""),
        "illustrative": bool(s.get("illustrative", True)),
        "scope": (
            "Additional ADU authorizations by building permit, 2027-2031, against no change "
            f"on the same {n:,} seeded draws (seed {grid.get('seed')}); take-up on a finite "
            "stock of sites (hazard method)."
        ),
        "options": {
            "targets": [{"value": t, "text": _show(t, "")} for t in targets],
            "budgets": [{"value": float(b), "text": _money(b)} for b in s["appropriations"]],
            "awards": [{"value": float(a), "text": _money(a)} for a in s["awards"]],
            "arms": [{"value": a, "text": arm_words.get(a, a)} for a in s["arms"]],
        },
        "accept_levels": [
            {"value": float(a), "text": f"{_show(float(a), 'share')} of the modeled draws"}
            for a in cfg.get("accept_levels") or []
        ],
        "flags": flags,
        "reference": {
            k: _range(ref.get(k))
            for k in ("benchmark_linear", "law_only_hazard", "method_difference")
        },
        "cells": cells,
        "assumption_ids": sorted(
            {a for c in grid["cells"] for a in ((c.get("added") or {}).get("assumption_ids") or [])}
        ),
        "notes": list(grid.get("notes") or []),
        "uncapped_note": " ".join(str(cfg.get("uncapped_note") or "").split()),
    }


def write_static(grid: Mapping[str, Any], state_dir: Path) -> int:
    """Save ``{grid, view}`` as ``state_dir/adu-budget-grid.json``; mark it in the index.

    Args:
        grid: The state's grid (``api.budget_grid.grid_payload``).
        state_dir: The static export's state folder (``web/public/data/PA``).

    Returns:
        The file's size in bytes.
    """
    from visionhousing.api.comparison_cache import _clean

    state_dir = Path(state_dir)
    body = _clean({"grid": grid, "view": grid_view(grid)})
    text = json.dumps(body, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    (state_dir / STATIC_FILE).write_text(text, encoding="utf-8")
    index = state_dir / "index.json"
    if index.is_file():
        idx = json.loads(index.read_text(encoding="utf-8"))
        idx["adu_budget_grid"] = True
        index.write_text(json.dumps(idx, indent=1), encoding="utf-8")
    return len(text.encode("utf-8"))
