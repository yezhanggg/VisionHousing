"""SBOM: CycloneDX 1.5 shape on a tiny fixture lock, and the committed file is current."""

import base64
import hashlib
import json
import re
import tomllib

import pytest

import sbom

UV_LOCK = (
    """
version = 1
revision = 3
requires-python = "==3.12.*"

[[package]]
name = "demo-app"
version = "0.1.0"
source = { editable = "." }
dependencies = [{ name = "Tiny_Lib" }]

[package.optional-dependencies]
extra = [{ name = "wheel-only" }]

[[package]]
name = "Tiny_Lib"
version = "1.2.3"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.org/tiny_lib-1.2.3.tar.gz", hash = "sha256:HASH_A", size = 10 }
wheels = [{ url = "https://example.org/tiny_lib-1.2.3-py3-none-any.whl", hash = "sha256:HASH_B" }]

[[package]]
name = "wheel-only"
version = "2.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://example.org/wheel_only-2.0-py3-none-any.whl", hash = "sha256:HASH_C" }]
""".replace("HASH_A", "a" * 64)
    .replace("HASH_B", "b" * 64)
    .replace("HASH_C", "c" * 64)
)

DIGEST = hashlib.sha512(b"tarball").digest()
NPM_LOCK = {
    "name": "demo-web",
    "version": "0.1.0",
    "lockfileVersion": 3,
    "packages": {
        "": {
            "name": "demo-web",
            "version": "0.1.0",
            "license": "Apache-2.0",
            "dependencies": {"@scope/pkg": "1.0.0"},
            "devDependencies": {"left": "2.0.0"},
        },
        "node_modules/@scope/pkg": {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/@scope/pkg/-/pkg-1.0.0.tgz",
            "integrity": "sha512-" + base64.b64encode(DIGEST).decode(),
            "license": "(MIT OR Apache-2.0)",
            "dependencies": {"left": "^1.0.0"},
        },
        "node_modules/@scope/pkg/node_modules/left": {"version": "1.5.0", "license": "ISC"},
        "node_modules/left": {"version": "2.0.0", "license": "BSD", "dev": True},
    },
}
LICENCES = {"tiny-lib": "MIT", "wheel-only": "Apache-2.0 AND BSD-3-Clause", "demo-app": "MIT"}


@pytest.fixture
def doc():
    return sbom.build_sbom(tomllib.loads(UV_LOCK), NPM_LOCK, LICENCES)


def by_purl(doc):
    return {c["purl"]: c for c in doc["components"]}


def test_document_header(doc):
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.5"
    assert doc["version"] == 1
    assert re.fullmatch(r"urn:uuid:[0-9a-f-]{36}", doc["serialNumber"])
    root = doc["metadata"]["component"]
    assert root["name"] == "demo-app" and root["type"] == "application"
    assert root["licenses"] == [{"license": {"id": "MIT"}}]
    assert "timestamp" not in doc["metadata"]
    refs = [c["bom-ref"] for c in doc["components"]]
    assert len(refs) == len(set(refs)) and root["bom-ref"] not in refs


def test_python_components(doc):
    comps = by_purl(doc)
    tiny = comps["pkg:pypi/tiny-lib@1.2.3"]
    assert tiny["name"] == "Tiny_Lib" and tiny["version"] == "1.2.3"
    assert tiny["licenses"] == [{"license": {"id": "MIT"}}]
    assert tiny["hashes"] == [{"alg": "SHA-256", "content": "a" * 64}]  # the source archive
    wheel = comps["pkg:pypi/wheel-only@2.0"]
    assert wheel["hashes"] == [{"alg": "SHA-256", "content": "c" * 64}]  # its only wheel
    assert wheel["licenses"] == [{"expression": "Apache-2.0 AND BSD-3-Clause"}]


def test_npm_components(doc):
    comps = by_purl(doc)
    scoped = comps["pkg:npm/%40scope/pkg@1.0.0"]
    assert scoped["name"] == "@scope/pkg"
    assert scoped["hashes"] == [{"alg": "SHA-512", "content": DIGEST.hex()}]
    assert scoped["licenses"] == [{"expression": "(MIT OR Apache-2.0)"}]
    assert comps["pkg:npm/left@1.5.0"]["licenses"] == [{"license": {"id": "ISC"}}]
    dev = comps["pkg:npm/left@2.0.0"]
    assert {"name": "visionhousing:npm-dev", "value": "true"} in dev["properties"]
    assert dev["licenses"] == [{"license": {"name": "BSD"}}]  # not a real SPDX id: kept as name
    assert "hashes" not in dev


def test_dependency_graph(doc):
    deps = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    assert deps["pkg:pypi/demo-app@0.1.0"] == [
        "pkg:npm/demo-web@0.1.0",
        "pkg:pypi/tiny-lib@1.2.3",
        "pkg:pypi/wheel-only@2.0",
    ]
    # npm resolves the nested copy first, as Node would
    assert deps["pkg:npm/%40scope/pkg@1.0.0"] == ["pkg:npm/left@1.5.0"]
    assert deps["pkg:npm/demo-web@0.1.0"] == ["pkg:npm/%40scope/pkg@1.0.0", "pkg:npm/left@2.0.0"]
    known = {doc["metadata"]["component"]["bom-ref"]} | {c["bom-ref"] for c in doc["components"]}
    assert all(r in known for d in doc["dependencies"] for r in [d["ref"], *d["dependsOn"]])


def test_output_is_deterministic():
    one = sbom.render(sbom.build_sbom(tomllib.loads(UV_LOCK), NPM_LOCK, LICENCES))
    two = sbom.render(sbom.build_sbom(tomllib.loads(UV_LOCK), NPM_LOCK, LICENCES))
    assert one == two and one.endswith("\n")
    json.loads(one)


def test_write_and_check(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(UV_LOCK, encoding="utf-8")
    out = tmp_path / "bom.json"
    empty = tmp_path / "site"
    empty.mkdir()
    args = ["--lock", str(lock), "--npm-lock", str(tmp_path / "none.json"), "--out", str(out)]
    # nothing installed in the empty search path: the script refuses to guess licences
    assert sbom.main([*args, "--search-path", str(empty)]) == 1
    text, missing = sbom.generate(lock, None, [str(empty)])
    assert missing == ["demo-app", "Tiny_Lib", "wheel-only"]
    out.write_text(text, encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))["components"]


def test_committed_sbom_is_current():
    text, missing = sbom.generate(sbom.ROOT / "uv.lock", sbom.ROOT / "web" / "package-lock.json")
    if missing:
        pytest.skip("optional packages not installed here (make setup); CI runs make sbom-check")
    committed = sbom.DEFAULT_OUT.read_text(encoding="utf-8")
    assert committed == text, "sbom/visionhousing.cdx.json is stale: run `make sbom`"


def test_committed_sbom_has_no_geometry_library_wrapping_geos():
    """Decision H8: no package that bundles GEOS or GDAL (LGPL) is in the bill of materials."""
    doc = json.loads(sbom.DEFAULT_OUT.read_text(encoding="utf-8"))
    names = {c["name"].lower() for c in doc["components"]}
    assert not names & {"shapely", "pygeos", "gdal", "fiona", "rasterio", "pyproj", "geopandas"}
    lock = tomllib.loads((sbom.ROOT / "uv.lock").read_text(encoding="utf-8"))
    root = next(p for p in lock["package"] if p.get("source", {}).get("editable") == ".")
    assert "optional-dependencies" not in root  # the geo extra is gone
