"""Water and sewer readiness per census tract, from official records only.

Plain-language summary
----------------------
A new home needs somewhere for its wastewater to go. Where public sewer is
available is decided by utilities and by each municipality's sewage facilities
plan, so this module never guesses it from distance to a pipe or a plant. It
asks one question per home: **does an official record place this home inside
a public sewer service area?** Homes are 2020 Census housing units by block; a
block is placed by its internal point.

Four answers, per home (then added up per tract and county):

- **verified**: inside a mapped public sewer service area published by a county
  or state body (Pennsylvania: Montgomery and Adams counties; Vermont: the
  state's known wastewater service areas), or in a place whose utility states
  it serves the whole jurisdiction (Philadelphia);
- **outside**: in a county whose published map is a complete inventory, but
  outside every service area (on-lot septic systems expected);
- **constraint**: inside an area with a documented moratorium, connection ban
  or capacity allocation on an official record (none found so far);
- **unknown**: no official record covers the home.

A tract's ``sewer_status`` is **verified** only when more than half of its
homes are verified (``status.verified_min_share`` in
``assumptions/infrastructure.yaml``). Otherwise it is whichever of the other
answers holds the most homes, ties going to the more cautious one
(constraint, then unknown, then outside). A tract with no 2020 homes has a
blank status.
``sewer_share`` is the share of homes verified; ``sewer_known`` the share any
official record speaks to. The same is done for public water service areas
(``water_share``; Pennsylvania DEP's statewide map), and, as context only, for
EPA's national sewersheds (``epa_share``), which are mostly modelled and which
EPA says are not for property-level decisions.

Pennsylvania also has the **Act 537** plan status: the date DEP last approved
each municipality's official sewage facilities plan. ``act537_class`` is the
plan-age class of the municipality holding most of a tract's homes
(``lt10``: under 10 years old, ``10to20``, ``20plus``, ``prereg``: only a
pre-1971 county plan, as of DEP's table date).

Nothing here measures treatment capacity, and an "outside" home may still be
served by a private community system or a future extension.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.features import planar

__all__ = [
    "CONTACT_COLUMNS",
    "COUNTY_COLUMNS",
    "STATUS_ORDER",
    "TABLES",
    "TRACT_COLUMNS",
    "VERIFIED_MIN_SHARE",
    "act537_by_cousub",
    "block_answers",
    "contacts_table",
    "county_table",
    "describe",
    "feature_index",
    "load_rules",
    "status_of",
    "tract_table",
]

ROOT = Path(__file__).resolve().parents[3]
ASSUMPTIONS = ROOT / "assumptions" / "infrastructure.yaml"
STATUS_ORDER = ("constraint", "unknown", "outside", "verified")
"""Tie order: the more cautious answer wins ("verified" also needs a majority of homes)."""
VERIFIED_MIN_SHARE = 0.5
"""Default share of homes "verified" must exceed (``status.verified_min_share`` in the rules)."""
ACT537_ORDER = ("lt10", "10to20", "20plus", "prereg")
SDWIS_URL = (
    "https://sdwis.epa.gov/ords/sfdw_pub/r/sfdw/sdwis_fed_reports_public/200?P200_PWSID={id}"
)


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/infrastructure.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- points in polygons


def feature_index(
    lon: np.ndarray, lat: np.ndarray, features: Sequence[dict[str, Any]]
) -> np.ndarray:
    """For each point, the index of the first feature whose outline holds it (-1 for none).

    A feature's rings are combined by the even-odd rule (a point inside an odd
    number of its rings is inside), so holes come out right without reading the
    ring direction. Points are first narrowed to each feature's bounding box,
    then tested by ray casting (:func:`visionhousing.features.planar.contains`).
    """
    x = np.asarray(lon, dtype=float)
    y = np.asarray(lat, dtype=float)
    out = np.full(len(x), -1, dtype=np.int64)
    order = np.argsort(x, kind="stable")
    xs = x[order]
    for k, feature in enumerate(features):
        rings = [r for r in feature.get("rings") or [] if len(r) >= 4]
        if not rings:
            continue
        coords = np.concatenate([np.asarray(r, dtype=float) for r in rings])
        lo, hi = np.searchsorted(xs, [coords[:, 0].min(), coords[:, 0].max()], side="left")
        hi = np.searchsorted(xs, coords[:, 0].max(), side="right")
        cand = order[lo:hi]
        cand = cand[(y[cand] >= coords[:, 1].min()) & (y[cand] <= coords[:, 1].max())]
        cand = cand[out[cand] < 0]
        if not len(cand):
            continue
        odd = planar.contains(x[cand], y[cand], [np.asarray(r, dtype=float) for r in rings])
        out[cand[odd]] = k
    return out


def _names(features: Sequence[dict[str, Any]], field: str) -> list[str]:
    return [str((f.get("attributes") or {}).get(field) or "").strip() for f in features]


# --------------------------------------------------------------------------- per block


def block_answers(
    blocks: pd.DataFrame,
    sewer_layers: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    service_records: Sequence[dict[str, Any]],
    constraint_records: Sequence[dict[str, Any]],
    water_layers: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    context: dict[str, Any] | None,
) -> pd.DataFrame:
    """The answer for every block with homes: sewer, water and EPA context.

    Args:
        blocks: Blocks with ``geoid``, ``tract``, ``housing_units``, ``lat``, ``lon``.
        sewer_layers: ``(rules entry, cached layer)`` pairs for official sewer maps.
        service_records: Whole-jurisdiction service statements (``counties``, ``name``, ``id``).
        constraint_records: Documented constraints (``counties`` and/or ``tracts``; ``id``).
        water_layers: ``(rules entry, cached layer)`` pairs for public water maps.
        context: The cached EPA sewershed layer, or ``None``.

    Returns:
        One row per block with ``units`` > 0 and columns ``tract``, ``county``,
        ``units``, ``sewer_in``, ``sewer_known``, ``sewer_source``, ``sewer_name``,
        ``constraint``, ``water_in``, ``water_known``, ``water_name``, ``water_id``,
        ``epa_in``, ``epa_id``, ``epa_name``.
    """
    b = blocks.copy()
    b["units"] = pd.to_numeric(b["housing_units"], errors="coerce").fillna(0)
    b = b[b["units"] > 0].reset_index(drop=True)
    b["tract"] = b["tract"].astype(str)
    b["county"] = b["tract"].str[:5]
    lon = pd.to_numeric(b["lon"], errors="coerce").to_numpy(float)
    lat = pd.to_numeric(b["lat"], errors="coerce").to_numpy(float)
    n = len(b)
    sewer_in = np.zeros(n, dtype=bool)
    sewer_known = np.zeros(n, dtype=bool)
    sewer_source = np.full(n, "", dtype=object)
    sewer_name = np.full(n, "", dtype=object)
    county = b["county"].to_numpy()
    for entry, layer in sewer_layers:
        feats = layer.get("features", [])
        idx = feature_index(lon, lat, feats)
        names = _names(feats, str(entry.get("name_field") or ""))
        covered = [str(c) for c in entry.get("counties") or []]
        scope = np.isin(county, covered) if covered else np.ones(n, dtype=bool)
        hit = (idx >= 0) & scope & ~sewer_in
        sewer_in |= hit
        sewer_name[hit] = [names[i] for i in idx[hit]]
        sewer_source[hit] = entry["id"]
        if entry.get("extent") in ("county", "state"):
            newly = scope & ~sewer_known
            sewer_source[newly & ~hit] = entry["id"]
            sewer_known |= scope
        sewer_known |= hit
    for rec in service_records:
        scope = np.isin(county, [str(c) for c in rec.get("counties") or []])
        sewer_in |= scope
        sewer_known |= scope
        sewer_source[scope] = rec["id"]
        sewer_name[scope] = rec.get("name", "")
    constraint = np.zeros(n, dtype=bool)
    for rec in constraint_records:
        constraint |= np.isin(county, [str(c) for c in rec.get("counties") or []])
        constraint |= np.isin(b["tract"].to_numpy(), [str(c) for c in rec.get("tracts") or []])
    water_in = np.zeros(n, dtype=bool)
    water_known = np.zeros(n, dtype=bool)
    water_name = np.full(n, "", dtype=object)
    water_id = np.full(n, "", dtype=object)
    for entry, layer in water_layers:
        feats = layer.get("features", [])
        idx = feature_index(lon, lat, feats)
        names = _names(feats, str(entry.get("name_field") or ""))
        ids = _names(feats, (list(entry.get("fields") or []) or [""])[0])
        covered = [str(c) for c in entry.get("counties") or []]
        scope = np.isin(county, covered) if covered else np.ones(n, dtype=bool)
        hit = (idx >= 0) & scope & ~water_in
        water_in |= hit
        water_name[hit] = [names[i] for i in idx[hit]]
        water_id[hit] = [ids[i] for i in idx[hit]]
        water_known |= scope if entry.get("extent") in ("county", "state") else hit
    epa_in = np.zeros(n, dtype=bool)
    epa_id = np.full(n, "", dtype=object)
    epa_name = np.full(n, "", dtype=object)
    if context is not None:
        feats = context.get("features", [])
        idx = feature_index(lon, lat, feats)
        epa_in = idx >= 0
        ids = _names(feats, "CWNS_ID")
        names = _names(feats, "FACILITY_NAME")
        epa_id[epa_in] = [ids[i] for i in idx[epa_in]]
        epa_name[epa_in] = [names[i] for i in idx[epa_in]]
    out = b[["geoid", "tract", "county", "units"]].copy()
    out["sewer_in"] = sewer_in
    out["sewer_known"] = sewer_known
    out["sewer_source"] = sewer_source
    out["sewer_name"] = sewer_name
    out["constraint"] = constraint
    out["water_in"] = water_in
    out["water_known"] = water_known
    out["water_name"] = water_name
    out["water_id"] = water_id
    out["epa_in"] = epa_in
    out["epa_id"] = epa_id
    out["epa_name"] = epa_name
    return out


def status_of(
    verified: float,
    outside: float,
    constraint: float,
    unknown: float,
    min_verified_share: float = VERIFIED_MIN_SHARE,
) -> str:
    """A tract's sewer status from its home counts per answer.

    "verified" needs more than ``min_verified_share`` of all homes. Otherwise
    the status is the other answer with the most homes, ties going to the more
    cautious answer (:data:`STATUS_ORDER`); with no homes at all, "unknown".
    """
    total = verified + outside + constraint + unknown
    if total <= 0:
        return "unknown"
    if verified > min_verified_share * total:
        return "verified"
    values = {"constraint": constraint, "outside": outside, "unknown": unknown}
    best = max(values.values())
    if best <= 0:
        return "unknown"
    return next(k for k in STATUS_ORDER if k in values and values[k] == best)


def _sums(answers: pd.DataFrame, key: str) -> pd.DataFrame:
    a = answers
    u = a["units"]
    c = a["constraint"]
    frame = pd.DataFrame(
        {
            key: a[key],
            "homes": u,
            "verified": u * (a["sewer_in"] & ~c),
            "outside": u * (a["sewer_known"] & ~a["sewer_in"] & ~c),
            "constraint": u * c,
            "unknown": u * (~a["sewer_known"] & ~c),
            "sewer_in": u * a["sewer_in"],
            "sewer_known": u * (a["sewer_known"] | c),
            "water_in": u * a["water_in"],
            "water_known": u * a["water_known"],
            "epa_in": u * a["epa_in"],
        }
    )
    return frame.groupby(key, sort=True).sum()


# --------------------------------------------------------------------------- Act 537


def _municipal_key(name: str) -> tuple[str, str]:
    words = re.sub(r"[^a-z0-9 ]", " ", str(name).lower()).split()
    kinds = {"township", "borough", "city", "town"}
    kind = next((w for w in words if w in kinds), "")
    base = " ".join(w for w in words if w not in kinds and w not in {"of", "the"})
    return base, kind


def act537_by_cousub(
    plans: pd.DataFrame, municipalities: pd.DataFrame, counties: pd.DataFrame
) -> pd.DataFrame:
    """Match DEP's plan-age rows to municipalities (county subdivisions).

    Names are compared without the words township, borough, city, town, "of"
    and punctuation, within the same county; the municipal type is used when
    two names would otherwise collide.

    Args:
        plans: :func:`visionhousing.data.sources.infrastructure.fetch_act537` rows.
        municipalities: The pack's ``municipal_planning`` table (``county``, ``cousub``, ``name``).
        counties: The pack's ``counties`` table (``geoid``, ``name``).

    Returns:
        ``county``, ``cousub``, ``approved``, ``plan_class`` (one row per matched municipality).
    """
    county_code = {
        str(n).lower().replace(" county", "").strip(): str(g)
        for g, n in zip(counties["geoid"], counties["name"], strict=True)
    }
    lookup: dict[tuple[str, str, str], str] = {}
    loose: dict[tuple[str, str], list[str]] = {}
    for c, s, name in zip(
        municipalities["county"], municipalities["cousub"], municipalities["name"], strict=True
    ):
        base, kind = _municipal_key(name)
        lookup[(str(c), base, kind)] = str(s)
        loose.setdefault((str(c), base), []).append(str(s))
    rows = []
    for _, p in plans.iterrows():
        county = county_code.get(str(p["county_name"]).lower().strip())
        if county is None:
            continue
        base, kind = _municipal_key(p["name"])
        cousub = lookup.get((county, base, kind))
        if cousub is None and len(loose.get((county, base), [])) == 1:
            cousub = loose[(county, base)][0]
        if cousub is not None:
            rows.append(
                {
                    "county": county,
                    "cousub": cousub,
                    "approved": p["approved"],
                    "plan_class": p["plan_class"],
                }
            )
    out = pd.DataFrame(rows, columns=["county", "cousub", "approved", "plan_class"])
    return out.drop_duplicates(["county", "cousub"]).reset_index(drop=True)


# --------------------------------------------------------------------------- tables

TRACT_COLUMNS: tuple[str, ...] = (
    "geoid",
    "sewer_status",
    "sewer_share",
    "sewer_known",
    "constraint_share",
    "sewer_source",
    "water_share",
    "epa_share",
    "act537_class",
)
"""Columns of ``infra_tract``, in order."""

COUNTY_COLUMNS: tuple[str, ...] = (
    "county",
    "homes",
    "tracts_verified",
    "tracts_outside",
    "tracts_constraint",
    "tracts_unknown",
    "homes_verified",
    "homes_outside",
    "homes_constraint",
    "homes_unknown",
    "water_share",
    "epa_share",
    "sewer_sources",
    "sewer_as_of",
    *(f"act537_{k}" for k in ACT537_ORDER),
    "act537_unmatched",
    "cwns_total",
    "cwns_treatment",
    "cwns_new_sewers",
    "cwns_sewer_repair",
    "cwns_cso",
    "cwns_stormwater",
    "cwns_decentralized",
    "cwns_nonpoint",
    "epa_plants",
)
"""Columns of ``infra_county``, in order."""

CONTACT_COLUMNS: tuple[str, ...] = (
    "county",
    "kind",
    "rank",
    "name",
    "homes",
    "contact_url",
    "source",
)
"""Columns of ``infra_contacts``, in order."""

_SOURCES = [
    "pa_montco_sewer_service_areas",
    "pa_adams_sewer_service_areas",
    "phl_pwd_wastewater",
    "vt_accd_wastewater_service_areas",
    "pa_dep_pws_service_areas",
    "pa_dep_act537_plan_status",
    "epa_sewersheds",
    "epa_cwns_2022",
    "tiger_2020_tabblock",
]

TABLES: dict[str, dict[str, object]] = {
    "infra_tract": {
        "key": ["geoid"],
        "sources": _SOURCES,
        "tier": "1",
        "description": (
            "Water and sewer readiness per tract from official records: the status holding "
            "most homes (verified / outside / constraint / unknown), the share of 2020 homes "
            "inside a mapped public sewer service area and the share any record speaks to, "
            "the share in a mapped public water service area, EPA's modelled sewershed share "
            "(context only) and the Act 537 plan-age class (Pennsylvania)."
        ),
    },
    "infra_county": {
        "key": ["county"],
        "sources": _SOURCES,
        "tier": "1",
        "description": (
            "Per county: tracts and homes by sewer status, water and EPA sewershed shares, "
            "the official layers used and their dates, homes by Act 537 plan age, EPA Clean "
            "Watersheds Needs Survey 2022 documented needs by category (January 2022 "
            "dollars) and the number of EPA sewershed treatment plants."
        ),
    },
    "infra_contacts": {
        "key": ["county", "kind", "rank"],
        "sources": [
            "pa_montco_sewer_service_areas",
            "pa_adams_sewer_service_areas",
            "phl_pwd_wastewater",
            "vt_accd_wastewater_service_areas",
            "pa_dep_pws_service_areas",
            "epa_sewersheds",
        ],
        "tier": "1",
        "description": (
            "The largest sewer and water providers per county by homes served, as the "
            "official layers name them, with an official listing page where one exists "
            "(EPA ECHO for treatment plants, EPA SDWIS for public water systems)."
        ),
    },
}
"""The three pack tables: key, sources, tier and description."""

_COLUMNS: dict[str, tuple[str, str]] = {
    "geoid": ("code", "Tract code: 5-digit county code + 6-digit tract code"),
    "county": ("code", "County code (5-digit FIPS)"),
    "sewer_status": (
        "category",
        "verified, outside, constraint or unknown: the sewer answer holding most of the "
        "tract's 2020 homes (official records only); blank for a tract with no homes",
    ),
    "sewer_share": (
        "share",
        "Share of 2020 homes inside a mapped public sewer service area or a utility's "
        "whole-jurisdiction service statement; blank where no official record speaks",
    ),
    "sewer_known": ("share", "Share of 2020 homes that an official sewer record speaks to"),
    "constraint_share": (
        "share",
        "Share of 2020 homes in an area with a documented sewer moratorium or connection ban",
    ),
    "sewer_source": ("source id", "Source id of the official sewer record used (blank: none)"),
    "water_share": (
        "share",
        "Share of 2020 homes inside a mapped public water supplier service area; blank where "
        "the state has no statewide map",
    ),
    "epa_share": (
        "share",
        "Share of 2020 homes inside an EPA sewershed (mostly modelled; context only)",
    ),
    "act537_class": (
        "category",
        "Act 537 plan age of the municipality with most of the tract's homes: lt10, 10to20, "
        "20plus or prereg (Pennsylvania DEP table date)",
    ),
    "homes": ("units", "2020 housing units (block counts)"),
    "tracts_verified": ("tracts", "Tracts whose status is verified"),
    "tracts_outside": ("tracts", "Tracts whose status is outside"),
    "tracts_constraint": ("tracts", "Tracts whose status is constraint"),
    "tracts_unknown": ("tracts", "Tracts whose status is unknown"),
    "homes_verified": ("units", "Homes inside a mapped public sewer service area"),
    "homes_outside": ("units", "Homes outside every service area of a complete county map"),
    "homes_constraint": ("units", "Homes in an area with a documented sewer constraint"),
    "homes_unknown": ("units", "Homes no official sewer record speaks to"),
    "sewer_sources": ("source ids", "Official sewer records used, joined by |"),
    "sewer_as_of": ("date", "Edition dates of those records, joined by |"),
    "act537_lt10": ("share", "Share of homes in municipalities whose plan is under 10 years old"),
    "act537_10to20": ("share", "Share of homes in municipalities whose plan is 10-20 years old"),
    "act537_20plus": ("share", "Share of homes in municipalities whose plan is 20+ years old"),
    "act537_prereg": (
        "share",
        "Share of homes in municipalities with only a pre-regulatory county plan",
    ),
    "act537_unmatched": ("share", "Share of homes in municipalities not matched to DEP's table"),
    "cwns_total": ("dollars", "CWNS 2022 documented needs, all categories (Jan 2022 dollars)"),
    "cwns_treatment": ("dollars", "CWNS 2022 needs: secondary and advanced treatment (I, II)"),
    "cwns_new_sewers": ("dollars", "CWNS 2022 needs: new collector and interceptor sewers (IV)"),
    "cwns_sewer_repair": (
        "dollars",
        "CWNS 2022 needs: infiltration/inflow and sewer replacement (III)",
    ),
    "cwns_cso": ("dollars", "CWNS 2022 needs: combined sewer overflow correction (V)"),
    "cwns_stormwater": ("dollars", "CWNS 2022 needs: stormwater (VI)"),
    "cwns_decentralized": ("dollars", "CWNS 2022 needs: decentralized (septic) systems (XII)"),
    "cwns_nonpoint": ("dollars", "CWNS 2022 needs: nonpoint source control (VII)"),
    "epa_plants": ("plants", "EPA sewershed endpoint treatment plants listed in the county"),
    "kind": ("category", "sewer (official map or statement), water or epa_sewershed (context)"),
    "rank": ("rank", "1 = most homes served in the county"),
    "name": ("text", "Provider or system name as the source gives it"),
    "contact_url": ("url", "Official listing page (EPA ECHO or SDWIS) where one exists"),
    "source": ("source id", "Source id of the layer naming the provider"),
}


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and plain description of one column, for the pack's data dictionary."""
    if name == "homes" and table == "infra_contacts":
        return "units", "2020 homes in the county inside this provider's mapped area"
    if name not in _COLUMNS:
        raise ValueError(f"no data-dictionary entry for {table}.{name}")
    return _COLUMNS[name]


def _round3(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round(3)


def tract_table(
    answers: pd.DataFrame,
    tract_ids: Sequence[str],
    act537_tract: pd.Series | None = None,
    rules: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """``infra_tract`` from the per-block answers (3-decimal shares).

    ``rules`` is ``assumptions/infrastructure.yaml`` (default: read from disk);
    its ``status.verified_min_share`` sets the majority "verified" needs.
    """
    rules = rules if rules is not None else load_rules()
    need = float((rules.get("status") or {}).get("verified_min_share", VERIFIED_MIN_SHARE))
    s = _sums(answers, "tract").reindex(list(tract_ids)).fillna(0)
    homes = s["homes"].where(s["homes"] > 0)
    known = s["sewer_known"]
    out = pd.DataFrame({"geoid": list(tract_ids)})
    out["sewer_status"] = [
        status_of(v, o, c, u, need) if h > 0 else ""
        for v, o, c, u, h in zip(
            s["verified"], s["outside"], s["constraint"], s["unknown"], s["homes"], strict=True
        )
    ]
    out["sewer_share"] = _round3((s["sewer_in"] / homes).where(known > 0)).to_numpy()
    out["sewer_known"] = _round3(known / homes).to_numpy()
    out["constraint_share"] = _round3(s["constraint"] / homes).to_numpy()
    first = (
        answers[answers["sewer_source"] != ""]
        .groupby("tract")["sewer_source"]
        .agg(lambda v: "|".join(sorted(set(v))))
    )
    out["sewer_source"] = out["geoid"].map(first).fillna("").to_numpy()
    out["water_share"] = _round3((s["water_in"] / homes).where(s["water_known"] > 0)).to_numpy()
    out["epa_share"] = _round3(s["epa_in"] / homes).to_numpy()
    out["act537_class"] = (
        "" if act537_tract is None else out["geoid"].map(act537_tract).fillna("").to_numpy()
    )
    return out[list(TRACT_COLUMNS)].sort_values("geoid").reset_index(drop=True)


def county_table(
    answers: pd.DataFrame,
    tracts: pd.DataFrame,
    counties: Sequence[str],
    sources_used: dict[str, str],
    act537_county: pd.DataFrame | None,
    cwns: pd.DataFrame | None,
    endpoints: pd.DataFrame | None,
) -> pd.DataFrame:
    """``infra_county``: block answers added up per county, with Act 537, CWNS and EPA plants."""
    s = _sums(answers, "county").reindex(list(counties)).fillna(0)
    t = tracts.assign(county=tracts["geoid"].str[:5])
    status_counts = t.groupby(["county", "sewer_status"]).size().unstack(fill_value=0)
    rows = []
    for county in counties:
        r = s.loc[county]
        homes = float(r["homes"])
        used = sorted(set(answers.loc[answers["county"] == county, "sewer_source"]) - {""})
        row: dict[str, Any] = {
            "county": county,
            "homes": round(float(homes)),
            **{
                f"tracts_{k}": int(status_counts.loc[county, k])
                if county in status_counts.index and k in status_counts.columns
                else 0
                for k in ("verified", "outside", "constraint", "unknown")
            },
            "homes_verified": round(float(r["verified"])),
            "homes_outside": round(float(r["outside"])),
            "homes_constraint": round(float(r["constraint"])),
            "homes_unknown": round(float(r["unknown"])),
            "water_share": round(r["water_in"] / homes, 3) if homes and r["water_known"] else None,
            "epa_share": round(r["epa_in"] / homes, 3) if homes else None,
            "sewer_sources": "|".join(used),
            "sewer_as_of": "|".join(sources_used.get(u, "") for u in used),
        }
        if act537_county is not None and county in act537_county.index:
            for k in (*ACT537_ORDER, "unmatched"):
                row[f"act537_{k}"] = act537_county.loc[county, k]
        if cwns is not None and county in cwns.index:
            c = cwns.loc[county]

            def need(*cols: str, _c: pd.Series = c) -> float:
                return float(sum(float(_c.get(k) or 0) for k in cols))

            row.update(
                {
                    "cwns_total": need("ALL_TOTAL"),
                    "cwns_treatment": need("I_NEED", "II_NEED"),
                    "cwns_new_sewers": need("IV_A_NEED", "IV_B_NEED"),
                    "cwns_sewer_repair": need("III_A_NEED", "III_B_NEED"),
                    "cwns_cso": need("V_NEED"),
                    "cwns_stormwater": need("STORMWATER_TOTAL"),
                    "cwns_decentralized": need("DECENTRALIZED_TOTAL"),
                    "cwns_nonpoint": need("NPS_TOTAL"),
                }
            )
        if endpoints is not None:
            row["epa_plants"] = int((endpoints["county"] == county).sum())
        rows.append(row)
    out = pd.DataFrame(rows).reindex(columns=list(COUNTY_COLUMNS))
    for col in out.columns:
        if col.startswith("cwns_"):
            out[col] = pd.to_numeric(out[col], errors="coerce").round()
    return out


def act537_tables(
    plans_by_cousub: pd.DataFrame, pieces: pd.DataFrame
) -> tuple[pd.Series, pd.DataFrame]:
    """Act 537 plan age per tract (main municipality) and home shares per county.

    Args:
        plans_by_cousub: :func:`act537_by_cousub` output.
        pieces: The pack's ``tract_municipalities`` table (tract piece per
            municipality with its 2020 housing units).

    Returns:
        ``(class per tract, county shares indexed by county with columns lt10,
        10to20, 20plus, prereg, unmatched)``.
    """
    p = pieces.copy()
    p["county"] = p["county"].astype(str)
    p["cousub"] = p["cousub"].astype(str)
    p["units"] = pd.to_numeric(p["housing_units_2020"], errors="coerce").fillna(0)
    p = p.merge(plans_by_cousub, on=["county", "cousub"], how="left")
    p["plan_class"] = p["plan_class"].fillna("unmatched")
    main = p.sort_values(["tract", "units", "cousub"], ascending=[True, False, True])
    main = main.drop_duplicates("tract").set_index("tract")["plan_class"].astype(str)
    main = main.where(main != "unmatched", "")
    shares = p.pivot_table(
        index="county", columns="plan_class", values="units", aggfunc="sum", fill_value=0
    )
    for k in (*ACT537_ORDER, "unmatched"):
        if k not in shares.columns:
            shares[k] = 0
    total = shares[[*ACT537_ORDER, "unmatched"]].sum(axis=1)
    shares = shares[[*ACT537_ORDER, "unmatched"]].div(total.where(total > 0), axis=0).round(3)
    return main, shares


def contacts_table(
    answers: pd.DataFrame, endpoints: pd.DataFrame | None, per_county: int
) -> pd.DataFrame:
    """``infra_contacts``: the largest providers per county and kind, with official links."""
    rows = []
    links = {}
    if endpoints is not None and len(endpoints):
        links = dict(zip(endpoints["CWNS_ID"].astype(str), endpoints["echo_url"], strict=True))
    groups = (
        ("sewer", "sewer_name", "sewer_in", "sewer_source", None),
        ("water", "water_name", "water_in", None, "water_id"),
        ("epa_sewershed", "epa_name", "epa_in", None, "epa_id"),
    )
    for kind, name_col, flag, src_col, id_col in groups:
        a = answers[answers[flag] & (answers[name_col] != "")]
        if a.empty:
            continue
        keys = ["county", name_col] + ([src_col] if src_col else []) + ([id_col] if id_col else [])
        g = a.groupby(keys, sort=True)["units"].sum().reset_index()
        g = g.sort_values(["county", "units", name_col], ascending=[True, False, True])
        for county, part in g.groupby("county", sort=True):
            for rank, (_, r) in enumerate(part.head(per_county).iterrows(), start=1):
                if kind == "water":
                    pws = str(r[id_col])
                    pws = pws if pws.startswith(("PA", "VT")) or not pws else f"PA{pws}"
                    url = SDWIS_URL.format(id=pws) if pws else ""
                    source = "pa_dep_pws_service_areas"
                elif kind == "epa_sewershed":
                    url = links.get(str(r[id_col]), "")
                    source = "epa_sewersheds"
                else:
                    url = ""
                    source = str(r[src_col])
                rows.append(
                    {
                        "county": county,
                        "kind": kind,
                        "rank": rank,
                        "name": str(r[name_col]),
                        "homes": round(float(r["units"])),
                        "contact_url": url,
                        "source": source,
                    }
                )
    return pd.DataFrame(rows, columns=list(CONTACT_COLUMNS))
