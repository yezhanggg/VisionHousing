"""Runs the engine for the interface: a bounded work queue, a time limit and a cache.

Plain-language summary
----------------------
A run on a real state can take tens of seconds (the missing-middle reform
reruns pro formas for every tract and draw), and one finished run can hold
gigabytes of draws. The interface must not freeze while it waits, must say
plainly when a run takes too long, and must not let a stream of requests fill
the computer's memory. So:

- runs go to a **thread pool** of a few workers (two by default) with a **small
  queue** in front of it. When the workers and the queue are all taken, a new
  job is refused at once with :class:`RunnerBusyError`; the app turns that into
  ``503 busy`` with a ``Retry-After`` header (seconds to wait before asking
  again);
- the request waits up to a **time limit** (``timeout`` seconds). Past it the
  caller gets a clear "took too long" error. If no one else waits for the same
  job, a job still in the queue is cancelled; one already running cannot be
  stopped, so it finishes but its result is **dropped** (an *abandoned* job),
  except for a default scenario, whose result is kept because the set of
  default scenarios is small and fixed. Asking again while the job still runs
  picks it up again;
- identical requests that arrive while a job is going share that job;
- finished results are cached in memory in two parts, each dropping the least
  recently used entry first (an LRU cache):

  * **rendered replies** (JSON or file bytes) up to a **byte budget**
    (256 MB by default);
  * **whole engine results** (and other Python objects) up to a **result
    budget** in estimated bytes (3 GB by default): the size of a result is
    the sum of the bytes of every numpy array and table reachable from it
    (:func:`estimate_nbytes`). A result larger than the whole budget is not
    kept at all; only its rendered replies are. On top of the budget, at most
    ``cache_size`` results are kept (4 by default), of which at most
    ``custom_slots`` (1 by default) may be a non-default scenario (another
    seed, draw count or assumption overrides). A default scenario is the
    default seed and draws with no overrides. A result that was dropped is
    simply run again, under the same queue limits, when an address needs it.

- a **memory watchdog** (:class:`MemoryWatchdog`) looks at the process's
  resident memory (RSS: the part of its memory held in RAM) every 30 seconds
  and empties both caches when it passes a limit (6,000 MB by default) and the
  caches hold something, with a log line saying so. After a clear it waits for
  memory to fall below 90% of the limit before it is armed again, and until then
  clears only after a growing back-off wait. It is the last line of defence when
  the estimates miss something.

The cache keys hold the checked request and the state's data, calibration and
assumption fingerprints, and the endpoints add the stamps of any other files
they read (:func:`stamp`), so a rebuilt pack, district table or edited ledger is
never served from an old result.

The limits can be changed with environment variables: ``VISIONHOUSING_RUN_QUEUE``
(jobs waiting beyond the workers), ``VISIONHOUSING_CACHE_MB`` (byte budget of
rendered replies), ``VISIONHOUSING_RESULT_MB`` (budget of whole results),
``VISIONHOUSING_RSS_MB`` (the watchdog's memory limit; 0 switches it off) and
``VISIONHOUSING_RETRY_AFTER`` (seconds in ``Retry-After``).

The engine is called through its one entry point, ``engine.run``, with a
loaded state context, so the numbers are exactly the engine's.
"""

from __future__ import annotations

import dataclasses
import gc
import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.engine.context import TESTLAND, TESTLAND_DIR, StateContext, load_context
from visionhousing.paths import safe_child, state_code

__all__ = [
    "LOAD",
    "ContextStore",
    "MemoryWatchdog",
    "RunTimeoutError",
    "Runner",
    "RunnerBusyError",
    "estimate_nbytes",
    "is_default_scenario",
    "process_rss",
    "require_default_sampling",
    "share_context",
    "stamp",
]

LOAD = {
    "queue": int(os.environ.get("VISIONHOUSING_RUN_QUEUE", "4")),
    "byte_budget": int(float(os.environ.get("VISIONHOUSING_CACHE_MB", "256")) * 1024 * 1024),
    "objects": 4,
    "result_budget": int(float(os.environ.get("VISIONHOUSING_RESULT_MB", "3072")) * 1024 * 1024),
    "custom_slots": 1,
    "retry_after": int(os.environ.get("VISIONHOUSING_RETRY_AFTER", "15")),
    "rss_limit": int(float(os.environ.get("VISIONHOUSING_RSS_MB", "6000")) * 1024 * 1024),
    "watch_every": 30.0,
    "watch_rearm": 0.9,
    "watch_backoff": 60.0,
    "watch_backoff_max": 1800.0,
}
"""Load limits of the interface (server settings, not model assumptions).

``queue``: jobs that may wait beyond the busy workers; ``byte_budget``: bytes of
rendered replies kept; ``objects``: whole results kept; ``result_budget``:
estimated bytes of whole results kept; ``custom_slots``: of those, how many may
be non-default scenarios; ``retry_after``: seconds a refused caller is told to
wait; ``rss_limit``: resident memory past which the watchdog empties the caches
(0: no watchdog); ``watch_every``: seconds between the watchdog's checks;
``watch_rearm``: after emptying the caches the watchdog is re-armed only once resident
memory falls below this share of the limit; ``watch_backoff``: while not re-armed, it
waits at least this many seconds before emptying again, doubling each time up to
``watch_backoff_max``."""

LOG = logging.getLogger("visionhousing.api.runner")


class RunTimeoutError(Exception):
    """A run did not finish within the time limit."""

    def __init__(self, seconds: float):
        super().__init__(f"the run took longer than {seconds:g} seconds")
        self.seconds = seconds


class RunnerBusyError(Exception):
    """Every worker and queue place is taken; the caller should retry later."""

    def __init__(self, retry_after: int):
        super().__init__(
            f"the tool is busy with other runs; try again in about {retry_after} seconds"
        )
        self.retry_after = retry_after


def _signature(folder: Path) -> tuple:
    from visionhousing.ledger.loader import ROOT

    paths = [p for p in sorted(folder.rglob("*")) if p.is_file()]
    paths += sorted((ROOT / "assumptions").rglob("*.yaml"))
    return tuple((p.as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def stamp(folder: Path) -> str:
    """A short fingerprint of a state folder's files and the assumption files.

    Built from each file's path, size and change time, so it changes when any
    of them is rebuilt or edited. Endpoints put it in their cache keys.
    """
    raw = json.dumps(_signature(Path(folder)), separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def is_default_scenario(req: Any) -> bool:
    """True for the default seed (0) and draws with no assumption overrides."""
    from visionhousing.ledger import DEFAULT_DRAWS

    return req.seed == 0 and req.draws == DEFAULT_DRAWS and not req.overrides


def require_default_sampling(req: Any) -> str | None:
    """The reason a GET scenario is refused, or ``None`` when it may run.

    A real state's GET address answers only the default seed (0) and draws;
    another seed or draw count goes through ``POST /run``. Testland, the small
    made-up state, accepts any seed and draws within the usual limits.
    """
    from visionhousing.ledger import DEFAULT_DRAWS

    if req.state == TESTLAND or (req.seed == 0 and req.draws == DEFAULT_DRAWS):
        return None
    return (
        f"this address answers only the default seed (0) and draws ({DEFAULT_DRAWS:,}); "
        "use POST /api/v1/run for another seed or draw count"
    )


_SHARED_BASES: dict[int, weakref.ref] = {}
"""Base arrays owned by a loaded ``StateContext`` (id -> weak reference). A result that
holds a view of one of them shares the state's data; the view is not counted."""
_SHARED_LOCK = threading.Lock()


def _root(array: np.ndarray) -> np.ndarray:
    base = array
    while isinstance(base.base, np.ndarray):
        base = base.base
    return base


def _context_arrays(ctx: Any, depth: int = 0, seen: set[int] | None = None):
    """Every numpy array (and table column) reachable from a context, without copying."""
    seen = set() if seen is None else seen
    if ctx is None or isinstance(ctx, str | bytes | int | float | bool) or depth > 8:
        return
    if id(ctx) in seen:
        return
    seen.add(id(ctx))
    if isinstance(ctx, np.ndarray):
        yield ctx
        return
    name = type(ctx).__name__
    if name == "DataFrame":
        for col in ctx.columns:
            try:
                values = ctx[col].to_numpy(copy=False)
            except Exception:  # an unusual column: skip it (it stays counted)
                values = None
            if isinstance(values, np.ndarray):
                yield values
        return
    if name in ("Series", "Index"):
        try:
            values = ctx.to_numpy(copy=False)
        except Exception:
            return
        if isinstance(values, np.ndarray):
            yield values
        return
    if isinstance(ctx, dict):
        children = list(ctx.values())
    elif isinstance(ctx, list | tuple | set | frozenset):
        children = list(ctx)
    elif dataclasses.is_dataclass(ctx) and not isinstance(ctx, type):
        children = [getattr(ctx, f.name, None) for f in dataclasses.fields(ctx)]
    else:
        attrs = getattr(ctx, "__dict__", None)
        children = list(attrs.values()) if isinstance(attrs, dict) else []
    for child in children:
        yield from _context_arrays(child, depth + 1, seen)


def share_context(ctx: Any) -> None:
    """Record a loaded context's arrays as shared, so views of them are not counted."""
    with _SHARED_LOCK:
        for key in [k for k, ref in _SHARED_BASES.items() if ref() is None]:
            del _SHARED_BASES[key]
        for array in _context_arrays(ctx):
            root = _root(array)
            try:
                _SHARED_BASES[id(root)] = weakref.ref(root)
            except TypeError:  # cannot be weakly referenced: leave it counted
                continue


def _is_shared(root: np.ndarray) -> bool:
    ref = _SHARED_BASES.get(id(root))
    return ref is not None and ref() is root


def estimate_nbytes(value: Any, *, _depth: int = 0, _seen: set[int] | None = None) -> int:
    """Estimate the memory a cached value holds: the bytes of its arrays and tables.

    Walks dataclass fields, mappings, lists, tuples and sets (and plain objects'
    attributes) and adds up ``nbytes`` of every numpy array and the memory of
    every pandas table it reaches. Each object is counted once (an array that
    is a view of another counts its base once). The state's loaded data
    (``StateContext``) and the Assumption Ledger are shared by every result,
    so they are not counted, and neither is an array whose base belongs to a
    loaded context (:func:`share_context`; a view of the state's data). Strings,
    numbers and small records are not counted either: the draws are what make a
    result large. It is quick (no copying) and it is an estimate, which is why
    the watchdog backs it up. The runner calls it before taking its lock.
    """
    seen = set() if _seen is None else _seen
    if value is None or isinstance(value, str | bytes | bytearray | int | float | bool):
        return len(value) if isinstance(value, bytes | bytearray) else 0
    if id(value) in seen or _depth > 12:
        return 0
    seen.add(id(value))
    if isinstance(value, np.ndarray):
        base = _root(value)
        if base is not value:
            if id(base) in seen:
                return 0
            seen.add(id(base))
        if _is_shared(base):
            return 0
        return int(base.nbytes)
    name = type(value).__name__
    if name == "StateContext":
        share_context(value)
        return 0
    if name in ("Ledger", "Record"):
        return 0
    if name in ("DataFrame", "Series", "Index") and hasattr(value, "memory_usage"):
        try:
            used = value.memory_usage(index=True, deep=False)
            return int(used.sum()) if hasattr(used, "sum") else int(used)
        except Exception:  # an unusual table: count nothing rather than fail
            return 0
    nxt = _depth + 1
    if isinstance(value, dict) or callable(getattr(value, "items", None)):
        try:
            items = list(value.items())
        except Exception:
            return 0
        return sum(estimate_nbytes(v, _depth=nxt, _seen=seen) for _, v in items)
    if isinstance(value, list | tuple | set | frozenset):
        return sum(estimate_nbytes(v, _depth=nxt, _seen=seen) for v in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return sum(
            estimate_nbytes(getattr(value, f.name, None), _depth=nxt, _seen=seen)
            for f in dataclasses.fields(value)
        )
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict) and not isinstance(value, type):
        return sum(estimate_nbytes(v, _depth=nxt, _seen=seen) for v in attrs.values())
    return 0


def process_rss(pid: int | None = None) -> int | None:
    """This process's resident memory in bytes, or ``None`` when it cannot be read.

    Asks ``ps -o rss= -p <pid>`` (kilobytes, on macOS and Linux alike). The
    standard library's ``resource.getrusage`` reports only the peak, which
    never goes down, so it cannot tell whether emptying the caches helped.
    """
    ps = shutil.which("ps") or "/bin/ps"
    try:
        # A fixed program and arguments; the only value passed is our own process id.
        out = subprocess.run(  # noqa: S603
            [ps, "-o", "rss=", "-p", str(int(pid or os.getpid()))],
            capture_output=True, text=True, timeout=5, check=True,
        )  # fmt: skip
        return int(out.stdout.split()[0]) * 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class ContextStore:
    """Loads each state's context once and reloads it when its files change.

    Args:
        packs_dir: Folder holding one built pack per state code.
    """

    def __init__(self, packs_dir: Path):
        self.packs_dir = Path(packs_dir)
        self._held: dict[str, tuple[tuple, StateContext]] = {}
        self._loading: dict[tuple[str, tuple], Future] = {}
        self._lock = threading.Lock()

    def folder(self, state: str) -> Path:
        """Return the folder a state's data are read from.

        Raises:
            ValueError: ``state`` is not a two-letter code (checked by
                :func:`visionhousing.paths.state_code`, joined by
                :func:`visionhousing.paths.safe_child`).
        """
        code = state_code(state)
        return TESTLAND_DIR if code == TESTLAND else safe_child(self.packs_dir, code)

    def get(self, state: str) -> StateContext:
        """Return the loaded context of ``state``.

        Raises:
            FileNotFoundError: The state has no built pack.
            ValueError: ``state`` is not a two-letter code.
        """
        code = state_code(state)
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
            # One load per state and file version: concurrent first requests wait on it.
            loading = self._loading.get((code, sig))
            owner = loading is None
            if owner:
                loading = Future()
                self._loading[(code, sig)] = loading
        if not owner:
            return loading.result()
        try:
            ctx = load_context(code, folder=folder)
            share_context(ctx)
        except BaseException as exc:
            with self._lock:
                self._loading.pop((code, sig), None)
            loading.set_exception(exc)
            raise
        with self._lock:
            self._held[code] = (sig, ctx)
            self._loading.pop((code, sig), None)
        loading.set_result(ctx)
        return ctx


class Runner:
    """Runs jobs on a bounded thread pool with a time limit, sharing and caching results.

    Args:
        workers: Threads in the pool.
        timeout: Seconds a request waits before it gets a timeout error.
        cache_size: Whole results (Python objects) kept in the cache, at most.
        queue: Jobs that may wait beyond the workers (default ``LOAD["queue"]``).
        byte_budget: Bytes of rendered replies kept (default ``LOAD["byte_budget"]``).
        result_budget: Estimated bytes of whole results kept (default
            ``LOAD["result_budget"]``; ``math.inf`` for no byte limit, so only
            ``cache_size`` counts). A result larger than this is not kept.
        custom_slots: Of the whole results kept, how many may be non-default
            scenarios (default ``LOAD["custom_slots"]``).
        retry_after: Seconds a refused caller is told to wait.
    """

    def __init__(
        self,
        workers: int = 2,
        timeout: float = 120.0,
        cache_size: int = LOAD["objects"],
        *,
        queue: int | None = None,
        byte_budget: int | None = None,
        result_budget: float | None = None,
        custom_slots: int | None = None,
        retry_after: int | None = None,
    ):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vh-run")
        self.workers = workers
        self.timeout = timeout
        self.cache_size = cache_size
        self.queue = LOAD["queue"] if queue is None else queue
        self.byte_budget = LOAD["byte_budget"] if byte_budget is None else byte_budget
        self.result_budget = LOAD["result_budget"] if result_budget is None else result_budget
        self.custom_slots = LOAD["custom_slots"] if custom_slots is None else custom_slots
        self.retry_after = LOAD["retry_after"] if retry_after is None else retry_after
        self._bytes: OrderedDict[str, bytes] = OrderedDict()
        self._bytes_held = 0
        # key -> (value, pinned, estimated bytes)
        self._objects: OrderedDict[str, tuple[Any, bool, int]] = OrderedDict()
        self._objects_held = 0
        self._running: dict[str, Future] = {}
        self._waiters: dict[str, int] = {}
        self._pinned: dict[str, bool] = {}
        self._abandoned: set[str] = set()
        # Re-entrant, to be safe; submit adds the done callback (_store) after releasing it.
        self._lock = threading.RLock()
        self.stats = {
            "hits": 0, "misses": 0, "shared": 0, "timeouts": 0,
            "busy": 0, "cancelled": 0, "dropped": 0, "too_big": 0, "evictions": 0,
        }  # fmt: skip

    @staticmethod
    def key(*parts: Any) -> str:
        """Return a stable cache key for JSON-ready parts."""
        return json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)

    # ------------------------------------------------------------ cache

    @property
    def cached_bytes(self) -> int:
        """Bytes of rendered replies held in the cache."""
        return self._bytes_held

    @property
    def cached_objects(self) -> int:
        """Whole results held in the cache."""
        return len(self._objects)

    @property
    def cached_object_bytes(self) -> int:
        """Estimated bytes of the whole results held in the cache."""
        return self._objects_held

    @property
    def in_flight(self) -> int:
        """Jobs running or waiting in the queue."""
        return len(self._running)

    def _lookup(self, key: str) -> tuple[bool, Any]:
        if key in self._bytes:
            self._bytes.move_to_end(key)
            return True, self._bytes[key]
        if key in self._objects:
            self._objects.move_to_end(key)
            return True, self._objects[key][0]
        return False, None

    def _keep(self, key: str, value: Any, pinned: bool, size: int | None = None) -> None:
        if isinstance(value, bytes | bytearray):
            size = len(value)
            if size > self.byte_budget:
                return
            old = self._bytes.pop(key, None)
            self._bytes_held -= len(old) if old is not None else 0
            self._bytes[key] = bytes(value)
            self._bytes_held += size
            while self._bytes_held > self.byte_budget:
                _, gone = self._bytes.popitem(last=False)
                self._bytes_held -= len(gone)
            return
        if self.cache_size <= 0 or (not pinned and self.custom_slots <= 0):
            return
        if size is None:
            size = estimate_nbytes(value)
        if size > self.result_budget:
            self.stats["too_big"] += 1
            return  # larger than the whole budget: keep only its rendered replies
        self._drop_object(key)
        self._objects[key] = (value, pinned, size)
        self._objects_held += size
        custom = [k for k, (_, p, _n) in self._objects.items() if not p]
        while len(custom) > self.custom_slots:
            self._drop_object(custom.pop(0))
        while len(self._objects) > self.cache_size or self._objects_held > self.result_budget:
            # Drop the least recently used non-default entry first, else the oldest.
            victim = next((k for k, (_, p, _n) in self._objects.items() if not p), None)
            self._drop_object(victim if victim is not None else next(iter(self._objects)))

    def _drop_object(self, key: str) -> None:
        held = self._objects.pop(key, None)
        if held is not None:
            self._objects_held -= held[2]

    def _store(self, key: str, fut: Future) -> None:
        # The estimate walks the whole result: do it before taking the lock.
        size = None
        if not fut.cancelled() and fut.exception() is None:
            value = fut.result()
            if not isinstance(value, bytes | bytearray):
                size = estimate_nbytes(value)
        with self._lock:
            if self._running.get(key) is not fut:
                return  # already cleaned up (cancelled while still queued)
            self._running.pop(key, None)
            self._waiters.pop(key, None)
            pinned = self._pinned.pop(key, False)
            abandoned = key in self._abandoned
            self._abandoned.discard(key)
            if fut.cancelled() or fut.exception() is not None:
                return
            if abandoned and not pinned:
                self.stats["dropped"] += 1
                return
            self._keep(key, fut.result(), pinned, size)

    def peek(self, key: str) -> tuple[bool, Any]:
        """Return ``(True, value)`` when ``key`` is cached, else ``(False, None)``; runs nothing.

        The addresses look up their rendered reply with this before they ask for
        the engine run behind it, so a cached reply never needs its run again.
        """
        with self._lock:
            found, value = self._lookup(key)
            if found:
                self.stats["hits"] += 1
            return found, value

    def put(self, key: str, value: Any, *, pin: bool = False) -> None:
        """Keep ``value`` under ``key`` (bytes in the reply cache, else as a whole result)."""
        size = None if isinstance(value, bytes | bytearray) else estimate_nbytes(value)
        with self._lock:
            self._keep(key, value, pin, size)

    # ------------------------------------------------------------ jobs

    def submit(self, key: str, job: Callable[[], Any], *, pin: bool = False) -> Any:
        """Return the cached value for ``key``, or run ``job`` within the time limit.

        Args:
            key: The cache key (:meth:`key`).
            job: Makes the value.
            pin: The value belongs to a default scenario: it may take any of the
                whole-result places, and it is kept even when its caller gave up.

        Raises:
            RunnerBusyError: Every worker and queue place is taken.
            RunTimeoutError: The job did not finish in time.
            Exception: Whatever the job raised.
        """
        with self._lock:
            found, value = self._lookup(key)
            if found:
                self.stats["hits"] += 1
                return value
            fut = self._running.get(key)
            new = fut is None
            if new:
                if len(self._running) >= self.workers + self.queue:
                    self.stats["busy"] += 1
                    raise RunnerBusyError(self.retry_after)
                self.stats["misses"] += 1
                fut = self.pool.submit(job)
                self._running[key] = fut
                self._waiters[key] = 1
                self._pinned[key] = pin
            else:
                self.stats["shared"] += 1
                self._waiters[key] = self._waiters.get(key, 0) + 1
                self._pinned[key] = self._pinned.get(key, False) or pin
                self._abandoned.discard(key)
        if new:
            # Outside the lock: a job already done runs _store here, at once, and its
            # estimate must not hold the lock.
            fut.add_done_callback(lambda f, k=key: self._store(k, f))
        try:
            return fut.result(timeout=self.timeout)
        except FutureTimeout:
            self._give_up(key, fut)
            raise RunTimeoutError(self.timeout) from None

    def _give_up(self, key: str, fut: Future) -> None:
        """One caller stopped waiting; with no caller left, cancel or abandon the job."""
        with self._lock:
            self.stats["timeouts"] += 1
            if self._running.get(key) is not fut:
                return
            left = self._waiters.get(key, 1) - 1
            self._waiters[key] = left
            if left > 0:
                return
            if fut.cancel():  # still in the queue: it never starts (_store cleans up)
                self.stats["cancelled"] += 1
            else:  # already running: let it finish, then drop the result
                self._abandoned.add(key)

    def clear(self) -> None:
        """Drop every cached result."""
        with self._lock:
            self._bytes.clear()
            self._bytes_held = 0
            self._objects.clear()
            self._objects_held = 0

    def shutdown(self) -> None:
        """Stop the pool without waiting for running jobs."""
        self.pool.shutdown(wait=False, cancel_futures=True)


class MemoryWatchdog:
    """Empties the runner's caches when the process holds too much memory.

    A background thread reads the process's resident memory (:func:`process_rss`)
    every ``every`` seconds. Past ``limit`` bytes, and only when the caches hold
    something, it clears both caches of the runner, asks Python to free what is
    no longer used, and logs a warning with the memory before and after. Results
    that a request is still using stay in memory until that request is done.

    Memory often stays high after a clear (Python keeps freed memory for reuse),
    so the watchdog does not clear again at every check: after a clear it is
    *disarmed* until resident memory falls below ``rearm`` of the limit
    (hysteresis). While disarmed it clears again only after a back-off wait
    (``backoff`` seconds, doubling after each such clear up to ``backoff_max``),
    so a server that truly stays over the limit is still kept in check.

    Args:
        runner: The runner whose caches are emptied.
        limit: Resident bytes past which the caches are emptied
            (default ``LOAD["rss_limit"]``).
        every: Seconds between checks (default ``LOAD["watch_every"]``).
        rss: Reads the resident bytes (default :func:`process_rss`; tests pass
            a fake).
        rearm: Share of the limit below which it is re-armed (``LOAD["watch_rearm"]``).
        backoff: First back-off wait in seconds (``LOAD["watch_backoff"]``).
        backoff_max: Longest back-off wait (``LOAD["watch_backoff_max"]``).
        clock: Seconds now (default ``time.monotonic``; tests pass a fake).
    """

    def __init__(
        self,
        runner: Runner,
        limit: int | None = None,
        every: float | None = None,
        rss: Callable[[], int | None] = process_rss,
        *,
        rearm: float | None = None,
        backoff: float | None = None,
        backoff_max: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.runner = runner
        self.limit = LOAD["rss_limit"] if limit is None else limit
        self.every = LOAD["watch_every"] if every is None else every
        self.rss = rss
        self.rearm = LOAD["watch_rearm"] if rearm is None else rearm
        self.backoff = LOAD["watch_backoff"] if backoff is None else backoff
        self.backoff_max = LOAD["watch_backoff_max"] if backoff_max is None else backoff_max
        self.clock = clock
        self.armed = True
        self._wait = self.backoff
        self._next = 0.0
        self.evictions = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> bool:
        """Check once; return ``True`` when the caches were emptied."""
        held = self.rss()
        if held is None:
            return False
        if not self.armed and held < self.rearm * self.limit:
            self.armed, self._wait = True, self.backoff  # memory came down: re-arm
        if held <= self.limit:
            return False
        objects, replies = self.runner.cached_objects, self.runner.cached_bytes
        if objects == 0 and replies == 0:
            return False  # nothing to free: emptying empty caches helps no one
        now = self.clock()
        if not self.armed:
            if now < self._next:
                return False  # still backing off since the last clear
            self._wait = min(self._wait * 2, self.backoff_max)
        self.armed = False
        self._next = now + self._wait
        self.runner.clear()
        gc.collect()
        self.evictions += 1
        self.runner.stats["evictions"] += 1
        after = self.rss()
        LOG.warning(
            "memory watchdog: resident memory %.0f MB is over the %.0f MB limit; emptied the "
            "caches (%d whole results, %.0f MB of replies); now %s MB",
            held / 2**20, self.limit / 2**20, objects, replies / 2**20,
            "unknown" if after is None else f"{after / 2**20:.0f}",
        )  # fmt: skip
        return True

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            try:
                self.check()
            except Exception:  # never let the watchdog take the server down
                LOG.exception("memory watchdog check failed")

    def start(self) -> None:
        """Start the background thread (does nothing when the limit is 0 or it runs)."""
        if self.limit <= 0 or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="vh-memory", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
