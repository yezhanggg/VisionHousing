"""The state intake manifest: one row per input, with where it came from and how it was used.

Plain-language summary
----------------------
For one state, :func:`build_manifest` lists every input in four groups and
gives each row the same fields, so any input can be checked the same way:

=====================  ========================================================
field                  meaning
=====================  ========================================================
``scenarios``          which reforms (or the baseline, or context) it feeds
``source_id``, ``url`` the source's id and web address
``clause_or_table``    the cited clause, table or file read
``published``          the publication edition or enactment date
``retrieved``          the date it was read or downloaded
``valid_period``       the period the value describes or the law is in force
``geography``          the places it covers
``quantity_unit``      what it measures, and the unit
``permit_stage``       the permit stage it counts (only for permit sources)
``coverage``           how much of the state it covers
``exclusions``         what it leaves out (its known limits)
``terms``              the licence or terms of use
``transformation``     what was done to it before the model read it
``reviewer``           who checked it by hand, and when (only hand-checked rows)
``version_hash``       the SHA-256 of the files or record behind it
=====================  ========================================================

When a field has no value, ``field_states`` says why, in the words of
:mod:`visionhousing.intake.value_state`: ``not_applicable`` (a census table has
no permit stage), ``missing`` (nobody recorded it) or ``feed_failure`` (a table
the pack promised is not there). An empty field is never a silent blank.

The groups and where they are read from:

- ``data``: each source the state pack used: the source registry
  (``src/visionhousing/data/sources.yaml``), the pack's ``pack_manifest.json``
  (download dates and file hashes) and ``data_dictionary.json`` (tables,
  columns and units). The scenarios come from the report card's input list.
- ``assumptions``: every Assumption Ledger id that stands in for state data or
  sets take-up, with its sources, and whether a calibration or a transfer rule
  changes it for this state.
- ``law``: the state's law rows: the pack's ``state_law_provisions`` table, the
  state file in ``assumptions/law/states/``, the dated records in
  ``assumptions/law/timeline.yaml`` and the already-legal calibration file.
- ``evidence``: the state's comparison records in
  ``assumptions/evidence/states/``.

**Two fractions** (:func:`evidence_fractions`), both shares of the eligible
single-family lots (the ADU benchmark's exposure):

- ``verified_exposure``: lots in municipalities whose rule was read and
  verified (``muni_zoning_rules.status == verified``), over all lots. A state
  without a town-rule table has 0 verified lots: a measured zero, because no
  town rule was read.
- ``transferred_takeup``: per reform, the take-up parameters listed in
  ``assumptions/intake.yaml`` that were not measured in this state (no
  calibration file names the state and the parameter, and no transfer rule
  lists the state as measured). ``parameter_share`` is their share;
  ``lots_share`` is the share of eligible lots whose take-up uses at least one
  of them (all lots when any is transferred, since each applies statewide).

Command line: ``visionhousing state-intake --state XX`` (JSON) and the
readiness check in :mod:`visionhousing.intake.readiness`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing import __version__
from visionhousing.intake.value_state import OBSERVED, VALUE_STATES
from visionhousing.paths import safe_child

__all__ = [
    "FIELDS",
    "INTAKE_PATH",
    "build_manifest",
    "evidence_fractions",
    "load_intake_settings",
    "project_root",
    "summary_lines",
    "takeup_status",
]

FIELDS = (
    "scenarios",
    "source_id",
    "url",
    "clause_or_table",
    "published",
    "retrieved",
    "valid_period",
    "geography",
    "quantity_unit",
    "permit_stage",
    "coverage",
    "exclusions",
    "terms",
    "transformation",
    "reviewer",
    "version_hash",
)
"""Every manifest row has these fields (value or ``None`` plus a field state)."""


def project_root() -> Path:
    """The repository folder (where ``assumptions/`` is)."""
    from visionhousing.ledger.loader import ROOT

    return ROOT


INTAKE_PATH = Path(__file__).resolve().parents[3] / "assumptions" / "intake.yaml"
"""The intake settings file."""


def load_intake_settings(path: Path | None = None) -> dict[str, Any]:
    """Read ``assumptions/intake.yaml``."""
    path = Path(path) if path is not None else INTAKE_PATH
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _sha(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return isinstance(v, str | list | tuple | dict) and len(v) == 0


def _row(
    kind: str, ident: str, label: str, values: Mapping[str, Any], **why: str
) -> dict[str, Any]:
    """One manifest row: every field filled or given a value state.

    ``why`` names the state of a blank field (default ``missing``).
    """
    row: dict[str, Any] = {"kind": kind, "id": ident, "label": label}
    states: dict[str, str] = {}
    for f in FIELDS:
        v = values.get(f)
        if isinstance(v, float) and pd.isna(v):
            v = None
        row[f] = v
        if _blank(v):
            row[f] = None
            states[f] = why.get(f, "missing")
    row["field_states"] = states
    return row


def _text(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = " ".join(str(v).split())
    return s or None


# --------------------------------------------------------------------------- reading


def _pack(folder: Path) -> dict[str, Any]:
    from visionhousing.data.report_card import load_pack_files, report_card

    files = load_pack_files(folder)
    return {"files": files, "card": report_card(files)}


def _table(folder: Path, name: str) -> pd.DataFrame | None:
    for path in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
        if path.is_file():
            return pd.read_csv(path, dtype={"geoid": str, "county": str})
    return None


def _scenarios_by_table() -> dict[str, list[str]]:
    from visionhousing.data.report_card import INPUTS

    out: dict[str, set[str]] = {}
    for item in INPUTS:
        for t in (item.table, *item.derived_from):
            if t:
                out.setdefault(t, set()).update(item.reforms)
    return {k: sorted(v) for k, v in out.items()}


def _coverage_by_table(card: Mapping[str, Any]) -> dict[str, float]:
    from visionhousing.data.report_card import INPUTS

    tables = {i.id: i.table for i in INPUTS}
    out: dict[str, float] = {}
    for r in card["inputs"]:
        t = tables.get(r["id"])
        if t and r.get("coverage") is not None:
            out[t] = min(out.get(t, 1.0), float(r["coverage"]))
    return out


# --------------------------------------------------------------------------- data rows


def _data_rows(
    state: str, folder: Path, pack: Mapping[str, Any], settings: Mapping[str, Any]
) -> list[dict[str, Any]]:
    from visionhousing.data.registry import load_registry

    registry = load_registry()
    files, card = pack["files"], pack["card"]
    dictionary, manifest = files["dictionary"], files["manifest"] or {}
    synthetic = bool(dictionary.get("synthetic"))
    by_table = _scenarios_by_table()
    cov = _coverage_by_table(card)
    stages = settings.get("permit_stage", {})
    present = {p.name.split(".")[0] for p in folder.iterdir() if p.is_file()}

    tables_of: dict[str, set[str]] = {}
    units_of: dict[str, list[str]] = {}
    for t in dictionary.get("tables", []):
        for s in t.get("sources", []):
            tables_of.setdefault(str(s), set()).add(str(t["table"]))
    for c in dictionary.get("columns", []):
        for s in str(c.get("source", "")).split(","):
            s = s.strip()
            if not s:
                continue
            tables_of.setdefault(s, set()).add(str(c["table"]))
            units_of.setdefault(s, []).append(f"{c['table']}.{c['name']} ({c.get('unit', '?')})")
    prov = {p["id"]: p for p in manifest.get("provenance", [])}
    raw: dict[str, list[dict[str, Any]]] = {}
    for i in manifest.get("inputs", []):
        raw.setdefault(str(i["source"]), []).append(i)
    pack_files = {f["path"].split(".")[0]: f for f in manifest.get("files", [])}

    rows = []
    for sid in sorted(set(prov) | set(tables_of) | set(raw)):
        if synthetic and sid == "synthetic":
            rows.append(
                _row(
                    "data",
                    sid,
                    "Synthetic test state (Testland)",
                    {
                        "scenarios": ["baseline", "all reforms"],
                        "clause_or_table": ", ".join(sorted(tables_of.get(sid, ()))),
                        "quantity_unit": f"{len(units_of.get(sid, []))} columns (data dictionary)",
                        "transformation": "Made by scripts/make_testland.py from a fixed seed",
                        "version_hash": _sha(dictionary),
                    },
                    **{f: "not_applicable" for f in FIELDS},
                )
            )
            continue
        reg = registry.get(sid)
        p = prov.get(sid, {})
        tables = sorted(tables_of.get(sid, ()))
        scen = sorted({s for t in tables for s in by_table.get(t, ["context"])}) or ["context"]
        absent = [t for t in tables if t not in present]
        shares = [cov[t] for t in tables if t in cov]
        accessed = sorted(
            {str(i["accessed"]) for i in raw.get(sid, []) if i.get("accessed")}
            | ({str(p["access_date"])} if p.get("access_date") else set())
        )
        hashes = [f"{i['file']}: {i['sha256']}" for i in raw.get(sid, []) if i.get("sha256")]
        if not hashes:
            hashes = [f"{t}: {pack_files[t]['sha256']}" for t in tables if t in pack_files]
        coverage: dict[str, Any] | None = None
        if tables:
            coverage = {
                "tables": tables,
                "share_filled": min(shares) if shares else None,
                "absent_tables": absent,
            }
        why = {"permit_stage": "not_applicable", "reviewer": "not_applicable"}
        if absent:
            why["version_hash"] = why["coverage"] = "feed_failure"
        rows.append(
            _row(
                "data",
                sid,
                reg.name if reg else str(p.get("source", sid)),
                {
                    "scenarios": scen,
                    "source_id": sid,
                    "url": reg.url_pattern if reg else None,
                    "clause_or_table": (
                        ", ".join(sorted({i["file"] for i in raw.get(sid, [])}))
                        or (reg.member if reg else None)
                    ),
                    "published": (reg.vintage if reg else p.get("version")),
                    "retrieved": ", ".join(accessed) if accessed else None,
                    "valid_period": (reg.vintage if reg else p.get("version")),
                    "geography": list(reg.geography) if reg else None,
                    "quantity_unit": units_of.get(sid, [])[:12] or None,
                    "permit_stage": stages.get(sid),
                    "coverage": coverage,
                    "exclusions": _text(reg.limits if reg else p.get("limits")),
                    "terms": _text(f"{reg.licence} ({reg.terms_url})" if reg else p.get("licence")),
                    "transformation": _text(reg.cleaning if reg else p.get("cleaning")),
                    "reviewer": None,
                    "version_hash": hashes,
                },
                **why,
            )
        )
    return rows


# --------------------------------------------------------------------------- assumptions


def _calibrations(root: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted((root / "assumptions" / "calibration").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["_file"] = f"assumptions/calibration/{path.name}"
        out.append(data)
    return out


def takeup_status(
    state: str, settings: Mapping[str, Any] | None = None, root: Path | None = None
) -> dict[str, dict[str, Any]]:
    """For each lot-based reform, whether each take-up parameter was measured in ``state``.

    Returns:
        ``{reform: {parameter: {"status": "local" | "transferred" |
        "not_applicable", "how": text}}}``. Testland gives ``not_applicable``
        (a synthetic state has no place to measure in).
    """
    from visionhousing.engine.context import TESTLAND, load_transfer_rules

    code = state.upper()
    settings = settings if settings is not None else load_intake_settings()
    root = root or project_root()
    cal = _calibrations(root)
    rules = {str(r["parameter"]): r for r in load_transfer_rules()}
    out: dict[str, dict[str, Any]] = {}
    for reform, params in (settings.get("takeup_parameters") or {}).items():
        out[reform] = {}
        for pid in params:
            if code == TESTLAND:
                out[reform][pid] = {"status": "not_applicable", "how": "synthetic test state"}
                continue
            own = [
                c for c in cal
                if str(c.get("state", "")).upper() == code and c.get("parameter") == pid
            ]  # fmt: skip
            rule = rules.get(pid)
            measured_in = {str(s).upper() for s in (rule or {}).get("measured_in", ())}
            if own:
                out[reform][pid] = {
                    "status": "local",
                    "how": f"calibration {own[0].get('id')} ({own[0]['_file']}), kind "
                    f"{own[0].get('kind', 'calibrated')}",
                }
            elif rule and code in measured_in:
                out[reform][pid] = {
                    "status": "local",
                    "how": f"calibration {rule.get('calibration')} measured in {code}",
                }
            elif rule:
                out[reform][pid] = {
                    "status": "transferred",
                    "how": f"borrowed from {', '.join(sorted(measured_in))} and widened "
                    "(assumptions/transfer.yaml)",
                }
            else:
                out[reform][pid] = {
                    "status": "transferred",
                    "how": "ledger range set from evidence in other places "
                    "(assumptions/parameters.yaml)",
                }
    return out


def _assumption_rows(state: str, settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    from visionhousing.data.report_card import INPUTS
    from visionhousing.ledger import load_ledger

    ledger = load_ledger()
    ids: dict[str, list[str]] = {}
    for item in INPUTS:
        for pid in item.assumed_by:
            ids.setdefault(pid, []).append(item.id)
    status = takeup_status(state, settings)
    for reform, params in status.items():
        for pid in params:
            ids.setdefault(pid, []).append(f"take-up ({reform})")
    rows = []
    for pid in sorted(ids):
        if pid not in ledger:  # a named id that is not in the ledger
            rows.append(_row("assumption", pid, pid, {}, **{f: "missing" for f in FIELDS}))
            continue
        p = ledger[pid]
        how = next((params[pid] for params in status.values() if pid in params), None)
        transform = f"ledger entry, kind {p.kind}, {p.shape} range {p.low} to {p.high}"
        if how:
            transform += f"; for {state.upper()}: {how['status']} ({how['how']})"
        rows.append(
            _row(
                "assumption",
                pid,
                p.label,
                {
                    "scenarios": list(p.used_by),
                    "source_id": [s.id for s in p.source],
                    "url": [s.url for s in p.source if s.url],
                    "clause_or_table": [_text(s.citation) for s in p.source],
                    "retrieved": sorted({str(s.accessed) for s in p.source if s.accessed}),
                    "geography": "statewide (one ledger value, the same in every county)",
                    "quantity_unit": f"{p.label} ({p.unit})",
                    "exclusions": _text(p.revisit),
                    "transformation": transform,
                    "reviewer": "checked against a cited source" if p.status == "sourced" else None,
                    "version_hash": _sha(p.model_dump(mode="json")),
                    "coverage": f"stands in for: {', '.join(ids[pid])}",
                },
                published="not_applicable",
                valid_period="not_applicable",
                permit_stage="not_applicable",
                terms="not_applicable",
            )
        )
    return rows


# --------------------------------------------------------------------------- law rows


def _law_rows(state: str, folder: Path, root: Path, fips: str | None) -> list[dict[str, Any]]:
    code = state.upper()
    rows: list[dict[str, Any]] = []
    docs = _table(folder, "law_documents")
    doc_hash = {}
    doc_terms = {}
    if docs is not None and len(docs):
        for _, d in docs.iterrows():
            if isinstance(d.get("source_id"), str):
                if isinstance(d.get("sha256"), str):
                    doc_hash.setdefault(d["source_id"], d["sha256"])
                doc_terms.setdefault(d["source_id"], _text(d.get("licence")))
    prov = _table(folder, "state_law_provisions")
    na = {"permit_stage": "not_applicable", "quantity_unit": "not_applicable"}
    if prov is not None:
        for _, r in prov.iterrows():
            verified = str(r.get("verification")) == "verified"
            rows.append(
                _row(
                    "law",
                    str(r["provision_id"]),
                    _text(r.get("citation")) or str(r["provision_id"]),
                    {
                        "scenarios": [str(r.get("reform"))],
                        "source_id": _text(r.get("source_id")),
                        "url": _text(r.get("url")),
                        "clause_or_table": _text(r.get("citation")),
                        "published": _text(r.get("enacted_on")) or _text(r.get("introduced_on")),
                        "retrieved": _text(r.get("checked_on")),
                        "valid_period": _text(r.get("effective_date")),
                        "geography": _text(r.get("applies_to")),
                        "coverage": _text(r.get("scope")),
                        "exclusions": _text(r.get("binding_conditions")),
                        "terms": doc_terms.get(r.get("source_id")),
                        "transformation": f"summarised in our own words; status {r.get('status')}",
                        "reviewer": f"verified reading on {r.get('checked_on')}"
                        if verified
                        else None,
                        "version_hash": doc_hash.get(r.get("source_id")),
                    },
                    **na,
                )
            )
    state_file = safe_child(root / "assumptions" / "law" / "states", f"{code.lower()}.yaml")
    timeline = root / "assumptions" / "law" / "timeline.yaml"
    sources = []
    if state_file.is_file():
        data = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
        for p in data.get("provisions", []):
            rows.append(
                _row(
                    "law",
                    str(p["provision_id"]),
                    _text(p.get("citation")) or str(p["provision_id"]),
                    {
                        "scenarios": [str(p.get("reform"))],
                        "source_id": _text(p.get("source_id")),
                        "url": _text(p.get("url")),
                        "clause_or_table": _text(p.get("citation")),
                        "valid_period": _text(p.get("effective")),
                        "geography": code,
                        "retrieved": _text(p.get("checked_on")),
                        "transformation": f"summarised in our own words; status {p.get('status')}",
                        "reviewer": f"verified reading on {p['checked_on']}"
                        if p.get("checked_on")
                        else None,
                        "version_hash": _sha(p),
                        "terms": "Public legislative record",
                    },
                    **na,
                )
            )
        sources.append((state_file, data.get("records", [])))
    if timeline.is_file():
        data = yaml.safe_load(timeline.read_text(encoding="utf-8")) or {}
        sources.append((timeline, data.get("records", [])))
    seen = set()
    for path, records in sources:
        for r in records:
            j = str(r.get("jurisdiction", ""))
            local = bool(fips) and j.isdigit() and j[:2] == fips
            if r.get("record_id") in seen or not (j == code or local):
                continue
            seen.add(r.get("record_id"))
            period = " to ".join(str(r[k]) for k in ("start", "end") if r.get(k)) or None
            rows.append(
                _row(
                    "law",
                    str(r["record_id"]),
                    _text(r.get("citation")) or str(r["record_id"]),
                    {
                        "scenarios": [str(r.get("housing_type"))],
                        "source_id": _text(r.get("source_id")),
                        "url": list(r.get("sources") or []),
                        "clause_or_table": _text(r.get("citation")),
                        "published": _text(r.get("text_version")),
                        "retrieved": _text(r.get("retrieved_on") or r.get("verified_on")),
                        "valid_period": period,
                        "geography": j,
                        "coverage": _text(r.get("scope")),
                        "exclusions": _text(r.get("exclusions")),
                        "terms": "Public legislative record",
                        "transformation": f"dated status record ({r.get('status')}) in "
                        f"{path.relative_to(root).as_posix()}",
                        "reviewer": f"verified on {r['verified_on']}"
                        if r.get("verified_on")
                        else None,
                        "version_hash": _sha(r),
                    },
                    source_id="not_applicable",
                    **na,
                )
            )
    for c in _calibrations(root):
        if str(c.get("state", "")).upper() != code or not c.get("parameter"):
            continue
        prov_ = c.get("provenance") or {}
        rows.append(
            _row(
                "law",
                str(c.get("id")),
                _text(c.get("label")) or f"{c.get('parameter')} for {code}",
                {
                    "scenarios": ["adu", "missing_middle"],
                    "source_id": [s.get("id") for s in prov_.get("sources", [])],
                    "url": [s.get("url") for s in prov_.get("sources", []) if s.get("url")],
                    "clause_or_table": [_text(s.get("citation")) for s in prov_.get("sources", [])],
                    "retrieved": _text(prov_.get("read_between")),
                    "geography": code,
                    "quantity_unit": f"{c.get('parameter')} ({c.get('unit')})",
                    "transformation": _text(prov_.get("summary")),
                    "reviewer": f"read between {prov_.get('read_between')}; kind "
                    f"{c.get('kind', 'calibrated')}",
                    "version_hash": _sha({k: v for k, v in c.items() if k != "_file"}),
                    "terms": "Public legislative record and public data",
                },
                permit_stage="not_applicable",
                published="missing",
            )
        )
    rules = _table(folder, "muni_zoning_rules")
    if rules is not None and len(rules):
        verified = rules[rules["status"].astype(str) == "verified"]
        dates = verified["reviewed_on"].dropna().astype(str) if "reviewed_on" in rules else []
        rows.append(
            _row(
                "law",
                "muni_zoning_rules",
                "Town-by-town zoning rules (ADU status, smallest lot, parking)",
                {
                    "scenarios": ["adu", "missing_middle", "parking"],
                    "clause_or_table": "muni_zoning_rules (section_cite per row)",
                    "retrieved": f"{min(dates)} to {max(dates)}" if len(dates) else None,
                    "geography": f"{len(rules):,} municipalities",
                    "quantity_unit": "ADU status category per municipality",
                    "coverage": f"{len(verified):,} of {len(rules):,} municipalities verified",
                    "transformation": "read from each municipality's published code; rule "
                    "reader drafts, a person verifies",
                    "reviewer": f"{len(verified):,} rows verified by hand",
                    "version_hash": _file_sha(folder, "muni_zoning_rules"),
                    "terms": "Public municipal codes",
                },
                permit_stage="not_applicable",
                published="not_applicable",
                source_id="not_applicable",
                url="not_applicable",
                valid_period="missing",
                exclusions="missing",
            )
        )
    return rows


def _file_sha(folder: Path, name: str) -> str | None:
    for path in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


# --------------------------------------------------------------------------- evidence rows


def _evidence_rows(state: str) -> list[dict[str, Any]]:
    from visionhousing.evidence.records import PanelError, load_panel

    try:
        panel = load_panel()
    except PanelError:
        return []
    rows = []
    for r in panel.records:
        if r.state != state.upper():
            continue
        data = r.model_dump(mode="json")
        eff = ", ".join(
            str(e.get("date")) for e in data.get("effective_dates", []) if e.get("date")
        )
        rows.append(
            _row(
                "evidence",
                r.id,
                r.title,
                {
                    "scenarios": list(r.policy_types),
                    "source_id": r.bill or r.id,
                    "url": r.url,
                    "clause_or_table": r.citation,
                    "published": str(r.enacted_date) if r.enacted_date else None,
                    "retrieved": str(r.retrieved),
                    "valid_period": eff or None,
                    "geography": r.jurisdiction,
                    "quantity_unit": " / ".join(x for x in (r.outcome_measure, r.denominator) if x)
                    or None,
                    "coverage": _text(r.geographic_scope),
                    "exclusions": list(r.exclusions) or None,
                    "terms": "Official public pages; summaries in our own words",
                    "transformation": "comparison record (context only; no number enters a run)",
                    "reviewer": f"verified on {r.retrieved}" if r.verified else None,
                    "version_hash": _sha(data),
                },
                permit_stage="not_applicable",
                quantity_unit="not_applicable",
                published="not_applicable" if r.kind == "study" else "missing",
                reviewer="missing",
            )
        )
    return rows


# --------------------------------------------------------------------------- fractions


def evidence_fractions(
    state: str, folder: Path, settings: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The verified-exposure and transferred-take-up fractions (see the module notes)."""
    settings = settings if settings is not None else load_intake_settings()
    ex = settings.get("exposure", {})
    rules = _table(folder, ex.get("rules_table", "muni_zoning_rules"))
    weight = ex.get("rules_weight", "sf_lots")
    lots_total: float | None = None
    lots_verified = 0.0
    basis = ""
    if rules is not None and weight in rules.columns and len(rules):
        w = pd.to_numeric(rules[weight], errors="coerce").fillna(0.0)
        lots_total = float(w.sum())
        ok = rules["status"].astype(str) == ex.get("verified_status", "verified")
        lots_verified = float(w[ok].sum())
        basis = (
            f"{weight} in {ex.get('rules_table')} ({int(ok.sum()):,} of {len(rules):,} verified)"
        )
    else:
        tracts = _table(folder, "tracts")
        cols = [c for c in ex.get("tract_columns", []) if tracts is not None and c in tracts]
        if tracts is not None and cols:
            lots_total = float(tracts[cols].apply(pd.to_numeric, errors="coerce").sum().sum())
            basis = f"{' + '.join(cols)} in tracts; no town-rule table, so no lot is verified"
    if lots_total is None:
        verified = {"share": None, "state": "feed_failure", "basis": "no lot count in the pack"}
    else:
        share = lots_verified / lots_total if lots_total > 0 else None
        verified = {
            "share": None if share is None else round(share, 4),
            "state": OBSERVED if share else ("true_zero" if share == 0 else "missing"),
            "lots_verified": round(lots_verified),
            "lots_total": round(lots_total),
            "basis": basis,
        }
    status = takeup_status(state, settings)
    transferred: dict[str, Any] = {}
    for reform, params in status.items():
        kinds = [v["status"] for v in params.values()]
        if all(k == "not_applicable" for k in kinds):
            transferred[reform] = {
                "parameter_share": None,
                "lots_share": None,
                "state": "not_applicable",
                "parameters": params,
            }
            continue
        n = sum(k == "transferred" for k in kinds)
        transferred[reform] = {
            "parameter_share": round(n / len(kinds), 4) if kinds else None,
            "lots_share": 1.0 if n else 0.0,
            "state": OBSERVED if n else "true_zero",
            "parameters": params,
        }
    return {"verified_exposure": verified, "transferred_takeup": transferred}


# --------------------------------------------------------------------------- the manifest


def build_manifest(
    state: str, folder: Path | None = None, settings: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The intake manifest for one state (``TL`` for Testland).

    Args:
        state: Two-letter state code.
        folder: The state's pack folder (default :func:`engine.context.pack_dir`).
        settings: ``assumptions/intake.yaml`` (read when not given).

    Returns:
        ``{state, synthetic, pack_hash, code_version, sources_read, fields,
        rows: {data, assumptions, law, evidence}, counts, field_state_counts,
        fractions}``.

    Raises:
        FileNotFoundError: The state has no built pack.
    """
    from visionhousing.engine.context import pack_dir

    code = state.upper()
    folder = Path(folder) if folder is not None else pack_dir(code)
    if not (folder / "data_dictionary.json").is_file():
        raise FileNotFoundError(
            f"no state pack for {code} at {folder}; run `make data STATE={code}`"
        )
    settings = settings if settings is not None else load_intake_settings()
    root = project_root()
    pack = _pack(folder)
    rows = {
        "data": _data_rows(code, folder, pack, settings),
        "assumptions": _assumption_rows(code, settings),
        "law": _law_rows(code, folder, root, (pack["files"]["manifest"] or {}).get("state_fips")),
        "evidence": _evidence_rows(code),
    }
    tally = {s: 0 for s in VALUE_STATES}
    for group in rows.values():
        for r in group:
            for s in r["field_states"].values():
                tally[s] += 1
    manifest = pack["files"]["manifest"] or {}
    return {
        "state": code,
        "synthetic": bool(pack["files"]["dictionary"].get("synthetic")),
        "pack_hash": manifest.get("pack_hash"),
        "code_version": __version__,
        "sources_read": [
            "src/visionhousing/data/sources.yaml",
            f"{_rel(folder, root)}/pack_manifest.json",
            f"{_rel(folder, root)}/data_dictionary.json",
            "assumptions/parameters.yaml",
            "assumptions/transfer.yaml",
            "assumptions/calibration/",
            "assumptions/law/",
            "assumptions/evidence/states/",
            "assumptions/intake.yaml",
        ],
        "fields": list(FIELDS),
        "rows": rows,
        "counts": {k: len(v) for k, v in rows.items()},
        "field_state_counts": tally,
        "report_card_value_states": pack["card"].get("value_states", {}),
        "fractions": evidence_fractions(code, folder, settings),
    }


def _rel(folder: Path, root: Path) -> str:
    try:
        return folder.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return folder.name


def summary_lines(manifest: Mapping[str, Any]) -> list[str]:
    """A few plain lines for the command line."""
    c = manifest["counts"]
    fx = manifest["fractions"]
    v = fx["verified_exposure"]
    lines = [
        f"Intake manifest {manifest['state']}: {c['data']} data sources, "
        f"{c['assumptions']} ledger entries, {c['law']} law rows, "
        f"{c['evidence']} comparison records.",
        "Blank fields by value state: "
        + ", ".join(f"{k} {n}" for k, n in manifest["field_state_counts"].items() if n),
    ]
    share = "n/a" if v.get("share") is None else f"{v['share']:.1%}"
    lines.append(f"Verified exposure: {share} of eligible lots ({v.get('basis', '')}).")
    for reform, t in fx["transferred_takeup"].items():
        ps = "n/a" if t["parameter_share"] is None else f"{t['parameter_share']:.0%}"
        moved = [p for p, v in t["parameters"].items() if v["status"] == "transferred"]
        lines.append(
            f"Transferred take-up, {reform}: {ps} of take-up parameters"
            + (f" ({', '.join(moved)})." if moved else f" ({t['state']}).")
        )
    return lines
