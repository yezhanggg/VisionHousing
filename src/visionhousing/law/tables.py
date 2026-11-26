"""Build the four law tables and add them to a state pack.

Plain-language summary
----------------------
Four small tables (tier 2, Pennsylvania only for now):

========================= =========================================== ===================
table                     one row per                                 built from
========================= =========================================== ===================
``law_documents``         legal text read or linked                   state_provisions.yaml,
                                                                      municipal_rules.yaml,
                                                                      the fetched MPC text
``state_law_provisions``  MPC section or bill bearing on a reform     state_provisions.yaml,
                                                                      bill pages
``county_land_use``       county (all 67)                             DCED Munstats
                                                                      (``municipal_planning``),
                                                                      ``assumptions/plans.yaml``
``muni_zoning_rules``     municipality part (every DCED municipality) DCED Munstats,
                                                                      municipal_rules.yaml
========================= =========================================== ===================

``muni_zoning_rules`` has a row for every municipality. Those nobody has read
are ``method: unread`` and ``adu_status: unknown`` with every other rule empty:
unknown is never shown as "no". Each row also carries two weights worked out
from the pack's own tables (:func:`visionhousing.law.coverage.muni_weights`):
``sf_lots`` (single-family lots) and ``res_land_ha`` (residential land), and the
rule-change tracker fields (adoption and effective dates, binding conditions,
``future_effective``). ``county_land_use`` adds, per county, the residents,
lots and land under a verified reading, so coverage is reported three ways.

:func:`add_law_to_pack` writes the tables into an existing pack folder and
rewrites its data dictionary and manifest, without re-fetching anything else.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.sources import legislation
from visionhousing.features.zoning_status import read_plan_citations
from visionhousing.law.coverage import county_coverage, muni_weights
from visionhousing.law.schema import (
    CountyLandUse,
    LawDocument,
    MuniRule,
    StateProvision,
    is_future,
)
from visionhousing.law.state import build_state_law

__all__ = [
    "COLUMNS",
    "MUNI_RULES_PATH",
    "TABLES",
    "VERSION_COLUMNS",
    "VERSION_TABLE",
    "LawTables",
    "add_law_to_pack",
    "add_provision_versions",
    "build_law_tables",
    "county_land_use",
    "load_municipal_rules",
    "muni_zoning_rules",
    "municipal_documents",
    "pack_weights",
    "provision_version_table",
    "weigh_law_tables",
]

ROOT = Path(__file__).resolve().parents[3]
MUNI_RULES_PATH = ROOT / "assumptions" / "law" / "municipal_rules.yaml"

TABLES: dict[str, dict[str, object]] = {
    "law_documents": {
        "key": ["doc_id"],
        "sources": [
            "pa_mpc",
            "pa_code_bulletin",
            "pa_hb2186",
            "pa_sb1346",
            "pa_hb2155",
            "pa_hb1459",
            "pgh_legistar",
            "phl_li_adu_checklist",
            "pa_municipal_ordinances",
        ],
        "description": (
            "Legal texts read for the law tables (state code, bill pages, municipal "
            "ordinances), with address, licence note, how they were read and a text checksum."
        ),
        "tier": "2",
    },
    "state_law_provisions": {
        "key": ["provision_id"],
        "sources": ["pa_mpc", "pa_hb2186", "pa_sb1346", "pa_hb2155", "pa_hb1459"],
        "description": (
            "State planning-code sections and current bills bearing on each benchmark reform: "
            "citation, status, last action and a summary in our own words."
        ),
        "tier": "2",
    },
    "county_land_use": {
        "key": ["county"],
        "sources": ["pa_dced_munstats", "census_subcounty_pop", "county_comprehensive_plans"],
        "description": (
            "Per county: municipalities and residents under the county's zoning ordinance or "
            "none, and the county comprehensive plan with its links."
        ),
        "tier": "2",
    },
    "muni_zoning_rules": {
        "key": ["geoid"],
        "sources": [
            "pa_dced_munstats",
            "pa_municipal_ordinances",
            "pgh_legistar",
            "phl_li_adu_checklist",
        ],
        "description": (
            "Per municipality: ADU rule, smallest residential minimum lot, parking minimums, "
            "housing types and impact-fee ordinance, with citation, quote, method and review "
            "status. Unread municipalities are unknown."
        ),
        "tier": "2",
    },
}
"""The law tables: key, source ids, description and tier."""

COLUMNS: dict[str, tuple[str, str]] = {
    "doc_id": ("code", "Document id"),
    "title": ("text", "Document title"),
    "level": ("category", "state, county or municipality"),
    "publisher": ("text", "Who publishes the document"),
    "url": ("text", "Web address of the document or its page"),
    "licence": ("text", "Licence or legal status of the text"),
    "access": ("category", "fetched (by the build), hand_read (once, by a person) or link_only"),
    "source_id": ("code", "Source id in sources.yaml"),
    "sha256": ("text", "SHA-256 of the document's text with white space collapsed"),
    "text_chars": ("characters", "Length of the document's text"),
    "fetched_on": ("date", "Date the text was read"),
    "provision_id": ("code", "Provision id"),
    "citation": ("text", "Legal citation (section or bill and printer's number)"),
    "reform": ("category", "Benchmark reform: adu, missing_middle, tod, parking or fees"),
    "status": (
        "category",
        "Provisions: enacted, pending or lapsed. Municipal rules: draft or verified",
    ),
    "bill_id": ("code", "Bill number, for a bill"),
    "last_action": ("text", "A bill's last dated action on its page"),
    "last_action_date": ("date", "Date of that action"),
    "applies_to": ("text", "Which places the provision reaches"),
    "scope": (
        "category",
        "Reach: all, all_zoned, mpc (all but Philadelphia and Pittsburgh), mpc_zoned, conditional",
    ),
    "summary_own_words": ("text", "What the provision does, in our own words (not a quote)"),
    "checked_on": ("date", "Date the provision was checked against its text"),
    "county": ("code", "County code (5 digits)"),
    "county_name": ("text", "County name"),
    "municipalities": ("municipalities", "Municipalities in the county (DCED record)"),
    "munis_under_county_zoning": (
        "municipalities",
        "Municipalities with no zoning of their own where the county's ordinance applies",
    ),
    "residents_under_county_zoning": ("persons", "Their residents (2024 estimates)"),
    "munis_no_zoning": ("municipalities", "Municipalities where no zoning ordinance applies"),
    "residents": ("persons", "County residents in DCED municipalities (2024 estimates)"),
    "plan_name": ("text", "County comprehensive plan"),
    "plan_adopted": ("year", "Year the plan was adopted (empty when not stated)"),
    "plan_url": ("text", "Link to the plan"),
    "commission_url": ("text", "Link to the county planning commission or agency"),
    "plan_verified": ("flag", "1 when the plan name and year were confirmed on the page"),
    "residents_read": ("persons", "Residents of municipalities with a verified reading"),
    "sf_lots": (
        "lots",
        "Single-family lots: ACS one-unit detached homes, split among municipalities by "
        "2020 housing units (empty when not worked out)",
    ),
    "sf_lots_read": ("lots", "Single-family lots in municipalities with a verified reading"),
    "res_land_ha": (
        "hectares",
        "Residential land: land of census blocks with homes, split among municipalities by "
        "2020 housing units",
    ),
    "res_land_read_ha": ("hectares", "Residential land in municipalities with a verified reading"),
    "adopted_on": (
        "date",
        "Date the ordinance was adopted (YYYY, YYYY-MM or YYYY-MM-DD; empty unknown)",
    ),
    "amended_through": ("date", "Latest amendment the read copy includes (empty unknown)"),
    "effective_date": ("date", "Date the rule took or takes effect (empty unknown)"),
    "in_force_by": (
        "date",
        "A date by which the rule was in force, used when no adoption date is printed: the "
        "latest dated enactment inside the cited codified section, or the posted text's "
        "'enacted to' date (not an adoption date; empty unknown)",
    ),
    "binding_conditions": ("text", "Conditions that limit the rule, in plain words"),
    "future_effective": (
        "flag",
        "1 when the effective date is after the build date (not yet law), 0 when not, "
        "empty when the date is unknown",
    ),
    "introduced_on": ("date", "A bill's first dated action (its referral to committee)"),
    "enacted_on": (
        "date",
        "Date signed into law, or the date the code section was added (empty unknown)",
    ),
    "last_amended": ("date", "Latest amendment note in the code section (empty none or unknown)"),
    "verification": (
        "category",
        "verified (checked against the fetched text or page) or checked_values (page not "
        "read; hand-checked values used)",
    ),
    "geoid": ("code", "Municipality: state, county and county subdivision code (10 digits)"),
    "dced_id": ("code", "DCED municipality id"),
    "name": ("text", "Census name of the municipality"),
    "zoning_regime": ("category", "Who regulates zoning: own, joint, county or none"),
    "section_cite": ("text", "Section(s) of the document the rule was read from"),
    "adu_status": (
        "category",
        "ADU rule: by_right, conditional, special_exception, prohibited, not_addressed or unknown",
    ),
    "adu_detached": ("flag", "1 detached ADU allowed, 0 not allowed, empty unknown"),
    "adu_owner_occupancy": ("flag", "1 owner must live on the lot, 0 not required, empty unknown"),
    "occupancy_restriction": (
        "category",
        "Who may live in the ADU: relatives_only (only relatives of the main household or a "
        "relative needing care; counted as by right with conditions), empty when no limit read",
    ),
    "unlisted_uses": (
        "category",
        "The ordinance's clause on uses it does not list: prohibited (a verified exclusivity "
        "clause), conditional or special_exception (a hearing route); empty when not read. A "
        "not_addressed ADU rule with no clause read is unknown, not prohibited",
    ),
    "unlisted_uses_cite": ("text", "Section of the clause on unlisted uses"),
    "min_lot_sf_smallest_res_district": (
        "square feet",
        "Smallest minimum lot area of any residential district (empty unknown)",
    ),
    "max_units_by_right_sf_district": (
        "units",
        "Homes allowed by right on one lot in the main single-family district (empty unknown)",
    ),
    "parking_min_per_unit_sf": (
        "spaces per unit",
        "Off-street spaces required per single-family home",
    ),
    "parking_min_per_unit_mf": ("spaces per unit", "Off-street spaces required per apartment"),
    "permitted_types": ("text", "Housing types permitted somewhere (; separated)"),
    "transport_impact_fee_ordinance": (
        "flag",
        "1 a transportation impact fee ordinance exists, 0 none, empty unknown",
    ),
    "evidence_quote": ("text", "Quote (at most 300 characters) the main rule was read from"),
    "evidence_offset": ("characters", "Character offset of the quote in the document's text"),
    "method": ("category", "hand (a person read it), rule (drafted by the reader) or unread"),
    "confidence": ("category", "high, medium or low; empty when unread"),
    "reviewed_on": ("date", "Date a person checked the row"),
    "note": ("text", "Plain note on limits of the reading"),
}
"""Column units and descriptions for the data dictionary."""


def describe(name: str) -> tuple[str, str]:
    """Unit and description of a law-table column."""
    if name not in COLUMNS:
        raise ValueError(f"no data-dictionary entry for law column {name}")
    return COLUMNS[name]


def load_municipal_rules(path: Path = MUNI_RULES_PATH) -> dict[str, Any]:
    """Read ``assumptions/law/municipal_rules.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def municipal_documents(data: dict[str, Any]) -> list[LawDocument]:
    """``law_documents`` rows for the municipal texts read by hand."""
    return [LawDocument(**d) for d in data.get("documents", [])]


def _frame(rows: list[Any], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame([r.row() for r in rows], columns=columns)


def muni_zoning_rules(
    munis: pd.DataFrame,
    data: dict[str, Any],
    weights: pd.DataFrame | None = None,
    today: date | None = None,
) -> pd.DataFrame:
    """One row per municipality part: hand-read rules where we have them, else unknown.

    Args:
        munis: The ``municipal_planning`` table (``county``, ``cousub``,
            ``name``, ``dced_id``, ``zoning_regime``).
        data: ``municipal_rules.yaml``.
        weights: :func:`~visionhousing.law.coverage.muni_weights` (``geoid``,
            ``sf_lots``, ``res_land_ha``); without it both stay empty.
        today: The build date, for ``future_effective`` (default: today).

    A rule's ``adopted_on`` and ``effective_date`` default to its document's.

    Raises:
        ValueError: A hand-read rule names a municipality not in ``munis``, or
            a document id that is not listed.
    """
    today = today or date.today()
    docs = {d["doc_id"] for d in data.get("documents", [])}
    doc_dates = {
        d["doc_id"]: {k: d.get(k) for k in ("adopted_on", "effective_date")}
        for d in data.get("documents", [])
    }
    w: dict[str, tuple[float, float]] = {}
    if weights is not None:
        w = {
            # Whole lots only, and no land column, to keep the pack small: land
            # coverage is reported per county in county_land_use.
            str(r.geoid): (float(round(float(r.sf_lots))), None)
            for r in weights.itertuples()
        }
    read: dict[str, dict[str, Any]] = {}
    for r in data.get("rules", []):
        if r["doc_id"] not in docs:
            raise ValueError(f"rule for {r['geoid']} cites unknown document {r['doc_id']}")
        read[str(r["geoid"])] = r
    rows = []
    seen = set()
    for m in munis.itertuples():
        geoid = str(m.county).zfill(5) + str(m.cousub).zfill(5)
        lots, land = w.get(geoid, (None, None))
        base = {
            "geoid": geoid,
            "dced_id": "" if pd.isna(m.dced_id) else str(m.dced_id),
            "name": str(m.name),
            "zoning_regime": "" if pd.isna(m.zoning_regime) else str(m.zoning_regime),
            "sf_lots": lots,
            "res_land_ha": land,
        }
        hand = read.get(geoid)
        if hand:
            seen.add(geoid)
            fields = {k: v for k, v in hand.items() if k != "geoid"}
            for k in ("evidence_quote", "note", "binding_conditions"):
                if k in fields:
                    fields[k] = " ".join(str(fields[k]).split())
            for k, v in doc_dates.get(fields.get("doc_id", ""), {}).items():
                if v and not fields.get(k):
                    fields[k] = v
            eff = fields.get("effective_date")
            eff = eff.isoformat() if isinstance(eff, date) else str(eff or "")
            fields["future_effective"] = is_future(eff, today)
            rows.append(MuniRule(**base, **fields))
        else:
            rows.append(MuniRule(**base))
    missing = set(read) - seen
    if missing:
        raise ValueError(f"hand-read rules for municipalities not in the DCED table: {missing}")
    return _frame(rows, list(MuniRule.model_fields)).sort_values("geoid").reset_index(drop=True)


def county_land_use(
    munis: pd.DataFrame,
    plans: dict[str, dict[str, Any]],
    counties: pd.DataFrame | None = None,
    rules: pd.DataFrame | None = None,
    weights: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per county: county zoning reach, the county plan and coverage three ways.

    Args:
        munis: ``municipal_planning`` (with ``population`` and ``zoning_regime``).
        plans: ``assumptions/plans.yaml`` entries by county code.
        counties: The pack's ``counties`` table, for names (optional).
        rules: ``muni_zoning_rules`` (optional): adds the residents, lots and
            land under a verified reading (:func:`~visionhousing.law.coverage.county_coverage`).
        weights: :func:`~visionhousing.law.coverage.muni_weights` (optional): the
            lot and land weights; they replace the rounded ones in ``rules``.
    """
    if rules is not None and weights is not None:
        rules = rules.drop(columns=["sf_lots", "res_land_ha"], errors="ignore").merge(
            weights, on="geoid", how="left"
        )
    cov = county_coverage(rules, munis).set_index("county") if rules is not None else pd.DataFrame()

    def _int(v: Any) -> int | None:
        return None if v is None or pd.isna(v) else round(float(v))

    def _ha(v: Any) -> float | None:
        return None if v is None or pd.isna(v) else round(float(v), 1)

    m = munis.copy()
    m["county"] = m["county"].astype(str).str.zfill(5)
    m["population"] = pd.to_numeric(m["population"], errors="coerce").fillna(0)
    names = {}
    if counties is not None:
        names = dict(zip(counties["geoid"].astype(str), counties["name"].astype(str), strict=False))
    rows = []
    for county, g in m.groupby("county"):
        plan = plans.get(county, {})
        under = g["zoning_regime"] == "county"
        none_ = g["zoning_regime"] == "none"
        rows.append(
            CountyLandUse(
                county=county,
                county_name=names.get(county, plan.get("county", "")),
                municipalities=len(g),
                munis_under_county_zoning=int(under.sum()),
                residents_under_county_zoning=int(g.loc[under, "population"].sum()),
                munis_no_zoning=int(none_.sum()),
                residents=int(g["population"].sum()),
                plan_name=plan.get("name") or "",
                plan_adopted=plan.get("adopted"),
                plan_url=plan.get("url") or "",
                commission_url=plan.get("commission_url") or "",
                plan_verified=bool(plan.get("verified")),
                **(
                    {
                        "residents_read": int(cov.at[county, "residents_read"]),
                        "sf_lots": _int(cov.at[county, "sf_lots"]),
                        "sf_lots_read": _int(cov.at[county, "sf_lots_read"]),
                        "res_land_ha": _ha(cov.at[county, "res_land_ha"]),
                        "res_land_read_ha": _ha(cov.at[county, "res_land_read_ha"]),
                    }
                    if county in cov.index
                    else {}
                ),
            )
        )
    return _frame(rows, list(CountyLandUse.model_fields))


@dataclass
class LawTables:
    """What :func:`build_law_tables` produced."""

    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    files: list[tuple[str, Path]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_law_tables(
    cache: Path,
    munis: pd.DataFrame,
    *,
    counties: pd.DataFrame | None = None,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
    today: Callable[[], date] = legislation.today,
    weights: pd.DataFrame | None = None,
) -> LawTables:
    """Build all four law tables. The state part needs the network (or a filled cache).

    ``weights`` (:func:`~visionhousing.law.coverage.muni_weights`) adds the lot and
    land weights; without it they stay empty and coverage is by residents only.
    """
    out = LawTables()
    data = load_municipal_rules()
    rules = muni_zoning_rules(munis, data, weights, today())
    out.tables["muni_zoning_rules"] = rules
    out.tables["county_land_use"] = county_land_use(
        munis, read_plan_citations(), counties, rules, weights
    )
    docs = municipal_documents(data)
    try:
        state = build_state_law(
            cache, client=client, manifest=manifest, refresh=refresh, today=today
        )
    except Exception as err:
        out.notes.append(f"law: state provisions not added ({type(err).__name__}: {err})"[:300])
    else:
        out.tables["state_law_provisions"] = _frame(
            state.provisions, list(StateProvision.model_fields)
        )
        docs = state.documents + docs
        out.files += state.files
        out.notes += state.notes
    out.tables["law_documents"] = _frame(docs, list(LawDocument.model_fields))
    cov = county_coverage(rules, munis)
    if weights is not None:
        lots = cov["sf_lots"].sum()
        out.notes.append(
            f"law: verified rows cover {cov['residents_read'].sum() / cov['residents'].sum():.1%} "
            f"of residents and {cov['sf_lots_read'].sum() / lots:.1%} of single-family lots"
            if lots > 0
            else "law: no single-family lots in the weights"
        )
    out.notes.append(
        f"law: {int((rules['status'] == 'verified').sum())} verified municipal rows, "
        f"{int((rules['method'] == 'unread').sum())} unread of {len(rules)}"
    )
    return out


def pack_weights(folder: Path) -> pd.DataFrame | None:
    """Lot and land weights per municipality from a built pack, or ``None`` when tables are missing.

    Reads ``tracts``, ``tract_municipalities`` and (when present) ``blocks``.
    """
    folder = Path(folder)
    need = [folder / "tracts.csv.gz", folder / "tract_municipalities.csv.gz"]
    if not all(p.is_file() for p in need):
        return None
    tracts = pd.read_csv(
        need[0], dtype={"geoid": str}, usecols=["geoid", "units_1_detached", "land_area_m2"]
    )
    tm = pd.read_csv(need[1], dtype={"tract": str, "county": str, "cousub": str})
    blocks_path = folder / "blocks.csv.gz"
    blocks = (
        pd.read_csv(
            blocks_path,
            dtype={"tract": str},
            usecols=["tract", "housing_units", "land_area_m2"],
        )
        if blocks_path.is_file()
        else None
    )
    return _weights(tracts, tm, blocks)


def _weights(tracts: pd.DataFrame, tm: pd.DataFrame, blocks: pd.DataFrame | None) -> pd.DataFrame:
    """:func:`muni_weights` with the land cap from ``assumptions/law/hooks.yaml``."""
    from visionhousing.law.hooks import load_hooks

    cap = float((load_hooks().get("land_weight") or {}).get("max_m2_per_home", 4046.86))
    return muni_weights(tracts, tm, blocks, cap)


def weigh_law_tables(tables: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Fill the lot and land weights of a full build's law tables from its own tables.

    A full build (``make data``) makes the law tables with the other local
    tables, before the pack's ``tracts`` and ``blocks`` exist, so their weights
    start empty. Once those tables exist, this re-weighs ``muni_zoning_rules``
    (whole single-family lots, as :func:`muni_zoning_rules` writes them) and
    rebuilds ``county_land_use`` (coverage by residents, lots and land, with
    county names). Nothing is fetched. Returns the two tables, or nothing when
    a needed table is missing.
    """
    need = ("muni_zoning_rules", "municipal_planning", "tract_municipalities", "tracts")
    if any(name not in tables for name in need):
        return {}
    tracts = tables["tracts"].assign(geoid=lambda t: t["geoid"].astype(str))
    tm = tables["tract_municipalities"].astype({"tract": str, "county": str, "cousub": str})
    blocks = tables.get("blocks")
    if blocks is not None:
        blocks = blocks.assign(tract=blocks["tract"].astype(str))
    weights = _weights(tracts, tm, blocks)
    lots = dict(zip(weights["geoid"].astype(str), weights["sf_lots"], strict=True))
    rules = tables["muni_zoning_rules"].copy()
    rules["sf_lots"] = [
        None if pd.isna(lots.get(g)) else float(round(float(lots[g])))
        for g in rules["geoid"].astype(str)
    ]
    land = county_land_use(
        tables["municipal_planning"], read_plan_citations(), tables.get("counties"), rules, weights
    )
    return {"muni_zoning_rules": rules, "county_land_use": land}


def add_law_to_pack(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> LawTables:
    """Add the law tables to a built pack; rewrite its dictionary and manifest.

    Reads ``municipal_planning`` and ``counties`` from the pack itself; nothing
    else is fetched. The pack hash, file list, inputs and provenance are
    rebuilt the same way the full build writes them.
    """
    from visionhousing.data import pack

    folder = Path(folder)
    state = folder.name.upper()
    munis = pd.read_csv(
        folder / "municipal_planning.csv.gz", dtype={"county": str, "cousub": str, "dced_id": str}
    )
    counties = pd.read_csv(folder / "counties.csv.gz", dtype={"geoid": str})
    built = build_law_tables(
        cache,
        munis,
        counties=counties,
        client=client,
        manifest=manifest,
        refresh=refresh,
        weights=pack_weights(folder),
    )
    for name, frame in built.tables.items():
        pack._write_gz_csv(
            pack._tidy_numbers(frame.reset_index(drop=True)), folder / f"{name}.csv.gz"
        )
    manifest_path = folder / "pack_manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables = pack.load_pack(folder)
    dictionary = pack.build_dictionary(tables, state)
    (folder / "data_dictionary.json").write_text(pack._json_text(dictionary), encoding="utf-8")
    keep = {f["path"]: f for f in old["files"]}
    for name in [*built.tables, "data_dictionary"]:
        path = folder / (f"{name}.csv.gz" if name != "data_dictionary" else "data_dictionary.json")
        entry = {"path": path.name, "bytes": path.stat().st_size, "sha256": pack.sha256_file(path)}
        if name in built.tables:
            entry["rows"] = len(tables[name])
        keep[path.name] = entry
    files = sorted(keep.values(), key=lambda f: f["path"])
    digest = hashlib.sha256()
    for f in files:
        digest.update(f"{f['path']}\t{f['sha256']}\n".encode())
    ids = {sid for sid, _ in built.files}
    inputs = [r for r in old.get("inputs", []) if r.get("source") not in ids]
    for sid, path in built.files:
        inputs.append(
            {
                "source": sid,
                "file": path.relative_to(cache).as_posix(),
                "sha256": pack.sha256_file(path),
                "bytes": path.stat().st_size,
                "accessed": None,
            }
        )
    inputs.sort(key=lambda r: (r["source"], r["file"]))
    old.update(
        {
            "pack_hash": digest.hexdigest(),
            "files": files,
            "inputs": inputs,
            "provenance": pack._provenance(inputs),
        }
    )
    manifest_path.write_text(pack._json_text(old), encoding="utf-8")
    return built


# --------------------------------------------------------------------------- provision versions

VERSION_TABLE: dict[str, dict[str, object]] = {
    "law_provision_version": {
        "key": ["record_id"],
        "sources": ["vt_acts_2026", "pgh_legistar", "pa_hb2186", "pa_sb1346"],
        "description": (
            "One version of one operative provision (state statute, bill, county or municipal "
            "ordinance) bearing on a reform, from assumptions/law/timeline.yaml: citation, "
            "section, final or draft text, effective start and end, adoption, publication and "
            "check dates, supersession, stays, the higher-law floor, a fingerprint of the text "
            "read and where its operative words are. Empty when no provision of the state has "
            "been verified."
        ),
        "tier": "2",
    }
}
"""The ``law_provision_version`` pack table (every state; added by its own build step)."""

VERSION_COLUMNS: dict[str, tuple[str, str]] = {
    "record_id": ("code", "Timeline record id: one version of one provision over one period"),
    "provision_id": ("code", "Provision id shared by every version of the same provision"),
    "jurisdiction": (
        "code",
        "Whose law: a two-letter state, a 5-digit county or a 10-digit municipality code",
    ),
    "citation": ("text", "Official citation of this version"),
    "section": ("text", "Section or subdivision cited (empty when the whole act or ordinance)"),
    "housing_type": ("category", "What it bears on: adu, missing_middle, multifamily, parking"),
    "status": (
        "category",
        "pending, enacted, legally_effective, stayed, locally_implemented or practically_usable",
    ),
    "version_state": ("category", "final (enacted text) or draft (never in force)"),
    "text_version": ("text", "Which text was read (printer's number, version, statute note)"),
    "source_url": ("text", "First official address the version was read from"),
    "effective_start": ("date", "First day in force (empty when the source does not state it)"),
    "effective_start_unknown": (
        "flag",
        "1 when the source does not state the start; the version then counts only from "
        "verified_on. Adoption is never used in its place",
    ),
    "effective_end": ("date", "First day no longer in force (empty when open)"),
    "adopted_on": ("date", "Date adopted or enacted (kept apart from the effective date)"),
    "published_on": ("date", "Date the text was published or signed"),
    "verified_on": ("date", "Date a person or script first confirmed the version"),
    "last_checked_on": ("date", "Date the version was last re-read"),
    "supersedes": ("code", "Record id of the version this one replaces"),
    "superseded_by": ("code", "Record id of the version that replaces this one"),
    "court_status": ("category", "none_known, or a stay or ruling bearing on the version"),
    "higher_law_floor": ("text", "The higher-level rule local law cannot go below, if any"),
    "source_fingerprint": ("text", "sha256 of the text read, and what was hashed"),
    "excerpt_locator": ("text", "Where the operative words are in that text"),
    "review": ("category", "verified, or unverified (not counted)"),
}
"""Column units and descriptions of ``law_provision_version``."""


def _state_fips(state: str) -> str:
    from visionhousing.data.sources import state_fips

    try:
        return str(state_fips(state))
    except (KeyError, ValueError):
        return ""


def provision_version_table(state: str, records: list[Any] | None = None) -> pd.DataFrame:
    """The ``law_provision_version`` rows of one state.

    A record belongs to the state when its jurisdiction is the state's two-letter
    code, or a county or municipality code that starts with the state's FIPS code.
    The rows are :func:`visionhousing.law.timeline.provision_versions`, sorted by
    provision and start. A state with no verified provision gets an empty table
    with the columns (so the pack says "none recorded", not "not built").
    """
    from visionhousing.law.timeline import load_timeline, provision_versions

    code = state.upper()
    fips = _state_fips(code)
    recs = load_timeline() if records is None else records
    mine = [
        r
        for r in recs
        if str(r.jurisdiction).upper() == code
        or (fips and str(r.jurisdiction).isdigit() and str(r.jurisdiction).startswith(fips))
    ]
    frame = provision_versions(mine)
    if frame.empty:
        return frame
    for col in ("effective_start_unknown",):
        frame[col] = frame[col].astype(int)
    order = frame["effective_start"].astype(str)
    return (
        frame.assign(_o=order)
        .sort_values(["provision_id", "_o", "record_id"])
        .drop(columns="_o")
        .reset_index(drop=True)
    )


def add_provision_versions(
    folder: Path,
    cache: Path,
    *,
    records: list[Any] | None = None,
    lock: Callable[[], Any] | None = None,
) -> pd.DataFrame:
    """Add (or refresh) ``law_provision_version`` in a built pack; rewrite dictionary and manifest.

    Reads only ``assumptions/law/timeline.yaml`` (no download). The pack hash,
    file list and provenance are rebuilt the way the full build writes them.
    ``lock`` is an optional context-manager factory held while files are written.
    """
    import contextlib

    from visionhousing.data import pack

    folder = Path(folder)
    table = provision_version_table(folder.name, records)
    with lock() if lock is not None else contextlib.nullcontext():
        pack._rewrite(folder, Path(cache), {"law_provision_version": table}, [])
    return table
