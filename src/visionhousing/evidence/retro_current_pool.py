"""The audited retrospective panel re-scored on today's comparison pool (a named update).

Plain-language summary
----------------------
The retrospective panel (:mod:`visionhousing.evidence.retro`) compares permits
in reformed cities with ordinary large cities. "Ordinary" leaves out every
state with a statewide ADU or middle-housing law in force by 2025-12-31. When
the panel first ran, that rule left 81 comparison offices. Records added since
then put nine more states on the list, so the same rule gives 68 today.

The owner kept both. The 81-office result of the treatment-date audit
(:mod:`visionhousing.evidence.retro_audit`) stays the published record and the
one the grades read. This module re-scores the same audit on today's 68-office
pool and writes it to a separate file,
``assumptions/evidence/retro_current_pool_results.yaml``, labelled as an update.

Its rules are block ``retro_current_pool`` in
``assumptions/evidence/retro_current_pool.yaml``, fingerprinted before any
estimate on the new pool. The block freezes the excluded-state list, keeps the
audit's four variants, adds ``original_design`` (the first run's treatment
years and comparison rule on the new pool, so the pool change can be told apart
from the date change), and fixes in advance what counts as "the conclusions
change". :func:`run_current_pool` checks all three fingerprints, re-scores, and
writes a per-case and pooled before/after table where "before" is the audited
result on the original pool.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from visionhousing.evidence import retro, retro_audit
from visionhousing.evidence.records import load_panel

__all__ = [
    "BLOCK",
    "LABEL_CURRENT",
    "LABEL_ORIGINAL",
    "OUT_PATH",
    "POOL_PATH",
    "check_current_pool",
    "conclusions",
    "load_block_file",
    "pool_label",
    "run_current_pool",
    "side_of_zero",
]

POOL_PATH = retro.ROOT / "assumptions" / "evidence" / "retro_current_pool.yaml"
OUT_PATH = retro.ROOT / "assumptions" / "evidence" / "retro_current_pool_results.yaml"
BLOCK = "retro_current_pool"
"""The update's name in ``assumptions/preregistration.yaml``."""

LABEL_ORIGINAL = "original pool ({n}, as pre-registered)"
LABEL_CURRENT = "current pool ({n}, update)"

HEADER = (
    "# Retrospective panel: the audited design on today's comparison pool (generated; do not\n"
    "# edit). Written by `visionhousing evidence-retro current-pool` from the pre-registered\n"
    "# block in assumptions/evidence/retro_current_pool.yaml (retro_current_pool). An UPDATE\n"
    "# shown beside the published record (retro_audit_results.yaml, original pool); it never\n"
    "# replaces it and the grades do not read it. Evidence only: nothing here enters a model\n"
    "# run. Rates are units authorized per 1,000 housing units per year.\n\n"
)


def pool_label(kind: str, n: int) -> str:
    """The display label of a pool: ``kind`` is ``"original"`` or ``"current"``."""
    return (LABEL_ORIGINAL if kind == "original" else LABEL_CURRENT).format(n=int(n))


def load_block_file(path: Path = POOL_PATH) -> dict[str, Any]:
    """Read ``assumptions/evidence/retro_current_pool.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def side_of_zero(ci: list[float]) -> str:
    """``"zero"`` when the interval includes zero, else ``"above"`` or ``"below"``."""
    lo, hi = ci
    return "zero" if lo <= 0 <= hi else ("above" if lo > 0 else "below")


def conclusions(
    before: Mapping[str, Any], after: Mapping[str, Any], pass_mark: float
) -> dict[str, Any]:
    """Apply the block's "conclusions change" rule to two audited results (primary outcome).

    ``before`` and ``after`` are ``{"cases": {record: summary}, "pooled": summary}``.
    A conclusion changes when a case's interval changes its side of zero or its
    identification label, the pooled interval changes its side of zero, or the
    pooled pre-trend p crosses ``pass_mark``.
    """
    changes = []
    for rid, b in before["cases"].items():
        a = after["cases"][rid]
        if side_of_zero(b["ci"]) != side_of_zero(a["ci"]):
            changes.append(f"{rid}: interval {side_of_zero(b['ci'])} -> {side_of_zero(a['ci'])}")
        if b["identification"] != a["identification"]:
            changes.append(f"{rid}: label {b['identification']} -> {a['identification']}")
    pb, pa = before["pooled"], after["pooled"]
    if side_of_zero(pb["ci"]) != side_of_zero(pa["ci"]):
        changes.append(f"pooled: interval {side_of_zero(pb['ci'])} -> {side_of_zero(pa['ci'])}")
    pre_b, pre_a = pb.get("pretrend_p"), pa.get("pretrend_p")
    if (
        pre_b is not None
        and pre_a is not None
        and (float(pre_b) >= pass_mark) != (float(pre_a) >= pass_mark)
    ):
        changes.append(f"pooled: pre-trend p {pre_b:.3f} -> {pre_a:.3f} crosses {pass_mark}")
    return {"change": bool(changes), "changes": changes}


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _primary(doc: Mapping[str, Any], primary: str) -> dict[str, Any]:
    return {
        "cases": {rid: o[primary] for rid, o in doc["cases"].items()},
        "pooled": doc["pooled"][primary],
    }


def run_current_pool(
    raw: Path = retro.RAW_DIR,
    out: Path = OUT_PATH,
    *,
    draws: int | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Check the three fingerprints, re-score every variant on today's pool and write the file.

    Raises:
        PreregistrationError: The design, the audit block or this block changed.
        SystemExit: A case definition fails, or the frozen pool is not the declared size.
    """
    from visionhousing.validation.preregistration import verify

    today = today or date.today()
    design = retro.load_design()
    audit = retro_audit.load_audit()
    mine = load_block_file()
    checks = [
        verify(retro.PREREG_BLOCK, design),
        verify(retro_audit.BLOCK, audit),
        verify(BLOCK, mine),
    ]
    ablock, block = audit[retro_audit.BLOCK], mine[BLOCK]
    records = load_panel()
    reforms = records.by_id()
    problems = retro_audit.check_definitions(ablock, design, reforms)
    if problems:
        raise SystemExit("retro current pool: " + "; ".join(problems))
    excluded = set(block["pool"]["excluded_states"])
    excluded_today = retro.excluded_states(design, records.records)
    first, last = design["data"]["years"]
    offices, files = retro.fetch_place_years(range(first, last + 1), raw)
    housing, hu_log = retro.fetch_housing_units(raw)
    audit_doc = yaml.safe_load(retro_audit.OUT_PATH.read_text(encoding="utf-8"))
    original_pool = int(audit_doc["provenance"]["panel"]["pool"])
    primary = design["outcomes"]["primary"]

    after: dict[str, Any] = {}
    records_out: list[dict[str, Any]] = []
    panel_summary: dict[str, Any] = {}
    for name, v in block["variants"].items():
        g = None
        if v["g"] != "design":
            g = {c["record"]: int(c["g"][v["g"]]) for c in ablock["cases"]}
        panel, panel_summary = retro.build_panel(offices, housing, design, excluded, g_override=g)
        if panel_summary["pool"] != int(block["pool"]["expected_pool"]):
            raise SystemExit(
                f"retro current pool: pool is {panel_summary['pool']}, "
                f"declared {block['pool']['expected_pool']}"
            )
        res = retro.run_estimates(
            panel,
            design,
            reforms,
            today=today,
            draws=draws,
            not_yet_treated=v["comparisons"] != "never_treated_only",
        )
        after[name] = retro_audit._by_case(res)
        if name == "audited":
            records_out = res["records"]

    before = audit_doc["variants"]["audited"]  # the published record: audited, original pool
    table = retro_audit.before_after(before, after, ablock, primary)
    for row in table:  # relabel: here "before" is the original pool, not the first run
        row["before_label"] = pool_label("original", original_pool)
        row["after_label"] = pool_label("current", panel_summary["pool"])
    pass_mark = float(design["thresholds"]["pretrend_p_min"])
    verdict = conclusions(_primary(before, primary), _primary(after["audited"], primary), pass_mark)
    current_n = int(panel_summary["pool"])
    doc = {
        "version": 1,
        "id": "retro_current_pool_results",
        "label": "Retrospective panel: audited design on today's comparison pool (update)",
        "use": "evidence only; never a multiplier on a model run",
        "role": (
            "update shown beside the published record; the published record and the grades' "
            "source is assumptions/evidence/retro_audit_results.yaml (original pool)"
        ),
        "pools": {
            "original": {
                "label": pool_label("original", original_pool),
                "pool": original_pool,
                "file": "assumptions/evidence/retro_audit_results.yaml",
                "excluded_states": sorted(audit_doc["provenance"]["panel"]["excluded_states"]),
                "role": "published record (graded)",
            },
            "current": {
                "label": pool_label("current", current_n),
                "pool": current_n,
                "file": "assumptions/evidence/retro_current_pool_results.yaml",
                "excluded_states": sorted(excluded),
                "role": "update (not graded)",
            },
        },
        "provenance": {
            "measured_on": str(today),
            "generated_by": "visionhousing evidence-retro current-pool",
            "block_file": "assumptions/evidence/retro_current_pool.yaml",
            "block_sha256": _sha(POOL_PATH),
            "audit_file": "assumptions/evidence/retro_audit.yaml",
            "audit_sha256": _sha(retro_audit.AUDIT_PATH),
            "design_file": "assumptions/evidence/retro_design.yaml",
            "design_sha256": _sha(retro.DESIGN_PATH),
            "before_file": "assumptions/evidence/retro_audit_results.yaml",
            "before_sha256": _sha(retro_audit.OUT_PATH),
            "preregistration": checks,
            "method": "docs/modules/evidence_retro.md (Current-pool update)",
            "panel": panel_summary,
            "excluded_states_frozen": sorted(excluded),
            "excluded_states_today": sorted(excluded_today),
            "frozen_list_matches_today": sorted(excluded) == sorted(excluded_today),
            "bps_place_files": len(files),
            "bps_sha256_of_file_hashes": hashlib.sha256(
                "".join(f["sha256"] for f in files).encode()
            ).hexdigest(),
            "acs": dict(hu_log),
        },
        "primary_outcome": primary,
        "reading_rule": block["reading_rule"],
        "conclusions": verdict,
        "before_after": table,
        "pooled": {
            "before": before["pooled"][primary],
            **{v: after[v]["pooled"][primary] for v in after},
        },
        "variants": after,
        "before": before,
        "records": records_out,
        "caveats": list(block["caveats"]),
    }
    text = yaml.safe_dump(retro._clean(doc), sort_keys=False, width=100, allow_unicode=False)
    Path(out).write_text(HEADER + text, encoding="utf-8")
    return doc


def check_current_pool(
    path: Path = OUT_PATH, registry: Mapping[str, Any] | None = None
) -> list[str]:
    """Check the update file: fingerprints, file hashes, pool sizes and record format."""
    from visionhousing.evidence.schema import ComparisonRecord
    from visionhousing.validation.preregistration import PreregistrationError, verify

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    prov = data.get("provenance") or {}
    problems = []
    for key, file in (
        ("block_sha256", POOL_PATH),
        ("audit_sha256", retro_audit.AUDIT_PATH),
        ("design_sha256", retro.DESIGN_PATH),
        ("before_sha256", retro_audit.OUT_PATH),
    ):
        if prov.get(key) != _sha(file):
            problems.append(
                f"{key} does not match {file.name} now: run `evidence-retro current-pool`"
            )
    for name, doc in (
        (retro.PREREG_BLOCK, retro.load_design()),
        (retro_audit.BLOCK, retro_audit.load_audit()),
        (BLOCK, load_block_file()),
    ):
        try:
            verify(name, doc, registry)
        except PreregistrationError as err:
            problems.append(str(err))
    declared = int(load_block_file()[BLOCK]["pool"]["expected_pool"])
    if (prov.get("panel") or {}).get("pool") != declared:
        problems.append(f"pool is not the declared {declared}")
    for raw in data.get("records", []):
        try:
            ComparisonRecord.model_validate(raw)
        except Exception as err:
            problems.append(f"{raw.get('id')}: {err}")
    return problems
