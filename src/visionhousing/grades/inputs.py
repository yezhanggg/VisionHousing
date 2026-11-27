"""Gather what the grade rule scores, from the run, the pack and the files on disk.

Plain-language summary
----------------------
The rule (``grades.rule``) only does arithmetic; this module finds its inputs,
read-only, for one number (a reform's state headline or one county's figure):

- **sources**: the record's own ``source_ids``, each classed by the source
  registry's tier, or as "not in the pack" when the state's pack manifest did
  not fetch it, or as a published study / benchmark when it is not a data source;
- **back-test**: the baseline back-test by kind of place
  (``baseline.places``), for the county's place type (the grouping the baseline
  itself uses, ``baseline.contest.place_info_for_context``), or all counties for
  a state number;
- **law coverage**: the verified local-law coverage tables the engine loaded
  (``StateContext.local_law.coverage``): lots or residents read; plus whether a
  state-law reading is on file for the reform's legal-status parameter;
- **calibration**: for each of the reform's key parameters, local / transferred
  / sourced / judgement, from ``assumptions/calibration/*.yaml``,
  ``assumptions/transfer.yaml`` and the ledger entry's kind;
- **reform evidence**: the best identification among verified outside records
  (``assumptions/evidence/states/`` and the retrospective panel). The panel is
  read from the treatment-date audit (``retro_audit_results.yaml``, its
  ``records``: the audited variant, the panel's reference record by the audit's
  pre-registered reading rule; it moves Saint Paul's first treated year to 2024).
  When that file is absent or holds no records, the original run
  (``retro_results.yaml``) is read instead and a warning is logged;
  :func:`retro_source` says which file was used. Its ``pools`` entry names
  both comparison pools of the panel with their labels: the original pool (as
  pre-registered, the one graded) and today's pool (``retro_current_pool_results.yaml``,
  an update shown beside it and never graded, by that update's pre-registered
  reading rule).

Nothing here changes a number; every value it returns comes from those files
and the engine result.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from visionhousing.ledger.loader import ROOT

__all__ = [
    "CALIBRATION_DIR",
    "RETRO_AUDIT_PATH",
    "RETRO_CURRENT_POOL_PATH",
    "RETRO_PATH",
    "backtest_rows",
    "best_evidence",
    "calibration_statuses",
    "law_inputs",
    "pack_sources",
    "retro_pools",
    "retro_source",
    "source_classes",
    "state_law_on_file",
]

LOG = logging.getLogger(__name__)
CALIBRATION_DIR = ROOT / "assumptions" / "calibration"
RETRO_AUDIT_PATH = ROOT / "assumptions" / "evidence" / "retro_audit_results.yaml"
RETRO_PATH = ROOT / "assumptions" / "evidence" / "retro_results.yaml"  # fallback (original run)
RETRO_CURRENT_POOL_PATH = ROOT / "assumptions" / "evidence" / "retro_current_pool_results.yaml"
TIER_CLASS = {2: "local", 1: "multi_state", 0: "national"}


# ---------------------------------------------------------------- sources


@lru_cache(maxsize=1)
def _registry_tiers() -> dict[str, int]:
    from visionhousing.data.registry import load_registry

    return {k: int(v.tier) for k, v in load_registry().items()}


def pack_sources(folder: Path | None) -> frozenset[str] | None:
    """Source ids the state's pack manifest fetched, or ``None`` with no manifest (Testland)."""
    if folder is None:
        return None
    path = Path(folder) / "pack_manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = {str(i.get("source")) for i in data.get("inputs", []) if i.get("source")}
    ids |= {str(p.get("id")) for p in data.get("provenance", []) if p.get("id")}
    return frozenset(ids)


def source_classes(
    source_ids: Iterable[str], in_pack: frozenset[str] | None = None
) -> list[tuple[str, str]]:
    """Each source id with its class.

    Classes: ``local``, ``multi_state``, ``national``, ``study``, ``not_in_pack``.

    Args:
        source_ids: The record's source ids.
        in_pack: Source ids the pack fetched (``None``: do not check, as for Testland).
    """
    tiers = _registry_tiers()
    out = []
    for sid in sorted(set(source_ids)):
        if sid in tiers:
            # A registry source that a local pack did not fetch cannot stand behind a
            # number; national (tier 0) sources are always fetched with the pack.
            fetched = in_pack is None or sid in in_pack or tiers[sid] == 0
            out.append((sid, TIER_CLASS[tiers[sid]] if fetched else "not_in_pack"))
        else:
            out.append((sid, "study"))
    return out


# ---------------------------------------------------------------- back-test


def backtest_rows(ctx: Any, result: Any, folder: Path | None) -> dict[str, Any]:
    """The back-test coverage for the state and for each county's place type.

    Returns:
        ``{"state": row, "counties": {fips: row}, "grouping": "typology" | "size_class"}``
        where a row is ``{"group", "coverage", "cases", "counties"}`` or ``None``.
        Empty rows when the state has no back-test (too few years).
    """
    from visionhousing.baseline.contest import place_info_for_context
    from visionhousing.baseline.panel import to_panel
    from visionhousing.baseline.places import place_backtest_for_context

    try:
        rc = result.run_context
        pb = place_backtest_for_context(ctx, rc.baseline.rule, folder).to_dict()
        info = place_info_for_context(ctx)
        panel = to_panel(ctx.permits)
    except Exception:  # a state without the history the back-test needs
        return {"state": None, "counties": {}, "grouping": None}

    def row(r: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if r is None:
            return None
        cov = r.get("coverage")
        return {
            "group": r.get("phrase") or r.get("group"),
            "coverage": None if cov is None or not math.isfinite(float(cov)) else float(cov),
            "cases": int(r.get("interval_cases") or 0),
            "counties": int(r.get("counties") or 0),
        }

    groups = pb.get("groups", [])
    state = next((g for g in groups if g.get("dimension") == "all"), None)
    lookup = {(g.get("dimension"), g.get("group")): g for g in groups}
    counties = {}
    for i, c in enumerate(panel.counties):
        counties[str(c)] = row(lookup.get((info.source, info.groups[i])))
    return {"state": row(state), "counties": counties, "grouping": info.source}


# ---------------------------------------------------------------- law


def law_inputs(ctx: Any, measure: str, county: str | None = None) -> dict[str, Any]:
    """Share of lots (or residents) under verified local reads, for the state or one county.

    Returns:
        ``{"measure", "read", "total", "share"}``; ``share`` is ``None`` when the
        state has no law tables (nothing read).
    """
    law = getattr(ctx, "local_law", None)
    cov = getattr(law, "coverage", None)
    lots = measure == "lots"
    col, read_col = ("sf_lots", "sf_lots_read") if lots else ("residents", "residents_read")
    if cov is None or len(cov) == 0 or col not in cov.columns:
        return {"measure": measure, "read": None, "total": None, "share": None}
    rows = cov if county is None else cov[cov["county"].astype(str) == str(county)]
    total = float(rows[col].sum(skipna=True))
    read = float(rows[read_col].sum(skipna=True))
    share = read / total if total > 0 and math.isfinite(total) else None
    return {"measure": measure, "read": read, "total": total, "share": share}


@lru_cache(maxsize=8)
def _calibration_files(stamp: tuple[tuple[str, int], ...]) -> tuple[dict[str, Any], ...]:
    out = []
    for name, _ in stamp:
        data = yaml.safe_load((CALIBRATION_DIR / name).read_text(encoding="utf-8")) or {}
        out.append(
            {
                "file": name,
                "id": str(data.get("id", name)),
                "state": str(data.get("state", "")).upper(),
                "parameter": data.get("parameter"),
                "status": str(data.get("status", "")),
                "kind": str(data.get("kind", "")),
            }
        )
    return tuple(out)


def calibration_files() -> tuple[dict[str, Any], ...]:
    """Header fields of every calibration file (id, state, parameter, status, kind)."""
    files = sorted(CALIBRATION_DIR.glob("*.yaml")) if CALIBRATION_DIR.is_dir() else []
    return _calibration_files(tuple((p.name, p.stat().st_mtime_ns) for p in files))


def state_law_on_file(state: str, parameter: str | None) -> str | None:
    """Id of a state-law reading (``kind: judgement``) for ``parameter`` in ``state``, or None."""
    if not parameter:
        return None
    for f in calibration_files():
        if f["parameter"] == parameter and f["state"] == state and f["kind"] == "judgement":
            return f["id"]
    return None


# ---------------------------------------------------------------- calibration


def calibration_statuses(
    state: str, parameters: Mapping[str, str], ledger: Any
) -> list[dict[str, Any]]:
    """Local / transferred / sourced / judgement for each key parameter.

    Order of the checks: a calibration file for the parameter made in this
    state (and not marked transferred) is local; a transfer rule
    (``assumptions/transfer.yaml``) is local when the state is in its
    ``measured_in``, otherwise transferred; else the ledger entry's kind
    (``calibrated`` local, ``sourced``/``benchmark`` sourced, else judgement).
    """
    from visionhousing.engine.context import load_transfer_rules

    rules = {str(r["parameter"]): r for r in load_transfer_rules()}
    files = calibration_files()
    out = []
    for pid, label in parameters.items():
        status, where, ref = None, None, None
        for f in files:
            if f["parameter"] == pid and f["state"] == state and "transferred" not in f["status"]:
                status, where, ref = "local", state, f["id"]
                break
        if status is None and pid in rules:
            r = rules[pid]
            measured = [str(s).upper() for s in r.get("measured_in", [])]
            ref = str(r.get("calibration", "transfer.yaml"))
            if state in measured:
                status, where = "local", state
            else:
                status, where = "transferred", ", ".join(measured)
        if status is None:
            kind = ledger[pid].kind if pid in ledger else "judgement"
            status = {"calibrated": "local", "sourced": "sourced", "benchmark": "sourced"}.get(
                kind, "judgement"
            )
            ref = f"ledger {pid} ({kind})"
        out.append({"parameter": pid, "label": label, "status": status, "where": where, "ref": ref})
    return out


# ---------------------------------------------------------------- reform evidence


def _raw_records(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("records", []) or [])


def retro_source() -> dict[str, Any]:
    """Which retrospective-panel file the grades read, and why.

    Returns:
        ``{"file", "variant", "fallback"}``: the audited records
        (``variant: "audited"``) when ``retro_audit_results.yaml`` holds any;
        else the original run (``variant: "original"``, ``fallback: True``);
        ``file: None`` when neither file is on disk.
    """
    pools = retro_pools()
    if _raw_records(RETRO_AUDIT_PATH):
        out = {"file": _rel(RETRO_AUDIT_PATH), "variant": "audited", "fallback": False}
    elif RETRO_PATH.is_file():
        out = {"file": _rel(RETRO_PATH), "variant": "original", "fallback": True}
    else:
        out = {"file": None, "variant": None, "fallback": True}
    return {**out, "pools": pools}


@lru_cache(maxsize=4)
def _pool_sizes(stamp: tuple[int, ...]) -> tuple[int | None, int | None]:
    def size(path: Path) -> int | None:
        if not path.is_file():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pool = ((data.get("provenance") or {}).get("panel") or {}).get("pool")
        return int(pool) if pool is not None else None

    return size(RETRO_AUDIT_PATH), size(RETRO_CURRENT_POOL_PATH)


def retro_pools() -> list[dict[str, Any]]:
    """Both comparison pools of the retrospective panel, labelled, with which one is graded.

    The original pool (as pre-registered; ``retro_audit_results.yaml``) is the
    published record and the one the grades read. Today's pool
    (``retro_current_pool_results.yaml``) is a pre-registered update shown
    beside it and never graded. Pool sizes are read from each file's
    provenance; a file not on disk is left out.
    """
    from visionhousing.evidence.retro_current_pool import pool_label

    stamp = tuple(
        p.stat().st_mtime_ns if p.is_file() else 0
        for p in (RETRO_AUDIT_PATH, RETRO_CURRENT_POOL_PATH)
    )
    original, current = _pool_sizes(stamp)
    out = []
    if original is not None:
        out.append(
            {
                "pool": "original",
                "label": pool_label("original", original),
                "offices": original,
                "file": _rel(RETRO_AUDIT_PATH),
                "graded": True,
            }
        )
    if current is not None:
        out.append(
            {
                "pool": "current",
                "label": pool_label("current", current),
                "offices": current,
                "file": _rel(RETRO_CURRENT_POOL_PATH),
                "graded": False,
            }
        )
    return out


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _retro_records() -> list[Any]:
    from visionhousing.evidence.schema import ComparisonRecord

    raws = _raw_records(RETRO_AUDIT_PATH)
    if not raws:
        LOG.warning(
            "audited retro results missing or empty (%s); grading from the original run (%s)",
            _rel(RETRO_AUDIT_PATH),
            _rel(RETRO_PATH),
        )
        raws = _raw_records(RETRO_PATH)
    out = []
    for raw in raws:
        try:
            out.append(ComparisonRecord.model_validate(raw))
        except ValueError as exc:  # a record the schema rejects is not evidence
            LOG.warning("retro record skipped: %s", str(exc)[:120])
    return out


@lru_cache(maxsize=4)
def _evidence(stamp: tuple[int, ...]) -> tuple[Any, ...]:
    from visionhousing.evidence.records import load_panel

    return (*load_panel().records, *_retro_records())


def _evidence_records() -> tuple[Any, ...]:
    from visionhousing.evidence.records import PANEL_DIR

    files = [*sorted(PANEL_DIR.glob("*.yaml")), RETRO_AUDIT_PATH, RETRO_PATH]
    return _evidence(tuple(p.stat().st_mtime_ns for p in files if p.is_file()))


def best_evidence(policy_type: str, scores: Mapping[str, float]) -> dict[str, Any]:
    """The best identification on file for a policy type, with the counts behind it.

    A verified record with an observed result counts at its identification
    (``not_applicable`` counts as descriptive); a verified record without one
    counts as ``law_only``; unverified records do not count.
    """
    mine = [r for r in _evidence_records() if policy_type in r.policy_types]
    verified = [r for r in mine if r.verified]
    best, ids = "none", []
    for r in verified:
        if r.observed_result is not None:
            ident = r.identification if r.identification != "not_applicable" else "descriptive"
        else:
            ident = "law_only"
        if float(scores[ident]) > float(scores[best]):
            best, ids = ident, [r.id]
        elif ident == best and ident != "none":
            ids.append(r.id)
    return {
        "policy_type": policy_type,
        "best": best,
        "records": len(mine),
        "verified": len(verified),
        "with_observed_result": sum(1 for r in verified if r.observed_result is not None),
        "record_ids": sorted(ids)[:8],
        "retro": retro_source(),
    }
