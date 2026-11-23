"""Runs the engine for the interface: a small thread pool, a time limit and a cache.

Plain-language summary
----------------------
A run on a real state can take tens of seconds (the missing-middle reform
reruns pro formas for every tract and draw). The interface must not freeze
while it waits, and it must say plainly when a run takes too long. So:

- runs go to a **thread pool** of a few workers;
- the request waits up to a **time limit** (``timeout`` seconds). Past it the
  caller gets a clear "took too long" error. The run itself keeps going in the
  background and its result is kept, so asking again a little later is quick;
- identical requests that arrive while a run is in progress share that run;
- finished results sit in an **in-memory LRU cache** (least recently used
  results are dropped first) keyed by the checked request and the state's data
  and assumption fingerprints, so a changed data pack or ledger is never served
  from an old result.

The engine is called through its one entry point, ``engine.run``, with a
loaded state context, so the numbers are exactly the engine's.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from visionhousing.engine.context import TESTLAND, TESTLAND_DIR, StateContext, load_context

__all__ = ["ContextStore", "RunTimeoutError", "Runner"]


class RunTimeoutError(Exception):
    """A run did not finish within the time limit (it keeps going in the background)."""

    def __init__(self, seconds: float):
        super().__init__(f"the run took longer than {seconds:g} seconds")
        self.seconds = seconds


def _signature(folder: Path) -> tuple:
    from visionhousing.ledger.loader import ROOT

    paths = [p for p in sorted(folder.rglob("*")) if p.is_file()]
    paths += sorted((ROOT / "assumptions").rglob("*.yaml"))
    return tuple((p.as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


class ContextStore:
    """Loads each state's context once and reloads it when its files change.

    Args:
        packs_dir: Folder holding one built pack per state code.
    """

    def __init__(self, packs_dir: Path):
        self.packs_dir = Path(packs_dir)
        self._held: dict[str, tuple[tuple, StateContext]] = {}
        self._lock = threading.Lock()

    def folder(self, state: str) -> Path:
        """Return the folder a state's data are read from."""
        code = state.upper()
        return TESTLAND_DIR if code == TESTLAND else self.packs_dir / code

    def get(self, state: str) -> StateContext:
        """Return the loaded context of ``state``.

        Raises:
            FileNotFoundError: The state has no built pack.
        """
        code = state.upper()
        folder = self.folder(code)
        if not folder.is_dir():
            raise FileNotFoundError(
                f"no state pack for {code}; build it with `make data STATE={code}`"
            )
        sig = _signature(folder)
        with self._lock:
            held = self._held.get(code)
            if held is not None and held[0] == sig:
                return held[1]
        ctx = load_context(code, folder=folder)
        with self._lock:
            self._held[code] = (sig, ctx)
        return ctx


@dataclass
class _Entry:
    value: Any


class Runner:
    """Runs jobs on a thread pool with a time limit, sharing and caching results.

    Args:
        workers: Threads in the pool.
        timeout: Seconds a request waits before it gets a timeout error.
        cache_size: Results kept in the LRU cache.
    """

    def __init__(self, workers: int = 2, timeout: float = 120.0, cache_size: int = 32):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vh-run")
        self.timeout = timeout
        self.cache_size = cache_size
        self._cache: OrderedDict[str, _Entry] = OrderedDict()
        self._running: dict[str, Future] = {}
        # Re-entrant: a job that is already done runs its callback (_store) at once,
        # inside submit, while the lock is held.
        self._lock = threading.RLock()
        self.stats = {"hits": 0, "misses": 0, "shared": 0, "timeouts": 0}

    @staticmethod
    def key(*parts: Any) -> str:
        """Return a stable cache key for JSON-ready parts."""
        return json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)

    def _store(self, key: str, fut: Future) -> None:
        with self._lock:
            self._running.pop(key, None)
            if fut.cancelled() or fut.exception() is not None:
                return
            self._cache[key] = _Entry(fut.result())
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def submit(self, key: str, job: Callable[[], Any]) -> Any:
        """Return the cached value for ``key``, or run ``job`` within the time limit.

        Raises:
            RunTimeoutError: The job did not finish in time.
            Exception: Whatever the job raised.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
                self.stats["hits"] += 1
                return entry.value
            fut = self._running.get(key)
            if fut is None:
                self.stats["misses"] += 1
                fut = self.pool.submit(job)
                self._running[key] = fut
                fut.add_done_callback(lambda f, k=key: self._store(k, f))
            else:
                self.stats["shared"] += 1
        try:
            return fut.result(timeout=self.timeout)
        except FutureTimeout:
            self.stats["timeouts"] += 1
            raise RunTimeoutError(self.timeout) from None

    def clear(self) -> None:
        """Drop every cached result."""
        with self._lock:
            self._cache.clear()

    def shutdown(self) -> None:
        """Stop the pool without waiting for running jobs."""
        self.pool.shutdown(wait=False, cancel_futures=True)
