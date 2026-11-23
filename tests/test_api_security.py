"""Interface safety: bad input gets a 400, a big body a 413, every response the headers."""

from __future__ import annotations

import base64
import hashlib
import re

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api.models import LIMITS
from visionhousing.api.security import inline_script_hashes
from visionhousing.ledger import load_ledger
from visionhousing.ledger.loader import ROOT

API = "/api/v1"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


@pytest.mark.parametrize(
    "body",
    [
        {"state": "P1"},
        {"state": "TL", "draws": 0},
        {"state": "TL", "draws": LIMITS["draws"] + 1},
        {"state": "TL", "seed": -1},
        {"state": "TL", "preset": "extreme"},
        {"state": "TL", "surprise": 1},
        {"state": "TL", "reforms": ["adu"] * 2},
        {"state": "TL", "reforms": [f"r{i}" for i in range(LIMITS["reforms"] + 1)]},
        {"state": "TL", "reforms": ["Not-A-Key"]},
        {"state": "TL", "overrides": {f"adu.p{i}": 1 for i in range(LIMITS["overrides"] + 1)}},
        {"state": "TL", "overrides": {"adu.takeup": "maybe"}},
        {"state": "TL", "overrides": {"adu.takeup": True}},
        {"state": "TL", "overrides": {"adu.takeup": {"low": "x"}}},
        {"state": "TL", "overrides": {"adu.takeup": {"color": 1}}},
        {"state": "TL", "overrides": {"NOT VALID": 1}},
        {"state": "TL", "reforms": ["bogus"]},
        {"state": "TL", "reforms": ["adu", "pa_hb2186"]},
        {"state": "TL", "overrides": {"adu.not_a_parameter": "l"}},
    ],
)
def test_bad_run_body_is_400(client, body):
    r = client.post(f"{API}/run", body)
    assert r.status == 400, r.text
    out = r.json()
    assert out["error"] and out["message"]


def test_malformed_json_is_400(client):
    r = client.request(
        "POST", f"{API}/run", body=b"{not json", headers={"content-type": "application/json"}
    )
    assert r.status == 400 and r.json()["error"] == "invalid_request"


def test_bad_override_range_from_engine_is_400(client):
    pid = next(iter(load_ledger()))
    r = client.post(f"{API}/run", {"state": "TL", "overrides": {pid: {"low": 5, "high": 1}}})
    assert r.status == 400 and r.json()["error"] == "invalid_override"


def test_errors_do_not_echo_input(client):
    r = client.post(f"{API}/run", {"state": "TL", "draws": "<script>alert(1)</script>"})
    assert r.status == 400 and "<script>" not in r.text


@pytest.mark.parametrize(
    "url",
    [
        f"{API}/export/csv?s=TL&a=adu",
        f"{API}/export/csv?s=TL&a=adu.x~zz",
        f"{API}/export/csv?s=TL&seed=abc",
        f"{API}/export/csv?s=TL&draws=99999",
        f"{API}/export/csv?s=TL&v=2",
        f"{API}/export/csv?s=TL&unknown=1",
        f"{API}/export/csv?s=TL&p=Not%20OK",
        f"{API}/export/xml?s=TL",
        f"{API}/export/csv?s=TL&r=" + ",".join(["adu"] * 3000),
        f"{API}/states/TL/counties/abc",
        f"{API}/states/T1/summary",
        f"{API}/ledger?q=" + "x" * (LIMITS["search_chars"] + 1),
    ],
)
def test_bad_query_is_400(client, url):
    r = client.get(url)
    assert r.status == 400, r.text
    assert r.json()["error"]


def test_big_body_declared_is_413(client):
    big = b'{"state": "TL", "pad": "' + b"x" * LIMITS["body_bytes"] + b'"}'
    r = client.request("POST", f"{API}/run", body=big, headers={"content-type": "application/json"})
    assert r.status == 413 and r.json()["error"] == "body_too_large"


def test_big_body_streamed_without_length_is_413(tmp_path):
    chunked = AsgiClient(create_app(packs_dir=tmp_path), chunk=4096)
    big = b'{"state": "TL", "pad": "' + b"x" * LIMITS["body_bytes"] + b'"}'
    r = chunked.request(
        "POST",
        f"{API}/run",
        body=big,
        headers={"content-type": "application/json"},
        send_length=False,
    )
    assert r.status == 413


def test_small_streamed_body_still_works(tmp_path):
    chunked = AsgiClient(create_app(packs_dir=tmp_path), chunk=7)
    r = chunked.request(
        "POST",
        f"{API}/run",
        body=b'{"state": "TL", "draws": 50}',
        headers={"content-type": "application/json"},
        send_length=False,
    )
    assert r.status == 200 and r.json()["draws"] == 50


def _import_map_hash() -> str:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    body = re.search(r'<script type="importmap">(.*?)</script>', html, re.DOTALL).group(1)
    return "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"


@pytest.mark.parametrize(
    "url", ["/", f"{API}/states", f"{API}/nothing", f"{API}/states/ZZ/summary", "/js/main.js"]
)
def test_security_headers_on_every_response(client, url):
    r = client.get(url)
    h = r.headers
    csp = h["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert _import_map_hash() in csp
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert h["x-frame-options"] == "DENY"
    assert "server" not in h
    if url.startswith(API):
        assert h["cache-control"] == "no-store"


def test_headers_on_413_too(client):
    r = client.request(
        "POST", f"{API}/run", body=b"x", headers={"content-length": str(10**9)}, send_length=False
    )
    assert r.status == 413 and "content-security-policy" in r.headers


def test_inline_script_hashes_skip_external_scripts():
    html = '<script type="importmap">{"a":1}</script><script type="module" src="x.js"></script>'
    hashes = inline_script_hashes(html)
    expected = base64.b64encode(hashlib.sha256(b'{"a":1}').digest()).decode()
    assert hashes == [f"'sha256-{expected}'"]


def test_csv_cells_cannot_start_a_formula(client):
    r = client.get(f"{API}/export/csv?s=TL&r=adu&draws=100")
    for line in r.text.splitlines()[1:]:
        for cell in line.split(","):
            assert not cell.startswith(("=", "+", "@"))
