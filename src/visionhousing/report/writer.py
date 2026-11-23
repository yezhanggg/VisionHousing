"""Write a run's brief to files: Markdown, HTML, CSV tables or JSON.

Plain-language summary
----------------------
:func:`render` turns an engine result into the text of one format, and
:func:`write_report` saves it. The command ``visionhousing report`` calls
these. The same run always gives the same bytes; the only thing that can
change is the run date printed in the stamp, which the caller chooses (or
leaves out).

Formats:

- ``md``: the brief in Markdown;
- ``html``: the same brief as one print-ready page (open it and print to PDF);
- ``csv``: one CSV file per table (see ``report.tables``), named
  ``<stem>-<table>.csv`` in the output folder;
- ``json``: the full explanation (sentences, verdicts, funnels, drivers,
  waterfall, limits, "explain this number" payloads and the slide spec).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from visionhousing.explain import explain
from visionhousing.report.brief import brief_blocks
from visionhousing.report.render import to_html, to_markdown
from visionhousing.report.tables import csv_tables

__all__ = ["DEFAULT_DIR", "FORMATS", "default_stem", "render", "write_report"]

FORMATS = ("md", "html", "csv", "json")
DEFAULT_DIR = Path("outputs") / "reports"


def default_stem(result: Any) -> str:
    """File name stem: ``PA-adu-fees-parking`` (``PA-baseline`` with no reform)."""
    reforms = "-".join(k.replace("_", "-") for k in result.reform_ids) or "baseline"
    return f"{result.state}-{reforms}"


def render(result: Any, fmt: str, *, run_date: str | None = None) -> dict[str, str]:
    """The text of one format.

    Args:
        result: An engine result (from ``engine.run``).
        fmt: One of :data:`FORMATS`.
        run_date: Date to print (``YYYY-MM-DD``), or ``None`` to print none.

    Returns:
        Name to text: ``{"brief": ...}`` for md, html and json; one entry per
        table for csv.
    """
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}")
    e = explain(result)
    if fmt == "json":
        payload = {"run_date": run_date, **e}
        return {"brief": json.dumps(payload, indent=1, ensure_ascii=False, allow_nan=False) + "\n"}
    if fmt == "csv":
        return csv_tables(result, e, run_date=run_date)
    blocks = brief_blocks(result, e, run_date=run_date)
    return {"brief": to_markdown(blocks) if fmt == "md" else to_html(blocks)}


def write_report(
    result: Any, fmt: str, out: Path | None = None, *, run_date: str | None = None
) -> list[Path]:
    """Render one format and save it.

    Args:
        result: An engine result.
        fmt: One of :data:`FORMATS`.
        out: For md, html and json, the file to write (default
            ``outputs/reports/<stem>.<fmt>``). For csv, the folder to write into
            (default ``outputs/reports/``).
        run_date: Date to print, or ``None``.

    Returns:
        The files written.
    """
    texts = render(result, fmt, run_date=run_date)
    stem = default_stem(result)
    written: list[Path] = []
    if fmt == "csv":
        folder = out if out is not None else DEFAULT_DIR
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in texts.items():
            path = folder / f"{stem}-{name}.csv"
            path.write_bytes(text.encode("utf-8"))
            written.append(path)
        return written
    path = out if out is not None else DEFAULT_DIR / f"{stem}.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(texts["brief"].encode("utf-8"))
    return [path]
