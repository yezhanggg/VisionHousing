"""State confidence check: the current baseline range, held out of time, for named states.

Plain-language summary
----------------------
The readiness check (:mod:`visionhousing.intake.readiness`, step 3) asks
whether a validation set holds the state out: has anyone checked, on that
state's own past, how often the baseline's likely range caught the real
five-year total? The first donor-pool test did that for a few small states,
but not for every state the tool publishes.

This module answers it for the states listed in
``assumptions/validation_state_holdouts.yaml`` (block ``state_holdout_check``).
For each past start year (*origin*) it builds the range the baseline uses
today: the state's own error record blended toward Pennsylvania's, only ever
widened (``small_sample`` in ``assumptions/backtest.yaml``). Only past cases
whose five target years were fully observed by that origin are used (*out of
time*), as in a real run. It then checks whether the real total fell inside.

It reports, per state: cases scored, coverage (share caught), misses above and
below, mean width and whether coverage sits inside the 75-85% band. It decides
nothing and changes no baseline run. The block was fingerprinted
(``assumptions/preregistration.yaml``) before its first score, and the run
stops if it, or the shared donor-pool rules, changed.

The command ``visionhousing validate-state-holdouts`` writes
``assumptions/calibration/state_holdouts.yaml``.

Method in full: ``docs/modules/validation_donor_pool.md`` (section "A state
confidence check").
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.baseline.rules import Rule
from visionhousing.baseline.settings import BaselineSettings
from visionhousing.validation import donor_pool as dp
from visionhousing.validation import growth_coverage as gc

__all__ = [
    "BLOCK_KEY",
    "OUT_PATH",
    "SETTINGS_PATH",
    "held_out_states",
    "load_check",
    "run_check",
    "summarise_states",
    "write_document",
]

BLOCK_KEY = "state_holdout_check"
SETTINGS_PATH = dp.ROOT / "assumptions" / "validation_state_holdouts.yaml"
OUT_PATH = dp.ROOT / "assumptions" / "calibration" / "state_holdouts.yaml"

HEADER = """\
# Calibration (validation): held-out coverage of the baseline's CURRENT range, per state
# (generated; do not edit). Written by `visionhousing validate-state-holdouts` from the
# Census Building Permits Survey county files of every state. Descriptive: no decision,
# no baseline run reads it. `visionhousing state-readiness` reads `states.<ST>.held_out`.
# Method: docs/modules/validation_donor_pool.md ("A state confidence check").
"""


def load_check(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Read ``assumptions/validation_state_holdouts.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def summarise_states(
    scored: pd.DataFrame,
    states: list[str],
    settings: Mapping[str, Any],
    block: Mapping[str, Any],
) -> dict[str, Any]:
    """Per-state coverage of the current range from :func:`growth_coverage.score_cases` rows.

    A state with no scored case is listed with ``cases: 0`` and ``held_out: false``.
    """
    band = [float(x) for x in settings["coverage_band"]]
    need = int(block["min_cases_scored"])
    out: dict[str, Any] = {}
    for st in states:
        g = scored[scored["state"] == st] if not scored.empty else scored
        n = len(g)
        if n == 0:
            out[st] = {"cases": 0, "held_out": False, "coverage": None}
            continue
        cov = float(g["current_inside"].mean())
        out[st] = {
            "cases": n,
            "held_out": n >= need,
            "origins": [int(g["origin"].min()), int(g["origin"].max())],
            "coverage": cov,
            "covered": int(g["current_inside"].sum()),
            "above": int(g["current_above"].sum()),
            "below": int(g["current_below"].sum()),
            "mean_width": float(g["current_width"].mean()),
            "weight_own": float(g["w"].median()),
            "inside_band": bool(band[0] <= cov <= band[1]),
            "gap_to_target": abs(cov - float(settings["target_coverage"])),
        }
    return out


def run_check(
    panel: pd.DataFrame,
    settings: Mapping[str, Any],
    check: Mapping[str, Any],
    bsettings: BaselineSettings,
    rule: Rule,
    *,
    as_of: str | None = None,
    check_preregistration: bool = True,
) -> dict[str, Any]:
    """Score the current range for each listed state, out of time; return the results document.

    Args:
        panel: The national county panel (:func:`donor_pool.build_national_panel`).
        settings: ``assumptions/validation_donor_pool.yaml`` (shared rules).
        check: ``assumptions/validation_state_holdouts.yaml``.
        bsettings: Baseline settings, from :func:`donor_pool.rule_and_settings`.
        rule: The scored rule, from the same function.
        as_of: Date of the data, written into the provenance.
        check_preregistration: ``False`` only for synthetic test settings.
    """
    checked = []
    if check_preregistration:
        from visionhousing.validation.preregistration import verify

        checked = [verify(dp.RULES_BLOCK, settings), verify(BLOCK_KEY, check)]
    block = check[BLOCK_KEY]
    if block.get("method") != "pa_blend" or block.get("variant") != "out_of_time":
        raise ValueError("state_holdout_check scores pa_blend, out of time, only")
    states = [str(s).upper() for s in block["states"]]
    cases = dp.area_cases(panel, rule, bsettings, settings)
    no_classes = pd.DataFrame({"state": [], "origin": [], "growth_class": []})
    scored = gc.score_cases(cases, no_classes, states, settings, {}, with_candidate=False)
    doc: dict[str, Any] = {
        "version": 1,
        "id": "state_holdouts_2026_11",
        "label": "State confidence check (current baseline range, held out of time)",
        "status": "validation",
        "use": (
            "Descriptive only. Read by visionhousing state-readiness (step 3, validation "
            "hold-out); never by a baseline run."
        ),
        "provenance": {
            "summary": (
                "Out-of-time coverage of the baseline's current state range (own record "
                "blended toward Pennsylvania's, only widened) for each listed state."
            ),
            "measured_on": as_of or date.today().isoformat(),
            "method": (
                "National county panel from the BPS annual county files; shared rules in "
                "assumptions/validation_donor_pool.yaml, block state_holdout_check in "
                "assumptions/validation_state_holdouts.yaml; back-test cases from "
                "visionhousing.baseline (read-only); see docs/modules/validation_donor_pool.md."
            ),
            "generated_by": "visionhousing validate-state-holdouts",
            "sources": [
                {
                    "id": "bps_county_annual",
                    "citation": (
                        "U.S. Census Bureau, Building Permits Survey, county annual files, "
                        f"{settings['first_year']}-{settings['last_year']}"
                    ),
                    "url": "https://www2.census.gov/econ/bps/County/co{year}a.txt",
                    "licence": "Public domain (U.S. government work)",
                }
            ],
            "limits": [
                "Each state is scored on about a dozen overlapping five-year windows, so its "
                "coverage moves in steps of several points.",
                "All origins share one national housing cycle (the recovery after the bust).",
                "The growth-aware widening adopted by the third test is not scored here: the "
                "engine is not wired to it yet.",
            ],
        },
        "preregistration": {
            "block": BLOCK_KEY,
            "sha256": dp.preregistration_sha256(block),
            "checks": checked,
        },
        "settings": {
            "rule": settings["rule"],
            "method": block["method"],
            "variant": block["variant"],
            "lead_years": cases.lead,
            "horizon_years": cases.horizon,
            "target_coverage": settings["target_coverage"],
            "coverage_band": list(settings["coverage_band"]),
            "full_weight_cases": settings["full_weight_cases"],
            "min_cases_scored": block["min_cases_scored"],
        },
        "states": summarise_states(scored, states, settings, block),
    }
    return dp._clean(doc)


def write_document(doc: Mapping[str, Any], path: Path = OUT_PATH) -> Path:
    """Write the document as YAML with the generated-file header; returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(dict(doc), Dumper=dp._Dumper, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


def held_out_states(path: Path = OUT_PATH) -> dict[str, dict[str, Any]]:
    """``{state: row}`` for states held out in the results file (empty if it is absent)."""
    path = Path(path)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("states") or {}
    return {str(k).upper(): v for k, v in rows.items() if (v or {}).get("held_out")}
