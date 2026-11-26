"""How much of a county the verified rules cover, and the ADU legal-status categories.

Plain-language summary
----------------------
Counting municipalities read says little: one city can hold more homes than
thirty townships. This module measures coverage three ways and sorts every
municipality's ADU rule into five plain categories.

**Weights per municipality** (:func:`muni_weights`). Two weights are worked out
from tables the state pack already has, so nothing new is downloaded:

- ``sf_lots``: single-family lots, taken as the ACS count of one-unit detached
  homes in each tract (``tracts.units_1_detached``), split among the
  municipalities a tract touches in proportion to their 2020 housing units in
  that tract (``tract_municipalities``). One detached home stands for one lot
  that the ADU benchmark could reach.
- ``res_land_ha``: residential land. Each census block with homes (``blocks``)
  gives its land shared among its homes, but at most
  ``land_weight.max_m2_per_home`` per home (one acre, ``hooks.yaml``), so a
  big rural block with one farmhouse counts one acre, not its whole area.
  Blocks without homes count nothing. Summed per tract and split the same way
  as the lots. It is a proxy for the land the homes sit on, not a parcel map.

**Coverage three ways** (:func:`county_coverage`). For each county, the share of
its residents, of its single-family lots and of its residential land that
lives under a verified reading. The engine's switch rule is unchanged: a county
uses its own value only when verified rows cover at least ``min_coverage`` of
its *residents* (``assumptions/law/hooks.yaml``). The lot figure is shown beside
it in every explanation, because the ADU benchmark acts on lots, not people.

**ADU legal-status categories** (:func:`adu_category`, mapping in
``hooks.yaml`` under ``adu_categories``):

========================= ====================================================
category                  rows
========================= ====================================================
``already_permitted``     by right, detached allowed, no owner-occupancy rule,
                          no occupancy restriction: the benchmark adds nothing
``by_right_conditions``   by right, but only attached, or with an owner rule,
                          or for relatives only (``occupancy_restriction``),
                          or with conditions not read
``discretionary``         conditional use or special exception (a hearing)
``prohibited``            expressly prohibited, or not provided for as a use
``unknown``               not read, or read but not verified
========================= ====================================================

:func:`county_adu_categories` gives, per county, the lots and residents in each
category, and the benchmark's **exposure**: lots where it changes the status quo
(``by_right_conditions + discretionary + prohibited``), lots already
equivalent (``already_permitted``) and lots whose status is unknown.

Only rows with ``status: verified`` that are in force on the run's as-of date
count as read (``as_of`` in ``assumptions/outcome.yaml`` unless one is passed):
the same test the engine's hooks apply,
:func:`visionhousing.law.inforce.read_flags`, so the tables and the hooks always
agree. A known effective date must be on or before the as-of date (a partial
date counts from its last day); with no effective date the row counts only when
its adoption date is known and on or before it; a known ``in_force_by`` on or
before it is enough on its own. For the ADU categories a read
row must also answer the ADU question: a blank or ``unknown`` ``adu_status`` is
``unknown``. Unknown is never "no".
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from visionhousing.law.inforce import read_flags, resolve_as_of

__all__ = [
    "CATEGORIES",
    "CATEGORY_COLUMNS",
    "COVERAGE_COLUMNS",
    "adu_category",
    "county_adu_categories",
    "county_coverage",
    "muni_weights",
]

CATEGORIES = (
    "already_permitted",
    "by_right_conditions",
    "discretionary",
    "prohibited",
    "unknown",
)
"""The five ADU legal-status categories, from closest to the benchmark to unknown."""

CHANGED = ("by_right_conditions", "discretionary", "prohibited")
"""Categories where the ADU benchmark changes the status quo."""

COVERAGE_COLUMNS = [
    "county",
    "residents",
    "residents_read",
    "coverage_residents",
    "sf_lots",
    "sf_lots_read",
    "coverage_lots",
    "res_land_ha",
    "res_land_read_ha",
    "coverage_land",
    "qualifies",
]

CATEGORY_COLUMNS = [
    "county",
    "sf_lots",
    "residents",
    *[f"lots_{c}" for c in CATEGORIES],
    *[f"share_lots_{c}" for c in CATEGORIES],
    *[f"share_residents_{c}" for c in CATEGORIES],
    "lots_changed",
    "lots_equivalent",
    "share_lots_changed_of_read",
    "weight",
]


def muni_weights(
    tracts: pd.DataFrame,
    tract_munis: pd.DataFrame,
    blocks: pd.DataFrame | None = None,
    max_m2_per_home: float = 4046.86,
) -> pd.DataFrame:
    """Single-family lots and residential land per municipality (10-digit ``geoid``).

    Args:
        tracts: The pack's ``tracts`` table (``geoid``, ``units_1_detached``,
            ``land_area_m2``).
        tract_munis: ``tract_municipalities`` (``tract``, ``county``, ``cousub``,
            ``housing_units_2020``).
        blocks: The pack's ``blocks`` table (``tract``, ``housing_units``,
            ``land_area_m2``). Without it, a tract's land counts at most
            ``max_m2_per_home`` per one-unit detached home.
        max_m2_per_home: The most land one home's lot is taken to have
            (``land_weight.max_m2_per_home`` in ``assumptions/law/hooks.yaml``).

    Returns:
        ``geoid``, ``sf_lots`` and ``res_land_ha``. A tract with no 2020 homes in
        any municipality is split evenly among the municipalities it touches.
    """
    t = tracts[["geoid", "units_1_detached", "land_area_m2"]].copy()
    t["geoid"] = t["geoid"].astype(str)
    t["sf"] = pd.to_numeric(t["units_1_detached"], errors="coerce").fillna(0.0)
    land = pd.to_numeric(t["land_area_m2"], errors="coerce").fillna(0.0)
    t["land"] = land.clip(upper=t["sf"] * max_m2_per_home)
    if blocks is not None and len(blocks):
        b = blocks[["tract", "housing_units", "land_area_m2"]].copy()
        b["tract"] = b["tract"].astype(str)
        homes = pd.to_numeric(b["housing_units"], errors="coerce").fillna(0)
        area = pd.to_numeric(b["land_area_m2"], errors="coerce").fillna(0)
        res = area.clip(upper=homes * max_m2_per_home).groupby(b["tract"]).sum()
        t["land"] = t["geoid"].map(res).fillna(0.0)
    tm = tract_munis.copy()
    tm["tract"] = tm["tract"].astype(str)
    tm["geoid"] = tm["county"].astype(str).str.zfill(5) + tm["cousub"].astype(str).str.zfill(5)
    tm["hu"] = pd.to_numeric(tm["housing_units_2020"], errors="coerce").fillna(0.0)
    total = tm.groupby("tract")["hu"].transform("sum")
    count = tm.groupby("tract")["hu"].transform("size")
    tm["part"] = (tm["hu"] / total).where(total > 0, 1.0 / count)
    tm = tm.merge(t[["geoid", "sf", "land"]].rename(columns={"geoid": "tract"}), on="tract")
    tm["sf_lots"] = tm["part"] * tm["sf"]
    tm["res_land_ha"] = tm["part"] * tm["land"] / 10_000.0
    out = tm.groupby("geoid", as_index=False)[["sf_lots", "res_land_ha"]].sum()
    return out.sort_values("geoid").reset_index(drop=True)


def _weights(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    as_of: date | str | None = None,
    hooks: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Municipalities with residents, lots, land, ``read`` / ``adu_read`` flags and rule columns.

    ``read`` and ``adu_read`` come from :func:`visionhousing.law.inforce.read_flags`
    on ``as_of`` (the hooks' own test).
    """
    m = munis.copy()
    m["geoid"] = m["county"].astype(str).str.zfill(5) + m["cousub"].astype(str).str.zfill(5)
    m["residents"] = pd.to_numeric(m["population"], errors="coerce").fillna(0.0)
    r = rules.copy()
    r["geoid"] = r["geoid"].astype(str)
    keep = [
        c
        for c in (
            "geoid",
            "status",
            "adu_status",
            "adu_detached",
            "adu_owner_occupancy",
            "occupancy_restriction",
            "unlisted_uses",
            "adopted_on",
            "effective_date",
            "in_force_by",
            "reviewed_on",
            "sf_lots",
            "res_land_ha",
        )
        if c in r.columns
    ]
    out = m[["geoid", "residents"]].merge(r[keep], on="geoid", how="left")
    out["county"] = out["geoid"].str[:5]
    for c in ("sf_lots", "res_land_ha"):
        out[c] = pd.to_numeric(out[c], errors="coerce") if c in out.columns else float("nan")
    out["read"], out["adu_read"] = read_flags(out, as_of, hooks)
    return out


def _share(part: float, whole: float) -> float | None:
    return float(part / whole) if whole > 0 else None


def county_coverage(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    hooks: dict[str, Any] | None = None,
    *,
    as_of: date | str | None = None,
) -> pd.DataFrame:
    """Per county: the share of residents, single-family lots and residential land read.

    ``rules`` needs ``sf_lots`` and ``res_land_ha`` columns for the lot and land
    figures (the ``muni_zoning_rules`` table has them); without them those
    shares are empty. ``qualifies`` applies the engine's switch rule, on
    residents (``min_coverage`` of ``hooks``; 0.5 when not given). A row is read
    when verified and in force on ``as_of`` (default: the run's as-of date),
    whatever question it answers; the ADU categories add the ADU-answer test.
    """
    bar = float((hooks or {}).get("min_coverage", 0.5))
    w = _weights(rules, munis, as_of, hooks)
    rows = []
    for county, g in w.groupby("county"):
        read = g[g["read"]]
        res, res_r = float(g["residents"].sum()), float(read["residents"].sum())
        has_lots = g["sf_lots"].notna().any()
        lots = float(g["sf_lots"].sum()) if has_lots else float("nan")
        lots_r = float(read["sf_lots"].sum()) if has_lots else float("nan")
        has_land = g["res_land_ha"].notna().any()
        land = float(g["res_land_ha"].sum()) if has_land else float("nan")
        land_r = float(read["res_land_ha"].sum()) if has_land else float("nan")
        cov = _share(res_r, res) or 0.0
        rows.append(
            {
                "county": str(county),
                "residents": res,
                "residents_read": res_r,
                "coverage_residents": cov,
                "sf_lots": lots,
                "sf_lots_read": lots_r,
                "coverage_lots": _share(lots_r, lots) if has_lots else None,
                "res_land_ha": land,
                "res_land_read_ha": land_r,
                "coverage_land": _share(land_r, land) if has_land else None,
                "qualifies": bool(res > 0 and cov >= bar),
            }
        )
    return pd.DataFrame(rows, columns=COVERAGE_COLUMNS)


def _flag(v: Any) -> bool | None:
    """``True``/``False`` from a table cell (1/0, true/false), ``None`` when empty."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("", "nan", "none"):
            return None
        return s in ("1", "1.0", "true", "yes")
    return bool(v)


def _mapping(hooks: dict[str, Any] | None) -> dict[str, str]:
    return (hooks or {}).get("adu_categories") or {
        "conditional": "discretionary",
        "special_exception": "discretionary",
        "prohibited": "prohibited",
        "not_addressed": "unknown",
        "unknown": "unknown",
    }


def _unlisted(hooks: dict[str, Any] | None) -> dict[str, str]:
    """Category of a ``not_addressed`` row by its clause on unlisted uses (``hooks.yaml``)."""
    return (hooks or {}).get("not_addressed_by_unlisted_uses") or {
        "prohibited": "prohibited",
        "conditional": "discretionary",
        "special_exception": "discretionary",
    }


def _restrictions(hooks: dict[str, Any] | None) -> dict[str, str]:
    """Occupancy restriction to category (``occupancy_restrictions`` in ``hooks.yaml``)."""
    return (hooks or {}).get("occupancy_restrictions") or {"relatives_only": "by_right_conditions"}


def adu_category(
    row: pd.Series | dict[str, Any],
    hooks: dict[str, Any] | None = None,
    *,
    as_of: date | str | None = None,
) -> str:
    """The ADU legal-status category of one ``muni_zoning_rules`` row on ``as_of``.

    A row that is not read on ``as_of`` (not verified, or not yet in force by
    :func:`visionhousing.law.inforce.in_force`; default date: the run's as-of
    date) is ``unknown``, and so is a read row with a blank or ``unknown``
    ``adu_status``. A ``by_right`` row is
    ``already_permitted`` only when the reading says a detached ADU is allowed,
    the owner need not live on the lot and no occupancy restriction (such as
    relatives only) applies (the benchmark's terms); otherwise it is
    ``by_right_conditions``. Every other status maps through
    ``hooks['adu_categories']`` (``assumptions/law/hooks.yaml``).
    """
    mapping = _mapping(hooks)
    one = pd.DataFrame([dict(row)])
    _read, adu_read = read_flags(one, as_of, hooks)
    if not bool(adu_read.iloc[0]):
        return "unknown"
    return _category(row, mapping, _restrictions(hooks), _unlisted(hooks))


def _restricted(v: Any) -> bool:
    """True when an ``occupancy_restriction`` cell names a restriction (not blank)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    return str(v).strip().lower() not in ("", "nan", "none")


def _category(
    row: pd.Series | dict[str, Any],
    mapping: dict[str, str],
    restrictions: dict[str, str] | None = None,
    unlisted: dict[str, str] | None = None,
) -> str:
    """The category of a row already known to be read and to answer the ADU question.

    A ``by_right`` row with an occupancy restriction (``relatives_only``) goes
    to the category ``hooks['occupancy_restrictions']`` names for it
    (``by_right_conditions``; any restriction not listed goes there too): the
    benchmark lets any household live in the ADU.
    """
    get = row.get
    status = str(get("adu_status")).strip()
    if status == "by_right":
        restriction = get("occupancy_restriction")
        if _restricted(restriction):
            cat = (restrictions or {}).get(str(restriction).strip(), "by_right_conditions")
            if cat not in ("by_right_conditions", "discretionary", "prohibited"):
                raise ValueError(
                    f"occupancy_restrictions maps {restriction} to {cat}; a restricted by-right "
                    "rule cannot be already_permitted or unknown"
                )
            return cat
        detached = _flag(get("adu_detached"))
        owner = _flag(get("adu_owner_occupancy"))
        return "already_permitted" if detached and owner is False else "by_right_conditions"
    if status == "not_addressed":
        # Not addressed is not prohibited: only the ordinance's clause on
        # unlisted uses decides, and with no clause read it stays unknown.
        clause = str(get("unlisted_uses") or "").strip().lower()
        cat = (unlisted or _unlisted(None)).get(clause, mapping.get(status, "unknown"))
        if cat not in CATEGORIES:
            raise ValueError(f"not_addressed_by_unlisted_uses maps {clause} to {cat}")
        return cat
    cat = mapping.get(status, "unknown")
    if cat not in CATEGORIES:
        raise ValueError(f"adu_categories maps {status} to {cat}, not one of {CATEGORIES}")
    return cat


def county_adu_categories(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    hooks: dict[str, Any] | None = None,
    *,
    as_of: date | str | None = None,
) -> pd.DataFrame:
    """Per county: lots and residents in each ADU category, and the benchmark's exposure.

    Returns one row per county (columns :data:`CATEGORY_COLUMNS`). ``weight``
    says which weight the lot columns use: ``sf_lots`` when the rules table has
    lot weights, else ``residents`` (the lot columns then hold residents, so the
    shares are still meaningful). ``share_lots_changed_of_read`` is the share of
    the read lots where the benchmark changes the status quo (empty when
    nothing is read). A municipality counts in a known category only when its
    row is read on ``as_of`` and answers the ADU question (the ADU hook's own
    ``adu_read`` test); every other one is ``unknown``.
    """
    when = resolve_as_of(as_of)
    w = _weights(rules, munis, when, hooks)
    mapping = _mapping(hooks)
    restrictions = _restrictions(hooks)
    rules_by_geoid = rules.assign(geoid=rules["geoid"].astype(str)).set_index("geoid")
    cats = []
    for g, ok in zip(w["geoid"], w["adu_read"], strict=True):
        known = bool(ok) and g in rules_by_geoid.index
        cats.append(
            _category(rules_by_geoid.loc[g], mapping, restrictions, _unlisted(hooks))
            if known
            else "unknown"
        )
    w["category"] = cats
    lots_ok = w["sf_lots"].notna().any()
    w["lots"] = w["sf_lots"].fillna(0.0) if lots_ok else w["residents"]
    rows = []
    for county, g in w.groupby("county"):
        lots, res = float(g["lots"].sum()), float(g["residents"].sum())
        row: dict[str, Any] = {"county": str(county), "sf_lots": lots, "residents": res}
        by_lot = g.groupby("category")["lots"].sum()
        by_res = g.groupby("category")["residents"].sum()
        for c in CATEGORIES:
            row[f"lots_{c}"] = float(by_lot.get(c, 0.0))
        for c in CATEGORIES:
            row[f"share_lots_{c}"] = _share(row[f"lots_{c}"], lots)
        for c in CATEGORIES:
            row[f"share_residents_{c}"] = _share(float(by_res.get(c, 0.0)), res)
        changed = sum(row[f"lots_{c}"] for c in CHANGED)
        row["lots_changed"] = changed
        row["lots_equivalent"] = row["lots_already_permitted"]
        row["share_lots_changed_of_read"] = _share(changed, changed + row["lots_already_permitted"])
        row["weight"] = "sf_lots" if lots_ok else "residents"
        rows.append(row)
    return pd.DataFrame(rows, columns=CATEGORY_COLUMNS)
