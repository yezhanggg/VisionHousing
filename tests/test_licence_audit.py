"""Licence audit: allowed licences pass, copyleft and unknown licences fail."""

from pathlib import Path

import pytest

import licence_audit as la

ROOT = Path(__file__).resolve().parent.parent


def plant(site: Path, name: str, version: str, **fields: str) -> None:
    """Create a fake installed package with the given metadata fields."""
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    for key, value in fields.items():
        if key == "classifiers":
            lines += [f"Classifier: {c}" for c in value.split("|")]
        else:
            lines.append(f"{key.replace('_', '-')}: {value}")
    (info / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "expression",
    [
        "MIT",
        "Apache-2.0 OR BSD-2-Clause",
        "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
        "(MIT OR GPL-3.0) AND ISC",
        "mit and psf-2.0",
        "GPL-3.0-or-later WITH GCC-exception-3.1",
    ],
)
def test_allowed_expressions(expression):
    ok, _ = la.evaluate(expression)
    assert ok


@pytest.mark.parametrize(
    "expression",
    [
        "GPL-3.0-only",
        "AGPL-3.0",
        "LGPL-2.1-or-later",
        "MPL-2.0",
        "EUPL-1.2",
        "CC-BY-NC-4.0",
        "MIT AND LGPL-3.0",
        "SomethingMadeUp-1.0",
        "",
        "MIT AND (",
        "GPL-2.0 WITH Classpath-exception-2.0",
    ],
)
def test_denied_expressions(expression):
    ok, _ = la.evaluate(expression)
    assert not ok


def test_planted_gpl_package_fails(tmp_path, capsys):
    site = tmp_path / "site"
    plant(site, "goodpkg", "1.0", License_Expression="MIT")
    plant(site, "badpkg", "2.0", License_Expression="GPL-3.0-or-later")
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n[[package]]\nname = "goodpkg"\nversion = "1.0"\n'
        '[[package]]\nname = "badpkg"\nversion = "2.0"\n',
        encoding="utf-8",
    )
    code = la.main(
        ["--lock", str(lock), "--search-path", str(site), "--npm-lock", str(tmp_path / "none.json")]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "badpkg" in out and "FAIL" in out
    assert "1 failing" in out


def test_classifier_and_unknown_and_missing(tmp_path):
    site = tmp_path / "site"
    plant(site, "classy", "1.0", classifiers="License :: OSI Approved :: BSD License")
    plant(site, "mystery", "1.0")
    plant(
        site,
        "lgplclass",
        "1.0",
        classifiers="License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
    )
    findings = la.audit_python(
        [("classy", "1.0"), ("mystery", "1.0"), ("lgplclass", "1.0"), ("ghost", "9.9")],
        [str(site)],
    )
    verdicts = {f.name: f.ok for f in findings}
    assert verdicts == {"classy": True, "mystery": False, "lgplclass": False, "ghost": False}


def test_version_mismatch_fails(tmp_path):
    site = tmp_path / "site"
    plant(site, "drift", "1.1", License_Expression="MIT")
    [finding] = la.audit_python([("drift", "1.0")], [str(site)])
    assert not finding.ok


def test_npm_lockfile(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        '{"lockfileVersion": 3, "packages": {"": {"name": "web"},'
        ' "node_modules/preact": {"version": "10.0.0", "license": "MIT"},'
        ' "node_modules/copyleft": {"version": "1.0.0", "license": "GPL-2.0"},'
        ' "node_modules/nolicence": {"version": "1.0.0"}}}',
        encoding="utf-8",
    )
    verdicts = {f.name: f.ok for f in la.audit_npm(lock)}
    assert verdicts == {"preact": True, "copyleft": False, "nolicence": False}


def test_real_lockfile_installed_packages_pass():
    # `make licence` (after `make setup`, all groups) is the full gate. Here, packages
    # from optional groups may not be installed, so only their licences are skipped.
    findings = la.audit_python(la.locked_python_packages(ROOT / "uv.lock"))
    assert findings
    installed = [f for f in findings if f.source != "not installed"]
    assert any(f.name == "numpy" for f in installed)
    assert [f.name for f in installed if not f.ok] == []


def plant_bundle(site: Path, name: str, files: dict[str, str], **fields: str) -> None:
    """A fake installed package whose wheel ships extra files (listed in its RECORD)."""
    plant(site, name, "1.0", **fields)
    info = f"{name}-1.0.dist-info"
    record = [f"{info}/METADATA,,"]
    for rel, text in files.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        record.append(f"{rel},,")
    (site / info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


LGPL_TEXT = "    GNU LESSER GENERAL PUBLIC LICENSE\n       Version 2.1, February 1999\n"
GCC_TEXT = (
    "Name: GCC runtime library\nFiles: pkg/.dylibs/libgfortran.5.dylib\n"
    "License: GPL-3.0-or-later WITH GCC-exception-3.1\n\n"
    "                    GNU GENERAL PUBLIC LICENSE\n\nGCC RUNTIME LIBRARY EXCEPTION\n"
)
PSF_TEXT = "The licences are GPL-compatible. All Python licenses, unlike the GPL, let you ...\n"


def test_bundled_copyleft_library_fails_even_when_the_package_is_bsd(tmp_path):
    """A BSD wheel that ships an LGPL native library (GEOS, libquadmath) fails the audit."""
    site = tmp_path / "site"
    plant_bundle(
        site,
        "geomwrap",
        {
            "geomwrap/.dylibs/libgeos.3.14.dylib": "binary",
            "geomwrap/_core.cpython-312-darwin.so": "binary",
            "geomwrap-1.0.dist-info/licenses/LICENSE_GEOS": LGPL_TEXT,
        },
        License_Expression="BSD-3-Clause",
    )
    plant_bundle(
        site,
        "mathwrap",
        {
            "mathwrap/.dylibs/libquadmath.0.dylib": "binary",
            "mathwrap-1.0.dist-info/LICENSE.txt": (
                "Name: libquadmath\nFiles: mathwrap/.dylibs/libquadmath*.so\n"
                "License: LGPL-2.1-or-later\n"
            ),
        },
        License_Expression="BSD-3-Clause",
    )
    plant_bundle(
        site,
        "fastnum",
        {
            "fastnum/.dylibs/libgfortran.5.dylib": "binary",
            "fastnum-1.0.dist-info/licenses/LICENSE.txt": GCC_TEXT,
            "fastnum-1.0.dist-info/licenses/PSF_LICENSE": PSF_TEXT,
        },
        License_Expression="BSD-3-Clause",
    )
    findings = {
        f.name: f
        for f in la.audit_python(
            [("geomwrap", "1.0"), ("mathwrap", "1.0"), ("fastnum", "1.0")], [str(site)]
        )
    }
    geo = findings["geomwrap"]
    assert not geo.ok
    assert any("LICENSE_GEOS" in n and "LGPL" in n for n in geo.notes)
    assert any("libgeos.3.14.dylib" in n for n in geo.notes)
    assert not any("_core" in n for n in geo.notes)  # own extension modules are not bundles
    assert not findings["mathwrap"].ok
    assert any("LGPL-2.1-or-later" in n for n in findings["mathwrap"].notes)
    # the GCC runtime exception stays allowed; "GPL-compatible" prose is not a licence
    assert findings["fastnum"].ok


def test_copyleft_mentions():
    assert la.copyleft_mentions(LGPL_TEXT) == ["LGPL (full licence text)"]
    assert la.copyleft_mentions(GCC_TEXT) == []
    assert la.copyleft_mentions(PSF_TEXT) == []
    assert la.copyleft_mentions("License: MIT AND (Apache-2.0 OR MPL-2.0)") == ["MPL-2.0"]
    assert la.copyleft_mentions("License: BSD-3-Clause-Open-MPI") == []
