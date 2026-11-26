"""A disk cache for the comparisons parts, so a server restart does not lose them.

Plain-language summary
----------------------
Each comparisons part (``interactions``, ``dials``, ``matrix``, ``ablation``,
``ranking``, ``market``) takes about one to four minutes for a large state. The
server keeps replies in memory only, so a restart threw that work away. This
module keeps each finished part on disk, one small gzip-compressed JSON file per
part, and serves it again when nothing it depends on has changed.

**The key.** A part is read back only when its key matches. The key is a
SHA-256 fingerprint of everything the part depends on:

- the state's data pack (``data_hash``), the calibration tables and scenario
  file (``calibration_hash``) and the state's ledger (``ledger.hash()``);
- the code (every ``.py`` file of the package, ``engine.hashes.code_hash``);
- an assumptions stamp: the bytes of every ``assumptions/**/*.yaml`` file;
- the seed and the number of draws;
- the part's own options (its name, and no assumption overrides: the web
  address offers none).

Change any of them and the key changes, so an old file is simply never read.

**Where.** ``data/cache/comparisons/<ST>/<fingerprint>/<part>.json.gz``. The
folder ``<fingerprint>`` covers the shared inputs; each file also stores its
full key (shared inputs plus the part's options), checked on every read.
``data/cache`` may be a link to another drive. If that folder does not exist
(for example the drive is not plugged in), the cache switches itself off with
one logged warning and every part is computed as before; a read or write error
does the same for that one file. The cache never stops a reply.

**Safe writes.** A file is written under a temporary name ending in ``.part``
in the same folder and then renamed in one step (``os.replace``), so a reader
never sees half a file and a crash leaves at most a stray ``.part`` file.

**Bounded size.** After each write, files are removed oldest first (by last
use; a read refreshes a file's time) until the folder is under its byte
budget. A single part larger than the whole budget is not kept.

**Settings** (server settings, not model assumptions):
``VISIONHOUSING_COMPARISONS_DIR`` (the folder; ``none`` switches the disk cache
off) and ``VISIONHOUSING_COMPARISONS_DISK_MB`` (the byte budget, 1,024 MB by
default).

``visionhousing comparisons --warm --state PA`` computes every part once
(sharing one prepared bench) and writes them, so a demo starts warm.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import math
import os
import tempfile
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from visionhousing.engine.context import StateContext
from visionhousing.ledger.loader import ROOT

__all__ = [
    "DEFAULT_DIR",
    "DEFAULT_MB",
    "ComparisonDiskCache",
    "assumptions_stamp",
    "cached_comparisons",
    "default_cache",
    "fingerprint",
    "warm",
]

LOG = logging.getLogger("visionhousing.api")

DEFAULT_DIR = ROOT / "data" / "cache" / "comparisons"
"""Default folder: under ``data/cache`` (git-ignored; may link to another drive)."""

DEFAULT_MB = 1024.0
"""Default byte budget of the folder, in MB."""

KEY_VERSION = "visionhousing-comparisons-disk-v1"
"""Bumped when the file layout changes, so older files are never read."""

_WARNED: set[str] = set()
"""Folders already reported as missing (the warning is logged once each)."""

HEADER_KEYS = ("state", "seed", "draws", "synthetic", "hashes", "county_ids")
"""The fields every comparisons reply carries besides its parts."""


# ------------------------------------------------------------------ keys


def _yaml_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.yaml") if p.is_file())


@lru_cache(maxsize=8)
def _stamp(folder: str, signature: tuple) -> str:
    root = Path(folder)
    digest = hashlib.sha256(b"assumptions-stamp-v1\0")
    for rel, _size, _mtime in signature:
        digest.update(rel.encode() + b"\0")
        digest.update((root / rel).read_bytes() + b"\0")
    return digest.hexdigest()


def assumptions_stamp(folder: Path | None = None) -> str:
    """Fingerprint of the bytes of every ``assumptions/**/*.yaml`` file.

    The files are only re-read when one's size or change time moves (the
    hash itself is of their contents, so touching a file does not change it).
    """
    root = Path(folder or ROOT / "assumptions")
    sig = tuple(
        (p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in _yaml_files(root)
    )
    return _stamp(str(root), sig)


def fingerprint(
    ctx: StateContext, *, seed: int, draws: int, assumptions: Path | None = None
) -> str:
    """The shared part of the key: pack, calibration, ledger, code, assumptions, seed, draws."""
    from visionhousing.engine.hashes import code_hash

    raw = json.dumps(
        {
            "v": KEY_VERSION,
            "state": ctx.state,
            "data": ctx.data_hash,
            "calibration": ctx.calibration_hash,
            "ledger": ctx.ledger.hash(),
            "code": code_hash(),
            "assumptions": assumptions_stamp(assumptions),
            "seed": int(seed),
            "draws": int(draws),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def part_key(shared: str, part: str, options: dict[str, Any] | None = None) -> str:
    """The full key of one part: the shared fingerprint plus the part's options."""
    raw = json.dumps(
        {"shared": shared, "part": part, "options": options or {"overrides": {}}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# ------------------------------------------------------------------ the cache


class ComparisonDiskCache:
    """Comparison parts kept on disk, keyed by :func:`fingerprint` and :func:`part_key`.

    Args:
        root: The cache folder. Its parent must already exist (it may be a link
            to another drive); the folder itself is created when needed.
        max_bytes: Byte budget of all files; oldest-used files go first.
        assumptions: Assumptions folder for the stamp (default ``assumptions/``).
    """

    def __init__(
        self, root: Path | str, *, max_bytes: int, assumptions: Path | None = None
    ) -> None:
        self.root = Path(root)
        self.max_bytes = int(max_bytes)
        self.assumptions = assumptions

    # ------------------------------------------------------------ state

    def available(self) -> bool:
        """True when the folder can be used; logs one warning when it cannot."""
        try:
            if self.root.is_dir() or self.root.parent.is_dir():
                return True
        except OSError:
            pass
        self._warn(
            f"comparisons disk cache off: {self.root.parent} is not there "
            "(drive not mounted?); parts are computed and kept in memory only"
        )
        return False

    def _warn(self, message: str) -> None:
        if str(self.root) not in _WARNED:  # once per folder per process, not per request
            LOG.warning(message)
            _WARNED.add(str(self.root))

    def path(self, state: str, shared: str, part: str) -> Path:
        """``<root>/<ST>/<fingerprint>/<part>.json.gz``."""
        from visionhousing.paths import safe_child, state_code

        return safe_child(self.root, state_code(state), shared, f"{part}.json.gz")

    # ------------------------------------------------------------ read / write

    def load(self, state: str, shared: str, part: str) -> dict[str, Any] | None:
        """The stored ``{"header", "value"}`` of a part, or ``None`` (missing or stale)."""
        if not self.available():
            return None
        path = self.path(state, shared, part)
        try:
            if not path.is_file():
                return None
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            os.utime(path)  # last use, for the oldest-first trim
        except (OSError, ValueError, EOFError) as err:
            LOG.warning("comparisons disk cache: cannot read %s (%s); recomputing", path, err)
            return None
        if not isinstance(data, dict) or data.get("key") != part_key(shared, part):
            return None
        return {"header": data.get("header", {}), "value": data.get("value")}

    def save(self, state: str, shared: str, part: str, header: dict, value: Any) -> bool:
        """Write one part atomically (``.part`` file, then rename). True when kept."""
        if not self.available():
            return False
        path = self.path(state, shared, part)
        body = json.dumps(
            {"key": part_key(shared, part), "part": part, "header": header, "value": value},
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        blob = gzip.compress(body, compresslevel=6, mtime=0)
        if len(blob) > self.max_bytes:
            LOG.info("comparisons disk cache: %s is larger than the budget; not kept", part)
            return False
        tmp: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{part}.", suffix=".part", dir=path.parent)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            tmp = None
        except OSError as err:
            LOG.warning("comparisons disk cache: cannot write %s (%s); not kept", path, err)
            return False
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        self.trim()
        return True

    def trim(self) -> int:
        """Remove the oldest-used files until the folder fits its budget. Returns bytes freed."""
        try:
            files = [(p, p.stat()) for p in self.root.rglob("*.json.gz") if p.is_file()]
        except OSError:
            return 0
        total = sum(st.st_size for _, st in files)
        freed = 0
        for p, st in sorted(files, key=lambda item: item[1].st_mtime_ns):
            if total <= self.max_bytes:
                break
            try:
                p.unlink()
                total -= st.st_size
                freed += st.st_size
                if not any(p.parent.iterdir()):
                    p.parent.rmdir()
            except OSError:
                continue
        return freed


def default_cache() -> ComparisonDiskCache | None:
    """The cache the server and the command line use, from the settings; ``None`` when off."""
    where = os.environ.get("VISIONHOUSING_COMPARISONS_DIR", "").strip()
    if where.lower() in ("none", "off", "0"):
        return None
    try:
        mb = float(os.environ.get("VISIONHOUSING_COMPARISONS_DISK_MB", DEFAULT_MB))
    except ValueError:
        mb = DEFAULT_MB
    if not math.isfinite(mb) or mb <= 0:
        return None
    return ComparisonDiskCache(Path(where) if where else DEFAULT_DIR, max_bytes=int(mb * 2**20))


# ------------------------------------------------------------------ use


def _clean(value: Any) -> Any:
    """NaN and infinity become ``None`` (JSON cannot hold them), as the API does."""
    from visionhousing.api.service import _clean as clean

    return clean(value)


def cached_comparisons(
    ctx: StateContext,
    parts: Iterable[str],
    *,
    seed: int,
    draws: int,
    cache: ComparisonDiskCache | None = None,
    compute: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The engine's comparisons reply (cleaned of NaN), with each part read from disk if kept.

    Parts not on disk are computed together on one bench (``engine.comparisons``)
    and each is written to disk. With ``cache=None`` this is the plain engine call.

    Args:
        ctx: The loaded state.
        parts: Part names (checked by the engine).
        seed: Seed.
        draws: Monte Carlo draws.
        cache: The disk cache, or ``None``.
        compute: The engine function (tests pass a counting wrapper).

    Returns:
        ``{"state", "seed", "draws", "synthetic", "hashes", "county_ids", <part>: ...}``.
    """
    from visionhousing.engine.comparisons import OPT_IN_PARTS, PARTS
    from visionhousing.engine.comparisons import comparisons as engine_comparisons

    run = compute or engine_comparisons
    wanted = list(dict.fromkeys(parts))
    if cache is None:
        return _clean(run(ctx, wanted, seed=seed, draws=draws))
    shared = fingerprint(ctx, seed=seed, draws=draws, assumptions=cache.assumptions)
    header: dict[str, Any] | None = None
    found: dict[str, Any] = {}
    for part in wanted:
        if part not in (*PARTS, *OPT_IN_PARTS):
            raise ValueError(f"unknown comparison part {part!r}")
        hit = cache.load(ctx.state, shared, part)
        if hit is not None:
            found[part] = hit["value"]
            header = header or hit["header"]
    missing = [p for p in wanted if p not in found]
    if missing:
        fresh = _clean(run(ctx, missing, seed=seed, draws=draws))
        header = {k: fresh[k] for k in HEADER_KEYS if k in fresh}
        for part in missing:
            found[part] = fresh[part]
            cache.save(ctx.state, shared, part, header, fresh[part])
    order = [p for p in (*PARTS, *OPT_IN_PARTS) if p in found]
    return {**(header or {}), **{p: found[p] for p in order}}


def warm(
    state: str,
    parts: Iterable[str] | None = None,
    *,
    seed: int = 0,
    draws: int | None = None,
    cache: ComparisonDiskCache | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Compute every comparisons part for a state once and keep each on disk.

    One bench is prepared and shared by every part; a part already on disk
    with a matching key is skipped. Default parts: all of ``PARTS`` plus ``market``.

    Returns:
        Part name to ``"kept"``, ``"already"`` or ``"not kept"`` (cache off or write failed).

    Raises:
        RuntimeError: The disk cache is off or its folder is not there.
    """
    from visionhousing.engine.comparisons import MARKET_PART, PARTS, Bench, comparisons
    from visionhousing.engine.context import load_context
    from visionhousing.ledger import DEFAULT_DRAWS

    store = cache or default_cache()
    if store is None or not store.available():
        raise RuntimeError(
            "the comparisons disk cache is off or its folder is not there "
            "(check VISIONHOUSING_COMPARISONS_DIR and that data/cache's drive is mounted)"
        )
    n = draws or DEFAULT_DRAWS
    ctx = load_context(state.upper())
    shared = fingerprint(ctx, seed=seed, draws=n, assumptions=store.assumptions)
    say = progress or (lambda _m: None)
    status: dict[str, str] = {}
    bench = None
    for part in list(parts or (*PARTS, MARKET_PART)):
        if store.load(ctx.state, shared, part) is not None:
            status[part] = "already"
            say(f"{part}: already on disk")
            continue
        bench = bench or Bench.build(ctx, seed=seed, draws=n)
        out = _clean(comparisons(ctx, [part], seed=seed, draws=n, bench=bench))
        header = {k: out[k] for k in HEADER_KEYS if k in out}
        ok = store.save(ctx.state, shared, part, header, out[part])
        status[part] = "kept" if ok else "not kept"
        say(f"{part}: {status[part]}")
    return status
