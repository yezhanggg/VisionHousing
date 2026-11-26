"""Massachusetts capacity and sewer layers: 3A parcels and station radii, MassDEP sewer areas.

Plain-language summary
----------------------
Reads three public map-service layers by script and caches each as one
gzipped JSON file under ``data/raw/local/ma/``:

- **3A parcels** (MassGIS / HLC MBTA Communities District Atlas, layer 1):
  every parcel inside a Section 3A district of a town HLC found compliant,
  with the compliance model's unit capacity on it (``F_L_M__``, "Final Lot
  Multi-Family Unit Capacity"), the model's capacity before the lot limits
  (``MDL_U_C``), the homes already on it (``RB_RSDN``) and whether it lies in
  the half-mile station area (``TRNSTST``). Owner names and addresses are
  never asked for.
- **3A station radii** (same atlas, layer 2): the half-mile circles around
  stations the compliance model uses, one dissolved shape.
- **MassDEP estimated sewer service areas** (layers 1 and 2: privately and
  publicly owned treatment works; table 3: systems not yet mapped).

Each layer is read the same way (:func:`visionhousing.data.sources.infrastructure.query_features`):
the service is asked for every object id first, then for the features in
chunks of ids with a fixed column list, so no page is skipped or repeated.
The cached file records the layer's own edit date (its version stamp), the
read date, the service's own count and the count received; the read fails
when they differ (completeness check).

Terms (checked 2026-11-25, before any data request): the hosts publish no
robots.txt rule against these services (services1.arcgis.com answers "Invalid
URL" for robots.txt; www.arcgis.com allows all; arcgisserver.digital.mass.gov
answers 502 for it). The atlas item says "Data are public"; the MassDEP item
carries a disclaimer (estimates for broad planning; not for parcel-level use)
and a citation, and does not speak to redistribution. Only derived tract and
district sums enter the pack; the raw outlines stay in the local cache.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources.infrastructure import query_features
from visionhousing.data.sources.ma_intake import cache_dir, load_settings

__all__ = [
    "fetch_capacity_layer",
    "fetch_sewer_layers",
    "layer_stamp",
    "read_cached",
]


def _get_json(client: HttpClient, url: str, params: dict[str, str]) -> dict[str, Any]:
    doc = json.loads(client.get_bytes(url, params).decode("utf-8"))
    if "error" in doc:
        raise FetchError(f"{url}: {doc['error']}")
    return doc


def layer_stamp(client: HttpClient, url: str) -> dict[str, Any]:
    """The layer's version stamp and size, as the service describes itself.

    Returns ``{"name", "last_edit", "service_count", "fields"}``: the layer
    name, its last edit date (ISO; empty when the service does not say), the
    feature count the service reports, and the field names it offers.
    """
    info = _get_json(client, url, {"f": "json"})
    edit = (info.get("editingInfo") or {}).get("lastEditDate")
    last = datetime.fromtimestamp(float(edit) / 1000, tz=UTC).date().isoformat() if edit else ""
    count = _get_json(client, f"{url}/query", {"where": "1=1", "returnCountOnly": "true",
                                                "f": "json"}).get("count")  # fmt: skip
    return {
        "name": str(info.get("name") or ""),
        "last_edit": last,
        "service_count": int(count) if count is not None else None,
        "fields": [str(f.get("name")) for f in info.get("fields") or []],
    }


def read_cached(path: Path) -> dict[str, Any]:
    """Read a cached file written by :func:`fetch_capacity_layer` or :func:`fetch_sewer_layers`."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    text = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with part.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(text)
    part.replace(path)


def _read_layer(
    client: HttpClient,
    url: str,
    fields: list[str],
    *,
    geometry: bool,
    offset_deg: float,
) -> dict[str, Any]:
    stamp = layer_stamp(client, url)
    missing = [f for f in fields if f not in stamp["fields"]]
    if missing:
        raise FetchError(f"{url}: the service has no field {', '.join(missing)}")
    features = query_features(
        client, url, "1=1", ("OBJECTID", *fields), geometry=geometry, offset_deg=offset_deg
    )
    if stamp["service_count"] is not None and len(features) != stamp["service_count"]:
        raise FetchError(f"{url}: got {len(features)} features, the service counts "
                         f"{stamp['service_count']}")  # fmt: skip
    features.sort(key=lambda f: int((f.get("attributes") or {}).get("OBJECTID") or 0))
    return {
        "url": url,
        "layer_name": stamp["name"],
        "last_edit": stamp["last_edit"],
        "service_count": stamp["service_count"],
        "received": len(features),
        "fields": list(fields),
        "read_on": date.today().isoformat(),
        "features": features,
    }


def fetch_capacity_layer(
    kind: str,
    cache: Path,
    settings: dict[str, Any] | None = None,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Read (or reuse) one 3A atlas layer: ``kind`` is ``parcels`` or ``station_radii``.

    Settings: ``capacity.<kind>`` in ``assumptions/ma_intake.yaml`` (source id,
    layer number, fields, simplification). Returns the cached document and
    its path. Raises :class:`FetchError` when the service refuses, lacks a
    field, or the features received do not add up to its own count.
    """
    spec = (settings or load_settings())["capacity"][kind]
    path = cache_dir(cache) / str(spec["file"])
    if path.is_file() and not refresh:
        return read_cached(path), path
    client = client or HttpClient(timeout=120)
    source = get_source(str(spec["source"]))
    url = source.url(layer=int(spec["layer"])).removesuffix("/query")
    data = _read_layer(
        client, url, list(spec.get("fields") or []), geometry=True,
        offset_deg=float(spec["simplify_degrees"]),
    )  # fmt: skip
    data["id"] = source.id
    _save(path, data)
    if manifest is not None:
        vintage = f"layer edited {data['last_edit'] or 'unknown'}; read {data['read_on']}"
        mf.record(mf.entry_for_file(source.id, url, path, vintage, root=mf.ROOT), manifest)
    return data, path


def fetch_sewer_layers(
    cache: Path,
    settings: dict[str, Any] | None = None,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Read (or reuse) MassDEP's estimated sewer service areas and the unmapped-systems table.

    Settings: ``sewer_estimated`` in ``assumptions/ma_intake.yaml``. The cached
    document holds ``{"edition", "read_on", "layers": {name: layer doc}}``;
    each polygon layer doc is as in :func:`fetch_capacity_layer`, the
    unmapped-systems table has attributes only.
    """
    spec = (settings or load_settings())["sewer_estimated"]
    path = cache_dir(cache) / str(spec["file"])
    if path.is_file() and not refresh:
        return read_cached(path), path
    client = client or HttpClient(timeout=120)
    source = get_source(str(spec["source"]))
    base = source.url(layer=0).rsplit("/", 2)[0]
    layers: dict[str, Any] = {}
    for name, entry in spec["layers"].items():
        url = f"{base}/{int(entry['layer'])}"
        layers[name] = _read_layer(
            client, url, list(entry.get("fields") or []),
            geometry=bool(entry.get("geometry", True)),
            offset_deg=float(spec["simplify_degrees"]),
        )  # fmt: skip
    data = {
        "id": source.id,
        "edition": str(spec.get("edition") or ""),
        "read_on": date.today().isoformat(),
        "layers": layers,
    }
    _save(path, data)
    if manifest is not None:
        vintage = f"{data['edition']}; read {data['read_on']}"
        mf.record(mf.entry_for_file(source.id, base, path, vintage, root=mf.ROOT), manifest)
    return data, path
