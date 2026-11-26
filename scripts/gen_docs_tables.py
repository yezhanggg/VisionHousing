"""Regenerate the generated tables in the project documents.

Why: every table of sources, licences and assumptions in the documents must match
the repository's own records. Typing them by hand would let them drift. This
script writes them from those records instead.

What it fills (only the text between ``<!-- generated: NAME -->`` and
``<!-- /generated: NAME -->`` is ever changed):

- ``DATA_PROVENANCE.md``: the source table, from ``provenance_rows()`` and the
  source registry, and the calibration tables, from the ``provenance`` part of
  each file in ``assumptions/calibration/``.
- ``DISCLOSURES.md``: the dependency table, licence exceptions, composite licences
  and browser libraries, from ``uv.lock``, ``web/package.json`` and
  ``web/package-lock.json``, and the dataset licences, from the registry and the
  calibration files. Python licences come from ``scripts/licence_audit.py`` when it
  can be imported, else straight from ``importlib.metadata``.
- ``docs/MODEL_AND_ASSUMPTIONS.md`` (the Overview, kept under the 25-page limit): the
  key-assumption table and the ledger summary, from ``assumptions/parameters.yaml``;
  the thin-data state comparison (section 3.4), from the engine runs and report cards
  saved for the static site in ``web/public/data/`` (committed, so the check needs no
  fetched data); and, from the same saved files, the baseline rule contest, the
  headline results and the top inputs per reform.
- ``docs/TECHNICAL_APPENDIX.md``: the full assumption tables (every ledger entry), the
  inputs each thin-data state fell back on, the yearly split of the results, and the
  Massachusetts and Wyoming headline results (appendix G.3 and G.4), from their saved runs.
- ``docs/POLICY_SCENARIO_COMPARISON.md``: the headline results per state, the package
  arithmetic and the top inputs per reform, from the same saved runs.

When the static export is refreshed (``make export-static``), rerun this script and
every results table follows it.

The output depends only on files in the repository and on the installed package
metadata. It does not read ``data/manifest.json``, because that file exists only
on a machine that has fetched the data. Running it twice gives the same files.

Usage: ``uv run python scripts/gen_docs_tables.py [--check] [--root PATH]``.
``--check`` writes nothing and exits 1 if any block differs from what it would write.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

try:  # the audit's own licence logic, so the table and the audit always agree
    import licence_audit as _audit
except ImportError:  # pragma: no cover - only when scripts/ is not on the path
    _audit = None

try:  # the secret scan, to mark long public web addresses it would mistake for keys
    import secret_scan as _scan
except ImportError:  # pragma: no cover - only when scripts/ is not on the path
    _scan = None

URL = re.compile(r"https?://[^\s)]+")
ALLOW_URL = " <!-- secret-scan: allow (public web address) -->"

RUN_TIME = "run time"
CITED_ONLY = "Cited only. No data from it is kept."
BROWSER = "browser"


class BlockError(ValueError):
    """A document is missing a generated block's start or end marker."""


# --------------------------------------------------------------------------- blocks


def block_pattern(name: str) -> re.Pattern[str]:
    """Return the pattern matching one generated block, markers included."""
    n = re.escape(name)
    return re.compile(rf"<!-- generated: {n} -->\n.*?<!-- /generated: {n} -->", re.DOTALL)


def replace_block(text: str, name: str, body: str) -> str:
    """Replace the body of the block ``name`` in ``text``; everything else is kept.

    Raises:
        BlockError: The block's markers are missing or appear more than once.
    """
    pattern = block_pattern(name)
    found = pattern.findall(text)
    if len(found) != 1:
        raise BlockError(f"block '{name}': expected one start and end marker, found {len(found)}")
    inner = mark_public_urls(body.strip("\n"))
    new = f"<!-- generated: {name} -->\n{inner}\n<!-- /generated: {name} -->"
    return pattern.sub(lambda _m: new, text)


def mark_public_urls(body: str) -> str:
    """Excuse a line from the secret scan only when its sole findings are inside web addresses.

    A long address path can look like a random key to the scan. Each such line gets
    the scan's allow marker. A line that would still be flagged with every address
    removed is left as it is, so a real key is never excused.
    """
    if _scan is None:  # pragma: no cover
        return body
    out = []
    for line in body.split("\n"):
        if _scan.scan_text("doc", line, []) and not _scan.scan_text("doc", URL.sub("", line), []):
            line += ALLOW_URL
        out.append(line)
    return "\n".join(out)


def cell(value: Any) -> str:
    """Make one Markdown table cell: one line, pipes escaped."""
    text = " ".join(str(value).split())
    return text.replace("|", "\\|") or "-"


def table(header: list[str], rows: Iterable[Iterable[Any]]) -> str:
    """Render a Markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def number(value: float) -> str:
    """Show a ledger number plainly: whole numbers with thousands commas, others to 6 digits."""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:.6g}"


def link(text: str, url: str | None) -> str:
    """Return a Markdown link, or the text alone when there is no address."""
    return f"[{text}]({url})" if url else text


# --------------------------------------------------------------------------- inputs


def registry_path(root: Path) -> Path:
    """Return the source registry path under ``root``."""
    return root / "src" / "visionhousing" / "data" / "sources.yaml"


def load_calibrations(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """Read every calibration file, sorted by file name, as ``(file name, content)``."""
    folder = root / "assumptions" / "calibration"
    return [
        (p.name, yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(folder.glob("*.yaml"))
    ]


def uv_groups(lock: dict[str, Any], project: str = "visionhousing") -> dict[str, list[str]]:
    """Return, for each locked package, the dependency groups that pull it in.

    A package reached from the project's own dependencies is "run time". One
    reached only from a dependency group (build, dev, spatial, research) carries
    that group's name; one reached from an optional extra (``geo``) carries
    "geo (extra)". Packages reached from several get every name, in order.
    """
    packages = {p["name"]: p for p in lock.get("package", [])}
    root = packages.get(project, {})
    starts: dict[str, list[str]] = {RUN_TIME: [d["name"] for d in root.get("dependencies", [])]}
    for group, deps in sorted(root.get("dev-dependencies", {}).items()):
        starts[group] = [d["name"] for d in deps]
    for extra, deps in sorted(root.get("optional-dependencies", {}).items()):
        starts[f"{extra} (extra)"] = [d["name"] for d in deps]
    found: dict[str, list[str]] = {}
    for group, first in starts.items():
        seen: set[str] = set()
        todo = list(first)
        while todo:
            name = todo.pop()
            if name in seen or name == project:
                continue
            seen.add(name)
            todo += [d["name"] for d in packages.get(name, {}).get("dependencies", [])]
        for name in seen:
            found.setdefault(name, []).append(group)
    return found


def _metadata_licence(name: str) -> str:
    """Fallback when the audit cannot be imported: read the declared licence directly."""
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "not installed"
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    short = (meta.get("License") or "").strip()
    if short and "\n" not in short and len(short) <= 80:
        return short
    classifiers = [
        c.split("::")[-1].strip()
        for c in (meta.get_all("Classifier") or [])
        if c.startswith("License ::") and c.strip() != "License :: OSI Approved"
    ]
    return " OR ".join(classifiers) or "not declared"


def python_findings(root: Path) -> list[dict[str, Any]]:
    """List every locked Python package with its version, groups, licence and verdict."""
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    groups = uv_groups(lock)
    locked = [
        (p["name"], p.get("version", "")) for p in lock.get("package", []) if p["name"] in groups
    ]
    rows = []
    if _audit is not None:
        for f in _audit.audit_python(locked):
            rows.append(
                {
                    "name": f.name,
                    "version": f.version,
                    "groups": groups.get(f.name, []),
                    "licence": f.expression or "not declared",
                    "ok": f.ok,
                    "notes": f.notes,
                }
            )
    else:  # pragma: no cover - exercised only without scripts/ on the path
        for name, version in locked:
            rows.append(
                {
                    "name": name,
                    "version": version,
                    "groups": groups.get(name, []),
                    "licence": _metadata_licence(name),
                    "ok": None,
                    "notes": [],
                }
            )
    return sorted(rows, key=lambda r: r["name"].lower())


def npm_findings(root: Path) -> list[dict[str, Any]]:
    """List every package in the npm lockfile, marking which direct library brings it."""
    manifest = json.loads((root / "web" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "web" / "package-lock.json").read_text(encoding="utf-8"))
    packages = {
        key.split("node_modules/")[-1]: info
        for key, info in lock.get("packages", {}).items()
        if key
    }
    direct = sorted(manifest.get("dependencies", {}))
    dev_direct = set(manifest.get("devDependencies", {}))
    parent: dict[str, str] = {}
    for top in direct:
        todo = list(packages.get(top, {}).get("dependencies", {}))
        while todo:
            name = todo.pop()
            if name in direct or name in parent:
                continue
            parent[name] = top
            todo += list(packages.get(name, {}).get("dependencies", {}))
    rows = []
    for name, info in sorted(packages.items()):
        raw = info.get("license", "")
        expression = raw.get("type", "") if isinstance(raw, dict) else str(raw)
        ok, notes = _audit.evaluate(expression) if _audit and expression else (None, [])
        if info.get("dev"):  # the build tool (Vite) and what it needs; not in the page
            group = "web build" if name in dev_direct else "web build, inside vite"
        else:
            group = (
                BROWSER if name in direct else f"{BROWSER}, inside {parent.get(name, 'a library')}"
            )
        rows.append(
            {
                "name": name,
                "version": info.get("version", ""),
                "groups": [group],
                "licence": expression or "not declared",
                "ok": ok,
                "notes": notes,
                "direct": name in direct,
                "resolved": info.get("resolved", ""),
                "integrity": info.get("integrity", ""),
            }
        )
    return rows


# --------------------------------------------------------------------------- DATA_PROVENANCE.md


def provenance_table(root: Path) -> str:
    """The source table: one row per registry source, from ``provenance_rows()``."""
    from visionhousing.data.manifest import provenance_rows
    from visionhousing.data.registry import load_registry

    sources = load_registry(registry_path(root))
    rows = provenance_rows(sources, entries=[])
    out = []
    for row in rows:
        checked = sources[row.id].checked_on
        out.append(
            [
                f"**{row.source}** (`{row.id}`)",
                row.version,
                checked.isoformat() if checked else "not yet confirmed",
                f"{row.licence} ({link('terms', row.terms)})",
                row.cleaning,
                row.limits,
            ]
        )
    note = (
        f"*{len(rows)} sources, in registry order. Generated from `provenance_rows()` and "
        "`src/visionhousing/data/sources.yaml`.*"
    )
    header = ["Source", "Version", "Address checked", "Licence", "Cleaning", "Limits"]
    return note + "\n\n" + table(header, out)


def calibration_provenance(root: Path) -> str:
    """One section per calibration file: what it is, its sources and their terms, its limits."""
    parts = []
    for file_name, data in load_calibrations(root):
        prov = data.get("provenance", {})
        title = data.get("label") or f"Sets `{data.get('parameter', data.get('id', ''))}`"
        lines = [f"#### `{file_name}`: {cell(title)}", ""]
        lines.append(f"- File: `assumptions/calibration/{file_name}` (id `{data.get('id', '')}`)")
        if data.get("parameter"):
            lines.append(f"- Sets: ledger parameter `{data['parameter']}`")
        if data.get("also"):
            names = ", ".join(f"`{k}`" for k in data["also"])
            lines.append(f"- Also sets: {names}")
        if data.get("state"):
            lines.append(f"- Applies to: {data['state']} runs only")
        when = prov.get("measured_on") or prov.get("read_between")
        if when:
            lines.append(f"- Measured or read: {when}")
        lines.append(f"- What it is: {cell(prov.get('summary', ''))}")
        if prov.get("method"):
            lines.append(f"- Method: {cell(prov['method'])}")
        lines.append("")
        source_rows = [
            [
                s.get("id", ""),
                link(cell(s.get("citation", "")), s.get("url")),
                s.get("licence") or CITED_ONLY,
            ]
            for s in prov.get("sources", [])
        ]
        lines.append(table(["Source id", "Source", "Licence or terms"], source_rows))
        samples = prov.get("samples", [])
        if samples:
            lines.append("")
            lines.append(
                table(
                    ["Sample", "Lots", "Note"],
                    [
                        [s.get("place", ""), number(s.get("lots", 0)), s.get("note", "")]
                        for s in samples
                    ],
                )
            )
        limits = prov.get("limits") or data.get("limits") or []
        if limits:
            lines.append("")
            lines.append("Limits:")
            lines.append("")
            lines += [f"- {cell(item)}" for item in limits]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- DISCLOSURES.md


def dependency_table(root: Path) -> str:
    """Every locked Python and npm package with its version, group and licence."""
    py = python_findings(root)
    npm = npm_findings(root)
    rows = [["Python", r["name"], r["version"], ", ".join(r["groups"]), r["licence"]] for r in py]
    rows += [["npm", r["name"], r["version"], ", ".join(r["groups"]), r["licence"]] for r in npm]
    source = "`scripts/licence_audit.py`" if _audit else "`importlib.metadata`"
    note = (
        f"*{len(py)} Python packages from `uv.lock` and {len(npm)} npm packages from "
        f"`web/package-lock.json`. Licences read by {source}. The project itself is not listed.*"
    )
    return note + "\n\n" + table(["Ecosystem", "Package", "Version", "Group", "Licence"], rows)


def licence_exceptions(root: Path) -> str:
    """Packages the audit does not accept, or packages accepted only from a reviewed table."""
    rows = []
    for r in python_findings(root) + npm_findings(root):
        if r["ok"] is False:
            rows.append([r["name"], r["version"], r["licence"], " / ".join(r["notes"])])
    reviewed = [r for r in python_findings(root) if any("reviewed table" in n for n in r["notes"])]
    parts = []
    if rows:
        parts.append(table(["Package", "Version", "Licence", "Why it fails"], rows))
    else:
        parts.append("None. Every locked package passes the licence audit.")
    if reviewed:
        names = ", ".join(f"`{r['name']}` {r['version']} ({r['licence']})" for r in reviewed)
        parts.append(
            f"Installed only on other platforms, so the licence comes from the audit's "
            f"hand-checked table: {names}."
        )
    return "\n\n".join(parts)


def composite_licences(root: Path) -> str:
    """Packages whose declared licence joins several licences with AND, OR or WITH."""
    joined = re.compile(r"\b(AND|OR|WITH)\b")
    rows = [
        [r["name"], r["version"], r["licence"]]
        for r in python_findings(root) + npm_findings(root)
        if joined.search(r["licence"])
    ]
    if not rows:
        return "None."
    return table(["Package", "Version", "Full licence expression"], rows)


def browser_libraries(root: Path) -> str:
    """The libraries pinned in ``web/package.json``: version, licence, where and how checked."""
    rows = []
    for r in npm_findings(root):
        if not r["direct"]:
            continue
        host = re.sub(r"^https://([^/]+)/.*$", r"\1", r["resolved"]) or "-"
        algorithm = r["integrity"].split("-", 1)[0].upper() if r["integrity"] else "none"
        rows.append([r["name"], r["version"], r["licence"], host, algorithm])
    header = ["Library", "Version", "Licence", "Downloaded from", "Integrity check"]
    return table(header, rows)


def dataset_licences(root: Path) -> str:
    """Every dataset: the registry's sources, then each calibration file's sources."""
    from visionhousing.data.registry import load_registry

    rows = [
        [s.name, s.publisher, s.licence, link("terms", s.terms_url)]
        for s in load_registry(registry_path(root)).values()
    ]
    for file_name, data in load_calibrations(root):
        for s in data.get("provenance", {}).get("sources", []):
            rows.append(
                [
                    link(cell(s.get("citation", "")), s.get("url")),
                    f"calibration file `{file_name}`",
                    s.get("licence") or CITED_ONLY,
                    "-",
                ]
            )
    return table(["Dataset", "Publisher or use", "Licence", "Terms page"], rows)


# ------------------------------------------------------------------ MODEL_AND_ASSUMPTIONS.md


def _ledger(root: Path) -> Any:
    from visionhousing.ledger.loader import load_ledger

    return load_ledger(root / "assumptions" / "parameters.yaml")


def _range_cells(p: Any) -> list[str]:
    return [number(p.central), number(p.low), number(p.high), p.unit]


def assumption_table_sourced(root: Path) -> str:
    """Ledger entries of kind sourced or benchmark."""
    rows = []
    for pid, p in sorted(_ledger(root).items()):
        if p.kind not in {"sourced", "benchmark"}:
            continue
        sources = ", ".join(link(s.id, s.url) for s in p.source) or "-"
        rows.append([f"`{pid}`", p.label, *_range_cells(p), sources, p.status])
    header = ["Id", "Label", "Central", "Low", "High", "Unit", "Source", "Status"]
    return table(header, rows)


def assumption_table_judgement(root: Path) -> str:
    """Ledger entries of kind judgement or calibrated."""
    rows = []
    for pid, p in sorted(_ledger(root).items()):
        if p.kind not in {"judgement", "calibrated"}:
            continue
        rows.append([f"`{pid}`", p.label, *_range_cells(p), p.rationale, p.revisit])
    header = ["Id", "Label", "Central", "Low", "High", "Unit", "Rationale", "Revisit"]
    return table(header, rows)


KEY_ASSUMPTIONS = (
    "adu.unit_size_sqft",
    "adu.already_legal_share",
    "adu.takeup_feasible_annual",
    "adu.nonfinancial_share",
    "adu.hard_cost_per_sqft",
    "missing_middle.sf_only_zoned_share",
    "missing_middle.redevelopment_hazard_annual",
    "missing_middle.cap_rate",
    "parking.transit_bus_min_departures",
    "parking.transit_rail_radius_miles",
    "parking.transit_bus_radius_miles",
    "tod.upzoned_far",
    "multifamily.cap_rate",
    "parking.share_still_built_transit_rich",
    "parking.share_still_built_elsewhere",
    "supply.elasticity_medium_run",
    "supply.saving_passthrough_k",
    "fees.passthrough_k",
)
"""The ledger entries the Overview shows in full: the benchmark choices the team made and
the inputs that move the results most. Every other entry is in the Technical Appendix."""


def key_assumptions(root: Path) -> str:
    """The key ledger entries with their range, kind and rationale, in a fixed order."""
    ledger = _ledger(root)
    rows, missing = [], []
    for pid in KEY_ASSUMPTIONS:
        if pid not in ledger:
            missing.append(pid)
            continue
        p = ledger[pid]
        rows.append([f"`{pid}`", p.label, *_range_cells(p), p.kind, p.rationale])
    header = ["Id", "Label", "Central", "Low", "High", "Unit", "Kind", "Rationale"]
    out = table(header, rows)
    if missing:
        out += "\n\n*Not in the ledger: " + ", ".join(f"`{m}`" for m in missing) + ".*"
    return out


def ledger_summary(root: Path) -> str:
    """Counts of ledger entries by kind and by status, and the ledger fingerprint."""
    ledger = _ledger(root)
    kinds = Counter(p.kind for _, p in ledger.items())
    statuses = Counter(p.status for _, p in ledger.items())
    rows = [["kind", k, number(n)] for k, n in sorted(kinds.items())]
    rows += [["status", s, number(n)] for s, n in sorted(statuses.items())]
    rows.append(["all", "entries", number(len(ledger))])
    note = f"*Ledger fingerprint `{ledger.short_hash()}`.*"
    return table(["Count by", "Value", "Entries"], rows) + "\n\n" + note


SPARSE_STATES = ("PA", "VT")
"""The full-data state and the thin-data state compared in section 3.4."""
SPARSE_RUNS = ("baseline", "adu", "missing_middle", "tod", "parking", "fees", "package")


def _saved_run(root: Path, state: str, key: str) -> dict[str, Any] | None:
    """One saved engine run from the static-site export (unpacked), or None if absent."""
    path = root / "web" / "public" / "data" / state / "runs" / f"{key}.json"
    if not path.is_file():
        return None
    from visionhousing.export_static import read_saved

    return read_saved(path)


def _run_range(run: dict[str, Any], key: str) -> dict[str, Any]:
    """The range a run reports: the baseline for ``baseline``, else the added units."""
    if key == "baseline":
        return run["baseline"]
    if key == "package":
        return run["package"]["combined"]
    return run["reform_results"][key]["added"]


def _width(r: dict[str, Any]) -> float | None:
    """Range width as a multiple of the middle value: (P90 - P10) / P50."""
    p10, p50, p90 = r.get("p10"), r.get("p50"), r.get("p90")
    if p10 is None or p50 is None or p90 is None or not p50:
        return None
    return (p90 - p10) / abs(p50)


def sparse_state_comparison(root: Path) -> str:
    """Likely ranges, Pennsylvania against Vermont, from the saved default engine runs."""
    a, b = SPARSE_STATES
    rows = []
    for key in SPARSE_RUNS:
        runs = {st: _saved_run(root, st, key) for st in SPARSE_STATES}
        if any(r is None for r in runs.values()):
            continue
        ranges = {st: _run_range(r, key) for st, r in runs.items()}
        base = {st: r["baseline"]["p50"] for st, r in runs.items()}
        widths = {st: _width(ranges[st]) for st in SPARSE_STATES}
        cells = [
            runs[a]["reform_results"][key]["title"]
            if key in runs[a]["reform_results"]
            else ("Baseline (no change)" if key == "baseline" else "All five together")
        ]
        for st in SPARSE_STATES:
            r = ranges[st]
            share = "" if key == "baseline" else f" ({100 * r['p50'] / base[st]:.1f}% of baseline)"
            cells.append(
                f"{number(round(r['p10']))} to {number(round(r['p90']))}, "
                f"middle {number(round(r['p50']))}{share}"
            )
            cells.append("-" if widths[st] is None else f"{widths[st]:.2f}")
        ratio = (widths[b] / widths[a]) if widths[a] and widths[b] is not None else None
        cells.append("-" if ratio is None else f"{ratio:.2f}x")
        rows.append(cells)
    if not rows:
        return (
            "*No saved runs for both states: run `make export-static` for each, then this script.*"
        )
    header = [
        "Run",
        f"{a} likely range (units, 2027-2031)",
        f"{a} width",
        f"{b} likely range (units, 2027-2031)",
        f"{b} width",
        f"{b} width / {a} width",
    ]
    note = (
        "*Generated from the default runs saved in `web/public/data/{PA,VT}/runs/` "
        "(seed 0, default draws). Width = (P90 - P10) / P50. Added units for a reform; "
        "permitted units for the baseline.*"
    )
    return note + "\n\n" + table(header, rows)


_THIN_MARKERS = (
    "borrowed",
    "national default",
    "BLS published",
    "imputation",
    "data in this pack",
    "from state law",
)


def sparse_state_inputs(root: Path) -> str:
    """Report-card rows, Pennsylvania against Vermont, from the saved static-site cards."""
    a, b = SPARSE_STATES
    cards = {}
    for st in SPARSE_STATES:
        path = root / "web" / "public" / "data" / st / "report-card.json"
        if not path.is_file():
            return "*No saved report cards for both states: run `make export-static` for each.*"
        cards[st] = {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["inputs"]}

    def status(r: dict[str, Any]) -> str:
        cov = "" if r.get("coverage") is None else f", {100 * r['coverage']:.0f}% filled"
        return f"{r['status']} (tier {r['tier']}{cov})"

    rows = []
    for ident, ra in cards[a].items():
        rb = cards[b].get(ident)
        if rb is None:
            continue
        notes = [t for t in rb["limits"] if any(m in t for m in _THIN_MARKERS)]
        if status(ra) == status(rb) and not notes:
            continue
        rows.append([ra["label"], status(ra), status(rb), " ".join(notes) or "-"])
    note = (
        f"*Generated from `web/public/data/{{{a},{b}}}/report-card.json`. Only inputs that "
        f"differ, or that the {b} card flags as thinner, are listed.*"
    )
    return note + "\n\n" + table(["Input", a, b, f"What is thinner in {b}"], rows)


# ------------------------------------------------- headline results (saved static-site runs)

BILLS = ("pa_hb2186", "pa_sb1346")
"""Pennsylvania bill runs the static export saves beside the benchmark runs."""
BENCHMARKS = ("adu", "missing_middle", "tod", "parking", "fees")


def _saved_json(root: Path, rel: str) -> Any:
    """One saved static-site file under ``web/public/data/`` (unpacked), or None if absent."""
    path = root / "web" / "public" / "data" / rel
    if not path.is_file():
        return None
    from visionhousing.export_static import read_saved

    return read_saved(path)


def _span(r: dict[str, Any] | None) -> str:
    """``P10 to P90 (middle P50)`` in whole units, or ``-``."""
    if not r or r.get("p50") is None:
        return "-"
    lo, mid, hi = (number(round(r[k])) for k in ("p10", "p50", "p90"))
    return f"{lo} to {hi} (middle {mid})"


def _pct_span(r: dict[str, Any] | None) -> str:
    """A percent-of-baseline record as ``P10% to P90% (middle P50%)``, or ``-``."""
    if not r or r.get("p50") is None:
        return "-"
    return f"{r['p10']:.1f}% to {r['p90']:.1f}% (middle {r['p50']:.1f}%)"


def _run_stamp(run: dict[str, Any], folder: str) -> str:
    """The note under a results table: where it came from, and whether it is current."""
    note = (
        f"*Generated from the default runs saved in `{folder}` (seed {run.get('seed')}, "
        f"{number(run.get('draws') or 0)} draws). Likely range = P10 to P90; eight in ten "
        "simulated futures fall inside it."
    )
    if run.get("as_of"):
        note += f" Frozen as of {run['as_of']}."
    else:
        note += (
            " These saved runs were made before the current outcome rules (no as-of date in"
            " them), so they can differ from a live run; `make export-static` refreshes them."
        )
    return note + "*"


def headline_table(state: str) -> Builder:
    """Build the headline table for one state from its saved runs."""

    def build(root: Path) -> str:
        folder = f"web/public/data/{state}/runs/"
        base_run = _saved_run(root, state, "baseline")
        if base_run is None:
            return f"*No saved runs for {state}: run `make export-static STATE={state}`.*"
        rows = [["Baseline (no reform): homes permitted", _span(base_run["baseline"]), "-", "-"]]
        keys = [*BENCHMARKS, *(BILLS if state == "PA" else ())]
        for key in keys:
            run = _saved_run(root, state, key)
            if run is None or key not in run["reform_results"]:
                continue
            o = run["reform_results"][key]
            net = (o.get("gross_net") or {}).get("net_stock")
            rows.append([o["title"], _span(o["added"]), _pct_span(o.get("pct_change")),
                         _span(net)])  # fmt: skip
        pkg = _saved_run(root, state, "package")
        if pkg is not None and pkg.get("package"):
            p = pkg["package"]
            rows.append(["All five together", _span(p["combined"]),
                         _pct_span(p.get("pct_change")), "-"])  # fmt: skip
        header = [
            "Run",
            "Added homes permitted, 2027-2031: likely range",
            "Change against the baseline",
            "Net added homes (after homes torn down)",
        ]
        tail = (
            "\n\n*The baseline row counts all homes permitted with no reform. Every other row "
            "counts the homes a reform adds to that, in the same simulated futures. Net added "
            "homes are shown where the saved run carries them (missing middle and transit-"
            "oriented density replace some existing homes); `-` means not in the saved run.*"
        )
        return _run_stamp(base_run, folder) + "\n\n" + table(header, rows) + tail

    return build


def annual_table(state: str) -> Builder:
    """Build the yearly split (baseline and all five together) from the saved package run."""

    def build(root: Path) -> str:
        run = _saved_run(root, state, "package")
        annual = (run or {}).get("annual") or {}
        base, pkg = annual.get("baseline"), annual.get("package")
        if not base or not pkg:
            return (
                f"*The saved {state} runs do not hold the yearly split yet. A live run (`make "
                "app`) shows it; `make export-static` adds it here.*"
            )
        rows = []
        for year, b, p in zip(base["years"], base["state"], pkg["state"], strict=False):
            rows.append([year, _span(b), _span(p)])
        note = (
            f"*Generated from `web/public/data/{state}/runs/package.json`. Each year's range is "
            "worked out over the draws; the five middle values need not add up to the five-year "
            "middle value.*"
        )
        header = ["Year", "Baseline: homes permitted", "All five together: added homes"]
        return note + "\n\n" + table(header, rows)

    return build


def package_waterfall(state: str) -> Builder:
    """Build the package arithmetic: sum of parts, overlap, interaction, combined."""

    def build(root: Path) -> str:
        run = _saved_run(root, state, "package")
        if run is None or not run.get("package"):
            return f"*No saved package run for {state}: run `make export-static STATE={state}`.*"
        p = run["package"]
        rows = [
            ["Each reform alone, added up", _span(p.get("sum_of_parts"))],
            ["Minus lots counted twice (overlap)", _span(p.get("overlap"))],
            ["Plus savings that help another reform (interaction)", _span(p.get("interaction"))],
            ["Equals all five together", _span(p.get("combined"))],
        ]
        note = f"*Generated from `web/public/data/{state}/runs/package.json`.*"
        return note + "\n\n" + table(["Step", "Homes, 2027-2031: likely range"], rows)

    return build


def top_drivers(state: str, per_reform: int = 3) -> Builder:
    """Build the top inputs per reform from the tornado saved with each reform's run."""

    def build(root: Path) -> str:
        rows = []
        for key in BENCHMARKS:
            run = _saved_run(root, state, key)
            drivers = ((run or {}).get("explanation") or {}).get("drivers") or {}
            entry = drivers.get(key)
            if not entry:
                continue
            for item in entry.get("items", [])[:per_reform]:
                rows.append(
                    [
                        entry.get("title", key),
                        f"{item['label']} (`{item['parameter']}`)",
                        f"{number(round(item['total_low']))} at {item['low']:.3g}",
                        f"{number(round(item['total_high']))} at {item['high']:.3g}",
                    ]
                )
        if not rows:
            return f"*No saved drivers for {state}: run `make export-static STATE={state}`.*"
        note = (
            f"*Generated from the tornado saved with each reform's run in "
            f"`web/public/data/{state}/runs/`. Each input is moved alone from its low to its high "
            "value (Assumption Ledger) while the others stay central; the figures are the "
            "five-year homes the reform adds at each end.*"
        )
        header = ["Reform", "Input", "Added homes at its low value", "At its high value"]
        return note + "\n\n" + table(header, rows)

    return build


def baseline_contest(state: str) -> Builder:
    """Build the baseline rule contest from the saved validation table."""

    def build(root: Path) -> str:
        data = _saved_json(root, f"{state}/validation.json")
        if not data or not data.get("rows"):
            return f"*No saved validation table for {state}: run `make export-static`.*"
        rows = []
        for r in data["rows"]:
            cov = r.get("coverage_80")
            rows.append(
                [
                    f"`{r['rule']}`" + (" (chosen)" if r.get("chosen") else ""),
                    number(r.get("cases") or 0),
                    "-" if r.get("median_abs_log_ratio") is None
                    else f"{r['median_abs_log_ratio']:.3f}",
                    "-" if cov is None else f"{100 * cov:.0f}%",
                ]
            )  # fmt: skip
        note = (
            f"*Generated from `web/public/data/{state}/validation.json`. Typical miss = median of "
            "|log(actual / projected)| over past county five-year totals; caught = share of "
            "past totals inside the P10-P90 range (built leaving the county out). The chosen "
            f"rule here: `{data.get('chosen_rule')}`.*"
        )
        header = ["Rule", "Past cases", "Typical miss", "Range caught the real total"]
        return note + "\n\n" + table(header, rows)

    return build


def baseline_calibration(root: Path) -> str:
    """The pre-registered out-of-time calibration test of the baseline range, by state."""
    path = root / "assumptions" / "calibration" / "baseline_calibration.yaml"
    if not path.is_file():
        return "*No calibration results: run `visionhousing validate-baseline-calibration`.*"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = []
    for state, st in (data.get("states") or {}).items():
        b, a, t = st["before"], st["after"], st.get("state_total_check") or {}
        rows.append(
            [
                state,
                f"`{b['rule']}`",
                f"{100 * b['coverage']:.0f}% / {b['width']:.2f} / {b['median_abs_error']:.3f}",
                f"`{a['rule']}`",
                f"{100 * a['coverage']:.0f}% / {a['width']:.2f} / {a['median_abs_error']:.3f}",
                number(a["cases"]),
                "-" if t.get("coverage") is None
                else f"{t['covered']} of {t['cases']}",
            ]
        )  # fmt: skip
    d = data.get("decision") or {}
    words = {
        "in_band": "pooled coverage inside the band and every state at least the floor",
        "under_band": "pooled coverage below the band",
        "over_band": "pooled coverage above the band",
        "state_short": "pooled coverage in the band but a state below the floor",
    }
    pre = data.get("preregistration") or {}
    note = (
        "*Generated from `assumptions/calibration/baseline_calibration.yaml` (block "
        f"`oot_calibration`, fingerprint `{str(pre.get('sha256', ''))[:12]}`). Before = today's "
        "rule and its contest coverage (other counties' earlier errors); after = the winner "
        "of the same choosing rule re-run with calibrated coverage. Cells: coverage / mean "
        "width (P90 - P10) / P50 / typical miss of the P50. State totals = the same method "
        "on the state's own yearly totals (caught / scored origins; descriptive).*"
    )
    header = ["State", "Rule before", "Before", "Rule after", "After (calibrated)",
              "Scored cases", "State totals caught"]  # fmt: skip
    verdict = (
        f"**Decision: {d.get('outcome', '?')}** ({words.get(d.get('reason'), d.get('reason'))}: "
        f"{100 * float(d.get('pooled_coverage') or 0):.1f}% of "
        f"{number(d.get('pooled_cases') or 0)} cases)."
    )
    return note + "\n\n" + table(header, rows) + "\n\n" + verdict


def baseline_reliability(root: Path) -> str:
    """How often ranges like the shipped baseline range caught past totals, and the E14 test."""
    path = root / "assumptions" / "calibration" / "baseline_reliability.yaml"
    if not path.is_file():
        return "*No reliability counts: run `visionhousing validate-baseline-reliability`.*"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def caught(c: dict | None) -> str:
        if not c or not c.get("cases"):
            return "-"
        return f"{c['covered']} of {number(c['cases'])} ({100 * c['covered'] / c['cases']:.0f}%)"

    rows = []
    for state, st in (data.get("states") or {}).items():
        county = st.get("county_ranges") or {}
        groups = county.get("groups") or {}
        rows.append(
            [state, f"`{st['rule']}`", caught(st.get("state_range")), caught(county.get("all")),
             *(caught(groups.get(g)) for g in ("small", "medium", "large"))]
        )  # fmt: skip
    pre = data.get("preregistration") or {}
    note = (
        "*Generated from `assumptions/calibration/baseline_reliability.yaml` (block "
        f"`baseline_reliability`, fingerprint `{str(pre.get('sha256', ''))[:12]}`). Each cell: "
        "past cases whose real five-year total fell inside a range built the way the tool "
        "builds it, using only errors known at the time (out of time). State range: the "
        "state's own earlier totals, with the small-sample and growth-aware widening; county "
        "ranges: other counties' earlier errors in the same size class, by size class at the "
        "case's start year.*"
    )
    header = ["State", "Rule", "State range caught", "County ranges caught", "Small counties",
              "Medium counties", "Large counties"]  # fmt: skip
    out = note + "\n\n" + table(header, rows)
    nat = root / "assumptions" / "calibration" / "national_state_calibration.yaml"
    if nat.is_file():
        doc = yaml.safe_load(nat.read_text(encoding="utf-8")) or {}
        parts = []
        for v, res in (doc.get("variants") or {}).items():
            p, j = res.get("pooled") or {}, res.get("judged") or {}
            named = ", ".join(
                f"{k} {x['covered']} of {x['cases']}"
                for k, x in (res.get("named_states") or {}).items()
            )
            parts.append(
                f"`{v}` caught {p.get('covered')} of {number(p.get('cases') or 0)} state totals "
                f"({100 * float(p.get('coverage') or 0):.1f}%, "
                f"mean width {float(p.get('width') or 0):.2f}; "
                f"{named}; {j.get('reason')})"
            )
        d = doc.get("decision") or {}
        pre2 = doc.get("preregistration") or {}
        out += (
            "\n\n*National state-total calibration (block `national_state_calibration`, "
            f"fingerprint `{str(pre2.get('sha256', ''))[:12]}`), out of time over every "
            "state: " + "; ".join(parts) + f". **Decision: {d.get('outcome', '?')}**.*"
        )
    return out


def baseline_reconciliation(root: Path) -> str:
    """The baseline card: state reconciliation, PA window sensitivity and the 2026 bridge."""
    path = root / "assumptions" / "calibration" / "baseline_reconciliation.yaml"
    if not path.is_file():
        return "*No baseline card: run `visionhousing baseline-card`.*"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    card = data.get("card") or {}
    rel = card.get("data_release") or {}
    states = data.get("states") or {}
    years = sorted({int(y) for st in states.values() for y in (st.get("annual") or {})})

    def span(v: list | None) -> str:
        return f"{v[0]}-{v[1]}" if v else "-"

    rows = []
    for state, st in states.items():
        annual = st.get("annual") or {}
        check = (
            f"**{number(st['state_component'])}** = {cell(st.get('formula', ''))}"
            if st.get("role") == "reconciled"
            else "outside control only (county shrinkage; not reproducible from state totals)"
        )
        rows.append(
            [state, *(number(annual.get(y, 0)) for y in years), f"`{st['rule']}`",
             span((st.get("historical_years") or {}).get("level_years")), check]
        )  # fmt: skip
    gap = ", ".join(str(y) for y in card.get("forecast_gap") or []) or "none"
    note = (
        "*Generated from `assumptions/calibration/baseline_reconciliation.yaml` (command "
        "`visionhousing baseline-card`, settings `assumptions/baseline_card.yaml`). "
        f"Units: {card.get('unit_fields_label', '')} (columns "
        + ", ".join(f"`{c}`" for c in card.get("unit_fields") or [])
        + f"). Data release: {rel.get('vintage', '?')}, posted {rel.get('release_date', '?')} "
        f"({rel.get('release_date_basis', '')}). Information cutoff: "
        f"{card.get('information_cutoff', '?')}. Projected years: "
        f"{span(card.get('forecast_window'))}; forecast gap: {gap} "
        "(no full-year count; the rule's yearly level is carried through it).*"
    )
    header = ["State", *(str(y) for y in years), "Shipped rule", "Level years", "State check"]
    out = [note, table(header, rows)]
    for did, d in (data.get("diagnostics") or {}).items():
        out.append(
            f"**Window sensitivity `{did}`** ({d['state']}, record only): "
            f"{cell(d['formula'])}. {cell(d['status'])}"
        )
    nc = ((data.get("bridge") or {}).get("nowcast")) or {}
    if nc.get("status") == "available":
        nrows = []
        for state, v in (nc.get("states") or {}).items():
            carried = number(v["carried_annual"])
            if "shrinkage" in str(v.get("carried_annual_basis")):
                carried += " (state-series mean)"
            low_high = f"{number(v['low'])}-{number(v['high'])}"
            nrows.append([state, number(v["ytd"]), low_high, number(v["mid"]), carried])
        out.append(
            f"**{nc['year']} bridge, nowcast `{nc['id']}`** (diagnostic only; no estimate "
            f"uses it). Latest monthly file released by the cutoff: `{nc['file']}` (posted "
            f"{nc['release_date']}); the next file, `{nc.get('next_file')}`, was posted "
            f"{nc.get('next_release_date')} and is not used. Nowcast = year-to-date units / s, "
            "s = the same months' share of the full year in "
            f"{span([nc['history_years'][0], nc['history_years'][-1]])} (low, middle and high "
            "use the highest, median and lowest s). A year-to-date count is never a full year."
        )
        months = f"Jan-{calendar.month_abbr[nc['through_month']]} {nc['year']}"
        header2 = [
            "State",
            f"Units {months}",
            "Nowcast low-high",
            "Nowcast middle",
            "Carried yearly level",
        ]
        out.append(table(header2, nrows))
    elif nc:
        reason = cell(nc.get("reason", ""))
        out.append(f"**Gap-year bridge, nowcast `{nc.get('id')}`**: {nc.get('status')} ({reason}).")
    return "\n\n".join(out)


# --------------------------------------------------------------------------- driver

Builder = Callable[[Path], str]

DOCUMENTS: dict[str, dict[str, Builder]] = {
    "DATA_PROVENANCE.md": {
        "provenance_table": provenance_table,
        "calibration_provenance": calibration_provenance,
    },
    "DISCLOSURES.md": {
        "browser_libraries": browser_libraries,
        "dependency_table": dependency_table,
        "licence_exceptions": licence_exceptions,
        "composite_licences": composite_licences,
        "dataset_licences": dataset_licences,
    },
    "docs/MODEL_AND_ASSUMPTIONS.md": {
        "key_assumptions": key_assumptions,
        "ledger_summary": ledger_summary,
        "sparse_state_comparison": sparse_state_comparison,
        "baseline_contest_pa": baseline_contest("PA"),
        "baseline_reliability": baseline_reliability,
        "baseline_reconciliation": baseline_reconciliation,
        "headline_table_pa": headline_table("PA"),
        "top_drivers_pa": top_drivers("PA"),
    },
    "docs/modules/baseline.md": {
        "baseline_reconciliation": baseline_reconciliation,
    },
    "docs/TECHNICAL_APPENDIX.md": {
        "assumption_table_sourced": assumption_table_sourced,
        "assumption_table_judgement": assumption_table_judgement,
        "sparse_state_inputs": sparse_state_inputs,
        "annual_table_pa": annual_table("PA"),
        "baseline_calibration": baseline_calibration,
        "headline_table_ma": headline_table("MA"),
        "headline_table_wy": headline_table("WY"),
    },
    "docs/POLICY_SCENARIO_COMPARISON.md": {
        "headline_table_pa": headline_table("PA"),
        "package_waterfall_pa": package_waterfall("PA"),
        "top_drivers_pa": top_drivers("PA", per_reform=2),  # two per reform: 4-page limit
        "headline_table_vt": headline_table("VT"),
    },
}


def render(
    root: Path, documents: Mapping[str, Mapping[str, Builder]] = DOCUMENTS
) -> dict[Path, str]:
    """Return the new text of every document, with each generated block rebuilt."""
    out = {}
    for rel, builders in documents.items():
        path = root / rel
        text = path.read_text(encoding="utf-8")
        for name, build in builders.items():
            try:
                text = replace_block(text, name, build(root))
            except BlockError as err:
                raise BlockError(f"{rel}: {err}") from None
        out[path] = text
    return out


def stale(root: Path) -> list[str]:
    """Return the documents whose generated blocks differ from what would be written."""
    return [
        path.relative_to(root).as_posix()
        for path, text in render(root).items()
        if path.read_text(encoding="utf-8") != text
    ]


def main(argv: list[str] | None = None) -> int:
    """Regenerate the blocks, or with ``--check`` report stale ones; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="write nothing, fail if stale")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check:
            old = stale(root)
            if old:
                print("generated blocks are stale in: " + ", ".join(old), file=sys.stderr)
                print("run: uv run python scripts/gen_docs_tables.py", file=sys.stderr)
                return 1
            print("generated blocks: up to date")
            return 0
        changed = []
        for path, text in render(root).items():
            if path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8")
                changed.append(path.relative_to(root).as_posix())
    except BlockError as err:
        print(f"gen_docs_tables: {err}", file=sys.stderr)
        return 1
    print("generated blocks: " + (", ".join(changed) + " updated" if changed else "no change"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
