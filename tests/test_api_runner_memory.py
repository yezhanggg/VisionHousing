"""Memory safety of the interface's runner: result budget, watchdog, reply-first lookups.

A whole engine result on a real state can hold gigabytes of draws, so the
runner keeps whole results up to an estimated byte budget (not a count), never
keeps one larger than the budget, and a watchdog empties the caches when the
process's resident memory passes a limit. Every scenario GET address looks up
its rendered reply before it asks for the run behind it, and a real state's
scenario GET addresses answer only the default seed and draws.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest

import visionhousing.api.app as app_module
from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api.runner import MemoryWatchdog, Runner, estimate_nbytes, process_rss
from visionhousing.engine.context import load_context

API = "/api/v1"
MB = 1024 * 1024


@dataclass(frozen=True)
class FakeOutcome:
    county_draws: np.ndarray
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeResult:
    """Shaped like an engine result: arrays in fields, mappings and nested outcomes."""

    baseline_draws: np.ndarray
    reforms: dict[str, FakeOutcome] = field(default_factory=dict)
    state: str = "PA"


def fake_result(mb: float) -> FakeResult:
    half = int(mb * MB / 2 / 8)
    return FakeResult(np.zeros(half), {"adu": FakeOutcome(np.zeros(half))})


# ---------------------------------------------------------------- the estimate


def test_estimate_counts_every_array_once_and_skips_shared_state():
    base = np.zeros(1000)  # 8,000 bytes
    view = base[:10]
    table = pd.DataFrame({"a": np.zeros(500)})  # 4,000 bytes of data, plus its index
    value = {"x": base, "y": (view, [base]), "t": table, "label": "text", "n": 3}
    size = estimate_nbytes(value)
    assert 12_000 <= size <= 13_000
    assert estimate_nbytes(fake_result(2)) == pytest.approx(2 * MB, rel=0.01)
    ctx = load_context("TL")
    assert estimate_nbytes({"ctx": ctx, "a": np.zeros(10)}) == 80  # the state's data not counted


def test_process_rss_reads_this_process():
    rss = process_rss()
    assert rss is None or rss > 1 * MB


# ---------------------------------------------------------------- the result budget


def test_result_larger_than_the_budget_is_not_kept():
    runner = Runner(workers=1, timeout=5.0, result_budget=1 * MB)
    runs: list[int] = []

    def job() -> FakeResult:
        runs.append(1)
        return fake_result(2)

    runner.submit("pa-default", job, pin=True)
    assert runner.cached_objects == 0 and runner.cached_object_bytes == 0
    assert runner.stats["too_big"] == 1
    runner.submit("pa-default", job, pin=True)  # not cached: it runs again
    assert len(runs) == 2
    runner.submit("reply", lambda: b"x" * 100)  # its rendered replies are still kept
    assert runner.cached_bytes == 100
    runner.shutdown()


def test_whole_results_are_evicted_by_bytes_least_recently_used_first():
    runner = Runner(workers=1, timeout=5.0, cache_size=10, result_budget=5 * MB)
    for name in ("a", "b", "c"):
        runner.submit(name, lambda: fake_result(2), pin=True)
        assert runner.cached_object_bytes <= 5 * MB
    assert runner.cached_objects == 2  # 3 x 2 MB does not fit in 5 MB
    hits = runner.stats["hits"]
    runner.submit("c", lambda: None, pin=True)
    runner.submit("b", lambda: None, pin=True)
    assert runner.stats["hits"] == hits + 2  # the two newest stay
    assert runner.submit("a", lambda: "rerun", pin=True) == "rerun"  # the oldest went
    runner.shutdown()


# ---------------------------------------------------------------- the watchdog


def test_watchdog_empties_the_caches_past_the_limit():
    runner = Runner(workers=1, timeout=5.0)
    runner.submit("result", lambda: fake_result(1), pin=True)
    runner.submit("reply", lambda: b"x" * 1000)
    memory = [100 * MB]
    dog = MemoryWatchdog(runner, limit=500 * MB, every=0.01, rss=lambda: memory[0])
    assert not dog.check() and runner.cached_objects == 1  # under the limit: nothing happens
    memory[0] = 900 * MB
    assert dog.check()
    assert runner.cached_objects == 0 and runner.cached_bytes == 0
    assert runner.cached_object_bytes == 0 and runner.stats["evictions"] == 1
    runner.shutdown()


def test_watchdog_skips_empty_caches_rearms_below_90_percent_and_backs_off():
    runner = Runner(workers=1, timeout=5.0)
    memory, now = [900 * MB], [0.0]
    dog = MemoryWatchdog(runner, limit=500 * MB, rss=lambda: memory[0], rearm=0.9,
                         backoff=60.0, backoff_max=200.0, clock=lambda: now[0])  # fmt: skip
    assert not dog.check() and dog.evictions == 0  # over the limit, but nothing to free

    def fill():
        runner.put("reply", b"x" * 1000)

    fill()
    assert dog.check() and not dog.armed and dog.evictions == 1
    fill()
    now[0] = 30.0
    assert not dog.check() and runner.cached_bytes == 1000  # disarmed, backing off
    now[0] = 61.0
    assert dog.check() and dog.evictions == 2  # back-off over: clears; wait doubles
    fill()
    now[0] = 61.0 + 100.0
    assert not dog.check()  # 120 s now
    now[0] = 61.0 + 121.0
    assert dog.check() and dog._wait == 200.0  # capped
    # Memory between 90% and 100% of the limit does not re-arm it ...
    memory[0] = 460 * MB
    assert not dog.check() and not dog.armed
    # ... below 90% does, and the back-off starts again.
    memory[0] = 400 * MB
    assert not dog.check() and dog.armed and dog._wait == 60.0
    fill()
    memory[0] = 900 * MB
    assert dog.check() and dog.evictions == 4
    runner.shutdown()


def test_estimate_skips_views_of_the_state_data_and_runs_outside_the_lock(monkeypatch):
    from visionhousing.api import runner as runner_module
    from visionhousing.api.runner import ContextStore

    store = ContextStore(packs_dir=__import__("pathlib").Path("."))
    ctx = store.get("TL")
    column = next(a for a in runner_module._context_arrays(ctx) if a.nbytes > 0)
    view = column[: max(1, len(column) // 2)]
    own = np.zeros(100)
    assert estimate_nbytes({"view": view, "own": own}) == own.nbytes
    # The runner estimates a finished result without holding its lock.
    runner = Runner(workers=1, timeout=5.0)
    held = []
    real = runner_module.estimate_nbytes

    def spy(value, **kw):
        held.append(runner._lock._is_owned())
        return real(value, **kw)

    monkeypatch.setattr(runner_module, "estimate_nbytes", spy)
    runner.submit("r", lambda: fake_result(1), pin=True)
    runner.put("p", fake_result(1), pin=True)
    assert held and not any(held)
    runner.shutdown()


def test_context_store_loads_a_state_once_for_concurrent_first_requests(monkeypatch):
    from pathlib import Path

    from visionhousing.api import runner as runner_module
    from visionhousing.api.runner import ContextStore

    calls = []
    gate = threading.Event()
    real = runner_module.load_context

    def slow(code, folder=None):
        calls.append(code)
        gate.wait(5)
        return real(code, folder=folder)

    monkeypatch.setattr(runner_module, "load_context", slow)
    store = ContextStore(packs_dir=Path("."))
    got = []
    threads = [threading.Thread(target=lambda: got.append(store.get("TL"))) for _ in range(4)]
    for t in threads:
        t.start()
    threading.Event().wait(0.2)
    gate.set()
    for t in threads:
        t.join(10)
    assert calls == ["TL"] and len(got) == 4 and all(g is got[0] for g in got)
    # A failed load is not cached: the next request tries again.
    monkeypatch.setattr(runner_module, "load_context", lambda code, folder=None: 1 / 0)
    store2 = ContextStore(packs_dir=Path("."))
    with pytest.raises(ZeroDivisionError):
        store2.get("TL")
    assert store2._loading == {}


def test_watchdog_thread_checks_in_the_background():
    runner = Runner(workers=1, timeout=5.0)
    runner.submit("result", lambda: fake_result(1), pin=True)
    checked = threading.Event()

    def rss() -> int:
        checked.set()
        return 10_000 * MB

    dog = MemoryWatchdog(runner, limit=1 * MB, every=0.01, rss=rss)
    dog.start()
    try:
        assert checked.wait(5)
        for _ in range(500):
            if dog.evictions:
                break
            threading.Event().wait(0.01)
    finally:
        dog.stop()
    assert dog.evictions >= 1 and runner.cached_objects == 0
    MemoryWatchdog(runner, limit=0).start()  # a limit of 0 switches it off: no thread
    runner.shutdown()


# ---------------------------------------------------------------- through the app


@pytest.fixture()
def counted_runs(monkeypatch):
    calls: list[tuple] = []
    real = app_module.run

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "run", counting)
    return calls


@pytest.mark.parametrize(
    "path",
    [
        "/states/TL/map?r=adu&draws=60",
        "/states/TL/tracts?r=adu&draws=60",
        "/states/TL/tract-funnel?r=adu&draws=60",
        "/states/TL/counties/{fips}?r=adu&draws=60",
        "/export/csv?s=TL&r=adu&draws=60",
        "/export/json?s=TL&r=adu&draws=60",
        "/states/TL/permit-years",
    ],
)
def test_cached_reply_is_served_without_the_engine_run(tmp_path, counted_runs, path):
    app = create_app(packs_dir=tmp_path)
    runner = app.fastapi.state.runner
    client = AsgiClient(app)
    fips = load_context("TL").tracts["county"].astype(str).iloc[0]
    path = path.format(fips=fips)
    first = client.get(f"{API}{path}")
    assert first.status == 200, first.text
    assert len(counted_runs) == 1
    runner._objects.clear()  # the whole result is gone (budget or watchdog) ...
    runner._objects_held = 0
    again = client.get(f"{API}{path}")
    assert again.status == 200 and again.body == first.body
    assert len(counted_runs) == 1  # ... but the reply came from its cache: no new run
    runner.shutdown()


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    root = tmp_path_factory.mktemp("packs")
    (root / "PA").mkdir()
    (root / "PA" / "tracts.csv").write_text("geoid,county\n")
    return root


REAL_STATE_GETS = [
    "/states/PA/map?r=adu",
    "/states/PA/tracts?r=adu",
    "/states/PA/tract-funnel?r=adu",
    "/states/PA/counties/42001?r=adu",
    "/export/csv?s=PA&r=adu",
    "/export/json?s=PA&r=adu",
    "/export/html?s=PA&r=adu",
]


@pytest.mark.parametrize("path", REAL_STATE_GETS)
def test_real_state_scenario_get_refuses_other_sampling(packs, path):
    app = create_app(packs_dir=packs)
    runner = app.fastapi.state.runner
    client = AsgiClient(app)
    for extra in ("&seed=3", "&draws=500"):
        r = client.get(f"{API}{path}{extra}")
        assert r.status == 400, r.text
        assert r.json()["error"] == "default_sampling_only"
        assert "POST /api/v1/run" in r.json()["message"]
    assert runner.stats["misses"] == 0 and runner.in_flight == 0
    runner.shutdown()


def test_static_exporter_flag_lets_any_sampling_through(packs):
    app = create_app(packs_dir=packs, any_sampling=True)
    client = AsgiClient(app)
    r = client.get(f"{API}/states/PA/map?r=adu&seed=3")
    assert r.json().get("error") != "default_sampling_only"
    app.fastapi.state.runner.shutdown()


def test_testland_get_still_takes_any_seed(tmp_path):
    client = AsgiClient(create_app(packs_dir=tmp_path))
    r = client.get(f"{API}/states/TL/map?r=adu&seed=7&draws=40")
    assert r.status == 200, r.text
