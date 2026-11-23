"""Who owns single-family homes: entity, non-homestead and out-of-state shares, as pack tables.

Plain-language summary
----------------------
Builds two pack tables for the ``owners`` profile section, for the two
counties with open parcel records:

- ``owners_area`` (one row per tract and one per county): single-family
  parcels (``sf_parcels``) and how many of them
  - are owned by an **entity** (``sf_entity``). Philadelphia: the owner name
    contains a word from ``assumptions/owners.yaml`` (LLC, INC, TRUST ...),
    tested on the City's server. Allegheny: the assessment office's own owner
    type is "CORPORATION";
  - have **no homestead exemption** (``sf_non_homestead``): the owner has not
    claimed the home as their main residence;
  - send the tax bill to an address **outside Pennsylvania**
    (``sf_out_of_state``).
- ``owners_deeds_year`` (Philadelphia only, one row per year): arm's-length
  single-family deeds (``deeds``) and how many went to an entity grantee
  (``entity_deeds``). ``partial_year`` is 1 for the year the records were read.

Stocks (who owns today) and flows (who is buying) answer different questions;
the section shows both and says which is which. No owner name is ever stored.

:func:`benchmarks` reads the published comparison figures (Philadelphia Fed,
Pew) from the ``_owners`` group of ``assumptions/evidence.yaml``. They are
shown next to our numbers as cited context, never mixed into them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.features.parcels import ALLEGHENY, PHILADELPHIA, philadelphia_class

__all__ = [
    "AREA_COLUMNS",
    "ASSUMPTIONS",
    "DEED_COLUMNS",
    "allegheny_owners",
    "benchmarks",
    "deeds_year",
    "load_rules",
    "philadelphia_owners",
]

ROOT = Path(__file__).resolve().parents[3]
ASSUMPTIONS = ROOT / "assumptions" / "owners.yaml"
EVIDENCE = ROOT / "assumptions" / "evidence.yaml"

AREA_COLUMNS = (
    "level",
    "geoid",
    "county",
    "as_of",
    "entity_basis",
    "sf_parcels",
    "sf_entity",
    "sf_non_homestead",
    "sf_out_of_state",
)
DEED_COLUMNS = ("county", "year", "deeds", "entity_deeds", "partial_year")


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/owners.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def philadelphia_owners(
    parcels: pd.DataFrame, tracts: np.ndarray, read_on: str, parcel_rules: Mapping[str, Any]
) -> pd.DataFrame:
    """``owners_area`` rows for Philadelphia from the slim OPA extract (single-family only)."""
    p = parcels[["category", "condo", "homestead", "entity", "out_of_state"]].copy()
    p["tract"] = tracts
    p = p[philadelphia_class(p["category"], p["condo"], parcel_rules) == "single_family"]
    for c in ("homestead", "entity", "out_of_state"):
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0).astype(int)

    def summary(rows: pd.DataFrame) -> dict[str, int]:
        return {
            "sf_parcels": len(rows),
            "sf_entity": int(rows["entity"].sum()),
            "sf_non_homestead": int((rows["homestead"] == 0).sum()),
            "sf_out_of_state": int(rows["out_of_state"].sum()),
        }

    rows = [
        {"level": "tract", "geoid": t, **summary(part)}
        for t, part in p[p["tract"] != ""].groupby("tract")
    ]
    rows.append({"level": "county", "geoid": PHILADELPHIA, **summary(p)})
    return _finish(rows, PHILADELPHIA, read_on, "owner name contains an entity word")


def allegheny_owners(single_family: pd.DataFrame) -> pd.DataFrame:
    """``owners_area`` rows for Allegheny from the server-side single-family extract."""
    sf = single_family.copy()
    sf["tract"] = sf["tract"].fillna("")
    sf = sf[(sf["tract"] == "") | sf["tract"].str.startswith(ALLEGHENY)]
    read_on = str(sf["read_on"].iloc[0]) if "read_on" in sf.columns and len(sf) else ""
    rows = []
    for r in sf.to_dict("records"):
        key = r["tract"]
        rows.append(
            {
                "level": "county" if key == "" else "tract",
                "geoid": ALLEGHENY if key == "" else key,
                **{
                    c: int(float(r.get(c) or 0))
                    for c in ("sf_parcels", "sf_entity", "sf_non_homestead", "sf_out_of_state")
                },
            }
        )
    return _finish(rows, ALLEGHENY, read_on, "assessment owner type is corporation")


def _finish(rows: list[dict[str, Any]], county: str, read_on: str, basis: str) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    out["county"] = county
    out["as_of"] = read_on
    out["entity_basis"] = basis
    return out[list(AREA_COLUMNS)]


def deeds_year(flow: pd.DataFrame, county: str = PHILADELPHIA) -> pd.DataFrame:
    """``owners_deeds_year`` from the yearly deed counts; the year read is marked partial."""
    f = flow.copy()
    read_year = int(str(f["read_on"].iloc[0])[:4]) if "read_on" in f.columns and len(f) else None
    out = pd.DataFrame(
        {
            "county": county,
            "year": pd.to_numeric(f["year"], errors="coerce").astype(int),
            "deeds": pd.to_numeric(f["deeds"], errors="coerce").astype(int),
            "entity_deeds": pd.to_numeric(f["entity_deeds"], errors="coerce").astype(int),
        }
    )
    out["partial_year"] = (out["year"] == read_year).astype(int) if read_year else 0
    return out[list(DEED_COLUMNS)].sort_values("year").reset_index(drop=True)


def benchmarks(path: Path = EVIDENCE) -> list[dict[str, Any]]:
    """Published comparison figures from the ``_owners`` group of ``evidence.yaml``.

    Each item: ``id``, ``place``, ``measure``, ``value``, ``unit``, ``period``,
    ``definition`` and ``source`` (citation, url, accessed, locator,
    verification). Checked against the evidence source schema.
    """
    from visionhousing.ledger.evidence import EvidenceSource

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out = []
    for key, item in (data.get("_owners") or {}).items():
        source = EvidenceSource.model_validate(item["source"])
        out.append(
            {
                "id": key,
                **{k: v for k, v in item.items() if k != "source"},
                "source": source.model_dump(mode="json"),
            }
        )
    return out
