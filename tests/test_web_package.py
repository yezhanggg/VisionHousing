"""The web front end's packages, its Vercel settings and the static-site data budget.

No Node is needed for these checks: they read web/package.json, web/package-lock.json,
vercel.json and the source files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from visionhousing.api.security import SecurityHeaders, content_security_policy
from visionhousing.export_static import (
    GZIP_LIMIT_BYTES,
    SIZE_LIMIT_BYTES,
    TRACT_FILE_BUDGET_BYTES,
    folder_gzip_size,
    folder_size,
)

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
EXACT = re.compile(r"^\d+\.\d+\.\d+$")
IMPORT = re.compile(r"""(?:^|\n)\s*import\s+(?:[^"';]*?\s+from\s+)?["']([^"']+)["']""")
DYNAMIC = re.compile(r"""import\(\s*["']([^"']+)["']\s*\)""")

sys.path.insert(0, str(ROOT / "scripts"))
import licence_audit  # noqa: E402


def manifest() -> dict:
    return json.loads((WEB / "package.json").read_text(encoding="utf-8"))


def lock() -> dict:
    return json.loads((WEB / "package-lock.json").read_text(encoding="utf-8"))


def test_every_pin_is_exact_and_locked():
    pkg, locked = manifest(), lock()["packages"]
    pins = {**pkg["dependencies"], **pkg["devDependencies"]}
    assert {"vite", "preact", "htm", "@preact/signals", "maplibre-gl"} <= set(pins)
    for name, version in pins.items():
        assert EXACT.match(version), f"{name} is not pinned exactly: {version}"
        assert locked[f"node_modules/{name}"]["version"] == version, name
    assert locked[""]["dependencies"] == pkg["dependencies"]
    assert locked[""]["devDependencies"] == pkg["devDependencies"]


def test_lockfile_only_uses_the_public_registry_with_sha512():
    for key, info in lock()["packages"].items():
        if not key or info.get("link"):
            continue
        assert info["resolved"].startswith("https://registry.npmjs.org/"), key
        assert info["integrity"].startswith("sha512-"), key


def test_every_npm_package_has_a_permissive_licence():
    findings = licence_audit.audit_npm(WEB / "package-lock.json")
    assert findings
    bad = [f"{f.name} {f.version}: {f.expression}" for f in findings if not f.ok]
    assert bad == []


def test_node_version_is_pinned_for_the_build_host():
    for path in (WEB / "package.json", ROOT / "package.json"):
        engines = json.loads(path.read_text(encoding="utf-8"))["engines"]["node"]
        assert engines == ">=22.12.0", path
    assert "dependencies" not in json.loads((ROOT / "package.json").read_text(encoding="utf-8"))


def test_vercel_builds_the_static_site_from_web():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["framework"] is None
    assert cfg["installCommand"] == "cd web && npm ci"
    assert cfg["buildCommand"] == "cd web && npm run build:static"
    assert cfg["outputDirectory"] == "web/dist"
    assert manifest()["scripts"]["build:static"] == "vite build --mode static"


def test_vercel_sends_the_same_security_headers_as_the_app():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    every = next(h for h in cfg["headers"] if h["source"] == "/(.*)")
    sent = {h["key"].lower(): h["value"] for h in every["headers"]}
    app = SecurityHeaders(None, content_security_policy())
    expected = {k.decode(): v.decode() for k, v in app.headers}
    assert sent == expected


def test_static_site_data_stays_under_the_limit():
    """web/public/data is committed for the static site; it must stay small.

    The budget is the compressed size (what git stores and the host sends, 12 MB); the
    uncompressed size has a hard ceiling (60 MB). Both are set in ``export_static``.
    """
    data = WEB / "public" / "data"
    packed = folder_gzip_size(data)
    assert packed <= GZIP_LIMIT_BYTES, (
        f"web/public/data is {packed:,} bytes compressed (budget {GZIP_LIMIT_BYTES:,})"
    )
    size = folder_size(data)
    assert size <= SIZE_LIMIT_BYTES, f"web/public/data is {size:,} bytes (ceiling 60 MB)"


def test_tract_column_files_stay_small():
    """Each statewide tract-column file (points, one scenario's values, one funnel) is small."""
    data = WEB / "public" / "data"
    files = [*data.glob("*/tract-points.json"), *data.glob("*/tracts/*.json")]
    files += data.glob("*/tract-funnel/*.json")
    big = {f.relative_to(data).as_posix(): f.stat().st_size for f in files}
    assert {k: v for k, v in big.items() if v > TRACT_FILE_BUDGET_BYTES} == {}


def test_build_output_and_installs_are_not_committed_but_data_is():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "node_modules/" in ignore and "dist/" in ignore
    assert not any(line.strip().startswith(("web/public", "public/")) for line in ignore)
    assert "data/*" in ignore  # root data/ only: the pattern has a slash, so it is anchored


@pytest.mark.parametrize("folder", ["js"])
def test_imports_resolve_to_files_or_locked_packages(folder):
    """No CDN: an import is a file in web/ or a package in the lockfile."""
    packages = {k.split("node_modules/")[-1] for k in lock()["packages"] if k}
    modules = list((WEB / folder).rglob("*.js"))
    assert modules
    for module in modules:
        text = module.read_text(encoding="utf-8")
        for spec in IMPORT.findall(text) + DYNAMIC.findall(text):
            path = spec.split("?", 1)[0]
            if path.startswith("."):
                assert (module.parent / path).resolve().is_file(), f"{module.name}: {spec}"
            else:
                name = "/".join(path.split("/")[:2]) if path.startswith("@") else path.split("/")[0]
                assert name in packages, f"{module.name} imports {spec}, not in the lockfile"
                assert not path.startswith(("http:", "https:", "//")), spec
