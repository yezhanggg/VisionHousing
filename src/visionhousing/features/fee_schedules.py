"""Documented local development fee schedules: price standard prototypes from city fee lists.

Plain-language summary
----------------------
The Challenge benchmark fixes development fees at $50,000 per unit. Real fee
schedules are much more detailed: a building permit priced by floor area, a
plumbing permit priced by fixtures, an electrical permit priced by the cost
of the electrical work, a water tap priced by pipe size, and so on. This
module reads the schedules that were copied, with quotes and sources, into
``assumptions/fee_schedules.yaml`` and prices five standard prototypes with
them: a 600 sq ft ADU, a single-family home, a duplex, a fourplex and a
20-unit apartment building.

How one prototype is priced
---------------------------
Every fee **item** has a basis (how its amount is worked out), and the
prototype supplies the quantities (floor area, fixtures, meter sizes, sheets).
Where a reading could go two ways (an ADU filed as a new accessory structure
or as a new dwelling; one shared water service or a new one) the prototype
gives a low and a high quantity, so every line has a **low** and a **high**
amount. Valuation-based lines (Philadelphia's electrical permit: $25 per
$1,000 of electrical work) use the construction cost per square foot from the
Assumption Ledger, so their range comes from the ledger's cost range.

In each Monte Carlo draw the documented fee of a prototype is::

    fee = sum of low lines + position x (sum of high lines - sum of low lines)

where ``position`` is the ledger entry ``fees.local_schedule_position`` (0 =
every item at its low reading, 1 = every item at its high reading). The
valuation lines are worked out from the same draw's cost.

Rules that keep the count honest:

- An application (filing) fee that the City credits to the final permit fee
  is counted once: it adds only what exceeds the permit it is credited to.
- Tap and connection charges are their own category (``water_connection``,
  ``sewer_connection``). They are the fee counterpart of the ADU pro forma's
  ``adu.utility_connection_fee``; digging and laying the laterals is site work
  (``adu.site_utility_cost``), never a fee. See ``ADU_LEDGER_RULE``.
- An item marked ``unknown_*`` is never read as zero. A city whose core fee
  categories are not all documented, or which covers only part of a county,
  is **not applied**: its county keeps the labelled judgement fee.

What it feeds
-------------
Only the named variant ``fees_documented_local`` of the fee reform
(:func:`county_fee_matrix`, used by ``scenarios.fees``). The benchmark never
reads this module.

Fetching
--------
``python -m visionhousing.features.fee_schedules --check`` downloads every
listed document again (into ``data/raw/fee_schedules/``) and confirms that
every quote is still in its text and that each PDF's checksum is unchanged.
Robots rules were checked when the file was written; the documents whose
sites refuse scripted reading are not fetched (they have no quote).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.ledger.loader import ROOT, read_yaml

__all__ = [
    "ADU_LEDGER_RULE",
    "BASES",
    "POSITION_ID",
    "SCHEDULE_PATH",
    "STATUSES",
    "CityStatus",
    "LinePrice",
    "PrototypePrice",
    "Schedule",
    "city_status",
    "county_fee_matrix",
    "html_text",
    "load_schedule",
    "local_ids",
    "normalise",
    "price_prototype",
    "prototype_draws",
    "summary_rows",
    "verify_text",
]

SCHEDULE_PATH = ROOT / "assumptions" / "fee_schedules.yaml"
"""The documented schedules, prototypes and city rules."""

POSITION_ID = "fees.local_schedule_position"
"""Ledger id: where each draw sits between the low and high readings (0-1)."""

STATUSES = (
    "documented",
    "not_applicable",
    "optional_excluded",
    "unknown_not_reviewed",
    "unknown_refused",
)
"""Allowed item and review statuses (see the head of the YAML file)."""

COUNTED = ("documented",)
"""Statuses whose amounts enter a total."""

KNOWN = ("documented", "not_applicable")
"""Review statuses that let a core category count as complete."""

BASES = (
    "flat",
    "per_count",
    "area_tiered",
    "fixtures_tiered",
    "valuation_per_1000",
    "percent_of_table_value",
)
"""How an item's amount is worked out."""

ADU_LEDGER_RULE = (
    "Tap and connection charges (water ferrule or tap, meter, sewer connection permit) are the "
    "fee counterpart of the ADU pro forma's adu.utility_connection_fee; building, zoning and "
    "trade permits that of adu.permit_fee. Digging and laying the laterals is site work, "
    "adu.site_utility_cost, and is never counted as a fee. A fee cut in a package scales "
    "adu.permit_fee and adu.utility_connection_fee once (engine.adapters.ADU_FEE_IDS) and "
    "never adu.site_utility_cost; the variant fees_documented_local changes only the fee "
    "reform's saving, not the ADU pro forma, so no connection charge is counted twice."
)
"""How the documented connection charges relate to the ADU cost ledger (counted once)."""


# ---------------------------------------------------------------- loading


@dataclass(frozen=True)
class Schedule:
    """The parsed ``assumptions/fee_schedules.yaml``.

    Attributes:
        id: The file's id (cited by records).
        read_on: Date the documents were read.
        prototypes: Prototype id to its quantities.
        documents: Document id to its entry.
        cities: City id to its entry (county, whole_county, review).
        items: Item id to its entry.
        type_prototypes: Fee-reform structure type to prototype id.
        core: Core fee categories.
    """

    id: str
    read_on: str
    prototypes: dict[str, dict[str, Any]]
    documents: dict[str, dict[str, Any]]
    cities: dict[str, dict[str, Any]]
    items: dict[str, dict[str, Any]]
    type_prototypes: dict[str, str]
    core: tuple[str, ...]
    secondary: tuple[str, ...] = ()
    cost_ids: tuple[str, ...] = field(default=())


def _ends(value: Any) -> tuple[float, float]:
    """A quantity as ``(low, high)``: a number gives the same at both ends."""
    if isinstance(value, Mapping):
        return float(value["low"]), float(value["high"])
    return float(value), float(value)


def _check(data: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    protos = data.get("prototypes") or {}
    docs = data.get("documents") or {}
    cities = data.get("cities") or {}
    cats = data.get("categories") or {}
    allowed_cats = {*(cats.get("core") or []), *(cats.get("secondary") or [])}
    for pid, p in protos.items():
        for key in ("units", "floor_area_sqft", "schedule_class", "cost_id"):
            if key not in p:
                problems.append(f"prototype {pid}: missing {key}")
        if float(p.get("units", 0)) <= 0:
            problems.append(f"prototype {pid}: units must be above 0")
    for t, pid in (data.get("structure_type_prototypes") or {}).items():
        if pid not in protos:
            problems.append(f"structure type {t}: unknown prototype {pid}")
    for cid, c in cities.items():
        for cat, status in (c.get("review") or {}).items():
            if cat not in allowed_cats:
                problems.append(f"city {cid}: unknown category {cat}")
            if status not in STATUSES:
                problems.append(f"city {cid}: unknown review status {status}")
        missing = [k for k in cats.get("core") or [] if k not in (c.get("review") or {})]
        if missing:
            problems.append(f"city {cid}: core categories not reviewed: {missing}")
    needed = ("city", "category", "name", "status", "exemptions", "payment_stage")
    needed += ("dollars_year", "payee", "service", "doc")
    for iid, it in (data.get("items") or {}).items():
        for key in needed:
            if key not in it:
                problems.append(f"item {iid}: missing {key}")
        if it.get("city") not in cities:
            problems.append(f"item {iid}: unknown city {it.get('city')}")
        if it.get("category") not in allowed_cats:
            problems.append(f"item {iid}: unknown category {it.get('category')}")
        if it.get("status") not in STATUSES:
            problems.append(f"item {iid}: unknown status {it.get('status')}")
        if it.get("doc") not in docs:
            problems.append(f"item {iid}: unknown document {it.get('doc')}")
        if it.get("status") == "documented":
            if it.get("basis") not in BASES:
                problems.append(f"item {iid}: basis must be one of {BASES}")
            if not it.get("quote"):
                problems.append(f"item {iid}: a documented item needs a quote")
            elif len(str(it["quote"])) > 300:
                problems.append(f"item {iid}: quote longer than 300 characters")
        for pid, mult in (it.get("applies") or {}).items():
            if pid not in protos:
                problems.append(f"item {iid}: applies to unknown prototype {pid}")
            elif min(_ends(mult)) < 0:
                problems.append(f"item {iid}: negative multiplier for {pid}")
        for target in it.get("credited_to") or []:
            if target not in (data.get("items") or {}):
                problems.append(f"item {iid}: credited to unknown item {target}")
    return problems


_CACHE: dict[tuple[str, int], Schedule] = {}


def load_schedule(path: Path | str | None = None) -> Schedule:
    """Read and check ``assumptions/fee_schedules.yaml`` (once per file version).

    Raises:
        ValueError: A field is missing or a reference does not resolve.
    """
    p = Path(path) if path is not None else SCHEDULE_PATH
    key = (str(p), p.stat().st_mtime_ns)
    if key in _CACHE:
        return _CACHE[key]
    data = read_yaml(p) or {}
    problems = _check(data)
    if problems:
        raise ValueError("fee_schedules.yaml: " + " | ".join(problems))
    cats = data.get("categories") or {}
    protos = {str(k): dict(v) for k, v in (data.get("prototypes") or {}).items()}
    sched = Schedule(
        id=str(data.get("id", "fee_schedules")),
        read_on=str(data.get("read_on", "")),
        prototypes=protos,
        documents={str(k): dict(v) for k, v in (data.get("documents") or {}).items()},
        cities={str(k): dict(v) for k, v in (data.get("cities") or {}).items()},
        items={str(k): dict(v) for k, v in (data.get("items") or {}).items()},
        type_prototypes={str(k): str(v) for k, v in data["structure_type_prototypes"].items()},
        core=tuple(cats.get("core") or ()),
        secondary=tuple(cats.get("secondary") or ()),
        cost_ids=tuple(sorted({str(p["cost_id"]) for p in protos.values()})),
    )
    _CACHE[key] = sched
    return sched


def local_ids(schedule: Schedule) -> tuple[str, ...]:
    """Ledger ids the documented prices read (position, cost shares, construction costs)."""
    shares = {
        str(it["cost_share_id"])
        for it in schedule.items.values()
        if it.get("status") in COUNTED and it.get("cost_share_id")
    }
    return tuple(sorted({POSITION_ID, *shares, *schedule.cost_ids}))


# ---------------------------------------------------------------- pricing


@dataclass(frozen=True)
class LinePrice:
    """One fee line of one prototype.

    Attributes:
        item: Item id.
        low: Amount at the low reading, by draw ($ per building).
        high: Amount at the high reading, by draw.
        credited: True when the line is an application fee credited to the
            final permit (its amount is shown; only any excess is counted).
    """

    item: str
    low: np.ndarray
    high: np.ndarray
    credited: bool = False


@dataclass(frozen=True)
class PrototypePrice:
    """A prototype priced under one city's schedule.

    Attributes:
        city: City id.
        prototype: Prototype id.
        units: Homes in the prototype.
        lines: The counted lines (credited ones carry only their excess).
        shown: Every documented line, before crediting (for display).
        low: Total at the low reading, by draw ($ per building).
        high: Total at the high reading, by draw.
        unknown: Item ids that stay unknown for this city.
        complete: Every core category is documented or not applicable.
    """

    city: str
    prototype: str
    units: float
    lines: tuple[LinePrice, ...]
    shown: tuple[LinePrice, ...]
    low: np.ndarray
    high: np.ndarray
    unknown: tuple[str, ...]
    complete: bool

    def per_unit(self, position: np.ndarray | float) -> np.ndarray:
        """Documented fee per home at a position between the low and high readings."""
        pos = np.asarray(position, dtype=float)
        return (self.low + pos * (self.high - self.low)) / self.units


def _proto_value(proto: Mapping[str, Any], key: str, end: int) -> float:
    if key not in proto:
        raise ValueError(f"prototype has no '{key}'")
    return _ends(proto[key])[end]


def _amount(
    item: Mapping[str, Any],
    proto: Mapping[str, Any],
    end: int,
    params: Mapping[str, np.ndarray],
    n: int,
) -> np.ndarray:
    """One item's amount for one building at one end (0 low, 1 high), by draw."""
    basis = item["basis"]
    area = float(proto["floor_area_sqft"])
    if basis == "flat":
        return np.full(n, float(item["amount"]))
    if basis == "per_count":
        count = _proto_value(proto, str(item["count_field"]), end)
        amount = float(item["amount"]) * count
        if count > 0 and item.get("minimum") is not None:
            amount = max(amount, float(item["minimum"]))
        return np.full(n, amount)
    if basis == "area_tiered":
        steps = math.ceil(max(0.0, area - float(item["first_sqft"])) / float(item["step_sqft"]))
        return np.full(n, float(item["first_amount"]) + float(item["step_amount"]) * steps)
    if basis == "fixtures_tiered":
        fixtures = _proto_value(proto, "plumbing_fixtures", end)
        extra = max(0.0, fixtures - float(item["first_count"]))
        return np.full(n, float(item["first_amount"]) + float(item["step_amount"]) * extra)
    if basis == "valuation_per_1000":
        cost = np.asarray(params[str(proto["cost_id"])], dtype=float) * area
        share = np.asarray(params[str(item["cost_share_id"])], dtype=float)
        thousands = np.ceil(np.round(share * cost / 1000.0, 9))
        out = float(item["rate"]) * thousands
        lo = item.get("minimum")
        hi = item.get("maximum")
        return np.clip(out, float(lo) if lo is not None else 0.0, float(hi) if hi else np.inf)
    if basis == "percent_of_table_value":
        value = _proto_value(proto, "dit_value_per_sqft", end)
        return np.full(n, float(item["rate"]) * area * value)
    raise ValueError(f"unknown basis {basis!r}")


def _size(params: Mapping[str, np.ndarray]) -> int:
    sizes = {int(np.size(v)) for v in params.values()}
    return max(sizes) if sizes else 1


def price_prototype(
    schedule: Schedule,
    city: str,
    prototype: str,
    params: Mapping[str, np.ndarray],
) -> PrototypePrice:
    """Price one prototype under one city's documented schedule.

    Args:
        schedule: The parsed schedule.
        city: City id (``philadelphia``, ``pittsburgh``).
        prototype: Prototype id.
        params: Ledger id to draws; needs the prototype's ``cost_id`` and each
            valuation item's ``cost_share_id`` (see :func:`local_ids`).

    Returns:
        The priced prototype: lines, low and high totals per building by draw,
        the unknown items and whether the city's core categories are complete.
    """
    if city not in schedule.cities:
        raise ValueError(f"unknown city {city!r}")
    if prototype not in schedule.prototypes:
        raise ValueError(f"unknown prototype {prototype!r}")
    proto = schedule.prototypes[prototype]
    n = _size(params)
    raw: dict[str, LinePrice] = {}
    for iid, it in schedule.items.items():
        if it["city"] != city or it["status"] not in COUNTED:
            continue
        mult = (it.get("applies") or {}).get(prototype)
        if mult is None:
            continue
        m_lo, m_hi = _ends(mult)
        lo = m_lo * _amount(it, proto, 0, params, n) if m_lo else np.zeros(n)
        hi = m_hi * _amount(it, proto, 1, params, n) if m_hi else np.zeros(n)
        raw[iid] = LinePrice(iid, lo, hi, credited=bool(it.get("credited_to")))
    lines = []
    for iid, line in raw.items():
        targets = schedule.items[iid].get("credited_to") or []
        if targets:
            # An application fee applied towards the final permit: counted once.
            lo_t = sum((raw[t].low for t in targets if t in raw), np.zeros(n))
            hi_t = sum((raw[t].high for t in targets if t in raw), np.zeros(n))
            line = LinePrice(
                iid,
                np.maximum(line.low - lo_t, 0.0),
                np.maximum(line.high - hi_t, 0.0),
                credited=True,
            )
        lines.append(line)
    low = sum((ln.low for ln in lines), np.zeros(n))
    high = sum((ln.high for ln in lines), np.zeros(n))
    unknown = tuple(
        sorted(
            iid
            for iid, it in schedule.items.items()
            if it["city"] == city and str(it["status"]).startswith("unknown")
        )
    )
    return PrototypePrice(
        city=city,
        prototype=prototype,
        units=float(proto["units"]),
        lines=tuple(lines),
        shown=tuple(raw.values()),
        low=low,
        high=high,
        unknown=unknown,
        complete=city_status(schedule, city).complete,
    )


@dataclass(frozen=True)
class CityStatus:
    """Whether a city's documented schedule may replace its county's judgement fee.

    Attributes:
        city: City id.
        county: County FIPS code.
        complete: Every core category is documented or not applicable.
        applied: Complete and the city covers the whole county.
        gaps: Core categories that are unknown.
        secondary_gaps: Secondary categories that are unknown (the total is a floor for them).
        reason: Plain words for the record.
    """

    city: str
    county: str
    complete: bool
    applied: bool
    gaps: tuple[str, ...]
    secondary_gaps: tuple[str, ...]
    reason: str


def city_status(schedule: Schedule, city: str) -> CityStatus:
    """Decide whether a city's schedule is complete and applied in the variant."""
    c = schedule.cities[city]
    review = c.get("review") or {}
    gaps = tuple(k for k in schedule.core if review.get(k) not in KNOWN)
    sec = tuple(k for k in schedule.secondary if review.get(k, "unknown") not in KNOWN)
    whole = bool(c.get("whole_county"))
    complete = not gaps
    if complete and whole:
        reason = f"{c.get('name', city)}: every core fee category documented; covers the county"
    elif not complete:
        reason = f"{c.get('name', city)}: not applied, core categories unknown: {', '.join(gaps)}"
    else:
        reason = f"{c.get('name', city)}: not applied, the city is only part of its county"
    return CityStatus(city, str(c["county"]), complete, complete and whole, gaps, sec, reason)


def prototype_draws(
    schedule: Schedule, city: str, prototype: str, params: Mapping[str, np.ndarray]
) -> np.ndarray:
    """Documented fee per home by draw, at each draw's schedule position."""
    price = price_prototype(schedule, city, prototype, params)
    return price.per_unit(np.asarray(params[POSITION_ID], dtype=float))


def county_fee_matrix(
    schedule: Schedule,
    counties: Sequence[str],
    structure_types: Sequence[str],
    params: Mapping[str, np.ndarray],
    fallback: np.ndarray,
) -> tuple[np.ndarray, dict[str, CityStatus]]:
    """Fees per home before any cut, by county, structure type and draw.

    A county takes its city's documented fee (prototype per structure type,
    at the draw's schedule position) when the city is applied
    (:func:`city_status`); every other county takes ``fallback`` (the
    labelled judgement), the same in every structure type.

    Args:
        schedule: The parsed schedule.
        counties: County FIPS codes in array order.
        structure_types: Structure types in array order.
        params: Ledger id to draws (see :func:`local_ids`).
        fallback: Judgement fee per home by draw, shape (draws,).

    Returns:
        ``(fees of shape (counties, types, draws), city id -> status)``.
    """
    fb = np.asarray(fallback, dtype=float)
    out = np.broadcast_to(fb, (len(counties), len(structure_types), fb.size)).copy()
    status = {c: city_status(schedule, c) for c in schedule.cities}
    index = {str(c): i for i, c in enumerate(counties)}
    for city, st in status.items():
        if not st.applied or st.county not in index:
            continue
        i = index[st.county]
        for j, t in enumerate(structure_types):
            proto = schedule.type_prototypes[t]
            out[i, j, :] = prototype_draws(schedule, city, proto, params)
    return out, status


# ---------------------------------------------------------------- summary


def _end_params(schedule: Schedule, ledger: Any, end: str) -> dict[str, np.ndarray]:
    """Ledger low, central or high for every local id; position 0, 0.5 or 1."""
    params = {i: np.array([float(getattr(ledger[i], end))]) for i in local_ids(schedule)}
    params[POSITION_ID] = np.array([{"low": 0.0, "central": 0.5, "high": 1.0}[end]])
    return params


def summary_rows(schedule: Schedule, ledger: Any) -> list[dict[str, Any]]:
    """Documented fee per home by city and prototype at the low, central and high ends.

    Low end: every item at its low reading and the ledger's low construction
    cost and electrical share; high end: the high readings and high ledger
    values; central: halfway position and central ledger values. Also the
    central amount by fee category, so tap and connection charges are shown
    on their own.

    Returns:
        One row per (city, prototype): ``city``, ``prototype``, ``label``,
        ``units``, ``low``, ``central``, ``high`` ($ per home), ``complete``,
        ``applied``, ``unknown`` (item ids) and ``by_category`` ($ per home,
        central).
    """
    rows = []
    ends = {e: _end_params(schedule, ledger, e) for e in ("low", "central", "high")}
    for city in schedule.cities:
        st = city_status(schedule, city)
        for proto in schedule.prototypes:
            vals = {e: float(prototype_draws(schedule, city, proto, p)[0]) for e, p in ends.items()}
            price = price_prototype(schedule, city, proto, ends["central"])
            cats: dict[str, float] = {}
            for ln in price.lines:
                cat = str(schedule.items[ln.item]["category"])
                cats[cat] = cats.get(cat, 0.0) + float((ln.low[0] + ln.high[0]) / 2 / price.units)
            rows.append(
                {
                    "city": city,
                    "prototype": proto,
                    "label": schedule.prototypes[proto].get("label", proto),
                    "units": price.units,
                    **vals,
                    "complete": st.complete,
                    "applied": st.applied,
                    "unknown": price.unknown,
                    "by_category": cats,
                }
            )
    return rows


# ---------------------------------------------------------------- checking the documents

_PLAIN = str.maketrans({0x201C: '"', 0x201D: '"', 0x2033: '"', 0x2018: "'", 0x2019: "'"})
"""Curly quote marks and the inch sign read as plain quote marks."""


def normalise(text: str) -> str:
    """Text for quote matching: no white space, plain quote marks, lower case."""
    t = text.translate(_PLAIN)
    return re.sub(r"\s+", "", t).lower()


def html_text(raw: str) -> str:
    """Visible text of a web page (scripts and styles removed, tags dropped)."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t)


def verify_text(schedule: Schedule, doc_id: str, text: str) -> list[str]:
    """Quotes of a document's items that are not in ``text`` (empty list = all found)."""
    body = normalise(text)
    missing = []
    for iid, it in schedule.items.items():
        if it.get("doc") != doc_id:
            continue
        for key in ("quote", "table_quote"):
            q = it.get(key)
            if q and normalise(str(q)) not in body:
                missing.append(f"{iid}.{key}")
    return missing


def _document_text(kind: str, path: Path) -> str:
    if kind == "pdf":
        from visionhousing.law.text import pdf_text

        return pdf_text(path).text
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if kind == "legistar_text":
        import json

        return str(json.loads(raw).get("MatterTextPlain") or "")
    return html_text(raw)


def check_documents(
    schedule: Schedule,
    raw_dir: Path,
    fetch: Callable[[str, Path], None] | None = None,
) -> dict[str, list[str]]:
    """Fetch every document with quotes and report what no longer matches.

    Args:
        schedule: The parsed schedule.
        raw_dir: Folder for the downloads (``data/raw/fee_schedules``).
        fetch: ``(url, destination) -> None``; defaults to the project's HTTP client.

    Returns:
        Document id to its problems (missing quotes, changed checksum).
    """
    if fetch is None:
        from visionhousing.data.http import HttpClient

        client = HttpClient()

        def fetch(url: str, dest: Path) -> None:
            dest.write_bytes(client.get_bytes(url))

    raw_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, list[str]] = {}
    for doc_id, doc in schedule.documents.items():
        if not any(it.get("doc") == doc_id and it.get("quote") for it in schedule.items.values()):
            continue
        kind = str(doc.get("kind", "html"))
        ext = {"pdf": ".pdf", "legistar_text": ".json"}.get(kind, ".html")
        dest = raw_dir / f"{doc_id}{ext}"
        fetch(str(doc["url"]), dest)
        problems = verify_text(schedule, doc_id, _document_text(kind, dest))
        if kind == "pdf" and doc.get("sha256"):
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if digest != doc["sha256"]:
                problems.append(f"sha256 changed: {digest}")
        report[doc_id] = problems
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Command line: ``--check`` re-fetches the documents; ``--price`` prints the prototypes."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fetch the documents and check quotes")
    ap.add_argument("--raw", type=Path, default=Path("data/raw/fee_schedules"))
    ap.add_argument("--price", action="store_true", help="print prototype prices at ledger ends")
    args = ap.parse_args(argv)
    sched = load_schedule()
    code = 0
    if args.check:
        for doc_id, problems in check_documents(sched, args.raw).items():
            print(f"{doc_id}: {'ok' if not problems else '; '.join(problems)}")
            code = code or (1 if problems else 0)
    if args.price:
        from visionhousing.ledger import load_ledger

        for row in summary_rows(sched, load_ledger()):
            flag = "" if row["complete"] else "  (incomplete: documented part only)"
            print(
                f"{row['city']:13s} {row['prototype']:14s} ${row['low']:,.0f} - "
                f"${row['high']:,.0f} (central ${row['central']:,.0f}) per home{flag}"
            )
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
