"""Calibration transfer: a state without its own measurement borrows a range, widened."""

import numpy as np

from visionhousing.engine.context import load_transfer_rules, transfer_overrides
from visionhousing.ledger import apply_overrides, load_ledger
from visionhousing.scenarios.adu import FIT_PARAM, load_fit_table


def test_rules_name_ledger_entries_and_widen_them():
    ledger = load_ledger()
    entries = dict(ledger.items())
    rules = load_transfer_rules()
    assert rules
    for rule in rules:
        old = entries[rule["parameter"]]
        assert rule["low"] <= old.low and rule["high"] >= old.high  # never narrower
        assert rule["measured_in"]


def test_only_unmeasured_real_states_are_widened():
    ledger = load_ledger()
    assert transfer_overrides("PA", ledger) == {}  # measured there
    assert transfer_overrides("TL", ledger) == {}  # synthetic: built to match
    vt = transfer_overrides("vt", ledger)
    assert FIT_PARAM in vt
    widened = dict(apply_overrides(ledger, vt).items())[FIT_PARAM]
    assert (widened.low, widened.high) == (-1.5, 1.5)
    assert "Borrowed from PA" in widened.rationale and "VT" in widened.rationale


def test_widened_fit_shares_stay_between_zero_and_one():
    fit = load_fit_table()
    area = np.array(["urban", "suburban", "rural"])
    shares = fit.shares("sf_detached", area, np.array([-1.5, -1.0, 0.0, 1.0, 1.5]))
    finite = shares[np.isfinite(shares)]
    assert finite.size and (finite >= 0).all() and (finite <= 1).all()
    # Past the measured end the share keeps moving the same way (or stops at a bound).
    assert (shares[0] <= shares[1] + 1e-12)[np.isfinite(shares[0])].all()
    assert (shares[4] >= shares[3] - 1e-12)[np.isfinite(shares[4])].all()
