"""Where each place-profile section's data come from, and what those sources cover.

Plain-language summary
----------------------
A place profile has many sections (people, homes, jobs, zoning, safety, ...).
For a given place some sections have no data: the state's data pack does not
hold that table, or the source covers only some places (a city's parcel file,
one state's ordinance list). Showing an empty block there tells a reader
nothing. Instead, the screen says which source the section is built from and
what that source covers, so "not measured here" is never confused with "zero".

:data:`SECTION_SOURCES` lists, for each section key of
``profile.registry.SECTIONS``, the source ids (from ``data/sources.yaml``) its
module reads. :func:`coverage_table` adds each source's name, publisher, the
geographic levels it covers, its data tier (every state / many states / local
refinement only) and, for sources that exist only for some counties, those
county codes (read from the section module's own ``SOURCES`` mapping). The
command ``python -m visionhousing.profile.coverage`` writes the table to
``web/js/content/coverage.json``, which the Place screen reads (live and static
builds alike); ``tests/test_profile_coverage.py`` checks it is up to date and
that every id is a registered source that the section's module names.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

__all__ = ["OUT_PATH", "SECTION_SOURCES", "TIER_TEXT", "coverage_table", "main", "render"]

OUT_PATH = Path(__file__).resolve().parents[3] / "web" / "js" / "content" / "coverage.json"
"""Where the browser's copy of the table is written."""

SECTION_SOURCES: dict[str, tuple[str, ...]] = {
    "people": ("acs5_summary_file",),
    "housing": ("acs5_summary_file", "acs5_profile_tables", "fhfa_hpi_county", "fhfa_hpi_tract"),
    "jobs": ("lodes_wac", "lodes_od", "cbp_county"),
    "permits": ("bps_county_annual",),
    "affordability": (
        "hud_chas",
        "hud_income_limits",
        "hud_hcv_tract",
        "hud_lihtc",
        "hud_mf_assisted",
        "hud_public_housing",
        "nlihc_gap_2026",
    ),
    "pipeline_local": ("phl_li_permits", "pgh_pli_permits", "phfa_lihtc_awards"),
    "zoning": ("pa_dced_munstats", "pa_municipal_ordinances"),
    "plans": ("pa_dced_munstats", "county_comprehensive_plans"),
    "state_law": ("massgis_mbta3a_atlas",),
    "incentives": (
        "hud_qct",
        "hud_dda",
        "treasury_oz2027",
        "cdfi_nmtc_lic",
        "dced_koz_sites",
        "hud_lihtc",
        "nps_nrhp",
    ),
    "transit": ("ntm_stops", "tiger_2020_tabblock"),
    "infrastructure": (
        "epa_cwns_2022",
        "epa_sewersheds",
        "pa_dep_pws_service_areas",
        "pa_dep_act537_plan_status",
        "tiger_2020_tabblock",
    ),
    "hazards": ("fema_nfhl", "usgs_3dep_dem", "usgs_annual_nlcd"),
    "safety": ("fbi_cde_agency",),
    "typology": ("acs5_summary_file",),
    "owners": ("phl_opa_properties", "alle_assessments", "phl_rtt_summary"),
    "parcels": ("phl_opa_properties", "alle_assessments", "alle_property_sales"),
    "data_quality": (),
}
"""Section key to the registered source ids its module reads."""

TIER_TEXT = {
    0: "published for every state",
    1: "published for many states, not all",
    2: "local only: published for a few counties or cities",
}
"""The registry's data tier in plain words."""

NO_SOURCE = (
    "This section summarises how complete the other sections are for the place; "
    "it has no source of its own."
)


def _local_places(key: str) -> dict[str, list[str]]:
    """Source id to the county codes it exists for, from the section module's ``SOURCES``."""
    try:
        module = importlib.import_module(f"visionhousing.profile.sections.{key}")
    except ModuleNotFoundError:
        return {}
    by_place = getattr(module, "SOURCES", None)
    if not isinstance(by_place, dict):
        return {}
    out: dict[str, list[str]] = {}
    for fips, sid in by_place.items():
        out.setdefault(str(sid), []).append(str(fips))
    return {k: sorted(v) for k, v in out.items()}


def _first_sentence(text: str) -> str:
    t = " ".join(str(text).split())
    cut = t.find(". ")
    return t if cut < 0 else t[: cut + 1]


def coverage_table() -> dict[str, Any]:
    """Every section's sources and what they cover, plus the plain sentence the screen shows."""
    from visionhousing.data.registry import load_registry
    from visionhousing.profile.registry import SECTIONS

    reg = load_registry()
    sections: dict[str, Any] = {}
    for key, title in SECTIONS:
        local = _local_places(key)
        sources = []
        for sid in SECTION_SOURCES.get(key, ()):
            s = reg[sid]
            places = local.get(sid, [])
            covers = ", ".join(s.geography)
            sources.append(
                {
                    "id": sid,
                    "name": s.name,
                    "publisher": s.publisher,
                    "geography": list(s.geography),
                    "tier": s.tier,
                    "tier_text": TIER_TEXT[s.tier],
                    "counties": places,
                    "covers": (
                        f"{s.name} ({s.publisher}): {TIER_TEXT[s.tier]}; by {covers}"
                        + (f"; only for counties {', '.join(places)}" if places else "")
                        + "."
                    ),
                    "limits": _first_sentence(s.limits),
                }
            )
        if sources:
            local_only = all(x["tier"] == 2 for x in sources)
            text = (
                f"Not measured for this place. {title} is built from "
                + "; ".join(f"{x['name']} ({x['tier_text']})" for x in sources)
                + "."
                + (
                    " These sources exist only for some places, so most places have no data here."
                    if local_only
                    else " A source that covers this state may not have been added to its data "
                    "pack yet, or may not report for this place."
                )
            )
        else:
            text = NO_SOURCE
        sections[key] = {"title": title, "text": text, "sources": sources}
    return {
        "version": 1,
        "generated_by": "python -m visionhousing.profile.coverage",
        "registry": "src/visionhousing/data/sources.yaml",
        "sections": sections,
    }


def render() -> str:
    """The JSON text written to :data:`OUT_PATH`."""
    return json.dumps(coverage_table(), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write (or with ``--check``, compare) ``web/js/content/coverage.json``."""
    p = argparse.ArgumentParser(description="Write the profile coverage table for the browser.")
    p.add_argument("--out", type=Path, default=OUT_PATH)
    p.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = p.parse_args(argv)
    text = render()
    if args.check:
        same = args.out.is_file() and args.out.read_text(encoding="utf-8") == text
        print("up to date" if same else f"{args.out} is out of date")
        return 0 if same else 1
    args.out.write_text(text, encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
