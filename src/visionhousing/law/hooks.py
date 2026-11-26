"""Hooks: how verified municipal rules make reform assumptions vary by county.

Plain-language summary
----------------------
The ADU reform uses one statewide range for the share of lots where an ADU is
already legal (``adu.already_legal_share``). Where enough municipal rules have
been read and verified, that share differs by county instead. The same rule
decides for all three hooks: a county uses its own value only when its
verified rows cover at least ``min_coverage`` of its residents
(``assumptions/law/hooks.yaml``). Which hooks feed the engine is set by
``engine_wired`` and ``wired`` in that file: the ADU and parking hooks are on;
the fee hook is off (no reading answers the impact-fee question yet).

- :func:`county_adu_legal_share`: per county, weighting municipalities by
  residents. Read residents count as legal when their rule is ``by_right``;
  unread residents take the statewide range. So

  ``share = (by-right residents + unread residents x statewide share) / residents``

  for each of low, central and high. A county whose verified rows cover less
  than ``min_coverage`` of its residents keeps the statewide range unchanged
  (``source`` says ``ledger``).
- :func:`county_adu_coefficients`: the same formula written as
  ``share = a + b x statewide share`` (``a`` = by-right residents / residents,
  ``b`` = unread residents / residents), for the qualifying counties only. The
  engine applies it to every Monte Carlo draw of the statewide share, so the
  statewide knob still moves every county and the range stays a range.
- :func:`county_parking_reach`: the share of read residents living where a
  parking minimum above zero applies. Parking savings make sense only there.
- :func:`county_parking_factor`: for a qualifying county, the share of its
  residents where the parking reform can save anything:
  ``1 - residents read with a minimum of zero / residents`` (unread residents,
  and read ones with no parking answer, keep the statewide assumption that a
  minimum applies). The engine scales the county's parking estimate by it.
- :func:`county_fee_reach`: the share of read residents living where a
  transportation impact fee ordinance exists. Not wired: no answers yet.
- :func:`local_law_for_engine`: all of the above for the engine, with the
  citations of the readings behind each county's value, coverage three ways
  (residents, single-family lots, residential land;
  :func:`visionhousing.law.coverage.county_coverage`) and, for every county,
  the ADU legal-status categories and the benchmark's exposure
  (:func:`visionhousing.law.coverage.county_adu_categories`).

The switch rule stays on residents (50%); the lot-weighted coverage is shown
beside it in every explanation.

Only rows with ``status: verified`` count, and only once in force on the run's
as-of date (``as_of`` in ``assumptions/outcome.yaml``, or the ``as_of`` passed
in): a known ``effective_date`` must be on or before it; with no effective
date, the row counts only when its ``adopted_on`` date is known and on or
before it; a known ``in_force_by`` on or before it (the latest dated enactment
inside the cited section, kept where no adoption date is printed) is enough on
its own (:func:`~visionhousing.law.inforce.in_force`, shared with the coverage
and category tables so they always agree; the rule is stated in ``hooks.yaml``). For the ADU
hook a row must also answer the ADU question: a blank or ``unknown``
``adu_status`` counts as unread (its residents take the statewide share), not
as "not legal". Unknown is never treated as "no".

A ``by_right`` row limited to relatives (``occupancy_restriction:
relatives_only``) still counts in ``a`` of the resident-weighted rule, like any
other by-right row. In the lot-weighted ADU categories, which the engine uses
instead wherever they exist, it is "by right with conditions", so only the
ledger share ``adu.conditions_exposure_share`` of its lots stays exposed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.law.coverage import CATEGORIES, county_adu_categories, county_coverage
from visionhousing.law.inforce import in_force, last_day, read_flags, resolve_as_of, run_as_of

__all__ = [
    "HOOKS_PATH",
    "LocalLaw",
    "county_adu_coefficients",
    "county_adu_legal_share",
    "county_fee_reach",
    "county_parking_factor",
    "county_parking_reach",
    "in_force",
    "last_day",
    "load_hooks",
    "local_law_for_engine",
    "run_as_of",
]

HOOKS_PATH = Path(__file__).resolve().parents[3] / "assumptions" / "law" / "hooks.yaml"


def load_hooks(path: Path = HOOKS_PATH) -> dict[str, Any]:
    """Read ``assumptions/law/hooks.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _joined(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    as_of: date | str | None = None,
    hooks: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Municipalities with their rule row, ``read`` (in force on ``as_of``) and ``adu_read``.

    ``read``: verified and in force on the run's as-of date
    (:func:`visionhousing.law.inforce.read_flags`, the same test the coverage
    tables use).
    ``adu_read``: read, and the row answers the ADU question (``adu_status``
    neither blank nor ``unknown``); a read row with no ADU answer counts as
    unread for the ADU hook, so it takes the statewide share.
    """
    m = munis.copy()
    m["geoid"] = m["county"].astype(str).str.zfill(5) + m["cousub"].astype(str).str.zfill(5)
    m["population"] = pd.to_numeric(m["population"], errors="coerce").fillna(0)
    r = rules.copy()
    r["geoid"] = r["geoid"].astype(str)
    out = m[["geoid", "population"]].merge(r, on="geoid", how="left")
    out["county"] = out["geoid"].str[:5]
    out["read"], out["adu_read"] = read_flags(out, as_of, hooks)
    return out


def county_adu_legal_share(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    statewide: tuple[float, float, float],
    hooks: dict[str, Any] | None = None,
    *,
    as_of: date | str | None = None,
) -> pd.DataFrame:
    """Already-legal ADU share per county: low, central, high, coverage and source.

    Args:
        rules: The ``muni_zoning_rules`` table.
        munis: The ``municipal_planning`` table (for residents).
        statewide: The statewide ``(low, central, high)`` range.
        hooks: ``assumptions/law/hooks.yaml`` (read when not given).
        as_of: The run's as-of date (default: ``assumptions/outcome.yaml``).

    Only rows that answer the ADU question count as read here (``adu_read``):
    a verified row with a blank or ``unknown`` ADU status counts as unread, so
    its residents take the statewide share.
    """
    hooks = hooks or load_hooks()
    legal = set(hooks["adu_legal_statuses"])
    j = _joined(rules, munis, as_of, hooks)
    rows = []
    for county, g in j.groupby("county"):
        total = g["population"].sum()
        read = g[g["adu_read"]]
        coverage = float(read["population"].sum() / total) if total > 0 else 0.0
        if coverage < float(hooks["min_coverage"]) or total <= 0:
            low, central, high = statewide
            source = "ledger"
        else:
            yes = read.loc[read["adu_status"].isin(legal), "population"].sum()
            unread = total - read["population"].sum()
            low, central, high = ((yes + unread * s) / total for s in statewide)
            source = "verified_rules"
        rows.append(
            {
                "county": county,
                "low": float(low),
                "central": float(central),
                "high": float(high),
                "coverage": coverage,
                "source": source,
            }
        )
    return pd.DataFrame(rows)


def _reach(rules: pd.DataFrame, munis: pd.DataFrame, test, as_of=None) -> pd.DataFrame:
    j = _joined(rules, munis, as_of)
    rows = []
    for county, g in j.groupby("county"):
        total = g["population"].sum()
        answers = [(test(r), float(r["population"])) for _, r in g[g["read"]].iterrows()]
        known = sum(p for a, p in answers if a is not None)
        hit = sum(p for a, p in answers if a)
        rows.append(
            {
                "county": county,
                "coverage": float(known / total) if total > 0 else 0.0,
                "share_of_read": float(hit / known) if known > 0 else None,
            }
        )
    return pd.DataFrame(rows)


def _num(v: Any) -> float | None:
    x = pd.to_numeric(v, errors="coerce")
    return None if pd.isna(x) else float(x)


def county_parking_reach(
    rules: pd.DataFrame, munis: pd.DataFrame, *, as_of: date | str | None = None
) -> pd.DataFrame:
    """Per county: coverage of verified parking rules and the share with a minimum above 0."""

    def test(r: pd.Series) -> bool | None:
        vals = [_num(r.get(c)) for c in ("parking_min_per_unit_sf", "parking_min_per_unit_mf")]
        vals = [v for v in vals if v is not None]
        return None if not vals else max(vals) > 0

    return _reach(rules, munis, test, as_of)


def county_fee_reach(
    rules: pd.DataFrame, munis: pd.DataFrame, *, as_of: date | str | None = None
) -> pd.DataFrame:
    """Per county: coverage of verified fee answers and the share with an impact fee ordinance."""

    def test(r: pd.Series) -> bool | None:
        v = _num(r.get("transport_impact_fee_ordinance"))
        return None if v is None else v > 0

    return _reach(rules, munis, test, as_of)


def _qualifying(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    hooks: dict[str, Any],
    as_of: date | str | None = None,
    flag: str = "read",
):
    """Yield ``(county, group, residents, read rows, coverage)`` for counties over the bar.

    ``flag`` is ``read`` (any verified rule in force) or ``adu_read`` (one that
    also answers the ADU question).
    """
    j = _joined(rules, munis, as_of, hooks)
    for county, g in j.groupby("county"):
        total = float(g["population"].sum())
        read = g[g[flag]]
        coverage = float(read["population"].sum() / total) if total > 0 else 0.0
        if total > 0 and coverage >= float(hooks["min_coverage"]):
            yield str(county), g, total, read, coverage


def county_adu_coefficients(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    hooks: dict[str, Any] | None = None,
    *,
    as_of: date | str | None = None,
) -> pd.DataFrame:
    """``share = a + b x statewide share`` for each county with enough verified rows.

    Returns:
        One row per qualifying county: ``county``, ``a`` (by-right residents /
        residents), ``b`` (unread residents / residents), ``coverage``,
        ``legal_residents`` and ``residents``. Other counties are not listed:
        they keep the statewide range. "Read" means in force on ``as_of`` and
        answering the ADU question; a verified row with a blank or ``unknown``
        ADU status is unread (in ``b``), never "not legal".
    """
    hooks = hooks or load_hooks()
    legal = set(hooks["adu_legal_statuses"])
    rows = []
    for county, _g, total, read, coverage in _qualifying(rules, munis, hooks, as_of, "adu_read"):
        yes = float(read.loc[read["adu_status"].isin(legal), "population"].sum())
        unread = total - float(read["population"].sum())
        rows.append(
            {
                "county": county,
                "a": yes / total,
                "b": unread / total,
                "coverage": coverage,
                "legal_residents": yes,
                "residents": total,
            }
        )
    return pd.DataFrame(
        rows, columns=["county", "a", "b", "coverage", "legal_residents", "residents"]
    )


def county_parking_factor(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    hooks: dict[str, Any] | None = None,
    *,
    as_of: date | str | None = None,
) -> pd.DataFrame:
    """Share of residents where a parking reform can save anything, per qualifying county.

    ``factor = 1 - residents read with a parking minimum of zero / residents``.
    Unread residents, and read ones with no parking answer, keep the statewide
    assumption (a minimum applies). Returns ``county``, ``factor``, ``coverage``.
    """
    hooks = hooks or load_hooks()
    rows = []
    for county, _g, total, read, coverage in _qualifying(rules, munis, hooks, as_of):
        zero = 0.0
        for _, r in read.iterrows():
            vals = [_num(r.get(c)) for c in ("parking_min_per_unit_sf", "parking_min_per_unit_mf")]
            vals = [v for v in vals if v is not None]
            if vals and max(vals) <= 0:
                zero += float(r["population"])
        rows.append({"county": county, "factor": 1.0 - zero / total, "coverage": coverage})
    return pd.DataFrame(rows, columns=["county", "factor", "coverage"])


def _citations(read: pd.DataFrame, legal: set[str], limit: int) -> list[str]:
    """Plain citations of the readings behind one county: the by-right ones first, then the rest.

    A county ordinance read once for many municipalities is cited once, with the
    number of municipalities it covers.
    """
    out: list[str] = []
    r = read.copy()
    r["legal"] = r["adu_status"].isin(legal)
    r = r.sort_values(["legal", "population"], ascending=[False, False])
    for doc, g in r.groupby("doc_id", sort=False):
        first = g.iloc[0]
        status = str(first["adu_status"]).replace("_", " ")
        where = str(first["name"]) if len(g) == 1 else f"{len(g)} municipalities"
        out.append(
            f"{where}: ADUs {status} ({doc}, {first['section_cite']}; read {first['reviewed_on']})"
        )
    return out[:limit]


@dataclass(frozen=True)
class LocalLaw:
    """What verified municipal rules change in the engine, for one state.

    Attributes:
        adu: :func:`county_adu_coefficients` (qualifying counties only).
        parking: :func:`county_parking_factor` (qualifying counties only).
        citations: County to plain citations of the readings behind its value.
        min_coverage: The coverage bar used.
        source_id: Registry id of the ordinance source, for records.
        coverage: :func:`~visionhousing.law.coverage.county_coverage`, every
            county (residents, lots and land read).
        categories: :func:`~visionhousing.law.coverage.county_adu_categories`,
            every county (lots per ADU category and the benchmark's exposure).
    """

    adu: pd.DataFrame = field(repr=False)
    parking: pd.DataFrame = field(repr=False)
    citations: dict[str, list[str]] = field(default_factory=dict)
    min_coverage: float = 0.5
    source_id: str = "pa_municipal_ordinances"
    coverage: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    categories: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    def lot_coverage(self, county: str) -> float | None:
        """Share of the county's single-family lots under a verified reading, or ``None``."""
        if self.coverage.empty:
            return None
        hit = self.coverage[self.coverage["county"] == county]
        if hit.empty:
            return None
        v = hit.iloc[0]["coverage_lots"]
        return None if v is None or pd.isna(v) else float(v)

    def adu_categories(self, county: str) -> dict[str, float | None]:
        """Category to share of the county's single-family lots (empty dict when unknown)."""
        if self.categories.empty:
            return {}
        hit = self.categories[self.categories["county"] == county]
        if hit.empty:
            return {}
        r = hit.iloc[0]
        return {
            c: (None if pd.isna(r[f"share_lots_{c}"]) else float(r[f"share_lots_{c}"]))
            for c in CATEGORIES
        }

    def _lots_words(self, county: str) -> str:
        lots = self.lot_coverage(county)
        return "" if lots is None else f" and {lots:.0%} of its single-family lots"

    @property
    def counties(self) -> list[str]:
        """Counties that use read local law, in code order."""
        return sorted(set(self.adu["county"]) | set(self.parking["county"]))

    def adu_coefficients(self) -> dict[str, tuple[float, float]]:
        """County to ``(a, b)`` of ``share = a + b x statewide share``."""
        return {str(r.county): (float(r.a), float(r.b)) for r in self.adu.itertuples()}

    def parking_factors(self) -> dict[str, float]:
        """County to the parking reach factor."""
        return {str(r.county): float(r.factor) for r in self.parking.itertuples()}

    def adu_note(self, county: str) -> str | None:
        """Plain sentence: the county's already-legal ADU share comes from read local law."""
        hit = self.adu[self.adu["county"] == county]
        if hit.empty:
            return None
        r = hit.iloc[0]
        cites = " | ".join(self.citations.get(county, []))
        return (
            f"already-legal ADU share from read local law: verified ordinances cover "
            f"{r.coverage:.0%} of residents{self._lots_words(county)} "
            f"({r.legal_residents:,.0f} of {r.residents:,.0f} live "
            f"where ADUs are allowed by right), so share = {r.a:.3f} + {r.b:.3f} x statewide "
            f"share (assumptions/law/hooks.yaml). Readings: {cites}"
        )

    def plain_words(self, county: str) -> str | None:
        """Plain sentences for the county panel and the place profile, or ``None``."""
        hit = self.adu[self.adu["county"] == county]
        if hit.empty:
            return None
        r = hit.iloc[0]
        legal = (
            f"{r.a:.0%} of residents live where an ADU is already allowed by right, so the "
            "reform adds nothing there"
            if r.legal_residents > 0
            else "none of the read municipalities allows an ADU by right today, so the reform "
            "reaches all of their lots"
        )
        text = (
            f"The ADU estimate here uses local zoning rules that were read and checked, not the "
            f"statewide guess: they cover {r.coverage:.0%} of residents"
            f"{self._lots_words(county)}, and {legal}. The other "
            f"{r.b:.0%} (rules not read) still use the statewide range."
        )
        park = self.parking[self.parking["county"] == county]
        if len(park):
            f = float(park.iloc[0]["factor"])
            text += (
                " Every read municipality requires parking, so the parking estimate is unchanged."
                if f >= 1.0
                else f" {1 - f:.0%} of residents live where no parking minimum applies, so the "
                "parking estimate is scaled down to the rest."
            )
        return text

    def parking_note(self, county: str) -> str | None:
        """Plain sentence on the parking reach of a qualifying county."""
        hit = self.parking[self.parking["county"] == county]
        if hit.empty:
            return None
        f = float(hit.iloc[0]["factor"])
        return (
            f"parking reach from read local law: {f:.0%} of residents live where a parking "
            "minimum above zero applies or the rule is not read (assumptions/law/hooks.yaml)"
        )


def local_law_for_engine(
    rules: pd.DataFrame | None,
    munis: pd.DataFrame | None,
    hooks: dict[str, Any] | None = None,
    *,
    citations_shown: int = 4,
    as_of: date | str | None = None,
) -> LocalLaw | None:
    """The engine's local-law adjustments, or ``None`` when off or when the tables are absent.

    Args:
        rules: The ``muni_zoning_rules`` table (``None`` when the pack has none).
        munis: The ``municipal_planning`` table.
        hooks: ``assumptions/law/hooks.yaml`` (read when not given).
        citations_shown: At most this many citations per county.
        as_of: The run's as-of date; a rule counts only if in force by then
            (default: ``as_of`` in ``assumptions/outcome.yaml``, the date every
            run is frozen at).
    """
    hooks = hooks or load_hooks()
    if not hooks.get("engine_wired") or rules is None or munis is None:
        return None
    wired = hooks.get("wired") or {}
    empty_adu = pd.DataFrame(
        columns=["county", "a", "b", "coverage", "legal_residents", "residents"]
    )
    when = resolve_as_of(as_of)
    adu = (
        county_adu_coefficients(rules, munis, hooks, as_of=when)
        if wired.get("adu", True)
        else empty_adu
    )
    parking = (
        county_parking_factor(rules, munis, hooks, as_of=when)
        if wired.get("parking", True)
        else pd.DataFrame(columns=["county", "factor", "coverage"])
    )
    legal = set(hooks["adu_legal_statuses"])
    cites = {
        county: _citations(read, legal, citations_shown)
        for county, _g, _t, read, _c in _qualifying(rules, munis, hooks, when)
    }
    return LocalLaw(
        adu=adu,
        parking=parking,
        citations=cites,
        min_coverage=float(hooks["min_coverage"]),
        source_id=str(hooks.get("source_id", "pa_municipal_ordinances")),
        coverage=county_coverage(rules, munis, hooks, as_of=when),
        categories=county_adu_categories(rules, munis, hooks, as_of=when),
    )
