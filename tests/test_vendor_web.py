"""Browser-library vendoring: integrity checks, safe extraction, and the page's import graph."""

import base64
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

import pytest

import vendor_web

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
URL = "https://registry.npmjs.org/demo/-/demo-1.0.0.tgz"


def tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """A one-package project: package.json, lockfile, tarball and a fake opener."""
    monkeypatch.setattr(vendor_web, "FILES", {"demo": ("demo", {"dist/demo.mjs": "demo.mjs"})})
    data = tarball({"package/dist/demo.mjs": b"export const x = 1;\n", "package/README": b"hi"})

    def write(version="1.0.0", url=URL, integrity=None, lock_version="1.0.0"):
        pkg = tmp_path / "package.json"
        lock = tmp_path / "package-lock.json"
        pkg.write_text(json.dumps({"dependencies": {"demo": version}}))
        entry = {"version": lock_version, "resolved": url, "integrity": integrity or sri(data)}
        lock.write_text(json.dumps({"packages": {"": {}, "node_modules/demo": entry}}))
        return pkg, lock

    calls = []

    def opener(url):
        calls.append(url)
        return data

    return {"write": write, "data": data, "opener": opener, "calls": calls, "dest": tmp_path / "v"}


def test_good_hash_passes_and_writes_only_the_listed_file(demo):
    pkg, lock = demo["write"]()
    manifest = vendor_web.vendor(pkg, lock, demo["dest"], opener=demo["opener"])
    out = demo["dest"] / "demo" / "demo.mjs"
    assert out.read_bytes() == b"export const x = 1;\n"
    assert not (demo["dest"] / "demo" / "README").exists()
    files = manifest["packages"]["demo"]["files"]
    assert files == {"demo/demo.mjs": hashlib.sha256(out.read_bytes()).hexdigest()}
    assert vendor_web.check_copy(demo["dest"]) == []


def test_second_run_is_offline_when_nothing_changed(demo):
    pkg, lock = demo["write"]()
    vendor_web.vendor(pkg, lock, demo["dest"], opener=demo["opener"])
    vendor_web.vendor(pkg, lock, demo["dest"], opener=demo["opener"])
    assert len(demo["calls"]) == 1


def test_bad_hash_fails_and_writes_nothing(demo):
    pkg, lock = demo["write"](integrity=sri(b"something else"))
    with pytest.raises(vendor_web.IntegrityError):
        vendor_web.vendor(pkg, lock, demo["dest"], opener=demo["opener"])
    assert not demo["dest"].exists()


def test_sha256_and_sha384_values_are_accepted():
    data = b"abc"
    for algo in ("sha256", "sha384"):
        value = f"{algo}-" + base64.b64encode(hashlib.new(algo, data).digest()).decode()
        vendor_web.check_integrity(data, value)
    with pytest.raises(vendor_web.IntegrityError):
        vendor_web.check_integrity(data, "md5-xyz")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"version": "^1.0.0"}, "exact version"),
        ({"url": "http://example.org/demo.tgz"}, "must come from"),
        ({"lock_version": "1.0.1"}, "lockfile says"),
    ],
)
def test_loose_pins_and_foreign_tarballs_are_refused(demo, kwargs, message):
    pkg, lock = demo["write"](**kwargs)
    with pytest.raises(vendor_web.VendorError, match=message):
        vendor_web.vendor(pkg, lock, demo["dest"], opener=demo["opener"])


def test_archive_paths_that_leave_the_folder_are_refused():
    evil = tarball({"package/../../evil.mjs": b"x"})
    with pytest.raises(vendor_web.VendorError, match="unsafe path"):
        vendor_web.extract(evil, {"dist/demo.mjs": "demo.mjs"})


def test_missing_file_in_archive_is_reported():
    with pytest.raises(vendor_web.VendorError, match=r"archive lacks dist/demo\.mjs"):
        vendor_web.extract(tarball({"package/other.js": b"x"}), {"dist/demo.mjs": "demo.mjs"})


def test_check_spots_a_changed_file(demo):
    pkg, lock = demo["write"]()
    vendor_web.vendor(pkg, lock, demo["dest"], opener=demo["opener"])
    (demo["dest"] / "demo" / "demo.mjs").write_text("tampered")
    assert vendor_web.check_copy(demo["dest"]) == ["demo: demo/demo.mjs has changed"]


def test_default_opener_refuses_other_hosts():
    with pytest.raises(vendor_web.VendorError):
        vendor_web.default_opener("https://example.org/x.tgz")


# --- The real manifest, lockfile and page ---


def test_real_pins_are_exact_and_locked():
    pins = vendor_web.read_pins(WEB / "package.json", WEB / "package-lock.json")
    assert {p.name for p in pins} == set(vendor_web.FILES)
    for pin in pins:
        assert pin.integrity.startswith("sha512-")


def import_map() -> dict[str, str]:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    block = re.search(r'<script type="importmap">(.*?)</script>', html, re.S)
    assert block
    return json.loads(block.group(1))["imports"]


def test_import_map_points_only_at_vendored_files():
    vendored = {
        f"./vendor/{folder}/{name}"
        for folder, files in vendor_web.FILES.values()
        for name in files.values()
    }
    for target in import_map().values():
        assert target in vendored, target


IMPORT = re.compile(
    r"""(?:import|export)\s[^'"]*?from\s*["']([^"']+)["']|import\(\s*["']([^"']+)["']"""
)


def test_every_module_import_resolves_inside_web():
    """No CDN and no Node at run time: imports are relative files or import-map names."""
    names = set(import_map())
    modules = list((WEB / "js").rglob("*.js"))
    assert modules
    for module in modules:
        for match in IMPORT.finditer(module.read_text(encoding="utf-8")):
            spec = match.group(1) or match.group(2)
            if spec.startswith("."):
                assert (module.parent / spec).resolve().is_file(), f"{module.name}: {spec}"
            else:
                assert spec in names, f"{module.name} imports {spec}, not in the import map"
