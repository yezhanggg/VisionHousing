"""Places like this one: group counties and tracts by k-means, name the groups by rule.

Plain-language summary
----------------------
Two places can be compared fairly only when they are alike. This module sorts
the state's counties (and its tracts) into a handful of groups of similar
places and, for every place, finds the few places most like it.

1. **Features.** Each place is described by a few numbers already in the
   pack (population, growth, density, rent, home value, permits per 1,000
   homes, share of homes near frequent transit, renters, empty homes, older
   residents, jobs per resident, share of homes in Qualified Census Tracts
   ...). The list, and whether a number is first logged, is in
   ``assumptions/typology.yaml``; the formulas are in :data:`FORMULAS`.
2. **z-scores.** Each feature is turned into a z-score: how many standard
   deviations the place is above or below the state's average. So every
   feature weighs the same, whatever its unit. A missing value is set to the
   state median of that feature (so it does not pull the place either way);
   ``imputed_features`` counts them.
3. **k-means** (Lloyd's algorithm). Start from k seed places (chosen by the
   "k-means++" rule: each new seed is picked with probability growing with
   its distance from the seeds already chosen), then repeat two steps until
   nothing moves: put every place in the group whose centre is nearest, and
   move every centre to the average of its places. It is run ``n_init``
   times from different seeds and the tightest result (smallest
   within-group sum of squares) is kept. The random generator has a fixed
   seed, so the same pack always gives the same groups.
4. **Choosing k (the elbow rule).** For every k in ``k_range`` the
   within-group sum of squares W(k) is found. The chosen k is the smallest
   one for which going to k + 1 groups cuts W by less than ``min_gain``
   of the total sum of squares W(1) (the spread with every place in one
   group): one more group would explain little more of the differences
   between places.
5. **Names by rule.** A group's name is built from its centre, never typed:
   a settlement word from its typical housing density ("Big-city",
   "Suburban", "Rural" ...), a growth word from its growth features
   ("growing", "steady", "slow-growth") and, when clearly different from
   the state, a cost word ("higher-cost", "lower-cost"). Two groups with the
   same name each add the phrase of their most unusual remaining feature.
6. **Similar places.** The ``n_similar`` places nearest in the same z-score
   space (whatever their group).

Groups are numbered from the densest (1) to the least dense.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

__all__ = [
    "ASSUMPTIONS",
    "COLUMNS",
    "FORMULAS",
    "TABLES",
    "KMeansResult",
    "choose_k",
    "county_features",
    "describe",
    "kmeans",
    "load_rules",
    "name_groups",
    "nearest",
    "tract_features",
    "traits",
    "typology",
    "typology_tables",
    "zscores",
]

ROOT = Path(__file__).resolve().parents[3]
ASSUMPTIONS = ROOT / "assumptions" / "typology.yaml"
LEVELS = ("county", "tract")

FORMULAS: dict[str, tuple[str, str]] = {
    "population": ("persons", "Residents (ACS 2020-2024)"),
    "population_change": ("share", "ACS 2020-2024 residents / 2020 Census residents - 1"),
    "housing_density": ("units per km2", "Homes per square kilometre of land"),
    "median_rent": ("dollars per month", "Median gross rent (counties: mean of tract medians "
                    "weighted by renter households)"),
    "median_value": ("dollars", "Median home value (counties: mean of tract medians weighted "
                     "by owner households)"),
    "median_income": ("dollars", "Median household income"),
    "permits_per_1000": ("per 1,000 homes", "Average yearly homes permitted over the last "
                         "permit_years years / homes x 1,000"),
    "new_homes_share": ("share", "Homes built 2010 or later / all homes (ACS)"),
    "transit_share": ("share", "2020 homes within the major-transit-stop distance / 2020 homes"),
    "renter_share": ("share", "Renter households / occupied homes"),
    "vacancy_rate": ("share", "Vacant homes / all homes"),
    "share_65plus": ("share", "Residents aged 65 or older / residents"),
    "jobs_per_resident": ("jobs per person", "Jobs located in the place (LODES) / residents "
                          "(ACS)"),
    "qct_share": ("share", "Homes in HUD Qualified Census Tracts / all homes"),
}  # fmt: skip
"""Feature -> (unit, formula in words)."""

_SOURCES = [
    "acs5_summary_file",
    "bps_county_annual",
    "tiger_2020_tabblock",
    "ntm_stops",
    "lodes_wac",
    "hud_qct",
]
TABLES: dict[str, dict[str, object]] = {
    f"typology_{level}": {
        "key": ["geoid"],
        "sources": _SOURCES,
        "description": (
            f"Places like this one: each {level}'s group from k-means on z-scored features "
            "(assumptions/typology.yaml), the group's rule-made name and the nearest "
            f"{level}s in the same feature space."
        ),
    }
    for level in LEVELS
}
"""The two pack tables: key, sources and description."""

COLUMNS: dict[str, tuple[str, str]] = {
    "geoid": ("code", "County (5 digits) or tract (11 digits) code"),
    "county": ("code", "County code"),
    "type_id": ("group", "Group number, 1 = densest"),
    "type_name": ("text", "Group name, made by the naming rules in assumptions/typology.yaml"),
    "k": ("groups", "Number of groups (chosen by the elbow rule)"),
    "places_in_type": ("places", "Places in this group"),
    "distance": ("z-score units", "Distance from the group's centre"),
    "similar": ("codes", "The nearest places in feature space, nearest first, ';'-separated"),
    "imputed_features": ("features", "Features missing for this place and set to the median"),
    "features": ("text", "Features used, ';'-separated"),
}
"""Shared column descriptions; feature columns are described from :data:`FORMULAS`."""


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and description of a typology table's column (for the data dictionary)."""
    if name in COLUMNS:
        return COLUMNS[name]
    if name in FORMULAS:
        unit, text = FORMULAS[name]
        return unit, f"Feature (before z-scoring): {text}"
    raise ValueError(f"no data-dictionary entry for {table}.{name}")


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/typology.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- features


def _num(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce")


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.where(den > 0)


def _weighted(tracts: pd.DataFrame, value: str, weight: Sequence[str]) -> pd.Series:
    v = _num(tracts, value)
    w = sum(_num(tracts, c).fillna(0) for c in weight)
    ok = v.notna() & (w > 0)
    num = (v * w).where(ok).groupby(tracts["county"]).sum(min_count=1)
    den = w.where(ok).groupby(tracts["county"]).sum(min_count=1)
    return _ratio(num, den)


def _density(frame: pd.DataFrame) -> pd.Series:
    if "housing_density_per_km2" in frame.columns:
        return _num(frame, "housing_density_per_km2")
    return _ratio(_num(frame, "housing_units"), _num(frame, "land_area_m2") / 1e6)


def _jobs_per_resident(
    jobs: pd.DataFrame | None, key: str, index: pd.Index, population: pd.Series
) -> pd.Series:
    """Jobs located in each place / its residents; missing when the pack has no jobs table."""
    if jobs is None or not len(jobs) or "jobs" not in jobs.columns:
        return pd.Series(np.nan, index=index)
    count = _num(jobs, "jobs").groupby(jobs[key].astype(str)).sum(min_count=1)
    return _ratio(count.reindex(index), population)


def county_features(
    counties: pd.DataFrame,
    tracts: pd.DataFrame,
    permits: pd.DataFrame | None,
    permit_years: int,
    jobs: pd.DataFrame | None = None,
    incentives: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Every county feature of :data:`FORMULAS` that the pack allows, indexed by county.

    ``jobs`` is the ``jobs_county`` table and ``incentives`` the ``incentives_tract``
    table; without them ``jobs_per_resident`` and ``qct_share`` are missing (and so
    dropped from the grouping).
    """
    c = counties.assign(geoid=counties["geoid"].astype(str)).set_index("geoid")
    t = tracts.assign(county=tracts["county"].astype(str))
    sums = t.assign(
        **{k: _num(t, k) for k in ("households_renter", "households_owner", "vacant_units",
                                   "housing_units", "transit_units_d07", "housing_units_2020")}
    ).groupby("county")[["households_renter", "households_owner", "vacant_units",
                         "housing_units", "transit_units_d07", "housing_units_2020"]
                        ].sum(min_count=1).reindex(c.index)  # fmt: skip
    out = pd.DataFrame(index=c.index)
    out["population"] = _num(c, "population")
    out["population_change"] = _ratio(_num(c, "population"), _num(c, "population_2020")) - 1
    out["housing_density"] = _density(c)
    if out["housing_density"].isna().all():  # no county land area: add up the tracts'
        land = _num(t, "land_area_m2").groupby(t["county"]).sum(min_count=1) / 1e6
        out["housing_density"] = _ratio(sums["housing_units"], land.reindex(c.index))
    out["median_rent"] = _weighted(t, "median_gross_rent", ["households_renter"]).reindex(c.index)
    out["median_value"] = _weighted(t, "median_home_value", ["households_owner"]).reindex(c.index)
    if permits is not None and len(permits):
        p = permits.assign(county=permits["county"].astype(str))
        years = pd.to_numeric(p["year"], errors="coerce")
        recent = p[years > years.max() - int(permit_years)]
        units = sum(_num(recent, k).fillna(0) for k in ("units_1", "units_2", "units_3_4",
                                                         "units_5plus"))  # fmt: skip
        yearly = units.groupby(recent["county"]).sum() / int(permit_years)
        out["permits_per_1000"] = _ratio(yearly.reindex(c.index), _num(c, "housing_units")) * 1000
    out["transit_share"] = _ratio(sums["transit_units_d07"], sums["housing_units_2020"])
    occupied = sums["households_renter"] + sums["households_owner"]
    out["renter_share"] = _ratio(sums["households_renter"], occupied)
    out["vacancy_rate"] = _ratio(sums["vacant_units"], sums["housing_units"])
    out["share_65plus"] = _weighted(t, "share_age_65plus", ["population"]).reindex(c.index)
    out["jobs_per_resident"] = _jobs_per_resident(jobs, "county", c.index, out["population"])
    if incentives is not None and len(incentives) and "qct" in incentives.columns:
        flag = _num(incentives, "qct").groupby(incentives["tract"].astype(str)).max()
        homes = _num(t, "housing_units")
        in_qct = homes.where(t["geoid"].astype(str).map(flag).fillna(0) > 0, 0)
        qct_homes = in_qct.groupby(t["county"]).sum(min_count=1).reindex(c.index)
        out["qct_share"] = _ratio(qct_homes, sums["housing_units"])
    else:
        out["qct_share"] = np.nan
    return out


def tract_features(tracts: pd.DataFrame, jobs: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every tract feature of :data:`FORMULAS` that the pack allows, indexed by tract.

    ``jobs`` is the ``jobs_tract`` table; without it ``jobs_per_resident`` is missing.
    """
    t = tracts.assign(geoid=tracts["geoid"].astype(str)).set_index("geoid")
    units = _num(t, "housing_units")
    out = pd.DataFrame(index=t.index)
    out["housing_density"] = _density(t)
    out["median_rent"] = _num(t, "median_gross_rent")
    out["median_value"] = _num(t, "median_home_value")
    out["median_income"] = _num(t, "median_household_income")
    new = _num(t, "built_2010_2019").fillna(0) + _num(t, "built_2020_plus").fillna(0)
    has_built = t.columns.intersection(["built_2010_2019", "built_2020_plus"]).size > 0
    out["new_homes_share"] = _ratio(new, units) if has_built else np.nan
    if "transit_units_share_d07" in t.columns:
        out["transit_share"] = _num(t, "transit_units_share_d07")
    else:
        out["transit_share"] = _ratio(_num(t, "transit_units_d07"), _num(t, "housing_units_2020"))
    occupied = _num(t, "households_renter") + _num(t, "households_owner")
    out["renter_share"] = _ratio(_num(t, "households_renter"), occupied)
    out["vacancy_rate"] = _ratio(_num(t, "vacant_units"), units)
    out["share_65plus"] = _num(t, "share_age_65plus")
    out["jobs_per_resident"] = _jobs_per_resident(jobs, "tract", t.index, _num(t, "population"))
    return out


# --------------------------------------------------------------------------- k-means


@dataclass(frozen=True)
class KMeansResult:
    """One k-means fit: group of each row (0-based), centres, within-group sum of squares."""

    labels: np.ndarray
    centres: np.ndarray
    inertia: float


def _sq_dist(x: np.ndarray, centres: np.ndarray) -> np.ndarray:
    return ((x[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)


def _plus_plus(x: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ seeds: the first at random, each next one with chance ~ squared distance."""
    centres = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        d = _sq_dist(x, np.array(centres)).min(axis=1)
        total = d.sum()
        pick = rng.integers(len(x)) if total <= 0 else rng.choice(len(x), p=d / total)
        centres.append(x[pick])
    return np.array(centres, dtype=float)


def kmeans(
    x: np.ndarray, k: int, *, seed: int, n_init: int = 10, max_iter: int = 300
) -> KMeansResult:
    """Lloyd's k-means, best of ``n_init`` k-means++ starts, with a fixed seed.

    Args:
        x: Rows to group (already z-scored), shape ``(n, p)``.
        k: Number of groups (``1 <= k <= n``).
        seed: Random seed; the same seed always gives the same answer.
        n_init: Starts tried; the smallest within-group sum of squares wins.
        max_iter: Most iterations per start.
    """
    x = np.asarray(x, dtype=float)
    if not 1 <= k <= len(x):
        raise ValueError(f"k must be between 1 and {len(x)}, got {k}")
    rng = np.random.default_rng([int(seed), int(k)])
    best: KMeansResult | None = None
    for _ in range(int(n_init)):
        centres = _plus_plus(x, k, rng)
        labels = np.zeros(len(x), dtype=int)
        for _step in range(int(max_iter)):
            d = _sq_dist(x, centres)
            labels = d.argmin(axis=1)
            new = centres.copy()
            for j in range(k):
                members = x[labels == j]
                if len(members):
                    new[j] = members.mean(axis=0)
                else:  # an empty group takes the row farthest from its centre
                    far = d[np.arange(len(x)), labels].argmax()
                    new[j] = x[far]
                    labels[far] = j
            if np.allclose(new, centres, rtol=0, atol=1e-10):
                centres = new
                break
            centres = new
        labels = _sq_dist(x, centres).argmin(axis=1)
        inertia = float(_sq_dist(x, centres)[np.arange(len(x)), labels].sum())
        if best is None or inertia < best.inertia - 1e-9:
            best = KMeansResult(labels, centres, inertia)
    if best is None:  # pragma: no cover - n_init is at least 1
        raise ValueError("n_init must be at least 1")
    return best


def choose_k(
    x: np.ndarray,
    k_range: Sequence[int],
    min_gain: float,
    *,
    seed: int,
    n_init: int,
    max_iter: int = 300,
) -> tuple[int, dict[int, KMeansResult]]:
    """The elbow rule: smallest k whose step to k + 1 cuts W by less than ``min_gain``.

    The cut is measured as a share of the total sum of squares W(1) (all places
    in one group), so it reads as "one more group explains less than
    ``min_gain`` more of the spread between places".

    ``k_range`` is ``[low, high]``; ``high`` is capped at the number of rows.

    Returns:
        The chosen k and the fit for every k tried.
    """
    low, high = int(k_range[0]), min(int(k_range[1]), len(x))
    if low > high:
        raise ValueError(f"too few places ({len(x)}) for {low} groups")
    fits = {k: kmeans(x, k, seed=seed, n_init=n_init, max_iter=max_iter)
            for k in range(low, high + 1)}  # fmt: skip
    total = float(((x - x.mean(axis=0)) ** 2).sum())  # W(1): everything in one group
    for k in range(low, high):
        w, w_next = fits[k].inertia, fits[k + 1].inertia
        if total <= 0 or (w - w_next) / total < float(min_gain):
            return k, fits
    return high, fits


def zscores(
    features: pd.DataFrame, transforms: Mapping[str, str]
) -> tuple[pd.DataFrame, pd.Series]:
    """Transform, fill missing values with the median, and z-score every feature.

    Returns:
        The z-scores (columns with no spread are dropped) and the count of
        filled values per row.
    """
    f = features.copy()
    for name, how in transforms.items():
        if how == "log" and name in f.columns:
            f[name] = np.log1p(f[name].clip(lower=0))
    filled = f.isna().sum(axis=1)
    f = f.fillna(f.median())
    sd = f.std(ddof=0)
    keep = sd[sd > 0].index
    return (f[keep] - f[keep].mean()) / sd[keep], filled


# --------------------------------------------------------------------------- naming


def _word(value: float, threshold: float, high: str, low: str, middle: str) -> str:
    if value >= threshold:
        return high
    if value <= -threshold:
        return low
    return middle


def name_groups(
    centres: pd.DataFrame, density: pd.Series, rules: Mapping[str, Any], level: str
) -> dict[int, str]:
    """Plain names for groups from their centres (z-scores) and typical density.

    Args:
        centres: One row per group id, one column per feature (z-scores).
        density: Typical homes per km2 of each group (geometric mean).
        rules: ``assumptions/typology.yaml``.
        level: ``county`` or ``tract`` (picks the growth features).
    """

    def mean_z(names: Sequence[str]) -> pd.Series:
        cols = [n for n in names if n in centres.columns]
        return centres[cols].mean(axis=1) if cols else pd.Series(0.0, index=centres.index)

    growth = mean_z(rules["growth_features"][level])
    cost = mean_z(rules["cost_features"])
    names: dict[int, str] = {}
    for g in centres.index:
        settle = next(
            word for floor, word in rules["settlement"][level] if density[g] >= float(floor)
        )
        parts = [settle, _word(growth[g], float(rules["growth_threshold"]), "growing",
                               "slow-growth", "steady")]  # fmt: skip
        price = _word(cost[g], float(rules["cost_threshold"]), "higher-cost", "lower-cost", "")
        if price:
            parts.append(price)
        names[g] = ", ".join(parts)
    used = set(rules["growth_features"][level]) | set(rules["cost_features"]) | {"housing_density"}
    rest = [c for c in centres.columns if c not in used and c in rules["phrases"]]
    for name in sorted(set(names.values())):
        same = [g for g in centres.index if names[g] == name]
        if len(same) < 2 or not rest:
            continue
        for g in same:
            row = centres.loc[g, rest]
            top = row.abs().idxmax()
            high, low = rules["phrases"][top]
            names[g] = f"{name}, {high if row[top] > 0 else low}"
    seen: dict[str, int] = {}
    for g in centres.index:
        seen[names[g]] = seen.get(names[g], 0) + 1
    for g in centres.index:
        if seen[names[g]] > 1:
            names[g] = f"{names[g]} (group {g})"
    return names


def traits(
    centre: pd.Series, rules: Mapping[str, Any], n: int = 3, floor: float = 0.5
) -> list[str]:
    """The phrases of a group's ``n`` most unusual features (|z| at least ``floor``)."""
    row = centre[[c for c in centre.index if c in rules["phrases"]]]
    order = row.abs().sort_values(ascending=False, kind="stable")
    out = []
    for name in order.index[:n]:
        if abs(row[name]) >= floor:
            high, low = rules["phrases"][name]
            out.append(high if row[name] > 0 else low)
    return out


# --------------------------------------------------------------------------- tables


def nearest(z: np.ndarray, n: int, chunk: int = 512) -> np.ndarray:
    """Indices of each row's ``n`` nearest other rows (Euclidean), nearest first."""
    n = min(n, len(z) - 1)
    out = np.zeros((len(z), max(n, 0)), dtype=int)
    if n <= 0:
        return out
    sq = (z**2).sum(axis=1)
    for start in range(0, len(z), chunk):
        part = z[start : start + chunk]
        d = (part**2).sum(axis=1)[:, None] + sq[None, :] - 2 * part @ z.T
        rows = np.arange(len(part))
        d[rows, start + rows] = np.inf
        idx = np.argsort(d, axis=1, kind="stable")[:, :n]
        out[start : start + len(part)] = idx
    return out


def typology(
    features: pd.DataFrame,
    specs: Sequence[Mapping[str, str]],
    rules: Mapping[str, Any],
    level: str,
) -> pd.DataFrame:
    """Group the places of ``features`` and find their nearest neighbours.

    Args:
        features: One row per place (index: geoid), one column per feature.
        specs: The ``county_features`` or ``tract_features`` list of the rules.
        rules: ``assumptions/typology.yaml``.
        level: ``county`` or ``tract``.
    """
    wanted = [s["name"] for s in specs]
    f = features[[c for c in wanted if c in features.columns]]
    # Nullable integer columns (a small state with withheld counts) arrive as object dtype.
    f = f.apply(pd.to_numeric, errors="coerce").astype(float)
    f = f.loc[:, f.notna().any()]
    transforms = {s["name"]: s.get("transform", "none") for s in specs if s["name"] in f.columns}
    z, filled = zscores(f, transforms)
    x = z.to_numpy()
    k, fits = choose_k(x, rules["k_range"], float(rules["min_gain"]), seed=int(rules["seed"]),
                       n_init=int(rules["n_init"]), max_iter=int(rules["max_iter"]))  # fmt: skip
    fit = fits[k]
    density = features["housing_density"].reindex(z.index).fillna(0).clip(lower=0)
    typical = pd.Series(np.log1p(density.to_numpy())).groupby(fit.labels).mean()
    order = sorted(range(k), key=lambda g: (-typical[g], g))
    renumber = {old: new + 1 for new, old in enumerate(order)}
    labels = np.array([renumber[g] for g in fit.labels])
    centres = pd.DataFrame(fit.centres, columns=z.columns).rename(index=renumber).sort_index()
    names = name_groups(centres, np.expm1(typical.rename(index=renumber)), rules, level)
    near = nearest(x, int(rules["n_similar"]))
    ids = z.index.to_numpy()
    out = pd.DataFrame({"geoid": ids})
    if level == "tract":
        out["county"] = [g[:5] for g in ids]
    out["type_id"] = labels
    out["type_name"] = [names[g] for g in labels]
    out["k"] = k
    out["places_in_type"] = pd.Series(labels).map(pd.Series(labels).value_counts()).to_numpy()
    out["distance"] = np.sqrt(((x - centres.loc[labels].to_numpy()) ** 2).sum(axis=1)).round(3)
    out["similar"] = [";".join(ids[row]) for row in near]
    out["imputed_features"] = filled.to_numpy()
    out["features"] = ";".join(z.columns)
    for name in f.columns:
        out[name] = f[name].to_numpy()
    return out.sort_values("geoid").reset_index(drop=True)


def typology_tables(
    tables: Mapping[str, pd.DataFrame], rules: Mapping[str, Any] | None = None
) -> dict[str, pd.DataFrame]:
    """``typology_county`` and ``typology_tract`` from the pack's tables.

    A level with too few places for the smallest k is left out.
    """
    rules = rules or load_rules()
    out: dict[str, pd.DataFrame] = {}
    low = int(rules["k_range"][0])
    tracts, counties = tables.get("tracts"), tables.get("counties")
    if tracts is None or counties is None:
        return out
    cf = county_features(counties, tracts, tables.get("permits_county_year"),
                         int(rules["permit_years"]), jobs=tables.get("jobs_county"),
                         incentives=tables.get("incentives_tract"))  # fmt: skip
    if len(cf) > low:
        out["typology_county"] = typology(cf, rules["county_features"], rules, "county")
    tf = tract_features(tracts, jobs=tables.get("jobs_tract"))
    pop = _num(tracts.set_index(tracts["geoid"].astype(str)), "population")
    tf = tf[pop.reindex(tf.index) >= float(rules["tract_min_population"])]
    if len(tf) > low:
        out["typology_tract"] = typology(tf, rules["tract_features"], rules, "tract")
    return out
