"""Pro formas: three hand-calculated golden cases each, checked to the cent.

Each case gives every input explicitly (not the ledger), so a change to the
ledger never moves a golden number. The hand calculation is in the docstring.
The monthly payment factor is ``r / (1 - (1 + r) ** -m)`` with ``r`` the yearly
rate / 12 and ``m`` = 12 x years; with a zero rate it is ``1 / m``.
"""

import pytest

from visionhousing.proforma import adu_proforma, midrise_proforma, redevelopment_proforma

CENT = 0.005


def adu_params(**changes: float) -> dict[str, float]:
    base = {
        "adu.unit_size_sqft": 600,
        "adu.hard_cost_per_sqft": 250,
        "adu.site_utility_cost": 15000,
        "adu.soft_cost_share": 0.20,
        "adu.permit_fee": 2500,
        "adu.utility_connection_fee": 3000,
        "adu.added_parking_spaces": 0,
        "parking.cost_per_space_surface": 8000,
        "adu.loan_share": 0.80,
        "econ.mortgage_rate_30y": 0.06,
        "adu.loan_rate_spread": 0.0,
        "adu.loan_term_years": 20,
        "adu.cash_return": 0.06,
        "adu.required_coverage": 1.0,
        "adu.operating_cost_share": 0.30,
        "adu.value_uplift_share": 0.25,
    }
    base.update({k.replace("__", "."): v for k, v in changes.items()})
    return base


def check(lines, expected: dict[str, float]) -> None:
    for name, value in expected.items():
        assert float(lines[name]) == pytest.approx(value, abs=CENT), name


# ---------------------------------------------------------------- homeowner ADU


def test_adu_golden_benchmark():
    """Benchmark ADU, 600 sq ft, no parking, 6% for both loan and cash.

    building   = 600 x $250 x 1.0                       = $150,000.00
    hard       = 150,000 + 15,000 site work             = $165,000.00
    soft       = 0.20 x 165,000                         =  $33,000.00
    fees       = 2,500 permit + 3,000 connection        =   $5,500.00
    parking    = 0 spaces x $8,000                      =       $0.00
    total      = 165,000 + 33,000 + 5,500 + 0           = $203,500.00
    loan       = 0.80 x 203,500 = $162,800; cash = $40,700
    factor     = 0.005 / (1 - 1.005^-240)
               = 0.005 / (1 - 1 / 3.3102044758) = 0.005 / 0.6979038... = 0.0071643106
    loan pay   = 162,800 x 0.0071643106                 =   $1,166.35
    cash cost  = 40,700 x 0.0071643106                  =     $291.59
    monthly    = 1,166.35 + 291.59                      =   $1,457.94
    net rent   = 1,500 x (1 - 0.30)                     =   $1,050.00
    break-even = 1.0 x 1,457.9372 / 0.70                =   $2,082.77
    surplus    = 1,050.00 - 1,457.94                    =    -$407.94
    home value = 203,500 / 0.25                         = $814,000.00
    """
    result = adu_proforma(adu_params(), {"rent_monthly": 1500, "cost_factor": 1.0})
    check(
        result.lines,
        {
            "building_cost": 150000.00,
            "hard_cost": 165000.00,
            "soft_cost": 33000.00,
            "permit_fees": 2500.00,
            "connection_fees": 3000.00,
            "fees": 5500.00,
            "parking_cost": 0.00,
            "total_cost": 203500.00,
            "loan_amount": 162800.00,
            "owner_cash": 40700.00,
            "loan_payment": 1166.35,
            "cash_cost": 291.59,
            "monthly_cost": 1457.94,
            "net_rent": 1050.00,
            "breakeven_rent": 2082.77,
            "monthly_surplus": -407.94,
            "breakeven_home_value": 814000.00,
        },
    )
    assert float(result.lines["loan_payment_factor"]) == pytest.approx(0.0071643106, abs=1e-10)


def test_adu_golden_sb1346_style():
    """No connection fee, all borrowed at 7% for 30 years, coverage 1.1.

    building   = 600 x $200 x 0.9                       = $108,000.00
    hard       = 108,000 + 10,000                       = $118,000.00
    soft       = 0.15 x 118,000                         =  $17,700.00
    fees       = 1,000 permit + 0 connection            =   $1,000.00
    total      = 118,000 + 17,700 + 1,000 + 0           = $136,700.00
    loan       = 1.0 x 136,700 = $136,700; cash = $0
    rate       = 0.065 + 0.005 = 0.07;  r = 0.07 / 12 = 0.0058333
    factor     = 0.0058333 / (1 - 1 / 8.1164974754) = 0.0058333 / 0.8767941 = 0.0066530250
    loan pay   = 136,700 x 0.0066530250                 =     $909.47
    monthly    = 909.47 + 0                             =     $909.47
    required   = 1.1 x 909.4685                         =   $1,000.42
    net rent   = 1,400 x (1 - 0.25)                     =   $1,050.00
    break-even = 1,000.4154 / 0.75                      =   $1,333.89
    surplus    = 1,050.00 - 1,000.42                    =      $49.58
    home value = 136,700 / 0.25                         = $546,800.00
    """
    params = adu_params(
        adu__hard_cost_per_sqft=200,
        adu__site_utility_cost=10000,
        adu__soft_cost_share=0.15,
        adu__permit_fee=1000,
        adu__utility_connection_fee=0,
        adu__loan_share=1.0,
        econ__mortgage_rate_30y=0.065,
        adu__loan_rate_spread=0.005,
        adu__loan_term_years=30,
        adu__cash_return=0.05,
        adu__required_coverage=1.1,
        adu__operating_cost_share=0.25,
    )
    result = adu_proforma(params, {"rent_monthly": 1400, "cost_factor": 0.9})
    check(
        result.lines,
        {
            "building_cost": 108000.00,
            "hard_cost": 118000.00,
            "soft_cost": 17700.00,
            "connection_fees": 0.00,
            "fees": 1000.00,
            "total_cost": 136700.00,
            "owner_cash": 0.00,
            "loan_rate": 0.07,
            "loan_payment": 909.47,
            "monthly_cost": 909.47,
            "required_net_rent": 1000.42,
            "net_rent": 1050.00,
            "breakeven_rent": 1333.89,
            "monthly_surplus": 49.58,
            "breakeven_home_value": 546800.00,
        },
    )


def test_adu_golden_zero_rate_with_parking():
    """Larger ADU with one parking space, zero rates, half borrowed, 10 years.

    building   = 800 x $300 x 1.2                       = $288,000.00
    hard       = 288,000 + 20,000                       = $308,000.00
    soft       = 0.25 x 308,000                         =  $77,000.00
    fees       = 4,000 + 6,000                          =  $10,000.00
    parking    = 1 x 8,000                              =   $8,000.00
    total      = 308,000 + 77,000 + 10,000 + 8,000      = $403,000.00
    loan       = 0.5 x 403,000 = $201,500; cash = $201,500
    factor     = 1 / (12 x 10) = 1 / 120                = 0.0083333
    loan pay   = 201,500 / 120                          =   $1,679.17
    cash cost  = 201,500 / 120                          =   $1,679.17
    monthly    = 403,000 / 120                          =   $3,358.33
    net rent   = 2,000 x (1 - 0.40)                     =   $1,200.00
    break-even = 3,358.3333 / 0.60                      =   $5,597.22
    surplus    = 1,200.00 - 3,358.33                    =  -$2,158.33
    home value = 403,000 / 0.30                         = $1,343,333.33
    """
    params = adu_params(
        adu__unit_size_sqft=800,
        adu__hard_cost_per_sqft=300,
        adu__site_utility_cost=20000,
        adu__soft_cost_share=0.25,
        adu__permit_fee=4000,
        adu__utility_connection_fee=6000,
        adu__added_parking_spaces=1,
        adu__loan_share=0.5,
        econ__mortgage_rate_30y=0.0,
        adu__loan_term_years=10,
        adu__cash_return=0.0,
        adu__operating_cost_share=0.40,
        adu__value_uplift_share=0.30,
    )
    result = adu_proforma(params, {"rent_monthly": 2000, "cost_factor": 1.2})
    check(
        result.lines,
        {
            "building_cost": 288000.00,
            "hard_cost": 308000.00,
            "soft_cost": 77000.00,
            "fees": 10000.00,
            "parking_cost": 8000.00,
            "total_cost": 403000.00,
            "loan_payment": 1679.17,
            "cash_cost": 1679.17,
            "monthly_cost": 3358.33,
            "net_rent": 1200.00,
            "breakeven_rent": 5597.22,
            "monthly_surplus": -2158.33,
            "breakeven_home_value": 1343333.33,
        },
    )


# ---------------------------------------------------------------- 2-6 unit redevelopment


def mm_params(**changes: float) -> dict[str, float]:
    base = {
        "missing_middle.units_per_project": 4,
        "missing_middle.unit_size_sqft": 1000,
        "missing_middle.hard_cost_per_sqft": 200,
        "missing_middle.soft_cost_share": 0.25,
        "fees.fee_total_per_unit": 50000,
        "parking.required_spaces_per_unit_attached": 2,
        "parking.cost_per_space_surface": 8000,
        "missing_middle.developer_margin": 0.15,
        "missing_middle.operating_cost_share": 0.35,
        "missing_middle.cap_rate": 0.06,
    }
    base.update({k.replace("__", "."): v for k, v in changes.items()})
    return base


def test_redevelopment_golden_fourplex_income():
    """Fourplex valued on rent, benchmark fees, two surface spaces per unit.

    floor area = 4 x 1,000                              =     4,000 sq ft
    hard       = 4,000 x $200 x 1.0                     =   $800,000.00
    soft       = 0.25 x 800,000                         =   $200,000.00
    fees       = 4 x $50,000                            =   $200,000.00
    parking    = (4 x 2 = 8 spaces) x $8,000            =    $64,000.00
    dev cost   = 800,000 + 200,000 + 200,000 + 64,000   = $1,264,000.00
    return     = 0.15 x 1,264,000                       =   $189,600.00
    gross rent = 4 x $1,800 x 12                        =    $86,400.00 a year
    NOI        = 86,400 x (1 - 0.35)                    =    $56,160.00 a year
    value      = 56,160 / 0.06                          =   $936,000.00
    residual   = 936,000 - (1,264,000 + 189,600)        =  -$517,600.00
    margin     = -517,600 - 250,000 acquisition         =  -$767,600.00
    """
    result = redevelopment_proforma(
        mm_params(), {"rent_monthly": 1800, "cost_factor": 1.0, "acquisition_price": 250000}
    )
    check(
        result.lines,
        {
            "floor_area": 4000,
            "hard_cost": 800000.00,
            "soft_cost": 200000.00,
            "fees": 200000.00,
            "parking_spaces": 8,
            "parking_cost": 64000.00,
            "development_cost": 1264000.00,
            "developer_return": 189600.00,
            "gross_rent": 86400.00,
            "noi": 56160.00,
            "stabilised_value": 936000.00,
            "residual_land_value": -517600.00,
            "land_margin": -767600.00,
        },
    )


def test_redevelopment_golden_sixplex_sale_after_reforms():
    """Sixplex sold unit by unit, fees halved, no parking required.

    floor area = 6 x 900                                =     5,400 sq ft
    hard       = 5,400 x $180 x 1.1                     = $1,069,200.00
    soft       = 0.20 x 1,069,200                       =   $213,840.00
    fees       = 6 x $25,000                            =   $150,000.00
    parking    = (6 x 0 = 0 spaces) x $8,000            =         $0.00
    dev cost   = 1,069,200 + 213,840 + 150,000 + 0      = $1,433,040.00
    return     = 0.10 x 1,433,040                       =   $143,304.00
    value      = 6 x $400,000                           = $2,400,000.00
    residual   = 2,400,000 - (1,433,040 + 143,304)      =   $823,656.00
    margin     = 823,656 - 300,000                      =   $523,656.00
    """
    params = mm_params(
        missing_middle__units_per_project=6,
        missing_middle__unit_size_sqft=900,
        missing_middle__hard_cost_per_sqft=180,
        missing_middle__soft_cost_share=0.20,
        fees__fee_total_per_unit=25000,
        parking__required_spaces_per_unit_attached=0,
        missing_middle__developer_margin=0.10,
    )
    context = {"sale_value_per_unit": 400000, "cost_factor": 1.1, "acquisition_price": 300000}
    result = redevelopment_proforma(params, context, basis="sale")
    check(
        result.lines,
        {
            "hard_cost": 1069200.00,
            "soft_cost": 213840.00,
            "fees": 150000.00,
            "parking_cost": 0.00,
            "development_cost": 1433040.00,
            "developer_return": 143304.00,
            "stabilised_value": 2400000.00,
            "residual_land_value": 823656.00,
            "land_margin": 523656.00,
        },
    )
    assert "noi" not in result.lines


def test_redevelopment_golden_duplex_income_no_acquisition():
    """Duplex in a high-rent tract, one space per unit, no acquisition price given.

    floor area = 2 x 1,200                              =     2,400 sq ft
    hard       = 2,400 x $210 x 0.95                    =   $478,800.00
    soft       = 0.25 x 478,800                         =   $119,700.00
    fees       = 2 x $50,000                            =   $100,000.00
    parking    = (2 x 1 = 2 spaces) x $6,000            =    $12,000.00
    dev cost   = 478,800 + 119,700 + 100,000 + 12,000   =   $710,500.00
    return     = 0.15 x 710,500                         =   $106,575.00
    gross rent = 2 x $2,500 x 12                        =    $60,000.00 a year
    NOI        = 60,000 x (1 - 0.30)                    =    $42,000.00 a year
    value      = 42,000 / 0.05                          =   $840,000.00
    residual   = 840,000 - (710,500 + 106,575)          =    $22,925.00
    """
    params = mm_params(
        missing_middle__units_per_project=2,
        missing_middle__unit_size_sqft=1200,
        missing_middle__hard_cost_per_sqft=210,
        parking__required_spaces_per_unit_attached=1,
        parking__cost_per_space_surface=6000,
        missing_middle__operating_cost_share=0.30,
        missing_middle__cap_rate=0.05,
    )
    result = redevelopment_proforma(params, {"rent_monthly": 2500, "cost_factor": 0.95})
    check(
        result.lines,
        {
            "hard_cost": 478800.00,
            "soft_cost": 119700.00,
            "fees": 100000.00,
            "parking_cost": 12000.00,
            "development_cost": 710500.00,
            "developer_return": 106575.00,
            "noi": 42000.00,
            "stabilised_value": 840000.00,
            "residual_land_value": 22925.00,
        },
    )
    assert "land_margin" not in result.lines


# ---------------------------------------------------------------- mid-rise multifamily


def mf_params(**changes: float) -> dict[str, float]:
    base = {
        "multifamily.units_per_project": 60,
        "multifamily.unit_size_sqft": 850,
        "multifamily.hard_cost_per_sqft": 300,
        "multifamily.soft_cost_share": 0.25,
        "fees.fee_total_per_unit": 50000,
        "parking.required_spaces_per_unit_multifamily": 1.5,
        "parking.cost_per_space_structured": 52000,
        "multifamily.developer_margin": 0.15,
        "multifamily.operating_cost_share": 0.38,
        "multifamily.cap_rate": 0.055,
    }
    base.update({k.replace("__", "."): v for k, v in changes.items()})
    return base


def test_midrise_golden_current_rules():
    """60 apartments, benchmark fees, 1.5 structured spaces per unit.

    floor area = 60 x 850                               =     51,000 sq ft
    hard       = 51,000 x $300 x 1.0                    = $15,300,000.00
    soft       = 0.25 x 15,300,000                      =  $3,825,000.00
    fees       = 60 x $50,000                           =  $3,000,000.00
    parking    = (60 x 1.5 = 90 spaces) x $52,000       =  $4,680,000.00
    dev cost   = 15.3m + 3.825m + 3.0m + 4.68m          = $26,805,000.00
    return     = 0.15 x 26,805,000                      =  $4,020,750.00
    gross rent = 60 x $2,200 x 12                       =  $1,584,000.00 a year
    NOI        = 1,584,000 x (1 - 0.38)                 =    $982,080.00 a year
    value      = 982,080 / 0.055                        = $17,856,000.00
    residual   = 17,856,000 - (26,805,000 + 4,020,750)  = -$12,969,750.00
    margin     = -12,969,750 - 2,000,000                = -$14,969,750.00
    """
    context = {"rent_monthly": 2200, "cost_factor": 1.0, "acquisition_price": 2000000}
    result = midrise_proforma(mf_params(), context)
    check(
        result.lines,
        {
            "floor_area": 51000,
            "hard_cost": 15300000.00,
            "soft_cost": 3825000.00,
            "fees": 3000000.00,
            "parking_spaces": 90,
            "parking_cost": 4680000.00,
            "development_cost": 26805000.00,
            "developer_return": 4020750.00,
            "gross_rent": 1584000.00,
            "noi": 982080.00,
            "stabilised_value": 17856000.00,
            "residual_land_value": -12969750.00,
            "land_margin": -14969750.00,
        },
    )


def test_midrise_golden_transit_area_after_reforms():
    """100 apartments near transit: fees halved, 60% of 1.5 spaces still built.

    floor area = 100 x 800                              =     80,000 sq ft
    hard       = 80,000 x $250 x 1.05                   = $21,000,000.00
    soft       = 0.20 x 21,000,000                      =  $4,200,000.00
    fees       = 100 x $25,000                          =  $2,500,000.00
    parking    = (100 x 0.9 = 90 spaces) x $40,000      =  $3,600,000.00
    dev cost   = 21.0m + 4.2m + 2.5m + 3.6m             = $31,300,000.00
    return     = 0.12 x 31,300,000                      =  $3,756,000.00
    gross rent = 100 x $3,000 x 12                      =  $3,600,000.00 a year
    NOI        = 3,600,000 x (1 - 0.35)                 =  $2,340,000.00 a year
    value      = 2,340,000 / 0.05                       = $46,800,000.00
    residual   = 46,800,000 - (31,300,000 + 3,756,000)  = $11,744,000.00
    """
    params = mf_params(
        multifamily__units_per_project=100,
        multifamily__unit_size_sqft=800,
        multifamily__hard_cost_per_sqft=250,
        multifamily__soft_cost_share=0.20,
        fees__fee_total_per_unit=25000,
        parking__required_spaces_per_unit_multifamily=0.9,
        parking__cost_per_space_structured=40000,
        multifamily__developer_margin=0.12,
        multifamily__operating_cost_share=0.35,
        multifamily__cap_rate=0.05,
    )
    result = midrise_proforma(params, {"rent_monthly": 3000, "cost_factor": 1.05})
    check(
        result.lines,
        {
            "hard_cost": 21000000.00,
            "soft_cost": 4200000.00,
            "fees": 2500000.00,
            "parking_spaces": 90,
            "parking_cost": 3600000.00,
            "development_cost": 31300000.00,
            "developer_return": 3756000.00,
            "noi": 2340000.00,
            "stabilised_value": 46800000.00,
            "residual_land_value": 11744000.00,
        },
    )


def test_midrise_golden_condominium_sale():
    """40 condominiums sold at $650,000 each, one structured space per unit.

    floor area = 40 x 900                               =     36,000 sq ft
    hard       = 36,000 x $320 x 0.9                    = $10,368,000.00
    soft       = 0.30 x 10,368,000                      =  $3,110,400.00
    fees       = 40 x $50,000                           =  $2,000,000.00
    parking    = (40 x 1 = 40 spaces) x $60,000         =  $2,400,000.00
    dev cost   = 10,368,000 + 3,110,400 + 2,000,000 + 2,400,000 = $17,878,400.00
    return     = 0.20 x 17,878,400                      =  $3,575,680.00
    value      = 40 x $650,000                          = $26,000,000.00
    residual   = 26,000,000 - (17,878,400 + 3,575,680)  =  $4,545,920.00
    margin     = 4,545,920 - 3,000,000                  =  $1,545,920.00
    """
    params = mf_params(
        multifamily__units_per_project=40,
        multifamily__unit_size_sqft=900,
        multifamily__hard_cost_per_sqft=320,
        multifamily__soft_cost_share=0.30,
        parking__required_spaces_per_unit_multifamily=1,
        parking__cost_per_space_structured=60000,
        multifamily__developer_margin=0.20,
    )
    context = {"sale_value_per_unit": 650000, "cost_factor": 0.9, "acquisition_price": 3000000}
    result = midrise_proforma(params, context, basis="sale")
    check(
        result.lines,
        {
            "hard_cost": 10368000.00,
            "soft_cost": 3110400.00,
            "fees": 2000000.00,
            "parking_cost": 2400000.00,
            "development_cost": 17878400.00,
            "developer_return": 3575680.00,
            "stabilised_value": 26000000.00,
            "residual_land_value": 4545920.00,
            "land_margin": 1545920.00,
        },
    )
