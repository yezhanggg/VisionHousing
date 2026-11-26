"""Licence audit: fail if any dependency has a licence outside the allow-list.

Why: the Challenge requires permissive, OSI-approved licences only. This script is
the project's licence lister. It uses only the Python standard library, so it adds
no dependency of its own.

What it does:
1. Reads every package pinned in ``uv.lock`` (all dependency groups).
2. Looks up each one's installed metadata with ``importlib.metadata`` and finds its
   declared licence (the SPDX ``License-Expression`` first, then the short
   ``License`` field, then the "License ::" classifiers).
3. Evaluates composite expressions such as ``MIT AND (Apache-2.0 OR BSD-2-Clause)``
   one component at a time: every part joined by AND must be allowed, and at least
   one branch of an OR must be allowed.
4. Looks inside each installed package for **bundled native code**: compiled
   libraries shipped next to the package's own modules (``.dylib``, ``.so``,
   ``.dll`` files that are not Python extension modules, such as the
   ``pkg/.dylibs/libgeos.dylib`` a wheel tool copies in) and every licence file
   in the package's ``.dist-info`` (``licenses/`` and ``LICENSE*`` / ``COPYING*``
   / ``NOTICE*``). A package fails when such a licence file names a copyleft
   licence for bundled code: a ``License:`` line whose expression holds a denied
   id (GPL, LGPL, ...), or the title of a GNU licence text (``GNU LESSER GENERAL
   PUBLIC LICENSE``). The declared licence of a wheel describes only the
   package's own code, so this is how a BSD package that ships an LGPL library
   inside its wheel (Shapely with GEOS, SciPy with libquadmath) is caught. The
   GCC runtime library exception (``GPL-3.0-or-later WITH GCC-exception-3.1``)
   stays allowed, as above.
5. Does the same for ``web/package-lock.json`` when that file exists.
6. Prints a report and exits 1 if anything is denied, unknown or not installed.

Usage: ``uv run python scripts/licence_audit.py [--lock uv.lock] [--npm-lock PATH]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# SPDX ids we accept. All are permissive and allow commercial use.
ALLOWED = {
    "0BSD",
    "Apache-2.0",
    "BSD-1-Clause",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-3-Clause-Clear",
    "BSD",  # classifier "BSD License" without a version: every variant is permissive
    "BSL-1.0",
    "CC0-1.0",
    "HPND",
    "ISC",
    "MIT",
    "MIT-0",
    "PSF-2.0",
    "Python-2.0",
    "Unicode-3.0",
    "Unicode-DFS-2016",
    "Unlicense",
    "Zlib",
}

# Licence families that fail even if someone adds them to ALLOWED by mistake.
DENIED_PREFIXES = (
    "GPL",
    "AGPL",
    "LGPL",
    "MPL",
    "EUPL",
    "SSPL",
    "BUSL",
    "CC-BY-NC",
    "Commons-Clause",
    "Elastic",
    "PolyForm",
)

# "X WITH exception" pairs that are explicitly permitted. The GCC runtime-library
# exception lets compiled programs ship the compiler's runtime under any licence.
ALLOWED_WITH = {("GPL-3.0-or-later", "GCC-exception-3.1"), ("GPL-3.0", "GCC-exception-3.1")}

# Common free-text spellings found in the short "License" field and in classifiers.
ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "the mit license": "MIT",
    "mit-0": "MIT-0",
    "bsd": "BSD",
    "bsd license": "BSD",
    "new bsd": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "2-clause bsd": "BSD-2-Clause",
    "3-clause bsd": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "simplified bsd": "BSD-2-Clause",
    "apache": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "isc": "ISC",
    "isc license": "ISC",
    "isc license (iscl)": "ISC",
    "psf": "PSF-2.0",
    "psf license": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "zlib": "Zlib",
    "zlib/libpng license": "Zlib",
    "public domain": "Unlicense",
    "the unlicense (unlicense)": "Unlicense",
    "cc0 1.0 universal (cc0 1.0) public domain dedication": "CC0-1.0",
    "gnu general public license v3 (gplv3)": "GPL-3.0",
    "gnu general public license v2 (gplv2)": "GPL-2.0",
    "gnu general public license (gpl)": "GPL",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.0-or-later",
    "gnu library or lesser general public license (lgpl)": "LGPL",
    "gnu affero general public license v3": "AGPL-3.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "european union public licence 1.2 (eupl 1.2)": "EUPL-1.2",
}

# Packages pinned in uv.lock that are installed only on other platforms, so their
# metadata cannot be read here. Each entry was checked by hand against the package's
# own published metadata. A locked package that is neither installed nor listed fails.
PLATFORM_ONLY = {
    "colorama": "BSD-3-Clause",  # Windows-only dependency of pytest
}

_SHORT_FIELD_LIMIT = 80  # longer "License" fields are full licence texts, not names


@dataclass
class Finding:
    """One audited package: where it came from, its licence, and the verdict."""

    ecosystem: str
    name: str
    version: str
    expression: str
    source: str
    ok: bool
    notes: list[str] = field(default_factory=list)


def canonical(token: str) -> str:
    """Return the SPDX spelling of one licence id, matching case-insensitively."""
    token = token.strip()
    if token.endswith("+"):
        token = token[:-1] + "-or-later"
    lowered = token.lower()
    for known in ALLOWED:
        if known.lower() == lowered:
            return known
    return ALIASES.get(lowered, token)


def is_denied(licence_id: str) -> bool:
    """Return True for copyleft, source-available and non-commercial licence ids."""
    upper = licence_id.upper()
    return any(upper.startswith(p.upper()) for p in DENIED_PREFIXES) or "NC" in upper.split("-")


def _tokenize(expression: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expression)


def evaluate(expression: str) -> tuple[bool, list[str]]:
    """Decide whether a licence expression is acceptable, one component at a time.

    Returns ``(ok, notes)``. AND needs every part allowed; OR needs one allowed branch.
    Unparseable or empty expressions are not acceptable.
    """
    tokens = _tokenize(expression)
    if not tokens:
        return False, ["no licence declared"]
    pos = 0
    notes: list[str] = []

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("expression ends early")
        pos += 1
        return tokens[pos - 1]

    def atom() -> bool:
        tok = take()
        if tok == "(":
            result = or_expr()
            if take() != ")":
                raise ValueError("missing closing bracket")
            return result
        if tok.upper() in {"AND", "OR", "WITH", ")"}:
            raise ValueError(f"unexpected {tok!r}")
        lic = canonical(tok)
        if (peek() or "").upper() == "WITH":
            take()
            exc = take()
            ok = (lic, exc) in ALLOWED_WITH
            notes.append(f"{lic} WITH {exc}: {'permitted exception' if ok else 'not allowed'}")
            return ok
        if is_denied(lic):
            notes.append(f"{lic}: denied licence family")
            return False
        if lic in ALLOWED:
            return True
        notes.append(f"{lic}: not on the allow-list")
        return False

    def and_expr() -> bool:
        results = [atom()]
        while (peek() or "").upper() == "AND":
            take()
            results.append(atom())
        return all(results)

    def or_expr() -> bool:
        results = [and_expr()]
        while (peek() or "").upper() == "OR":
            take()
            results.append(and_expr())
        return any(results)

    try:
        ok = or_expr()
        if pos != len(tokens):
            raise ValueError(f"unexpected {tokens[pos]!r}")
    except ValueError as err:
        return False, [f"cannot parse {expression!r}: {err}"]
    return ok, notes


def declared_licence(meta: metadata.PackageMetadata) -> tuple[str, str]:
    """Find a package's declared licence and say which metadata field it came from."""
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression, "License-Expression"
    short = (meta.get("License") or "").strip()
    if short and "\n" not in short and len(short) <= _SHORT_FIELD_LIMIT:
        mapped = ALIASES.get(short.lower())
        if mapped:
            return mapped, "License"
        parts = _tokenize(short)
        if all(canonical(p) in ALLOWED or p.upper() in {"AND", "OR", "(", ")"} for p in parts):
            return short, "License"
    classifiers = [
        c.split("::")[-1].strip()
        for c in (meta.get_all("Classifier") or [])
        if c.startswith("License ::") and c.strip() != "License :: OSI Approved"
    ]
    if classifiers:
        ids = [ALIASES.get(c.lower(), c) for c in classifiers]
        expr = " OR ".join(i if " " not in i else f"({i})" for i in ids)
        return expr, "Classifier"
    if short:
        return short, "License (unrecognised text)"
    return "", "none"


_NATIVE = re.compile(r"\.(dylib|so|dll)(\.[0-9][0-9.]*)?$", re.IGNORECASE)
_EXTENSION = re.compile(r"(\.cpython-|\.abi3\.|\.pypy|\.cp3\d+-win|\.pyd$)", re.IGNORECASE)
_LICENCE_FILE = re.compile(r"(^|/)(licen[cs]es?|copying|notice|authors)([^/]*)$", re.IGNORECASE)
_LICENCE_LINE = re.compile(r"^\s*Licen[cs]e\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_GNU_TITLE = re.compile(
    r"^\s*GNU (LESSER |LIBRARY |AFFERO )?GENERAL PUBLIC LICEN[CS]E\s*$", re.MULTILINE
)
_GCC_EXCEPTION = re.compile(r"GCC RUNTIME LIBRARY EXCEPTION|GCC-exception-3\.1", re.IGNORECASE)


def bundled_native_libraries(dist: metadata.Distribution) -> list[str]:
    """Compiled libraries a package ships besides its own Python extension modules."""
    out = []
    for f in dist.files or []:
        name = str(f)
        base = name.rsplit("/", 1)[-1]
        if _NATIVE.search(base) and not _EXTENSION.search(base):
            out.append(name)
    return sorted(out)


def licence_files(dist: metadata.Distribution) -> list[metadata.PackagePath]:
    """The licence, copying and notice files inside a package's ``.dist-info`` folder."""
    out = []
    for f in dist.files or []:
        name = str(f)
        if ".dist-info/" not in name:
            continue
        inner = name.split(".dist-info/", 1)[1]
        if inner.startswith("licenses/") or _LICENCE_FILE.search(inner):
            out.append(f)
    return out


def copyleft_mentions(text: str) -> list[str]:
    """Copyleft licences a licence file names for bundled code (empty when none).

    Two signs count: a ``License: <expression>`` line (the format wheel tools
    use to list bundled libraries) holding a denied id that is not a permitted
    ``WITH`` exception, and the title line of a GNU licence text. A full GPL text
    is allowed when the same file grants the GCC runtime library exception.
    """
    found = []
    for expr in _LICENCE_LINE.findall(text):
        tokens = _tokenize(expr)
        i = 0
        while i < len(tokens):
            lic = canonical(tokens[i])
            if i + 2 < len(tokens) and tokens[i + 1].upper() == "WITH":
                if (lic, tokens[i + 2]) not in ALLOWED_WITH and is_denied(lic):
                    found.append(f"{lic} WITH {tokens[i + 2]}")
                i += 3
                continue
            if lic.upper() not in {"AND", "OR", "(", ")"} and is_denied(lic):
                found.append(lic)
            i += 1
    for title in _GNU_TITLE.findall(text):
        kind = {"LESSER ": "LGPL", "LIBRARY ": "LGPL", "AFFERO ": "AGPL"}.get(title, "GPL")
        if kind == "GPL" and _GCC_EXCEPTION.search(text):
            continue
        found.append(f"{kind} (full licence text)")
    return sorted(set(found))


def bundled_findings(dist: metadata.Distribution) -> tuple[bool, list[str]]:
    """Check a package's bundled native code and licence files: ``(ok, notes)``."""
    notes = []
    ok = True
    libs = bundled_native_libraries(dist)
    for f in licence_files(dist):
        try:
            text = Path(f.locate()).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        hits = copyleft_mentions(text or "")
        if hits:
            ok = False
            where = str(f).split(".dist-info/", 1)[-1]
            notes.append(f"licence file {where} names {', '.join(hits)} for bundled code")
    if libs:
        shown = ", ".join(p.rsplit("/", 1)[-1] for p in libs[:3])
        more = f" and {len(libs) - 3} more" if len(libs) > 3 else ""
        notes.append(f"bundles native libraries: {shown}{more}")
    return ok, notes


def locked_python_packages(lock_path: Path) -> list[tuple[str, str]]:
    """List ``(name, version)`` for every package pinned in a uv lockfile."""
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return [(p["name"], p.get("version", "")) for p in data.get("package", [])]


def audit_python(
    packages: Iterable[tuple[str, str]], search_path: list[str] | None = None
) -> list[Finding]:
    """Audit each locked Python package against its installed metadata."""
    installed = {
        metadata_name(d): d
        for d in (
            metadata.distributions(path=search_path) if search_path else metadata.distributions()
        )
    }
    findings = []
    for name, version in packages:
        dist = installed.get(normalise(name))
        if dist is None:
            if normalise(name) in PLATFORM_ONLY:
                expr = PLATFORM_ONLY[normalise(name)]
                ok, notes = evaluate(expr)
                notes.append("other platforms only; licence from the reviewed table")
                findings.append(Finding("python", name, version, expr, "table", ok, notes))
            else:
                findings.append(
                    Finding(
                        "python",
                        name,
                        version,
                        "",
                        "not installed",
                        False,
                        ["locked but not installed: run `make setup`"],
                    )
                )
            continue
        expr, source = declared_licence(dist.metadata)
        ok, notes = evaluate(expr) if expr else (False, ["no licence declared"])
        bundled_ok, bundled_notes = bundled_findings(dist)
        ok = ok and bundled_ok
        notes = [*notes, *bundled_notes]
        if version and dist.version != version:
            ok = False
            notes.append(f"installed {dist.version} differs from locked {version}")
        findings.append(Finding("python", name, dist.version, expr, source, ok, notes))
    return findings


def audit_npm(lock_path: Path) -> list[Finding]:
    """Audit every package in an npm ``package-lock.json`` (lockfile version 2 or 3)."""
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    findings = []
    for key, info in sorted(data.get("packages", {}).items()):
        if not key:  # the root project itself
            continue
        name = info.get("name") or key.split("node_modules/")[-1]
        raw = info.get("license", "")
        expr = raw.get("type", "") if isinstance(raw, dict) else str(raw)
        ok, notes = evaluate(expr) if expr else (False, ["no licence declared"])
        findings.append(
            Finding("npm", name, info.get("version", ""), expr, "package-lock", ok, notes)
        )
    return findings


def normalise(name: str) -> str:
    """Normalise a package name the way Python packaging does (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def metadata_name(dist: metadata.Distribution) -> str:
    """Return the normalised name of an installed distribution."""
    return normalise(dist.metadata["Name"] or "")


def render(findings: list[Finding]) -> str:
    """Format the findings as a plain-text table with a one-line summary."""
    lines = [f"{'ecosystem':<9} {'package':<22} {'version':<12} {'verdict':<7} licence (source)"]
    for f in findings:
        verdict = "ok" if f.ok else "FAIL"
        lines.append(
            f"{f.ecosystem:<9} {f.name:<22} {f.version:<12} {verdict:<7} "
            f"{f.expression or '-'} ({f.source})"
        )
        lines.extend(f"{'':<53}- {n}" for n in f.notes)
    bad = [f for f in findings if not f.ok]
    lines.append("")
    lines.append(
        f"licence audit: {len(findings)} packages checked, {len(bad)} failing, "
        f"{len(findings) - len(bad)} allowed"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the audit from the command line; return 0 when every licence is allowed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    parser.add_argument("--npm-lock", type=Path, default=ROOT / "web" / "package-lock.json")
    parser.add_argument(
        "--search-path",
        action="append",
        default=None,
        help="look for installed packages here instead of the current environment",
    )
    args = parser.parse_args(argv)
    if not args.lock.exists():
        print(f"licence audit: lockfile not found: {args.lock.name}", file=sys.stderr)
        return 1
    findings = audit_python(locked_python_packages(args.lock), args.search_path)
    if args.npm_lock.exists():
        findings += audit_npm(args.npm_lock)
    print(render(findings))
    return 0 if all(f.ok for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
