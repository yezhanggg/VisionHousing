"""Speed checks for the ADU and missing-middle funnels, and a guard that results did not move.

Plain-language summary
----------------------
The ADU and missing-middle funnels were made faster (task F-04). Speed work must
never change a number. Before the change, every P10 / P50 / P90 the engine
produced for a four-reform package (ADU, missing middle, fee cut, parking) was
saved to ``tests/fixtures/perf_golden.json``: for Testland and Pennsylvania,
seeds 0 and 1, with the benchmark ADU scenario and with the HB 2186 and SB 1346
ADU scenarios. These tests rerun the same packages and ask for the same numbers.

Comparison rule: equal within a relative tolerance of 1e-12 (``np.allclose``
with ``rtol=1e-12, atol=0``). The faster code adds the same numbers in the same
order, so in practice the values match exactly; the tolerance only allows for a
last-bit difference in floating-point rounding.

- Testland checks run in ``make test``.
- Pennsylvania checks run only when the PA pack is built (``make data``); they
  are skipped otherwise. They take about a minute.
- One timing test: the Testland package must run in under 2 seconds (a generous
  bound, so CI stays stable). Pennsylvania timings are printed, not asserted.

To rebuild the golden file (only when a change is *meant* to move results)::

    uv run python tests/test_perf_scenarios.py
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from visionhousing import engine
from visionhousing.engine.context import pack_dir

GOLDEN = Path(__file__).parent / "fixtures" / "perf_golden.json"
STATES = ("TL", "PA")
SEEDS = (0, 1)
ADU_KEYS = ("adu", "pa_hb2186", "pa_sb1346")
OTHERS = ("missing_middle", "fees", "parking")
RTOL = 1e-12
TESTLAND_PACKAGE_SECONDS = 2.0


def _case(state: str, seed: int, adu_key: str) -> str:
    return f"{state}|seed={seed}|{adu_key}"


def collect(state: str, seed: int, adu_key: str) -> list[list]:
    """Run one package and return every record as ``[name, p10, p50, p90, value]``."""
    res = engine.run(state, [adu_key, *OTHERS], seed=seed, use_cache=False)
    return [[r.name, r.p10, r.p50, r.p90, r.value] for r in res.records()]


def _has_pack(state: str) -> bool:
    return state == "TL" or (pack_dir(state) / "tracts.csv.gz").is_file()


def _golden() -> dict[str, list[list]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return bool(np.allclose(a, b, rtol=RTOL, atol=0.0))


CASES = [
    pytest.param(state, seed, key, id=_case(state, seed, key))
    for state in STATES
    for seed in SEEDS
    for key in ADU_KEYS
]


@pytest.mark.parametrize(("state", "seed", "adu_key"), CASES)
def test_package_matches_golden(state: str, seed: int, adu_key: str) -> None:
    """Every record of the package equals the saved one (rtol 1e-12)."""
    if not _has_pack(state):
        pytest.skip(f"no {state} pack built")
    want = _golden()[_case(state, seed, adu_key)]
    got = collect(state, seed, adu_key)
    assert [g[0] for g in got] == [w[0] for w in want]
    for g, w in zip(got, want, strict=True):
        for gv, wv in zip(g[1:], w[1:], strict=True):
            assert _same(gv, wv), f"{g[0]}: got {g[1:]}, want {w[1:]}"


def test_testland_package_is_fast() -> None:
    """The four-reform Testland package runs in under two seconds."""
    collect("TL", 0, "adu")  # warm the context cache and imports
    start = time.perf_counter()
    collect("TL", 1, "adu")
    assert time.perf_counter() - start < TESTLAND_PACKAGE_SECONDS


def test_report_pennsylvania_timing(capsys: pytest.CaptureFixture[str]) -> None:
    """Print the Pennsylvania package time (reported, never asserted)."""
    if not _has_pack("PA"):
        pytest.skip("no PA pack built")
    collect("PA", 0, "adu")
    start = time.perf_counter()
    collect("PA", 1, "adu")
    with capsys.disabled():
        print(f"\nPA four-reform package: {time.perf_counter() - start:.2f} s")


def main() -> None:
    """Rebuild ``perf_golden.json`` from the current code."""
    out = {}
    for state in STATES:
        if not _has_pack(state):
            raise SystemExit(f"no {state} pack built; run `make data STATE={state}` first")
        for seed in SEEDS:
            for key in ADU_KEYS:
                out[_case(state, seed, key)] = collect(state, seed, key)
    GOLDEN.write_text(json.dumps(out, indent=0) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
