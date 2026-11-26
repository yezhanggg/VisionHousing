"""Build the water and sewer readiness tables for a state pack.

Plain-language summary
----------------------
``visionhousing infrastructure --state PA`` runs :func:`add_infrastructure`:

1. read the official layers listed for the state in
   ``assumptions/infrastructure.yaml`` (sewer service areas, public water
   supplier areas), the service statements and documented constraints listed
   there, and EPA's sewersheds as context (each cached in
   ``data/raw/infrastructure/``; see :mod:`visionhousing.data.sources.infrastructure`);
2. answer every 2020 Census block with homes: verified, outside, constraint or
   unknown (:func:`visionhousing.features.infrastructure.block_answers`);
3. for Pennsylvania, match DEP's Act 537 plan-age table to municipalities and
   give each tract the class of the municipality holding most of its homes;
4. add EPA's Clean Watersheds Needs Survey 2022 county needs and the count of
   EPA sewershed treatment plants;
5. write ``infra_tract``, ``infra_county`` and ``infra_contacts`` into the pack
   and rewrite its data dictionary and manifest.

Blocks are placed by their internal point with a plain NumPy point-in-polygon
test (:mod:`visionhousing.features.planar`), a few hundred thousand points at
most, so the whole state runs in memory in about a minute.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data import pack as pk
from visionhousing.data.http import HttpClient
from visionhousing.data.sources import infrastructure as src
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.features import infrastructure as fx

__all__ = ["add_infrastructure", "infrastructure_cmd"]


def _county_of_points(lon: pd.Series, lat: pd.Series, folder: Path) -> list[str]:
    """County code holding each point, from the pack's county outlines ("" when none)."""
    path = folder / "boundaries" / "counties.geojson"
    shapes = json.loads(path.read_text(encoding="utf-8"))["features"]
    feats = []
    codes = []
    for f in shapes:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        feats.append({"rings": [ring for poly in polys for ring in poly]})
        codes.append(str(f["properties"]["geoid"]))
    x = pd.to_numeric(lon, errors="coerce").fillna(0).to_numpy(float)
    y = pd.to_numeric(lat, errors="coerce").fillna(0).to_numpy(float)
    idx = fx.feature_index(x, y, feats)
    return [codes[i] if i >= 0 else "" for i in idx]


def add_infrastructure(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    rules: dict[str, Any] | None = None,
    write: bool = True,
    log: Callable[[str], None] = print,
) -> dict[str, pd.DataFrame]:
    """Add (or refresh) ``infra_tract``, ``infra_county`` and ``infra_contacts`` in a built pack.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client (default: a new one).
        manifest: The download manifest to record fetches in.
        rules: The rules (default: ``assumptions/infrastructure.yaml``).
        write: Write the tables into the pack (``False``: only compute them).
        log: Function that prints progress.

    Returns:
        The three tables.
    """
    folder, cache = Path(folder), Path(cache)
    rules = rules or fx.load_rules()
    client = client or HttpClient(timeout=180)
    state = folder.name.upper()
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or to_state_fips(state))
    tables = pk.load_pack(folder)
    inputs: list[tuple[str, Path]] = []
    as_of: dict[str, str] = {}

    def layers(group: str, kind: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        out = []
        for entry in (rules.get(group) or {}).get(state, []) or []:
            data, path = src.fetch_layer(
                src.Layer.from_rules(entry, kind), cache, client=client, manifest=manifest
            )
            log(f"{entry['id']}: {len(data['features'])} polygons (as of {entry.get('as_of')})")
            inputs.append((entry["id"], path))
            as_of[entry["id"]] = str(entry.get("as_of") or "")
            out.append((entry, data))
        return out

    sewer = layers("sewer_layers", "sewer")
    water = layers("water_layers", "water")
    context = None
    for entry in rules.get("context_layers") or []:
        e = {**entry, "where": str(entry.get("where", "1=1")).format(state=state)}
        context, path = src.fetch_layer(
            src.Layer.from_rules(e, "context"),
            cache,
            client=client,
            manifest=manifest,
            suffix=f"_{state}",
        )
        log(f"{entry['id']}: {len(context['features'])} sewersheds (context)")
        inputs.append((entry["id"], path))
    records = (rules.get("service_records") or {}).get(state, []) or []
    for rec in records:
        as_of[rec["id"]] = str(rec.get("checked_on") or "")
    constraints = (rules.get("constraint_records") or {}).get(state, []) or []
    answers = fx.block_answers(tables["blocks"], sewer, records, constraints, water, context)

    main = shares = None
    if "municipal_planning" in tables and "tract_municipalities" in tables and "act537" in rules:
        plans, pdfs = src.fetch_act537(rules["act537"], cache, client=client, manifest=manifest)
        inputs += [("pa_dep_act537_plan_status", p) for p in pdfs]
        by_cousub = fx.act537_by_cousub(plans, tables["municipal_planning"], tables["counties"])
        log(f"act537: {len(plans)} plan rows, {len(by_cousub)} matched to municipalities")
        main, shares = fx.act537_tables(by_cousub, tables["tract_municipalities"])

    cwns, cwns_path = src.fetch_cwns_county(fips, cache, client=client, manifest=manifest)
    inputs.append(("epa_cwns_2022", cwns_path))
    endpoints, ep_path = src.fetch_endpoints(state, cache, client=client, manifest=manifest)
    endpoints["county"] = _county_of_points(endpoints["lon"], endpoints["lat"], folder)
    inputs.append(("epa_sewersheds", ep_path))

    tract_ids = sorted(tables["tracts"]["geoid"].astype(str))
    counties = sorted(tables["counties"]["geoid"].astype(str))
    tract = fx.tract_table(answers, tract_ids, main, rules)
    county = fx.county_table(
        answers, tract, counties, as_of, shares, cwns.set_index("county"), endpoints
    )
    contacts = fx.contacts_table(answers, endpoints, int(rules.get("contacts_per_county", 3)))
    written = {"infra_tract": tract, "infra_county": county, "infra_contacts": contacts}
    if write:
        pk._rewrite(folder, cache, written, inputs)
    return written


def infrastructure_cmd(state: str, raw: Path, packs: Path) -> int:
    """``visionhousing infrastructure``: add the readiness tables to a pack; print coverage."""
    folder = Path(packs) / state.upper()
    if not (folder / "tracts.csv.gz").is_file():
        print(
            f"infrastructure: {folder} has no tracts table; build the pack first", file=sys.stderr
        )
        return 1
    written = add_infrastructure(folder, Path(raw))
    for name, frame in written.items():
        path = folder / f"{name}.csv.gz"
        print(f"{name}: {len(frame):,} rows, {path.stat().st_size:,} bytes")
    counts = written["infra_tract"]["sewer_status"].value_counts()
    print("tracts by sewer status: " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    print(
        f"pack total: {total:,} of {pk.PACK_BUDGET_BYTES:,} bytes "
        f"({total / pk.PACK_BUDGET_BYTES:.1%})"
    )
    return 0
