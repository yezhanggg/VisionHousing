"""Pro formas: method invariants, vectorised = scalar loop, records and ledger links."""

import itertools

import numpy as np
import pytest

from visionhousing.engine.records import Record
from visionhousing.ledger import load_ledger, sample
from visionhousing.proforma import adu as adu_module
from visionhousing.proforma import (
    adu_proforma,
    as_draw_column,
    midrise_proforma,
    payment_factor,
    redevelopment_proforma,
)
from visionhousing.proforma import residual as residual_module

RNG_SEED = 7
DRAWS = 64


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def central(ledger):
    return ledger.central()


@pytest.fixture(scope="module")
def draws(ledger):
    return sample(ledger, n=DRAWS, seed=RNG_SEED)


TRACTS = {
    "rent_monthly": np.array([700.0, 1100.0, 1500.0, 2200.0, 3200.0]),
    "cost_factor": np.array([0.85, 0.95, 1.0, 1.1, 1.25]),
    "acquisition_price": np.array([90000.0, 180000.0, 260000.0, 400000.0, 650000.0]),
    "sale_value_per_unit": np.array([150000.0, 250000.0, 350000.0, 500000.0, 800000.0]),
}

PRO_FORMAS = [
    ("adu", adu_proforma, {}),
    ("redevelopment", redevelopment_proforma, {}),
    ("redevelopment_sale", redevelopment_proforma, {"basis": "sale"}),
    ("midrise", midrise_proforma, {}),
    ("midrise_sale", midrise_proforma, {"basis": "sale"}),
]


# ---------------------------------------------------------------- ledger links


def test_every_input_is_in_the_ledger_with_the_same_unit(ledger):
    specs = [adu_module.INPUTS]
    for spec in (residual_module.REDEVELOPMENT, residual_module.MIDRISE):
        specs.append(spec.inputs("income", True) + spec.inputs("sale", True))
    for item in itertools.chain.from_iterable(specs):
        if item.is_parameter:
            assert item.key in ledger, item.key
            assert ledger[item.key].unit == item.unit, item.key


def test_runs_on_ledger_central_values(central):
    for _, fn, kwargs in PRO_FORMAS:
        result = fn(central, {k: v[2] for k, v in TRACTS.items()}, **kwargs)
        assert result.shape == ()
        assert all(np.isfinite(v) for v in result.lines.values())


def test_missing_input_is_named(central):
    params = dict(central)
    del params["adu.permit_fee"]
    with pytest.raises(KeyError, match=r"adu\.permit_fee"):
        adu_proforma(params, {"rent_monthly": 1000, "cost_factor": 1.0})
    with pytest.raises(KeyError, match=r"context\.sale_value_per_unit"):
        redevelopment_proforma(central, {"cost_factor": 1.0}, basis="sale")
    with pytest.raises(ValueError, match="basis"):
        midrise_proforma(central, {"cost_factor": 1.0}, basis="lease")  # type: ignore[arg-type]


# ---------------------------------------------------------------- vectorised = scalar loop


@pytest.mark.parametrize(("label", "fn", "kwargs"), PRO_FORMAS)
def test_vectorised_equals_scalar_loop(draws, label, fn, kwargs):
    grid = fn(as_draw_column(draws), TRACTS, **kwargs)
    assert grid.shape == (DRAWS, len(TRACTS["rent_monthly"]))
    for d in range(0, DRAWS, 9):
        for t in range(len(TRACTS["rent_monthly"])):
            params = {k: float(v[d]) for k, v in draws.items()}
            context = {k: float(v[t]) for k, v in TRACTS.items()}
            one = fn(params, context, **kwargs)
            for name, value in one.lines.items():
                assert grid.lines[name][d, t] == pytest.approx(float(value), rel=1e-12), (
                    label,
                    name,
                )


@pytest.mark.parametrize(("label", "fn", "kwargs"), PRO_FORMAS)
def test_records_match_the_arrays(draws, label, fn, kwargs):
    grid = fn(as_draw_column(draws), TRACTS, **kwargs)
    for index in [(0, 0), (13, 2), (DRAWS - 1, 4)]:
        records = grid.records(index)
        assert set(records) == set(grid.lines), label
        for name, record in records.items():
            assert record.value == pytest.approx(float(grid.lines[name][index]), rel=1e-12)


def test_records_need_a_single_element(draws):
    grid = adu_proforma(as_draw_column(draws), TRACTS)
    with pytest.raises(ValueError, match="single value"):
        grid.records((0,))


# ---------------------------------------------------------------- records render formulas


def test_adu_records_render_formulas(central, ledger):
    result = adu_proforma(central, {"rent_monthly": 1500, "cost_factor": 1.0})
    records = result.records(ledger=ledger)
    assert all(isinstance(r, Record) for r in records.values())
    building = records["building_cost"].formula
    assert building.startswith("ADU floor area (600 sq ft)")
    assert building.endswith("= $150,000/unit")
    assert "adu.hard_cost_per_sqft" in records["building_cost"].assumption_ids
    fees = records["fees"]
    assert "local permit fees" in fees.formula and "utility connection fees" in fees.formula
    assert set(fees.assumption_ids) == {"adu.permit_fee", "adu.utility_connection_fee"}
    assert "(ledger adu.utility_connection_fee)" in records["connection_fees"].formula
    assert "240 months" in records["loan_payment_factor"].formula
    breakeven = records["breakeven_rent"]
    assert "econ.mortgage_rate_30y" in breakeven.assumption_ids
    assert "freddie_mac_pmms" in breakeven.source_ids
    assert "(tract context)" in records["rent"].formula
    for record in records.values():
        assert record.formula.count("=") >= 1
        assert Record.from_json(record.to_json()) == record


def test_residual_records_render_formulas(central):
    context = {"rent_monthly": 1800, "cost_factor": 1.0, "acquisition_price": 250000}
    records = redevelopment_proforma(central, context).records()
    assert records["fees"].formula.startswith("units (4 units/lot)")
    assert "surface parking cost" in records["development_cost"].formula
    assert records["residual_land_value"].formula.startswith("stabilised value (")
    records = midrise_proforma(central, context).records()
    assert records["parking_cost"].name == "structured parking cost"
    assert "parking.cost_per_space_structured" in records["parking_cost"].assumption_ids
    assert "parking.cost_per_space_structured" in records["residual_land_value"].assumption_ids


def test_notes_say_family_use_is_applied_in_the_scenario(central):
    result = adu_proforma(central, {"rent_monthly": 1500, "cost_factor": 1.0})
    assert any("family-use" in note for note in result.notes)
    assert "adu.nonfinancial_share" not in result.assumption_ids()


# ---------------------------------------------------------------- invariants


def scale(params, key, factors):
    out = dict(params)
    out[key] = np.asarray(params[key], dtype=float) * factors
    return out


FACTORS = np.linspace(0.0, 2.0, 21)[:, None]


@pytest.mark.parametrize("fee", ["adu.permit_fee", "adu.utility_connection_fee"])
def test_lower_fees_never_raise_adu_breakeven(central, fee):
    result = adu_proforma(scale(central, fee, FACTORS), TRACTS)
    for name in ("breakeven_rent", "breakeven_home_value", "total_cost"):
        assert np.all(np.diff(result.lines[name], axis=0) >= 0), name


@pytest.mark.parametrize(("fn", "kwargs"), [(redevelopment_proforma, {}), (midrise_proforma, {})])
def test_lower_fees_never_lower_residual(central, fn, kwargs):
    result = fn(scale(central, "fees.fee_total_per_unit", FACTORS), TRACTS, **kwargs)
    assert np.all(np.diff(result.lines["residual_land_value"], axis=0) <= 0)


@pytest.mark.parametrize(
    ("fn", "key"),
    [
        (adu_proforma, "adu.added_parking_spaces"),
        (redevelopment_proforma, "parking.required_spaces_per_unit_attached"),
        (midrise_proforma, "parking.required_spaces_per_unit_multifamily"),
    ],
)
def test_fewer_parking_spaces_never_raise_cost(central, fn, key):
    params = dict(central)
    params[key] = np.linspace(0.0, 3.0, 13)[:, None]
    result = fn(params, TRACTS)
    cost = "total_cost" if "total_cost" in result.lines else "development_cost"
    for name in (cost, "parking_cost"):
        assert np.all(np.diff(result.lines[name], axis=0) >= 0), name
    if "residual_land_value" in result.lines:
        assert np.all(np.diff(result.lines["residual_land_value"], axis=0) <= 0)
    else:
        assert np.all(np.diff(result.lines["breakeven_rent"], axis=0) >= 0)


@pytest.mark.parametrize(
    ("fn", "kwargs", "key"),
    [
        (redevelopment_proforma, {}, "rent_monthly"),
        (midrise_proforma, {}, "rent_monthly"),
        (redevelopment_proforma, {"basis": "sale"}, "sale_value_per_unit"),
        (midrise_proforma, {"basis": "sale"}, "sale_value_per_unit"),
    ],
)
def test_higher_rent_never_lowers_residual(draws, fn, kwargs, key):
    context = dict(TRACTS, cost_factor=1.0)  # only rent (or sale value) changes across tracts
    context[key] = np.sort(TRACTS[key])
    result = fn(as_draw_column(draws), context, **kwargs)
    assert np.all(np.diff(result.lines["residual_land_value"], axis=1) >= 0)


def test_higher_rent_never_lowers_adu_surplus_and_leaves_breakeven(draws):
    result = adu_proforma(as_draw_column(draws), TRACTS)
    assert np.all(np.diff(result.lines["monthly_surplus"], axis=1) >= 0)
    # The break-even depends on cost only, so it is the same across tracts with
    # the same cost factor; with rising cost factors it never falls.
    assert np.all(np.diff(result.lines["breakeven_rent"], axis=1) >= 0)


def test_surplus_sign_agrees_with_breakeven(draws):
    result = adu_proforma(as_draw_column(draws), TRACTS)
    pays = result.lines["monthly_surplus"] >= 0
    above = result.lines["rent"] >= result.lines["breakeven_rent"]
    assert np.array_equal(pays, above)


def test_payment_factor_known_values():
    assert payment_factor(0.0, 10) == pytest.approx(1 / 120)
    assert payment_factor(0.06, 20) == pytest.approx(0.0071643106, abs=1e-10)
    assert payment_factor(0.12, 1) == pytest.approx(0.0888487887, abs=1e-10)
    rates = np.array([0.0, 0.03, 0.06, 0.09])
    assert np.all(np.diff(payment_factor(rates, 20)) > 0)
    with pytest.raises(ValueError, match="term"):
        payment_factor(0.05, 0)
