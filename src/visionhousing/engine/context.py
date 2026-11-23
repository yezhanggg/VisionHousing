"""The state context: every table and calibration file one state's run needs.

Plain-language summary
----------------------
Before a run, the engine gathers what it needs for one state into a single
read-only object, the *state context*:

- the tract table (census features per tract),
- the county permit history (for the baseline),
- census blocks, transit stops and tract boundaries (for the parking reform's
  transit test),
- the Assumption Ledger, the scenario file, and the ADU calibration tables,
- a fingerprint (hash) of the data files, so every result says which data it
  came from.

Where the data come from:

- ``state="TL"`` (Testland) reads the small synthetic state committed in
  ``tests/fixtures/testland/``. Every test and the offline interface use it.
- Any other state reads its built state pack from ``data/packs/<STATE>/``
  (made by ``make data STATE=..``). If the pack is not there the run stops
  with a message saying how to build it. It never falls back to Testland,
  because synthetic numbers must never be shown as a real state's.

:func:`load_context` is the only function here that reads files. The engine's
core (``engine.core.compute``) takes a context and does no file access.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.ledger import (
    Ledger,
    Scenario,
    apply_overrides,
    load_ledger,
    load_scenarios,
)
from visionhousing.ledger.loader import ROOT
from visionhousing.scenarios.adu import (
    FIT_PATH,
    LEGAL_PATH,
    FitTable,
    LegalShare,
    legal_share_override,
    load_fit_table,
    load_legal_share,
)

__all__ = [
    "PACKS_DIR",
    "TESTLAND",
    "TESTLAND_DIR",
    "StateContext",
    "context_from_tables",
    "load_context",
    "pack_dir",
]

TESTLAND = "TL"
"""State code of the synthetic test state."""

TESTLAND_DIR = ROOT / "tests" / "fixtures" / "testland"
"""Folder of the committed synthetic state."""

PACKS_DIR = ROOT / "data" / "packs"
"""Folder that holds one built state pack per state code."""

TABLES = {
    "tracts": "tracts.csv",
    "permits": "permits_county_year.csv",
    "blocks": "blocks.csv",
    "stops": "transit_stops.csv",
}
"""Table name to file name in a state pack (a ``.gz`` copy is also accepted)."""

BOUNDARIES = "boundaries.geojson"
"""Tract and county outlines in one file (optional; a ``.gz`` copy is also accepted)."""

BOUNDARIES_DIR = "boundaries"
"""Or a folder: ``counties.geojson`` plus one tract file per county in ``tracts/``."""

_TEXT_COLUMNS = {
    "tracts": {"geoid": str, "county": str},
    "permits": {"county": str},
    "blocks": {"geoid": str, "tract": str},
    "stops": {"stop_id": str},
}


@dataclass(frozen=True)
class StateContext:
    """Everything one state's run reads, already loaded.

    Attributes:
        state: State code, for example ``"PA"`` or ``"TL"``.
        tracts: Tract table (state-pack schema).
        permits: County x year permit table (one column per structure type).
        blocks: Census blocks (``tract``, ``housing_units``, ``lat``, ``lon``).
        stops: Transit stops (``lat``, ``lon``, ``mode``, ``weekday_departures``).
        boundaries: Tract outlines as GeoJSON, or ``None``.
        ledger: The Assumption Ledger with any state-level calibration applied
            (for Pennsylvania, the already-legal ADU share).
        scenarios: Scenario id to its checked entry.
        fit: ADU physical-fit calibration table.
        legal: ADU already-legal share table (and the bills' place scope).
        data_hash: SHA-256 of the data files, in a fixed order.
        calibration_hash: SHA-256 of the calibration and scenario files.
        source: Where the data were read from (text, for provenance).
        synthetic: True for Testland.
    """

    state: str
    tracts: pd.DataFrame = field(repr=False)
    permits: pd.DataFrame = field(repr=False)
    blocks: pd.DataFrame = field(repr=False)
    stops: pd.DataFrame = field(repr=False)
    boundaries: Mapping[str, Any] | None = field(repr=False)
    ledger: Ledger = field(repr=False)
    scenarios: Mapping[str, Scenario] = field(repr=False)
    fit: FitTable = field(repr=False)
    legal: LegalShare = field(repr=False)
    data_hash: str
    calibration_hash: str
    source: str
    synthetic: bool = False


def pack_dir(state: str) -> Path:
    """Return the folder a state's data are read from.

    Args:
        state: State code (``"TL"`` for Testland).

    Returns:
        ``tests/fixtures/testland`` for Testland, else ``data/packs/<STATE>``.
    """
    code = state.upper()
    return TESTLAND_DIR if code == TESTLAND else PACKS_DIR / code


def _find(folder: Path, name: str) -> Path | None:
    for candidate in (folder / name, folder / f"{name}.gz"):
        if candidate.is_file():
            return candidate
    return None


def _hash_files(paths: list[Path], prefix: str) -> str:
    digest = hashlib.sha256(prefix.encode())
    for path in paths:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    if path.suffix == ".gz":
        import gzip

        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _boundary_files(folder: Path) -> list[Path]:
    single = _find(folder, BOUNDARIES)
    if single is not None:
        return [single]
    sub = folder / BOUNDARIES_DIR
    if not sub.is_dir():
        return []
    files = sorted(p for p in sub.glob("*.geojson*") if p.is_file())
    tracts = sub / "tracts"
    if tracts.is_dir():
        files += sorted(p for p in tracts.glob("*.geojson*") if p.is_file())
    return files


def _read_boundaries(folder: Path) -> tuple[dict[str, Any] | None, list[Path]]:
    """Read the outlines: one ``boundaries.geojson`` or a ``boundaries/`` folder.

    A folder's files (counties, then one tract file per county) are merged into
    one feature collection, in file-name order. Returns the collection (or
    ``None`` when there are no outlines) and the files read, for the data hash.
    """
    files = _boundary_files(folder)
    if not files:
        return None, []
    features: list[Any] = []
    for path in files:
        features.extend(_read_json(path).get("features", []))
    return {"type": "FeatureCollection", "features": features}, files


def _state_ledger(ledger: Ledger, legal: LegalShare, state: str) -> Ledger:
    if legal.state and legal.state.upper() == state.upper():
        return apply_overrides(ledger, legal_share_override(legal))
    return ledger


def load_context(state: str = TESTLAND, *, folder: Path | str | None = None) -> StateContext:
    """Read one state's tables and the assumption files into a context.

    Args:
        state: State code. ``"TL"`` reads Testland; any other code reads
            ``data/packs/<STATE>/``.
        folder: Read the tables from this folder instead (tests use it).

    Returns:
        The state context.

    Raises:
        FileNotFoundError: The state pack, or one of its tables, is missing.
    """
    code = state.upper()
    where = Path(folder) if folder is not None else pack_dir(code)
    if not where.is_dir():
        raise FileNotFoundError(
            f"no state pack for {code} at {where}; build it with `make data STATE={code}` "
            f"or run Testland with state='{TESTLAND}'"
        )
    found: dict[str, Path] = {}
    for key, name in TABLES.items():
        path = _find(where, name)
        if path is None:
            raise FileNotFoundError(f"state pack {code} is missing {name} in {where}")
        found[key] = path
    tables = {key: pd.read_csv(path, dtype=_TEXT_COLUMNS.get(key)) for key, path in found.items()}
    boundaries, bfiles = _read_boundaries(where)
    data_files = [found[k] for k in TABLES] + bfiles
    ledger = load_ledger()
    scenarios = load_scenarios(ledger)
    legal = load_legal_share()
    calibration = [FIT_PATH, LEGAL_PATH, ROOT / "assumptions" / "scenarios.yaml"]
    return context_from_tables(
        code,
        tracts=tables["tracts"],
        permits=tables["permits"],
        blocks=tables["blocks"],
        stops=tables["stops"],
        boundaries=boundaries,
        ledger=ledger,
        scenarios=scenarios,
        fit=load_fit_table(),
        legal=legal,
        data_hash=_hash_files(data_files, f"visionhousing-data-v1\n{code}"),
        calibration_hash=_hash_files(calibration, "visionhousing-calibration-v1"),
        source=str(where.relative_to(ROOT)) if where.is_relative_to(ROOT) else where.name,
    )


def context_from_tables(
    state: str,
    *,
    tracts: pd.DataFrame,
    permits: pd.DataFrame,
    blocks: pd.DataFrame,
    stops: pd.DataFrame,
    boundaries: Mapping[str, Any] | None,
    ledger: Ledger,
    scenarios: Mapping[str, Scenario],
    fit: FitTable,
    legal: LegalShare,
    data_hash: str,
    calibration_hash: str,
    source: str = "in memory",
) -> StateContext:
    """Build a context from tables already in memory (pure; no files read).

    Text id columns are normalised to strings and the state's calibration
    (the already-legal ADU share, when the table is for this state) is applied
    to the ledger.

    Args:
        state: State code.
        tracts: Tract table.
        permits: County x year permit table.
        blocks: Block table.
        stops: Transit stop table.
        boundaries: Tract outlines as GeoJSON, or ``None``.
        ledger: The base ledger (before the state's calibration).
        scenarios: Scenario id to its entry.
        fit: ADU fit table.
        legal: ADU legal-share table.
        data_hash: Fingerprint of the data.
        calibration_hash: Fingerprint of the calibration and scenario files.
        source: Where the data came from, for provenance.

    Returns:
        The context.
    """
    code = state.upper()
    tracts = tracts.assign(geoid=tracts["geoid"].astype(str), county=tracts["county"].astype(str))
    permits = permits.assign(county=permits["county"].astype(str))
    blocks = blocks.assign(tract=blocks["tract"].astype(str))
    return StateContext(
        state=code,
        tracts=tracts,
        permits=permits,
        blocks=blocks,
        stops=stops,
        boundaries=boundaries,
        ledger=_state_ledger(ledger, legal, code),
        scenarios=dict(scenarios),
        fit=fit,
        legal=legal,
        data_hash=data_hash,
        calibration_hash=calibration_hash,
        source=source,
        synthetic=code == TESTLAND,
    )
