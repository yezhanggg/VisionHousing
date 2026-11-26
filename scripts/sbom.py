"""Software bill of materials: write a CycloneDX 1.5 JSON list of every pinned dependency.

Why: the Challenge asks for a documented software bill of materials (SBOM), a
machine-readable list of every third-party package the tool is built from. This
script writes it from the two lockfiles, so it always matches what ``make setup``
installs. It uses only the Python standard library.

What it does:
1. Reads every package pinned in ``uv.lock`` (Python) and ``web/package-lock.json``
   (browser libraries and build tools).
2. Gives each one its name, version, package URL (``purl``, the standard address
   of a package such as ``pkg:pypi/numpy@2.3.0``), licence and the published
   checksum of its download, when the lockfile has one.
3. Finds Python licences with the licence audit's own logic
   (``scripts/licence_audit.py``), from the installed package metadata, so the SBOM
   and the audit always agree. npm licences come from the lockfile.
4. Records which package depends on which (the ``dependencies`` section).
5. Writes ``sbom/visionhousing.cdx.json``. The output has no timestamp and its
   serial number is derived from its content, so the same lockfiles always give the
   same file. ``--check`` writes nothing and exits 1 when the committed file is stale.

Usage: ``uv run python scripts/sbom.py [--check] [--out PATH] [--lock PATH] [--npm-lock PATH]``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import tomllib
import uuid
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import quote

import licence_audit as audit

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "sbom" / "visionhousing.cdx.json"
SPEC_VERSION = "1.5"
TOOL_NAME = "visionhousing-sbom"
_OPERATORS = {"AND", "OR", "WITH", "(", ")"}
# SPDX ids the licence audit accepts that are not real SPDX ids (a bare classifier).
_NOT_SPDX = {"BSD"}


# --------------------------------------------------------------------------- licences


def licence_entry(expression: str) -> list[dict[str, Any]]:
    """Turn a declared licence into CycloneDX ``licenses`` entries.

    A single SPDX id becomes ``{"license": {"id": ...}}``; a valid composite
    expression becomes ``{"expression": ...}``; anything else (free text, a bare
    "BSD" classifier) is kept as a licence ``name`` so nothing is invented.
    """
    expression = (expression or "").strip()
    if not expression:
        return []
    tokens = audit._tokenize(expression)
    canon = [t if t.upper() in _OPERATORS else audit.canonical(t) for t in tokens]
    known = [t for t in canon if t.upper() not in _OPERATORS]
    spdx_like = all(t in audit.ALLOWED and t not in _NOT_SPDX for t in known)
    if len(tokens) == 1 and spdx_like:
        return [{"license": {"id": canon[0]}}]
    if spdx_like and known:
        return [{"expression": " ".join(canon).replace("( ", "(").replace(" )", ")")}]
    return [{"license": {"name": expression}}]


def python_licences(
    names: list[str], search_path: list[str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Find each locked Python package's declared licence; also list the ones not found.

    Uses the installed metadata, like the licence audit. A package installed only on
    other platforms takes its licence from the audit's reviewed table.
    """
    dists = metadata.distributions(path=search_path) if search_path else metadata.distributions()
    installed = {audit.metadata_name(d): d for d in dists}
    found: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        key = audit.normalise(name)
        dist = installed.get(key)
        if dist is not None:
            expression, _source = audit.declared_licence(dist.metadata)
            found[key] = expression
        elif key in audit.PLATFORM_ONLY:
            found[key] = audit.PLATFORM_ONLY[key]
        else:
            missing.append(name)
    return found, missing


# --------------------------------------------------------------------------- Python


def pypi_purl(name: str, version: str) -> str:
    """Return the package URL of a Python package (name normalised, as PyPI does)."""
    return f"pkg:pypi/{audit.normalise(name)}@{quote(version, safe='')}"


def _sha256(entry: dict[str, Any] | None) -> list[dict[str, str]]:
    raw = (entry or {}).get("hash", "")
    if raw.startswith("sha256:"):
        return [{"alg": "SHA-256", "content": raw.split(":", 1)[1]}]
    return []


def python_components(
    lock: dict[str, Any], licences: dict[str, str]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the root project, the Python components and their dependency links.

    The project itself (the editable package) is returned separately as the root.
    A component's hash is the source archive's checksum; when a package publishes
    no source archive and exactly one wheel, the wheel's checksum is used.
    """
    packages = lock.get("package", [])
    by_name = {audit.normalise(p["name"]): p for p in packages}
    root = None
    components = []
    deps = []
    for pkg in sorted(packages, key=lambda p: audit.normalise(p["name"])):
        name, version = pkg["name"], pkg.get("version", "")
        ref = pypi_purl(name, version)
        children = set()
        for dep in pkg.get("dependencies", []):
            child = by_name.get(audit.normalise(dep["name"]))
            if child:
                children.add(pypi_purl(child["name"], child.get("version", "")))
        for group in ("optional-dependencies", "dev-dependencies"):
            for listed in pkg.get(group, {}).values():
                for dep in listed:
                    child = by_name.get(audit.normalise(dep["name"]))
                    if child:
                        children.add(pypi_purl(child["name"], child.get("version", "")))
        deps.append({"ref": ref, "dependsOn": sorted(children)})
        source = pkg.get("source", {})
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": ref,
        }
        lic = licence_entry(licences.get(audit.normalise(name), ""))
        if lic:
            component["licenses"] = lic
        if "registry" not in source:
            component["type"] = "application"
            root = component
            continue
        hashes = _sha256(pkg.get("sdist"))
        wheels = pkg.get("wheels", [])
        if not hashes and len(wheels) == 1:
            hashes = _sha256(wheels[0])
        if hashes:
            component["hashes"] = hashes
        sdist_url = (pkg.get("sdist") or {}).get("url")
        if sdist_url:
            component["externalReferences"] = [{"type": "distribution", "url": sdist_url}]
        component["properties"] = [{"name": "visionhousing:ecosystem", "value": "python"}]
        components.append(component)
    return root, components, deps


# --------------------------------------------------------------------------- npm


def npm_purl(name: str, version: str) -> str:
    """Return the package URL of an npm package (scope ``@`` encoded as ``%40``)."""
    if name.startswith("@") and "/" in name:
        scope, base = name.split("/", 1)
        return f"pkg:npm/{quote(scope, safe='')}/{quote(base, safe='')}@{quote(version, safe='')}"
    return f"pkg:npm/{quote(name, safe='')}@{quote(version, safe='')}"


def integrity_hashes(integrity: str) -> list[dict[str, str]]:
    """Convert an npm ``integrity`` string (``sha512-<base64>``) into CycloneDX hashes."""
    algs = {"sha512": "SHA-512", "sha384": "SHA-384", "sha256": "SHA-256", "sha1": "SHA-1"}
    out = []
    for part in (integrity or "").split():
        alg, _, digest = part.partition("-")
        if alg in algs and digest:
            out.append({"alg": algs[alg], "content": base64.b64decode(digest).hex()})
    return out


def _npm_name(key: str, info: dict[str, Any]) -> str:
    return info.get("name") or key.rsplit("node_modules/", 1)[-1]


def _resolve_npm(packages: dict[str, Any], from_key: str, dep: str) -> str | None:
    """Find the lockfile key npm would load ``dep`` from, starting at ``from_key``."""
    base = from_key
    while True:
        candidate = f"{base}/node_modules/{dep}" if base else f"node_modules/{dep}"
        if candidate in packages:
            return candidate
        if not base:
            return None
        cut = base.rfind("/node_modules/")
        base = base[:cut] if cut >= 0 else ""


def npm_components(
    lock: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Build the npm components, their dependency links and the web app's direct ones."""
    packages = lock.get("packages", {})
    refs = {k: npm_purl(_npm_name(k, v), v.get("version", "")) for k, v in packages.items() if k}
    components: dict[str, dict[str, Any]] = {}
    deps: dict[str, set[str]] = {}
    for key in sorted(packages):
        info = packages[key]
        if not key or info.get("link"):
            continue
        ref = refs[key]
        children = set()
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            for dep in info.get(field, {}) or {}:
                found = _resolve_npm(packages, key, dep)
                if found and found in refs:
                    children.add(refs[found])
        deps.setdefault(ref, set()).update(children)
        if ref in components:
            continue
        name = _npm_name(key, info)
        raw = info.get("license", "")
        expression = raw.get("type", "") if isinstance(raw, dict) else str(raw)
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": info.get("version", ""),
            "purl": ref,
        }
        lic = licence_entry(expression)
        if lic:
            component["licenses"] = lic
        hashes = integrity_hashes(info.get("integrity", ""))
        if hashes:
            component["hashes"] = hashes
        if info.get("resolved"):
            component["externalReferences"] = [{"type": "distribution", "url": info["resolved"]}]
        props = [{"name": "visionhousing:ecosystem", "value": "npm"}]
        if info.get("dev"):
            props.append({"name": "visionhousing:npm-dev", "value": "true"})
        component["properties"] = props
        components[ref] = component
    root_info = packages.get("", {})
    direct = []
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        for dep in root_info.get(field, {}) or {}:
            found = _resolve_npm(packages, "", dep)
            if found:
                direct.append(refs[found])
    dep_list = [{"ref": r, "dependsOn": sorted(c)} for r, c in sorted(deps.items())]
    return list(components.values()), dep_list, sorted(set(direct))


# --------------------------------------------------------------------------- document


def build_sbom(
    lock: dict[str, Any], npm_lock: dict[str, Any] | None, licences: dict[str, str]
) -> dict[str, Any]:
    """Assemble the CycloneDX 1.5 document from parsed lockfiles and Python licences."""
    root, py_components, py_deps = python_components(lock, licences)
    if root is None:
        root = {"type": "application", "bom-ref": "visionhousing", "name": "visionhousing"}
    components = list(py_components)
    dependencies = list(py_deps)
    if npm_lock is not None:
        web_ref = npm_purl(npm_lock.get("name", "visionhousing-web"), npm_lock.get("version", ""))
        web_info = npm_lock.get("packages", {}).get("", {})
        web: dict[str, Any] = {
            "type": "application",
            "bom-ref": web_ref,
            "name": npm_lock.get("name", "visionhousing-web"),
            "version": npm_lock.get("version", ""),
            "purl": web_ref,
            "description": "Browser interface (web/), built with Vite",
        }
        lic = licence_entry(str(web_info.get("license", "")))
        if lic:
            web["licenses"] = lic
        npm_parts, npm_deps, direct = npm_components(npm_lock)
        components.append(web)
        components.extend(npm_parts)
        dependencies.append({"ref": web_ref, "dependsOn": direct})
        dependencies.extend(npm_deps)
        for entry in dependencies:
            if entry["ref"] == root["bom-ref"]:
                entry["dependsOn"] = sorted({*entry["dependsOn"], web_ref})
    doc: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": "",
        "version": 1,
        "metadata": {
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": TOOL_NAME,
                        "description": "scripts/sbom.py (Python standard library only)",
                    }
                ]
            },
            "component": root,
        },
        "components": components,
        "dependencies": dependencies,
    }
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()
    doc["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'sbom:' + digest)}"
    return doc


def render(doc: dict[str, Any]) -> str:
    """Serialise the SBOM the same way every time (two-space indent, trailing newline)."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def generate(
    lock_path: Path, npm_lock_path: Path | None, search_path: list[str] | None = None
) -> tuple[str, list[str]]:
    """Read the lockfiles and return the SBOM text and any Python packages not installed."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    npm_lock = None
    if npm_lock_path is not None and npm_lock_path.exists():
        npm_lock = json.loads(npm_lock_path.read_text(encoding="utf-8"))
    names = [p["name"] for p in lock.get("package", [])]
    licences, missing = python_licences(names, search_path)
    return render(build_sbom(lock, npm_lock, licences)), missing


def main(argv: list[str] | None = None) -> int:
    """Write the SBOM, or with ``--check`` fail if the committed one is stale."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="write nothing, fail if stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    parser.add_argument("--npm-lock", type=Path, default=ROOT / "web" / "package-lock.json")
    parser.add_argument("--search-path", action="append", default=None)
    args = parser.parse_args(argv)
    if not args.lock.exists():
        print(f"sbom: lockfile not found: {args.lock.name}", file=sys.stderr)
        return 1
    text, missing = generate(args.lock, args.npm_lock, args.search_path)
    if missing:
        print(
            "sbom: locked but not installed (run `make setup`): " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"sbom: {args.out.name} is stale; run `make sbom`", file=sys.stderr)
            return 1
        print("sbom: up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    changed = not args.out.exists() or args.out.read_text(encoding="utf-8") != text
    if changed:
        args.out.write_text(text, encoding="utf-8")
    count = text.count('"bom-ref"') - 1
    print(f"sbom: {args.out.name} {'written' if changed else 'unchanged'} ({count} components)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
