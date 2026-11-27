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

States with thinner data
------------------------
Every real state runs on the same national (Tier 0) tables, so the method runs
anywhere a pack can be built. Two kinds of ranges are measured only in some
states, and the context swaps them in or out:

- a state's own already-legal table replaces the ledger's national default
  only for that state: Pennsylvania's town-rule index
  (``assumptions/calibration/adu_legal_share.yaml``) and Vermont's state-law
  table (``already_legal_vt.yaml``: the ADU share, because 24 V.S.A. § 4412
  already requires towns to allow one; Vermont's missing middle reads the
  dated § 4412(1)(D) floor itself, ``engine.law_floor``);
- where the pack holds verified municipal rules and
  ``assumptions/law/hooks.yaml`` switches the hooks on, counties whose rules
  cover enough residents use their own already-legal ADU share and parking
  reach (:attr:`StateContext.local_law`); the ledger entry's rationale says
  which counties and cites the readings;
- a range measured in another state (the ADU physical-fit table, measured on
  Pennsylvania lots) is *borrowed* by every other real state, and widened as
  ``assumptions/transfer.yaml`` says (:func:`transfer_overrides`). The widened
  entry, with its reason, is what the state's Assumption Ledger shows.

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

from visionhousing.law.hooks import HOOKS_PATH, LocalLaw, local_law_for_engine
from visionhousing.ledger import (
    Ledger,
    Scenario,
    apply_overrides,
    load_ledger,
    load_scenarios,
)
from visionhousing.ledger.loader import ROOT
from visionhousing.paths import safe_child, state_code
from visionhousing.scenarios.adu import (
    FIT_PATH,
    LEGAL_PARAM,
    FitTable,
    LegalShare,
    legal_share_override,
    legal_share_paths,
    load_fit_table,
    load_legal_share,
    load_legal_shares,
)

__all__ = [
    "PACKS_DIR",
    "TESTLAND",
    "TESTLAND_DIR",
    "StateContext",
    "context_from_tables",
    "load_context",
    "load_transfer_rules",
    "local_law_override",
    "lot_weighted_rules",
    "pack_dir",
    "safe_child",
    "state_code",
    "state_legal_share",
    "transfer_overrides",
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

LAW_TABLES = ("muni_zoning_rules.csv", "municipal_planning.csv")
"""Pack tables the local-law hooks read (optional; a ``.gz`` copy is also accepted)."""

LOT_WEIGHT_TABLE = "tract_municipalities.csv"
"""Optional pack table that splits tracts among municipalities. When the rules
table has no ``sf_lots`` column, it gives each municipality its single-family
lots (:func:`visionhousing.law.coverage.muni_weights`), so the ADU legal-status
categories are lot-weighted, not resident-weighted."""

WALK_TABLES = {"tract": "transit_walk_tract.csv", "county": "transit_walk_county.csv"}
"""Optional pack tables of network walksheds and the peak frequent-bus rule
(:mod:`visionhousing.features.transit_walk`); the TOD reform reads them when present."""

ZONED_TABLE = "mbta_3a_districts.csv"
ZONED_COLUMNS = ("district_id", "county", "tract", "acres", "capacity_station_area")
"""Optional pack table of districts the state already zones for multi-family homes
by right (Massachusetts: the MBTA Communities, Section 3A, district atlas). The
TOD reform takes their land inside station areas out of its new mid-rise sites."""

TYPOLOGY_TABLE = "typology_county.csv"
COUNTIES_TABLE = "counties.csv"
"""Optional pack tables for the county groups (``StateContext.county_types``)."""

TRANSFER_PATH = ROOT / "assumptions" / "transfer.yaml"
BACKTEST_PATH = ROOT / "assumptions" / "backtest.yaml"
GROWTH_PATH = ROOT / "assumptions" / "calibration" / "growth_coverage.yaml"
RELIABILITY_PATH = ROOT / "assumptions" / "calibration" / "baseline_reliability.yaml"
"""Back-test rules, including the small-sample widening of the state baseline range."""
"""Rules that widen borrowed calibrations for states without their own."""

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
        local_law: County values from verified municipal rules, or ``None``
            (no law tables in the pack, or the hooks switched off).
        transit_walk: Network walkshed acres per tract (pack table
            ``transit_walk_tract``), or ``None`` when the pack has none.
        transit_walk_county: Per-county stop and fallback counts (pack table
            ``transit_walk_county``), or ``None``.
        county_types: County id to its "places like this one" group name
            (pack table ``typology_county``, or worked out from the pack's
            ``counties`` table when that table is missing), or ``None`` when
            neither is there. Used to group county results, never to change them.
        zoned_districts: Districts already zoned for multi-family homes by
            right (pack table ``mbta_3a_districts``: ``tract``, ``acres``,
            ``capacity_station_area``), or ``None`` when the pack has none.
        base_ledger: The ledger before this state's calibration (the one the
            context's ``ledger`` was built from), so a test that hides data
            can rebuild the calibration from the same start. ``None`` for a
            context built by hand.
        derived: Values worked out once from this state's data and reused by
            every run (for example the transit-rich tract flags). Not part of
            the data; a changed copy of the context starts with it empty.
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
    local_law: LocalLaw | None = field(default=None, repr=False)
    county_types: Mapping[str, str] | None = field(default=None, repr=False)
    transit_walk: pd.DataFrame | None = field(default=None, repr=False)
    transit_walk_county: pd.DataFrame | None = field(default=None, repr=False)
    zoned_districts: pd.DataFrame | None = field(default=None, repr=False)
    base_ledger: Ledger | None = field(default=None, repr=False, compare=False)
    derived: dict[Any, Any] = field(default_factory=dict, init=False, compare=False, repr=False)


STATE_CODES = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
    ]
) | {TESTLAND}
"""Every state code a pack may be built for (the 50 states, DC) plus Testland."""


def pack_dir(state: str) -> Path:
    """Return the folder a state's data are read from.

    The code is checked first (two letters, a known state), and the folder is
    built under ``data/packs/`` only, so no request value can point the engine
    at another folder.

    Args:
        state: State code (``"TL"`` for Testland).

    Returns:
        ``tests/fixtures/testland`` for Testland, else ``data/packs/<STATE>``.

    Raises:
        ValueError: The code is not two letters.
        FileNotFoundError: A well-formed code that is no known state (it has no pack).
    """
    code = state_code(state)
    if code == TESTLAND:
        return TESTLAND_DIR
    if code not in STATE_CODES:
        raise FileNotFoundError(
            f"no state pack for {code}: not a known state code; build a real state with "
            f"`make data STATE={code}` or run Testland with state='{TESTLAND}'"
        )
    return safe_child(PACKS_DIR, code)


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


def load_transfer_rules(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Read the calibration-transfer rules (``assumptions/transfer.yaml``)."""
    import yaml

    path = Path(path) if path is not None else TRANSFER_PATH
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("rules") or [])


def transfer_overrides(
    state: str, ledger: Ledger, rules: list[dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """The ledger overrides that widen borrowed calibrations for ``state``.

    A rule applies to every real state not in its ``measured_in`` list. Testland
    (synthetic) is never widened. Only the fields a rule names are replaced; the
    entry's rationale says the range was borrowed and widened.

    Args:
        state: State code.
        ledger: The ledger the overrides will apply to (for the old rationale).
        rules: The rules (default: :func:`load_transfer_rules`).

    Returns:
        ``{parameter id: fields to replace}``; empty for a measured state.
    """
    code = state.upper()
    if code == TESTLAND:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for rule in load_transfer_rules() if rules is None else rules:
        measured = {str(s).upper() for s in rule.get("measured_in", ())}
        pid = str(rule["parameter"])
        if code in measured or pid not in dict(ledger.items()):
            continue
        change = {k: rule[k] for k in ("low", "central", "high", "shape") if k in rule}
        where = ", ".join(sorted(measured)) or "another state"
        why = " ".join(str(rule.get("rationale", "")).split())
        change["rationale"] = (
            f"Borrowed from {where} (calibration {rule.get('calibration', '-')}) and widened "
            f"for {code}, which has no measurement of its own (assumptions/transfer.yaml). {why}"
        )
        out[pid] = change
    return out


def local_law_override(ledger: Ledger, law: LocalLaw | None) -> dict[str, dict[str, Any]]:
    """The rationale change that says which counties use read local law for the ADU share.

    The range itself is unchanged: it stays the statewide knob, and each
    qualifying county turns every draw ``s`` of it into ``a + b x s``.
    """
    if law is None or law.adu.empty or LEGAL_PARAM not in dict(ledger.items()):
        return {}
    parts = []
    for county in sorted(law.adu["county"]):
        note = law.adu_note(county)
        if note:
            parts.append(f"County {county}: {note}.")
    old = ledger[LEGAL_PARAM].rationale
    text = (
        f"{old} Where verified municipal rules cover at least {law.min_coverage:.0%} of a "
        f"county's residents, that county's share comes from read local law instead: "
        + " ".join(parts)
    )
    return {LEGAL_PARAM: {"rationale": text}}


def _state_ledger(
    ledger: Ledger, legal: LegalShare, state: str, law: LocalLaw | None = None
) -> Ledger:
    if legal.state and legal.state.upper() == state.upper():
        ledger = apply_overrides(ledger, legal_share_override(legal))
    ledger = apply_overrides(ledger, transfer_overrides(state, ledger))
    return apply_overrides(ledger, local_law_override(ledger, law))


def state_legal_share(state: str, tables: Mapping[str, LegalShare] | None = None) -> LegalShare:
    """The already-legal table of ``state``, or Pennsylvania's when the state has none.

    Pennsylvania's table also holds the place scope of the Pennsylvania bills, so
    a state without its own table keeps it for that; its range is applied only
    to Pennsylvania runs (``_state_ledger`` checks the table's state).
    """
    tables = load_legal_shares() if tables is None else tables
    return tables.get(state.upper()) or tables.get("PA") or load_legal_share()


def _read_law_tables(folder: Path) -> tuple[dict[str, pd.DataFrame], list[Path]]:
    found: dict[str, pd.DataFrame] = {}
    files: list[Path] = []
    for name in LAW_TABLES:
        path = _find(folder, name)
        if path is not None:
            found[name.split(".")[0]] = pd.read_csv(path, dtype=str)
            files.append(path)
    return found, files


def lot_weighted_rules(
    rules: pd.DataFrame | None,
    tracts: pd.DataFrame,
    tract_munis: pd.DataFrame | None,
    blocks: pd.DataFrame | None = None,
) -> pd.DataFrame | None:
    """Add ``sf_lots`` and ``res_land_ha`` to the rules table when it lacks them.

    The ADU legal-status categories count lots, not residents, when the rules
    table carries single-family lots per municipality. The pack's rules table
    may not; then the lots are worked out from the tract table and the
    tract-to-municipality split (:func:`visionhousing.law.coverage.muni_weights`).
    Nothing changes when the columns are already there or the split is missing.
    """
    if rules is None or tract_munis is None or "sf_lots" in rules.columns:
        return rules
    from visionhousing.law.coverage import muni_weights

    weights = muni_weights(tracts, tract_munis, blocks)
    out = rules.copy()
    out["geoid"] = out["geoid"].astype(str)
    return out.merge(weights, on="geoid", how="left")


def _read_walk_tables(folder: Path) -> tuple[dict[str, pd.DataFrame], list[Path]]:
    found: dict[str, pd.DataFrame] = {}
    files: list[Path] = []
    for key, name in WALK_TABLES.items():
        path = _find(folder, name)
        if path is not None:
            found[key] = pd.read_csv(path, dtype={"geoid": str, "county": str})
            files.append(path)
    return found, files


PACK_FOLDER_KEY = ("pack_folder",)
"""``StateContext.derived`` key holding the folder a context was read from."""

LAZY_TABLES = ("incentives_county", "tract_places")
"""Pack tables read later, outside this module, that change results: the place
breakdown's incentive table, and the local-readings variants' tract-to-place
table (``tract_places``: Wyoming cities are Census places). The extra scenarios' optional tables
(``scenarios.extra_common.OPTIONAL_TABLES``) and the state-only tables
(``STATE_TABLES``) are added to these."""


def _lazy_table_files(folder: Path) -> list[Path]:
    """The optional pack tables that are present and read lazily, for the data hash.

    The extra scenarios and the capacity screen read ``infra_tract``,
    ``hazard_tract``, ``parcels_area`` and ``afford_income_limits`` on first use,
    and the place breakdown reads ``incentives_county``. Each one that exists is
    hashed (the same file the reader picks: ``.csv.gz`` first), so a retrofit that
    adds or changes one changes the data hash, the run cache key and the API keys
    together. A table that is absent adds nothing, so packs without it keep their
    hash.
    """
    from visionhousing.scenarios.extra_common import OPTIONAL_TABLES, STATE_TABLES

    files: list[Path] = []
    for name in (*sorted(OPTIONAL_TABLES), *LAZY_TABLES, *sorted(STATE_TABLES)):
        for cand in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
            if cand.is_file():
                files.append(cand)
                break
    return files


def _county_types(
    folder: Path, tables: Mapping[str, pd.DataFrame]
) -> tuple[dict[str, str] | None, list[Path]]:
    """County id to group name, and the files read (for the data hash).

    Reads ``typology_county``; without it, works the groups out from the pack's
    ``counties`` table with ``features.typology`` (as the place profile does).
    """
    path = _find(folder, TYPOLOGY_TABLE)
    if path is not None:
        table = pd.read_csv(path, dtype={"geoid": str})
        files = [path]
    else:
        counties = _find(folder, COUNTIES_TABLE)
        if counties is None:
            return None, []
        from visionhousing.features.typology import typology_tables

        try:
            made = typology_tables(
                {
                    "tracts": tables["tracts"],
                    "counties": pd.read_csv(counties, dtype={"geoid": str}),
                    "permits_county_year": tables["permits"],
                }
            )
        except (ValueError, KeyError, IndexError):
            return None, []
        table = made.get("typology_county")
        files = [counties]
    if table is None or table.empty or "type_name" not in table.columns:
        return None, []
    return dict(zip(table["geoid"].astype(str), table["type_name"].astype(str), strict=True)), files


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
    code = state_code(state) if folder is None else str(state).upper()
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
    law_tables, law_files = _read_law_tables(where)
    lot_path = _find(where, LOT_WEIGHT_TABLE)
    rules = law_tables.get("muni_zoning_rules")
    if rules is not None and lot_path is not None and "sf_lots" not in rules.columns:
        tract_munis = pd.read_csv(lot_path, dtype=str)
        rules = lot_weighted_rules(rules, tables["tracts"], tract_munis, tables["blocks"])
        law_files.append(lot_path)
    local_law = local_law_for_engine(rules, law_tables.get("municipal_planning"))
    data_files += law_files if local_law is not None else []
    walk_tables, walk_files = _read_walk_tables(where)
    data_files += walk_files
    zoned_path = _find(where, ZONED_TABLE)
    zoned = None
    if zoned_path is not None:
        zoned = pd.read_csv(
            zoned_path,
            dtype={"district_id": str, "county": str, "tract": str},
            usecols=lambda c: c in ZONED_COLUMNS,
        )
        data_files.append(zoned_path)
    county_types, type_files = _county_types(where, tables)
    data_files += type_files
    data_files += _lazy_table_files(where)
    if lot_path is not None and lot_path not in data_files:
        data_files.append(lot_path)  # SB 1346 reads it (engine.place_scope)
    ledger = load_ledger()
    scenarios = load_scenarios(ledger)
    legal = state_legal_share(code)
    calibration = [FIT_PATH, *legal_share_paths(), ROOT / "assumptions" / "scenarios.yaml"]
    if (ROOT / "assumptions" / "variants.yaml").is_file():
        calibration.append(ROOT / "assumptions" / "variants.yaml")
    if TRANSFER_PATH.is_file():
        calibration.append(TRANSFER_PATH)
    if local_law is not None:
        calibration.append(HOOKS_PATH)
    if BACKTEST_PATH.is_file():
        calibration.append(BACKTEST_PATH)
    if GROWTH_PATH.is_file():
        calibration.append(GROWTH_PATH)
    if RELIABILITY_PATH.is_file():
        calibration.append(RELIABILITY_PATH)  # its counts appear in the baseline's formula
    ctx = context_from_tables(
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
        local_law=local_law,
        county_types=county_types,
        transit_walk=walk_tables.get("tract"),
        transit_walk_county=walk_tables.get("county"),
        zoned_districts=zoned,
    )
    ctx.derived[PACK_FOLDER_KEY] = where  # optional tables are read from this same folder
    return ctx


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
    local_law: LocalLaw | None = None,
    county_types: Mapping[str, str] | None = None,
    transit_walk: pd.DataFrame | None = None,
    transit_walk_county: pd.DataFrame | None = None,
    zoned_districts: pd.DataFrame | None = None,
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
        local_law: County values from verified municipal rules (optional).
        county_types: County id to its group name (optional).
        transit_walk: Network walkshed acres per tract (optional). A table
            with no rows counts as no walksheds (``None``), so the run says it
            falls back to straight-line distance.
        transit_walk_county: Per-county walkshed and stop counts (optional).
        zoned_districts: Districts already zoned for multi-family by right
            (optional; ``StateContext.zoned_districts``).

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
        ledger=_state_ledger(ledger, legal, code, local_law),
        base_ledger=ledger,
        scenarios=dict(scenarios),
        fit=fit,
        legal=legal,
        data_hash=data_hash,
        calibration_hash=calibration_hash,
        source=source,
        synthetic=code == TESTLAND,
        local_law=local_law,
        county_types=None if county_types is None else dict(county_types),
        transit_walk=(
            None if transit_walk is None or transit_walk.empty else _text_ids(transit_walk, "geoid")
        ),
        transit_walk_county=(
            None if transit_walk_county is None else _text_ids(transit_walk_county, "county")
        ),
        zoned_districts=(None if zoned_districts is None else _text_ids(zoned_districts, "tract")),
    )


def _text_ids(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """Return ``frame`` with ``column`` as text (when it has the column)."""
    if column not in frame.columns:
        return frame
    return frame.assign(**{column: frame[column].astype(str)})
