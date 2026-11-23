"""PHFA Low-Income Housing Tax Credit (LIHTC) award lists, read from the agency's PDFs.

Plain-language summary
----------------------
The Pennsylvania Housing Finance Agency (PHFA) awards federal Low-Income
Housing Tax Credits once a year and posts the list of winning developments as
a PDF on its Multifamily Housing Programs page. Each list names the
development, its address and county, its homes and the credit awarded.

This module:

1. reads the programme page and finds the award-list links
   (:func:`award_links`; the pattern is in ``assumptions/pipeline_local.yaml``);
2. downloads each PDF to a temporary file and reads its text with ``pypdf``
   (BSD licence) (:func:`pdf_text`);
3. turns the text into one row per development (:func:`parse_awards`). The
   layout changes from year to year, so the reader looks for landmarks that
   every year shares: a line with a Pennsylvania ZIP code (city line), a county
   name, and a closing line holding the home counts and dollar amounts;
4. finds the census tract of each address with the Census Bureau's free
   geocoder (:func:`geocode`). Addresses such as "Scattered sites" are kept
   with their county only.

Only the development name, address, city, ZIP, county, homes, credit amount
and tract are kept. Sponsor contact names and e-mail addresses in the PDFs are
never read into the table. A PDF with no text layer (a scan) is listed as
skipped, not guessed.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources.opendata import read_extract, save_extract

__all__ = [
    "AWARD_COLUMNS",
    "PA_COUNTIES",
    "AwardFetch",
    "award_links",
    "fetch_awards",
    "geocode",
    "parse_awards",
    "pdf_text",
]

SOURCE = "phfa_lihtc_awards"
GEOCODER_SOURCE = "census_geocoder"
GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"

PA_COUNTIES: tuple[str, ...] = (
    "ADAMS",
    "ALLEGHENY",
    "ARMSTRONG",
    "BEAVER",
    "BEDFORD",
    "BERKS",
    "BLAIR",
    "BRADFORD",
    "BUCKS",
    "BUTLER",
    "CAMBRIA",
    "CAMERON",
    "CARBON",
    "CENTRE",
    "CHESTER",
    "CLARION",
    "CLEARFIELD",
    "CLINTON",
    "COLUMBIA",
    "CRAWFORD",
    "CUMBERLAND",
    "DAUPHIN",
    "DELAWARE",
    "ELK",
    "ERIE",
    "FAYETTE",
    "FOREST",
    "FRANKLIN",
    "FULTON",
    "GREENE",
    "HUNTINGDON",
    "INDIANA",
    "JEFFERSON",
    "JUNIATA",
    "LACKAWANNA",
    "LANCASTER",
    "LAWRENCE",
    "LEBANON",
    "LEHIGH",
    "LUZERNE",
    "LYCOMING",
    "MCKEAN",
    "MERCER",
    "MIFFLIN",
    "MONROE",
    "MONTGOMERY",
    "MONTOUR",
    "NORTHAMPTON",
    "NORTHUMBERLAND",
    "PERRY",
    "PHILADELPHIA",
    "PIKE",
    "POTTER",
    "SCHUYLKILL",
    "SNYDER",
    "SOMERSET",
    "SULLIVAN",
    "SUSQUEHANNA",
    "TIOGA",
    "UNION",
    "VENANGO",
    "WARREN",
    "WASHINGTON",
    "WAYNE",
    "WESTMORELAND",
    "WYOMING",
    "YORK",
)
"""Pennsylvania's 67 counties in alphabetical order; county ``i`` has code ``42{2i+1:03d}``."""

AWARD_COLUMNS = (
    "year",
    "development",
    "address",
    "city",
    "zip",
    "county",
    "units",
    "lihtc_award",
    "tract",
)

_ZIP_LINE = re.compile(
    r"^(?P<city>[A-Za-z .'\-]+?)\s*,\s*(?:PA\.?\s*)?(?P<zip>1[5-9]\d{3})(?:-\d{4})?\b"
)
_MONEY = re.compile(r"\$\s*([\d,]+)")
_EMAIL = re.compile(r"\S+@\S+")
_HEADER = re.compile(r"(?i)housing finance agency|tax credit.*awards|awards\s+\w+ \d{1,2}, \d{4}")


def county_fips(name: str) -> str:
    """``"Allegheny"`` -> ``"42003"``."""
    return f"42{2 * PA_COUNTIES.index(name.upper()) + 1:03d}"


def award_links(page: str, pattern: str) -> dict[int, str]:
    """Award-list PDF links on the programme page, by award year (the newest link per year)."""
    out: dict[int, str] = {}
    for href in re.findall(r'href="([^"]+\.pdf)"', page, flags=re.I):
        name = href.rsplit("/", 1)[-1].lower()
        if pattern.lower() not in name:
            continue
        year = re.match(r"(\d{4})", name)
        if not year:
            continue
        url = href if href.startswith("http") else "https://www.phfa.org" + href
        out.setdefault(int(year.group(1)), url)
    return dict(sorted(out.items()))


def pdf_text(data: bytes) -> str:
    """The text of a PDF, page by page, read from a temporary file with pypdf."""
    from pypdf import PdfReader

    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        reader = PdfReader(tmp.name)
        return "\n".join(page.extract_text() or "" for page in reader.pages)


def _county_in(text: str) -> str | None:
    words = re.sub(r"[^A-Za-z ]", " ", text).upper().split()
    for w in words:
        if w in PA_COUNTIES:
            return w
    return None


def _is_numbers_line(line: str) -> bool:
    return bool(_MONEY.search(line)) and bool(re.search(r"(?<![\d,$])\d{1,4}\s+\$", line + " $"))


def parse_awards(text: str, year: int) -> pd.DataFrame:
    """One row per awarded development from the text of one award list.

    A record is the lines from a development name up to the line holding its
    dollar amounts. Inside it: the first line is the name; the lines before
    the city/ZIP line are the address; the county is the first Pennsylvania
    county named after the address; the homes are the last whole number before
    the first dollar amount (the total homes where two counts are given); the
    credit is the last dollar amount (the federal LIHTC column is the last
    column every year).
    """
    lines = [ln.strip() for ln in text.splitlines()]
    rows: list[dict[str, object]] = []
    buf: list[str] = []
    for line in lines:
        if not line or line.lower().startswith(("development name", "page ")):
            continue
        if _HEADER.search(line):
            buf = []  # a page title: whatever came before it is not part of a record
            continue
        buf.append(line)
        if not _MONEY.search(line):
            continue
        record, buf = buf, []
        zip_at = next((i for i, ln in enumerate(record) if _ZIP_LINE.match(ln)), None)
        if zip_at is None or zip_at == 0:
            continue
        found = _ZIP_LINE.match(record[zip_at])
        after = " ".join(_EMAIL.sub(" ", ln) for ln in record[zip_at:])
        county = _county_in(after[found.end() :] if found else after) or _county_in(after)
        if county is None:
            continue
        before_money = line[: _MONEY.search(line).start()]
        counts = re.findall(r"(?<![\d+])(\d{1,4})(?![\d+])", before_money)
        awards = [int(m.replace(",", "")) for m in _MONEY.findall(line)]
        name = re.sub(r"\s*\*+\s*$", "", record[0]).strip()
        rows.append(
            {
                "year": int(year),
                "development": name,
                "address": " ".join(record[1:zip_at]).strip(),
                "city": found.group("city").strip() if found else "",
                "zip": found.group("zip") if found else "",
                "county": county_fips(county),
                "units": int(counts[-1]) if counts else None,
                "lihtc_award": awards[-1] if awards else None,
            }
        )
    out = pd.DataFrame(rows, columns=[c for c in AWARD_COLUMNS if c != "tract"])
    out["units"] = out["units"].astype("Int64")
    out["lihtc_award"] = out["lihtc_award"].astype("Int64")
    return out


def geocode(address: str, city: str, zip_code: str, client: HttpClient) -> str:
    """The 2020 census tract (11 digits) of an address, or ``""`` when the geocoder finds none."""
    if not address or not re.search(r"\d", address):
        return ""
    params = {
        "address": f"{address}, {city}, PA {zip_code}",
        "benchmark": "Public_AR_Current",
        "vintage": "Census2020_Current",
        "format": "json",
    }
    try:
        answer = json.loads(client.get_bytes(GEOCODER_URL, params))
    except Exception:
        return ""
    matches = answer.get("result", {}).get("addressMatches", [])
    if not matches:
        return ""
    tracts = matches[0].get("geographies", {}).get("Census Tracts", [])
    return str(tracts[0].get("GEOID", "")) if tracts else ""


@dataclass
class AwardFetch:
    """The award table and what could not be read."""

    awards: pd.DataFrame
    skipped: dict[int, str] = field(default_factory=dict)


def fetch_awards(
    cache: Path,
    *,
    index_url: str,
    link_pattern: str,
    first_year: int,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
    geocode_addresses: bool = True,
) -> AwardFetch:
    """Download (or reuse) every award list from ``first_year``; parse and geocode them."""
    dest = Path(cache) / "local" / "phfa" / "lihtc_awards.csv"
    skipped_path = dest.with_suffix(".skipped.json")
    if not refresh and dest.exists():
        skipped = json.loads(skipped_path.read_text()) if skipped_path.exists() else {}
        return AwardFetch(read_extract(dest), {int(k): v for k, v in skipped.items()})
    client = client or HttpClient(timeout=120)
    page = client.get_bytes(index_url).decode("utf-8", "replace")
    frames, skipped = [], {}
    for year, url in award_links(page, link_pattern).items():
        if year < first_year:
            continue
        text = pdf_text(client.get_bytes(url))
        table = parse_awards(text, year)
        if table.empty:
            skipped[year] = "no text layer or no readable rows (scanned PDF?)"
            continue
        frames.append(table)
    awards = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=AWARD_COLUMNS)
    awards["tract"] = [
        geocode(r.address, r.city, r.zip, client) if geocode_addresses else ""
        for r in awards.itertuples()
    ]
    save_extract(
        awards,
        dest,
        source_id=SOURCE,
        url=get_source(SOURCE).url_pattern,
        vintage="award lists from " + str(first_year),
        manifest=manifest,
    )
    skipped_path.write_text(json.dumps(skipped, indent=1) + "\n", encoding="utf-8")
    return AwardFetch(read_extract(dest), skipped)
