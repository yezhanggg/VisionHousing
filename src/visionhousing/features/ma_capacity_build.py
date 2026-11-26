"""Add the Massachusetts 3A capacity and estimated-sewer tables to a built pack.

Plain-language summary
----------------------
``visionhousing ma-capacity --state MA`` (and the last-but-one add-on step of
``make data``) runs :func:`add_ma_capacity`:

1. read (or reuse from ``data/raw/local/ma/``) the 3A atlas parcel layer and
   station radii, and MassDEP's estimated sewer service areas
   (:mod:`visionhousing.data.sources.ma_capacity`);
2. split every parcel's modeled capacity across 2020 tracts by area, count a
   district's own total only when the atlas has no parcel for it, and write
   ``mbta_3a_capacity_tract`` and ``mbta_3a_capacity_district``
   (:func:`visionhousing.features.ma_capacity.capacity_tables`);
3. place every 2020 block with homes in the estimated sewer areas and write
   ``ma_sewer_estimated_tract`` (:func:`visionhousing.features.ma_capacity.sewer_tract_table`);
4. rewrite the pack's data dictionary and manifest.

In any pack without Massachusetts' ``mbta_3a_districts`` table it does
nothing. A source that cannot be read leaves its table out and says so.
"""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.sources import ma_capacity as src
from visionhousing.data.sources.ma_intake import load_settings
from visionhousing.features import ma_capacity as fx

__all__ = ["add_ma_capacity", "ma_capacity_cmd", "station_flag_check"]


def station_flag_check(parcels: pd.DataFrame, radii: dict[str, Any]) -> dict[str, Any]:
    """How often the atlas's station-area flag agrees with its own half-mile radii.

    Each parcel's first corner is tested against the radii shape. Returns the
    share of flagged (Y) and unflagged (N) parcels whose corner is inside.
    """
    from visionhousing.features.infrastructure import feature_index

    feats = [f for f in radii.get("features") or [] if f.get("rings")]
    first = [np.asarray(r[0], dtype=float)[0] if r else (np.nan, np.nan) for r in parcels["rings"]]
    pts = np.asarray([p[:2] for p in first], dtype=float).reshape(-1, 2)
    ok = ~np.isnan(pts[:, 0])
    inside = np.zeros(len(parcels), dtype=bool)
    if feats and ok.any():
        inside[ok] = feature_index(pts[ok, 0], pts[ok, 1], feats) >= 0
    flag = parcels["station_area"]
    out: dict[str, Any] = {}
    for label, value in (("flag_Y_inside", 1), ("flag_N_inside", 0)):
        sel = (flag == value).fillna(False).to_numpy()
        out[label] = round(float(inside[sel].mean()), 3) if sel.any() else None
    return out


def add_ma_capacity(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    settings: dict[str, Any] | None = None,
    lock: Callable[[], Any] | None = None,
    write: bool = True,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Add (or refresh) the three Massachusetts tables in a built pack.

    Args:
        folder: The pack folder (``data/packs/MA``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client for a missing cache file.
        manifest: The download manifest to record fetches in.
        settings: ``assumptions/ma_intake.yaml`` content (tests); ``None`` reads it.
        lock: Optional context-manager factory held only while files are written.
        write: False builds the tables without touching the pack (checks).

    Returns:
        The tables written and a report (totals, coverage, notes).
    """
    from visionhousing.data import pack as pk

    folder, cache = Path(folder), Path(cache)
    path = folder / "mbta_3a_districts.csv.gz"
    if not path.is_file():
        return {}, {"notes": ["no mbta_3a_districts table: not a Massachusetts pack"]}
    spec = settings or load_settings()
    report: dict[str, Any] = {"notes": []}
    written: dict[str, pd.DataFrame] = {}
    inputs: list[tuple[str, Path]] = []
    districts = pd.read_csv(path, dtype={"tract": str, "county": str}, compression="gzip")
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or "25")
    tracts = pd.read_csv(folder / "tracts.csv.gz", dtype={"geoid": str}, compression="gzip")
    tract_ids = set(tracts["geoid"])
    shapes = [(g, s) for g, s in pk._tract_shapes(folder, cache, fips) if g in tract_ids]
    try:
        doc, ppath = src.fetch_capacity_layer(
            "parcels", cache, spec, client=client, manifest=manifest
        )
        inputs.append((str(doc.get("id") or spec["capacity"]["parcels"]["source"]), ppath))
        parcels = fx.parcel_frame(doc["features"])
        parcels, dropped = fx.dedupe_parcels(parcels)
        rule = fx.SplitRule.from_settings(spec["capacity"]["split"])
        tract, district, summary = fx.capacity_tables(
            parcels, districts, shapes, rule, read_on=str(doc.get("read_on") or "")
        )
        summary.update(
            layer_last_edit=doc.get("last_edit"), service_count=doc.get("service_count"),
            received=doc.get("received"), read_on=doc.get("read_on"),
            duplicate_rows_dropped=len(dropped),
            duplicate_units_dropped=float(dropped["capacity_units"].fillna(0).sum()),
        )  # fmt: skip
        try:
            radii, rpath = src.fetch_capacity_layer(
                "station_radii", cache, spec, client=client, manifest=manifest
            )
            inputs.append((str(radii.get("id")), rpath))
            summary["station_flag_check"] = station_flag_check(parcels, radii)
        except (FetchError, OSError) as err:
            report["notes"].append(f"3A station radii not read ({err})"[:300])
        status = folder / "mbta_3a_status.csv.gz"
        if status.is_file():
            st = pd.read_csv(status, compression="gzip")
            summary["communities_by_status"] = (
                st["compliance_status"].fillna("unknown").value_counts().sort_index().to_dict()
            )
        written["mbta_3a_capacity_tract"] = tract
        written["mbta_3a_capacity_district"] = district
        report["capacity"] = summary
    except (FetchError, OSError) as err:
        report["notes"].append(f"3A parcel capacity not added ({err})"[:300])
    try:
        sew = spec["sewer_estimated"]
        doc, spath = src.fetch_sewer_layers(cache, spec, client=client, manifest=manifest)
        inputs.append((str(doc.get("id") or sew["source"]), spath))
        blocks = pd.read_csv(
            folder / "blocks.csv.gz", dtype={"geoid": str, "tract": str}, compression="gzip"
        )
        polygons = {k: v for k, v in doc["layers"].items() if k != "unlocated"}
        table, summary = fx.sewer_tract_table(
            blocks, polygons, circle_min_ratio=float(sew["circle_ratio"]),
            circle_max_acres=float(sew["circle_max_acres"]),
            edition=str(doc.get("edition") or ""), read_on=str(doc.get("read_on") or ""),
        )  # fmt: skip
        summary["layers"] = {
            k: {"service_count": v.get("service_count"), "received": v.get("received"),
                "last_edit": v.get("last_edit")}
            for k, v in doc["layers"].items()
        }  # fmt: skip
        written["ma_sewer_estimated_tract"] = table
        report["sewer"] = summary
    except (FetchError, OSError) as err:
        report["notes"].append(f"MassDEP estimated sewer not added ({err})"[:300])
    if write and written:
        with lock() if lock is not None else contextlib.nullcontext():
            pk._rewrite(folder, cache, written, inputs)
    return written, report


def ma_capacity_cmd(state: str, raw: Path, packs: Path) -> int:
    """``visionhousing ma-capacity``: add the 3A capacity and sewer tables; print checks."""
    from visionhousing.data import pack as pk

    folder = Path(packs) / state.upper()
    written, report = add_ma_capacity(folder, Path(raw))
    if not written:
        print("ma-capacity: " + "; ".join(report["notes"]), file=sys.stderr)
        return 1
    for name, frame in written.items():
        p = folder / f"{name}.csv.gz"
        print(f"{name}: {len(frame):,} rows, {p.stat().st_size:,} bytes")
    print(json.dumps({k: v for k, v in report.items() if k != "notes"}, indent=1, default=str))
    for note in report["notes"]:
        print(f"note: {note}")
    total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    print(f"pack total: {total:,} of {pk.PACK_BUDGET_BYTES:,} bytes "
          f"({total / pk.PACK_BUDGET_BYTES:.1%})")  # fmt: skip
    return 0
