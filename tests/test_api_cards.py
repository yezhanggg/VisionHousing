"""The explanation cards' server views (``api/cards.py``) on Testland and the law timeline.

The confidence reply must carry the two signals unchanged and apart; the lever card's
binding factor must be the decision view's, and its effect range must be the paired
(draw-by-draw) difference between the variant run and the benchmark run; the
implementation rows must follow the timeline's verified records for the place, with
pending bills shown as pending and superseded versions kept as history.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api.cards import implementation_rows, load_cards, paired_effect
from visionhousing.api.decision import binding_constraint
from visionhousing.engine.core import run
from visionhousing.engine.variants import load_variants
from visionhousing.law.timeline import TimelineRecord
from visionhousing.validation.donor_pool import confidence_signals

API = "/api/v1"
DRAWS = 200


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


# ------------------------------------------------------------------ cards file


def test_cards_file_names_known_variants_and_every_benchmark():
    cfg = load_cards()
    known = load_variants()
    reforms = cfg["lever"]["reforms"]
    assert set(reforms) == {"adu", "missing_middle", "tod", "parking", "fees"}
    for family, spec in reforms.items():
        assert spec["verify"].strip(), family
        for name in spec["variants"]:
            assert name in known, name
            assert known[name].reform == family, name
    words = cfg["implementation"]
    for kind in ("local_words", "permit_words"):
        assert set(words[kind]) == {"done", "missing", "not_yet"}
    assert set(cfg["benchmark_types"]) <= set(cfg["type_labels"])


# ------------------------------------------------------------------ confidence


def test_confidence_keeps_the_two_signals_apart_and_unchanged(client):
    reply = client.get(f"{API}/confidence/TL")
    assert reply.status == 200
    body = reply.json()
    plain = confidence_signals("TL")
    assert body["reform_evidence"] == plain["reform_evidence"]
    bp = dict(body["baseline_performance"])
    assert bp.pop("coverage_text") is None  # Testland has no back-test record
    assert bp == plain["baseline_performance"]
    assert "grade" not in body  # never one merged score
    labels = body["type_labels"]
    assert set(body["reform_evidence"]["by_reform_type"]) <= set(labels)
    assert body["benchmark_types"][0] == "adu"


def test_confidence_rejects_a_bad_state(client):
    assert client.get(f"{API}/confidence/P1").status == 400


# ------------------------------------------------------------------ lever


def test_paired_effect_is_the_percentiles_of_the_draw_differences():
    v = np.array([10.0, 12, 14, 16, 18, 20, 22, 24, 26, 28])
    b = np.array([9.0, 9, 9, 9, 9, 9, 9, 9, 9, 9])
    out = paired_effect(v, b, name="x")
    want = np.percentile(v - b, [10, 50, 90])
    assert (out["p10"], out["p50"], out["p90"]) == tuple(float(x) for x in want)
    assert out["direction"] == "more"
    assert out["shown"]["p50"].endswith("units")
    with pytest.raises(ValueError):
        paired_effect(v, b[:5], name="x")


def test_lever_card_follows_the_decision_rule_and_pairs_the_draws(client):
    reply = client.get(f"{API}/lever/TL?r=adu&county=99001&draws={DRAWS}")
    assert reply.status == 200, reply.text
    body = reply.json()
    bench = run("TL", ["adu"], seed=0, draws=DRAWS)
    bc = binding_constraint(bench.reforms["adu"], "adu")
    assert body["binding"]["id"] == bc["binding"]["id"]
    assert body["who"]["actor"] == bc["binding"]["actor"]
    names = [e["variant"] for e in body["effects"]]
    assert names == load_cards()["lever"]["reforms"]["adu"]["variants"]
    tight = run("TL", {"adu": {"variant": "adu_financing_tight"}}, seed=0, draws=DRAWS)
    diff = tight.reforms["adu"].state_draws - bench.reforms["adu"].state_draws
    eff = body["effects"][0]["effect"]
    assert eff["p50"] == pytest.approx(float(np.percentile(diff, 50)))
    assert eff["p10"] <= eff["p50"] <= eff["p90"]
    assert body["effects"][0]["county_effect"]["p50"] is not None
    assert body["county"]["geoid"] == "99001"
    assert body["county"]["name"]
    assert body["grade"]["grade"] in ("A", "B", "C", "D")
    assert body["verify"]
    # every paired effect is traceable: the variant's ledger ids and the runs' sources
    tight_v = load_variants()["adu_financing_tight"]
    want = {*tight_v.overrides, *tight_v.overrides.values()}
    assert set(eff["assumption_ids"]) == want
    assert eff["source_ids"] and set(eff["source_ids"]) >= set(
        bench.reforms["adu"].added.source_ids
    )
    assert body["effects"][0]["county_effect"]["assumption_ids"] == eff["assumption_ids"]


def test_lever_and_plausibility_keys_hold_the_settings_stamp(tmp_path, monkeypatch):
    """The settings files are in the cache key: a new stamp renders again, the same one does not."""
    import visionhousing.api.cards as cards_mod
    import visionhousing.api.intake as intake_mod

    stamps = iter(["one", "two", "two"])
    monkeypatch.setattr(cards_mod, "stamp", lambda _f: next(stamps))
    renders: list[str] = []
    real = cards_mod.lever_payload

    def counted(*args, **kwargs):
        renders.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(cards_mod, "lever_payload", counted)
    c = AsgiClient(create_app(packs_dir=tmp_path))
    for _ in range(3):
        assert c.get(f"{API}/lever/TL?r=fees&draws={DRAWS}").status == 200
    assert renders == ["fees", "fees"]  # "one", then "two"; the second "two" is the cache

    pstamps = iter(["a", "b", "b"])
    monkeypatch.setattr(intake_mod, "stamp", lambda _f: next(pstamps))
    seen: list[int] = []
    import visionhousing.validation.plausibility as pl

    real_p = pl.plausibility
    monkeypatch.setattr(pl, "plausibility", lambda *a, **k: seen.append(1) or real_p(*a, **k))
    for _ in range(3):
        assert c.get(f"{API}/plausibility/TL?r=adu&draws={DRAWS}").status == 200
    assert len(seen) == 2


def test_lever_card_checks_its_query(client):
    assert client.get(f"{API}/lever/TL?r=adu,fees&draws={DRAWS}").status == 400
    assert client.get(f"{API}/lever/TL?draws={DRAWS}").status == 400
    assert client.get(f"{API}/lever/TL?r=adu&county=12&draws={DRAWS}").status == 400
    assert client.get(f"{API}/lever/TL?r=adu&county=42003&draws={DRAWS}").status == 404


def test_lever_card_on_a_real_state_answers_only_the_default_sampling(tmp_path):
    (tmp_path / "PA").mkdir()
    (tmp_path / "PA" / "tracts.csv").write_text("geoid,county\n")
    app = create_app(packs_dir=tmp_path)
    reply = AsgiClient(app).get(f"{API}/lever/PA?r=adu&seed=7")
    assert reply.status == 400
    assert reply.json()["error"] == "default_sampling_only"
    assert app.fastapi.state.runner.stats["misses"] == 0
    app.fastapi.state.runner.shutdown()


# ------------------------------------------------------------------ implementation


def _rec(**kw):
    base = {
        "citation": "Test rule",
        "verified_on": date(2026, 1, 1),
        "sources": ("https://example.org/rule",),
    }
    return TimelineRecord(**{**base, **kw})


RECORDS = [
    _rec(record_id="bill_pending", provision_id="bill", jurisdiction="PA",
         housing_type="adu", status="pending", start=date(2026, 2, 1)),
    _rec(record_id="old_version", provision_id="law", jurisdiction="PA", housing_type="adu",
         status="legally_effective", start=date(2020, 1, 1), end=date(2025, 7, 1),
         end_verified_on=date(2026, 1, 1)),
    _rec(record_id="new_version", provision_id="law", jurisdiction="PA", housing_type="adu",
         status="legally_effective", start=date(2025, 7, 1)),
    _rec(record_id="law_local", provision_id="law", jurisdiction="PA", housing_type="adu",
         status="locally_implemented", start=date(2025, 9, 1)),
    _rec(record_id="town_rule", provision_id="town", jurisdiction="4200361000",
         housing_type="adu", status="legally_effective", start=date(2026, 1, 1)),
    _rec(record_id="other_town", provision_id="other", jurisdiction="4207702000",
         housing_type="adu", status="practically_usable", start=date(2026, 1, 1)),
    _rec(record_id="late_check", provision_id="late", jurisdiction="PA", housing_type="adu",
         status="legally_effective", start=date(2026, 1, 1), verified_on=date(2026, 12, 1)),
    _rec(record_id="fees_rule", provision_id="fees", jurisdiction="PA", housing_type="fees",
         status="legally_effective", start=date(2026, 1, 1)),
]  # fmt: skip


def _rows(**kw):
    out = implementation_rows(RECORDS, state="PA", reforms=["adu"], on=date(2026, 6, 1),
                              state_fips="42", **kw)  # fmt: skip
    return {r["provision_id"]: r for r in out["rows"]}


def test_implementation_rows_by_status():
    rows = _rows(geoid="42003")
    assert set(rows) == {"bill", "law", "town"}  # other county, other type, late check: out
    bill = rows["bill"]
    assert bill["pending"] and bill["line"] == "Pending bill (not law)"
    assert bill["local"]["state"] == bill["permits"]["state"] == "not_yet"
    law = rows["law"]
    assert law["legal"]["record"]["record_id"] == "law_local"
    assert law["line"] == (
        "Legally effective · local guidance issued · permit results not yet published"
    )
    assert law["local"]["record"]["sources"] == ["https://example.org/rule"]
    assert [h["record_id"] for h in law["history"]] == ["old_version", "new_version"]
    assert rows["town"]["level"] == "municipality"
    assert rows["town"]["local"]["state"] == "missing"


def test_implementation_rows_whole_state_and_permit_step():
    rows = _rows()
    assert {"bill", "law", "town", "other"} <= set(rows)
    assert rows["other"]["permits"]["state"] == "done"
    assert rows["other"]["permits"]["text"] == "permit results published"


def test_implementation_endpoint(client):
    reply = client.get(f"{API}/implementation/PA?r=adu&geoid=42003")
    assert reply.status == 200
    body = reply.json()
    juris = {r["jurisdiction"] for r in body["rows"]}
    assert "PA" in juris and "4207702000" not in juris
    assert any(r["pending"] for r in body["rows"])
    assert all(r["legal"]["record"]["sources"] for r in body["rows"])
    assert client.get(f"{API}/implementation/PA?geoid=4200").status == 400
    assert client.get(f"{API}/implementation/PA?geoid=50001").status == 404
    assert client.get(f"{API}/implementation/PA?r=bogus").status == 400
    assert client.get(f"{API}/implementation/PA?on=yesterday").status == 400
    empty = client.get(f"{API}/implementation/TL?r=adu").json()
    assert empty["rows"] == [] and empty["none_text"]
