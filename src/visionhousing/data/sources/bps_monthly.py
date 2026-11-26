"""Building Permits Survey: state monthly year-to-date files, with their release dates.

Plain-language summary
----------------------
Each month the Census Bureau posts a state file of permits authorized so far in
the year (``st{YY}{MM}y.txt``: "y" = year to date). The layout is the same as
the annual files, but the survey date is ``YYYYMM`` instead of ``YYYY99``.

The baseline uses these files for one diagnostic only: a nowcast of the year
between the last full year of data and the first projected year (the "2026
bridge", :mod:`visionhousing.baseline.card`). For that it needs to know *when*
each file was released, so that a run only uses files released on or before
its information cutoff. The release date is the file's ``Last-Modified`` date
on the Census server, read with a ``HEAD`` request. That date is later than the
first release if the Bureau re-posted the file; the card says so.

Files are cached in ``data/raw/bps_monthly/`` with their release dates in
``releases.json`` beside them; downloads are recorded in the data manifest.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources.bps import STRUCTURES, parse_annual

__all__ = [
    "SOURCE_ID",
    "fetch_ytd",
    "file_name",
    "parse_state_ytd",
    "release_date",
    "state_ytd_units",
]

SOURCE_ID = "bps_state_monthly_ytd"
"""Registry id of the monthly year-to-date state files."""

RELEASES = "releases.json"
"""Name of the release-date record kept beside the cached files."""


def file_name(year: int, month: int) -> str:
    """Census file name of a year-to-date state file: ``st2609y.txt`` for 2026-09."""
    return f"st{year % 100:02d}{month:02d}y.txt"


def parse_state_ytd(text: str, year: int, month: int) -> pd.DataFrame:
    """Read one year-to-date state file into a wide table (see ``bps.parse_annual``)."""
    return parse_annual(text, "state", year, month=month)


def state_ytd_units(wide: pd.DataFrame, state_fips: str) -> int | None:
    """Estimated units (1, 2, 3-4, 5+ unit buildings) for one state, or ``None`` if absent."""
    row = wide[wide["state_fips"] == state_fips]
    if row.empty:
        return None
    return int(sum(int(row[f"est_{s}_units"].iloc[0]) for s in STRUCTURES))


def release_date(client: HttpClient, url: str) -> str | None:
    """The file's ``Last-Modified`` date (UTC, ``YYYY-MM-DD``), or ``None`` if not posted.

    A 404 answer means the month is not released yet and gives ``None``.
    """
    try:
        resp = client.open(url, method="HEAD", ok=(200,))
    except FetchError as err:
        if err.status == 404:
            return None
        raise
    try:
        value = resp.headers.get("Last-Modified") if getattr(resp, "headers", None) else None
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    if not value:
        return None
    return parsedate_to_datetime(value).astimezone(UTC).date().isoformat()


def _read_releases(folder: Path) -> dict[str, str | None]:
    path = folder / RELEASES
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def fetch_ytd(
    periods: Iterable[tuple[int, int]],
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh_releases: bool = False,
    released_by: str | None = None,
) -> dict[tuple[int, int], dict[str, object]]:
    """Download (or reuse) year-to-date files and read their release dates.

    Args:
        periods: ``(year, month)`` pairs, for example ``[(2026, 9), (2025, 9)]``.
        cache: Folder for raw files (``data/raw``); files go in ``cache/bps_monthly/``.
        client: The HTTP client; a default one is made when omitted.
        manifest: Manifest file to record downloads in; ``None`` records nothing.
        refresh_releases: Read the release date again even if one is recorded.
        released_by: Information cutoff (``YYYY-MM-DD``). A file released after it
            is not downloaded; only its release date is recorded.

    Returns:
        For each period: ``{"file", "url", "release_date", "path"}``. ``path`` is
        ``None`` and ``release_date`` is ``None`` when the file is not posted;
        ``path`` is also ``None`` for a file released after ``released_by``.
    """
    source = get_source(SOURCE_ID)
    folder = Path(cache) / "bps_monthly"
    folder.mkdir(parents=True, exist_ok=True)
    releases = _read_releases(folder)
    out: dict[tuple[int, int], dict[str, object]] = {}
    for year, month in sorted(set(periods)):
        name = file_name(year, month)
        url = source.url(yy=f"{year % 100:02d}", mm=f"{month:02d}")
        dest = folder / name
        if refresh_releases or name not in releases or (releases[name] is None):
            client = client or HttpClient()
            releases[name] = release_date(client, url)
        usable = releases[name] is not None and (
            released_by is None or str(releases[name]) <= released_by
        )
        if usable and not dest.exists():
            client = client or HttpClient()
            client.download(url, dest)
            if manifest is not None:
                mf.record(
                    mf.entry_for_file(SOURCE_ID, url, dest, f"{year}-{month:02d}", root=mf.ROOT),
                    manifest,
                )
        out[(year, month)] = {
            "file": name,
            "url": url,
            "release_date": releases[name],
            "path": dest if usable and dest.exists() else None,
        }
    (folder / RELEASES).write_text(
        json.dumps(
            {**releases, "_read_on": datetime.now(UTC).date().isoformat()},
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return out
