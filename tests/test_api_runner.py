"""Load safety of the interface's runner: bounded queue, timeouts, byte-budget cache, stamps.

The interface runs the engine on a small thread pool (``api/runner.py``). These
tests check that a stream of distinct requests (40 different seeds) cannot grow
the queue or the cache without bound: extra jobs are refused with 503 and a
``Retry-After``, GET addresses of a real state refuse another seed with 400,
queued jobs whose caller gave up never run, results of abandoned custom jobs
are not kept, rendered replies stay inside the byte budget, and a rebuilt
district table is never served from an old reply.
"""

from __future__ import annotations

import os
import shutil
import threading
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api import districts as districts_api
from visionhousing.api.runner import (
    Runner,
    RunnerBusyError,
    RunTimeoutError,
    is_default_scenario,
    require_default_sampling,
    stamp,
)
from visionhousing.engine import run
from visionhousing.engine.context import TESTLAND_DIR, load_context
from visionhousing.ledger import DEFAULT_DRAWS

API = "/api/v1"
SEEDS = range(1, 41)


def _blocker(gate: threading.Event, ran: list[str], name: str):
    def job() -> str:
        ran.append(name)
        gate.wait(10)
        return name

    return job


def _wait_until(check, seconds: float = 5.0) -> None:
    end = time.monotonic() + seconds
    while not check() and time.monotonic() < end:
        time.sleep(0.005)


# ---------------------------------------------------------------- the runner alone


def test_queue_is_bounded_and_extra_jobs_are_refused():
    runner = Runner(workers=2, timeout=5.0, queue=2, retry_after=7)
    gate, ran = threading.Event(), []
    outcomes: list[str] = []
    peak = [0]

    def caller(seed: int) -> None:
        try:
            outcomes.append(runner.submit(f"run-{seed}", _blocker(gate, ran, str(seed))))
        except RunnerBusyError as exc:
            assert exc.retry_after == 7 and "busy" in str(exc)
            outcomes.append("busy")
        peak[0] = max(peak[0], runner.in_flight)

    threads = [threading.Thread(target=caller, args=(seed,)) for seed in SEEDS]
    for t in threads:
        t.start()
    _wait_until(lambda: runner.stats["busy"] == len(SEEDS) - 4)
    assert runner.in_flight == 4  # two running, two waiting: never more
    gate.set()
    for t in threads:
        t.join(10)
    runner.pool.shutdown(wait=True)
    assert outcomes.count("busy") == len(SEEDS) - 4 and peak[0] <= 4
    assert len(ran) == 4 and runner.in_flight == 0
    assert runner.cached_objects == 1  # four custom results, one place for them


def test_queued_job_is_cancelled_when_its_caller_times_out():
    runner = Runner(workers=1, timeout=0.05, queue=4)
    gate, ran = threading.Event(), []
    try:
        with pytest.raises(RunTimeoutError):
            runner.submit("busy", _blocker(gate, ran, "busy"))
        with pytest.raises(RunTimeoutError):
            runner.submit("queued", _blocker(gate, ran, "queued"))
        assert runner.stats["cancelled"] == 1
    finally:
        gate.set()
        runner.pool.shutdown(wait=True)
    assert ran == ["busy"]  # the queued job never started
    assert runner.cached_objects == 0 and runner.in_flight == 0


def test_abandoned_custom_result_is_dropped_but_default_one_kept():
    runner = Runner(workers=2, timeout=0.05, queue=2)
    gate, ran = threading.Event(), []
    with pytest.raises(RunTimeoutError):
        runner.submit("custom", _blocker(gate, ran, "custom"))
    with pytest.raises(RunTimeoutError):
        runner.submit("default", _blocker(gate, ran, "default"), pin=True)
    gate.set()
    _wait_until(lambda: runner.in_flight == 0)
    assert runner.stats["dropped"] == 1
    assert runner.submit("default", lambda: "again") == "default"  # kept
    assert runner.submit("custom", lambda: "fresh") == "fresh"  # run again
    runner.shutdown()


def test_asking_again_while_running_picks_the_job_up():
    runner = Runner(workers=1, timeout=0.05, queue=1)
    gate, ran = threading.Event(), []
    with pytest.raises(RunTimeoutError):
        runner.submit("job", _blocker(gate, ran, "job"))
    _wait_until(lambda: ran == ["job"])
    runner.timeout = 5.0
    threading.Timer(0.05, gate.set).start()
    assert runner.submit("job", lambda: "other") == "job"  # shared, not rerun
    runner.pool.shutdown(wait=True)
    assert ran == ["job"] and runner.submit("job", lambda: "other") == "job"


def test_rendered_bytes_stay_inside_the_byte_budget():
    runner = Runner(workers=1, timeout=5.0, byte_budget=10_000)
    for seed in SEEDS:
        runner.submit(f"json-{seed}", lambda: b"x" * 1_000)
        assert runner.cached_bytes <= 10_000
    assert runner.cached_bytes == 10_000
    runner.submit("huge", lambda: b"y" * 20_000)  # larger than the budget: not kept
    assert runner.cached_bytes == 10_000
    runner.shutdown()


def test_whole_results_are_few_and_custom_ones_fewer():
    runner = Runner(workers=1, timeout=5.0, cache_size=3, custom_slots=1)
    for seed in SEEDS:
        runner.submit(f"custom-{seed}", lambda s=seed: {"seed": s})
        assert runner.cached_objects <= 1
    for name in ("a", "b", "c", "d"):
        runner.submit(f"default-{name}", lambda n=name: {"name": n}, pin=True)
        assert runner.cached_objects <= 3
    runner.submit("custom-x", lambda: {"seed": "x"})
    assert runner.cached_objects == 3
    hits = runner.stats["hits"]
    assert runner.submit("default-d", lambda: None) == {"name": "d"}
    assert runner.stats["hits"] == hits + 1
    runner.shutdown()


def test_default_scenario_and_sampling_rule():
    class Req:
        def __init__(self, state, seed=0, draws=DEFAULT_DRAWS, overrides=None):
            self.state, self.seed, self.draws, self.overrides = state, seed, draws, overrides or {}

    assert is_default_scenario(Req("PA"))
    assert not is_default_scenario(Req("PA", seed=3))
    assert not is_default_scenario(Req("PA", overrides={"fees.passthrough_k": "h"}))
    assert require_default_sampling(Req("PA")) is None
    assert require_default_sampling(Req("TL", seed=5, draws=100)) is None
    assert "POST /api/v1/run" in require_default_sampling(Req("PA", seed=5))
    assert "default seed" in require_default_sampling(Req("PA", draws=100))


# ---------------------------------------------------------------- through the app


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    root = tmp_path_factory.mktemp("packs")
    (root / "PA").mkdir()
    (root / "PA" / "tracts.csv").write_text("geoid,county\n")
    return root


@pytest.mark.parametrize(
    "path",
    [
        "/states/PA/districts?r=adu",
        "/grades/PA?r=adu",
        "/trace/PA?r=adu&id=state",
        "/switch/PA?r=adu",
        "/plausibility/PA?r=adu",
    ],
)
def test_real_state_get_refuses_forty_custom_seeds(packs, path):
    app = create_app(packs_dir=packs)
    client = AsgiClient(app)
    runner = app.fastapi.state.runner
    for seed in SEEDS:
        r = client.get(f"{API}{path}&seed={seed}")
        assert r.status == 400, (seed, r.text)
        assert r.json()["error"] == "default_sampling_only"
    r = client.get(f"{API}{path}&draws=4000")
    assert r.status == 400 and "POST /api/v1/run" in r.json()["message"]
    assert runner.stats["misses"] == 0 and runner.in_flight == 0
    assert runner.cached_objects == 0 and runner.cached_bytes == 0
    runner.shutdown()


def test_forty_seeds_on_a_full_queue_get_503_with_retry_after(packs):
    app = create_app(packs_dir=packs, workers=1)
    runner = app.fastapi.state.runner
    runner.queue = 1
    gate = threading.Event()
    blockers = [runner.pool.submit(gate.wait, 10) for _ in range(2)]
    runner._running.update({"block-1": blockers[0], "block-2": blockers[1]})
    client = AsgiClient(app)
    try:
        for seed in SEEDS:
            r = client.post(f"{API}/run", {"state": "TL", "reforms": ["adu"], "draws": 50,
                                           "seed": seed, "explain": False})  # fmt: skip
            assert r.status == 503, r.text
            assert r.json()["error"] == "busy"
            assert int(r.headers["retry-after"]) == runner.retry_after
            assert runner.in_flight == 2
    finally:
        gate.set()
        runner._running.clear()
        runner.pool.shutdown(wait=True)
    assert runner.stats["busy"] == len(SEEDS) and runner.stats["misses"] == 0
    assert runner.cached_objects == 0


def test_forty_testland_seeds_keep_the_cache_bounded(packs):
    app = create_app(packs_dir=packs)
    runner = app.fastapi.state.runner
    runner.byte_budget = 200_000
    client = AsgiClient(app)
    for seed in range(1, 41):
        r = client.get(f"{API}/plausibility/TL?r=adu&draws=20&seed={seed}")
        assert r.status == 200, r.text
        assert runner.cached_objects <= runner.custom_slots
        assert runner.cached_bytes <= 200_000
    assert runner.stats["misses"] >= 80  # one run and one reply per seed
    runner.shutdown()


# ---------------------------------------------------------------- stamps in cache keys


def test_district_reply_is_rebuilt_when_the_table_changes(tmp_path):
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    ctx = load_context("TL")
    runner = Runner(workers=1, timeout=30.0)
    renders: list[str] = []

    def execute(req):
        key = Runner.key("run", req.seed, req.draws, req.reform_arg())
        return key, runner.submit(key, lambda: run("TL", req.reform_arg(), seed=req.seed,
                                                    draws=req.draws, context=ctx))  # fmt: skip

    def timed(key, job):
        def counted():
            renders.append(key)
            return job()

        return runner.submit(key, counted)

    class StubError(Exception):
        def __init__(self, status, code, message):
            super().__init__(message)
            self.status, self.code = status, code

    app = FastAPI()

    @app.exception_handler(StubError)
    async def _err(_req: Request, exc: StubError) -> JSONResponse:
        return JSONResponse({"error": exc.code}, exc.status)

    districts_api.register(app, api=API, error=StubError, context=lambda st: ctx, execute=execute,
                           timed=timed, folder=lambda st: folder)  # fmt: skip
    client = AsgiClient(app)
    url = f"{API}/states/TL/districts?r=adu&draws=50"
    assert client.get(url).status == 200
    assert client.get(url).status == 200
    assert len(renders) == 1  # second answer from the cache
    probe = folder / "blocks.csv"
    later = probe.stat().st_mtime_ns + 5_000_000_000
    os.utime(probe, ns=(later, later))
    assert client.get(url).status == 200
    assert len(renders) == 2  # the rebuilt table gave a new key
    runner.shutdown()


def test_stamp_changes_when_a_file_changes(tmp_path):
    (tmp_path / "a.csv").write_text("x\n")
    before = stamp(tmp_path)
    assert stamp(tmp_path) == before
    (tmp_path / "a.csv").write_text("xy\n")
    assert stamp(tmp_path) != before
