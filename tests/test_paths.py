"""Path safety: outside values never lead outside the data folders.

Covers :mod:`visionhousing.paths` on its own, the folders the interface reads
from, and every interface address that takes a state, place or record id.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api.runner import ContextStore
from visionhousing.grades.switch import stored_run
from visionhousing.paths import UnsafePathError, safe_child, state_code
from visionhousing.profile import load_profile_context

API = "/api/v1"
MARKER = "outside-the-data-folder"

BAD_PARTS = [
    "..",
    ".",
    "../x",
    "..\\x",
    "a/b",
    "a\\b",
    "/etc/passwd",
    "C:\\x",
    "C:",
    "x\x00",
    "\x00",
    "%2e%2e",
    ".hidden",
    "",
    "a..b",
    "x" * 200,
]


# ------------------------------------------------------------------ the helper


def test_safe_child_joins_plain_names(tmp_path):
    out = safe_child(tmp_path, "PA", "runs", "adu.json")
    assert out == tmp_path / "PA" / "runs" / "adu.json"
    assert out.is_absolute()


@pytest.mark.parametrize("part", BAD_PARTS)
def test_safe_child_refuses_unsafe_parts(tmp_path, part):
    with pytest.raises(ValueError):
        safe_child(tmp_path, part)
    with pytest.raises(UnsafePathError):
        safe_child(tmp_path, "PA", part)


def test_safe_child_refuses_no_parts_and_non_text(tmp_path):
    with pytest.raises(ValueError):
        safe_child(tmp_path)
    with pytest.raises(ValueError):
        safe_child(tmp_path, 7)  # type: ignore[arg-type]


def test_safe_child_refuses_a_link_out(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    os.symlink(outside, base / "XX")
    with pytest.raises(ValueError, match="link"):
        safe_child(base, "XX")


@pytest.mark.parametrize("good", ["PA", "pa", "TL", "Vt"])
def test_state_code_accepts_two_letters(good):
    assert state_code(good) == good.upper()


@pytest.mark.parametrize("bad", ["", "P", "PAX", "..", "P1", "../PA", "P\x00", "%2", None, 12])
def test_state_code_refuses_others(bad):
    with pytest.raises(ValueError):
        state_code(bad)


def test_state_code_known_list():
    assert state_code("pa", known={"PA", "VT"}) == "PA"
    with pytest.raises(ValueError):
        state_code("ZZ", known={"PA", "VT"})


# ------------------------------------------------------------------ the readers


@pytest.mark.parametrize("bad", ["..", "../..", "/etc", "a\\b", "P\x00"])
def test_context_store_folder_refuses_bad_codes(tmp_path, bad):
    store = ContextStore(tmp_path)
    with pytest.raises(ValueError):
        store.folder(bad)
    with pytest.raises(ValueError):
        store.get(bad)


def test_context_store_folder_stays_under_packs(tmp_path):
    assert ContextStore(tmp_path).folder("pa") == tmp_path / "PA"


def test_profile_context_refuses_bad_codes():
    with pytest.raises(ValueError):
        load_profile_context("..")


def test_stored_run_ignores_unsafe_keys(tmp_path):
    (tmp_path / "secret.json").write_text("{}", encoding="utf-8")
    assert stored_run(tmp_path, "..", "secret") is None
    assert stored_run(tmp_path, "PA", "../../secret") is None


# ------------------------------------------------------------------ the interface


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("traversal")
    (root / "secret.json").write_text(f'{{"x": "{MARKER}"}}', encoding="utf-8")
    packs = root / "packs"
    packs.mkdir()
    (packs / "secret.json").write_text(f'{{"x": "{MARKER}"}}', encoding="utf-8")
    return AsgiClient(create_app(packs_dir=packs))


#: Traversal attempts as they reach the app: a real server decodes ``%2e`` to
#: ``.`` before routing, so both the raw and the decoded spelling are sent.
TRAVERSAL = [
    "..",
    "../..",
    "../secret.json",
    "../../../../etc/passwd",
    "%2e%2e",
    "%2e%2e%2fsecret.json",
    "..%2f..%2fetc%2fpasswd",
    "..\\..\\secret.json",
    "\\..",
    "C:",
    "\x00",
    "P\x00",
    "PA\x00",
    "/etc/passwd",
]

#: Interface addresses with a state in the path (``{st}``).
STATE_ROUTES = [
    "states/{st}/summary",
    "states/{st}/counties/42001",
    "states/{st}/map",
    "states/{st}/tract-points",
    "states/{st}/tracts",
    "states/{st}/tract-funnel",
    "states/{st}/permit-years",
    "states/{st}/outlines",
    "states/{st}/districts",
    "states/{st}/district-outlines",
    "states/{st}/comparisons",
    "places/{st}",
    "profile/{st}/42001",
    "confidence/{st}",
    "lever/{st}",
    "implementation/{st}",
    "grades/{st}",
    "trace/{st}",
    "switch/{st}",
    "intake/{st}",
    "plausibility/{st}",
]

#: Addresses with a state, place or id in the query.
QUERY_ROUTES = [
    "report-card?state={v}",
    "provenance?state={v}",
    "export/json?s={v}",
    "export/csv?s={v}",
    "reproduce?id={v}",
    "reproduce?id={v}:adu:reform_results.adu.added",
    "reproduce?id=TL:{v}:reform_results.adu.added",
]


def _spellings(value: str) -> list[str]:
    from urllib.parse import unquote

    return sorted({value, unquote(value)})


def _never_leaks(r) -> None:
    assert r.status in (400, 404), (r.status, r.text[:200])
    assert MARKER not in r.text
    assert "root:" not in r.text
    assert r.json()["error"]


@pytest.mark.parametrize("route", STATE_ROUTES)
@pytest.mark.parametrize("value", TRAVERSAL)
def test_state_in_path_never_reads_outside(client, route, value):
    for spelled in _spellings(value):
        r = client.get(f"{API}/" + route.format(st=spelled))
        _never_leaks(r)
        if "/" not in spelled:
            assert r.status == 400, (route, spelled, r.text[:200])


@pytest.mark.parametrize("route", QUERY_ROUTES)
@pytest.mark.parametrize("value", TRAVERSAL)
def test_state_in_query_never_reads_outside(client, route, value):
    r = client.get(f"{API}/" + route.format(v=quote(value, safe="")))
    assert r.status == 400, (route, value, r.text[:200])
    assert MARKER not in r.text


@pytest.mark.parametrize("value", TRAVERSAL)
def test_place_id_never_reads_outside(client, value):
    for spelled in _spellings(value):
        for route in (f"profile/TL/{spelled}", f"states/TL/counties/{spelled}"):
            r = client.get(f"{API}/{route}")
            _never_leaks(r)
            if "/" not in spelled:
                assert r.status == 400, (route, r.text[:200])


@pytest.mark.parametrize("value", TRAVERSAL)
def test_run_body_state_never_reads_outside(client, value):
    r = client.post(f"{API}/run", {"state": value})
    assert r.status == 400 and MARKER not in r.text


@pytest.mark.parametrize("value", ["..", "../secret", "%2e%2e", "a\\b", "x\x00"])
def test_reform_id_is_not_a_path(client, value):
    r = client.get(f"{API}/reforms/{value}")
    _never_leaks(r)


@pytest.mark.parametrize(
    "path", ["/../pyproject.toml", "/%2e%2e/pyproject.toml", "/..%2fpyproject.toml"]
)
def test_static_files_stay_in_the_web_folder(client, path):
    r = client.get(path)
    assert "[project]" not in r.text
