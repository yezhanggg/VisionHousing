"""Vendor the browser libraries: download them once, check them, copy them into ``web/vendor/``.

Why: the interface is a set of plain ES modules with no build step and no Node
(decision D-10). It still needs four browser libraries (preact, htm,
@preact/signals and maplibre-gl). Their exact versions are pinned in
``web/package.json`` and ``web/package-lock.json`` so that they show up in the
software bill of materials and in Dependabot. This script is the only way they
reach the browser.

What it does:
1. Reads the exact pins from ``web/package.json`` and, for each one, the tarball
   address and the integrity hash from ``web/package-lock.json``.
2. Refuses a pin that is a range (``^1.2``) or a tarball that is not on the public
   npm registry over HTTPS.
3. Downloads each tarball and compares its SHA-512 (or SHA-384 / SHA-256) digest
   with the lockfile's integrity value. One mismatch stops the run before any
   file is written.
4. Copies only the few files the page needs (listed in ``FILES`` below) into
   ``web/vendor/<folder>/``, refusing any archive path that tries to leave the
   folder.
5. Writes ``web/vendor/manifest.json`` with each version, the integrity value
   and a SHA-256 per copied file. A second run with an unchanged lockfile and
   intact files does nothing and needs no network.

Usage: ``uv run python scripts/vendor_web.py [--force] [--check]``.
``--check`` only verifies the copied files against the manifest (no network).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import re
import ssl
import sys
import tarfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import truststore

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
REGISTRY = "https://registry.npmjs.org/"
MAX_TARBALL_BYTES = 40_000_000
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

# For each package: the folder under web/vendor/, and archive path -> file name.
# The page's import map (web/index.html) points at these files.
FILES: dict[str, tuple[str, dict[str, str]]] = {
    "preact": (
        "preact",
        {
            "dist/preact.mjs": "preact.mjs",
            "hooks/dist/hooks.mjs": "hooks.mjs",
            "LICENSE": "LICENSE",
        },
    ),
    "htm": (
        "htm",
        {"dist/htm.mjs": "htm.mjs", "preact/index.mjs": "preact.mjs", "LICENSE": "LICENSE"},
    ),
    "@preact/signals": ("signals", {"dist/signals.mjs": "signals.mjs", "LICENSE": "LICENSE"}),
    "@preact/signals-core": (
        "signals-core",
        {"dist/signals-core.mjs": "signals-core.mjs", "LICENSE": "LICENSE"},
    ),
    "maplibre-gl": (
        "maplibre-gl",
        {
            "dist/maplibre-gl.mjs": "maplibre-gl.mjs",
            # Loaded by maplibre-gl.mjs from its own folder, so the name must not change.
            "dist/maplibre-gl-worker.mjs": "maplibre-gl-worker.mjs",
            "dist/maplibre-gl.css": "maplibre-gl.css",
            "LICENSE.txt": "LICENSE.txt",
        },
    ),
}

Opener = Callable[[str], bytes]


class VendorError(Exception):
    """The pins, the lockfile or a download is not what it should be."""


class IntegrityError(VendorError):
    """A downloaded tarball does not match the lockfile's integrity hash."""


@dataclass(frozen=True)
class Pin:
    """One package to vendor: its exact version, tarball address and integrity value."""

    name: str
    version: str
    url: str
    integrity: str


def read_pins(package_json: Path, lock_json: Path) -> list[Pin]:
    """Return one ``Pin`` per vendored package, after checking pins and lockfile agree."""
    manifest = json.loads(package_json.read_text(encoding="utf-8"))
    lock = json.loads(lock_json.read_text(encoding="utf-8"))
    deps = manifest.get("dependencies", {})
    pins = []
    for name in FILES:
        version = deps.get(name)
        if version is None:
            raise VendorError(f"{name} is not listed in {package_json.name}")
        if not EXACT_VERSION.match(version):
            raise VendorError(f"{name} must be pinned to an exact version, not {version!r}")
        entry = lock.get("packages", {}).get(f"node_modules/{name}")
        if not entry:
            raise VendorError(f"{name} is missing from {lock_json.name}")
        if entry.get("version") != version:
            raise VendorError(
                f"{name}: package.json says {version}, lockfile says {entry.get('version')}"
            )
        url = entry.get("resolved", "")
        if not url.startswith(REGISTRY):
            raise VendorError(f"{name}: tarball must come from {REGISTRY}, not {url!r}")
        if not entry.get("integrity"):
            raise VendorError(f"{name}: the lockfile has no integrity value")
        pins.append(Pin(name, version, url, entry["integrity"]))
    return pins


def check_integrity(data: bytes, integrity: str) -> None:
    """Raise ``IntegrityError`` unless ``data`` matches one SRI value such as ``sha512-…``."""
    for token in integrity.split():
        algo, _, expected = token.partition("-")
        if algo in {"sha512", "sha384", "sha256"}:
            actual = base64.b64encode(hashlib.new(algo, data).digest()).decode()
            if hmac.compare_digest(actual, expected):
                return
            raise IntegrityError(f"{algo} digest is {actual}, lockfile expects {expected}")
    raise IntegrityError(f"no supported hash in integrity value {integrity!r}")


def extract(data: bytes, wanted: dict[str, str]) -> dict[str, bytes]:
    """Return ``{file name: bytes}`` for the wanted archive paths of an npm tarball."""
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise VendorError(f"unsafe path in archive: {member.name}")
            inner = "/".join(path.parts[1:])  # npm tarballs wrap everything in package/
            if inner in wanted and member.isfile():
                handle = tar.extractfile(member)
                if handle is None:
                    raise VendorError(f"cannot read {member.name}")
                found[wanted[inner]] = handle.read()
    missing = sorted(k for k, v in wanted.items() if v not in found)
    if missing:
        raise VendorError(f"archive lacks {', '.join(missing)}")
    return found


def default_opener(url: str) -> bytes:
    """Download ``url`` over HTTPS with the system certificate store (via truststore)."""
    if not url.startswith(REGISTRY):
        raise VendorError(f"refusing to download {url}")
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    request = urllib.request.Request(url, headers={"User-Agent": "visionhousing-vendor"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=60, context=context) as response:  # noqa: S310
        data = response.read(MAX_TARBALL_BYTES + 1)
    if len(data) > MAX_TARBALL_BYTES:
        raise VendorError(f"{url} is larger than {MAX_TARBALL_BYTES} bytes")
    return data


def sha256(data: bytes) -> str:
    """Return the hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def check_copy(dest: Path) -> list[str]:
    """List every problem with the copied files compared with ``manifest.json``."""
    stamp = dest / "manifest.json"
    if not stamp.exists():
        return ["no manifest.json: run scripts/vendor_web.py"]
    problems = []
    for name, info in json.loads(stamp.read_text(encoding="utf-8"))["packages"].items():
        for rel, digest in info["files"].items():
            path = dest / rel
            if not path.is_file():
                problems.append(f"{name}: {rel} is missing")
            elif sha256(path.read_bytes()) != digest:
                problems.append(f"{name}: {rel} has changed")
    return problems


def up_to_date(dest: Path, pins: list[Pin]) -> bool:
    """True when the manifest records exactly these pins and every file is intact."""
    stamp = dest / "manifest.json"
    if not stamp.exists():
        return False
    recorded = json.loads(stamp.read_text(encoding="utf-8"))["packages"]
    wanted = {p.name: p.integrity for p in pins}
    have = {name: info["integrity"] for name, info in recorded.items()}
    return wanted == have and not check_copy(dest)


def vendor(
    package_json: Path,
    lock_json: Path,
    dest: Path,
    opener: Opener = default_opener,
    force: bool = False,
) -> dict:
    """Download, verify and copy every pinned package. Returns the manifest written.

    Every tarball is downloaded and checked before the first file is written, so a
    bad hash leaves ``dest`` untouched.
    """
    pins = read_pins(package_json, lock_json)
    if not force and up_to_date(dest, pins):
        return json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    staged: dict[str, tuple[Pin, dict[str, bytes]]] = {}
    for pin in pins:
        data = opener(pin.url)
        check_integrity(data, pin.integrity)
        staged[pin.name] = (pin, extract(data, FILES[pin.name][1]))
    manifest: dict = {"source": REGISTRY, "packages": {}}
    root = dest.resolve()
    for name, (pin, files) in staged.items():
        folder = FILES[name][0]
        recorded = {}
        for filename, content in files.items():
            target = (dest / folder / filename).resolve()
            if root not in target.parents:
                raise VendorError(f"refusing to write outside {dest}: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            recorded[f"{folder}/{filename}"] = sha256(content)
        manifest["packages"][name] = {
            "version": pin.version,
            "integrity": pin.integrity,
            "files": recorded,
        }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Exits 1 on any pin, download or integrity problem."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package-json", type=Path, default=WEB / "package.json")
    parser.add_argument("--lock", type=Path, default=WEB / "package-lock.json")
    parser.add_argument("--dest", type=Path, default=WEB / "vendor")
    parser.add_argument("--force", action="store_true", help="download again even if up to date")
    parser.add_argument("--check", action="store_true", help="only verify the copied files")
    args = parser.parse_args(argv)
    if args.check:
        problems = check_copy(args.dest)
        for problem in problems:
            print(f"vendor_web: {problem}", file=sys.stderr)
        print(f"vendor_web: {'FAILED' if problems else 'copied files match the manifest'}")
        return 1 if problems else 0
    try:
        manifest = vendor(args.package_json, args.lock, args.dest, force=args.force)
    except (VendorError, OSError) as exc:
        print(f"vendor_web: {exc}", file=sys.stderr)
        return 1
    for name, info in manifest["packages"].items():
        print(f"vendor_web: {name} {info['version']} ok ({len(info['files'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
