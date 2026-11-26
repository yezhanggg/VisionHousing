"""The baseline card: what the baseline is built from, a state check, and the 2026 bridge.

Plain-language summary
----------------------
A reader of a baseline range should be able to see, in one place:

* **which past years** the baseline reads, and **which permit columns** (the
  Census Building Permits Survey's estimated units in 1-, 2-, 3-4 and 5+ unit
  buildings; the "reported only" columns are not used);
* **which data release** it rests on (the vintage and the date the file was
  posted), and the **information cutoff** (the model's as-of date: data
  released after it are not used);
* the **forecast gap**: the last full year of data is 2025 and the projected
  years are 2027-2031, so 2026 has no full-year count. The shipped method
  carries the rule's yearly level flat across that year.

This module writes that card for each state, plus three checks. None of them
changes an estimate:

1. **State reconciliation (a release fixture).** The shipped rule of each
   state, applied to the state's own yearly totals from the state annual files,
   gives the state part of the shipped middle value. For Pennsylvania (rule:
   the state's five-year mean, shared out to counties) that is the 2021-2025
   sum; for Massachusetts and Wyoming (mean of the last three years) it is five
   times the 2023-2025 mean. Vermont's middle value comes from county
   shrinkage, which a state series cannot reproduce, so its state series is
   shown as an outside control only.
2. **Pennsylvania window sensitivity** (id ``baseline_pa_window_2022_2025``).
   Pennsylvania's 2021 total is far above the next four years. The 2022-2025
   mean times five shows how much that one year moves the middle value. It was
   named after the spike was seen, so it is a disclosed sensitivity, not a
   pre-registered test and not a replacement baseline. It is recorded only and
   is never subtracted from a reform or package result.
3. **2026 nowcast** (id ``baseline_bridge_nowcast_2026``, diagnostic only).
   From the monthly year-to-date state files released on or before the cutoff:
   ``nowcast = YTD(2026, Jan..m) / s``, with ``s = YTD(y, Jan..m) / annual(y)``
   for each history year ``y`` (same months, like for like). The low, middle
   and high nowcast use the highest, median and lowest ``s``. A year-to-date
   count is never treated as a full year.

Settings: block ``baseline_card`` in ``assumptions/baseline_card.yaml`` (kept
out of ``backtest.yaml`` so it never changes a run's calibration hash). The command
``visionhousing baseline-card`` (light: state files only, no engine run) writes
``assumptions/calibration/baseline_reconciliation.yaml``; the API
(:func:`card_for`) and the generated docs table read that file.
"""

from __future__ import annotations

import calendar
import math
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from visionhousing.baseline.rules import Rule, project

__all__ = [
    "BLOCK",
    "RESULTS_PATH",
    "card_for",
    "load_settings",
    "nowcast",
    "rule_state_component",
    "run_card",
    "state_annual_units",
    "window_diagnostic",
    "write_results",
]

BLOCK = "baseline_card"
"""Name of the settings block in ``assumptions/baseline_card.yaml``."""

SETTINGS_PATH = Path(__file__).resolve().parents[3] / "assumptions" / "baseline_card.yaml"
"""The card's settings file."""

_ROOT = Path(__file__).resolve().parents[3]
RESULTS_PATH = _ROOT / "assumptions" / "calibration" / "baseline_reconciliation.yaml"
"""Default results file (the block's ``results`` names it too)."""

RAW = _ROOT / "data" / "raw"
OUTCOME_PATH = _ROOT / "assumptions" / "outcome.yaml"

STATE_FIPS = {"PA": "42", "MA": "25", "WY": "56", "VT": "50"}
"""States the card is written for (the four state packs)."""

SOURCES = {
    "bps_state_annual": {
        "id": "bps_state_annual",
        "citation": "U.S. Census Bureau, Building Permits Survey, state annual files",
        "url": "https://www2.census.gov/econ/bps/State/",
        "licence": "Public domain (U.S. government work)",
    },
    "bps_state_monthly_ytd": {
        "id": "bps_state_monthly_ytd",
        "citation": "U.S. Census Bureau, Building Permits Survey, state monthly year-to-date files",
        "url": "https://www2.census.gov/econ/bps/State/",
        "licence": "Public domain (U.S. government work)",
    },
}


def _n(x: float) -> str:
    return f"{x:,.0f}"


# --------------------------------------------------------------------------- pure parts


def state_annual_units(wide: Any, state_fips: str) -> int | None:
    """Estimated units (1, 2, 3-4, 5+ unit buildings) of one state in a parsed state file."""
    from visionhousing.data.sources.bps_monthly import state_ytd_units

    return state_ytd_units(wide, state_fips)


def rule_state_component(
    rule: Rule, series: Mapping[int, float], origin: int, targets: Sequence[int]
) -> float:
    """The rule applied to the state's own yearly totals, summed over the target years.

    For mean-type rules (and the top-down share, whose state level is the
    state's own window mean) this is the state part of the shipped middle value.
    """
    years = np.array(sorted(series), dtype=int)
    values = np.array([[float(series[y]) for y in years]])
    out, _ = project(rule, years, values, origin, np.array(list(targets), dtype=int))
    return float(out.sum())


def window_diagnostic(
    series: Mapping[int, float], first: int, last: int, horizon: int, shipped: float
) -> dict[str, Any]:
    """Mean of ``first..last`` times the horizon, and its difference from ``shipped``."""
    years = list(range(first, last + 1))
    missing = [y for y in years if y not in series]
    if missing:
        raise ValueError(f"window years without data: {missing}")
    total = sum(float(series[y]) for y in years)
    mean = total / len(years)
    five = mean * horizon
    diff = five - shipped
    return {
        "years": [first, last],
        "annual_mean": mean,
        "five_year": five,
        "difference": diff,
        "difference_pct": diff / shipped if shipped else None,
    }


def nowcast(ytd_now: float, ytd_past: Mapping[int, float], annual: Mapping[int, float]) -> dict:
    """Full-year nowcast from a year-to-date count and like-for-like seasonal shares.

    Args:
        ytd_now: Units authorized so far in the gap year (months 1..m).
        ytd_past: History year -> units in months 1..m of that year, as published then.
        annual: History year -> the year's final annual total.

    Returns:
        ``shares`` (year -> s), and ``low`` / ``mid`` / ``high`` = ``ytd_now / s``
        at the highest, median and lowest share.
    """
    shares = {y: float(ytd_past[y]) / float(annual[y]) for y in sorted(ytd_past) if annual.get(y)}
    if not shares:
        raise ValueError("no history year has both a year-to-date and an annual count")
    s = list(shares.values())
    return {
        "shares": shares,
        "share_low": min(s),
        "share_mid": statistics.median(s),
        "share_high": max(s),
        "low": ytd_now / max(s),
        "mid": ytd_now / statistics.median(s),
        "high": ytd_now / min(s),
    }


# --------------------------------------------------------------------------- reading


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """The ``baseline_card`` block of ``assumptions/baseline_card.yaml``."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return dict(doc[BLOCK])


def information_cutoff(path: Path = OUTCOME_PATH) -> str:
    """The model's as-of date (``as_of`` in ``assumptions/outcome.yaml``)."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return str(doc["as_of"])


def shipped_rules(path: Path | None = None) -> dict[str, str]:
    """Each state's shipped rule, as recorded in ``baseline_reliability.yaml``."""
    from visionhousing.baseline.reliability import RESULTS_PATH as REL

    doc = yaml.safe_load(Path(path or REL).read_text(encoding="utf-8")) or {}
    return {s: str(e["rule"]) for s, e in (doc.get("states") or {}).items()}


def _rule(name: str, ledger: Any, rules: Mapping[str, Any]) -> Rule:
    from visionhousing.baseline.contest import all_rules

    for r in all_rules(ledger, rules):
        if r.name == name:
            return r
    raise ValueError(f"rule {name!r} is not one the baseline can run")


def _annual_series(
    states: Sequence[str], years: Sequence[int], raw: Path, client: Any = None
) -> dict[str, dict[int, int]]:
    """Each state's yearly units from the state annual files (fetched if not cached)."""
    from visionhousing.data.sources import bps

    out: dict[str, dict[int, int]] = {s: {} for s in states}
    for y in years:
        dest = Path(raw) / "bps" / f"st{y}a.txt"
        if dest.exists():
            text = dest.read_text(encoding="latin-1")
        else:
            from visionhousing.data.http import HttpClient

            text = bps._get(client or HttpClient(), "bps_state_annual", y, dest, None)
        wide = bps.parse_state_annual(text, y)
        for s in states:
            v = state_annual_units(wide, STATE_FIPS[s])
            if v is None:
                raise ValueError(f"state file {dest.name} has no row for {s}")
            out[s][y] = v
    return out


# --------------------------------------------------------------------------- the study


def _bridge(
    settings: Mapping[str, Any],
    states: Sequence[str],
    annual: Mapping[str, Mapping[int, int]],
    carried: Mapping[str, float],
    basis: Mapping[str, str],
    gap: Sequence[int],
    cutoff: str,
    raw: Path,
    client: Any,
) -> dict[str, Any]:
    """The carry-annual method and the monthly nowcast diagnostic (or 'unavailable')."""
    from visionhousing.data.sources import bps_monthly as bm

    spec = settings["bridge"]
    nc = spec["nowcast"]
    hist = [int(y) for y in nc["history_years"]["central"]]
    out: dict[str, Any] = {
        "method": spec["method"],
        "method_text": (
            "Carry annual through the gap: the rule's yearly level from the last full year "
            "of data is used unchanged for the gap year and each projected year. The gap "
            "year is not projected separately and adds nothing to the five-year total."
        ),
        "gap_years": list(gap),
        "nowcast": {"id": nc["id"], "label": nc["label"], "use": nc["use"]},
    }
    if not gap:
        out["nowcast"].update(status="not_needed")
        return out
    year = int(gap[-1])
    try:
        found = None
        for m in range(12, 0, -1):
            got = bm.fetch_ytd([(year, m)], raw, client=client, released_by=cutoff)[(year, m)]
            if got["path"] is not None:
                found = (m, got)
                break
        if found is None:
            out["nowcast"].update(
                status="unavailable",
                reason=f"no monthly year-to-date file for {year} was released by {cutoff}",
            )
            return out
        month, now = found
        nxt = None
        if month < 12:
            nxt = bm.fetch_ytd([(year, month + 1)], raw, client=client, released_by=cutoff)[
                (year, month + 1)
            ]
        past = bm.fetch_ytd([(y, month) for y in hist], raw, client=client, released_by=cutoff)
    except Exception as err:
        out["nowcast"].update(status="unavailable", reason=f"monthly files not fetchable: {err}")
        return out

    def units(info: Mapping[str, Any], y: int) -> dict[str, int]:
        text = Path(info["path"]).read_text(encoding="latin-1")
        wide = bm.parse_state_ytd(text, y, month)
        return {s: bm.state_ytd_units(wide, STATE_FIPS[s]) for s in states}

    now_units = units(now, year)
    past_units = {y: units(past[(y, month)], y) for y in hist if past[(y, month)]["path"]}
    per_state = {}
    ids = [nc["id"], "baseline.first_year", "baseline.recent_window_years"]
    months = f"Jan-{calendar.month_abbr[month]}"
    for s in states:
        res = nowcast(now_units[s], {y: u[s] for y, u in past_units.items()}, annual[s])
        shares = ", ".join(f"{y} {v:.3f}" for y, v in res["shares"].items())
        per_state[s] = {
            "ytd": now_units[s],
            "ytd_history": {y: u[s] for y, u in past_units.items()},
            "shares": {y: round(v, 4) for y, v in res["shares"].items()},
            "low": round(res["low"]),
            "mid": round(res["mid"]),
            "high": round(res["high"]),
            "carried_annual": round(carried[s]),
            "formula": (
                f"nowcast = YTD {year} {months} / s; YTD = {_n(now_units[s])}; "
                f"s = YTD(y, {months}) / annual(y) for y in {hist[0]}-{hist[-1]} "
                f"({shares}); low / middle / high use s = {res['share_high']:.3f} / "
                f"{res['share_mid']:.3f} / {res['share_low']:.3f} = {_n(res['low'])} / "
                f"{_n(res['mid'])} / {_n(res['high'])} units; the carried annual level is "
                f"{_n(carried[s])} units ({basis[s]}). Diagnostic only: no estimate uses it."
            ),
            "carried_annual_basis": basis[s],
            "sources": ["bps_state_monthly_ytd", "bps_state_annual"],
            "assumption_ids": ids,
        }
    out["nowcast"].update(
        status="available",
        year=year,
        through_month=month,
        file=now["file"],
        release_date=now["release_date"],
        next_file=None if nxt is None else nxt["file"],
        next_release_date=None if nxt is None else nxt["release_date"],
        history_years=hist,
        states=per_state,
        caveats=[
            "A year-to-date count is never treated as a full year.",
            "Monthly and annual files differ in reporting and imputation; the share s "
            "absorbs that difference as well as the season.",
            "Release dates are the files' Last-Modified dates on the Census server; a "
            "re-posted file shows a later date than its first release.",
            "The history files are as posted now; earlier copies were not archived, so "
            "this is a revised-data exercise.",
        ],
    )
    return out


def run_card(
    *,
    rules: Mapping[str, Any] | None = None,
    ledger: Any = None,
    raw: Path = RAW,
    client: Any = None,
    cutoff: str | None = None,
    annual_release: str | None = None,
    bridge: bool = True,
) -> dict[str, Any]:
    """Build the card document for every state in the block (see module docstring)."""
    from visionhousing.data.sources import bps
    from visionhousing.data.sources.bps_monthly import release_date
    from visionhousing.ledger import load_ledger

    settings = load_settings()
    if rules is None:
        from visionhousing.baseline.places import load_place_rules

        rules = load_place_rules()
    ledger = ledger if ledger is not None else load_ledger()
    cutoff = cutoff or information_cutoff()
    first_year = round(ledger["baseline.first_year"].central)
    horizon = round(ledger["baseline.horizon_years"].central)
    targets = list(range(first_year, first_year + horizon))
    origin = bps.LATEST_YEAR
    gap = list(range(origin + 1, first_year))
    states = [str(s) for s in settings["states"]]
    shipped = shipped_rules()
    need_years = set(int(y) for y in settings["shown_years"])
    rule_objs = {s: _rule(shipped[s], ledger, rules) for s in states}
    for r in rule_objs.values():
        need_years |= set(range(origin - (r.window or 1) + 1, origin + 1))
    annual = _annual_series(states, sorted(need_years), raw, client)
    if annual_release is None:
        from visionhousing.data.http import HttpClient

        url = bps.get_source("bps_state_annual").url(year=origin)
        try:
            annual_release = release_date(client or HttpClient(), url)
        except Exception:
            annual_release = None
    units_label = str(settings["unit_fields_label"])
    per_state: dict[str, Any] = {}
    carried: dict[str, float] = {}
    basis: dict[str, str] = {}
    for s in states:
        rule = rule_objs[s]
        lo = origin - rule.window + 1
        comp = rule_state_component(rule, annual[s], origin, targets)
        carried[s] = comp / horizon
        reconciled = s in settings["reconcile"]
        basis[s] = (
            f"the shipped rule's state level, mean of {lo}-{origin}"
            if reconciled
            else f"state-series mean of {lo}-{origin}; the shipped level comes from county "
            "shrinkage"
        )
        window_vals = " + ".join(_n(annual[s][y]) for y in range(lo, origin + 1))
        hist = {
            "level_years": [lo, origin],
            "backtest_years": [bps.FIRST_YEAR, origin],
        }
        if rule.method == "top_down":
            hist["share_years"] = [origin - rule.share_window + 1, origin]
        entry: dict[str, Any] = {
            "rule": rule.name,
            "rule_text": rule.describe(origin),
            "annual": {y: annual[s][y] for y in settings["shown_years"]},
            "historical_years": hist,
            "role": "reconciled" if reconciled else "control",
        }
        if reconciled:
            entry["state_component"] = round(comp)
            entry["formula"] = (
                f"state component = {horizon} x mean of {lo}-{origin} state units "
                f"= {horizon} x ({window_vals}) / {rule.window} = {_n(comp)} units "
                f"(rule {rule.name}, state series only; county shares do not change the sum)"
            )
            entry["sources"] = ["bps_state_annual"]
            entry["assumption_ids"] = list(
                dict.fromkeys(
                    [*rule.assumption_ids, "baseline.horizon_years", "baseline.first_year"]
                )
            )
        else:
            entry["note"] = (
                "The shipped middle value comes from county shrinkage toward place-type rates; "
                "county histories, home counts and group assignments are needed to reproduce "
                "it, so the state series is an outside control only."
            )
        per_state[s] = entry
    diagnostics = {}
    for did, spec in (settings.get("window_diagnostics") or {}).items():
        s = str(spec["state"])
        first, last = int(spec["first_year"]["central"]), int(spec["last_year"]["central"])
        ship = float(per_state[s]["state_component"])
        d = window_diagnostic(annual[s], first, last, horizon, ship)
        vals = " + ".join(_n(annual[s][y]) for y in range(first, last + 1))
        n = last - first + 1
        diagnostics[did] = {
            "label": spec["label"],
            "state": s,
            "use": spec["use"],
            "kind": spec["kind"],
            "years": [first, last],
            "annual_mean": round(d["annual_mean"]),
            "five_year": round(d["five_year"]),
            "shipped_state_component": round(ship),
            "difference": round(d["difference"]),
            "difference_pct": round(d["difference_pct"], 4),
            "formula": (
                f"{horizon} x ({vals}) / {n} = {horizon} x {_n(d['annual_mean'])} = "
                f"{_n(d['five_year'])} units; difference from the shipped state component "
                f"{_n(ship)} = {_n(d['difference'])} units ({100 * d['difference_pct']:.1f}%)"
            ),
            "sources": ["bps_state_annual"],
            "assumption_ids": [did, "baseline.horizon_years"],
            "status": (
                "Disclosed historical-window sensitivity. Named after the spike was seen, so "
                "not pre-registered. Not a replacement baseline; never subtracted from any "
                "reform or package result."
            ),
            "rationale": " ".join(str(spec["rationale"]).split()),
        }
    doc: dict[str, Any] = {
        "version": 1,
        "id": "baseline_reconciliation_2026_11",
        "label": "Baseline card and state reconciliation (Building Permits Survey state files)",
        "status": "validation",
        "use": (
            "The API shows each state's card beside the baseline; the release fixture test "
            "checks the reconciled values. Changes no estimate. Method: docs/modules/baseline.md."
        ),
        "provenance": {
            "summary": (
                "What the baseline reads (years, permit columns, data release, information "
                "cutoff, forecast gap), the shipped rule applied to each state's own totals, a "
                "Pennsylvania window sensitivity and a 2026 nowcast diagnostic."
            ),
            "method": (
                "Census Building Permits Survey state annual and monthly year-to-date files; "
                "visionhousing.baseline.card; settings in assumptions/baseline_card.yaml "
                "(baseline_card)."
            ),
            "generated_by": "visionhousing baseline-card",
            "sources": [SOURCES["bps_state_annual"], SOURCES["bps_state_monthly_ytd"]],
            "limits": [
                "The state component checks the state-level arithmetic only; it does not "
                "check the county split, the range or any reform.",
                "Vermont's middle value is not reproduced here (county shrinkage).",
                "Release dates are Last-Modified dates on the Census server.",
            ],
        },
        "card": {
            "unit_fields": list(settings["unit_fields"]),
            "unit_fields_label": units_label,
            "data_release": {
                "source": "bps_state_annual",
                "vintage": f"{origin} annual files (final)",
                "last_full_year": origin,
                "release_date": annual_release,
                "release_date_basis": "Last-Modified date of the state annual file",
            },
            "information_cutoff": cutoff,
            "information_cutoff_basis": "as_of in assumptions/outcome.yaml",
            "forecast_window": [targets[0], targets[-1]],
            "forecast_gap": gap,
            "forecast_gap_text": (
                f"{', '.join(str(y) for y in gap)} has no full-year permit count by the cutoff; "
                f"the baseline carries the {origin} rule level through it."
                if gap
                else "No gap: the projected years follow the last full year of data."
            ),
        },
        "states": per_state,
        "diagnostics": diagnostics,
    }
    if bridge:
        doc["bridge"] = _bridge(settings, states, annual, carried, basis, gap, cutoff, raw, client)
    return _clean(doc)


def _clean(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {(int(k) if isinstance(k, np.integer) else k): _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, float | np.floating):
        f = float(v)
        return None if math.isnan(f) else f
    return v


HEADER = """\
# Calibration (validation): baseline card and state reconciliation (generated; do not edit).
# Written by `visionhousing baseline-card` from the Census Building Permits Survey
# state annual files and monthly year-to-date files. What the baseline reads, the
# shipped rule applied to each state's own totals (a release fixture), the
# Pennsylvania window sensitivity and the 2026 nowcast. Changes no estimate
# (settings: assumptions/baseline_card.yaml).
# Method: docs/modules/baseline.md ("Baseline card").
"""


def write_results(doc: Mapping[str, Any], path: Path = RESULTS_PATH) -> Path:
    """Write the card document as YAML with the generated-file header."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- engine side


def card_for(state: str | None, path: Path | None = None) -> dict[str, Any] | None:
    """The baseline card of one state, for the API; ``None`` if the state has none."""
    if not state:
        return None
    where = Path(path) if path is not None else RESULTS_PATH
    if not where.is_file():
        return None
    doc = yaml.safe_load(where.read_text(encoding="utf-8")) or {}
    code = str(state).upper()
    entry = (doc.get("states") or {}).get(code)
    if not entry:
        return None
    bridge = dict(doc.get("bridge") or {})
    nc = dict(bridge.get("nowcast") or {})
    if "states" in nc:
        nc["state"] = (nc.pop("states") or {}).get(code)
    if bridge:
        bridge["nowcast"] = nc
    return {
        "state": code,
        **dict(doc.get("card") or {}),
        **entry,
        "diagnostics": {
            k: v for k, v in (doc.get("diagnostics") or {}).items() if v.get("state") == code
        },
        "bridge": bridge or None,
        "source": "assumptions/calibration/baseline_reconciliation.yaml",
    }


# --------------------------------------------------------------------------- command


def add_parser(sub: Any) -> None:
    """Add ``baseline-card`` to the command line."""
    p = sub.add_parser(
        "baseline-card",
        help="Write the baseline card, state reconciliation and 2026 bridge (state files only)",
    )
    p.add_argument("--out", type=Path, default=RESULTS_PATH, help="results file")
    p.add_argument("--no-bridge", action="store_true", help="skip the monthly nowcast")


def command(args: Any) -> int:
    """Build the card, print the reconciled values and write the results file."""
    doc = run_card(bridge=not args.no_bridge)
    for s, e in doc["states"].items():
        print(f"{s} ({e['rule']}, {e['role']}): {e.get('state_component', '-')}")
    for k, d in doc["diagnostics"].items():
        print(f"{k}: {d['five_year']} ({d['difference']}, {100 * d['difference_pct']:.1f}%)")
    nc = (doc.get("bridge") or {}).get("nowcast") or {}
    print(f"nowcast: {nc.get('status')} {nc.get('file', '')} {nc.get('release_date', '')}")
    print(f"wrote {write_results(doc, args.out)}")
    return 0
