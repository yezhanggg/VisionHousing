"""Result records: formulas written from the arithmetic, ids carried, JSON round-trip."""

import numpy as np
import pytest
from pydantic import ValidationError

from visionhousing.engine.records import (
    Record,
    add,
    div,
    format_number,
    from_draws,
    from_parameter,
    mul,
    product,
    quantity,
    sub,
)
from visionhousing.ledger import load_ledger

TIMES = chr(0x00D7)


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


def test_mul_formula_reads_like_the_arithmetic():
    lots = quantity("eligible lots", 1000, "lots", tier=1, source_ids=["acs_b25024"])
    fit = quantity("fit share", 0.536, "share", assumption_ids=["adu.fit_share_sf_detached"])
    out = mul(lots, fit, name="lots with room")
    assert out.value == pytest.approx(536)
    assert out.unit == "lots"
    assert out.formula == f"eligible lots (1,000 lots) {TIMES} fit share (0.536) = 536 lots"
    assert out.assumption_ids == ("adu.fit_share_sf_detached",)
    assert out.source_ids == ("acs_b25024",)
    assert out.tier == 1
    assert [i.name for i in out.inputs] == ["eligible lots", "fit share"]


def test_fee_saving_from_ledger(ledger):
    fee = from_parameter("fees.fee_total_per_unit", ledger["fees.fee_total_per_unit"])
    cut = from_parameter("fees.fee_cut_share", ledger["fees.fee_cut_share"])
    saving = mul(fee, cut, name="fee saved per unit")
    assert saving.value == 25000
    assert saving.unit == "$/unit"
    assert saving.formula.endswith("= $25,000/unit")
    assert "($50,000/unit)" in saving.formula and "(0.5)" in saving.formula
    assert saving.assumption_ids == ("fees.fee_cut_share", "fees.fee_total_per_unit")
    assert saving.source_ids == ("handbook_benchmarks",)
    assert "ledger fees.fee_total_per_unit, central" in fee.formula


def test_funnel_product_and_other_operators():
    e = quantity("eligible lots", 2000, "lots", tier=0)
    f = quantity("fit", 0.5, "share", tier=2)
    h = quantity("take-up", 0.01, "share per year")
    yearly = product([e, f, h], name="ADUs per year", unit="units per year")
    assert yearly.value == pytest.approx(10)
    assert yearly.formula.count(TIMES) == 2
    assert yearly.tier == 0  # coarsest input wins
    total = add([yearly, yearly], name="two years")
    assert total.value == pytest.approx(20) and " + " in total.formula
    net = sub(total, yearly, name="net")
    assert net.value == pytest.approx(10) and chr(0x2212) in net.formula
    per = div(e, quantity("tracts", 4, "tracts"), name="lots per tract")
    assert per.value == 500 and per.unit == "lots per tracts" and chr(0x00F7) in per.formula


def test_operator_guards():
    a = quantity("a", 1, "lots")
    b = quantity("b", 1, "units")
    with pytest.raises(ValueError, match="different units"):
        add([a, b], name="x")
    with pytest.raises(ValueError, match="cannot subtract"):
        sub(a, b, name="x")
    with pytest.raises(ZeroDivisionError, match="'zero'"):
        div(a, quantity("zero", 0, "lots"), name="x")
    with pytest.raises(ValueError):
        product([], name="x")


def test_from_draws_gives_ordered_range():
    draws = np.random.default_rng(0).normal(100, 10, 2000)
    r = from_draws("added units", draws, "units", expression="the ADU funnel", tier=1)
    assert r.is_range and r.p10 <= r.p50 <= r.p90
    assert r.formula.startswith("P10, P50, P90 of 2,000 seeded draws of the ADU funnel = ")
    with pytest.raises(ValueError, match="is a range"):
        mul(r, r, name="x")


def test_record_must_be_point_or_full_range():
    with pytest.raises(ValidationError):
        Record(name="x", unit="u", formula="f")
    with pytest.raises(ValidationError):
        Record(name="x", unit="u", formula="f", value=1, p10=0, p50=1, p90=2)
    with pytest.raises(ValidationError):
        Record(name="x", unit="u", formula="f", p10=0, p50=1)
    with pytest.raises(ValidationError):
        Record(name="x", unit="u", formula="f", p10=3, p50=1, p90=2)


def test_json_round_trip():
    lots = quantity("eligible lots", 1234.5, "lots", tier=2, source_ids=["acs"])
    fit = quantity("fit share", 1 / 3, "share", assumption_ids=["adu.fit_share_sf_detached"])
    point = mul(lots, fit, name="lots with room")
    ranged = from_draws("units", [1.0, 2.0, 3.0, 4.0], "units", expression="x", inputs=[point])
    for record in (point, ranged):
        back = Record.from_json(record.to_json())
        assert back == record
        assert back.to_json() == record.to_json()


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (25000, "$/unit", "$25,000/unit"),
        (-1500, "$", "-$1,500"),
        (536.0, "lots", "536 lots"),
        (1.25, "ratio", "1.25"),
        (0.0661, "share per year", "0.0661"),
        (0, "units", "0 units"),
        (12.5, "sq ft", "12.5 sq ft"),
    ],
)
def test_format_number(value, unit, text):
    assert format_number(value, unit) == text
