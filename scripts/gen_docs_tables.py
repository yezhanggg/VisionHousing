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
- ``docs/MODEL_AND_ASSUMPTIONS.md``: the two assumption tables and the ledger
  summary, from ``assumptions/parameters.yaml``.

The output depends only on files in the repository and on the installed package
metadata. It does not read ``data/manifest.json``, because that file exists only
on a machine that has fetched the data. Running it twice gives the same files.

Usage: ``uv run python scripts/gen_docs_tables.py [--check] [--root PATH]``.
``--check`` writes nothing and exits 1 if any block differs from what it would write.
"""

from __future__ import annotations

import argparse
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
    that group's name. Packages reached from several get every name, in order.
    """
    packages = {p["name"]: p for p in lock.get("package", [])}
    root = packages.get(project, {})
    starts: dict[str, list[str]] = {RUN_TIME: [d["name"] for d in root.get("dependencies", [])]}
    for group, deps in sorted(root.get("dev-dependencies", {}).items()):
        starts[group] = [d["name"] for d in deps]
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
        "assumption_table_sourced": assumption_table_sourced,
        "assumption_table_judgement": assumption_table_judgement,
        "ledger_summary": ledger_summary,
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
