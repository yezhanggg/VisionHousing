"""Local law in the engine: county ADU shares and parking reach from verified rules (Testland)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.context import context_from_tables, load_context, local_law_override
from visionhousing.engine.core import compute
from visionhousing.law import hooks
from visionhousing.scenarios.adu import LEGAL_PARAM, adu_funnel, scope_from_scenario

DRAWS = 300
HOOKS = {
    "engine_wired": True,
    "wired": {"adu": True, "parking": True, "fees": False},
    "min_coverage": 0.5,
    "adu_legal_statuses": ["by_right"],
    "source_id": "pa_municipal_ordinances",
}

# Harbor County (99001): 10,000 residents, 7,000 read. Of those, 4,000 live where ADUs are
# allowed by right and 3,000 where the county ordinance does not address them and a verified
# exclusivity clause bars unlisted uses (so the omission is a "no"). Maple County (99003) has
# one small read municipality: below the 50% bar, so it keeps the statewide range.
MUNIS = pd.DataFrame(
    [
        {"county": "99001", "cousub": "00100", "population": "4000"},
        {"county": "99001", "cousub": "00200", "population": "3000"},
        {"county": "99001", "cousub": "00300", "population": "3000"},
        {"county": "99003", "cousub": "00400", "population": "1000"},
        {"county": "99003", "cousub": "00500", "population": "9000"},
    ]
)


def _rule(geoid, name, adu, doc, park, status="verified", unlisted=""):
    return {
        "unlisted_uses": unlisted,
        "unlisted_uses_cite": "§ 2" if unlisted else "",
        "geoid": geoid,
        "name": name,
        "adu_status": adu,
        "doc_id": doc,
        "section_cite": "§ 1",
        "reviewed_on": "2026-11-23",
        "parking_min_per_unit_sf": park,
        "parking_min_per_unit_mf": park,
        "status": status,
        "adopted_on": "2019-05-01",
    }


RULES = pd.DataFrame(
    [
        _rule("9900100100", "Harbor city", "by_right", "harbor_city_zo", "1"),
        _rule(
            "9900100200",
            "Cove township",
            "not_addressed",
            "harbor_county_zo",
            "2",
            unlisted="prohibited",
        ),
        _rule("9900100300", "Reef township", "unknown", "", "", status="draft"),
        _rule("9900300400", "Elm borough", "by_right", "elm_zo", "0"),
    ]
)


@pytest.fixture(scope="module")
def law():
    return hooks.local_law_for_engine(RULES, MUNIS, HOOKS)


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


def test_coefficients_only_for_counties_over_the_bar(law):
    coef = law.adu_coefficients()
    assert set(coef) == {"99001"}
    a, b = coef["99001"]
    assert a == pytest.approx(0.4) and b == pytest.approx(0.3)
    # the same as the hook's own low/central/high, for any statewide share
    shares = hooks.county_adu_legal_share(RULES, MUNIS, (0.1, 0.2, 0.3), HOOKS).set_index("county")
    assert shares.loc["99001", "central"] == pytest.approx(a + b * 0.2)
    assert shares.loc["99003", "source"] == "ledger"


def test_not_addressed_is_not_counted_as_legal(law):
    a, _ = law.adu_coefficients()["99001"]
    assert a == pytest.approx(4000 / 10000)  # Cove (not_addressed, exclusive) adds nothing


def test_not_addressed_without_an_exclusivity_clause_stays_unknown():
    # no clause on unlisted uses read: Cove does not answer the ADU question
    rules = _harbor(unlisted_uses="", unlisted_uses_cite="")
    assert hooks.county_adu_coefficients(rules, MUNIS, HOOKS, as_of="2026-11-24").empty
    shares = hooks.county_adu_legal_share(
        rules, MUNIS, (0.1, 0.2, 0.3), {**HOOKS, "min_coverage": 0.4}, as_of="2026-11-24"
    ).set_index("county")
    # Cove's 3,000 residents take the statewide share, like Reef's: unknown, not "no"
    assert shares.loc["99001", "central"] == pytest.approx((4000 + 6000 * 0.2) / 10000)
    # a hearing route makes it a read, discretionary answer (still not legal)
    route = _harbor(unlisted_uses="special_exception")
    a, b = hooks.local_law_for_engine(route, MUNIS, HOOKS).adu_coefficients()["99001"]
    assert a == pytest.approx(0.4) and b == pytest.approx(0.3)


def test_parking_factor_counts_only_read_zero_minimums():
    rules = RULES.copy()
    rules.loc[
        rules["geoid"] == "9900100100", ["parking_min_per_unit_sf", "parking_min_per_unit_mf"]
    ] = "0"
    f = hooks.county_parking_factor(rules, MUNIS, HOOKS).set_index("county")
    assert f.loc["99001", "factor"] == pytest.approx(1 - 4000 / 10000)
    assert "99003" not in f.index


def test_notes_cite_the_readings(law):
    note = law.adu_note("99001")
    assert "read local law" in note and "harbor_city_zo" in note and "harbor_county_zo" in note
    assert law.adu_note("99003") is None


def test_hooks_off_or_no_tables_give_nothing():
    assert hooks.local_law_for_engine(RULES, MUNIS, {**HOOKS, "engine_wired": False}) is None
    assert hooks.local_law_for_engine(None, MUNIS, HOOKS) is None


def test_shipped_hooks_wire_adu_and_parking_not_fees():
    h = hooks.load_hooks()
    assert h["engine_wired"] is True
    assert h["wired"] == {"adu": True, "parking": True, "fees": False}


def test_funnel_uses_county_share_and_leaves_other_counties(ctx):
    scope = scope_from_scenario("adu", ctx.scenarios["adu"], ctx.legal)
    plain = adu_funnel(ctx.tracts, ctx.ledger, ctx.fit, scope, draws=DRAWS, seed=1)
    local = adu_funnel(
        ctx.tracts,
        ctx.ledger,
        ctx.fit,
        scope,
        draws=DRAWS,
        seed=1,
        local_legal={"99001": (1.0, 0.0)},
    )
    harbor = ctx.tracts["county"].astype(str).to_numpy() == "99001"
    assert np.all(local.stages["eligible"][:, harbor] == 0)
    assert np.allclose(local.stages["eligible"][:, ~harbor], plain.stages["eligible"][:, ~harbor])
    # a = 0, b = 1 is the statewide share itself
    same = adu_funnel(
        ctx.tracts,
        ctx.ledger,
        ctx.fit,
        scope,
        draws=DRAWS,
        seed=1,
        local_legal={"99001": (0.0, 1.0)},
    )
    assert np.allclose(same.stages["net"], plain.stages["net"])


def test_engine_cites_local_law_in_the_county_record(ctx, law):
    local = context_from_tables(
        "TL",
        tracts=ctx.tracts,
        permits=ctx.permits,
        blocks=ctx.blocks,
        stops=ctx.stops,
        boundaries=ctx.boundaries,
        ledger=ctx.ledger,
        scenarios=ctx.scenarios,
        fit=ctx.fit,
        legal=ctx.legal,
        data_hash=ctx.data_hash,
        calibration_hash=ctx.calibration_hash,
        local_law=law,
    )
    assert "read local law" in local.ledger[LEGAL_PARAM].rationale
    res = compute(local, ["adu", "parking"], seed=2, draws=DRAWS)
    harbor = res.reforms["adu"].counties["99001"]
    assert "read local law" in harbor.formula and "harbor_city_zo" in harbor.formula
    assert "pa_municipal_ordinances" in harbor.source_ids
    maple = res.reforms["adu"].counties["99003"]
    assert "read local law" not in maple.formula
    base = compute(ctx, ["adu"], seed=2, draws=DRAWS)
    # Harbor's share rises from s to 0.4 + 0.3 s, so fewer ADUs are added there
    assert harbor.p50 < base.reforms["adu"].counties["99001"].p50
    assert maple.p50 == pytest.approx(base.reforms["adu"].counties["99003"].p50)
    assert "parking reach" in res.reforms["parking"].counties["99001"].formula


def test_parking_reach_scales_the_county(ctx):
    rules = RULES.copy()
    rules["parking_min_per_unit_sf"] = rules["parking_min_per_unit_mf"] = "0"
    law = hooks.local_law_for_engine(rules, MUNIS, HOOKS)
    on = compute(replace(ctx, local_law=law), ["parking"], seed=5, draws=DRAWS)
    off = compute(ctx, ["parking"], seed=5, draws=DRAWS)
    f = law.parking_factors()["99001"]
    assert f == pytest.approx(0.3)
    assert on.reforms["parking"].counties["99001"].p50 == pytest.approx(
        f * off.reforms["parking"].counties["99001"].p50
    )
    assert on.reforms["parking"].counties["99003"].p50 == pytest.approx(
        off.reforms["parking"].counties["99003"].p50
    )


def test_override_says_which_counties(ctx, law):
    change = local_law_override(ctx.ledger, law)
    assert "99001" in change[LEGAL_PARAM]["rationale"]
    assert local_law_override(ctx.ledger, None) == {}


# --------------------------------------------------------------------------- what counts as read


def _harbor(**cove):
    """Harbor County with Cove township's row changed (Harbor city by right, Reef unread)."""
    rules = RULES.copy()
    for k, v in cove.items():
        rules.loc[rules["geoid"] == "9900100200", k] = v
    return rules


def test_unknown_adu_status_counts_as_unread_not_as_not_legal():
    from datetime import date

    for blank in ("unknown", ""):
        rules = _harbor(adu_status=blank)
        # Harbor city alone answers the ADU question: 4,000 of 10,000 residents < 50% bar
        coef = hooks.county_adu_coefficients(rules, MUNIS, HOOKS, as_of=date(2026, 11, 24))
        assert coef.empty, blank
        shares = hooks.county_adu_legal_share(rules, MUNIS, (0.1, 0.2, 0.3), {**HOOKS,
                                              "min_coverage": 0.4}, as_of="2026-11-24")  # fmt: skip
        h = shares.set_index("county").loc["99001"]
        assert h["coverage"] == pytest.approx(0.4)
        # Cove's 3,000 residents are unread (statewide share), not counted as "not legal"
        assert h["central"] == pytest.approx((4000 + 6000 * 0.2) / 10000)
    # the parking hook still reads Cove's parking answer
    f = hooks.county_parking_factor(_harbor(adu_status="unknown"), MUNIS, HOOKS, as_of="2026-11-24")
    assert set(f["county"]) == {"99001"}


def test_a_rule_counts_only_once_in_force_on_the_runs_as_of_date():
    rules = _harbor(effective_date="2027-01-01")
    before = hooks.county_adu_coefficients(rules, MUNIS, HOOKS, as_of="2026-11-24")
    assert before.empty  # Cove not law yet: 4,000 read < 50%
    after = hooks.county_adu_coefficients(rules, MUNIS, HOOKS, as_of="2027-06-30")
    assert after.set_index("county").loc["99001", "b"] == pytest.approx(0.3)
    # a partial date counts from its last possible day
    rules = _harbor(effective_date="2026-12")
    assert hooks.county_adu_coefficients(rules, MUNIS, HOOKS, as_of="2026-11-30").empty
    assert not hooks.county_adu_coefficients(rules, MUNIS, HOOKS, as_of="2026-12-31").empty


def test_adoption_never_stands_in_for_the_effective_date():
    import numpy as np

    # no effective date: a rule counts from the day a person saw it in force (reviewed_on
    # 2026-11-23), not from its adoption, and never before that day
    known = _harbor(adopted_on="2015")
    assert hooks.county_adu_coefficients(known, MUNIS, HOOKS, as_of="2026-11-22").empty
    assert not hooks.county_adu_coefficients(known, MUNIS, HOOKS, as_of="2026-11-24").empty
    no_dates = _harbor(adopted_on=np.nan)
    assert not hooks.county_adu_coefficients(no_dates, MUNIS, HOOKS, as_of="2026-11-24").empty
    never_seen = _harbor(adopted_on="2015", reviewed_on=np.nan)
    assert hooks.county_adu_coefficients(never_seen, MUNIS, HOOKS, as_of="2026-11-24").empty
    # an adoption after the as-of date still rules the rule out
    late = _harbor(adopted_on="2027-03-01")
    assert hooks.county_adu_coefficients(late, MUNIS, HOOKS, as_of="2026-11-24").empty
    # a known effective date always decides, whatever the adoption or review date
    eff = _harbor(adopted_on="2015", effective_date="2026-12-01")
    assert hooks.county_adu_coefficients(eff, MUNIS, HOOKS, as_of="2026-11-24").empty
    strict = {**HOOKS, "unknown_effective": "unread"}
    assert hooks.county_adu_coefficients(known, MUNIS, strict, as_of="2026-11-24").empty


def test_partial_dates_and_the_default_as_of_is_the_runs():
    from datetime import date

    assert hooks.last_day("2019") == date(2019, 12, 31)
    assert hooks.last_day("2024-02") == date(2024, 2, 29)
    assert hooks.last_day("2024-12") == date(2024, 12, 31)
    assert hooks.last_day("") is None and hooks.last_day(None) is None
    from visionhousing.engine.outcome import load_rules

    assert hooks.run_as_of() == date.fromisoformat(str(load_rules()["as_of"]))
    shipped = hooks.load_hooks()
    assert shipped["unknown_effective"] == "read_from_reviewed_on"
    assert shipped["adu_status_unknown"] == "treat_as_unread"
