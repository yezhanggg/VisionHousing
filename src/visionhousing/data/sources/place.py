"""Place-profile inputs (jobs, incentive areas, safety): fetch them together, build their tables.

Plain-language summary
----------------------
The state pack carries a few tables used only by the place profile's market
research sections ``jobs``, ``incentives`` and ``safety``. This module is the
one door the pack builder uses for them:

- :func:`fetch_place` fetches (or reads from the cache) every input. Each
  source is fetched on its own and a failure is written down as a note instead
  of stopping the build: these tables describe places, the reform model does
  not use them.
- :func:`place_tables` builds the pack tables from those inputs.
- :data:`TABLES` and :data:`COLUMNS` describe every table and column for the
  pack's data dictionary.

The safety source needs an api.data.gov key in the environment; without one
the safety table is simply left out and a note says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.sources import incentives as inc_src
from visionhousing.data.sources import jobs as jobs_src
from visionhousing.data.sources import safety as safety_src
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.features import incentives as inc_fx
from visionhousing.features import jobs as jobs_fx
from visionhousing.features import safety as safety_fx

__all__ = ["COLUMNS", "TABLES", "PlaceInputs", "fetch_place", "place_tables"]

TABLES: dict[str, dict[str, object]] = {**jobs_fx.TABLES, **inc_fx.TABLES, **safety_fx.TABLES}
"""Every place-profile pack table: key, sources and description."""
COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    **jobs_fx.COLUMNS,
    **inc_fx.COLUMNS,
    **safety_fx.COLUMNS,
}
"""``(table, column) -> (unit, description, source id)`` for every place-profile column."""
TIER_TWO = {("incentives_tract", "koz_sites"), ("incentives_tract", "koz_acres"),
            ("incentives_county", "koz_sites"), ("incentives_county", "koz_acres")}  # fmt: skip
"""Columns from a single state's own source (Keystone Opportunity Zones): tier 2."""


@dataclass
class PlaceInputs:
    """The place-profile inputs; any part is ``None`` when it could not be fetched."""

    wac: pd.DataFrame | None = None
    od: pd.DataFrame | None = None
    cbp: pd.DataFrame | None = None
    incentives: inc_src.IncentiveInputs | None = None
    safety: pd.DataFrame | None = None
    files: list[tuple[str, Path]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def fetch_place(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> PlaceInputs:
    """Fetch (or reuse) every place-profile input; failures become notes."""
    fips = to_state_fips(state)
    cache = Path(cache)
    client = client or HttpClient()
    out = PlaceInputs()

    def attempt(name: str, func):
        try:
            return func()
        except (FetchError, ValueError, KeyError, OSError) as err:
            out.notes.append(f"place: {name} not fetched ({type(err).__name__}: {err})")
            return None

    out.wac = attempt(
        "lodes_wac",
        lambda: jobs_src.fetch_wac_tracts(state, cache, client=client, manifest=manifest),
    )
    out.od = attempt(
        "lodes_od", lambda: jobs_src.fetch_od_county(state, cache, client=client, manifest=manifest)
    )
    out.cbp = attempt(
        "cbp_county", lambda: jobs_src.fetch_cbp(state, cache, client=client, manifest=manifest)
    )
    jobs_dir = cache / "jobs"
    for sid, name in (
        ("lodes_wac", f"lodes_wac_{jobs_src.LODES_YEAR}_tracts_{fips}.csv"),
        ("lodes_od", f"lodes_od_{jobs_src.LODES_YEAR}_counties_{fips}.csv"),
        ("cbp_county", f"cbp_{jobs_src.CBP_YEAR}_county_{fips}.csv"),
    ):
        if (jobs_dir / name).exists():
            out.files.append((sid, jobs_dir / name))
    out.incentives = inc_src.fetch_incentives(state, cache, client=client, manifest=manifest)
    out.files += out.incentives.files
    out.notes += out.incentives.notes
    out.safety = attempt("fbi_cde_agency", lambda: safety_src.fetch_safety(
        state, cache, client=client, manifest=manifest))  # fmt: skip
    path = cache / "safety" / f"fbi_agencies_{safety_src.SAFETY_YEAR}_{fips}.csv"
    if path.exists():
        out.files.append(("fbi_cde_agency", path))
    elif out.safety is None:
        out.notes.append(
            "place: safety table left out (no api.data.gov key in API_DATA_GOV_KEY or "
            "FBI_API_KEY, and nothing cached)"
        )
    out.files.sort()
    return out


def place_tables(
    inputs: PlaceInputs | None,
    tracts: pd.DataFrame,
    counties: pd.DataFrame,
    tract_shapes: Sequence[tuple[str, dict[str, Any]]],
    blocks: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Build every place-profile table the inputs allow (pure: no file or network access).

    Args:
        inputs: The fetched inputs; ``None`` gives no tables.
        tracts: The pack's tracts (``geoid``).
        counties: The pack's counties (``geoid``, ``name``, ``population``).
        tract_shapes: ``(tract code, GeoJSON geometry)`` pairs of the pack's tracts.
        blocks: The pack's blocks (``tract``, ``housing_units``, ``lat``, ``lon``).
    """
    if inputs is None:
        return {}
    tract_ids = sorted(tracts["geoid"].astype(str))
    county_ids = sorted(counties["geoid"].astype(str))
    out = jobs_fx.jobs_tables(inputs.wac, inputs.od, inputs.cbp, tract_ids, county_ids)
    if inputs.incentives is not None:
        names = dict(zip(counties["geoid"].astype(str), counties["name"].astype(str), strict=True))
        out.update(
            inc_fx.incentive_tables(inputs.incentives, tract_ids, names, tract_shapes, blocks)
        )
    if inputs.safety is not None and len(inputs.safety):
        out["safety_county"] = safety_fx.safety_table(inputs.safety, counties)
    return out
