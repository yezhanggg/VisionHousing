"""Check which dates Philadelphia's public permit layer carries, and how often each is filled.

Plain-language summary
----------------------
A permit-timing model needs the date an application was received as well as
the date the permit was issued. Philadelphia publishes its Licenses and
Inspections permits in two places: a map-service layer (ArcGIS FeatureServer
``PERMITS``, layer 0) and the city's SQL table ``permits`` behind
data.phila.gov. The layer's description says the underlying system holds a
"date of application", so this check reads the published field list and
counts how many rows fill each date field.

:func:`check_fields` makes a handful of small requests (the field list and one
count per date field; no rows are downloaded) and returns a plain dictionary:
every field name and type, which ones are dates, which names look like an
application or received date, and the filled count of each date field. The
answer recorded on 2026-11-24 is in ``docs/modules/evidence_states.md``.

It reaches the network, so it runs only on request (``visionhousing
evidence-states phl-fields``) and never in the offline tests, which use a
fake client.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from visionhousing.data.http import HttpClient

__all__ = ["LAYER_URL", "check_fields"]

LAYER_URL = (
    "https://services.arcgis.com/fLeGjb7u4uXqeF9q/ArcGIS/rest/services/PERMITS/FeatureServer/0"
)
APPLICATION_WORDS = re.compile(r"appl|receiv|submit|intake|filed|accept", re.IGNORECASE)


def check_fields(
    get_bytes: Callable[[str, dict[str, Any]], bytes] | None = None, url: str = LAYER_URL
) -> dict[str, Any]:
    """Read the layer's field list and the filled count of every date field.

    Args:
        get_bytes: ``(url, params) -> bytes``; defaults to the project's HTTP client.
        url: the layer address.

    Returns:
        ``{"layer", "fields", "date_fields", "application_like", "rows", "filled",
        "describes_application_date", "has_application_field"}``.
    """
    if get_bytes is None:
        client = HttpClient()
        get_bytes = client.get_bytes

    def ask(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return json.loads(get_bytes(url + path, {**params, "f": "json"}))

    meta = ask("", {})
    fields = [(f["name"], f["type"]) for f in meta.get("fields", [])]
    dates = [n for n, t in fields if t == "esriFieldTypeDate"]
    like = [n for n, _ in fields if APPLICATION_WORDS.search(n)]

    def count(where: str) -> int:
        return int(ask("/query", {"where": where, "returnCountOnly": "true"})["count"])

    return {
        "layer": meta.get("name"),
        "fields": fields,
        "date_fields": dates,
        "application_like": like,
        "rows": count("1=1"),
        "filled": {d: count(f"{d} IS NOT NULL") for d in dates},
        "describes_application_date": "date of application" in meta.get("description", "").lower(),
        "has_application_field": any(n in dates for n in like),
    }
