"""Switch off one assumption: each reform's top drivers at low, central and high, ready to flip.

Plain-language summary
----------------------
The tornado (``sensitivity.tornado``) already works out, for every uncertain
input of a reform, the state's five-year total with that one input at its low
value, then at its high value, every other input at its central value. This
module takes the **top three** rows per reform and lays them out as a
three-way switch: ``low`` / ``central`` / ``high``, each with the input's value
and the total it gives. A static site can then show real sensitivity with no
server: flipping the switch just picks one of three stored numbers.

Two sources, same numbers:

- **stored**: the tornado rows the static export already saved with each
  reform's run (``<data>/<ST>/runs/<key>.json``, ``explanation.drivers``), used
  when that file's hashes match the live run (same code, data and assumptions);
- **live**: otherwise the tornado is worked out now with
  :func:`visionhousing.sensitivity.drivers.drivers` (the same function the
  export used).

The totals above are pinned-input totals for the state, not likely ranges.
When the engine has already worked out the **switch runs**
(``sensitivity.switch_runs``: the same input fixed at low / central / high,
every other input drawn as in the run), each driver row also carries
``ranges``: a P10 / P50 / P90 record per setting for the state (and for one
county when asked). They are read, never computed here, from:

- the engine's switch-run caches (memory, then disk for the default scenario);
- else the static export's ``<data>/<ST>/switch-ranges/<key>.json`` when its
  hashes match the live run.

With neither, ``ranges`` is absent and the switch shows the pinned totals.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from visionhousing.paths import safe_child

__all__ = [
    "TOP_SWITCH",
    "from_drivers",
    "from_stored",
    "ranges_for",
    "stored_ranges",
    "stored_run",
    "switch_payload",
]

TOP_SWITCH = 3
"""How many drivers per reform get a switch (the plan's 'top 3')."""

NOTE = (
    "Each switch moves one input to its low or high value while every other input stays at "
    "its central value; the figure is the state's five-year total at those pinned values, "
    "not a likely range. The central total can differ from the middle of the likely range, "
    "which comes from drawing every input at once."
)


def stored_run(data_dir: Path | None, state: str, key: str) -> dict[str, Any] | None:
    """The static export's saved run of one reform alone, unpacked, or ``None``."""
    if data_dir is None:
        return None
    try:
        path = safe_child(data_dir, state, "runs", f"{key}.json")
    except ValueError:
        return None
    if not path.is_file():
        return None
    from visionhousing.export_static import read_saved

    try:
        return read_saved(path)
    except (ValueError, OSError):
        return None


RANGES_NOTE = (
    "Ranges: each setting fixes that one input and draws every other input as in the run "
    "(same seed, same draws); P10 to P90 is the likely range with that input known."
)


def stored_ranges(data_dir: Path | None, state: str, key: str) -> dict[str, Any] | None:
    """The static export's switch ranges of one reform (``<ST>/switch-ranges/<key>.json``)."""
    if data_dir is None:
        return None
    try:
        path = safe_child(data_dir, state, "switch-ranges", f"{key}.json")
    except ValueError:
        return None
    if not path.is_file():
        return None
    from visionhousing.export_static import read_saved

    try:
        return read_saved(path)
    except (ValueError, OSError):
        return None


def ranges_for(
    result: Any, key: str, data_dir: Path | None = None, county: str | None = None
) -> tuple[dict[str, dict[str, dict[str, Any]]], str] | None:
    """Switch ranges per driver and setting, already worked out, or ``None``.

    Returns:
        ``({parameter: {setting: {"state": record, "county"?: record}}}, source)``
        with ``source`` ``"engine"`` (switch-run cache) or ``"stored"`` (static file).
    """
    from visionhousing.sensitivity.switch_runs import cached_switch_runs, to_jsonable

    runs = cached_switch_runs(result, key)
    data: dict[str, Any] | None = None
    source = "engine"
    if runs is not None:
        data = to_jsonable(runs)
    else:
        stored = stored_ranges(data_dir, result.state, key)
        h = result.hashes
        live = {"code": h.code, "data": h.data, "assumptions": h.assumptions}
        found = (live, result.seed, result.draws)
        if (
            stored is not None
            and (stored.get("hashes"), stored.get("seed"), stored.get("draws")) == found
        ):
            data = {d["parameter"]: d["settings"] for d in stored.get("drivers", [])}
            source = "stored"
    if not data:
        return None
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for pid, by in data.items():
        out[pid] = {}
        for setting, body in by.items():
            one: dict[str, Any] = {"state": body["state"]}
            if county is not None and county in body.get("counties", {}):
                one["county"] = body["counties"][county]
            out[pid][setting] = one
    return out, source


def _row(rank: int, parameter: str, label: str, unit: str, low: float, central: float,
         high: float, t_low: float, t_central: float, t_high: float) -> dict[str, Any]:  # fmt: skip
    return {
        "rank": rank,
        "parameter": parameter,
        "label": label,
        "unit": unit,
        "settings": {
            "low": {"input": low, "total": t_low},
            "central": {"input": central, "total": t_central},
            "high": {"input": high, "total": t_high},
        },
        "swing": abs(t_high - t_low),
    }


def from_stored(run: Mapping[str, Any], key: str, top: int = TOP_SWITCH) -> dict[str, Any] | None:
    """The switch rows from a stored run's ``explanation.drivers``, or ``None`` if absent."""
    block = ((run.get("explanation") or {}).get("drivers") or {}).get(key)
    if not block or not block.get("items"):
        return None
    central = float(block["central_total"])
    rows = [
        _row(
            int(it["rank"]),
            str(it["parameter"]),
            str(it["label"]),
            str(it.get("unit", "")),
            float(it["low"]),
            float(it["central"]),
            float(it["high"]),
            float(it["total_low"]),
            central,
            float(it["total_high"]),
        )
        for it in block["items"][:top]
    ]
    return {"central_total": central, "drivers": rows, "from": "stored"}


def from_drivers(result: Any, key: str, top: int = TOP_SWITCH) -> dict[str, Any]:
    """The switch rows worked out now from the tornado of ``result`` (pinned inputs, no draws)."""
    from visionhousing.sensitivity.drivers import drivers

    d = drivers(result, key, top=top)
    frame = d.tornado.head(top)
    rows = [
        _row(
            int(r.rank),
            str(r.parameter),
            str(r.label),
            str(r.unit),
            float(r.low),
            float(r.central),
            float(r.high),
            float(r.total_low),
            float(r.total_central),
            float(r.total_high),
        )
        for r in frame.itertuples(index=False)
    ]
    central = float(frame["total_central"].iloc[0]) if len(frame) else float("nan")
    return {"central_total": central, "drivers": rows, "from": "live"}


def switch_payload(
    result: Any,
    keys: Sequence[str],
    data_dir: Path | None = None,
    county: str | None = None,
) -> dict[str, Any]:
    """Top-three switches for each reform in ``keys``, stored when current, else live.

    Args:
        result: The engine ``Result`` of the default scenario (one or more reforms).
        keys: Reform keys to answer for.
        data_dir: The static export folder (``web/public/data``), or ``None``.
        county: A county id: each range setting also carries that county's record.

    Returns:
        ``{"state", "note", "reforms": {key: {"title", "central_total", "drivers",
        "from", "run_p50", "ranges_from"}}}``; each driver row has ``ranges``
        (setting to ``{"state", "county"?}`` records) when switch runs exist.
    """
    h = result.hashes
    live_hashes = {"code": h.code, "data": h.data, "assumptions": h.assumptions}
    out: dict[str, Any] = {"state": result.state, "note": NOTE, "reforms": {}}
    for key in keys:
        stored = stored_run(data_dir, result.state, key)
        body = None
        if stored is not None and stored.get("hashes") == live_hashes:
            body = from_stored(stored, key)
        if body is None:
            body = from_drivers(result, key)
        found = ranges_for(result, key, data_dir, county)
        if found is not None:
            ranges, source = found
            for row in body["drivers"]:
                if row["parameter"] in ranges:
                    row["ranges"] = ranges[row["parameter"]]
            body["ranges_from"] = source
            body["ranges_note"] = RANGES_NOTE
        else:
            body["ranges_from"] = None
        o = result.reforms[key]
        out["reforms"][key] = {
            "title": o.title,
            "run_p50": o.added.p50,
            "record_id": f"reform_results.{key}.added",
            **body,
        }
    return out
