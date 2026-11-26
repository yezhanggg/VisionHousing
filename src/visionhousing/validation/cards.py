"""Validation cards: what each published coverage count refers to, with fuller scores.

Plain-language summary
----------------------
Screens and documents quote counts like "caught 8 of 8", "10 of 11" or
"6 of 11". Each is how often a range caught a real five-year permit total in
past years, but they come from different studies, start years, targets and
geographies. A **validation card** names all of that next to the count:

* the **study** and the command that produced it, and its pre-registered block;
* the **origin set** (the past start years scored), the lead and horizon;
* the **target** (five-year total of units authorized by building permits)
  and the **geography** (a state total, or every county of a state);
* the **release** (the data files and the date the count was made).

For the studies whose cases can be re-derived here (the baseline's measured
reliability, state and county ranges, and the state confidence check), the
card first rebuilds every scored case with the study's own building blocks and
checks it gets the same count (*parity*). Only then does it add:

* an **interval score** (Gneiting and Raftery): the range's width plus a
  penalty of 2 / alpha times the distance by which the real total fell outside
  it (alpha = 0.2 for a P10-P90 range), divided by the real total. Lower is
  better. A wide range that always catches the total, and a narrow one that
  often misses, both score badly;
* the **signed error** log(actual / centre), above zero when more homes were
  permitted than the centre said;
* the **error by size** (county size class; or states ordered by size);
* a **non-overlapping subset**: consecutive start years share four of their
  five target years, so "8 of 8" is closer to two independent checks than to
  eight. The card counts only start years five years apart, for every possible
  anchor;
* a **fold timeline**: for each start year, the years whose errors were used
  (train), the gap, the five years scored (evaluation), and the years the rule
  itself was chosen on (selection: the full record, which overlaps evaluation).

Every card also discloses that the rule was chosen on the full record and that
the contest describes counties with today's place types. The rules are in
``assumptions/validation_cards.yaml`` (block ``validation_card_scores``),
fingerprinted before any score was computed. The command
``visionhousing validate-cards`` writes
``assumptions/calibration/validation_cards.yaml``; the validation API returns
the cards of the state being viewed. Nothing here changes an estimate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

__all__ = [
    "BLOCK",
    "OUT_PATH",
    "SETTINGS_PATH",
    "build_cards",
    "cards_for",
    "case_metrics",
    "fold_timeline",
    "interval_score",
    "load_settings",
    "non_overlapping",
    "write_document",
]

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "assumptions" / "validation_cards.yaml"
OUT_PATH = ROOT / "assumptions" / "calibration" / "validation_cards.yaml"
BLOCK = "validation_card_scores"
"""The block's name in ``assumptions/preregistration.yaml``."""

HEADER = """\
# Calibration (validation): validation cards (generated; do not edit).
# Written by `visionhousing validate-cards`. Names the study, origins, target,
# geography and release behind every published coverage count, and adds an interval
# score, signed error, error by size, a non-overlapping subset and a fold timeline
# (block validation_card_scores in assumptions/validation_cards.yaml). Descriptive;
# changes no estimate. Method: docs/modules/validation_cards.md.
"""


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Read ``assumptions/validation_cards.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


# --------------------------------------------------------------------------- scores


def interval_score(
    low: np.ndarray, high: np.ndarray, actual: np.ndarray, alpha: float
) -> dict[str, np.ndarray]:
    """Gneiting-Raftery interval score of central (1 - alpha) ranges, relative to the actual.

    ``IS = (U - L) + 2/alpha * (L - y) * [y < L] + 2/alpha * (y - U) * [y > U]``,
    divided by ``y``. Returns the score and its two parts (width and miss penalty),
    one value per case.
    """
    lo, hi, y = (np.asarray(a, dtype=float) for a in (low, high, actual))
    width = (hi - lo) / y
    miss = (2.0 / alpha) * (np.maximum(lo - y, 0.0) + np.maximum(y - hi, 0.0)) / y
    return {"score": width + miss, "width": width, "miss": miss}


def _count(inside: np.ndarray) -> dict[str, Any]:
    n, k = int(inside.size), int(np.sum(inside))
    return {"cases": n, "covered": k, "coverage": (k / n) if n else None}


def non_overlapping(rows: pd.DataFrame, horizon: int) -> dict[str, Any]:
    """Coverage on start years spaced ``horizon`` apart, for every anchor.

    The primary anchor is the latest scored start year, stepping back by
    ``horizon``. Each other anchor (offset 1 .. horizon - 1 before the latest)
    gives another subset; the lowest and highest coverage across anchors are
    reported. Rows need ``origin`` and ``inside``.
    """
    if rows.empty:
        return {"primary": _count(np.array([], bool)), "anchors": [], "min": None, "max": None}
    latest = int(rows["origin"].max())
    anchors = []
    for off in range(horizon):
        start = latest - off
        keep = ((start - rows["origin"]) % horizon == 0) & (rows["origin"] <= start)
        sub = rows[keep]
        anchors.append(
            {
                "anchor": start,
                "origins": sorted({int(o) for o in sub["origin"]}),
                **_count(sub["inside"].to_numpy(dtype=bool)),
            }
        )
    covs = [a["coverage"] for a in anchors if a["coverage"] is not None]
    return {
        "primary": anchors[0],
        "anchors": anchors,
        "min": min(covs) if covs else None,
        "max": max(covs) if covs else None,
    }


def case_metrics(rows: pd.DataFrame, alpha: float, horizon: int) -> dict[str, Any]:
    """All scores for one card from its case rows.

    Rows need ``origin``, ``low``, ``high``, ``point`` and ``actual`` (and
    ``size`` for error by size). Coverage counts use every case, as the studies
    do. The relative scores (interval score, signed error, width) need an actual
    and a centre above zero; cases without (a county that permitted nothing in
    five years) are left out of those scores only and counted in
    ``excluded_from_relative_scores``.
    """
    rows = rows.copy()
    a, lo, hi = (rows[c].to_numpy(dtype=float) for c in ("actual", "low", "high"))
    rows["inside"] = (a >= lo) & (a <= hi)
    pos = (rows["actual"] > 0) & (rows["point"] > 0)
    rows["signed"] = np.nan
    rows["is"] = np.nan
    rows["is_width"] = np.nan
    rows["is_miss"] = np.nan
    if pos.any():
        p = rows[pos]
        isc = interval_score(p["low"], p["high"], p["actual"], alpha)
        rows.loc[pos, "signed"] = np.log(p["actual"] / p["point"]).to_numpy(dtype=float)
        rows.loc[pos, "is"] = isc["score"]
        rows.loc[pos, "is_width"] = isc["width"]
        rows.loc[pos, "is_miss"] = isc["miss"]

    def num(x: float) -> float | None:
        return None if not np.isfinite(x) else float(x)

    def block(g: pd.DataFrame) -> dict[str, Any]:
        if len(g) == 0:
            return {"cases": 0}
        y = g["actual"].to_numpy(dtype=float)
        gp = g[(g["actual"] > 0) & (g["point"] > 0)]
        return {
            **_count(g["inside"].to_numpy(dtype=bool)),
            "above": int((y > g["high"].to_numpy(dtype=float)).sum()),
            "below": int((y < g["low"].to_numpy(dtype=float)).sum()),
            "interval_score_mean": num(gp["is"].mean()) if len(gp) else None,
            "interval_score_median": num(gp["is"].median()) if len(gp) else None,
            "signed_log_error_median": num(gp["signed"].median()) if len(gp) else None,
            "signed_log_error_mean": num(gp["signed"].mean()) if len(gp) else None,
            "width_mean": (
                num(((gp["high"] - gp["low"]) / gp["point"]).mean()) if len(gp) else None
            ),
            "actual_mean": float(np.mean(y)),
            "excluded_from_relative_scores": int(len(g) - len(gp)),
        }

    out = block(rows)
    out["interval_score_parts"] = {
        "width_mean": num(rows["is_width"].mean()) if pos.any() else None,
        "miss_mean": num(rows["is_miss"].mean()) if pos.any() else None,
    }
    if "size" in rows.columns:
        out["by_size"] = {str(k): block(g) for k, g in rows.groupby("size", sort=False)}
    out["non_overlapping"] = non_overlapping(rows, horizon)
    return out


def fold_timeline(
    origins: Iterable[int], lead: int, horizon: int, first_year: int, last_year: int
) -> list[dict[str, Any]]:
    """Train, gap, evaluation and selection years for each start year (see the module doc)."""
    out = []
    for o in sorted({int(x) for x in origins}):
        ev = [o + lead, o + lead + horizon - 1]
        out.append(
            {
                "origin": o,
                "train": [first_year, o],
                "train_errors_from_origins_up_to": o - lead - horizon + 1,
                "gap": [o + 1, o + lead - 1] if lead > 1 else None,
                "evaluation": ev,
                "selection": [first_year, last_year],
                "selection_overlaps_evaluation": bool(first_year <= ev[1] and ev[0] <= last_year),
            }
        )
    return out


# --------------------------------------------------------------------------- re-derivation


def _baseline_rows(
    state: str, ledger: Any, rules: Mapping[str, Any], block: Mapping[str, Any]
) -> dict[str, Any]:
    """Re-derive the baseline-reliability cases of one state (state and county rows).

    Uses the same building blocks as :mod:`visionhousing.baseline.reliability`
    (read-only); the parity check compares the counts with the published file.
    """
    from visionhousing.baseline.backtest import (
        backtest_cases,
        backtest_origins,
        class_label,
        lead_years,
        leave_one_county_out,
        origin_size_classes,
        ratio_quantiles,
    )
    from visionhousing.baseline.calibrate import _state_inputs
    from visionhousing.baseline.contest import run_contest
    from visionhousing.baseline.panel import to_panel
    from visionhousing.baseline.project import (
        growth_widen_for,
        widen_growth_class,
        widen_small_sample,
    )
    from visionhousing.baseline.settings import settings_from_ledger

    settings = settings_from_ledger(ledger)
    table, info = _state_inputs(state, ledger, settings)
    contest = run_contest(table, ledger, rules, info)
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    horizon = settings.horizon
    origins = backtest_origins(panel.years, lead, horizon, settings.min_history)
    classes = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    rule = contest.rule
    cases = backtest_cases(rule, panel.years, panel.units, origins, lead, horizon, contest.info)
    scored = leave_one_county_out(cases, classes, lead, horizon)
    county = pd.DataFrame(
        {
            "origin": scored["origin"].astype(int),
            "area": scored["place"].astype(int),
            "low": scored["low"].astype(float),
            "high": scored["high"].astype(float),
            "point": scored["projected"].astype(float),
            "actual": scored["actual"].astype(float),
            "size": [class_label(int(k), settings.n_classes) for k in scored["size_class"]],
        }
    )
    # State range, as in baseline.reliability.state_reliability.
    keep = bool(contest.correction.get("keep", True))
    growth = growth_widen_for(state, ledger, rules, int(panel.years[-1]))
    levels = tuple(float(x) for x in block.get("state_quantiles", (0.1, 0.5, 0.9)))
    need = int(block["min_past_origins"])
    st = backtest_cases(rule, panel.years, panel.state_series(), origins, lead, horizon)
    st = st[np.isfinite(st["ratio"])]
    org = st["origin"].to_numpy(dtype=int)
    c_org = cases["origin"].to_numpy(dtype=int)
    c_ok = np.isfinite(cases["ratio"].to_numpy(dtype=float))
    rows = []
    for _, case in st.iterrows():
        o = int(case["origin"])
        past = st[org + lead + horizon - 1 <= o]
        if len(past) < need or case["actual"] <= 0 or case["projected"] <= 0:
            continue
        q = ratio_quantiles(past["ratio"].to_numpy(dtype=float), levels)
        if q is None or not q[1] > 0 or not q[0] > 0:
            continue
        low, high = math.log(q[0] / q[1]), math.log(q[2] / q[1])
        n = int((c_ok & (c_org + lead + horizon - 1 <= o)).sum())
        low, high, _ = widen_small_sample(low, high, n, levels, rules.get("small_sample"))
        if growth:
            low, high, _ = widen_growth_class(low, high, n, growth.get("entry"), growth)
        proj = float(case["projected"])
        p50 = proj * float(q[1]) if keep else proj
        rows.append((o, state, p50 * math.exp(low), p50 * math.exp(high), p50, case["actual"]))
    state_rows = pd.DataFrame(rows, columns=["origin", "area", "low", "high", "point", "actual"])
    return {
        "rule": rule.name,
        "lead": int(lead),
        "horizon": int(horizon),
        "first_year": int(panel.years[0]),
        "last_year": int(panel.years[-1]),
        "state": state_rows,
        "county": county,
    }


def _holdout_rows(raw: Path) -> dict[str, Any]:
    """Re-derive the state confidence check's cases (current method, out of time)."""
    from visionhousing.validation import donor_pool as dp
    from visionhousing.validation import growth_coverage as gc
    from visionhousing.validation import state_holdouts as sh

    s = dp.load_settings()
    check = sh.load_check()
    paths = dp.fetch_national(raw, range(s["first_year"], s["last_year"] + 1))
    panel = dp.build_national_panel(paths, s, Path(raw) / "bps_national" / dp.PANEL_FILE)
    rule, bsettings = dp.rule_and_settings()
    cases = dp.area_cases(panel, rule, bsettings, s)
    states = [str(x).upper() for x in check[sh.BLOCK_KEY]["states"]]
    no_classes = pd.DataFrame({"state": [], "origin": [], "growth_class": []})
    scored = gc.score_cases(cases, no_classes, states, s, {}, with_candidate=False)
    rows = pd.DataFrame(
        {
            "origin": scored["origin"].astype(int),
            "area": scored["state"].astype(str),
            "low": scored["current_low"].astype(float),
            "high": scored["current_high"].astype(float),
            "point": scored["projected"].astype(float),
            "actual": scored["actual"].astype(float),
        }
    )
    return {
        "rule": rule.name,
        "lead": int(cases.lead),
        "horizon": int(cases.horizon),
        "first_year": int(s["first_year"]),
        "last_year": int(s["last_year"]),
        "rows": rows,
    }


# --------------------------------------------------------------------------- cards


def _release(results: Mapping[str, Any], first: int, last: int) -> dict[str, Any]:
    prov = results.get("provenance") or {}
    src = (prov.get("sources") or [{}])[0]
    cite = str(src.get("citation", src.get("id", "")))
    return {
        "data": cite if f"{first}-{last}" in cite else f"{cite}, {first}-{last}",
        "source_id": src.get("id"),
        "url": src.get("url"),
        "results_measured_on": prov.get("measured_on"),
        "results_id": results.get("id"),
    }


def _card(
    *,
    cid: str,
    key: str,
    text: Mapping[str, Any],
    spec: Mapping[str, Any],
    results: Mapping[str, Any],
    geography: Mapping[str, Any],
    published: Mapping[str, Any],
    rows: pd.DataFrame | None,
    info: Mapping[str, Any],
    settings: Mapping[str, Any],
    present_day_types: bool,
) -> dict[str, Any]:
    blk = settings[BLOCK]
    alpha = 1.0 - float(blk["interval_level"])
    pub = {k: published.get(k) for k in ("cases", "covered", "coverage")}
    origins = list(published.get("origins") or [])
    card: dict[str, Any] = {
        "id": cid,
        "quote": f"{pub['covered']}/{pub['cases']}",
        "study": text.get("study"),
        "study_key": key,
        "command": text.get("command"),
        "method": text.get("method"),
        "results_file": spec["results"],
        "preregistered_block": spec["block"],
        "geography": dict(geography),
        "target": text.get("target"),
        "rule_scored": info.get("rule"),
        "release": _release(results, info.get("first_year", 0), info.get("last_year", 0)),
        "published": pub,
    }
    if rows is not None and len(rows):
        metrics = case_metrics(rows, alpha, int(info["horizon"]))
        origins = sorted({int(o) for o in rows["origin"]})
        parity = metrics["cases"] == pub["cases"] and metrics["covered"] == pub["covered"]
        card["parity"] = {
            "rederived": {"cases": metrics["cases"], "covered": metrics["covered"]},
            "matches": bool(parity),
        }
        if parity:
            card["scores"] = metrics
    else:
        card["parity"] = {"rederived": None, "matches": None}
    lead, horizon = int(info.get("lead", 0)), int(info.get("horizon", 0))
    card["origin_set"] = {
        "origins": origins,
        "first": min(origins) if origins else None,
        "last": max(origins) if origins else None,
        "lead_years": lead or None,
        "horizon_years": horizon or None,
        "overlapping_windows": bool(horizon and len(origins) > 1),
        "note": (
            "Consecutive start years share all but one of their five target years; see "
            "scores.non_overlapping for start years five years apart."
        ),
    }
    if origins and horizon:
        card["fold_timeline"] = fold_timeline(
            origins, lead, horizon, int(info["first_year"]), int(info["last_year"])
        )
    card["disclosures"] = {
        "selection_full_record": blk["disclosures"]["selection_full_record"],
        "present_day_place_types": (
            blk["disclosures"]["present_day_place_types"]
            if present_day_types
            else "Not used by this card's ranges (the scored rule needs no place types)."
        ),
    }
    return card


def build_cards(
    *,
    raw: Path = ROOT / "data" / "raw",
    settings: Mapping[str, Any] | None = None,
    baseline_rows: Mapping[str, Mapping[str, Any]] | None = None,
    holdout_rows: Mapping[str, Any] | None = None,
    check_preregistration: bool = True,
) -> dict[str, Any]:
    """Build every card; refuses to score if the block changed.

    ``baseline_rows`` (state -> :func:`_baseline_rows` output) and
    ``holdout_rows`` (:func:`_holdout_rows` output) may be passed in (tests);
    otherwise they are re-derived from the packs and the national panel.
    """
    settings = settings if settings is not None else load_settings()
    checked = []
    if check_preregistration:
        from visionhousing.validation.preregistration import verify

        checked.append(verify(BLOCK, settings))
    blk, text = settings[BLOCK], settings.get("card_text") or {}
    studies = blk["studies"]
    cards: list[dict[str, Any]] = []

    def read(rel: str) -> dict[str, Any]:
        path = ROOT / rel
        return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}

    # Baseline reliability: state and county cards for each state.
    rel = read(studies["baseline_reliability_state"]["results"])
    if baseline_rows is None and rel.get("states"):
        from visionhousing.baseline.places import load_place_rules
        from visionhousing.ledger import load_ledger

        rules, ledger = load_place_rules(), load_ledger()
        block = rules["baseline_reliability"]
        baseline_rows = {st: _baseline_rows(st, ledger, rules, block) for st in rel["states"]}
    for st, entry in (rel.get("states") or {}).items():
        got = (baseline_rows or {}).get(st) or {}
        same_rule = got.get("rule") == entry.get("rule")
        info = {**got, "rule": entry.get("rule")}
        cards.append(
            _card(
                cid=f"baseline_reliability.state.{st}",
                key="baseline_reliability_state",
                text=text.get("baseline_reliability_state", {}),
                spec=studies["baseline_reliability_state"],
                results=rel,
                geography={"level": "state", "area": st},
                published=entry["state_range"],
                rows=got.get("state") if same_rule else None,
                info=info,
                settings=settings,
                present_day_types=True,
            )
        )
        county = entry.get("county_ranges") or {}
        cards.append(
            _card(
                cid=f"baseline_reliability.county.{st}",
                key="baseline_reliability_county",
                text=text.get("baseline_reliability_county", {}),
                spec=studies["baseline_reliability_county"],
                results=rel,
                geography={"level": "county", "area": st, "groups": "size class at each origin"},
                published=county.get("all") or {},
                rows=got.get("county") if same_rule else None,
                info=info,
                settings=settings,
                present_day_types=True,
            )
        )
    # State confidence check.
    hold = read(studies["state_holdouts"]["results"])
    if holdout_rows is None and hold.get("states"):
        holdout_rows = _holdout_rows(raw)
    for st, entry in (hold.get("states") or {}).items():
        rows = None
        if holdout_rows is not None:
            r = holdout_rows["rows"]
            rows = r[r["area"] == st]
        info = dict(holdout_rows or {})
        info.pop("rows", None)
        published = {**entry, "origins": sorted(set(rows["origin"])) if rows is not None else []}
        cards.append(
            _card(
                cid=f"state_holdouts.state.{st}",
                key="state_holdouts",
                text=text.get("state_holdouts", {}),
                spec=studies["state_holdouts"],
                results=hold,
                geography={"level": "state", "area": st},
                published=published,
                rows=rows,
                info=info,
                settings=settings,
                present_day_types=False,
            )
        )
    # Listed only: national state-total calibration (counts from its own file).
    for key in blk.get("listed_only") or []:
        nat = read(f"assumptions/calibration/{key}.yaml")
        s = nat.get("settings") or {}
        for variant, v in (nat.get("variants") or {}).items():
            for st, entry in (v.get("named_states") or {}).items():
                origins = [int(d["origin"]) for d in (v.get("named_detail") or {}).get(st, [])]
                info = {
                    "rule": (s.get("rules") or {}).get(st),
                    "lead": s.get("lead_years", 0),
                    "horizon": s.get("horizon_years", 0),
                    "first_year": (s.get("origins") or [0])[0],
                    "last_year": (s.get("origins") or [0, 0])[-1]
                    + int(s.get("lead_years", 0))
                    + int(s.get("horizon_years", 0))
                    - 1,
                }
                card = _card(
                    cid=f"{key}.{variant}.state.{st}",
                    key=key,
                    text=text.get(key, {}),
                    spec={"results": f"assumptions/calibration/{key}.yaml", "block": key},
                    results=nat,
                    geography={"level": "state", "area": st},
                    published={**entry, "origins": origins},
                    rows=None,
                    info=info,
                    settings=settings,
                    present_day_types=True,
                )
                card["variant"] = variant
                card["listed_only"] = True
                cards.append(card)
    return {
        "version": 1,
        "id": "validation_cards_2026_11",
        "label": "Validation cards: what each coverage count refers to",
        "status": "validation",
        "use": (
            "Descriptive. The validation API returns each state's cards beside its counts; "
            "changes no estimate."
        ),
        "provenance": {
            "summary": (
                "Study, origins, target, geography and release behind each published coverage "
                "count, with an interval score, signed error, error by size, a non-overlapping "
                "subset and a fold timeline for the re-derived studies."
            ),
            "measured_on": date.today().isoformat(),
            "method": (
                "visionhousing.validation.cards; block validation_card_scores in "
                "assumptions/validation_cards.yaml; cases re-derived with the studies' own "
                "building blocks (read-only); see docs/modules/validation_cards.md."
            ),
            "generated_by": "visionhousing validate-cards",
            "sources": [
                {
                    "id": "bps_county_annual",
                    "citation": "U.S. Census Bureau, Building Permits Survey, county annual files",
                    "url": "https://www2.census.gov/econ/bps/County/",
                    "licence": "Public domain (U.S. government work)",
                }
            ],
            "limits": [
                "Rolling start years overlap: the non-overlapping subsets hold one to three "
                "cases per state, so they show how little the overlapping counts certify, not "
                "a better coverage estimate.",
                str(blk["disclosures"]["selection_full_record"]).strip(),
                str(blk["disclosures"]["present_day_place_types"]).strip(),
                "The national state-total calibration is listed with its counts only; its "
                "cases were not re-derived in this release.",
            ],
        },
        "preregistration": checked,
        "cross_state": _cross_state(cards),
        "cards": cards,
    }


def _cross_state(cards: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """State cards of each re-derived study, ordered by the state's mean actual total."""
    out: dict[str, Any] = {}
    for card in cards:
        if card.get("listed_only") or card["geography"]["level"] != "state":
            continue
        sc = card.get("scores")
        if not sc:
            continue
        out.setdefault(card["study_key"], []).append(
            {
                "state": card["geography"]["area"],
                "card": card["id"],
                "quote": card["quote"],
                "actual_mean": sc["actual_mean"],
                "interval_score_mean": sc["interval_score_mean"],
                "signed_log_error_median": sc["signed_log_error_median"],
                "non_overlapping_primary": sc["non_overlapping"]["primary"],
            }
        )
    for rows in out.values():
        rows.sort(key=lambda r: r["actual_mean"])
    return {
        "note": (
            "Error by size for state cards: each study's states ordered from the smallest to "
            "the largest mean five-year total over the scored cases."
        ),
        "studies": out,
    }


def write_document(doc: Mapping[str, Any], path: Path = OUT_PATH) -> Path:
    """Write the cards document as YAML with the generated-file header."""
    from visionhousing.validation.donor_pool import _clean

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(_clean(dict(doc)), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


def cards_for(state: str | None, path: Path = OUT_PATH) -> list[dict[str, Any]]:
    """The cards of one state (empty when the file is absent or the state has none)."""
    path = Path(path)
    if not state or not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    st = str(state).upper()
    return [c for c in data.get("cards") or [] if c.get("geography", {}).get("area") == st]


def add_parser(sub: Any) -> None:
    """Add the ``validate-cards`` sub-command."""
    p = sub.add_parser(
        "validate-cards",
        help="Validation cards: name the study behind each coverage count; add fuller scores",
    )
    p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    p.add_argument("--out", type=Path, default=OUT_PATH, help="results file")


def command(args: Any) -> int:
    """Build the cards and write them; print one line per card."""
    doc = build_cards(raw=args.raw)
    path = write_document(doc, args.out)
    for c in doc["cards"]:
        sc = c.get("scores") or {}
        extra = (
            f" IS {sc['interval_score_mean']:.2f}, non-overlapping "
            f"{sc['non_overlapping']['primary']['covered']}/"
            f"{sc['non_overlapping']['primary']['cases']}"
            if sc
            else ""
        )
        print(f"validate-cards: {c['id']} {c['quote']} parity={c['parity']['matches']}{extra}")
    print(f"validate-cards: wrote {path}")
    return 0
