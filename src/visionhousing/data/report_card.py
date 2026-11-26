"""State data report card: for each model input, is it available, used or assumed, and how good?

Plain-language summary
----------------------
A state's results are only as good as its data. The report card lists every
input the model needs and says, for the state's pack:

- **status**: ``used`` (the pack has it and the engine reads it), ``available``
  (the pack has it as context or evidence, but the engine takes the number
  from the Assumption Ledger), ``assumed`` (no state data; a ledger parameter
  stands in), or ``missing`` (no data and no stand-in).
- **tier**: 0 = national files that exist for every state, 1 = published for
  many states (here, transit schedules), 2 = local refinement only.
- **vintage**: the edition of the source (survey years, release date, schedule
  date).
- **limits**: what the source cannot tell us, plus how complete it is in this
  state (for example, the share of tracts with a published median rent, or the
  share of bus stops whose departures could be counted).
- **value_state**: why a value is or is not there, in the shared words of
  :mod:`visionhousing.intake.value_state` (``observed``, ``true_zero``,
  ``missing``, ``suppressed``, ``imputed``, ``not_applicable``,
  ``feed_failure``). A table the data dictionary lists but the pack lacks, or
  a table with no rows, is a ``feed_failure`` and is never shown as "0%".
  ``row_states`` counts the same states row by row (for example permit
  offices whose counts the Census Bureau imputed). In a real state's pack, an
  add-on table the full build should have added (hazards, for example) that
  is absent is also a ``feed_failure``.

A table read from a copy a person saved by hand (Massachusetts files whose
publisher refuses scripted downloads) carries ``hand_saved: true``, a limits
sentence naming each such file and the edition it says it holds (or that the
edition is unknown), and "(hand-saved)" after its vintage in the Markdown.
Those hand-saved files are optional. When one was not supplied, the card says
"not supplied" in the limits (naming the file) and leaves its source out of the
vintage, so a table built without it never claims that file's edition.

Some inputs are one state's own tables (Pennsylvania's town rules, parcels
and bills; Massachusetts's Section 3A and ADU survey tables). They are listed
only for those states; elsewhere they go to ``not_applicable`` and are never
shown as missing.

It is built **from the pack alone**: the tables, ``data_dictionary.json`` and
(for real states) ``pack_manifest.json``. Nothing is fetched and the ledger is
not read; ledger ids are only named. It works for Testland (the synthetic test
state), whose files are plain CSV and which has no manifest.

Output: a JSON-ready dictionary (:func:`report_card`) and Markdown
(:func:`to_markdown`). Command line::

    uv run python -m visionhousing.data.report_card PA --out data/packs
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.intake.value_state import LABELS, OBSERVED, count_states

__all__ = ["INPUTS", "Input", "load_pack_files", "main", "report_card", "to_markdown"]

_TEXT = ("geoid", "county", "tract", "stop_id", "feed_id", "ntd_id", "schedule_feed")


@dataclass(frozen=True)
class Input:
    """One model input as the report card sees it.

    Attributes:
        id: Short name.
        label: Plain description.
        reforms: Which parts of the model use it.
        table: Pack table holding it ("" when the model always assumes it).
        columns: Columns that must be filled (prefixes ending in ``_`` match
            every column that starts with them).
        used: True when the engine reads it; False when it is context only.
        assumed_by: Ledger ids that stand in when there is no state data, or
            that the engine uses instead of the table.
        limits: Plain words on what it cannot tell us.
        sources: ``("tier2",)`` marks an input only local data could supply.
        derived_from: Tables the engine can compute the input from when the
            pack lacks its columns (for example Testland's transit access).
        states: Two-letter codes of the only states whose packs can hold this
            input (a state's own law or local tables). Empty means every state.
            Elsewhere the input is not listed as missing: it goes to the card's
            ``not_applicable`` list, unless the pack holds the table anyway.
        hand_file: The optional hand-saved file the table is built from alone
            (its name under ``data/raw/local/``); when the table is absent the
            card says the file was not supplied. Empty for scripted tables.
    """

    id: str
    label: str
    reforms: tuple[str, ...]
    table: str = ""
    columns: tuple[str, ...] = ()
    used: bool = True
    assumed_by: tuple[str, ...] = ()
    limits: str = ""
    sources: tuple[str, ...] = field(default=())
    derived_from: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    hand_file: str = ""

    def applies_to(self, state: str, tables: Mapping[str, pd.DataFrame] | None = None) -> bool:
        """True when this input can exist for ``state`` (or its table is in the pack anyway)."""
        if not self.states or state.upper() in self.states:
            return True
        return bool(self.table) and self.table in (tables or {})


INPUTS: tuple[Input, ...] = (
    Input(
        "permits",
        "Housing units authorized by building permits, by county, year and type",
        ("baseline", "all reforms"),
        "permits_county_year",
        ("units_1", "units_2", "units_3_4", "units_5plus"),
    ),
    Input(
        "housing_stock",
        "Existing homes by structure type, per tract",
        ("adu", "missing_middle", "parking", "fees"),
        "tracts",
        ("units_1_detached", "units_1_attached", "units_2", "units_3_4", "units_5_9"),
    ),
    Input(
        "rents",
        "Gross rents: brackets and medians by bedrooms, per tract",
        ("adu", "missing_middle", "fees", "parking"),
        "tracts",
        ("median_rent_b1", "median_rent_b2", "rent_"),
    ),
    Input(
        "home_values",
        "Owner home values: brackets and median, per tract",
        ("missing_middle", "fees", "parking"),
        "tracts",
        ("median_home_value", "value_"),
    ),
    Input(
        "lot_sizes",
        "Lot-size proxy and area type (from census blocks), per tract",
        ("adu", "missing_middle"),
        "tracts",
        ("lot_m2_per_unit_p25", "lot_m2_per_unit_p50", "lot_m2_per_unit_p75", "area_type"),
    ),
    Input(
        "blocks",
        "Census blocks: homes, land and an internal point",
        ("parking", "tod"),
        "blocks",
        ("housing_units", "land_area_m2", "lat", "lon"),
    ),
    Input(
        "transit_stops",
        "Transit stops and stations with their mode",
        ("parking", "tod"),
        "transit_stops",
        ("lat", "lon", "mode"),
    ),
    Input(
        "transit_frequency",
        "Weekday departures at bus stops (who counts as a frequent bus stop)",
        ("parking", "tod"),
        "transit_stops",
        ("weekday_departures",),
        assumed_by=("parking.transit_bus_min_departures",),
    ),
    Input(
        "transit_access",
        "Homes and land within 1/4, 1/2 and 3/4 mile of a major stop, per tract",
        ("parking", "tod"),
        "tracts",
        ("transit_units_d07", "transit_rich"),
        assumed_by=("parking.transit_rail_radius_miles", "parking.transit_bus_radius_miles"),
        derived_from=("blocks", "transit_stops"),
    ),
    Input(
        "house_price_trend",
        "House price index changes, per county and tract",
        ("context",),
        "prices_county",
        ("hpi_change_5yr_pct",),
        used=False,
    ),
    Input(
        "mortgage_rates",
        "Average offered mortgage rates by year",
        ("context",),
        "mortgage_rates_year",
        ("rate_30yr_pct",),
        used=False,
        assumed_by=("econ.mortgage_rate_30y",),
        limits="National only; the model's rate is the ledger entry, this table is its evidence.",
    ),
    Input(
        "construction_costs",
        "Construction wage index per county (local building-cost proxy)",
        ("context",),
        "costs_county",
        ("wage_index_county_vs_state",),
        used=False,
        assumed_by=(
            "adu.hard_cost_per_sqft",
            "missing_middle.hard_cost_per_sqft",
            "multifamily.hard_cost_per_sqft",
        ),
        limits="Not yet applied to the ledger's hard costs, which are statewide.",
    ),
    Input(
        "flood_zones",
        "Share of homes in FEMA high-risk flood zones (SFHA), per tract",
        ("context",),
        "hazard_tract",
        ("sfha_share",),
        used=False,
        limits=(
            "Context for the place profile; blank where FEMA has no digital flood map. "
            "The engine does not screen land by hazard."
        ),
    ),
    Input(
        "land_cover",
        "Developed land and impervious surface (NLCD land cover), per tract",
        ("context",),
        "hazard_tract",
        ("developed_share", "impervious_share"),
        used=False,
        limits="Context for the place profile; 30 m cells, one survey year.",
    ),
    Input(
        "slope",
        "Share of land on steep slopes (from the national elevation model), per tract",
        ("context",),
        "hazard_tract",
        ("steep_share",),
        used=False,
        limits="Context for the place profile; 30 m cells smooth out small steep lots.",
    ),
    Input(
        "zoning_single_family",
        "Share of residential land zoned for single-family homes only",
        ("missing_middle",),
        assumed_by=("missing_middle.sf_only_zoned_share",),
        limits="No statewide zoning map exists; a local zoning atlas would be a Tier 2 refinement.",
        sources=("tier2",),
    ),
    Input(
        "adu_already_legal",
        "Share of lots where an ADU is already allowed",
        ("adu",),
        assumed_by=("adu.already_legal_share",),
        limits="Judgement from local ordinances; no complete municipal inventory.",
        sources=("tier2",),
    ),
    Input(
        "adu_physical_fit",
        "Share of lots with room for a detached ADU (needs parcels)",
        ("adu",),
        assumed_by=(
            "adu.fit_share_sf_detached",
            "adu.fit_share_sf_attached",
            "adu.fit_share_two_to_four",
        ),
        limits="Parcel data are local (Tier 2); the share is a calibrated range.",
        sources=("tier2",),
    ),
    Input(
        "development_fees",
        "Development and impact fees per unit",
        ("fees",),
        assumed_by=("fees.fee_total_per_unit", "fees.pa_assumed_fee_per_unit_judgement"),
        limits="The benchmark $50,000 per unit; no statewide fee survey.",
        sources=("tier2",),
    ),
    Input(
        "municipal_rules",
        "Town-by-town zoning rules: ADU status, smallest lot, parking (local)",
        ("context",),
        "muni_zoning_rules",
        ("adu_status",),
        used=False,
        limits=(
            "Read town by town where codes are published; evidence for the ledger's "
            "statewide ranges, not a per-tract engine input."
        ),
        sources=("tier2",),
        states=("PA",),
    ),
    Input(
        "parcels",
        "County parcel records: land use and lot counts (local)",
        ("context",),
        "parcels_area",
        ("parcels",),
        used=False,
        limits="Published by some counties only; context for the place profile.",
        sources=("tier2",),
        states=("PA",),
    ),
    Input(
        "state_bills",
        "State law sections and pending bills bearing on a reform",
        ("state bills",),
        "state_law_provisions",
        ("citation",),
        used=False,
        limits=(
            "Bill runs are offered only for a state whose bills were read; elsewhere "
            "only the five benchmark reforms run."
        ),
        sources=("tier2",),
        states=("PA",),
    ),
    Input(
        "ma_3a_districts",
        "Massachusetts Section 3A districts: multi-family zoning allowed by right (MBTA "
        "communities)",
        ("tod", "missing_middle"),
        "mbta_3a_districts",
        ("capacity_district", "acres"),
        used=False,
        limits=(
            "Massachusetts only. The MassGIS atlas holds only districts found compliant; "
            "capacity is the state's zoning-capacity estimate, not homes built."
        ),
        sources=("tier2",),
        states=("MA",),
    ),
    Input(
        "ma_3a_status",
        "Massachusetts Section 3A compliance status, per MBTA community",
        ("context",),
        "mbta_3a_status",
        ("compliance_status",),
        used=False,
        limits=(
            "Massachusetts only. Without the state's status sheet (www.mass.gov refuses "
            "scripted downloads; a hand-saved copy is read when present) only the compliant "
            "towns in the atlas are listed."
        ),
        sources=("tier2",),
        states=("MA",),
    ),
    Input(
        "ma_adu_survey",
        "Massachusetts ADU survey: ADUs approved by town since the 2024 law",
        ("adu",),
        "ma_adu_survey",
        ("approvals",),
        used=False,
        limits=(
            "Massachusetts only; evidence for the ADU take-up range. www.mass.gov refuses "
            "scripted downloads, so the table is built only from a hand-saved copy of the "
            "survey file."
        ),
        sources=("tier2",),
        states=("MA",),
        hand_file="ma/ma_adu_survey.csv",
    ),
    Input(
        "parking_minimums",
        "Parking spaces required per unit",
        ("parking", "tod"),
        assumed_by=(
            "parking.required_spaces_per_unit_multifamily",
            "parking.required_spaces_per_unit_attached",
        ),
        limits="Set by each municipality; one typical value with a range.",
        sources=("tier2",),
    ),
)
"""Every input the report card checks, in display order."""


# --------------------------------------------------------------------------- reading


def _read_table(folder: Path, name: str) -> pd.DataFrame | None:
    for path in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
        if path.is_file():
            return pd.read_csv(path, dtype={c: str for c in _TEXT}, keep_default_na=True)
    return None


def load_pack_files(folder: Path) -> dict[str, Any]:
    """Read what the report card needs from a pack folder (tables, dictionary, manifest)."""
    folder = Path(folder)
    tables = {}
    for name in sorted({i.table for i in INPUTS if i.table} | {"transit_feeds"}):
        frame = _read_table(folder, name)
        if frame is not None:
            tables[name] = frame
    dictionary = json.loads((folder / "data_dictionary.json").read_text(encoding="utf-8"))
    manifest_path = folder / "pack_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    )
    return {"tables": tables, "dictionary": dictionary, "manifest": manifest}


# --------------------------------------------------------------------------- the card


def _columns(frame: pd.DataFrame, wanted: Sequence[str]) -> list[str]:
    out: list[str] = []
    for w in wanted:
        if w.endswith("_"):
            out += [
                c
                for c in frame.columns
                if c.startswith(w) and not c.endswith(("_moe", "_method")) and c != "rent_no_cash"
            ]
        elif w in frame.columns:
            out.append(w)
    return out


def _coverage(item: Input, frame: pd.DataFrame) -> tuple[float, str]:
    """Share of rows where the input is filled, and a sentence saying so."""
    if item.id == "transit_frequency":
        bus = frame[frame["mode"].astype(str) == "bus"]
        if "frequency" in frame.columns:
            counted = int((bus["frequency"].astype(str) == "counted").sum())
        else:
            counted = int(bus["weekday_departures"].notna().sum())
        share = counted / len(bus) if len(bus) else 0.0
        return share, f"departures counted for {counted:,} of {len(bus):,} bus stops ({share:.0%})"
    cols = _columns(frame, item.columns)
    if not cols or frame.empty:
        return 0.0, "no rows"
    filled = frame[cols].notna().all(axis=1)
    share = float(filled.mean())
    return share, f"{share:.0%} of {len(frame):,} {item.table.replace('_', ' ')} rows filled"


def _sources_for(item: Input, dictionary: Mapping[str, Any]) -> tuple[list[str], str]:
    srcs: list[str] = []
    tiers: list[str] = []
    for c in dictionary.get("columns", []):
        if c.get("table") == item.table and (
            c.get("name") in item.columns
            or any(w.endswith("_") and str(c.get("name")).startswith(w) for w in item.columns)
        ):
            srcs += [s.strip() for s in str(c.get("source", "")).split(",") if s.strip()]
            tiers.append(str(c.get("tier", "0")))
    return sorted(set(srcs)), (max(tiers) if tiers else "0")


def _vintage(srcs: Sequence[str], manifest: Mapping[str, Any] | None, synthetic: bool) -> str:
    if synthetic:
        return "synthetic"
    rows = {r["id"]: r for r in (manifest or {}).get("provenance", [])}
    parts = [f"{s}: {rows[s]['version']}" for s in srcs if s in rows]
    return "; ".join(parts) or "unknown"


def _limits(srcs: Sequence[str], manifest: Mapping[str, Any] | None) -> list[str]:
    rows = {r["id"]: r for r in (manifest or {}).get("provenance", [])}
    return [str(rows[s]["limits"]).strip() for s in srcs if s in rows and rows[s].get("limits")]


def _schedule_dates(tables: Mapping[str, pd.DataFrame]) -> str:
    feeds = tables.get("transit_feeds")
    if feeds is None or feeds.empty or "schedule_date" not in feeds.columns:
        return ""
    used = feeds[feeds["bus_stops_counted"].fillna(0) > 0]["schedule_date"].dropna()
    return f"schedules of {used.min()} to {used.max()}" if len(used) else ""


def _state_notes(item: Input, state: str, tables: Mapping[str, pd.DataFrame]) -> list[str]:
    """Extra sentences for a real state whose data are thinner than the calibration state's.

    Reads only the state codes of the calibration files (which state was measured),
    never their numbers, so the card still depends on the pack alone otherwise.
    """
    if not state or state == "TL":
        return []
    out: list[str] = []
    if item.id == "adu_physical_fit":
        from visionhousing.engine.context import load_transfer_rules

        for rule in load_transfer_rules():
            measured = [str(s).upper() for s in rule.get("measured_in", ())]
            if state not in measured and str(rule["parameter"]).startswith("adu.fit"):
                out.append(
                    f"No lot measurements for {state}: the fit table measured in "
                    f"{', '.join(measured)} is borrowed and its range widened "
                    f"({rule['parameter']}: {rule['low']} to {rule['high']}; "
                    "assumptions/transfer.yaml)."
                )
    if item.id == "adu_already_legal":
        from visionhousing.scenarios.adu import load_legal_shares

        tables = load_legal_shares()
        own = tables.get(state)
        if own is None:
            measured = ", ".join(sorted(tables)) or "another state"
            out.append(
                f"No town-rule index or state-law table for {state}: the ledger's national "
                f"default range stands in (the tables for {measured} are used only there)."
            )
        elif own.kind != "calibrated":
            out.append(
                f"No town-rule index for {state}: the range comes from state law "
                f"({own.id}; {own.sources[0]['citation'] if own.sources else 'see the file'}), "
                "with compliance unmeasured, so it stays a range below 100%."
            )
    if item.id == "construction_costs":
        frame = tables.get("costs_county")
        if frame is not None and "wage_index_method" in frame.columns and len(frame):
            method = frame["wage_index_method"].astype(str)
            shown = int((method == "county").sum())
            line = f"BLS published construction pay for {shown} of {len(frame)} counties"
            if method.isin(["state_from_counties", "national_value"]).any():
                line += (
                    "; the statewide figure was withheld and is estimated from the "
                    "published counties"
                )
            out.append(line + ".")
    if item.id in ("transit_frequency", "transit_access"):
        out += _transit_notes(item.id, state, tables)
    if item.id == "permits":
        frame = tables.get("permits_county_year")
        if frame is not None and {"imputed", "year"} <= set(frame.columns) and len(frame):
            recent = frame[frame["year"] >= frame["year"].max() - 4]
            share = float(pd.to_numeric(recent["imputed"], errors="coerce").fillna(0).gt(0).mean())
            out.append(
                f"{share:.0%} of county-years in the last five years carry Census Bureau "
                "imputation for permit offices that did not report."
            )
    return out


def _unknown_frequency_tracts(tables: Mapping[str, pd.DataFrame] | None) -> set[str]:
    """Tracts holding a bus stop whose weekday departures were not counted.

    Such a stop may run frequent service; nobody published a schedule that
    could show it. A zero for transit access in such a tract is therefore not
    a measured zero.
    """
    stops = (tables or {}).get("transit_stops")
    if stops is None or stops.empty or not {"mode", "frequency", "tract"} <= set(stops.columns):
        return set()
    unknown = (stops["mode"].astype(str) == "bus") & stops["frequency"].astype(str).isin(
        ["unknown", "stale"]
    )
    return {str(t).split(".")[0] for t in stops.loc[unknown, "tract"].dropna()}


def _transit_notes(item_id: str, state: str, tables: Mapping[str, pd.DataFrame]) -> list[str]:
    """Say why transit frequency or access is thin: no schedule feeds, no rail.

    A state whose agencies publish no keyless schedule (GTFS) feed has bus stops
    but no departure counts, so no bus stop can be shown to be a major stop. With
    no rail station either, transit access is zero for want of a schedule, not
    because frequent service was measured and found absent.
    """
    feeds, stops = tables.get("transit_feeds"), tables.get("transit_stops")
    if feeds is None or stops is None or feeds.empty or stops.empty:
        return []
    if not {"schedule_feed", "mode", "frequency"} <= set(feeds.columns) | set(stops.columns):
        return []
    agencies = len(feeds)
    no_feed = int(feeds["schedule_feed"].isna().sum())
    rail = int((stops["mode"].astype(str) == "rail").sum())
    major = int(pd.to_numeric(stops.get("major"), errors="coerce").fillna(0).gt(0).sum())
    unknown = int(
        (
            (stops["mode"].astype(str) == "bus")
            & stops["frequency"].astype(str).isin(["unknown", "stale"])
        ).sum()
    )
    out: list[str] = []
    if item_id == "transit_frequency" and no_feed:
        out.append(
            f"No keyless schedule (GTFS) feed matched {no_feed} of {agencies} transit "
            f"agencies in {state}. {unknown:,} bus stops have no counted departures (no "
            "feed, or a schedule that had expired when read): their frequency is unknown "
            "(missing, not zero), so they cannot count as frequent bus stops."
        )
    if item_id == "transit_access" and major == 0:
        why = (
            f"no rail station, and departures were counted at no bus stop ({unknown:,} "
            "bus stops have no published schedule)"
            if rail == 0
            else f"{rail:,} rail stops but none qualifies"
        )
        out.append(
            f"No major transit stops in {state}: {why}. Transit-oriented density has no "
            "eligible land and adds no homes here; read that as 'not measurable with "
            "published schedules', not as a measured absence of frequent service."
        )
    return out


def _row_states(
    item: Input, frame: pd.DataFrame, tables: Mapping[str, pd.DataFrame] | None = None
) -> dict[str, int]:
    """Count the value states over an input's rows (``intake.value_state``).

    A row is blank (``missing``) when any of the input's columns is empty, a
    true zero when every column is zero, ``imputed`` where the publisher or the
    build flagged an estimate (permit offices that did not report; county pay
    estimated from other counties) and ``suppressed`` where a method column
    says the value was withheld. Text columns (a mode, an area type) count as
    filled when they hold any text. For transit access, a zero in a tract that
    holds a bus stop with uncounted departures is ``missing``, not a true zero:
    the stop may be frequent, but no schedule says so.
    """
    cols = _columns(frame, item.columns)
    if not cols or frame.empty:
        return {}
    raw = frame[cols]
    empty = raw.isna() | raw.astype(str).apply(lambda c: c.str.strip()).eq("")
    values = raw.apply(pd.to_numeric, errors="coerce")
    values = values.mask(values.isna() & ~empty, 1.0)
    blank = empty.any(axis=1)
    if item.id == "transit_access" and "geoid" in frame.columns:
        unknown = _unknown_frequency_tracts(tables)
        tract = frame["geoid"].astype(str).str.split(".").str[0]
        blank = blank | (tract.isin(unknown) & values.abs().sum(axis=1).eq(0))
    summary = values.abs().sum(axis=1).where(~blank)
    imputed = [False] * len(frame)
    suppressed = [False] * len(frame)
    if "imputed" in frame.columns:
        imputed = list(pd.to_numeric(frame["imputed"], errors="coerce").fillna(0).gt(0))
    for col in [c for c in frame.columns if c.endswith("_method") and c[:-7] in cols]:
        method = frame[col].astype(str).str.lower()
        held = method.str.contains("suppress")
        suppressed = [a or b for a, b in zip(suppressed, held, strict=True)]
        est = method.isin(["state_value", "state_from_counties", "national_value"])
        imputed = [a or b for a, b in zip(imputed, est, strict=True)]
    return count_states(list(summary), imputed=imputed, suppressed=suppressed)


def _addon_tables() -> set[str]:
    """Tables a full build of a real state adds after the core pack (``pack.RETROFITS``).

    A full build (``make data``) runs every add-on step (districts, affordability,
    infrastructure, hazards, transit walk); a step whose source fails is skipped
    and its tables are left out. So in a real state's pack such a table being
    absent is a feed failure (for example the land-cover service timing out),
    not a gap in what the state publishes.
    """
    from visionhousing.data.pack import RETROFITS

    return {t for _name, tables, _step in RETROFITS for t in tables}


ROUTE_SOURCES = {
    "status": ("ma_3a_compliance_status", "ma/mbta3a_compliance_status.csv"),
    "categories": ("ma_3a_categories", "ma/mbta3a_categories.csv"),
    "tracker": ("ma_3a_development_tracker", "ma/mbta3a_development_tracker.csv"),
    "survey": ("ma_adu_survey_file", "ma/ma_adu_survey.csv"),
}
"""``<name>_route`` column prefix -> (source id, optional hand-saved file under data/raw/local)."""


def _not_supplied(frame: pd.DataFrame | None) -> tuple[list[str], set[str]]:
    """Sentences and source ids for each optional file ``frame`` was built without.

    A ``<name>_route`` column that is blank in every row (or ``atlas`` for the
    status list, built from the map service instead) means the file was not
    read: the publisher refused and no hand-saved copy was supplied.
    """
    if frame is None or frame.empty:
        return [], set()
    out: list[str] = []
    unread: set[str] = set()
    for column in sorted(c for c in frame.columns if str(c).endswith("_route")):
        name = str(column)[: -len("_route")]
        if name not in ROUTE_SOURCES:
            continue
        values = {str(v).strip() for v in frame[column].dropna()} - {""}
        if values and values != {"atlas"}:
            continue
        source, file = ROUTE_SOURCES[name]
        unread.add(source)
        out.append(
            f"not supplied: the optional {name} file (data/raw/local/{file}) was not read, "
            "so what it adds is left out; this is a gap, not a zero"
        )
    return out, unread


def _hand_saved(frame: pd.DataFrame | None) -> list[str]:
    """Sentences for each file in ``frame`` read from a hand-saved copy.

    A table says how each of its files was read in a ``<name>_route`` column
    (``publisher``, ``hand_saved``, ...) next to a ``<name>_as_of`` column with
    the edition the copy holds (empty when it did not say).
    """
    if frame is None or frame.empty:
        return []
    out: list[str] = []
    for column in sorted(c for c in frame.columns if str(c).endswith("_route")):
        if not frame[column].astype(str).eq("hand_saved").any():
            continue
        name = str(column)[: -len("_route")]
        dates = set()
        if f"{name}_as_of" in frame.columns:
            dates = {str(v) for v in frame[f"{name}_as_of"].dropna() if str(v).strip()}
        edition = (
            f"edition as of {', '.join(sorted(dates))}"
            if dates
            else "edition unknown (the copy does not say; see its .edition.yaml note)"
        )
        out.append(
            f"hand-saved: the {name} file was read from a copy a person saved from the "
            f"publisher's page ({edition}); it is not re-checked by script"
        )
    return out


def _value_state(
    item: Input,
    frame: pd.DataFrame | None,
    coverage: float | None,
    promised: set[str],
) -> str:
    """The input's value state: never a zero when the feed failed or the data are absent.

    ``feed_failure`` when the pack's data dictionary lists the table but the
    file is absent, when a real state's pack lacks a table its full build adds
    (:func:`_addon_tables`), or when the file has no rows; ``missing`` when the pack holds no
    such data (a ledger value may stand in); ``observed`` when some rows are
    filled.
    """
    if coverage is not None and coverage > 0:
        return OBSERVED
    if not item.table:
        return "missing"
    if frame is None:
        return "feed_failure" if item.table in promised else "missing"
    if frame.empty:
        return "feed_failure"
    return "missing"


def report_card(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Build the report card from :func:`load_pack_files` output.

    Inputs that exist only for other states (``Input.states``) are left out of
    ``inputs`` and the counts; they are listed under ``not_applicable`` with the
    value state ``not_applicable``, so a state is never shown as missing another
    state's own tables.

    Returns:
        ``{"state", "synthetic", "pack_hash", "counts", "inputs": [...],
        "not_applicable": [...]}``; each
        input has ``id``, ``label``, ``reforms``, ``status``, ``tier``,
        ``vintage``, ``coverage`` (0-1 or ``None``), ``limits`` (list of text),
        ``sources``, ``assumed_by`` (ledger ids) and ``hand_saved`` (True when
        the table was read from a copy a person saved by hand) and
        ``not_supplied`` (True when an optional hand-saved file was not supplied).
    """
    tables, dictionary, manifest = pack["tables"], pack["dictionary"], pack["manifest"]
    synthetic = bool(dictionary.get("synthetic"))
    promised = {str(t.get("table")) for t in dictionary.get("tables", []) if t.get("table")}
    if manifest is not None and not synthetic:
        promised |= _addon_tables()
    state = str(dictionary.get("state", "")).upper()
    rows = []
    elsewhere = []
    for item in INPUTS:
        if not item.applies_to(state, tables):
            elsewhere.append(
                {
                    "id": item.id,
                    "label": item.label,
                    "states": list(item.states),
                    "value_state": "not_applicable",
                }
            )
            continue
        frame = tables.get(item.table) if item.table else None
        srcs, tier = _sources_for(item, dictionary) if item.table else ([], "2")
        coverage: float | None = None
        limits: list[str] = []
        if frame is not None and _columns(frame, item.columns) and len(frame):
            coverage, sentence = _coverage(item, frame)
            limits.append(sentence)
        derivable = (
            coverage is None
            and bool(item.derived_from)
            and all(len(tables.get(t, ())) for t in item.derived_from)
        )
        if derivable:
            coverage = 1.0
            limits.append(
                "not stored in this pack; computed at run time from "
                + " and ".join(t.replace("_", " ") for t in item.derived_from)
            )
            srcs = sorted(
                {
                    s
                    for t in item.derived_from
                    for s in _sources_for(Input(t, "", (), t, ("lat",)), dictionary)[0]
                }
            )
        has_data = coverage is not None and coverage > 0
        if has_data:
            status = "used" if item.used else "available"
        else:
            status = "assumed" if item.assumed_by else "missing"
        if not has_data and item.table:
            if frame is not None and len(frame):
                limits.append(
                    f"the {item.table.replace('_', ' ')} table is in this pack, "
                    "but these columns are not filled"
                )
            else:
                limits.append(f"no {item.table.replace('_', ' ')} data in this pack")
        if item.limits:
            limits.append(item.limits)
        limits += _state_notes(item, state, tables)
        limits += _limits(srcs, manifest)
        value_state = _value_state(item, frame, coverage, promised)
        if value_state == "feed_failure":
            limits.append(
                f"the {item.table.replace('_', ' ')} table was expected in this pack but is "
                "absent or empty: a feed failure, not a zero"
            )
        hand = _hand_saved(frame) if item.table else []
        limits += hand
        absent, unread = _not_supplied(frame) if item.table else ([], set())
        if item.hand_file and frame is None:
            absent.append(
                f"not supplied: built only from an optional hand-saved file "
                f"(data/raw/local/{item.hand_file}); none was supplied, so this input is "
                "left out, not counted as zero"
            )
        limits += absent
        srcs = [s for s in srcs if s not in unread]
        vintage = _vintage(srcs, manifest, synthetic) if item.table else "ledger"
        if item.id == "transit_frequency" and has_data and not synthetic:
            vintage = "; ".join(v for v in (vintage, _schedule_dates(tables)) if v)
        rows.append(
            {
                "id": item.id,
                "label": item.label,
                "reforms": list(item.reforms),
                "status": status,
                "tier": 2 if item.sources == ("tier2",) else int(tier),
                "vintage": vintage if has_data else "ledger",
                "coverage": None if coverage is None else round(coverage, 4),
                "value_state": value_state,
                "hand_saved": bool(hand),
                "not_supplied": bool(absent),
                "row_states": _row_states(item, frame, tables) if frame is not None else {},
                "limits": limits,
                "sources": srcs,
                "assumed_by": list(item.assumed_by),
            }
        )
    counts = {s: sum(r["status"] == s for r in rows) for s in ("used", "available", "assumed")}
    counts["missing"] = sum(r["status"] == "missing" for r in rows)
    states = sorted({r["value_state"] for r in rows})
    return {
        "value_states": {s: sum(r["value_state"] == s for r in rows) for s in states},
        "state": dictionary.get("state", ""),
        "synthetic": synthetic,
        "pack_hash": (manifest or {}).get("pack_hash"),
        "counts": counts,
        "inputs": rows,
        "not_applicable": elsewhere,
    }


def to_markdown(card: Mapping[str, Any]) -> str:
    """Render the report card as a Markdown table plus a limits list."""
    title = f"# State data report card: {card['state']}"
    lines = [title, ""]
    if card["synthetic"]:
        lines += ["Synthetic test state (Testland). Not real data.", ""]
    if card.get("pack_hash"):
        lines += [f"Pack hash `{card['pack_hash']}`.", ""]
    c = card["counts"]
    lines += [
        f"{c['used']} inputs used, {c['available']} available as context, "
        f"{c['assumed']} assumed from the ledger, {c['missing']} missing.",
        "",
    ]
    failed = card.get("value_states", {}).get("feed_failure", 0)
    if failed:
        lines += [f"{failed} input(s) failed to load: shown as a feed failure, never as zero.", ""]
    lines += [
        "| Input | Status | Tier | Vintage | Coverage | Used by |",
        "|---|---|---|---|---|---|",
    ]
    for r in card["inputs"]:
        state = r.get("value_state", OBSERVED)
        if state != OBSERVED and not r["coverage"]:
            cov = LABELS[state]  # never "0%" for absent data or a failed feed
        else:
            cov = "-" if r["coverage"] is None else f"{r['coverage']:.0%}"
        vintage = str(r["vintage"]).replace("|", "/")
        if r.get("hand_saved"):
            vintage += " (hand-saved)"
        lines.append(
            f"| {r['label']} | {r['status']} | {r['tier']} | {vintage} | {cov} | "
            f"{', '.join(r['reforms'])} |"
        )
    other = card.get("not_applicable", [])
    if other:
        lines += [
            "",
            "Does not apply to this state (another state's own tables): "
            + "; ".join(f"{r['label']} ({', '.join(r['states'])} only)" for r in other)
            + ".",
        ]
    lines += ["", "## Limits", ""]
    for r in card["inputs"]:
        lines.append(f"- **{r['label']}** ({r['status']}).")
        lines += [f"  - {text}" for text in r["limits"]]
        if r["assumed_by"]:
            lines.append(f"  - Ledger: {', '.join(f'`{i}`' for i in r['assumed_by'])}.")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Print or write the report card for a state code (``TL`` for Testland)."""
    from visionhousing.engine.context import pack_dir

    parser = argparse.ArgumentParser(description="State data report card")
    parser.add_argument("state", help="state code, for example PA, or TL for Testland")
    parser.add_argument("--out", type=Path, help="folder to write <STATE>_report_card.json/.md")
    args = parser.parse_args(argv)
    card = report_card(load_pack_files(pack_dir(args.state)))
    md = to_markdown(card)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stem = args.out / f"{args.state.upper()}_report_card"
        stem.with_suffix(".json").write_text(json.dumps(card, indent=1) + "\n", encoding="utf-8")
        stem.with_suffix(".md").write_text(md, encoding="utf-8")
    else:
        print(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
