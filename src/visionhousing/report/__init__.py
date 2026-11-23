"""The report writer: a run's brief as Markdown, print-ready HTML, CSV and JSON.

``visionhousing report --state PA`` calls :func:`write_report`. The brief is
built once as data (``report.brief``), then drawn as Markdown or HTML
(``report.render``); the CSV tables keep full precision (``report.tables``).
Every file is stamped with the code, data and assumption hashes, the seed and
the draws. See ``docs/modules/report.md``.
"""

from visionhousing.report.brief import brief_blocks
from visionhousing.report.render import to_html, to_markdown
from visionhousing.report.tables import csv_tables, to_csv
from visionhousing.report.writer import DEFAULT_DIR, FORMATS, default_stem, render, write_report

__all__ = [
    "DEFAULT_DIR",
    "FORMATS",
    "brief_blocks",
    "csv_tables",
    "default_stem",
    "render",
    "to_csv",
    "to_html",
    "to_markdown",
    "write_report",
]
