"""Small helpers for city open-data query services (Carto SQL and CKAN datastore SQL).

Plain-language summary
----------------------
Philadelphia publishes its property, deed and permit records through a Carto
SQL service, and the Western Pennsylvania Regional Data Center (WPRDC)
publishes Allegheny County and Pittsburgh records through a CKAN datastore
that also answers SQL. Both let us **add things up on the publisher's
server** (``GROUP BY``, medians) and download only the totals, instead of
downloading half a million rows. This module sends those queries and returns
the rows as a pandas table.

- :func:`carto_sql` sends one query to Philadelphia's service and reads the
  CSV answer.
- :func:`ckan_sql` sends one query to the WPRDC service, page by page
  (``LIMIT``/``OFFSET``), and joins the pages.
- :func:`sql_text` quotes a value for an SQL string, and :func:`sql_list`
  writes a quoted list, so values read from ``assumptions/`` are always safe
  inside a query.
- :func:`word_regex` turns a list of words into one Postgres pattern that
  matches any of them as a whole word.

Nothing here keeps a file; the callers save what they need in the cache.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import BadRequestError, HttpClient

__all__ = [
    "CARTO_URL",
    "CKAN_SQL_URL",
    "carto_sql",
    "ckan_sql",
    "read_extract",
    "save_extract",
    "sql_list",
    "sql_text",
    "word_regex",
]

CARTO_URL = "https://phl.carto.com/api/v2/sql"
CKAN_SQL_URL = "https://data.wprdc.org/api/3/action/datastore_search_sql"
BUSY_RETRIES = 3
"""Extra attempts when Carto says the query waited too long in its queue."""
BUSY_WAIT = 10.0
"""Seconds waited before the first new attempt (then 20, 30)."""
CKAN_PAGE = 30_000
"""Rows asked for per page; the WPRDC service caps one answer at 32,000 rows."""


def sql_text(value: object) -> str:
    """Quote a value as an SQL string literal (single quotes doubled)."""
    return "'" + str(value).replace("'", "''") + "'"


def sql_list(values: Iterable[object]) -> str:
    """``('a', 'b')``: a quoted SQL list for ``IN``."""
    items = [sql_text(v) for v in values]
    if not items:
        raise ValueError("an SQL list needs at least one value")
    return "(" + ", ".join(items) + ")"


def word_regex(words: Iterable[str]) -> str:
    r"""One Postgres pattern matching any of the words as whole words.

    ``["LLC", "L L C"]`` -> ``\m(LLC|L L C)\M``. Postgres ``\m`` and ``\M`` mark
    the start and end of a word. Only letters, digits and spaces are allowed in
    a word, so nothing in the list can change the meaning of the pattern.
    """
    clean = []
    for w in words:
        w = str(w).strip().upper()
        if not re.fullmatch(r"[A-Z0-9 ]+", w):
            raise ValueError(f"entity word {w!r} must be letters, digits and spaces only")
        clean.append(w)
    if not clean:
        raise ValueError("no words given")
    return r"\m(" + "|".join(clean) + r")\M"


def carto_sql(
    query: str, client: HttpClient | None = None, *, url: str = CARTO_URL
) -> pd.DataFrame:
    """Run one query on Philadelphia's Carto SQL service and return the rows.

    The answer is read as CSV (``format=csv``), so numbers keep full precision
    and memory stays small.
    """
    client = client or HttpClient()
    for attempt in range(BUSY_RETRIES + 1):
        try:
            raw = client.get_bytes(url, {"q": query, "format": "csv"})
            break
        except BadRequestError as err:
            # A busy server answers 400 "query_wait_timeout"; wait and ask again.
            if "timeout" not in err.body or attempt >= BUSY_RETRIES:
                raise
            client.sleep(BUSY_WAIT * (attempt + 1))
    if not raw.strip():
        return pd.DataFrame()
    return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, na_values=[""])


def ckan_sql(
    sql: str,
    client: HttpClient | None = None,
    *,
    url: str = CKAN_SQL_URL,
    page: int = CKAN_PAGE,
    order_by: str | None = None,
) -> pd.DataFrame:
    """Run a query on the WPRDC datastore, page by page, and return all rows.

    Args:
        sql: A ``SELECT`` without ``LIMIT``; pages are added here.
        client: The HTTP client.
        url: The datastore SQL address.
        page: Rows per page.
        order_by: Columns for a stable page order; needed when the answer can
            be longer than one page.
    """
    client = client or HttpClient()
    frames: list[pd.DataFrame] = []
    offset = 0
    while True:
        text = sql
        if order_by:
            text += f" ORDER BY {order_by}"
        text += f" LIMIT {page} OFFSET {offset}"
        answer = json.loads(client.get_bytes(url, {"sql": text}))
        if not answer.get("success"):
            raise RuntimeError(f"WPRDC query failed: {answer.get('error')}")
        rows = answer["result"]["records"]
        frames.append(pd.DataFrame(rows))
        if len(rows) < page or not order_by:
            break
        offset += page
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out.drop(columns=[c for c in out.columns if c == "_full_text"])


def save_extract(
    frame: pd.DataFrame,
    dest,
    *,
    source_id: str,
    url: str,
    vintage: str,
    manifest=None,
) -> None:
    """Write an extract to the cache as CSV and record it in the download manifest.

    Args:
        frame: The rows to keep (already aggregated or stripped of names).
        dest: Cache file path.
        source_id: Id in ``sources.yaml``.
        url: Where the rows came from (no key in it).
        vintage: Edition read, for example the date the records were read.
        manifest: Manifest file, or ``None`` to record nothing.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    frame.to_csv(part, index=False, lineterminator="\n")
    part.replace(dest)
    if manifest is not None:
        mf.record(mf.entry_for_file(source_id, url, dest, vintage, root=mf.ROOT), manifest)


def read_extract(path) -> pd.DataFrame:
    """Read a cached extract written by :func:`save_extract`; code columns stay text."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
