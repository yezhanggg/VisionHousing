"""Baseline card: state reconciliation fixture, PA window sensitivity, 2026 bridge, API card."""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from visionhousing.baseline import card
from visionhousing.baseline.contest import all_rules
from visionhousing.baseline.places import load_place_rules
from visionhousing.data.http import HttpClient
from visionhousing.data.sources import bps, bps_monthly
from visionhousing.ledger import load_ledger

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

# Release fixture: the state components of the shipped middle values, checked
# independently against the Census state annual files (2021-2025).
EXPECTED = {"PA": 151_882, "MA": 66_715, "WY": 10_090}


@pytest.fixture(scope="module")
def doc():
    return yaml.safe_load(card.RESULTS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rules_by_name():
    return {r.name: r for r in all_rules(load_ledger(), load_place_rules())}


# ----------------------------------------------------------------- reconciliation


@pytest.mark.parametrize("state", sorted(EXPECTED))
def test_shipped_rule_on_state_totals_reproduces_the_midpoint(doc, rules_by_name, state):
    entry = doc["states"][state]
    assert entry["role"] == "reconciled"
    assert entry["rule"] == card.shipped_rules()[state]
    annual = {int(y): v for y, v in entry["annual"].items()}
    comp = card.rule_state_component(
        rules_by_name[entry["rule"]], annual, 2025, list(range(2027, 2032))
    )
    assert round(comp) == EXPECTED[state] == entry["state_component"]
    assert entry["sources"] == ["bps_state_annual"]
    assert "baseline.horizon_years" in entry["assumption_ids"]
    assert f"{EXPECTED[state]:,}" in entry["formula"]


def test_pa_component_is_the_five_year_sum_and_ma_wy_five_times_three_year_mean(doc):
    pa = doc["states"]["PA"]["annual"]
    assert sum(pa.values()) == EXPECTED["PA"]
    for s in ("MA", "WY"):
        a = doc["states"][s]["annual"]
        assert round(5 * (a[2023] + a[2024] + a[2025]) / 3) == EXPECTED[s]


def test_vermont_is_an_outside_control(doc):
    vt = doc["states"]["VT"]
    assert vt["role"] == "control"
    assert "state_component" not in vt
    assert set(vt["annual"]) == {2021, 2022, 2023, 2024, 2025}


def test_shipped_export_middle_values_match_the_fixture(doc):
    """The five benchmark headline runs rest on these baselines; they must not move."""
    for s, want in EXPECTED.items():
        path = ROOT / "web" / "public" / "data" / s / "summary.json"
        if not path.is_file():
            pytest.skip(f"no static export for {s}")
        p50 = json.loads(path.read_text(encoding="utf-8"))["baseline"]["p50"]
        assert round(p50) == want


@pytest.mark.skipif(
    not (RAW / "bps" / "st2025a.txt").is_file(), reason="Census state files not fetched"
)
def test_card_rebuilt_from_the_census_files_matches_the_saved_card(doc):
    fresh = card.run_card(bridge=False, annual_release=doc["card"]["data_release"]["release_date"])
    assert fresh["states"] == doc["states"]
    assert fresh["diagnostics"] == doc["diagnostics"]
    assert fresh["card"] == doc["card"]


# ----------------------------------------------------------------- card fields


def test_card_names_years_columns_release_cutoff_and_gap(doc):
    c = doc["card"]
    assert c["unit_fields"] == ["est_1_units", "est_2_units", "est_3-4_units", "est_5+_units"]
    assert c["data_release"]["last_full_year"] == 2025
    assert c["data_release"]["release_date"]
    assert c["information_cutoff"] == card.information_cutoff()
    assert c["forecast_window"] == [2027, 2031]
    assert c["forecast_gap"] == [2026]
    assert doc["states"]["PA"]["historical_years"]["level_years"] == [2021, 2025]
    assert doc["states"]["PA"]["historical_years"]["share_years"] == [2016, 2025]
    assert doc["states"]["MA"]["historical_years"]["level_years"] == [2023, 2025]


def test_card_for_api():
    pa = card.card_for("PA")
    assert pa["state"] == "PA" and pa["forecast_gap"] == [2026]
    assert "baseline_pa_window_2022_2025" in pa["diagnostics"]
    assert pa["bridge"]["method"] == "carry_annual"
    assert card.card_for("MA")["diagnostics"] == {}
    assert card.card_for("TL") is None
    assert card.card_for(None) is None


def test_api_adds_the_card_for_real_states_only():
    from visionhousing.api.service import _baseline_card

    assert _baseline_card(SimpleNamespace(synthetic=True, state="TL")) is None
    got = _baseline_card(SimpleNamespace(synthetic=False, state="WY"))
    assert got["state_component"] == EXPECTED["WY"]


# ----------------------------------------------------------------- PA window sensitivity


def test_window_diagnostic_by_hand():
    d = card.window_diagnostic({2020: 10, 2021: 20, 2022: 30}, 2021, 2022, 5, 100)
    assert d["annual_mean"] == 25 and d["five_year"] == 125
    assert d["difference"] == 25 and d["difference_pct"] == 0.25
    with pytest.raises(ValueError):
        card.window_diagnostic({2021: 1}, 2020, 2021, 5, 10)


def test_pa_window_sensitivity_is_recorded_only(doc):
    d = doc["diagnostics"]["baseline_pa_window_2022_2025"]
    assert (d["annual_mean"], d["five_year"], d["difference"]) == (25_997, 129_985, -21_897)
    assert d["difference_pct"] == pytest.approx(-0.144, abs=5e-4)
    assert d["shipped_state_component"] == EXPECTED["PA"]
    assert d["use"] == "record_only"
    assert "not pre-registered" in d["status"]
    spec = card.load_settings()["window_diagnostics"]
    s = spec["baseline_pa_window_2022_2025"]
    assert {k for k in ("first_year", "last_year", "kind", "source", "rationale")} <= set(s)
    assert {"low", "central", "high"} <= set(s["first_year"])


# ----------------------------------------------------------------- 2026 bridge


def test_nowcast_by_hand():
    r = card.nowcast(50, {2020: 60, 2021: 80}, {2020: 100, 2021: 100})
    assert r["shares"] == {2020: 0.6, 2021: 0.8}
    assert r["low"] == pytest.approx(62.5)
    assert r["mid"] == pytest.approx(50 / 0.7)
    assert r["high"] == pytest.approx(50 / 0.6)
    with pytest.raises(ValueError):
        card.nowcast(1, {2020: 1}, {})


def test_nowcast_never_treats_year_to_date_as_a_full_year(doc):
    nc = doc["bridge"]["nowcast"]
    assert doc["bridge"]["method"] == "carry_annual"
    if nc["status"] != "available":
        pytest.skip("no monthly file was posted by the cutoff")
    assert nc["release_date"] <= doc["card"]["information_cutoff"]
    assert nc["next_release_date"] is None or nc["next_release_date"] > nc["release_date"]
    for v in nc["states"].values():
        assert v["low"] <= v["mid"] <= v["high"]
        assert v["low"] > v["ytd"]  # every history share is below one
        assert v["assumption_ids"][0] == "baseline_bridge_nowcast_2026"


HEAD = (
    "Survey,FIPS,Region,Division,State,,1-unit,,,2-units,,,3-4 units,,,5+ units,,,"
    "1-unit rep,,,2-units rep,,,3-4 units rep,,,5+ units rep\n"
    "Date,State,Code,Code,Name,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,"
    "Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value,Bldgs,Units,Value\n"
    " \n"
)


def _file(code: str, units: int) -> bytes:
    row = [code, "42", "1", "2", "Pennsylvania"]
    for _ in range(8):
        row += ["1", str(units), "10"]
    return (HEAD + ",".join(row) + "\n").encode()


def _msg(**values: str) -> email.message.Message:
    m = email.message.Message()
    for k, v in values.items():
        m[k.replace("_", "-")] = v
    return m


class _Resp:
    def __init__(self, body: bytes, modified: str | None):
        self.status = 200
        self._buf = io.BytesIO(body)
        self.headers = _msg(Content_Length=str(len(body)))
        if modified:
            self.headers["Last-Modified"] = modified

    def read(self, n=-1):
        return self._buf.read(n)

    def close(self):
        pass


class _Server:
    """Serves {file name: (body, Last-Modified)}; anything else is a 404."""

    def __init__(self, files):
        self.files = files
        self.gets: list[str] = []

    def open(self, req, data=None, timeout=None):
        name = req.full_url.rsplit("/", 1)[-1]
        if name not in self.files:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", _msg(), io.BytesIO(b""))
        body, modified = self.files[name]
        if req.get_method() == "GET":
            self.gets.append(name)
        return _Resp(body if req.get_method() == "GET" else b"", modified)


def _client(server):
    return HttpClient(opener=server, sleep=lambda _s: None, free_bytes=lambda _p: 10**12)


def test_parse_monthly_checks_the_month():
    text = _file("202609", 7).decode()
    wide = bps_monthly.parse_state_ytd(text, 2026, 9)
    assert bps_monthly.state_ytd_units(wide, "42") == 28
    with pytest.raises(ValueError):
        bps_monthly.parse_state_ytd(text, 2026, 8)
    with pytest.raises(ValueError):
        bps.parse_state_annual(text, 2026)


def test_file_posted_after_the_cutoff_is_not_downloaded(tmp_path):
    server = _Server(
        {
            "st2609y.txt": (_file("202609", 5), "Tue, 27 Oct 2026 11:45:26 GMT"),
            "st2610y.txt": (_file("202610", 6), "Wed, 25 Nov 2026 12:45:26 GMT"),
        }
    )
    got = bps_monthly.fetch_ytd(
        [(2026, 9), (2026, 10), (2026, 11)],
        tmp_path,
        client=_client(server),
        manifest=None,
        released_by="2026-11-24",
    )
    assert got[(2026, 9)]["release_date"] == "2026-10-27" and got[(2026, 9)]["path"]
    assert got[(2026, 10)]["release_date"] == "2026-11-25" and got[(2026, 10)]["path"] is None
    assert got[(2026, 11)]["release_date"] is None
    assert server.gets == ["st2609y.txt"]


def test_bridge_nowcast_from_fake_files(tmp_path):
    files = {"st2609y.txt": (_file("202609", 10), "Tue, 27 Oct 2026 11:45:26 GMT")}
    for y in range(2021, 2026):
        files[f"st{y % 100:02d}09y.txt"] = (_file(f"{y}09", 15), "Mon, 01 Nov 2021 00:00:00 GMT")
    settings = card.load_settings()
    annual = {"PA": {y: 80 for y in range(2021, 2026)}}  # 4 x 15 = 60 of 80: s = 0.75
    out = card._bridge(
        settings, ["PA"], annual, {"PA": 90.0}, {"PA": "x"}, [2026], "2026-11-24",
        tmp_path, _client(_Server(files)),
    )  # fmt: skip
    nc = out["nowcast"]
    assert nc["status"] == "available" and nc["through_month"] == 9
    assert nc["next_file"] == "st2610y.txt" and nc["next_release_date"] is None
    pa = nc["states"]["PA"]
    assert pa["ytd"] == 40 and pa["mid"] == round(40 / 0.75) == pa["low"] == pa["high"]
    assert pa["carried_annual"] == 90


def test_bridge_unavailable_when_no_monthly_file(tmp_path):
    settings = card.load_settings()
    out = card._bridge(
        settings, ["PA"], {"PA": {}}, {"PA": 1.0}, {"PA": "x"}, [2026], "2026-11-24",
        tmp_path, _client(_Server({})),
    )  # fmt: skip
    assert out["method"] == "carry_annual"
    assert out["nowcast"]["status"] == "unavailable"
    assert "no monthly" in out["nowcast"]["reason"]
