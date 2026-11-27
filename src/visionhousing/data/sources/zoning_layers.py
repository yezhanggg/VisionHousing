"""Zoning district maps of the Pennsylvania pilot, and the block outlines they are split by.

Plain-language summary
----------------------
The zoning-district pilot (``law/zone_rules.py``, ``law/rule_weights.py``)
needs four kinds of map, all read by script from the publishers' own services:

- **Philadelphia base zoning districts**, the current layer and one dated
  snapshot (December 2021), from the City of Philadelphia's ArcGIS Online
  organisation (``maps.phl.data``), the same services OpenDataPhilly lists.
- **Philadelphia zoning overlays** (each overlay's symbol and the Zoning Code
  section that creates it).
- **Pittsburgh zoning districts** and the **Parking Reduction overlay**, from
  the City of Pittsburgh's ArcGIS Online organisation (``pgh.admin``).
- **2020 census block outlines** of the counties in the sample, from the
  Census Bureau's TIGER/Line 2020 block file. Only the ``.shp``, ``.shx`` and
  ``.dbf`` members of the state zip are read (range requests); the outlines of
  the sample's counties are kept, the rest is deleted at once.

Each layer is read the same way as the other map services of this project
(:func:`visionhousing.data.sources.infrastructure.query_features`: every
object id first, then the features in chunks of ids, checked against the
service's own count) and cached as one gzipped JSON file under
``data/raw/local/zoning/``. Outlines are in longitude and latitude (NAD83).

Terms (checked 2026-11-26, before any data request; see the registry entries):
www.arcgis.com allows all robots and services.arcgis.com / services1.arcgis.com
publish no robots rule; OpenDataPhilly allows all. Philadelphia's layers carry
the City of Philadelphia licence ("Public Use; Free"; the City reserves all
rights in the data). Pittsburgh's carry a use-at-own-risk notice with no
statement on redistribution. So the raw outlines stay in the local cache and
only derived counts and shares enter the pack.

Settings: ``assumptions/law/zone_weights.yaml`` (``layers``, ``block_shapes``,
``simplify_degrees``).
"""

from __future__ import annotations

import gzip
import json
import shutil
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources.infrastructure import query_features
from visionhousing.data.sources.ma_capacity import layer_stamp

__all__ = [
    "SETTINGS_PATH",
    "cache_dir",
    "fetch_block_shapes",
    "fetch_layer",
    "fetch_sample_layers",
    "load_settings",
    "read_cached",
]

SETTINGS_PATH = Path(__file__).resolve().parents[4] / "assumptions" / "law" / "zone_weights.yaml"
FOLDER = Path("local") / "zoning"


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """The pilot's settings (``assumptions/law/zone_weights.yaml``)."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def cache_dir(cache: Path) -> Path:
    """Folder of the cached zoning layers inside the raw cache."""
    return Path(cache) / FOLDER


def read_cached(path: Path) -> dict[str, Any]:
    """Read one cached layer or block-shape file."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    text = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with part.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(text)
    part.replace(path)


def fetch_layer(
    name: str,
    cache: Path,
    settings: dict[str, Any] | None = None,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Read (or reuse) one zoning layer named in ``layers`` of the settings.

    Returns the cached document ``{"id", "url", "layer_name", "last_edit",
    "service_count", "received", "fields", "read_on", "map_version",
    "features"}`` and its path. Each feature has ``attributes`` and ``rings``
    (lon/lat). Raises :class:`FetchError` when the service lacks a field or the
    features received do not add up to its own count.
    """
    settings = settings or load_settings()
    spec = settings["layers"][name]
    path = cache_dir(cache) / str(spec["file"])
    if path.is_file() and not refresh:
        return read_cached(path), path
    client = client or HttpClient(timeout=120)
    source = get_source(str(spec["source"]))
    url = source.url(service=str(spec["service"])).removesuffix("/query")
    stamp = layer_stamp(client, url)
    fields = [str(f) for f in spec.get("fields") or []]
    missing = [f for f in fields if f not in stamp["fields"]]
    if missing:
        raise FetchError(f"{url}: the service has no field {', '.join(missing)}")
    oid = next((f for f in stamp["fields"] if f.lower() == "objectid"), "OBJECTID")
    features = query_features(
        client,
        url,
        "1=1",
        (oid, *fields),
        geometry=True,
        offset_deg=float(settings["simplify_degrees"]),
    )
    if stamp["service_count"] is not None and len(features) != stamp["service_count"]:
        raise FetchError(
            f"{url}: got {len(features)} features, the service counts {stamp['service_count']}"
        )
    features.sort(key=lambda f: int((f.get("attributes") or {}).get(oid) or 0))
    read_on = date.today().isoformat()
    version = str(spec.get("map_version") or "current")
    data = {
        "id": source.id,
        "layer": name,
        "url": url,
        "layer_name": stamp["name"],
        "last_edit": stamp["last_edit"],
        "service_count": stamp["service_count"],
        "received": len(features),
        "fields": fields,
        "read_on": read_on,
        "map_version": read_on if version == "current" else version,
        "observed_on": str(spec.get("observed_on") or stamp["last_edit"] or read_on),
        "features": features,
    }
    _save(path, data)
    if manifest is not None:
        vintage = f"layer edited {data['last_edit'] or 'unknown'}; read {read_on}"
        mf.record(mf.entry_for_file(source.id, url, path, vintage, root=mf.ROOT), manifest)
    return data, path


def fetch_sample_layers(
    cache: Path,
    settings: dict[str, Any] | None = None,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> dict[str, tuple[dict[str, Any], Path]]:
    """Every layer the sample names (maps and overlays), each read or reused."""
    settings = settings or load_settings()
    names: list[str] = []
    for j in settings["sample"]:
        names += [str(m) for m in j.get("maps") or []]
        if j.get("overlays"):
            names.append(str(j["overlays"]))
    out = {}
    for name in dict.fromkeys(names):
        out[name] = fetch_layer(
            name, cache, settings, client=client, manifest=manifest, refresh=refresh
        )
    return out


def _round_ring(points: Iterable[Any], decimals: int) -> list[list[float]]:
    out: list[list[float]] = []
    for p in points:
        q = [round(float(p[0]), decimals), round(float(p[1]), decimals)]
        if not out or out[-1] != q:
            out.append(q)
    return out


def _shapes_from_shapefile(
    shp: Path, shx: Path, dbf: Path, counties: set[str], decimals: int
) -> dict[str, list[list[list[float]]]]:
    import shapefile  # pyshp (MIT)

    out: dict[str, list[list[list[float]]]] = {}
    with shp.open("rb") as s, shx.open("rb") as x, dbf.open("rb") as d:
        reader = shapefile.Reader(shp=s, shx=x, dbf=d)
        names = [f[0] for f in reader.fields[1:]]
        i_geoid = names.index("GEOID20")
        i_state = names.index("STATEFP20")
        i_county = names.index("COUNTYFP20")
        for k, rec in enumerate(reader.iterRecords()):
            if f"{rec[i_state]}{rec[i_county]}" not in counties:
                continue
            shape = reader.shape(k)
            parts = [*shape.parts, len(shape.points)]
            rings = [
                _round_ring(shape.points[parts[p] : parts[p + 1]], decimals)
                for p in range(len(parts) - 1)
            ]
            out[str(rec[i_geoid])] = [r for r in rings if len(r) >= 3]
    return out


def fetch_block_shapes(
    cache: Path,
    fips: str = "42",
    settings: dict[str, Any] | None = None,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Read (or reuse) the 2020 block outlines of the sample's counties.

    Downloads the ``.shp``, ``.shx`` and ``.dbf`` members of the state's
    TIGER/Line 2020 block zip into a temporary folder of the raw cache (range
    requests; the rest of the zip is never transferred), keeps the outlines of
    the counties in ``block_shapes.counties`` (rounded to ``decimals``), and
    deletes the members. Returns ``{"id", "url", "read_on", "counties",
    "blocks": {geoid: rings}}`` and its path.
    """
    settings = settings or load_settings()
    spec = settings["block_shapes"]
    path = cache_dir(cache) / str(spec["file"])
    if path.is_file() and not refresh:
        return read_cached(path), path
    client = client or HttpClient(timeout=300)
    source = get_source(str(spec["source"]))
    url = source.url(state_fips=fips)
    tmp = Path(cache) / "blocks" / f"_shapes_{fips}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        members = {}
        for ext in ("shp", "shx", "dbf"):
            name = f"tl_2020_{fips}_tabblock20.{ext}"
            dest = tmp / name
            if not dest.is_file():
                client.download_zip_member(url, name, dest)
            members[ext] = dest
        counties = {str(c) for c in spec["counties"]}
        blocks = _shapes_from_shapefile(
            members["shp"], members["shx"], members["dbf"], counties, int(spec["decimals"])
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not blocks:
        raise FetchError(f"{url}: no block of counties {sorted(counties)} was found")
    data = {
        "id": source.id,
        "url": url,
        "read_on": date.today().isoformat(),
        "counties": sorted(counties),
        "blocks": dict(sorted(blocks.items())),
    }
    _save(path, data)
    if manifest is not None:
        vintage = f"2020 Census blocks, counties {', '.join(sorted(counties))}"
        mf.record(mf.entry_for_file(source.id, url, path, vintage, root=mf.ROOT), manifest)
    return data, path
