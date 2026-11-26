"""Reproduce this number: the command, seed and fingerprints behind any record id.

Plain-language summary
----------------------
Every number the engine shows has an id: its path in the run's JSON (see
:mod:`visionhousing.explain.ids`), for example
``reform_results.adu.added``. :func:`reproduce` takes a full record id::

    PA:adu:reform_results.adu.added
    ^  ^   ^
    |  |   the record's path in the run
    |  the saved run (baseline, adu, missing_middle, tod, parking, fees, package, ...)
    the state

and answers with what someone needs to make the same number again, read from
the run's own metadata (nothing is recomputed):

- ``command``: the command line that writes the same run
  (``visionhousing report ... --format json``), and ``api_request``, the same
  run as a ``POST /api/v1/run`` body (needed when the run used ledger
  overrides or a non-default housing cycle, which the command line does not
  take);
- ``seed``, ``draws``, ``cycle``, ``reforms``, ``overrides``;
- ``hashes``: the run's code, data and assumptions fingerprints
  (:mod:`visionhousing.engine.hashes`);
- ``pack_hash``: the hash in the state pack's ``pack_manifest.json`` today;
- ``code_version``: the package version;
- ``record``: the number itself (name, unit, P10 / P50 / P90 or value).

The run is read from the static site's saved runs
(``web/public/data/<ST>/runs/<run>.json``). When the id is short
(``PA:reform_results.adu.added``) the run is the one the path names
(``reform_results.<key>`` -> ``<key>``; ``baseline...`` -> ``baseline``;
``package...``, ``total`` and ``annual...`` -> ``package``). A caller holding a
fresh run (the interface, Testland in the tests) passes it as ``run_data``.

Two runs with the same three hashes, reforms, overrides, seed and draws give
the same numbers. If the run's data hash differs from a fresh load of the pack,
the pack was rebuilt since: the command still runs, but on the newer data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from visionhousing import __version__
from visionhousing.paths import safe_child

__all__ = ["RUNS_DIR", "ReproduceError", "parse_id", "reproduce", "saved_run"]

RUNS_DIR = Path(__file__).resolve().parents[3] / "web" / "public" / "data"
"""Where the static site keeps each state's saved runs."""

_PRESET = {"weak": "low", "typical": "central", "strong": "high"}
"""Housing cycle to the interface preset that selects it (``api.models.PRESET_CYCLE``)."""

_PART = re.compile(r"^[A-Za-z0-9_.>\- ]{1,200}$")


class ReproduceError(ValueError):
    """The id is malformed, or no saved run or record matches it."""


def parse_id(record_id: str) -> tuple[str, str, str]:
    """Split ``ST:run:path`` (or ``ST:path``) into ``(state, run, path)``.

    Raises:
        ReproduceError: The id is malformed.
    """
    parts = record_id.strip().split(":")
    if len(parts) == 2:
        state, path = parts
        run = _run_for_path(path)
    elif len(parts) == 3:
        state, run, path = parts
    else:
        raise ReproduceError(
            "an id is STATE:run:path or STATE:path, for example PA:adu:reform_results.adu.added"
        )
    state = state.upper()
    if not re.fullmatch(r"[A-Z]{2}", state):
        raise ReproduceError("the id must start with a two-letter state code")
    if not re.fullmatch(r"[a-z0-9_]{1,40}", run):
        raise ReproduceError("the run name must be a short id (letters, digits, underscores)")
    if not _PART.match(path):
        raise ReproduceError("the record path has characters a record id never has")
    return state, run, path


def _run_for_path(path: str) -> str:
    head = path.split(".")
    if head[0] == "reform_results" and len(head) > 1:
        return head[1]
    if head[0] in ("baseline", "baseline_counties"):
        return "baseline"
    if head[0] == "annual" and len(head) > 1 and head[1] == "baseline":
        return "baseline"
    return "package"


def saved_run(state: str, run: str, runs_dir: Path | None = None) -> tuple[dict[str, Any], str]:
    """Read one saved run of the static site (unpacked), with its path.

    Raises:
        ReproduceError: No such saved run.
    """
    from visionhousing.export_static import read_saved

    folder = Path(runs_dir) if runs_dir is not None else RUNS_DIR
    try:
        path = safe_child(folder, state.upper(), "runs", f"{run}.json")
    except ValueError:
        raise ReproduceError(f"no saved run {str(run)[:40]!r} for {str(state)[:20]!r}") from None
    if not path.is_file():
        raise ReproduceError(f"no saved run '{run}' for {state.upper()}")
    data = read_saved(path)
    try:
        shown = path.resolve().relative_to(RUNS_DIR.parents[2]).as_posix()
    except ValueError:
        shown = path.name
    return data, shown


def _pack_hash(state: str, packs_dir: Path | None) -> str | None:
    from visionhousing.engine.context import PACKS_DIR, TESTLAND

    if state == TESTLAND:
        return None
    try:
        path = safe_child(packs_dir or PACKS_DIR, state, "pack_manifest.json")
    except ValueError:
        return None
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("pack_hash")


def _record(node: Any) -> dict[str, Any]:
    if isinstance(node, Mapping):
        keep = ("name", "unit", "value", "p10", "p50", "p90", "assumption_ids", "source_ids")
        return {k: node[k] for k in keep if k in node}
    return {"value": node}


def _reforms(data: Mapping[str, Any]) -> list[str]:
    reforms = data.get("reforms") or data.get("reform_ids") or []
    if isinstance(reforms, Mapping):
        return list(reforms)
    return [str(r) for r in reforms]


def reproduce(
    record_id: str,
    *,
    run_data: Mapping[str, Any] | None = None,
    source: str | None = None,
    runs_dir: Path | None = None,
    packs_dir: Path | None = None,
) -> dict[str, Any]:
    """The metadata that reproduces one record (see the module notes).

    Args:
        record_id: ``ST:run:path`` or ``ST:path``.
        run_data: A run's ``Result.to_dict()``, when the caller already has it.
        source: Where ``run_data`` came from (shown in the answer).
        runs_dir: Folder of the saved runs (default ``web/public/data``).
        packs_dir: Folder of the state packs (default ``data/packs``).

    Raises:
        ReproduceError: Bad id, no saved run, or no such record in the run.
    """
    from visionhousing.explain.ids import resolve

    state, run, path = parse_id(record_id)
    if run_data is None:
        run_data, source = saved_run(state, run, runs_dir)
    try:
        node = resolve(run_data, path)
    except KeyError as err:
        raise ReproduceError(f"no record {path} in run '{run}' of {state}") from err
    reforms = _reforms(run_data)
    seed, draws = int(run_data.get("seed", 0)), int(run_data.get("draws", 0))
    cycle = str(run_data.get("cycle", "typical"))
    overrides = dict(run_data.get("overrides") or {})
    command = (
        f"uv run visionhousing report --state {state} "
        f"--reforms {','.join(reforms) or 'none'} --seed {seed} --draws {draws} --format json"
    )
    cli_ok = not overrides and cycle == "typical"
    hashes = dict(run_data.get("hashes") or {})
    return {
        "id": f"{state}:{run}:{path}",
        "state": state,
        "run": run,
        "path": path,
        "record": _record(node),
        "command": command if cli_ok else None,
        "command_note": None
        if cli_ok
        else "the run used ledger overrides or a non-default housing cycle; "
        "use api_request (the command line takes neither)",
        "api_request": {
            "method": "POST",
            "url": "/api/v1/run",
            "body": {
                "state": state,
                "reforms": reforms,
                "overrides": overrides,
                "preset": _PRESET.get(cycle, "central"),
                "seed": seed,
                "draws": draws,
                "explain": False,
            },
        },
        "seed": seed,
        "draws": draws,
        "cycle": cycle,
        "reforms": reforms,
        "overrides": overrides,
        "hashes": hashes,
        "pack_hash": _pack_hash(state, packs_dir),
        "code_version": __version__,
        "run_source": source,
        "note": (
            "Same three hashes, reforms, overrides, seed and draws give the same number. "
            "If the code or data hash differs from a run made today, the code or the pack "
            "changed since this run was saved."
        ),
    }
