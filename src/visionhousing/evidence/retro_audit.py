"""Treatment-date audit of the retrospective reform panel: which law, which date, which year.

Plain-language summary
----------------------
The retrospective panel (:mod:`visionhousing.evidence.retro`) compares permits
in cities that legalised 2-4 unit homes with ordinary cities. Its answer
depends on the year each reform is taken to start (the *treatment year*
``g``). Two cases needed a closer look:

* **Saint Paul** was scored from its first phase (Ordinance 22-1, effective
  2022-03-05: small homes, ADUs, small lots). The 2-4 unit rule is the second
  phase (Ordinance 23-43, effective 2023-11-26).
* **Portland** was scored at ``g = 2022``. Its first Residential Infill phase
  took effect 2021-08-01 and its second (RIP2) on 2022-06-30; the year
  convention that turns those dates into 2022 was not shown beside the result.

The audit's rules live in ``assumptions/evidence/retro_audit.yaml`` (block
``retro_treatment_audit``), written and fingerprinted before any re-score. They
name each case's law and phase, publish the year convention (a reform that
starts after June 30 counts from the next year; the months before that fall in
the last base year), and list four variants:

* ``audited`` (the reference): the corrected treatment years, comparing only
  with cities that never reformed;
* ``dates_only``: the same years with the original comparison rule, so the
  date change and the comparison change can be told apart;
* ``any_exposure``: a partial year counts as treated;
* ``first_full_year_all_phases``: the first year every phase was in force all
  year (moves Portland to 2023).

:func:`run_audit` checks both fingerprints (the original design's and the
audit's), re-runs every outcome for every case under each variant with the
original estimators, and writes ``assumptions/evidence/retro_audit_results.yaml``
with a before/after table. "Before" is read from the original results file,
which is left unchanged. Intervals that include zero are kept and labelled.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from visionhousing.evidence import retro
from visionhousing.evidence.records import load_panel

__all__ = [
    "AUDIT_PATH",
    "BLOCK",
    "OUT_PATH",
    "before_after",
    "check_audit",
    "check_definitions",
    "estimate_summary",
    "load_audit",
    "robust_to_convention",
    "run_audit",
]

AUDIT_PATH = retro.ROOT / "assumptions" / "evidence" / "retro_audit.yaml"
OUT_PATH = retro.ROOT / "assumptions" / "evidence" / "retro_audit_results.yaml"
BLOCK = "retro_treatment_audit"
"""The audit's name in ``assumptions/preregistration.yaml``."""

HEADER = (
    "# Retrospective panel: treatment-date audit results (generated; do not edit).\n"
    "# Written by `visionhousing evidence-retro audit` from the pre-registered block in\n"
    "# assumptions/evidence/retro_audit.yaml (retro_treatment_audit) and the original\n"
    "# design. Evidence only: nothing here enters a model run. Rates are units authorized\n"
    "# per 1,000 housing units per year. Method: docs/modules/evidence_retro.md.\n\n"
)


def load_audit(path: Path = AUDIT_PATH) -> dict[str, Any]:
    """Read ``assumptions/evidence/retro_audit.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def check_definitions(
    block: Mapping[str, Any],
    design: Mapping[str, Any],
    reforms: Mapping[str, Any] | None = None,
) -> list[str]:
    """Check the audit's case definitions against the design, the records and its own convention.

    Every audit case must be a design case; ``audited`` must follow the June-30
    rule and ``any_exposure`` the calendar year of the effective date; and, when
    ``reforms`` (record id -> verified record) is given, the effective date must
    be one of the record's dates. Returns the problems found (empty when fine).
    """
    problems = []
    design_ids = {c["record"] for c in design["cases"]}
    seen = set()
    for case in block["cases"]:
        rid, eff = case["record"], _as_date(case["effective_date"])
        seen.add(rid)
        if rid not in design_ids:
            problems.append(f"{rid}: not a case of the original design")
        g = case["g"]
        if int(g["audited"]) != retro.treatment_year(eff):
            problems.append(f"{rid}: audited g {g['audited']} breaks the June-30 rule")
        if int(g["any_exposure"]) != eff.year:
            problems.append(f"{rid}: any_exposure g {g['any_exposure']} is not {eff.year}")
        if int(g["first_full_year_all_phases"]) < int(g["audited"]):
            problems.append(f"{rid}: first full year before the audited year")
        if reforms is not None:
            rec = reforms.get(rid)
            if rec is None or not rec.verified:
                problems.append(f"{rid}: needs a verified record")
            elif eff not in [e.date for e in rec.effective_dates]:
                problems.append(f"{rid}: effective date {eff} not in its record")
    missing = design_ids - seen
    if missing:
        problems.append(f"design cases missing from the audit: {sorted(missing)}")
    return problems


def estimate_summary(est: Mapping[str, Any]) -> dict[str, Any]:
    """The few numbers a before/after row needs, from one case-outcome estimate."""
    lo, hi = (float(x) for x in est["ci"])
    return {
        "g": int(est["g"]),
        "post_years": int(est["post_years"]),
        "att": float(est["att"]),
        "ci": [lo, hi],
        "includes_zero": bool(lo <= 0 <= hi),
        "p_value": est.get("p_value"),
        "pretrend_p": est.get("pretrend_p"),
        "placebo_date_p": (est.get("placebo_date") or {}).get("p"),
        "identification": est.get("identification"),
        "label_reasons": list(est.get("label_reasons") or []),
        "comparisons": est.get("comparisons"),
    }


def _pooled_summary(p: Mapping[str, Any]) -> dict[str, Any]:
    lo, hi = (float(x) for x in p["ci"])
    return {
        "overall": float(p["overall"]),
        "ci": [lo, hi],
        "includes_zero": bool(lo <= 0 <= hi),
        "pretrend_p": p.get("pretrend_p"),
        "treated": p.get("treated"),
        "comparisons": p.get("comparisons"),
    }


def _by_case(results: Mapping[str, Any]) -> dict[str, Any]:
    """``{record: {outcome: summary}}`` and the pooled rows of one results document."""
    cases = {
        c["record"]: {o: estimate_summary(e) for o, e in c["estimates"].items()}
        for c in results["cases"]
    }
    pooled = {o: _pooled_summary(p) for o, p in results["pooled"].items()}
    return {"cases": cases, "pooled": pooled}


def robust_to_convention(rows: Mapping[str, Mapping[str, Any]], variants: list[str]) -> bool:
    """True when every listed variant's interval has the same relation to zero.

    "Same relation" means all include zero, or all lie above it, or all below it.
    """

    def side(r: Mapping[str, Any]) -> str:
        lo, hi = r["ci"]
        return "zero" if lo <= 0 <= hi else ("above" if lo > 0 else "below")

    sides = {side(rows[v]) for v in variants if v in rows}
    return len(sides) == 1


def before_after(
    before: Mapping[str, Any],
    after: Mapping[str, Mapping[str, Any]],
    block: Mapping[str, Any],
    outcome: str,
) -> list[dict[str, Any]]:
    """One row per case: the original estimate next to every audit variant, for ``outcome``."""
    sens = ["audited", "any_exposure", "first_full_year_all_phases"]
    rows = []
    for case in block["cases"]:
        rid = case["record"]
        variants = {v: after[v]["cases"][rid][outcome] for v in after}
        b = before["cases"].get(rid, {}).get(outcome)
        a = variants["audited"]
        rows.append(
            {
                "record": rid,
                "name": case["name"],
                "treatment": case["treatment"],
                "effective_date": str(case["effective_date"]),
                "before": b,
                "after": a,
                "g_changed": bool(b is not None and b["g"] != a["g"]),
                "variants": variants,
                "robust_to_convention": robust_to_convention(variants, sens),
            }
        )
    return rows


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_audit(
    raw: Path = retro.RAW_DIR,
    out: Path = OUT_PATH,
    *,
    draws: int | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Check both fingerprints, re-score every variant and write the before/after file.

    Raises:
        PreregistrationError: The original design or the audit block changed.
        SystemExit: A case definition fails :func:`check_definitions`.
    """
    from visionhousing.validation.preregistration import verify

    today = today or date.today()
    design = retro.load_design()
    audit = load_audit()
    checks = [verify(retro.PREREG_BLOCK, design), verify(BLOCK, audit)]
    block = audit[BLOCK]
    records = load_panel()
    reforms = records.by_id()
    problems = check_definitions(block, design, reforms)
    if problems:
        raise SystemExit("retro audit: " + "; ".join(problems))
    first, last = design["data"]["years"]
    offices, files = retro.fetch_place_years(range(first, last + 1), raw)
    housing, hu_log = retro.fetch_housing_units(raw)
    before_doc = yaml.safe_load(retro.RESULTS_PATH.read_text(encoding="utf-8"))
    # The audit keeps the original run's pool (its caveats say so): the excluded
    # states are the ones the original run recorded, not today's list, so only the
    # treatment years and the comparison rule differ between before and after.
    excluded = set(before_doc["provenance"]["panel"]["excluded_states"])
    excluded_today = retro.excluded_states(design, records.records)
    data_hash = hashlib.sha256("".join(f["sha256"] for f in files).encode()).hexdigest()
    before_hash = next(
        s.get("sha256_of_file_hashes")
        for s in before_doc["provenance"]["sources"]
        if s.get("id") == "bps_place_annual"
    )
    primary = design["outcomes"]["primary"]
    # Reproduction check: the original design, unchanged, on the same pool and files.
    panel0, summary0 = retro.build_panel(offices, housing, design, excluded)
    again = _by_case(retro.run_estimates(panel0, design, reforms, today=today, draws=draws))
    before_cases = _by_case(before_doc)["cases"]
    diffs = [
        abs(again["cases"][rid][o]["att"] - before_cases[rid][o]["att"])
        for rid in before_cases
        for o in before_cases[rid]
    ]
    reproduction = {
        "what": (
            "The original design re-run on the original pool and files; its estimates must "
            "match the published 'before' record (rounding to 4 decimals)."
        ),
        "pool": summary0["pool"],
        "same_bps_files": data_hash == before_hash,
        "max_abs_diff_att": max(diffs),
        "matches": bool(data_hash == before_hash and max(diffs) < 1e-3),
    }
    after: dict[str, Any] = {}
    audited_records: list[dict[str, Any]] = []
    panel_summary: dict[str, Any] = {}
    for name, v in block["variants"].items():
        g = {c["record"]: int(c["g"][v["g"]]) for c in block["cases"]}
        panel, panel_summary = retro.build_panel(offices, housing, design, excluded, g_override=g)
        res = retro.run_estimates(
            panel,
            design,
            reforms,
            today=today,
            draws=draws,
            not_yet_treated=v["comparisons"] != "never_treated_only",
        )
        after[name] = _by_case(res)
        if name == "audited":
            audited_records = res["records"]
    before = _by_case(before_doc)
    table = before_after(before, after, block, primary)
    doc = {
        "version": 1,
        "id": "retro_treatment_audit_results",
        "label": "Retrospective panel: treatment-date audit (before and after)",
        "use": "evidence only; never a multiplier on a model run",
        "provenance": {
            "measured_on": str(today),
            "generated_by": "visionhousing evidence-retro audit",
            "audit_file": "assumptions/evidence/retro_audit.yaml",
            "audit_sha256": _sha(AUDIT_PATH),
            "design_file": "assumptions/evidence/retro_design.yaml",
            "design_sha256": _sha(retro.DESIGN_PATH),
            "before_file": "assumptions/evidence/retro_results.yaml",
            "before_sha256": _sha(retro.RESULTS_PATH),
            "preregistration": checks,
            "method": "docs/modules/evidence_retro.md (Treatment-date audit)",
            "panel": panel_summary,
            "pool_frozen_to_original": {
                "excluded_states": sorted(excluded),
                "excluded_states_today": sorted(excluded_today),
                "note": (
                    "Today's verified statewide records exclude more states than the original "
                    "run did; the audit keeps the original run's list so that before and after "
                    "differ only in treatment years and the comparison rule."
                ),
            },
            "reproduction": reproduction,
            "bps_place_files": len(files),
            "acs": dict(hu_log),
        },
        "year_convention": block["year_convention"],
        "reading_rule": block["reading_rule"],
        "treatments": [
            {
                k: case.get(k)
                for k in (
                    "record",
                    "name",
                    "treatment",
                    "phase",
                    "effective_date",
                    "partial_year",
                    "other_phases",
                    "date_check",
                    "g",
                    "g_before",
                )
                if case.get(k) is not None
            }
            for case in block["cases"]
        ],
        "primary_outcome": primary,
        "before_after": table,
        "pooled": {
            "before": before["pooled"][primary],
            **{v: after[v]["pooled"][primary] for v in after},
        },
        "variants": after,
        "before": before,
        "records": audited_records,
        "caveats": list(block["caveats"]),
    }
    text = yaml.safe_dump(retro._clean(doc), sort_keys=False, width=100, allow_unicode=False)
    Path(out).write_text(HEADER + text, encoding="utf-8")
    return doc


def check_audit(path: Path = OUT_PATH, registry: Mapping[str, Any] | None = None) -> list[str]:
    """Check an audit results file: fingerprints, file hashes and record format."""
    from visionhousing.evidence.schema import ComparisonRecord
    from visionhousing.validation.preregistration import PreregistrationError, verify

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    prov = data.get("provenance") or {}
    problems = []
    for key, file in (
        ("audit_sha256", AUDIT_PATH),
        ("design_sha256", retro.DESIGN_PATH),
        ("before_sha256", retro.RESULTS_PATH),
    ):
        if prov.get(key) != _sha(file):
            problems.append(f"{key} does not match {file.name} now: run `evidence-retro audit`")
    for name, doc in ((retro.PREREG_BLOCK, retro.load_design()), (BLOCK, load_audit())):
        try:
            verify(name, doc, registry)
        except PreregistrationError as err:
            problems.append(str(err))
    for raw in data.get("records", []):
        try:
            ComparisonRecord.model_validate(raw)
        except Exception as err:
            problems.append(f"{raw.get('id')}: {err}")
    return problems
