"""State readiness: the five steps of the intake and release standard, each pass, fail or unknown.

Plain-language summary
----------------------
Before a state's results are published, five things must hold (the steps and
their wording are in ``assumptions/intake.yaml``):

1. **National inputs**: the pack has the national tables (counties, tracts,
   blocks, permits, costs, prices), the permit table keeps the Census
   Bureau's imputation flag, the boundary files and data tiers are recorded,
   and no input failed to load.
2. **Current law**: the state's law rows exist, carry effective dates, and at
   least one was read and verified.
3. **Local evidence**: some eligible lots sit under a verified local rule
   reading, a validation set holds the state out (the donor-pool test's
   held-out states, or the state confidence check in
   ``assumptions/calibration/state_holdouts.yaml``, whose coverage is shown
   but never gates), and at least one verified comparison record exists for
   the state.
4. **Benchmark adapter**: every input the engine needs has data or a ledger
   stand-in, borrowed ranges are widened for the state, and a feed failure is
   kept apart from a true zero.
5. **Publication**: the static site holds every benchmark run for the state,
   each with its three hashes and a likely range, and the pack has its hash.

Each check is ``pass`` (it holds), ``fail`` (it was checked and does not hold)
or ``unknown`` (it cannot be checked here, for example on the synthetic test
state, or when a file it reads is absent). A step fails if any check fails,
is unknown if none fails but one is unknown, and passes otherwise.

Command line::

    uv run visionhousing state-readiness --state PA
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from visionhousing.paths import safe_child

__all__ = ["STATUSES", "readiness", "to_text"]

STATUSES = ("pass", "fail", "unknown")


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _step_status(checks: list[dict[str, str]]) -> str:
    kinds = {c["status"] for c in checks}
    if "fail" in kinds:
        return "fail"
    if "unknown" in kinds or not checks:
        return "unknown"
    return "pass"


def _yes(ok: bool) -> str:
    return "pass" if ok else "fail"


def _national(folder: Path, manifest: Mapping[str, Any], settings: Mapping[str, Any]) -> list:
    from visionhousing.data.report_card import load_pack_files

    files = load_pack_files(folder)
    dictionary = files["dictionary"]
    synthetic = bool(dictionary.get("synthetic"))
    present = {p.name.split(".")[0] for p in folder.iterdir() if p.is_file()}
    wanted = list(settings.get("national_tables", []))
    lacking = [t for t in wanted if t not in present]
    out = [
        _check(
            "national tables",
            _yes(not lacking),
            "all present" if not lacking else f"absent: {', '.join(lacking)}",
        )
    ]
    permits = files["tables"].get("permits_county_year")
    flag = permits is not None and "imputed" in permits.columns
    out.append(_check("permit reporting flag", _yes(flag), "imputed column kept" if flag else
                      "no imputation flag in permits_county_year"))  # fmt: skip
    if synthetic:
        out.append(_check("boundary vintage", "unknown", "synthetic test state"))
    else:
        ids = {p["id"] for p in (files["manifest"] or {}).get("provenance", [])}
        bounds = sorted(i for i in ids if i.startswith(("cb_", "tiger_")))
        out.append(_check("boundary vintage", _yes(bool(bounds)), ", ".join(bounds) or "none"))
    has_tiers = bool(dictionary.get("tiers")) or synthetic
    out.append(_check("data tiers", _yes(has_tiers), "recorded in the data dictionary"
                      if has_tiers else "no tiers in the data dictionary"))  # fmt: skip
    failed = manifest.get("report_card_value_states", {}).get("feed_failure", 0)
    out.append(_check("no feed failures", _yes(not failed), f"{failed} input(s) failed to load"))
    return out


def _law(manifest: Mapping[str, Any], synthetic: bool) -> list:
    if synthetic:
        return [_check("state law rows", "unknown", "synthetic test state has no law")]
    rows = manifest["rows"]["law"]
    dated = [r for r in rows if r.get("valid_period")]
    verified = [r for r in rows if r.get("reviewer")]
    return [
        _check("state law rows", _yes(bool(rows)), f"{len(rows)} rows"),
        _check("effective dates", _yes(bool(dated)), f"{len(dated)} rows carry a period"),
        _check("verified reading", _yes(bool(verified)), f"{len(verified)} rows checked by hand"),
    ]


def _local(state: str, manifest: Mapping[str, Any], root: Path, synthetic: bool) -> list:
    v = manifest["fractions"]["verified_exposure"]
    share = v.get("share")
    out = [
        _check(
            "verified local rule coverage",
            "unknown" if share is None else _yes(share > 0),
            "no lot count" if share is None else f"{share:.1%} of eligible lots ({v['basis']})",
        )
    ]
    out.append(_holdout(state, root, synthetic))
    ev = [r for r in manifest["rows"]["evidence"] if r.get("reviewer")]
    out.append(
        _check(
            "verified comparison records",
            "unknown" if synthetic else _yes(bool(ev)),
            f"{len(ev)} verified records for {state}",
        )
    )
    return out


def _holdout(state: str, root: Path, synthetic: bool) -> dict[str, str]:
    """Is the state held out in a validation set: the donor-pool test or the state check?"""
    calib = root / "assumptions" / "calibration"
    pool, check = calib / "donor_pool_errors.yaml", calib / "state_holdouts.yaml"
    if synthetic or not (pool.is_file() or check.is_file()):
        return _check("validation hold-out", "unknown", "no hold-out file or synthetic")
    where = []
    if pool.is_file():
        data = yaml.safe_load(pool.read_text(encoding="utf-8")) or {}
        held = {str(s).upper() for s in (data.get("settings") or {}).get("held_out", [])}
        if state in held:
            where.append(f"held out in {pool.name}")
    row = None
    if check.is_file():
        data = yaml.safe_load(check.read_text(encoding="utf-8")) or {}
        rows = {str(k).upper(): v for k, v in (data.get("states") or {}).items()}
        row = rows.get(state) or None
        if row and row.get("held_out") and row.get("coverage") is not None:
            where.append(
                f"{check.name}: current range caught {row['coverage']:.0%} of "
                f"{row['cases']} out-of-time cases (target 80%; descriptive)"
            )
    if where:
        return _check("validation hold-out", "pass", "; ".join(where))
    names = " or ".join(p.name for p in (pool, check) if p.is_file())
    return _check("validation hold-out", "fail", f"not held out in {names}")


def _adapter(state: str, folder: Path, manifest: Mapping[str, Any]) -> list:
    from visionhousing.data.report_card import INPUTS, load_pack_files, report_card
    from visionhousing.engine.context import TESTLAND, load_transfer_rules, transfer_overrides
    from visionhousing.intake.value_state import OBSERVED, VALUE_STATES
    from visionhousing.ledger import load_ledger

    card = report_card(load_pack_files(folder))
    used = {i.id for i in INPUTS if i.used}
    bare = [r["id"] for r in card["inputs"] if r["status"] == "missing" and r["id"] in used]
    out = [
        _check(
            "fallback for every engine input",
            _yes(not bare),
            "every engine input has data or a ledger stand-in"
            if not bare
            else f"no data and no stand-in: {', '.join(bare)}",
        )
    ]
    rules = load_transfer_rules()
    if state == TESTLAND:
        out.append(_check("borrowed ranges widened", "unknown", "synthetic state is never widened"))
    else:
        due = [str(r["parameter"]) for r in rules if state not in
               {str(s).upper() for s in r.get("measured_in", ())}]  # fmt: skip
        done = transfer_overrides(state, load_ledger(), rules)
        lacking = [p for p in due if p not in done]
        out.append(
            _check(
                "borrowed ranges widened",
                _yes(not lacking),
                f"{len(due)} borrowed, {len(due) - len(lacking)} widened"
                if due
                else "no borrowed range: every transfer rule was measured here",
            )
        )
    words = {r.get("value_state") for r in card["inputs"]}
    known = all(w in VALUE_STATES or w == OBSERVED for w in words)
    failed = card.get("value_states", {}).get("feed_failure", 0)
    out.append(
        _check(
            "feed failure kept apart from zero",
            _yes(known and not failed),
            "every input carries a value state; none failed"
            if known and not failed
            else f"{failed} feed failure(s) in the report card",
        )
    )
    return out


def _publication(state: str, manifest: Mapping[str, Any], settings: Mapping[str, Any],
                 runs_dir: Path, synthetic: bool) -> list:  # fmt: skip
    from visionhousing.export_static import read_saved

    runs = list(settings.get("benchmark_runs", []))
    folder = safe_child(runs_dir, state, "runs")
    if synthetic:
        return [_check("saved benchmark runs", "unknown", "the test state is not published")]
    have = [r for r in runs if (folder / f"{r}.json").is_file()]
    lacking = [r for r in runs if r not in have]
    out = [
        _check(
            "saved benchmark runs",
            _yes(not lacking),
            f"{len(have)} of {len(runs)} saved"
            + (f"; absent: {', '.join(lacking)}" if lacking else ""),
        )
    ]
    hashed, ranged = 0, 0
    for r in have:
        data = read_saved(folder / f"{r}.json")
        h = data.get("hashes") or {}
        hashed += all(h.get(k) for k in ("code", "data", "assumptions"))
        base = data.get("baseline") or {}
        ranged += base.get("p10") is not None and base.get("p90") is not None
    if have:
        out.append(_check("run hashes", _yes(hashed == len(have)), f"{hashed} of {len(have)}"))
        out.append(_check("likely ranges", _yes(ranged == len(have)), f"{ranged} of {len(have)}"))
    pack_hash = manifest.get("pack_hash")
    out.append(_check("pack hash", _yes(bool(pack_hash)), (pack_hash or "none")[:16]))
    return out


def readiness(
    state: str,
    *,
    folder: Path | None = None,
    runs_dir: Path | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the five steps for one state.

    Args:
        state: Two-letter state code (``TL`` for Testland).
        folder: The pack folder (default ``engine.context.pack_dir``).
        runs_dir: The static site's data folder (default ``web/public/data``).
        manifest: A manifest already built (:func:`manifest.build_manifest`).

    Returns:
        ``{state, overall, steps: [{id, title, what, status, checks}]}``.
    """
    from visionhousing.engine.context import pack_dir
    from visionhousing.intake.manifest import build_manifest, load_intake_settings, project_root
    from visionhousing.intake.reproduce import RUNS_DIR

    code = state.upper()
    settings = load_intake_settings()
    steps_cfg = list(settings.get("readiness_steps", []))
    folder = Path(folder) if folder is not None else pack_dir(code)
    if not (folder / "data_dictionary.json").is_file():
        steps = []
        for i, s in enumerate(steps_cfg):
            first = i == 0
            check = _check("state pack", "fail" if first else "unknown",
                           f"no pack at {folder}" if first else "no pack to check")  # fmt: skip
            steps.append({**s, "status": check["status"], "checks": [check]})
        return {"state": code, "overall": "fail", "steps": steps}
    manifest = manifest if manifest is not None else build_manifest(code, folder, settings)
    synthetic = bool(manifest.get("synthetic"))
    root = project_root()
    runs = Path(runs_dir) if runs_dir is not None else RUNS_DIR
    by_id = {
        "national_inputs": lambda: _national(folder, manifest, settings),
        "current_law": lambda: _law(manifest, synthetic),
        "local_evidence": lambda: _local(code, manifest, root, synthetic),
        "benchmark_adapter": lambda: _adapter(code, folder, manifest),
        "publication": lambda: _publication(code, manifest, settings, runs, synthetic),
    }
    steps = []
    for s in steps_cfg:
        checks = by_id[s["id"]]() if s["id"] in by_id else []
        steps.append({**s, "status": _step_status(checks), "checks": checks})
    return {"state": code, "overall": _step_status([{"status": s["status"]} for s in steps]),
            "steps": steps}  # fmt: skip


def to_text(report: Mapping[str, Any]) -> str:
    """The readiness report as plain lines for the terminal."""
    lines = [f"State readiness: {report['state']} (overall: {report['overall']})", ""]
    for i, s in enumerate(report["steps"], start=1):
        lines.append(f"{i}. {s['title']}: {s['status'].upper()}")
        for c in s["checks"]:
            lines.append(f"   [{c['status']:<7}] {c['name']}: {c['detail']}")
    return "\n".join(lines) + "\n"
