"""Export a state's results as plain JSON files, so the interface can run with no server.

Plain-language summary
----------------------
The interface normally asks the FastAPI back end for every number. A static web host
(such as Vercel) has no Python, so this module asks the back end once, ahead of time,
and saves each reply as a file. The front end, built with ``VITE_DATA_MODE=static``,
then reads those files instead (``web/js/lib/api.js``).

It calls the app in memory (no network, no open port), so every file holds exactly
the bytes the live interface would send. It saves only the **default** scenarios:
default assumptions, seed 0 and the default number of draws. Anything else (a custom
assumption, another seed) still needs the local app; the static site says so.

Usage::

    visionhousing export-static --state PA --out web/public/data

Files written under ``--out`` (``ST`` is the state code)::

    states.json                       the exported states (merged with earlier exports)
    reforms.json, reforms/{key}.json  every reform's definition and its ledger parameters
    ledger.json, provenance.json      the Assumption Ledger and the source list
    ST/index.json                     what this export holds: runs, counties, sizes
    ST/summary.json                   the state's facts and baseline
    ST/baseline-card.json             the summary's ``baseline_card`` (with its display rows),
                                      so the screens need not load the whole summary
    ST/runs/{key}.json                POST /run for "baseline", each benchmark reform
                                      alone (with its top drivers), and "package" (all
                                      five together); for PA (and Testland) also each
                                      Pennsylvania bill alone ("extra_runs" in the index)
    ST/compare/{key}.json             POST /compare for the same scenarios
    ST/maps/{key}.json                GET /states/ST/map for every saved run
    ST/tract-points.json              GET /states/ST/tract-points: every tract's id, county and
                                      interior point (the statewide tract columns stand on them)
    ST/tracts/{key}.json              GET /states/ST/tracts for every saved run (homes added per
                                      tract, keyed by geoid)
    ST/tract-funnel/{key}.json        GET /states/ST/tract-funnel for each saved run of one
                                      tract-level reform (its map funnel, tract by tract)
    ST/exports/{key}.csv, .html       GET /export/csv and /export/html (the brief) for
                                      every saved run
    ST/exports/{key}.hearing.html     GET /export/hearing: the two-page hearing brief
    hearing.css                       its style rules (the page links them: no inline styles)
    ST/outlines.json                  the county outlines for the map
    ST/permit-years.json              GET /states/ST/permit-years: homes permitted per county and
                                      year (the map key's year slider)
    ST/report-card.json               the state's data report card
    evidence.json                     the evidence library
    ST/counties/{fips}.json           one county: baseline, each reform and the package
    ST/ledger.json, ST/provenance.json, ST/validation.json
    ST/places/counties.json           the county search list
    ST/places/tracts-{fips}.json      the tract list of one county (only with tract profiles)
    ST/profiles/{geoid}.json          one place profile per county (and per tract, if small)
    ST/profiles/county-template.json  compact county profiles: the shared template ...
    ST/profiles/county-values.json    ... and every county's values (when that is smaller)
    ST/profiles/tract-template.json   compact tract profiles: the shared template ...
    ST/profiles/tracts-{fips}.json    ... and each county's tract values (when the whole
                                      tract profiles are over the budget)
    ST/comparisons/{part}.json        GET /states/ST/comparisons?part= for each part
                                      (interactions, dials, matrix, ablation, ranking);
                                      ``comparisons: true`` in the index
    ST/confidence.json                GET /confidence/ST (the two confidence signals)
    ST/plausibility.json              GET /plausibility/ST (every benchmark reform)
    ST/lever/{key}.json               GET /lever/ST?r=KEY for each benchmark reform (state)
    ST/implementation.json            GET /implementation/ST (every reform and place)
    ST/grades/{key}.json              GET /grades/ST for the baseline, each benchmark reform
                                      and the package
    ST/districts/outlines-{chamber}.json   GET /states/ST/district-outlines, ``lower`` and
                                      ``upper`` (only for a state whose pack has them;
                                      ``districts: true`` in the index)
    ST/districts/{chamber}/{key}.json GET /states/ST/districts for every saved run
    ST/switch-ranges/{key}.json       each benchmark reform's switch ranges (top inputs fixed
                                      at low / central / high), from the engine's cache only;
                                      ``switch_ranges`` in the index lists the keys
    ST/law_changes/{key}.json         "What this law changes" per reform ({changes, read_next};
                                      read_next only when cached), ``law_changes`` in the index
    ST/adu-budget-grid.json           the ADU budget-and-target grid ({grid, view}), from the
                                      disk cache only; ``adu_budget_grid: true`` in the index
    ST/alternatives.json              the benchmarks' Alternatives table (compact), from the
                                      disk cache only; ``alternatives: true`` in the index
    ST/pool.json                      the state's shared pool (see "Packed files")

Each card, the comparisons and the districts are flagged in ``index.json`` (``confidence``,
``plausibility``, ``lever``, ``implementation``, ``grades``, ``comparisons``, ``districts``)
only when they were saved, so the static site asks only for files that exist. With
``extras`` the extra scenarios (``GET /reforms`` kind ``extra``) are saved as single runs
too, like the state bills (``extra_runs``); without it the interface says they need the
local app.

Packed files
------------
A run's explanation repeats the same blocks many times (the same assumption, the
same source list under every number). To fit more than one state in the size
limit, the screen files (``runs``, ``compare``, ``maps``, ``tracts``, ``tract-funnel``,
``counties``, ``profiles``) are saved **packed** when that makes it at least 5% smaller:
each block (object, list or long text) that appears more than once is stored once
in a ``"$pool"`` list and replaced by ``{"$p": n}``. The file then reads
``{"$packed": 1, "$pool": [...], "$data": ...}``. Nothing is dropped or rounded:
:func:`unpack` (and ``unpack`` in ``web/js/lib/api.js``) gives back exactly the
live reply. Files that do not shrink are saved as the live bytes.

The screen files of one state also repeat each other: every run carries the same county
records, every district reply the same district names, every county panel the same lists.
So once a state is saved, :func:`share_state_pool` lays lists of like objects out as
columns (``{"$c": keys, "$v": columns}``, :func:`to_columns`) and moves every block that two
or more files hold into one file per state, ``ST/pool.json``, replacing it by
``{"$s": n}``; such files say ``"$shared": 1``. Read them with :func:`read_saved` (Python) or
``file`` in ``web/js/lib/api.js``, which fetches the pool once. With Pennsylvania's and
Massachusetts' tract profiles back, this took the four-state folder from about 74 MB
(14.6 MB compressed) to about 50 MB (11.2 MB compressed).

Tract profiles are saved whole while their total stays under ``--tract-budget-mb``
(default 12 MB, before packing; ``make export-static`` passes its own lower
``TRACT_BUDGET_MB``). Over it, they are saved **compact**: one template per state with
every word that is the same in each tract, and one file per county with only the values
that change, as the server wrote them (:mod:`visionhousing.compact_profiles`). The export
rebuilds every tract from those files and checks it equals the live reply; ``index.json``
says which form was saved (``tract_profile_format``). County profiles are saved the same
way (one template and one file for all counties) whenever that is smaller compressed than the
whole files (``county_profile_format``). The tract profiles of a large state take a long
time to ask for (about 15 minutes for Pennsylvania); with ``tract_cache`` each county's
replies are kept on disk as they arrive, so a stopped export picks up where it was (see
:func:`fetch_tract_profiles`).

Size budget
-----------
The whole ``--out`` folder must stay under two limits, both set here and checked by
``tests/test_web_package.py``:

* :data:`GZIP_LIMIT_BYTES` (12 MB), the folder's size **compressed**, file by file, with
  gzip. This is what counts: git stores every file compressed (zlib, the same method) and
  the static host sends JSON gzip- or brotli-compressed, so the compressed size is what the
  repository grows by and what a visitor downloads. The data files are repetitive JSON, so
  they shrink to about a quarter or less.
* :data:`SIZE_LIMIT_BYTES` (60 MB), a hard ceiling on the folder **uncompressed**, so a
  checkout and the built site stay a sensible size even for files that compress very well.

The tract columns (``tract-points``, ``tracts``, ``tract-funnel``) are saved with the maps,
before tract profiles, so when the folder is over the limit the profiles go first. Each
tract-column file should stay under :data:`TRACT_FILE_BUDGET_BYTES`; the report notes any
that does not.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import shutil
import sys
import time
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

__all__ = [
    "BENCHMARK_PACKAGE",
    "GZIP_LIMIT_BYTES",
    "SIZE_LIMIT_BYTES",
    "TRACT_FILE_BUDGET_BYTES",
    "ExportReport",
    "export_static",
    "fetch_tract_profiles",
    "folder_gzip_size",
    "folder_size",
    "over_limit",
    "pack_json",
    "read_saved",
    "share_state_pool",
    "to_columns",
    "unpack",
]

API = "/api/v1"
BENCHMARK_PACKAGE = ("adu", "missing_middle", "tod", "parking", "fees")
"""The five benchmark reforms, in the engine's keys. Together they are the "package"."""
STATE_BILLS = {"PA": ("pa_hb2186", "pa_sb1346"), "TL": ("pa_hb2186", "pa_sb1346")}
"""State bills saved as single runs. Each reaches the same lots as the ADU reform, so it is
never part of the package. Testland carries Pennsylvania's bills so the tests can check them."""
STATIC_SCHEMA_VERSION = 2
"""Version of the static file layout, written as ``schema_version`` in every ``ST/index.json``.

``web/js/lib/api.js`` reads it: an index without it is version 1 (before the baseline card
file and the hearing briefs); a version newer than the page knows disables the controls it
cannot honour, with the reason, instead of quietly showing something else."""

GZIP_LIMIT_BYTES = 12 * 1024 * 1024
"""The static site's data budget: the whole ``--out`` folder, each file gzip-compressed
(level :data:`GZIP_LEVEL`), added up. See "Size budget" in the module text: this is about
what git stores and what the host sends."""
SIZE_LIMIT_BYTES = 60 * 1024 * 1024
"""The hard ceiling on the whole ``--out`` folder, uncompressed (see "Size budget")."""
GZIP_LEVEL = 6
"""gzip level used to measure the budget: the default of gzip, zlib and git."""
DEFAULT_TRACT_BUDGET_MB = 12.0
"""Budget for whole tract profiles with ``--tracts auto`` (uncompressed MB, before packing).

Vermont's 193 tract profiles (about 10.5 MB before packing, 9 MB packed) fit and are saved
whole; Pennsylvania's 3,445 (well over 200 MB) do not, so they are saved compact (see the
module text). The folder's size budget still wins: tract profiles are dropped
first if the whole folder would go over it."""
TRACT_FILE_BUDGET_BYTES = 256 * 1024
"""Size each tract-column file should stay under (uncompressed, as saved). Pennsylvania's
3,445 tracts fit: the points, each scenario's values and each reform's funnel are separate
files, so the map loads the points once and only the values of what is shown."""
TRACT_TEMPLATE = "tract-template.json"
"""The compact tract profiles' shared template, under ``ST/profiles/``."""
COUNTY_TEMPLATE = "county-template.json"
"""The compact county profiles' shared template, under ``ST/profiles/``."""
COUNTY_VALUES = "county-values.json"
"""The compact county profiles' values (every county in one file), under ``ST/profiles/``."""
PACK_MIN_CHARS = 24
"""Blocks shorter than this (as compact JSON) are never pooled: a reference would not save much."""
PACK_MIN_SAVING = 0.05
"""A file is saved packed only if packing makes it at least this much smaller."""


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def pack_json(value: Any) -> Any:
    """Pool every repeated block of ``value``; see "Packed files" above.

    Returns ``value`` itself when nothing repeats. Pool entries may refer to
    earlier pool entries; :func:`unpack` resolves them.
    """
    counts: dict[str, int] = {}

    def count(node: Any) -> None:
        if isinstance(node, (dict, list)):
            text = _dumps(node)
            if len(text) >= PACK_MIN_CHARS:
                counts[text] = counts.get(text, 0) + 1
            for child in node.values() if isinstance(node, dict) else node:
                count(child)
        elif isinstance(node, str) and len(node) + 2 >= PACK_MIN_CHARS:
            text = _dumps(node)
            counts[text] = counts.get(text, 0) + 1

    count(value)
    pool: list[Any] = []
    index: dict[str, int] = {}

    def ref(node: Any) -> Any:
        if isinstance(node, (dict, list, str)):
            text = _dumps(node)
            if counts.get(text, 0) > 1:
                if text not in index:
                    body = walk(node)
                    index[text] = len(pool)
                    pool.append(body)
                return {"$p": index[text]}
        return walk(node)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: ref(v) for k, v in node.items()}
        if isinstance(node, list):
            return [ref(v) for v in node]
        return node

    data = walk(value)
    return {"$packed": 1, "$pool": pool, "$data": data} if pool else value


SHARED_POOL = "pool.json"
"""The state's shared pool (``ST/pool.json``): blocks that appear in more than one screen file."""
_MARKS = ("$p", "$s", "$c", "$v")


def to_columns(value: Any) -> Any:
    """A list of objects that all have the same keys, in the same order, as one list per key.

    ``[{"a": 1, "b": 2}, {"a": 3, "b": 4}]`` becomes ``{"$c": ["a", "b"], "$v": [[1, 3],
    [2, 4]]}``: each key once, and like values side by side (which compresses far better).
    Applied all the way down; anything else is kept as it is. :func:`unpack` undoes it.
    """
    if isinstance(value, dict):
        return {k: to_columns(v) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) >= 2 and all(isinstance(x, dict) for x in value):
            keys = list(value[0])
            if keys and all(list(x) == keys for x in value):
                return {"$c": keys, "$v": [to_columns([x[k] for x in value]) for k in keys]}
        return [to_columns(x) for x in value]
    return value


class _Resolver:
    """Puts a packed value back together.

    ``$p`` is a block of this file's pool, ``$s`` a block of the state's shared pool and
    ``$c`` a list of objects saved as columns.
    """

    def __init__(self, local: list[Any], shared: SharedPool | None):
        self.local, self.shared, self.done = local, shared, {}

    def get(self, n: int) -> Any:
        if n not in self.done:
            self.done[n] = self.walk(self.local[n])
        return self.done[n]

    def walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            if len(node) == 1 and "$p" in node:
                return self.get(node["$p"])
            if len(node) == 1 and "$s" in node:
                if self.shared is None:
                    raise ValueError("this file uses the state's shared pool; use read_saved")
                return self.shared.get(node["$s"])
            if len(node) == 2 and "$c" in node and "$v" in node:
                keys, columns = self.walk(node["$c"]), self.walk(node["$v"])
                return [dict(zip(keys, row, strict=True)) for row in zip(*columns, strict=True)]
            return {k: self.walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v) for v in node]
        return node


class SharedPool:
    """The state's shared pool file, read once: entry ``n`` is what ``{"$s": n}`` stands for."""

    def __init__(self, raw: Any):
        packed = isinstance(raw, dict) and raw.get("$packed") == 1
        self.entries = raw["$data"] if packed else raw
        self._resolver = _Resolver(raw["$pool"] if packed else [], self)
        self._done: dict[int, Any] = {}

    def get(self, n: int) -> Any:
        if n not in self._done:
            self._done[n] = self._resolver.walk(self.entries[n])
        return self._done[n]


def unpack(value: Any, shared: SharedPool | None = None) -> Any:
    """Undo the packing; a value that is not packed comes back unchanged.

    ``shared`` is the state's :class:`SharedPool`, needed by a file marked ``"$shared"``
    (:func:`read_saved` finds it by itself).
    """
    if not (isinstance(value, dict) and value.get("$packed") == 1 and "$pool" in value):
        return value
    return _Resolver(value["$pool"], shared).walk(value["$data"])


_POOLS: dict[tuple[str, int], SharedPool] = {}


def read_saved(path: Path) -> Any:
    """One saved static-site file as the live reply would read (packing undone).

    A file that uses the state's shared pool finds it in the nearest folder above it that
    holds ``pool.json`` (the state folder).
    """
    path = Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not (isinstance(value, dict) and value.get("$shared")):
        return unpack(value)
    for folder in path.parents:
        pool_path = folder / SHARED_POOL
        if pool_path.is_file():
            key = (str(pool_path.resolve()), pool_path.stat().st_mtime_ns)
            if key not in _POOLS:
                _POOLS[key] = SharedPool(json.loads(pool_path.read_text(encoding="utf-8")))
            return unpack(value, _POOLS[key])
    raise FileNotFoundError(f"{path} uses a shared pool, but no {SHARED_POOL} is above it")


PACKED_FOLDERS = (
    "runs", "compare", "maps", "tracts", "tract-funnel", "counties", "profiles", "comparisons",
    "districts", "lever", "grades", "switch-ranges",
)  # fmt: skip
"""Only screen data is packed. Files the Method screen offers as downloads (ledger,
sources, report card, backtest) stay exactly as the live app sends them."""


def _maybe_pack(rel: str, data: bytes) -> bytes:
    """The packed bytes of a JSON reply if they save enough, else the reply unchanged."""
    parts = rel.split("/")
    if not rel.endswith(".json") or len(parts) < 3 or parts[1] not in PACKED_FOLDERS:
        return data
    if len(data) < 4096:
        return data
    packed = pack_json(json.loads(data))
    if not isinstance(packed, dict) or "$packed" not in packed:
        return data
    out = _dumps(packed).encode("utf-8")
    return out if len(out) <= len(data) * (1 - PACK_MIN_SAVING) else data


def _check_marks(value: Any, rel: str) -> None:
    """Refuse a reply that already holds an object shaped like a packing mark."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if (len(node) == 1 and next(iter(node)) in ("$p", "$s")) or set(node) == {"$c", "$v"}:
                raise ExportError(f"{rel} holds an object the packing uses as a mark: {node!r:.80}")
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def share_state_pool(folder: Path) -> dict[str, tuple[int, int]]:
    """Store, once for the whole state, every block that more than one screen file repeats.

    The runs, maps, district replies, county panels and profiles of one state repeat a lot of
    each other (the same county records, assumption lists and source lists in every run).
    Packing one file at a time cannot see that. This pass reads every screen file of the state
    folder (``PACKED_FOLDERS``), lays lists of like objects out as columns
    (:func:`to_columns`), and moves each block (at least :data:`PACK_MIN_CHARS` long) found in
    two or more files into ``ST/pool.json``, replacing it by ``{"$s": n}``. A file that uses
    the pool is marked ``"$shared": 1`` and then packed on its own as before. Nothing is
    dropped or rounded: :func:`read_saved` (and ``file`` in ``web/js/lib/api.js``) give back
    exactly the live reply. Run again after files change: it reads the old pool first.

    Returns:
        Path (relative to ``folder``) to (size before, size after) for every file rewritten,
        the pool included.
    """
    files: dict[Path, Any] = {}
    was_shared: dict[Path, Any] = {}  # files that used the old pool: their live value
    for sub in PACKED_FOLDERS:
        for path in sorted((folder / sub).rglob("*.json")) if (folder / sub).is_dir() else []:
            value = read_saved(path)
            _check_marks(value, path.relative_to(folder).as_posix())
            raw = json.loads(path.read_bytes())
            if isinstance(raw, dict) and raw.get("$shared"):
                was_shared[path] = value
            files[path] = to_columns(value)
    pool_path = folder / SHARED_POOL
    before = {p: p.stat().st_size for p in [*files, pool_path] if p.is_file()}
    seen: dict[str, set[Path]] = {}
    for path, value in files.items():
        stack = [value]
        while stack:
            node = stack.pop()
            if isinstance(node, (dict, list)):
                text = _dumps(node)
                if len(text) >= PACK_MIN_CHARS:
                    seen.setdefault(text, set()).add(path)
                stack.extend(node.values() if isinstance(node, dict) else node)
            elif isinstance(node, str) and len(node) + 2 >= PACK_MIN_CHARS:
                seen.setdefault(_dumps(node), set()).add(path)
    shared = {text for text, where in seen.items() if len(where) > 1}
    pool: list[Any] = []
    index: dict[str, int] = {}
    used = False

    def ref(node: Any) -> Any:
        nonlocal used
        if isinstance(node, (dict, list, str)):
            text = _dumps(node)
            if text in shared:
                if text not in index:
                    body = walk(node)
                    index[text] = len(pool)
                    pool.append(body)
                used = True
                return {"$s": index[text]}
        return walk(node)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: ref(v) for k, v in node.items()}
        if isinstance(node, list):
            return [ref(v) for v in node]
        return node

    changed: dict[str, tuple[int, int]] = {}
    for path, value in files.items():
        used = False
        body = ref(value)
        if not used:
            if path in was_shared:  # used the old pool: save it on its own again
                rel = f"{folder.name}/{path.relative_to(folder).as_posix()}"
                data = _maybe_pack(rel, _dumps(was_shared[path]).encode("utf-8"))
                path.write_bytes(data)
                changed[path.relative_to(folder).as_posix()] = (before[path], len(data))
            continue  # nothing shared: the file stays as it was saved
        packed = pack_json(body)
        if not (isinstance(packed, dict) and "$packed" in packed):
            packed = {"$packed": 1, "$pool": [], "$data": body}
        data = _dumps({**packed, "$shared": 1}).encode("utf-8")
        path.write_bytes(data)
        changed[path.relative_to(folder).as_posix()] = (before.get(path, 0), len(data))
    if pool:
        data = _dumps(pack_json(pool)).encode("utf-8")
        pool_path.write_bytes(data)
        changed[SHARED_POOL] = (before.get(pool_path, 0), len(data))
    elif pool_path.is_file():
        pool_path.unlink()
        changed[SHARED_POOL] = (before[pool_path], 0)
    return changed


class ExportError(RuntimeError):
    """The back end refused a request during the export."""


@dataclass
class ExportReport:
    """What one export wrote: file counts and byte sizes per kind, and the notes."""

    state: str
    out: Path
    sizes: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    tract_profiles: bool = False
    tract_format: str = ""

    def add(self, kind: str, size: int, count: int = 1) -> None:
        """Count one file of ``kind`` with ``size`` bytes (negative values take it away)."""
        self.sizes[kind] = self.sizes.get(kind, 0) + size
        self.counts[kind] = self.counts.get(kind, 0) + count
        if self.counts[kind] <= 0:
            self.sizes.pop(kind)
            self.counts.pop(kind)

    @property
    def total(self) -> int:
        """Bytes written by this export."""
        return sum(self.sizes.values())

    def text(self) -> str:
        """A short size table for the terminal."""
        lines = [f"export-static {self.state}: wrote to {self.out}/"]
        for kind in sorted(self.sizes):
            lines.append(f"  {kind:<26} {self.counts[kind]:>6} files {self.sizes[kind]:>12,} bytes")
        lines.append(f"  {'this export':<26} {sum(self.counts.values()):>6} files "
                     f"{self.total:>12,} bytes")  # fmt: skip
        whole, packed = folder_size(self.out), folder_gzip_size(self.out)
        lines.append(f"  {'whole folder':<39} {whole:>12,} bytes (ceiling {SIZE_LIMIT_BYTES:,})")
        lines.append(
            f"  {'whole folder, gzip':<39} {packed:>12,} bytes (budget {GZIP_LIMIT_BYTES:,})"
        )
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def folder_size(folder: Path) -> int:
    """Total bytes of every file under ``folder`` (0 if it does not exist)."""
    if not folder.is_dir():
        return 0
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def gzip_size(data: bytes) -> int:
    """Bytes of ``data`` once gzip-compressed at :data:`GZIP_LEVEL`."""
    return len(gzip.compress(data, compresslevel=GZIP_LEVEL, mtime=0))


_GZIP_SEEN: dict[tuple[str, int, int], int] = {}
"""Compressed sizes already measured, by (path, size, change time): a file is compressed once."""


def folder_gzip_size(folder: Path) -> int:
    """Total gzip-compressed bytes of every file under ``folder``, file by file (0 if none)."""
    if not folder.is_dir():
        return 0
    total = 0
    for p in folder.rglob("*"):
        if p.is_file():
            st = p.stat()
            key = (str(p), st.st_size, st.st_mtime_ns)
            if key not in _GZIP_SEEN:
                _GZIP_SEEN[key] = gzip_size(p.read_bytes())
            total += _GZIP_SEEN[key]
    return total


def over_limit(folder: Path) -> str:
    """Why ``folder`` is over the static-site budget, in words, or "" when it is within it."""
    whole = folder_size(folder)
    if whole > SIZE_LIMIT_BYTES:
        return f"{whole / 2**20:.1f} MB is over the {SIZE_LIMIT_BYTES // 2**20} MB ceiling"
    packed = folder_gzip_size(folder)
    if packed > GZIP_LIMIT_BYTES:
        return (
            f"{packed / 2**20:.1f} MB compressed is over the "
            f"{GZIP_LIMIT_BYTES // 2**20} MB compressed budget"
        )
    return ""


class _Caller:
    """Call the ASGI app in memory, the way a web server would, and return the reply."""

    def __init__(self, app: Any):
        self.app = app

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, bytes]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._call(method, path, body))
        # Called from code that already runs an event loop (a browser test, a notebook):
        # use a short-lived thread with its own loop.
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self._call(method, path, body)).result()

    async def _call(self, method: str, path: str, body: Any) -> tuple[int, bytes]:
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        target, _, query = path.partition("?")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": target,
            "raw_path": target.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [
                (b"host", b"export"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ],
            "client": ("127.0.0.1", 0),
            "server": ("export", 80),
        }
        sent = False
        status = 0
        chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, send)
        return status, b"".join(chunks)


class _Writer:
    """Fetch an address from the app and save the reply under ``out``."""

    def __init__(self, call: _Caller, out: Path, report: ExportReport):
        self.call, self.out, self.report = call, out, report
        self.written: set[str] = set()
        self.kinds: dict[str, str] = {}

    def fetch(self, method: str, path: str, body: Any = None) -> bytes:
        status, data = self.call(method, path, body)
        if status != 200:
            try:
                message = json.loads(data).get("message", "")
            except ValueError:
                message = data[:200].decode("utf-8", "replace")
            raise ExportError(f"{method} {path} answered {status}: {message}")
        return data

    def save(self, rel: str, data: bytes, kind: str) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel in self.written:  # an overwrite in this export replaces the earlier count
            self.report.add(kind, -path.stat().st_size, -1)
        data = _maybe_pack(rel, data)
        path.write_bytes(data)
        self.written.add(rel)
        self.kinds[rel] = kind
        self.report.add(kind, len(data))

    def drop(self, rel: str, kind: str) -> None:
        path = self.out / rel
        if path.is_file():
            if rel in self.written:
                self.report.add(kind, -path.stat().st_size, -1)
                self.written.discard(rel)
            path.unlink()

    def share(self, code: str) -> None:
        """Run :func:`share_state_pool` on this state and keep the size table right."""
        for rel, (old, new) in share_state_pool(self.out / code).items():
            full = f"{code}/{rel}"
            kind = self.kinds.get(full, "shared pool")
            if full not in self.written and new:
                self.written.add(full)
                self.kinds[full] = kind
                self.report.add(kind, new)
            elif not new:
                self.written.discard(full)
                self.report.add(kind, -old, -1)
            else:
                self.report.add(kind, new - old, 0)

    def get(self, rel: str, path: str, kind: str) -> Any:
        data = self.fetch("GET", path)
        self.save(rel, data, kind)
        return json.loads(data)

    def post(self, rel: str, path: str, body: Any, kind: str) -> Any:
        data = self.fetch("POST", path, body)
        self.save(rel, data, kind)
        return json.loads(data)


def _scenarios() -> dict[str, list[str]]:
    """The exported runs: the baseline, each benchmark reform alone, and the package."""
    out: dict[str, list[str]] = {"baseline": []}
    out.update({key: [key] for key in BENCHMARK_PACKAGE})
    out["package"] = list(BENCHMARK_PACKAGE)
    return out


def _clear_state_folder(folder: Path) -> None:
    """Remove an earlier export of this state, but only a folder this module wrote."""
    if folder.is_dir():
        if not (folder / "index.json").is_file():
            raise ExportError(
                f"{folder} exists but is not an earlier export (no index.json); "
                "move it away or pick another --out"
            )
        shutil.rmtree(folder)


def export_static(
    state: str,
    out: Path,
    *,
    packs_dir: Path | None = None,
    seed: int = 0,
    draws: int | None = None,
    tracts: str = "auto",
    tract_budget_mb: float = DEFAULT_TRACT_BUDGET_MB,
    explain: str = "auto",
    tract_cache: Path | None = None,
    progress: Callable[[str], None] | None = None,
    comparisons: bool = True,
    extras: bool = False,
    law_changes: bool = False,
    budget_grid: bool = False,
    alternatives: bool = False,
) -> ExportReport:
    """Write the static-site files for ``state`` into ``out``.

    Args:
        state: Two-letter code (``TL`` for Testland).
        out: The data folder, normally ``web/public/data``.
        packs_dir: Built state packs (default ``data/packs``).
        seed: Random seed of the saved runs (the interface's default is 0).
        draws: Monte Carlo draws (default: the ledger default, as the interface uses).
        tracts: ``auto`` saves tract profiles whole while they fit the budget and
            compact (one template plus one file per county) when they do not; ``yes``
            always saves them whole, ``compact`` always compact, ``no`` leaves them out.
        tract_budget_mb: The budget for ``auto``.
        explain: ``yes`` saves each run with its explanation (formulas, inputs and
            sources for every number), as the interface asks for it; ``no`` saves
            the runs without it (much smaller); ``auto`` (default) keeps it unless
            the folder would be over the size budget even without tract profiles.
        tract_cache: A folder (git-ignored, such as ``data/cache/tract-profiles``) that keeps
            each county's tract replies as they arrive, so a stopped export resumes; ``None``
            keeps nothing (see :func:`fetch_tract_profiles`).
        progress: Called with one short line per county while tract profiles are fetched
            (the command line prints them); ``None`` is quiet.
        comparisons: Save the comparisons parts (``ST/comparisons/{part}.json``). They take
            minutes on a large state unless ``visionhousing comparisons --warm`` ran first.
        extras: Also save each extra scenario alone (as the state bills are saved).
        law_changes: Also save "What this law changes" per reform
            (``ST/law_changes/{key}.json``, ``law_changes`` in the index); each file holds the
            read-next bounds only when they are already in the comparisons disk cache.
        budget_grid: Also save the ADU budget-and-target grid (``ST/adu-budget-grid.json``,
            ``adu_budget_grid`` in the index), from the disk cache only (warm it with
            ``visionhousing adu-budget-grid --state ST``); left out with a note if not cached.
        alternatives: Also save the "Alternatives" table of the five benchmark reforms
            (``ST/alternatives.json``, ``alternatives`` in the index; compact: the P10 / P90
            numbers and standard errors are left out, their text kept), from the disk cache
            only (warm it with ``python -m visionhousing.api.alternatives --state ST``); left
            out with a note if not cached. No size step ever drops it.

    Returns:
        The report: files and bytes per kind, and notes.

    Raises:
        ExportError: The back end refused a request (for example, no pack for the state).
    """
    from visionhousing.api import create_app
    from visionhousing.ledger import DEFAULT_DRAWS

    code = state.upper()
    draws = draws or DEFAULT_DRAWS
    out = Path(out)
    report = ExportReport(code, out)
    # One whole run held at a time, whatever its size (the export asks for each run's
    # replies one after another), and the export's own seed and draws on every address.
    app = create_app(
        packs_dir=packs_dir, web_dir=out / ".no-web", workers=1,
        cache_size=1, result_mb=float("inf"), any_sampling=True,
    )  # fmt: skip
    w = _Writer(_Caller(app), out, report)
    base = {"state": code, "seed": seed, "draws": draws}

    # Check the state exists before anything is removed.
    w.fetch("GET", f"{API}/states/{code}/summary")
    _clear_state_folder(out / code)
    out.mkdir(parents=True, exist_ok=True)

    # Shared files: reforms, the ledger, the source list.
    reforms = w.get("reforms.json", f"{API}/reforms", "reforms")
    for r in reforms["reforms"]:
        w.get(f"reforms/{r['key']}.json", f"{API}/reforms/{r['key']}", "reforms")
    w.get("ledger.json", f"{API}/ledger", "ledger")
    w.get("provenance.json", f"{API}/provenance", "provenance")
    w.get("evidence.json", f"{API}/evidence", "evidence")

    # This state.
    summary = w.get(f"{code}/summary.json", f"{API}/states/{code}/summary", "summary")
    w.save("hearing.css", w.fetch("GET", f"{API}/export/hearing.css"), "exports")
    has_card = bool(summary.get("baseline_card"))
    if has_card:
        card = json.dumps(summary["baseline_card"], indent=1, ensure_ascii=False)
        w.save(f"{code}/baseline-card.json", card.encode("utf-8"), "summary")
    w.get(f"{code}/ledger.json", f"{API}/ledger?state={code}", "ledger")
    w.get(f"{code}/provenance.json", f"{API}/provenance?state={code}", "provenance")
    w.get(f"{code}/validation.json", f"{API}/validation?state={code}", "validation")
    w.get(f"{code}/report-card.json", f"{API}/report-card?state={code}", "report card")
    w.get(f"{code}/outlines.json", f"{API}/states/{code}/outlines", "outlines")
    w.get(f"{code}/tract-points.json", f"{API}/states/{code}/tract-points", "tract columns")
    w.get(f"{code}/permit-years.json", f"{API}/states/{code}/permit-years", "permit years")
    runs = _scenarios()
    extra = {key: [key] for key in STATE_BILLS.get(code, ())}
    if extras:
        extra.update({r["key"]: [r["key"]] for r in reforms["reforms"] if r.get("kind") == "extra"})
    every = {**runs, **extra}

    def save_runs(with_explanation: bool) -> None:
        for key, keys in every.items():
            # A reform alone also carries its top drivers (the Reform screen shows them).
            drivers = with_explanation and len(keys) == 1
            body = {**base, "reforms": keys, "explain": with_explanation, "drivers": drivers}
            w.post(f"{code}/runs/{key}.json", f"{API}/run", body, "runs")

    save_runs(explain != "no")
    for key, keys in runs.items():
        w.post(f"{code}/compare/{key}.json", f"{API}/compare", {**base, "reforms": keys},
               "compare")  # fmt: skip
    funnels: list[str] = []
    briefs = list(every)
    for key, keys in every.items():
        query = urlencode({"s": code, "r": ",".join(keys), "seed": seed, "draws": draws})
        w.get(f"{code}/maps/{key}.json", f"{API}/states/{code}/map?{query}", "maps")
        w.get(f"{code}/tracts/{key}.json", f"{API}/states/{code}/tracts?{query}", "tract columns")
        if len(keys) == 1:
            funnel = w.fetch("GET", f"{API}/states/{code}/tract-funnel?{query}")
            if json.loads(funnel)["funnel"]:
                w.save(f"{code}/tract-funnel/{key}.json", funnel, "tract columns")
                funnels.append(key)
        for fmt in ("csv", "html"):
            w.save(f"{code}/exports/{key}.{fmt}", w.fetch("GET", f"{API}/export/{fmt}?{query}"),
                   "exports")  # fmt: skip
        # The hearing brief links its style rules; here they sit at data/hearing.css.
        page = w.fetch("GET", f"{API}/export/hearing?{query}").replace(
            b'href="hearing.css"', b'href="../../hearing.css"'
        )
        w.save(f"{code}/exports/{key}.hearing.html", page, "exports")

    # Counties: one panel each (baseline, every reform and the package), and a profile.
    places = w.get(
        f"{code}/places/counties.json", f"{API}/places/{code}?level=county&limit=500", "places"
    )
    counties = [p["geoid"] for p in places["places"]]
    query = urlencode({"r": ",".join(BENCHMARK_PACKAGE), "seed": seed, "draws": draws})
    county_bodies: list[tuple[str, bytes]] = []
    for fips in counties:
        w.get(f"{code}/counties/{fips}.json", f"{API}/states/{code}/counties/{fips}?{query}",
              "counties")  # fmt: skip
        county_bodies.append((fips, w.fetch("GET", f"{API}/profile/{code}/{fips}")))
    county_format = _save_county_profiles(w, code, county_bodies)
    flags = _export_cards(w, code, every, seed=seed, draws=draws, comparisons=comparisons)
    if law_changes:
        flags["law_changes"] = _export_law_changes(w, app, code, seed=seed, draws=draws)
    if budget_grid:
        flags["adu_budget_grid"] = _export_budget_grid(w, app, code, seed=seed, draws=draws)
    if alternatives:
        flags["alternatives"] = _export_alternatives(w, app, code, seed=seed, draws=draws)

    cache = None
    if tract_cache is not None and county_bodies:
        cache = Path(tract_cache) / code / _cache_key(json.loads(county_bodies[0][1]))
    tract_files = _export_tracts(
        w, code, counties, tracts, tract_budget_mb, cache=cache, progress=progress
    )
    report.tract_profiles = tract_files is not None
    explained = explain != "no"
    w.share(code)

    # Over the budget: first the switch ranges' county records, then tract profiles (auto),
    # then run explanations (auto).
    why = over_limit(out)
    # Optional extras go before anything the switch ranges, tract or run screens need: the
    # read-next bounds, then the hearing briefs other than the package and each benchmark.
    if why and flags.get("law_changes"):
        flags["law_changes"] = _export_law_changes(
            w, app, code, seed=seed, draws=draws, read_next=False
        )
        w.share(code)
        report.notes.append(f"law changes saved without the read-next bounds ({why})")
        why = over_limit(out)
    if why:
        keep = {"package", *BENCHMARK_PACKAGE}
        for key in [k for k in briefs if k not in keep]:
            w.drop(f"{code}/exports/{key}.hearing.html", "exports")
        briefs = [k for k in briefs if k in keep]
        report.notes.append(f"hearing briefs kept for the package and each benchmark only ({why})")
        why = over_limit(out)
    if why and flags.get("switch_ranges"):
        _switch_ranges_state_only(w, code, flags["switch_ranges"])
        w.share(code)
        flags["switch_ranges_counties"] = False
        report.notes.append(f"switch ranges saved without county records ({why})")
        why = over_limit(out)
    if why and tracts == "auto" and tract_files:
        for rel, kind in tract_files:
            w.drop(rel, kind)
        w.share(code)
        report.tract_profiles = False
        report.notes.append(f"tract profiles left out to stay within the size budget ({why})")
    if over_limit(out) and explain == "auto":
        save_runs(False)
        w.share(code)
        explained = False
        report.notes.append(
            "runs saved without their explanations to stay within the size budget; "
            "the local app (make app) shows them"
        )

    index = {
        "schema_version": STATIC_SCHEMA_VERSION,
        "state": code,
        "seed": seed,
        "draws": draws,
        "package": list(BENCHMARK_PACKAGE),
        "runs": list(runs),
        "extra_runs": list(extra),
        "compare": list(runs),
        "maps": list(every),
        "tract_points": True,
        "permit_years": True,
        "tracts": list(every),
        "tract_funnels": funnels,
        "exports": list(every),
        "hearing_briefs": briefs,
        "baseline_card": has_card,
        "explanations": explained,
        "drivers": explained,
        "counties": counties,
        "tract_profiles": report.tract_profiles,
        "tract_profile_format": report.tract_format if report.tract_profiles else "",
        "county_profile_format": county_format,
        **flags,
        "note": "Default assumptions only. Changing assumptions needs the local app (make app).",
    }
    w.save(f"{code}/index.json", json.dumps(index, indent=1).encode("utf-8"), "index")
    _merge_states(w, code)
    big = sorted(
        p.relative_to(out).as_posix()
        for folder in (out / code / "tracts", out / code / "tract-funnel")
        if folder.is_dir()
        for p in folder.glob("*.json")
        if p.stat().st_size > TRACT_FILE_BUDGET_BYTES
    )
    if big:
        report.notes.append(
            f"tract-column files over {TRACT_FILE_BUDGET_BYTES // 1024} KB: " + ", ".join(big)
        )
    why = over_limit(out)
    if why:
        report.notes.append(
            f"the folder is over the static-site limit ({why}); "
            "export with --tracts no --explain no, or fewer states"
        )
    return report


def _try(w: _Writer, rel: str, path: str, kind: str) -> bool:
    """Save one reply; ``False`` (with a note) when the back end refused it."""
    try:
        w.get(rel, path, kind)
    except ExportError as err:
        w.report.notes.append(f"{rel} not saved: {err}")
        return False
    return True


def _with_reforms(keys: list[str], sampling: dict[str, int]) -> dict[str, Any]:
    """A query's reforms and sampling; the baseline (no reform) leaves ``r`` out."""
    return {"r": ",".join(keys), **sampling} if keys else dict(sampling)


def _export_cards(
    w: _Writer, code: str, every: dict[str, list[str]], *, seed: int, draws: int, comparisons: bool
) -> dict[str, Any]:
    """Save the comparisons, the explanation cards, the grades and the districts.

    Returns the index flags: each is true only when its files were all saved, so the
    static site never asks for a file that is not there.
    """
    from visionhousing.engine.comparisons import PARTS

    sampling = {"seed": seed, "draws": draws}
    flags: dict[str, Any] = {}

    if comparisons:
        flags["comparisons"] = all(
            [
                _try(
                    w,
                    f"{code}/comparisons/{part}.json",
                    f"{API}/states/{code}/comparisons?{urlencode({'part': part, **sampling})}",
                    "comparisons",
                )
                for part in PARTS
            ]
        )
    else:
        flags["comparisons"] = False
        w.report.notes.append("comparisons left out (--comparisons no)")

    flags["confidence"] = _try(w, f"{code}/confidence.json", f"{API}/confidence/{code}", "cards")
    flags["plausibility"] = _try(
        w, f"{code}/plausibility.json", f"{API}/plausibility/{code}?{urlencode(sampling)}", "cards"
    )
    flags["lever"] = all(
        [
            _try(
                w,
                f"{code}/lever/{key}.json",
                f"{API}/lever/{code}?{urlencode({'r': key, **sampling})}",
                "cards",
            )
            for key in BENCHMARK_PACKAGE
        ]
    )
    flags["implementation"] = _try(
        w, f"{code}/implementation.json", f"{API}/implementation/{code}", "cards"
    )
    graded = {"baseline": [], **{k: [k] for k in BENCHMARK_PACKAGE},
              "package": list(BENCHMARK_PACKAGE)}  # fmt: skip
    flags["grades"] = all(
        [
            _try(
                w,
                f"{code}/grades/{key}.json",
                f"{API}/grades/{code}?{urlencode(_with_reforms(keys, sampling))}",
                "grades",
            )
            for key, keys in graded.items()
        ]
    )

    # Districts: only for a state whose pack holds district outlines (both chambers). Each
    # run is asked for in both chambers one after the other, so the run is made once.
    chambers: list[str] = []
    for chamber in ("lower", "upper"):
        status, data = w.call("GET", f"{API}/states/{code}/district-outlines?chamber={chamber}")
        if status == 200 and json.loads(data).get("features"):
            w.save(f"{code}/districts/outlines-{chamber}.json", data, "districts")
            chambers.append(chamber)
    failed: set[str] = set()
    for key, keys in every.items():
        for chamber in chambers:
            rel = f"{code}/districts/{chamber}/{key}.json"
            query = urlencode({"chamber": chamber, **_with_reforms(keys, sampling)})
            if not _try(w, rel, f"{API}/states/{code}/districts?{query}", "districts"):
                failed.add(chamber)
    chambers = [c for c in chambers if c not in failed]
    flags["districts"] = chambers == ["lower", "upper"]
    flags["district_chambers"] = chambers
    if not chambers:
        w.report.notes.append(f"no district outlines for {code}; district view left out")
    flags["switch_ranges"] = _export_switch_ranges(w, code, seed=seed, draws=draws)
    flags["switch_ranges_counties"] = bool(flags["switch_ranges"])
    return flags


def _contexts(app: Any) -> Any:
    """The app's state contexts (``app.state.contexts``), under any middleware wrappers."""
    inner = app
    while not hasattr(inner, "state") and hasattr(inner, "app"):
        inner = inner.app
    return inner.state.contexts


def _export_law_changes(
    w: _Writer, app: Any, code: str, *, seed: int, draws: int, read_next: bool = True
) -> list[str]:
    """Save "What this law changes" for each reform the state can show.

    Each file is ``{changes, read_next}`` as :func:`visionhousing.api.law_changes.write_static`
    writes it: the changes come from the reform's own default run (same seed and draws as the
    saved runs); the read-next bounds only from the disk cache (no engine run here), and not
    at all with ``read_next=False``. Returns the keys saved (``law_changes`` in the index).
    """
    from visionhousing.api import law_changes as lc
    from visionhousing.engine.core import run

    try:
        ctx = _contexts(app).get(code)
        tables = lc.read_tables(_contexts(app).folder(code))
        settings = lc.load_settings()
        keys = lc._keys_for(ctx, settings, tables)
        folder = settings["static"]["folder"]
        saved = []
        for key in keys:
            res = run(ctx.state, [key], seed=seed, draws=draws, context=ctx)
            body: dict[str, Any] = {
                "changes": lc.build_payload(ctx, key, tables, res, settings=settings)
            }
            if read_next:
                body["read_next"] = lc.read_next_cached(ctx, key, tables, None, compute=False)
            w.save(f"{code}/{folder}/{key}.json", lc._dumps(body), "law changes")
            saved.append(key)
    except (FileNotFoundError, KeyError, ValueError) as err:
        w.report.notes.append(f"law changes not saved: {err}")
        return []
    return saved


def _export_budget_grid(w: _Writer, app: Any, code: str, *, seed: int, draws: int) -> bool:
    """Save the ADU budget-and-target grid from the disk cache (``ST/adu-budget-grid.json``)."""
    from visionhousing.api.budget_grid import grid_payload
    from visionhousing.api.budget_view import STATIC_FILE, grid_view
    from visionhousing.api.comparison_cache import _clean

    try:
        grid = grid_payload(_contexts(app).get(code), seed=seed, draws=draws, compute=False)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as err:
        w.report.notes.append(f"ADU budget grid not saved: {err}")
        return False
    if grid is None:
        w.report.notes.append(
            f"ADU budget grid not cached for {code} (visionhousing adu-budget-grid); left out"
        )
        return False
    body = _clean({"grid": grid, "view": grid_view(grid)})
    text = json.dumps(body, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    w.save(f"{code}/{STATIC_FILE}", text.encode("utf-8"), "budget grid")
    return True


def _export_alternatives(w: _Writer, app: Any, code: str, *, seed: int, draws: int) -> bool:
    """Save the Alternatives table from the disk cache (``ST/alternatives.json``, compact)."""
    from visionhousing.api import alternatives as alt

    try:
        body = alt.payload(_contexts(app).get(code), seed=seed, draws=draws, compute=False)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as err:
        w.report.notes.append(f"alternatives not saved: {err}")
        return False
    if not body:
        w.report.notes.append(
            f"alternatives not cached for {code} "
            "(python -m visionhousing.api.alternatives); left out"
        )
        return False
    data = alt.compact(alt._clean(dict(body)))
    text = json.dumps(data, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    w.save(f"{code}/{alt.STATIC_FILE}", text.encode("utf-8"), "alternatives")
    return True


def _export_switch_ranges(w: _Writer, code: str, *, seed: int, draws: int) -> list[str]:
    """Save each benchmark reform's switch ranges (``ST/switch-ranges/{key}.json``).

    They come from the engine's cache only (``visionhousing switch-ranges --state ST`` fills
    it); a reform with nothing cached is left out. Returns the keys saved.
    """
    from visionhousing.sensitivity.switch_runs import switch_payload

    saved: list[str] = []
    for key in BENCHMARK_PACKAGE:
        try:
            # The app's own context: the pack is read from the folder the app was given.
            ctx = _contexts(w.call.app).get(code)
            body = switch_payload(code, key, seed=seed, draws=draws, compute=False, context=ctx)
        except Exception as err:  # a missing pack or cache only leaves it out
            w.report.notes.append(f"switch ranges for {key} not saved: {err}")
            continue
        if body is None:
            continue
        w.save(f"{code}/switch-ranges/{key}.json", _dumps(body).encode("utf-8"), "switch ranges")
        saved.append(key)
    if not saved:
        w.report.notes.append(
            f"no switch ranges cached for {code} (visionhousing switch-ranges --state {code})"
        )
    return saved


def _switch_ranges_state_only(w: _Writer, code: str, keys: list[str]) -> None:
    """Keep only the state records of the saved switch ranges (no county records)."""
    for key in keys:
        rel = f"{code}/switch-ranges/{key}.json"
        body = read_saved(w.out / rel)
        for driver in body.get("drivers", []):
            for setting in (driver.get("settings") or {}).values():
                setting.pop("counties", None)
        w.save(rel, _dumps(body).encode("utf-8"), "switch ranges")


def _save_county_profiles(w: _Writer, code: str, bodies: list[tuple[str, bytes]]) -> str:
    """Save the county profiles whole or compact, whichever is smaller compressed.

    Compact is one template and one file holding every county's values (the tract format of
    :mod:`visionhousing.compact_profiles`, with all counties as its "tracts"); each county
    is rebuilt from it and checked against the live reply. Returns ``"full"`` or
    ``"compact"`` (``index.json`` says which, as ``county_profile_format``).
    """
    from visionhousing.compact_profiles import TemplateBuilder, encode_county

    if not bodies:
        return "full"
    whole = {f"{code}/profiles/{fips}.json": _maybe_pack(f"{code}/profiles/{fips}.json", body)
             for fips, body in bodies}  # fmt: skip
    builder = TemplateBuilder()
    profiles = [json.loads(body) for _, body in bodies]
    for profile in profiles:
        builder.add(profile)
    template = _dumps(builder.template()).encode("utf-8")
    values = encode_county(builder, [fips for fips, _ in bodies], profiles)
    data = _dumps(values).encode("utf-8")
    compact_gz = gzip_size(template) + gzip_size(data)
    whole_gz = sum(gzip_size(b) for b in whole.values())
    if len(bodies) < 2 or compact_gz >= whole_gz:
        for rel, body in whole.items():
            w.save(rel, body, "profiles (county)")
        return "full"
    w.save(f"{code}/profiles/{COUNTY_TEMPLATE}", template, "profiles (county, compact)")
    w.save(f"{code}/profiles/{COUNTY_VALUES}", data, "profiles (county, compact)")
    w.report.notes.append(
        f"county profiles saved compact: {compact_gz / 2**20:.2f} MB compressed "
        f"against {whole_gz / 2**20:.2f} MB whole"
    )
    if values["raw"]:
        w.report.notes.append(f"{len(values['raw'])} county profiles did not fit the template")
    return "compact"


def _cache_key(
    profile: dict[str, Any], *, package: Path | None = None, assumptions: Path | None = None
) -> str:
    """The tract cache's folder name: it changes when anything a reply depends on changes.

    A tract profile depends on the state pack (its fingerprint, ``pack_hash``), the code
    (the same fingerprint a run carries, :func:`visionhousing.engine.hashes.code_hash`),
    every assumption file (``assumptions/**/*.yaml``) and the files under the package's
    ``profile/``, ``law/`` and ``features/`` folders plus ``data/sources.yaml`` (read
    directly, so a data file next to the code counts too). Any change gives a new folder,
    so an old cache is never read by mistake; folders under old keys are simply unused.

    Args:
        profile: One live profile reply (its ``pack_hash`` is read).
        package: The package folder (default: the installed ``visionhousing``).
        assumptions: The assumptions folder (default: the repository's ``assumptions/``).
    """
    from visionhousing.engine.hashes import code_hash
    from visionhousing.ledger.loader import ROOT

    here = Path(package) if package is not None else Path(__file__).resolve().parent
    assume = Path(assumptions) if assumptions is not None else ROOT / "assumptions"
    digest = hashlib.sha256(b"tract-cache-v2\0")
    digest.update(str(profile.get("pack_hash")).encode() + b"\0")
    digest.update(code_hash(package).encode() + b"\0")
    files = [("assumptions", p) for p in sorted(assume.rglob("*.yaml"))]
    for name in ("profile", "law", "features"):
        files += [
            (name, p) for p in sorted((here / name).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        ]  # fmt: skip
    files.append(("data", here / "data" / "sources.yaml"))
    for base, path in files:
        if path.is_file():
            root = assume if base == "assumptions" else here
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def fetch_tract_profiles(
    fetch: Callable[[str], bytes],
    code: str,
    counties: list[str],
    *,
    cache: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, tuple[bytes, list[tuple[str, bytes]]]]:
    """Ask for every county's tract list and every tract's profile; keep them as they arrive.

    Args:
        fetch: Returns the reply to a GET address (the export's in-memory app).
        code: The state.
        counties: The county codes, in order.
        cache: A folder for one file per county, ``{fips}.json.gz``: the tract list and
            each tract's reply, exactly as sent, gzip-compressed. A county already there is
            read back instead of asked for again, so an export that was stopped resumes.
            The folder name should hold :func:`_cache_key`, so a changed pack or profile
            code never reads old replies. ``None`` keeps nothing.
        progress: Called with one line per county ("PA tract profiles: county 42001
            (1 of 67), 27 tracts, 27 of the state so far, 0:12").

    Returns:
        County code to (its tract list reply, [(geoid, zlib-compressed profile reply)]).
    """
    out: dict[str, tuple[bytes, list[tuple[str, bytes]]]] = {}
    start = time.monotonic()
    done = 0
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
    for n, fips in enumerate(counties, 1):
        path = cache / f"{fips}.json.gz" if cache is not None else None
        source = "asked"
        if path is not None and path.is_file():
            saved = json.loads(gzip.decompress(path.read_bytes()))
            listing = saved["list"].encode("utf-8")
            items = [(g, zlib.compress(t.encode("utf-8"))) for g, t in saved["items"]]
            source = "from cache"
        else:
            listing = fetch(f"{API}/places/{code}?level=tract&county={fips}&limit=500")
            listed = json.loads(listing)
            if listed.get("total") != len(listed["places"]):
                raise ExportError(
                    f"{code} county {fips}: the tract list was cut at {len(listed['places'])} "
                    f"of {listed.get('total')} tracts; the static site would miss some"
                )
            items = []
            plain: list[list[str]] = []
            for p in listed["places"]:
                if p["geoid"][:5] != fips:
                    raise ExportError(f"{code} county {fips}: tract {p['geoid']} is not in it")
                body = fetch(f"{API}/profile/{code}/{p['geoid']}")
                items.append((p["geoid"], zlib.compress(body)))
                plain.append([p["geoid"], body.decode("utf-8")])
            if path is not None:
                text = _dumps({"list": listing.decode("utf-8"), "items": plain})
                part = path.with_suffix(".part")
                part.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
                part.replace(path)
        out[fips] = (listing, items)
        done += len(items)
        if progress is not None:
            spent = int(time.monotonic() - start)
            progress(
                f"{code} tract profiles: county {fips} ({n} of {len(counties)}), "
                f"{len(items)} tracts {source}, {done:,} so far, {spent // 60}:{spent % 60:02d}"
            )
    return out


def print_progress(line: str) -> None:
    """Print one progress line to the terminal's error stream (the command line uses it)."""
    print(line, file=sys.stderr, flush=True)


def _export_tracts(
    w: _Writer,
    code: str,
    counties: list[str],
    mode: str,
    budget_mb: float,
    *,
    cache: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[tuple[str, str]] | None:
    """Save tract lists and tract profiles; ``None`` if left out (``no``).

    ``yes`` saves every profile whole; ``compact`` saves them as one template and one file
    per county (see :mod:`visionhousing.compact_profiles`); ``auto`` saves them whole while
    they fit the budget, else compact. Returns the saved files as ``(path, kind)`` so they
    can be dropped later.
    """
    from visionhousing.compact_profiles import TemplateBuilder, encode_county

    if mode == "no":
        w.report.notes.append("tract profiles left out (--tracts no)")
        return None
    budget = int(budget_mb * 1024 * 1024)
    lists: list[tuple[str, bytes]] = []
    bodies: dict[str, list[tuple[str, bytes]]] = {}  # county -> (geoid, compressed reply)
    builder = TemplateBuilder()
    used = 0
    fetched = fetch_tract_profiles(
        lambda path: w.fetch("GET", path), code, counties, cache=cache, progress=progress
    )
    for fips, (data, items) in fetched.items():
        lists.append((f"{code}/places/tracts-{fips}.json", data))
        bodies[fips] = items
        for _, packed in items:
            body = zlib.decompress(packed)
            used += len(body)
            if mode != "yes":
                builder.add(json.loads(body))
    compact = mode == "compact" or (mode == "auto" and used > budget)
    saved: list[tuple[str, str]] = []
    for rel, data in lists:
        w.save(rel, data, "places (tracts)")
        saved.append((rel, "places (tracts)"))
    if not compact:
        for items in bodies.values():
            for geoid, packed in items:
                rel = f"{code}/profiles/{geoid}.json"
                w.save(rel, zlib.decompress(packed), "profiles (tract)")
                saved.append((rel, "profiles (tract)"))
        w.report.tract_format = "full"
        return saved
    if mode == "auto":
        w.report.notes.append(
            f"tract profiles saved compact: {used / 2**20:.1f} MB whole is over the "
            f"{budget_mb:g} MB budget"
        )
    rel = f"{code}/profiles/{TRACT_TEMPLATE}"
    w.save(rel, _dumps(builder.template()).encode("utf-8"), "profiles (tract template)")
    saved.append((rel, "profiles (tract template)"))
    whole = 0
    for fips, items in bodies.items():
        if not items:
            continue
        geoids = [g for g, _ in items]
        data = encode_county(builder, geoids, [json.loads(zlib.decompress(b)) for _, b in items])
        whole += len(data["raw"])
        rel = f"{code}/profiles/tracts-{fips}.json"
        w.save(rel, _dumps(data).encode("utf-8"), "profiles (tract, compact)")
        saved.append((rel, "profiles (tract, compact)"))
    if whole:
        w.report.notes.append(f"{whole} tract profiles did not fit the template; saved whole")
    w.report.tract_format = "compact"
    return saved


def _merge_states(w: _Writer, code: str) -> None:
    """Write ``states.json``: every state exported into this folder, this one included."""
    live = json.loads(w.fetch("GET", f"{API}/states"))["states"]
    entry = next((s for s in live if s["code"] == code), {"code": code, "name": code})
    path = w.out / "states.json"
    kept: list[dict[str, Any]] = []
    if path.is_file():
        try:
            kept = json.loads(path.read_text(encoding="utf-8")).get("states", [])
        except ValueError:
            kept = []
    kept = [
        s
        for s in kept
        if s.get("code") != code and (w.out / s.get("code", "") / "index.json").is_file()
    ]
    states = sorted([*kept, entry], key=lambda s: s["code"])
    w.save("states.json", json.dumps({"states": states}).encode("utf-8"), "states")
