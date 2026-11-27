"""What a place profile reads: one state's data pack, loaded once, plus where each number came from.

Plain-language summary
----------------------
A :class:`ProfileContext` holds the small tables of one state data pack
(counties, tracts, permits, transit stops, house prices), the pack's data
dictionary and manifest, and the source registry (``sources.yaml``). Sections
ask it for tables and for the edition ("vintage") of each source, so every
fact can say exactly where it came from.

- ``state="TL"`` reads the synthetic test state Testland in
  ``tests/fixtures/testland/``; every vintage then reads "synthetic test data",
  so no synthetic number can pass for a real one.
- Any other state reads ``data/packs/<STATE>/``. A missing pack stops with a
  message saying how to build it.

A missing table is not an error: :meth:`ProfileContext.table` returns ``None``
and the section that needed it says "no data for this place yet".

:class:`Area` is one place to measure: the state, a county or a tract, with
its tracts. Sections compute the same measure for the place and for the
bigger area it is compared with (the state for a county, the county for a
tract), so the two always use the same formula.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.data.registry import Source, load_registry
from visionhousing.engine.context import PACKS_DIR, TESTLAND, TESTLAND_DIR
from visionhousing.features import brackets as br
from visionhousing.paths import safe_child, state_code

__all__ = [
    "Area",
    "Measure",
    "MissingDataError",
    "PlaceNotFoundError",
    "ProfileContext",
    "load_profile_context",
    "tract_name",
]

SYNTHETIC_VINTAGE = "synthetic test data (Testland)"
"""Testland's vintage text; another synthetic pack names itself (``ProfileContext.vintage``)."""
_TEXT = ("geoid", "county", "tract", "stop_id", "feed_id", "ntd_id")
STATE_NAMES = {"TL": "Testland (synthetic test state)"}


class MissingDataError(LookupError):
    """A table or column a section needs is not in this state's pack."""


class PlaceNotFoundError(KeyError):
    """The county or tract code is not in this state's pack."""


def _state_name(code: str) -> str:
    from visionhousing.api.service import STATE_NAMES as ALL_NAMES

    return STATE_NAMES.get(code) or ALL_NAMES.get(code, code)


def tract_name(geoid: str) -> str:
    """Plain name of a tract from its code: ``42003020100`` -> ``"Census tract 201"``."""
    code = str(geoid)[5:]
    if len(code) != 6 or not code.isdigit():
        return f"Census tract {geoid}"
    whole, part = int(code[:4]), code[4:]
    return f"Census tract {whole}" + ("" if part == "00" else f".{part}")


@dataclass(frozen=True)
class Measure:
    """A value worked out for one area, with its margin of error and a note on the method."""

    value: float | None
    moe: float | None = None
    note: str = ""


def _clean(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


@dataclass(frozen=True)
class Area:
    """One place to measure: the state, a county or a tract.

    Attributes:
        level: ``"state"``, ``"county"`` or ``"tract"``.
        geoid: Its code (the state code for the state).
        name: Its plain name.
        tracts: Its tract rows (one row for a tract).
        counties: The county codes it covers (a tract's own county for a tract).
    """

    level: str
    geoid: str
    name: str
    tracts: pd.DataFrame = field(repr=False)
    counties: tuple[str, ...] = ()

    @property
    def single(self) -> bool:
        """True for a tract: values are read as published, not added up."""
        return self.level == "tract"

    def _col(self, name: str) -> pd.Series:
        if name not in self.tracts.columns:
            raise MissingDataError(f"column {name} is not in the tract table")
        return pd.to_numeric(self.tracts[name], errors="coerce")

    def _moe(self, name: str) -> pd.Series | None:
        col = f"{name}_moe"
        if col not in self.tracts.columns:
            return None
        return pd.to_numeric(self.tracts[col], errors="coerce")

    def count(self, *names: str) -> Measure:
        """Sum of one or more count columns over the area's tracts.

        The margin is the Census Bureau's approximation for a sum: the square
        root of the sum of squared margins (``None`` when the pack has none).
        """
        total = 0.0
        squares = 0.0
        has_moe = True
        any_value = False
        for name in names:
            values = self._col(name)
            if values.notna().any():
                any_value = True
            total += float(values.sum(min_count=1)) if values.notna().any() else 0.0
            moe = self._moe(name)
            if moe is None or moe.isna().all():
                has_moe = False
            else:
                squares += float((moe.fillna(0) ** 2).sum())
        if not any_value:
            return Measure(None)
        return Measure(total, math.sqrt(squares) if has_moe else None)

    def ratio(self, num: Sequence[str], den: Sequence[str], *, proportion: bool = True) -> Measure:
        """Sum of ``num`` columns / sum of ``den`` columns, with the Bureau's margin formula.

        For a proportion (the numerator is part of the denominator) the margin is
        ``sqrt(moe_num^2 - p^2 * moe_den^2) / den``, switching to ``+`` when the
        term under the root is negative, as the Bureau advises.
        """
        n, d = self.count(*num), self.count(*den)
        if n.value is None or d.value is None or d.value <= 0:
            return Measure(None)
        p = n.value / d.value
        if n.moe is None or d.moe is None:
            return Measure(p)
        under = n.moe**2 - (p**2) * d.moe**2 if proportion else -1.0
        if under < 0:
            under = n.moe**2 + (p**2) * d.moe**2
        return Measure(p, math.sqrt(under) / d.value)

    def published(self, name: str) -> Measure:
        """A tract's published value and margin (tracts only)."""
        value = _clean(self._col(name).iloc[0]) if len(self.tracts) else None
        moe_col = self._moe(name)
        moe = _clean(moe_col.iloc[0]) if moe_col is not None and len(self.tracts) else None
        return Measure(value, moe)

    def weighted_mean(self, name: str, weight: str | Sequence[str], note: str = "") -> Measure:
        """For a tract, the published value; for a bigger area, the weighted mean of tracts."""
        if self.single:
            return self.published(name)
        values = self._col(name)
        weights = sum(
            self._col(w).fillna(0) for w in ([weight] if isinstance(weight, str) else weight)
        )
        ok = values.notna() & (weights > 0)
        if not ok.any():
            return Measure(None)
        mean = float(np.average(values[ok], weights=weights[ok]))
        return Measure(mean, None, note)

    def median(self, name: str, table: br.BracketTable) -> Measure:
        """For a tract, the published median; for a bigger area, the median of summed brackets.

        Bracket counts are added over the area's tracts and the median is found
        by straight-line interpolation inside the bracket that holds it
        (:func:`visionhousing.features.brackets.median_from_brackets`).
        """
        if self.single:
            return self.published(name)
        cols = list(table.columns)
        missing = [c for c in cols if c not in self.tracts.columns]
        if missing:
            raise MissingDataError(f"bracket columns missing: {missing[:3]}")
        counts = self.tracts[cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum()
        result = br.median_from_brackets(counts.to_numpy(dtype=float), table)
        value = _clean(result.value)
        note = (
            "estimated from the summed bracket counts of its tracts (straight-line interpolation)"
        )
        if result.bound == "lower":
            note += "; the median is in the open top bracket, so this is its lower edge"
        return Measure(value, None, note)


def _from_tract_table(
    area: Area, group: str, bands: dict[str, tuple[str, ...]]
) -> dict[str, float | None]:
    """One tract's band values and margins for ``group``, from tract-table columns."""
    cols = area.tracts.columns
    out: dict[str, float | None] = {}
    for band, names in bands.items():
        present = [n for n in names if n in cols]
        if not present:
            return {}
        value = 0.0
        squares = 0.0
        has_moe = True
        for n in present:
            v = _clean(area.tracts[n].iloc[0])
            if v is None:
                return {}
            value += v
            m = _clean(area.tracts[f"{n}_moe"].iloc[0]) if f"{n}_moe" in cols else None
            if m is None:
                has_moe = False
            else:
                squares += m * m
        out[f"{group}_{band}"] = value
        out[f"{group}_{band}_moe"] = round(math.sqrt(squares)) if has_moe else None
    return out


@dataclass
class ProfileContext:
    """One state's pack, read once, for building place profiles.

    Attributes:
        state: Two-letter code (``"TL"`` for Testland).
        state_name: The state's name.
        folder: The pack folder.
        synthetic: True for Testland.
        dictionary: The pack's ``data_dictionary.json`` (empty when absent).
        manifest: The pack's ``pack_manifest.json``, or ``None``.
        registry: The source registry, id to entry.
    """

    state: str
    state_name: str
    folder: Path
    synthetic: bool
    dictionary: dict[str, Any]
    manifest: dict[str, Any] | None
    registry: dict[str, Source]
    _tables: dict[str, pd.DataFrame | None] = field(default_factory=dict, repr=False)
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ tables

    def table(self, name: str) -> pd.DataFrame | None:
        """Read a pack table (``name.csv.gz`` or ``name.csv``); ``None`` when absent."""
        if name not in self._tables:
            frame = None
            for path in (
                safe_child(self.folder, f"{name}.csv.gz"),
                safe_child(self.folder, f"{name}.csv"),
            ):
                if path.is_file():
                    frame = pd.read_csv(path, dtype={c: str for c in _TEXT})
                    break
            self._tables[name] = frame
        return self._tables[name]

    def need(self, name: str) -> pd.DataFrame:
        """Like :meth:`table`, but raise :class:`MissingDataError` when the table is absent."""
        frame = self.table(name)
        if frame is None or frame.empty:
            raise MissingDataError(f"the {self.state} pack has no {name} table")
        return frame

    @property
    def pack_hash(self) -> str | None:
        """The pack's fingerprint, when it has a manifest."""
        return (self.manifest or {}).get("pack_hash")

    @property
    def pack_name(self) -> str:
        """The pack's own name, for a synthetic pack's vintage text.

        ``name`` in its manifest or data dictionary, else the state's name without a
        bracketed note ("Testland (synthetic test state)" gives "Testland").
        """
        for doc in (self.manifest or {}, self.dictionary):
            name = doc.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        return self.state_name.split(" (", 1)[0].strip() or self.state

    # ------------------------------------------------------------------ sources

    def vintage(self, source_id: str) -> str:
        """The edition of a source as used in this pack.

        A synthetic pack: "synthetic test data (<its name>)", the name the pack gives
        itself (``name`` in its manifest or data dictionary), else the state's name
        without its bracketed note ("Testland", "Demo state"). A real state: the
        version in the pack manifest, else the registry's vintage.

        Raises:
            KeyError: The id is not in ``sources.yaml``.
        """
        entry = self.registry[source_id]
        if self.synthetic:
            return f"synthetic test data ({self.pack_name})"
        for row in (self.manifest or {}).get("provenance", []):
            if row.get("id") == source_id and row.get("version"):
                return str(row["version"])
        return entry.vintage

    def source_info(self, source_id: str) -> dict[str, str]:
        """Name, publisher, vintage and address of a source, for footnotes."""
        entry = self.registry[source_id]
        return {
            "name": entry.name,
            "publisher": entry.publisher,
            "vintage": self.vintage(source_id),
            "url": entry.terms_url,
            "limits": entry.limits,
        }

    def acs_years(self) -> str:
        """The ACS survey years, for example ``"2020-2024"``."""
        from visionhousing.data.sources.acs import VINTAGE

        return f"{VINTAGE - 4}-{VINTAGE}"

    # ------------------------------------------------------------------ places

    def counties(self) -> pd.DataFrame:
        """The county table: ``geoid`` and ``name`` at least."""
        return self.need("counties")

    def county_name(self, geoid: str) -> str:
        """A county's name from the county table."""
        hit = self.counties()
        row = hit[hit["geoid"] == geoid]
        if row.empty:
            raise PlaceNotFoundError(geoid)
        name = row["name"].iloc[0]
        return str(name) if isinstance(name, str) and name else f"County {geoid}"

    def state_area(self) -> Area:
        """The whole state."""
        tracts = self.need("tracts")
        return Area(
            "state",
            self.state,
            self.state_name,
            tracts,
            tuple(sorted(self.counties()["geoid"].astype(str))),
        )

    def county_area(self, geoid: str) -> Area:
        """One county and its tracts."""
        name = self.county_name(geoid)
        tracts = self.need("tracts")
        return Area("county", geoid, name, tracts[tracts["county"] == geoid], (geoid,))

    def tract_area(self, geoid: str) -> Area:
        """One tract."""
        tracts = self.need("tracts")
        row = tracts[tracts["geoid"] == geoid]
        if row.empty:
            raise PlaceNotFoundError(geoid)
        return Area("tract", geoid, tract_name(geoid), row, (geoid[:5],))

    def place(self, geoid: str) -> tuple[Area, Area]:
        """The place and the bigger area it is compared with.

        A county is compared with the state; a tract with its county.

        Raises:
            PlaceNotFoundError: The code is not a county or tract in this pack.
        """
        geoid = str(geoid).strip()
        if len(geoid) == 5 and geoid.isdigit():
            return self.county_area(geoid), self.state_area()
        if len(geoid) == 11 and geoid.isdigit():
            area = self.tract_area(geoid)
            return area, self.county_area(geoid[:5])
        raise PlaceNotFoundError(geoid)

    # ------------------------------------------------------------------ ACS bands

    def bands(self, area: Area) -> tuple[dict[str, float | None], set[str]] | None:
        """The collapsed ACS bands of an area, and which groups came from the tract table.

        The state and counties read their own row of ``acs_bands`` (the Bureau's
        published county and state figures). A tract reads its row for the
        groups kept on tract rows, and builds owners and renters, building type
        and year built from the tract table
        (:data:`visionhousing.features.acs_bands.FROM_TRACT_TABLE`), with the
        Bureau's approximation for the margin of a sum.

        Returns:
            ``({column: value}, {group keys read from the tract table})``, or
            ``None`` when the pack has no ``acs_bands`` table or no row for the area.
        """
        from visionhousing.features import acs_bands as ab

        key = f"bands:{area.level}:{area.geoid}"
        if key in self._cache:
            return self._cache[key]
        table = self.table("acs_bands")
        result = None
        if table is not None and not table.empty:
            if area.level == "state":
                row = table[table["level"] == "state"]
            else:
                row = table[table["geoid"].astype(str) == area.geoid]
            if not row.empty:
                first = row.iloc[0]
                values = {c: _clean(v) for c, v in first.items() if c not in ("geoid", "level")}
                from_tracts: set[str] = set()
                if area.single:
                    for group, bands in ab.FROM_TRACT_TABLE.items():
                        filled = _from_tract_table(area, group, bands)
                        if filled:
                            values.update(filled)
                            from_tracts.add(group)
                result = (values, from_tracts)
        self._cache[key] = result
        return result

    # ------------------------------------------------------------------ transit

    def transit_rule(self):
        """The major-transit-stop rule, from the ledger's central values."""
        if "rule" not in self._cache:
            from visionhousing.features.transit import TransitRule
            from visionhousing.ledger import load_ledger

            self._cache["rule"] = TransitRule.from_ledger(load_ledger())
        return self._cache["rule"]

    def transit_tracts(self) -> pd.DataFrame:
        """Per tract: 2020 block units, units near major transit, major stops inside.

        A real pack stores these columns in the tract table. Testland does not,
        so they are worked out from its blocks and stops with the same function
        the pack build uses (:func:`visionhousing.features.transit.tract_transit_features`).

        Returns:
            Columns ``tract``, ``block_units``, ``transit_units_d07``,
            ``transit_units_0_50mi``, ``transit_major_stops``.
        """
        if "transit_tracts" in self._cache:
            return self._cache["transit_tracts"]
        tracts = self.need("tracts")
        wanted = ("transit_units_d07", "transit_units_0_50mi", "housing_units_2020")
        if all(c in tracts.columns for c in wanted):
            out = pd.DataFrame(
                {
                    "tract": tracts["geoid"],
                    "block_units": tracts["housing_units_2020"],
                    "transit_units_d07": tracts["transit_units_d07"],
                    "transit_units_0_50mi": tracts["transit_units_0_50mi"],
                    "transit_major_stops": tracts.get("transit_major_stops", 0),
                }
            )
        else:
            from visionhousing.features.transit import tract_transit_features

            blocks, stops = self.need("blocks"), self.stops()
            feats = tract_transit_features(
                blocks,
                stops,
                self.transit_rule(),
                stop_tracts=stops["tract"].astype(str),
                tracts=tracts["geoid"],
            )
            units = blocks.groupby(blocks["tract"].astype(str))["housing_units"].sum()
            out = feats[["tract", "transit_units_d07", "transit_units_0_50mi"]].copy()
            out["block_units"] = out["tract"].map(units).fillna(0)
            out["transit_major_stops"] = feats["transit_major_stops"]
        self._cache["transit_tracts"] = out
        return out

    def stops(self) -> pd.DataFrame:
        """Transit stops with ``major`` (0/1), ``county`` and ``tract`` filled.

        A real pack stores all three. For Testland they are worked out here:
        ``major`` by the major-stop rule and the county and tract by testing which
        outline holds each stop.
        """
        if "stops" in self._cache:
            return self._cache["stops"]
        stops = self.need("transit_stops").copy()
        if not {"major", "county", "tract"} <= set(stops.columns):
            from visionhousing.features.transit import major_stop_table, points_in_polygons

            rule = self.transit_rule()
            major = major_stop_table(stops.assign(_row=np.arange(len(stops))), rule)
            flag = np.zeros(len(stops), dtype=int)
            flag[major["_row"].to_numpy(dtype=int)] = 1
            stops["major"] = flag
            shapes = self._shapes("tract")
            holder = points_in_polygons(stops["lat"], stops["lon"], shapes)
            stops["tract"] = holder
            stops["county"] = [h[:5] if h else "" for h in holder]
        self._cache["stops"] = stops
        return stops

    def _shapes(self, level: str) -> list[tuple[str, dict[str, Any]]]:
        path = self.folder / "boundaries.geojson"
        if not path.is_file():
            raise MissingDataError("no tract outlines to place the stops in")
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            (f["properties"]["geoid"], f["geometry"])
            for f in data.get("features", [])
            if f["properties"].get("level") == level
        ]

    # ------------------------------------------------------------------ report card

    def report_card(self) -> dict[str, Any]:
        """The state data report card (:mod:`visionhousing.data.report_card`), built once."""
        if "card" not in self._cache:
            from visionhousing.data.report_card import load_pack_files, report_card

            self._cache["card"] = report_card(load_pack_files(self.folder))
        return self._cache["card"]


def load_profile_context(
    state: str = TESTLAND, *, folder: Path | str | None = None
) -> ProfileContext:
    """Open one state's pack for profiles.

    Args:
        state: Two-letter code; ``"TL"`` is Testland.
        folder: Read the pack from this folder instead (tests use it).

    Raises:
        FileNotFoundError: There is no pack for the state.
        ValueError: ``state`` is not a two-letter code.
    """
    code = state_code(state)
    if folder is not None:
        where = Path(folder)
    else:
        where = TESTLAND_DIR if code == TESTLAND else safe_child(PACKS_DIR, code)
    if not where.is_dir():
        raise FileNotFoundError(
            f"no state pack for {code} at {where}; build it with `make data STATE={code}`"
        )
    dict_path = where / "data_dictionary.json"
    manifest_path = where / "pack_manifest.json"
    dictionary = json.loads(dict_path.read_text(encoding="utf-8")) if dict_path.is_file() else {}
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    )
    return ProfileContext(
        state=code,
        state_name=_state_name(code),
        folder=where,
        synthetic=code == TESTLAND or bool(dictionary.get("synthetic")),
        dictionary=dictionary,
        manifest=manifest,
        registry=load_registry(),
    )
