"""Place-profile interface endpoints on Testland, called in memory (``tests/asgi_client.py``).

The endpoint must return exactly the builder's numbers.
"""

from __future__ import annotations

import json

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.profile import build_profile, list_places, load_profile_context, render

API = "/api/v1"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


@pytest.fixture(scope="module")
def ctx():
    return load_profile_context("TL")


@pytest.mark.parametrize("geoid", ["99001", "99003000500", "99007"])
def test_profile_equals_builder(client, ctx, geoid):
    r = client.get(f"{API}/profile/TL/{geoid}")
    assert r.status == 200
    expected = json.loads(json.dumps(build_profile("TL", geoid, ctx=ctx).to_dict()))
    assert r.json() == expected


def test_profile_downloads(client, ctx):
    r = client.get(f"{API}/profile/TL/99001?format=csv")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text == render(build_profile("TL", "99001", ctx=ctx), "csv")
    page = client.get(f"{API}/profile/TL/99001?format=html")
    assert page.status == 200 and page.text.startswith("<!doctype html>")


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/profile/TL/12", 400, "invalid_place"),
        ("/profile/TL/abcde", 400, "invalid_place"),
        ("/profile/TL/99999", 404, "place_not_found"),
        ("/profile/TL/99001?format=pdf", 400, "invalid_format"),
        ("/profile/T1/99001", 400, "invalid_state"),
        ("/profile/OH/39001", 404, "state_not_built"),
        ("/places/TL?level=block", 400, "invalid_level"),
        ("/places/TL?limit=0", 400, "invalid_limit"),
        ("/places/TL?q=" + "x" * 101, 400, "query_too_long"),
    ],
)
def test_bad_requests(client, path, status, code):
    r = client.get(f"{API}{path}")
    assert r.status == status
    assert r.json()["error"] == code


def test_places_search(client, ctx):
    r = client.get(f"{API}/places/TL?q=harb")
    assert r.status == 200
    assert r.json()["places"] == list_places(ctx, "harb")
    tracts = client.get(f"{API}/places/TL?level=tract&q=99001&limit=500").json()["places"]
    assert tracts == list_places(ctx, "99001", level="tract", limit=500)


def test_profile_responses_carry_security_headers(client):
    r = client.get(f"{API}/profile/TL/99001")
    assert "content-security-policy" in r.headers or "x-content-type-options" in r.headers
