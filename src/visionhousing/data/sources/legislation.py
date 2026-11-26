"""Pennsylvania General Assembly pages: bill status and the text of a statute.

Plain-language summary
----------------------
The General Assembly publishes every bill's page (sponsors, short title and the
list of actions with dates) and the text of every law at palegis.us. Its
robots file allows ordinary reading. Legislative records are public; we cite
them as "Public legislative record" and keep only status, dates, citations and
summaries in our own words.

- :func:`read_bill_status` reads a bill page's HTML into a :class:`BillStatus`:
  the short title, the memo subject and every action with its date, in order.
- :func:`statute_text` turns the statute page's HTML into plain text, one
  paragraph per line, keeping the section markers the site prints.
- :func:`fetch_page` downloads a page once into the raw cache.

The Pennsylvania Code and Bulletin site (pacodeandbulletin.gov) disallows all
automated reading in its robots file, so it is linked, never fetched.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from visionhousing.data.http import HttpClient

__all__ = [
    "BillStatus",
    "fetch_page",
    "parse_action_date",
    "read_bill_status",
    "statute_text",
    "strip_date",
]

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_DATE = re.compile(r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s+(\d{4})")


@dataclass(frozen=True)
class BillStatus:
    """What a bill page says.

    Attributes:
        short_title: The bill's short title.
        memo_subject: The sponsor's memo subject, when shown.
        actions: ``(text, date)`` pairs in the page's order; a heading row
            such as "In the Senate" has no date (``None``).
    """

    short_title: str
    memo_subject: str
    actions: tuple[tuple[str, date | None], ...]

    @property
    def last_action(self) -> tuple[str, date | None]:
        """The last dated action (``("", None)`` when there is none)."""
        for text, when in reversed(self.actions):
            if when is not None:
                return text, when
        return "", None

    @property
    def enacted(self) -> bool:
        """True when an action says the Governor approved it (it became an Act)."""
        return any(
            re.search(r"(?i)approved by the governor|\bact no\.", t) for t, _ in self.actions
        )


def _plain(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_action_date(text: str) -> date | None:
    """The date at the end of an action ("Referred to X, Feb. 2, 2026") or ``None``."""
    found = list(_DATE.finditer(text))
    if not found:
        return None
    month, day, year = found[-1].groups()
    m = _MONTHS.get(month.lower()[:4].rstrip(".")) or _MONTHS.get(month.lower()[:3])
    if m is None:
        return None
    return date(int(year), m, int(day))


def strip_date(text: str) -> str:
    """An action's text without its date: "Referred to X, Feb. 2, 2026" -> "Referred to X"."""
    found = list(_DATE.finditer(text))
    if not found:
        return text.strip()
    m = found[-1]
    out = (text[: m.start()].rstrip(" ,") + " " + text[m.end() :].strip()).strip()
    return re.sub(r"\s+", " ", out)


def _block(page: str, label: str) -> str:
    m = re.search(
        rf"<strong>\s*{label}\s*</strong>\s*</div>\s*<div[^>]*>(.*?)</div>", page, re.S | re.I
    )
    return m.group(1) if m else ""


def read_bill_status(page: str | bytes) -> BillStatus:
    """Read a palegis.us bill page.

    Raises:
        ValueError: The page has no short title (not a bill page, or the layout changed).
    """
    text = page.decode("utf-8", "replace") if isinstance(page, bytes) else page
    title = _plain(_block(text, "Short Title"))
    if not title:
        raise ValueError("no 'Short Title' on the page: not a bill page or the layout changed")
    memo = _plain(_block(text, "Memo Subject"))
    actions: list[tuple[str, date | None]] = []
    m = re.search(r"<strong>\s*Actions\s*</strong>.*?<table[^>]*>(.*?)</table>", text, re.S | re.I)
    if m:
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S | re.I):
            cells = [_plain(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
            if not cells or not cells[-1]:
                continue
            actions.append((cells[-1], parse_action_date(cells[-1])))
    return BillStatus(title, memo, tuple(actions))


def statute_text(page: str | bytes) -> str:
    """Plain text of a palegis.us statute page: one paragraph per line, blank lines dropped."""
    text = page.decode("utf-8", "replace") if isinstance(page, bytes) else page
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", "", text)
    text = re.sub(r"(?i)<(p|br|div|tr|h\d)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def fetch_page(url: str, dest: Path, *, client: HttpClient | None = None) -> tuple[bytes, bool]:
    """Download ``url`` to ``dest`` unless it is already there.

    Returns:
        The bytes, and whether they were downloaded now.
    """
    dest = Path(dest)
    if dest.exists():
        return dest.read_bytes(), False
    data = (client or HttpClient()).get_bytes(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data, True


def today() -> date:
    """Today's date (a function so tests can replace it)."""
    return datetime.now().date()
