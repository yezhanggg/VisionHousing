"""D6: tract-to-town and tract-to-place tables for Vermont and Wyoming, and place readings.

- ``tract_municipalities`` / ``tract_places`` split each tract's 2020 housing
  units among the towns (county subdivisions) or Census places its blocks are
  in; a tract's pieces add up to all its units (weights sum to 1).
- Wyoming's cities are Census places, not county subdivisions: a place-level
  reading (7-digit ``geoid``) is placed on tracts through ``tract_places``.

Everything is offline except the last test, which reads the built VT and WY
packs when they are present and is skipped otherwise.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.sources import munstats
from visionhousing.engine.adapters import _place_readings
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.features.zoning_status import tract_municipalities, tract_places
from visionhousing.law.local_readings import LocalReadings
from visionhousing.law.places import place_pieces, readings_on_places

PACKS = Path(__file__).resolve().parents[1] / "data" / "packs"


def _blocks():
    return pd.DataFrame(
        {
            "geoid": ["560010001001000", "560010001001001", "560010001001002", "560010002001000"],
            "tract": ["56001000100", "56001000100", "56001000100", "56001000200"],
            "housing_units": [30, 10, 0, 0],
        }
    )


def _weights(pieces: pd.DataFrame) -> pd.Series:
    hu = pieces["housing_units_2020"].astype(float)
    total = hu.groupby(pieces["tract"]).transform("sum")
    size = hu.groupby(pieces["tract"]).transform("size")
    return (hu / total).where(total > 0, 1.0 / size).groupby(pieces["tract"]).sum()


def test_tract_places_keep_split_tracts_and_sum_to_one():
    bp = pd.DataFrame(
        {"block": ["560010001001000", "560010001001001"], "place": ["45050", munstats.NO_PLACE]}
    )  # the third block and the second tract's block are missing from the file
    t = tract_places(_blocks(), bp, munstats.NO_PLACE)
    assert list(t.columns) == ["tract", "county", "place", "housing_units_2020"]
    first = t[t["tract"] == "56001000100"].set_index("place")["housing_units_2020"]
    assert first.to_dict() == {"45050": 30, "99999": 10}  # split tract: one row per piece
    assert t[t["tract"] == "56001000200"]["place"].tolist() == ["99999"]
    assert t["housing_units_2020"].sum() == _blocks()["housing_units"].sum()
    np.testing.assert_allclose(_weights(t).to_numpy(), 1.0)
    m = tract_municipalities(
        _blocks(), pd.DataFrame({"block": _blocks()["geoid"], "cousub": ["1", "1", "2", "2"]})
    )
    np.testing.assert_allclose(_weights(m).to_numpy(), 1.0)


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


class _Client:
    def __init__(self, raw: bytes):
        self.raw, self.urls = raw, []

    def get_bytes(self, url: str) -> bytes:
        self.urls.append(url)
        return self.raw


def test_place_fetcher_reads_the_place_member_and_blanks_become_99999(tmp_path):
    raw = _zip(
        {
            "BlockAssign_ST56_WY_CD.txt": "BLOCKID|DISTRICT\n560010001001000|00\n",
            "BlockAssign_ST56_WY_INCPLACE_CDP.txt": (  # secret-scan: allow (Census file name)
                "BLOCKID|PLACEFP\n560010001001000|45050\n560010001001001|\n"
            ),
        }
    )
    c = _Client(raw)
    t = munstats.fetch_block_places("WY", tmp_path, client=c, manifest=None)
    assert t.to_dict("list") == {
        "block": ["560010001001000", "560010001001001"],
        "place": ["45050", "99999"],
    }
    assert c.urls == [
        "https://www2.census.gov/geo/docs/maps-data/data/baf2020/BlockAssign_ST56_WY.zip"
    ]
    again = munstats.fetch_block_places("WY", tmp_path, client=_Client(b""), manifest=None)
    assert again.equals(t)  # cached: no second download
    with pytest.raises(LookupError, match="census county divisions"):
        munstats.fetch_block_subdivisions("WY", tmp_path, client=_Client(raw), manifest=None)


def test_place_rows_become_county_pieces():
    tp = pd.DataFrame(
        {
            "tract": ["56001000100", "56001000100", "56003000100"],
            "county": ["56001", "56001", "56003"],
            "place": ["45050", "99999", "45050"],  # a place in two counties
            "housing_units_2020": [30, 10, 5],
        }
    )
    pieces = place_pieces(tp)
    assert list(pieces.columns) == ["tract", "county", "cousub", "housing_units_2020"]
    rows = pd.DataFrame(
        {"geoid": ["5645050", "5600199999", "5600002"], "county": ["56450", "56001", "56999"],
         "name": ["Laramie", "a town", "nowhere"], "min_small": [1.0, 2.0, 3.0]}
    )  # fmt: skip
    out = readings_on_places(rows, tp)
    assert out["geoid"].tolist() == ["5600145050", "5600345050", "5600199999"]
    assert out["county"].tolist() == ["56001", "56003", "56001"]  # county reset from the table
    assert "nowhere" not in set(out["name"])  # a place with no piece cannot be placed


def _state(tm=None, tp=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        state="WY",
        derived={
            ("parking_law_table", "tract_municipalities"): tm,
            ("parking_law_table", "tract_places"): tp,
        },
    )


def test_place_readings_pick_one_crosswalk():
    tp = pd.DataFrame(
        {"tract": ["56001000100"], "county": ["56001"], "place": ["45050"],
         "housing_units_2020": [30]}
    )  # fmt: skip
    laramie = pd.DataFrame({"geoid": ["5645050"], "county": ["56450"], "name": ["Laramie"]})
    rows, pieces, notes, src = _place_readings(_state(tp=tp), laramie, None, "mcd")
    assert rows["geoid"].tolist() == ["5600145050"] and src == munstats.BAF_PLACE_SOURCE
    assert pieces is not None and pieces["cousub"].tolist() == ["45050"]
    assert "tract_places" in notes[0]
    rows, pieces, notes, src = _place_readings(_state(), laramie, None, "mcd")
    assert pieces is None and "no tract_places" in notes[0]
    town = pd.DataFrame({"geoid": ["5600100001"], "county": ["56001"], "name": ["Town"]})
    mixed = pd.concat([laramie, town], ignore_index=True)
    rows, pieces, notes, src = _place_readings(_state(tp=tp), mixed, "TM", "mcd")
    assert rows["geoid"].tolist() == ["5600100001"] and pieces == "TM" and src == "mcd"
    assert "not mixed" in notes[0]
    rows, pieces, notes, src = _place_readings(_state(tp=tp), town, "TM", "mcd")
    assert notes == () and rows.equals(town)


@pytest.fixture(scope="module")
def tl():
    return load_context("TL")


def _tl_place(tl, monkeypatch, with_table=True):
    """Testland with one verified place reading (7-digit geoid) on one county's tracts."""
    c = replace(tl, local_law=None)
    c.derived.clear()
    tracts = c.tracts.assign(geoid=c.tracts["geoid"].astype(str))
    county = str(tracts["county"].iloc[0])
    mine = tracts[tracts["county"].astype(str) == county]
    tp = pd.DataFrame(
        {"tract": mine["geoid"], "county": county, "place": "00001", "housing_units_2020": 1}
    )
    c.derived[("parking_law_table", "tract_municipalities")] = None
    c.derived[("parking_law_table", "tract_places")] = tp if with_table else None
    doc = {
        "doc_id": "tl_code", "state": "TL", "title": "Testcity code", "publisher": "Testcity",
        "url": "https://testcity.example/code.pdf", "retrieved_on": "2026-11-01",
        "sha256": "0" * 64, "kind": "bylaw", "version_state": "final",
        "effective_date": "2026-01-01",
    }  # fmt: skip
    fact = {
        "topic": "parking", "value": "3 per unit", "review": "verified",
        "evidence": [{"doc_id": "tl_code", "locator": "Sec. 1", "quote": "spaces per unit"}],
        "numbers": {"residential_min_per_unit": 3.0},
    }  # fmt: skip
    place = {
        "place_id": "tl_testcity", "state": "TL", "name": "Testcity", "geoid": f"{county[:2]}00001",
        "status": "verified", "reviewed_on": "2026-11-01", "effective_date": "2026-01-01",
        "facts": [fact],
    }  # fmt: skip
    readings = LocalReadings(documents=(doc,), places=(place,))
    import visionhousing.law.local_readings as lr

    monkeypatch.setattr(lr, "load_local_readings", lambda *a, **k: readings)
    return c


def _parking(c, **opts):
    return run("TL", {"parking": opts}, draws=300, context=c, use_cache=False).reforms["parking"]


def test_a_place_reading_enters_the_mix_through_tract_places(tl, monkeypatch):
    c = _tl_place(tl, monkeypatch)
    a = _parking(c)
    b = _parking(c, minimums="local_readings", readings_state="TL")
    rec = b.result.records["parking_verified_share"]
    assert rec.p50 > 0 and munstats.BAF_PLACE_SOURCE in rec.source_ids
    assert b.added.p50 > a.added.p50
    assert any("Census place, placed on tracts" in n for n in b.result.notes)
    bare = _tl_place(tl, monkeypatch, with_table=False)
    d = _parking(bare, minimums="local_readings", readings_state="TL")
    np.testing.assert_allclose(d.result.added, _parking(bare).result.added, rtol=1e-12, atol=1e-9)
    assert any("no tract_places table" in n for n in d.result.notes)


@pytest.mark.skipif(
    not (PACKS / "VT" / "tract_municipalities.csv.gz").is_file()
    or not (PACKS / "WY" / "tract_places.csv.gz").is_file(),
    reason="built VT and WY packs with the tract crosswalks are not present",
)
def test_built_packs_cover_every_tract_and_find_the_read_places():
    from visionhousing.law.local_readings import load_local_readings

    readings = load_local_readings()
    for state, name, key in (
        ("VT", "tract_municipalities", "cousub"),
        ("VT", "tract_places", "place"),
        ("WY", "tract_places", "place"),
    ):
        folder = PACKS / state
        t = pd.read_csv(folder / f"{name}.csv.gz", dtype=str)
        t["housing_units_2020"] = t["housing_units_2020"].astype(int)
        tracts = pd.read_csv(folder / "tracts.csv.gz", dtype={"geoid": str})
        blocks = pd.read_csv(folder / "blocks.csv.gz", dtype={"tract": str})
        assert set(t["tract"]) == set(tracts["geoid"])
        assert t["housing_units_2020"].sum() == blocks["housing_units"].sum()
        np.testing.assert_allclose(_weights(t).to_numpy(), 1.0)
        keys = set(
            t["county"].str.zfill(5) + t[key].str.zfill(5)
            if key == "cousub"
            else t["county"].str[:2] + t[key].str.zfill(5)
        )
        digits = 10 if key == "cousub" else 7
        read = {
            p.geoid
            for p in readings.places
            if p.state == state and p.level == "municipality" and len(p.geoid) == digits
        }
        assert read <= keys, read - keys  # every town or city read is matched
    assert not (PACKS / "WY" / "tract_municipalities.csv.gz").is_file()  # census county divisions
