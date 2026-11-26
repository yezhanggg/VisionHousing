"""The ADU budget-capped program, conserved timing and the hazard on surviving sites.

Acceptance items (update plan §4.1, §5): a fully legal design gives zero pure
permission gain; a fully used budget funds no further award ($2M at $20k caps
funded awards at 100 before administration, not 100 induced ADUs); faster
review alone keeps the project count over the extended horizon and reconciles
units moved into and out of 2027-2031; an all-conversion case gives zero
comparable authorizations. Also: zero uptake, delayed enactment and prolonged
lag fixtures, the grid, and the interface address.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.engine.variants import load_variants
from visionhousing.scenarios import adu_budget as ab
from visionhousing.scenarios.adu import ramp_sum
from visionhousing.scenarios.adu_levers import lever_of

DRAWS = 200
NEW = (
    "adu_hazard_reference",
    "adu_timing_conserved",
    "adu_finance_budget",
    "adu_finance_budget_loan",
)


# ---------------------------------------------------------------- synthetic inputs


def make_inputs(
    *,
    draws: int = 4,
    tracts: int = 3,
    sites: float = 1000.0,
    old_sites: float | None = 500.0,
    hazard: float = 0.02,
    p: float = 0.5,
    p_award: float = 0.7,
    ratio: float = 1.5,
    cash: float = 40_000.0,
    eligible: float = 1.0,
    takeup: float = 1.0,
    admin: float = 0.0,
    delay: float = 0.0,
    median: float = 250.0,
    log_sd: float = 0.9,
    comparable: float = 0.4,
) -> ab.HazardInputs:
    """Small hand-made inputs: every tract alike, every draw alike."""
    years = (2027, 2028, 2029, 2030, 2031)
    span = 15
    shape = (draws, tracts)
    r1, y = np.full(draws, 0.5), np.full(draws, 3.0)
    ramp = ramp_sum(r1, y, span, np.full(draws, delay))
    ramp0 = ramp_sum(r1, y, span, 0.0)
    med, sd = np.full(draws, median), np.full(draws, log_sd)
    k_new, k_old = ab.calibration(ramp0, med, sd, len(years), span)

    def program(award: float, loan_share: float, appropriation) -> ab.Program:
        return ab.Program(
            appropriation=np.broadcast_to(np.asarray(appropriation, float), (draws,)).copy(),
            award=float(award),
            loan_share=float(loan_share),
            p_award=np.full(shape, p_award),
            ratio=np.full(shape, ratio),
            spend_per_award=np.minimum(float(award), np.full(shape, cash)),
            eligible=np.full(draws, eligible),
            takeup=np.full(draws, takeup),
            admin=np.full(draws, admin),
            loss=np.full(draws, 0.05),
            discount=np.full(draws, 0.04),
            rate=np.full(draws, 0.03),
            term=np.full(draws, 20.0),
        )

    return ab.HazardInputs(
        years=years,
        span=span,
        fit_new=np.full(shape, sites),
        fit_old=None if old_sites is None else np.full(shape, old_sites),
        p=np.full(shape, p),
        comparable_share=np.full(shape, comparable),
        hazard=np.full((draws, 1), hazard),
        ramp=ramp,
        kappa_new=k_new,
        kappa_old=k_old,
        kept=np.full((draws, 1), 0.9),
        median=med,
        log_sd=sd,
        counties=np.array(["001"] * tracts),
        values={},
        program=program,
    )


def test_applications_are_remaining_sites_times_the_hazard():
    inp = make_inputs(old_sites=None)
    sim = ab.simulate(inp, law=True, old_pool=False)
    lam = np.clip(inp.kappa_new[:, None] * inp.hazard * inp.ramp, 0, 1)
    remaining = inp.fit_new[:, 0] * inp.p[:, 0] * 3  # three alike tracts
    for t in range(inp.span):
        want = remaining * lam[:, t]
        assert np.allclose(sim.apps[:, t], want)
        remaining = remaining - want
    assert np.allclose(sim.remaining_end, remaining)
    # No site files twice: applications over the span never exceed the sites.
    assert np.all(sim.apps.sum(1) <= sim.sites_start + 1e-9)


def test_no_depletion_reproduces_the_linear_benchmark():
    # With a tiny hazard there is no depletion, and kappa makes in-window permits equal
    # feasible sites x take-up x sum of the ramp (the benchmark convention).
    inp = make_inputs(hazard=1e-6, old_sites=None)
    sim = ab.simulate(inp, law=True, old_pool=False, keep_tracts=True)
    linear = inp.fit_new * inp.p * inp.hazard * inp.ramp[:, :5].sum(1, keepdims=True)
    assert np.allclose(sim.tract_window, linear, rtol=1e-4)
    assert np.allclose(ab.window_total(sim.apps, inp), linear.sum(1), rtol=1e-4)


def test_depletion_lowers_the_window_below_the_linear_total():
    inp = make_inputs(hazard=0.2, old_sites=None)
    sim = ab.simulate(inp, law=True, old_pool=False, keep_tracts=True)
    linear = np.minimum(
        inp.fit_new * inp.p * inp.hazard * inp.ramp[:, :5].sum(1, keepdims=True),
        inp.fit_new * inp.p,
    )
    assert np.all(sim.tract_window < linear)


def test_tract_and_state_window_permits_agree():
    inp = make_inputs()
    prog = inp.program(20_000, 0.0, 1e7)
    sim = ab.simulate(inp, law=True, program=prog, keep_tracts=True)
    assert np.allclose(sim.tract_window.sum(1), ab.window_total(sim.apps, inp))
    by_year = ab.permits_by_year(sim.apps, inp)
    assert np.allclose(by_year[:, :5].sum(1), ab.window_total(sim.apps, inp), rtol=1e-6)


# ---------------------------------------------------------------- the budget


def test_two_million_at_twenty_thousand_caps_awards_at_one_hundred():
    inp = make_inputs(sites=1e6, old_sites=1e6, hazard=0.05)  # demand far above the money
    prog = inp.program(20_000, 0.0, 2_000_000)
    assert np.allclose(prog.capacity_before_admin, 100)
    sim = ab.simulate(inp, law=True, program=prog)
    z = ab.simulate(inp, law=True)
    res = ab.arm_against(inp, sim, z, prog)
    assert np.allclose(res.awards, 100)  # no administration here: exactly the cap
    assert np.allclose(res.spend, 2_000_000)
    assert np.all(res.added < 100)  # awards are not induced ADUs
    assert np.all(res.windfall > 0)  # owners who would have built anyway are paid too
    with_admin = make_inputs(sites=1e6, old_sites=1e6, hazard=0.05, admin=0.1)
    prog = with_admin.program(20_000, 0.0, 2_000_000)
    sim = ab.simulate(with_admin, law=True, program=prog)
    assert np.allclose(sim.awards.sum(1), 90)
    assert np.allclose(prog.admin_cost(sim.spend.sum(1)) + sim.spend.sum(1), 2_000_000)


def test_a_fully_used_budget_funds_no_further_award():
    inp = make_inputs(sites=5e4, old_sites=5e4, hazard=0.05)
    prog = inp.program(20_000, 0.0, 2_000_000)
    sim = ab.simulate(inp, law=True, program=prog)
    spent = np.cumsum(sim.spend, axis=1)
    assert np.all(spent <= 2_000_000 + 1e-6)
    used_up = np.isclose(spent, 2_000_000)
    for d in range(inp.draws):
        first = int(np.argmax(used_up[d]))
        assert used_up[d].any()
        assert np.allclose(sim.awards[d, first + 1 :], 0.0)
    # After the window there is no program at all.
    big = inp.program(20_000, 0.0, 1e12)
    sim = ab.simulate(inp, law=True, program=big)
    assert np.allclose(sim.awards[:, 5:], 0.0) and np.all(sim.awards[:, :5] > 0)


def test_unused_money_is_returned_and_not_a_cost():
    inp = make_inputs(sites=10, old_sites=10)
    prog = inp.program(20_000, 0.0, 1e9)
    sim = ab.simulate(inp, law=True, program=prog)
    res = ab.arm_against(inp, sim, ab.simulate(inp, law=True), prog)
    assert np.allclose(res.unused + res.spend, 1e9)  # no administration
    assert np.allclose(res.net_cost, res.spend)  # grants: cost = what was paid out


def test_loans_cost_less_than_grants_and_are_repaid():
    inp = make_inputs()
    spend = np.full(inp.draws, 1e6)
    grant = inp.program(20_000, 0.0, 1e7)
    loan = inp.program(20_000, 1.0, 1e7)
    assert np.all(loan.net_cost(spend) < grant.net_cost(spend))
    assert np.allclose(loan.repayments(spend), 1e6 * 0.95)
    assert np.allclose(grant.repayments(spend), 0.0)


def test_eligibility_and_award_takeup_scale_who_is_served():
    full = make_inputs()
    half = make_inputs(eligible=0.5)
    for inp in (full, half):
        prog = inp.program(20_000, 0.0, 1e12)
        sim = ab.simulate(inp, law=True, program=prog)
        assert np.allclose(sim.awards.sum(1), sim.demand.sum(1))  # money never runs out
    a = ab.simulate(full, law=True, program=full.program(20_000, 0.0, 1e12)).awards.sum(1)
    b = ab.simulate(half, law=True, program=half.program(20_000, 0.0, 1e12)).awards.sum(1)
    assert np.all(b < a)


def test_zero_uptake_gives_zero_everything():
    inp = make_inputs(hazard=0.0)
    prog = inp.program(20_000, 0.0, 1e7)
    sim = ab.simulate(inp, law=True, program=prog, keep_tracts=True)
    # With no hazard nobody files, with or without an award.
    assert np.allclose(sim.apps, 0.0) and np.allclose(sim.awards, 0.0)
    nobody = make_inputs(takeup=0.0)
    p0 = nobody.program(20_000, 0.0, 1e7)
    res = ab.arm_against(nobody, ab.simulate(nobody, law=True, program=p0),
                         ab.simulate(nobody, law=True), p0)  # fmt: skip
    assert np.allclose(res.added, 0.0) and np.allclose(res.awards, 0.0)
    assert np.allclose(res.net_cost, 0.0)


def test_delayed_enactment_lowers_window_permits_more_than_the_ramp_alone():
    now = make_inputs(old_sites=None, hazard=1e-6)
    late = make_inputs(old_sites=None, hazard=1e-6, delay=1.0)
    a = ab.window_total(ab.simulate(now, law=True, old_pool=False).apps, now)
    b = ab.window_total(ab.simulate(late, law=True, old_pool=False).apps, late)
    linear_late = (late.fit_new * late.p * late.hazard).sum(1) * late.ramp[:, :5].sum(1)
    assert np.all(b < a)
    # kappa is set with no delay, so the delay is not normalised away: late filings
    # pass 2031 and the window falls below the linear total with the same delay.
    assert np.all(b < linear_late)


def test_prolonged_lag_keeps_applications_and_lowers_window_permits():
    inp = make_inputs()
    fast = ab.simulate(inp, law=True, keep_tracts=True)
    slow = ab.simulate(inp, law=True, keep_tracts=True, lag_median=np.full(inp.draws, 600.0))
    assert np.allclose(fast.apps, slow.apps)
    assert np.all(slow.tract_window.sum(1) < fast.tract_window.sum(1))


def test_fully_legal_design_has_zero_pure_permission_gain():
    inp = make_inputs(sites=0.0)  # every lot already legal: nothing newly legal
    z = ab.simulate(inp, law=False)
    law = ab.arm_against(inp, ab.simulate(inp, law=True), z, None)
    assert np.allclose(law.added, 0.0) and np.allclose(law.lifetime, 0.0)
    prog = inp.program(20_000, 0.0, 5e6)
    fin = ab.arm_against(inp, ab.simulate(inp, law=False, program=prog), z, prog)
    both = ab.arm_against(inp, ab.simulate(inp, law=True, program=prog), z, prog)
    assert np.allclose(both.added, fin.added)  # the law adds nothing on top


# ---------------------------------------------------------------- moved in time vs new (D2b)


def _old_moved(inp, arm, ref):
    """D2's first definition: all group-1 applications, no pairing by site group."""
    h = inp.horizon
    return inp.kept[:, 0] * (ref.apps_group1 - arm.apps_group1)[:, h:].sum(1)


def test_law_change_moves_nothing_from_later_years_and_its_new_projects_land_after():
    inp = make_inputs()
    z = ab.simulate(inp, law=False)
    law = ab.simulate(inp, law=True)
    res = ab.arm_against(inp, law, z, None)
    # The old unpaired definition went negative: the law adds post-2031 applications.
    assert np.all(_old_moved(inp, law, z) < 0)
    assert np.allclose(res.moved_from_later, 0.0)
    assert np.all(res.new_after_window > 0)
    # Already-legal sites behave alike in both arms, so every extra project is new:
    # lifetime = added in the window + new projects after it.
    assert np.allclose(res.lifetime, res.added + res.new_after_window)


def test_moved_from_later_is_never_negative_and_new_is_split_off():
    inp = make_inputs()
    z = ab.simulate(inp, law=False)
    for budget in (2e5, 2e6, 2e8):
        prog = inp.program(20_000, 0.0, budget)
        for law in (False, True):
            arm = ab.simulate(inp, law=law, program=prog)
            res = ab.arm_against(inp, arm, z, prog)
            assert np.all(res.moved_from_later >= 0), (budget, law)
            assert np.all(res.new_after_window >= 0), (budget, law)
            assert np.all(res.moved_from_later > 0), (budget, law)  # awards speed owners up
        # Program only: the same already-legal sites in both arms, so the paired
        # count equals the first definition (budget-only cells unchanged).
        fin = ab.simulate(inp, law=False, program=prog)
        assert np.allclose(ab.arm_against(inp, fin, z, prog).moved_from_later,
                           _old_moved(inp, fin, z))  # fmt: skip


def test_program_on_top_of_the_law_pairs_both_pools():
    inp = make_inputs()
    prog = inp.program(20_000, 0.0, 2e6)
    law = ab.simulate(inp, law=True)
    both = ab.simulate(inp, law=True, program=prog)
    own = ab.arm_against(inp, both, law, prog)
    # Every group-1 site exists in both arms: same value as the first definition.
    assert np.allclose(own.moved_from_later, _old_moved(inp, both, law))
    assert np.all(own.moved_from_later > 0)


# ---------------------------------------------------------------- engine runs


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def runs(ctx):
    out = {"bench": run("TL", ["adu"], draws=DRAWS, context=ctx, use_cache=False)}
    for name in NEW:
        out[name] = run("TL", {"adu": {"variant": name}}, draws=DRAWS, context=ctx, use_cache=False)
    return {k: r.reforms["adu"] for k, r in out.items()}


def test_new_variants_exist_and_leave_the_benchmark_alone(runs):
    v = load_variants()
    for name in NEW:
        assert v[name].reform == "adu"
        assert "adu.lever_hazard_method" in v[name].overrides
    assert lever_of(runs["bench"].result) is None
    bench = runs["bench"].state_draws
    for name in NEW:
        lv = lever_of(runs[name].result)
        assert lv is not None and lv.extra["method"] == "hazard"
        assert np.allclose(lv.bench_net, bench)  # same draws as the benchmark
        assert runs[name].added.p50 == pytest.approx(np.percentile(lv.net.sum(1), 50))


def test_hazard_reference_reports_the_method_difference(runs):
    lv = lever_of(runs["adu_hazard_reference"].result)
    assert np.allclose(lv.net.sum(1), lv.extra["reference_net"])
    diff = lv.extra["method_difference"]
    assert np.allclose(diff, lv.extra["reference_net"] - runs["bench"].state_draws)
    assert np.all(diff <= 1e-9)  # using sites up can only lower the window
    rec = lv.records["hazard_method_difference"]
    assert rec.p50 == pytest.approx(np.percentile(diff, 50))
    assert "adu.lever_hazard_method" in rec.assumption_ids


def test_timing_only_conserves_projects_and_reconciles_the_window(runs):
    lv = lever_of(runs["adu_timing_conserved"].result)
    r = lv.records
    assert np.allclose(lv.extra["lifetime_variant"], lv.extra["lifetime_reference"])
    assert r["timing_lifetime_difference"].p10 == r["timing_lifetime_difference"].p90 == 0
    assert r["timing_moved_out_of_window"].p90 == 0
    assert np.all(lv.moved >= -1e-9) and np.allclose(lv.newly, 0.0, atol=1e-6)
    # In the window + after it + still pending = the same projects in both runs.
    for side in ("reference", "variant"):
        assert r[f"timing_after_window_{side}"].p50 >= 0
    d_in = lv.net.sum(1) - lv.extra["reference_net"]
    assert np.allclose(d_in, lv.moved)
    years = [r[f"lever_change_{y}"].p50 for y in range(2027, 2032)]
    assert all(y >= -1e-9 for y in years)


def test_budget_variant_records(runs):
    lv = lever_of(runs["adu_finance_budget"].result)
    r = lv.records
    prog = lv.extra["program"]
    assert r["budget_award_capacity_before_admin"].value == pytest.approx(20e6 / 25_000)
    assert np.all(prog["spend"] <= prog["funds"] + 1e-6)
    assert np.all(prog["windfall"] <= prog["awards"] + 1e-9)
    assert np.allclose(lv.moved + lv.newly, lv.net.sum(1) - lv.extra["reference_net"])
    assert np.allclose(prog["added"], lv.net.sum(1) - lv.extra["reference_net"])
    for key in (
        "budget_applicants_served", "budget_additional_units", "budget_additional_comparable",
        "budget_moved_from_later_years", "budget_net_public_cost", "budget_unused_funds",
        "budget_zero_gain_share", "budget_awards_needed_for_target",
        "budget_uptake_needed_for_target", "budget_appropriation_needed_for_target",
    ):  # fmt: skip
        assert key in r, key
        assert r[key].formula and r[key].assumption_ids, key
    assert r["budget_additional_comparable"].p50 <= r["budget_additional_units"].p50
    # Cost per unit is a per-draw ratio over draws with a gain, never p50 / p50.
    if "budget_cost_per_additional_comparable_unit" in r:
        assert "per draw" in r["budget_cost_per_additional_comparable_unit"].formula
    loan = lever_of(runs["adu_finance_budget_loan"].result).records
    assert "budget_loan_repayments" in loan
    assert loan["budget_net_public_cost"].p50 < r["budget_net_public_cost"].p50


def test_budget_variant_accounting_has_a_comparable_record(runs):
    o = runs["adu_finance_budget"]
    books = o.accounting
    assert books.records["comparable"].p50 <= o.added.p50
    work = o.result.detail.work_types
    assert np.allclose(sum(work.values()), o.state_draws)


def test_budget_with_faster_review_compares_the_program_at_one_lag(ctx):
    """A user override adding faster review to the budget run.

    The program's comparable gain and its cost per comparable unit compare the
    program arm with the no-program arm at the same (pulled) review lag, so the
    review speed-up is never credited to the program; the timing ledger compares
    the program arm with itself at the two lags, so it is computed, not set to 0.
    """
    res = run("TL", {"adu": {"variant": "adu_finance_budget"}},
              overrides={"adu.timing_tail_pull_share": 0.5}, draws=DRAWS, context=ctx,
              use_cache=False).reforms["adu"]  # fmt: skip
    lv = lever_of(res.result)
    assert lv.timing_active and lv.financing_active
    r, prog = lv.records, lv.extra["program"]
    cmp_gain = prog["comparable"]
    rec = r["budget_additional_comparable"]
    for q, k in ((10, "p10"), (50, "p50"), (90, "p90")):
        assert getattr(rec, k) == pytest.approx(np.percentile(cmp_gain, q))
    assert "same review lag in both arms" in rec.formula
    assert np.all(cmp_gain <= prog["added"] + 1e-9)  # a subset of the same-lag program gain
    ok = cmp_gain >= float(ctx.ledger[ab.MIN_GAIN].central)
    assert r["budget_zero_gain_share"].value == pytest.approx(float(np.mean(~ok)))
    if "budget_cost_per_additional_comparable_unit" in r:
        cost = r["budget_cost_per_additional_comparable_unit"]
        ratio = prog["net_cost"][ok] / cmp_gain[ok]
        assert cost.p50 == pytest.approx(np.percentile(ratio, 50))
    life = r["timing_lifetime_difference"]
    assert life.p10 == life.p50 == life.p90 == pytest.approx(0.0)
    assert "same program setting in both" in life.formula
    for side in ("reference", "variant"):
        assert r[f"timing_after_window_{side}"].p50 >= 0
        assert r[f"timing_pending_{side}"].p50 >= 0


def test_fully_legal_state_gives_zero_pure_permission_gain(ctx):
    res = run("TL", {"adu": {"variant": "adu_hazard_reference"}},
              overrides={"adu.already_legal_share": 1.0}, draws=DRAWS, context=ctx,
              use_cache=False).reforms["adu"]  # fmt: skip
    lv = lever_of(res.result)
    assert np.allclose(lv.net, 0.0) and np.allclose(lv.bench_net, 0.0)


def test_all_conversion_case_has_no_comparable_program_units(ctx):
    changes = {
        "adu.detached_form_share_detached_lots": 0,
        "adu.detached_form_share_attached_lots": 0,
        "adu.conversion_share_of_attached_forms": 1,
    }
    res = run("TL", {"adu": {"variant": "adu_finance_budget"}}, overrides=changes,
              draws=DRAWS, context=ctx, use_cache=False).reforms["adu"]  # fmt: skip
    r = lever_of(res.result).records
    assert r["budget_additional_comparable"].p90 == 0
    assert r["budget_additional_units"].p50 > 0
    assert r["budget_zero_gain_share"].value == 1.0
    assert "budget_cost_per_additional_comparable_unit" not in r
    books = res.accounting
    assert books.records["comparable"].p90 == 0
    assert books.records["local_outside_bps"].p50 > 0


def test_hazard_variants_refuse_the_uncapped_financing_lever(ctx):
    with pytest.raises(ValueError, match="uncapped financing"):
        run("TL", {"adu": {"variant": "adu_finance_budget"}},
            overrides={"adu.financing_program_amount": 10_000}, draws=50, context=ctx,
            use_cache=False)  # fmt: skip


# ---------------------------------------------------------------- the grid


@pytest.fixture(scope="module")
def grid(ctx):
    settings = ab.load_grid_settings()
    settings = {**settings, "appropriations": [2_000_000, 20_000_000], "awards": [20_000],
                "targets": [10, 50, 100]}  # fmt: skip
    return ab.budget_grid(ctx, draws=DRAWS, settings=settings)


def test_grid_cells_pair_on_the_same_draws(grid, runs):
    cells = grid["cells"]
    assert {c["arm"] for c in cells} == {"law_only", "finance_only", "both"}
    law = next(c for c in cells if c["arm"] == "law_only")
    lv = lever_of(runs["adu_hazard_reference"].result)
    assert law["added"]["p50"] == pytest.approx(np.percentile(lv.extra["reference_net"], 50))
    assert law["net_public_cost"]["p90"] == 0
    both = [c for c in cells if c["arm"] == "both"]
    fin = [c for c in cells if c["arm"] == "finance_only"]
    for b in both:
        assert b["added"]["p50"] >= law["added"]["p50"] - 1e-9
        assert b["program_added"] is not None
    for c in (*both, *fin):
        for t, m in c["target_met"].items():
            assert 0 <= m["share"] <= 1 and m["mc_se"] >= 0, t
        assert set(c["pareto"]) == {"10", "50", "100"}
    assert grid["settings"]["illustrative"] is True
    assert "method_difference" in grid["reference"]
    for c in cells:
        assert c["moved_from_later"]["p10"] >= 0, c["label"]
        assert c["new_after_window"]["p10"] >= 0, c["label"]
    assert law["moved_from_later"]["p90"] == 0
    assert law["new_after_window"]["p50"] > 0


def test_grid_lookup_never_interpolates(grid):
    missing = ab.grid_lookup(grid)
    assert missing["flags"] == ["input_missing"]
    between = ab.grid_lookup(grid, target=30)
    assert "between_tested_values" in between["flags"] and between["brackets"]["target"] == [10, 50]
    outside = ab.grid_lookup(grid, target=1e6)
    assert "outside_tested_range" in outside["flags"]
    q = ab.grid_lookup(grid, target=10, budget=10_000_000, accept=0.0)
    assert "between_tested_values" in q["flags"]
    assert q["brackets"]["budget"] == [2_000_000, 20_000_000]
    assert "budget_exceeded" in q["flags"]
    assert all(o["appropriation"] <= 10_000_000 for o in q["options"])
    none = ab.grid_lookup(grid, target=100, budget=0, accept=1.0)
    assert "no_tested_option_meets_both" in none["flags"]


def test_grid_settings_are_checked(tmp_path):
    bad = tmp_path / "g.yaml"
    bad.write_text("arms: [law_only]\nappropriations: [2, 1]\nawards: [1]\ntargets: [1]\n")
    with pytest.raises(ValueError, match="increasing"):
        ab.load_grid_settings(bad)


def test_grid_address_answers_with_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
    client = AsgiClient(create_app(packs_dir=tmp_path))
    r = client.get("/api/v1/states/TL/adu-budget-grid?draws=60&target=1000&budget=5000000")
    assert r.status == 200
    body = r.json()
    assert body["grid"]["state"] == "TL" and body["grid"]["draws"] == 60
    assert "budget_exceeded" in body["query"]["flags"]
    bad = client.get("/api/v1/states/TL/adu-budget-grid?accept=2")
    assert bad.status == 400
