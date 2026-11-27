"""Zoning-district answers for the ADU variants and the law-changes district view.

Plain-language summary
----------------------
The benchmark ADU reform takes one statewide range for the share of lots where
an ADU is already legal. For a small Pennsylvania sample (Philadelphia,
Pittsburgh, Frankstown Township) the pack also holds ``rule_area_weights``: how
the single-family lots of each tract split among zoning districts, and what the
benchmark changes in each (:mod:`visionhousing.law.rule_weights`). Each lot is
in one of five **opportunity classes**: known by right, by right with
conditions relaxed, newly eligible, discretionary (hearing) or unresolved.

This module turns that table into the already-legal share the ADU funnel needs,
tract by tract, for the named variants ``adu_zoning_districts`` (verified code
text only) and ``adu_zoning_districts_guidance`` (also the City's published
guidance, a labelled sensitivity). In a tract, per draw::

    legal = (1 - z) x base + sum over classes k of z_k x legal_k

where ``z_k`` is the share of the tract's reached lots that are single-family
detached lots in the sample and in class ``k`` (the table counts detached lots
only, so attached and 2-4 unit lots keep the base), ``z = sum of z_k`` and
``base`` is the benchmark's own already-legal share for that tract. ``legal_k`` comes from
``assumptions/law/zone_scope.yaml``: 1 for known by right, ``1 - c`` for
conditions relaxed (``c`` = ``adu.conditions_exposure_share``), 0 for newly
eligible and discretionary, and the statewide draw ``s``
(``adu.already_legal_share``) for unresolved. A class that rests on a zone row
whose review state the view does not count (guidance, in the verified variant)
is read as unresolved, so its lots take ``s``. Outside the sample nothing
changes; without the table every ``z`` is 0 and the variant equals the
benchmark draw for draw.

The table's tract rows carry lots by class but not the review state, so the
share of a class that rests on counted rows is taken from the jurisdiction's
zone rows (lots by class and review state) and applied to every tract of that
jurisdiction. Where a class is all counted or all not counted (every class in
today's table) this is exact; otherwise the run notes say it is spread evenly.

The same counted classes give the law-changes page an opt-in district view
(:func:`jurisdiction_mix`): each sample municipality's single answer is
replaced by its mix of district answers.

The module reads only the pack table and the settings file; scenario modules
never import the law layer, they receive the rule from the engine adapter.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record

__all__ = [
    "CLASSES",
    "SETTINGS_PATH",
    "DistrictRule",
    "TractClasses",
    "counted_fraction",
    "detached_share",
    "district_rule",
    "jurisdiction_mix",
    "load_settings",
    "read_table",
    "table_fingerprint",
    "tract_classes",
    "typed_table",
]

ROOT = Path(__file__).resolve().parents[3]
SETTINGS_PATH = ROOT / "assumptions" / "law" / "zone_scope.yaml"
CLASSES = ("known_by_right", "conditions_relaxed", "newly_eligible", "discretionary", "unresolved")
"""The opportunity classes of the table (:data:`visionhousing.law.zone_rules.CLASSES`)."""
LEGAL_WORDS = ("one", "one_minus_conditions", "zero", "statewide")
"""How a class's already-legal share is read (see ``already_legal`` in the settings)."""
U13_ANSWER = {
    "known_by_right": "already_allowed",
    "conditions_relaxed": "changed_condition",
    "discretionary": "changed_condition",
    "newly_eligible": "newly_allowed",
    "unresolved": "unresolved",
}
"""Opportunity class to the law-changes answer (the same map as
:data:`visionhousing.law.rule_weights.U13_ANSWER`; a test keeps them equal)."""
TABLE_KEY = ("pack_table", "rule_area_weights")
"""``StateContext.derived`` key: a table placed there (tests) is used as is."""
_TEXT = {"jurisdiction": str, "county": str, "tract": str}


def load_settings(path: Path | str = SETTINGS_PATH) -> dict[str, Any]:
    """``assumptions/law/zone_scope.yaml``, checked."""
    s = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    legal = s.get("already_legal") or {}
    if set(legal) != set(CLASSES):
        raise ValueError(f"zone_scope.yaml: already_legal must name every class {CLASSES}")
    bad = sorted(str(v) for v in legal.values() if v not in LEGAL_WORDS)
    if bad:
        raise ValueError(f"zone_scope.yaml: unknown already_legal readings {bad}")
    for opt, view in (s.get("legal_options") or {}).items():
        if view not in (s.get("views") or {}):
            raise ValueError(f"zone_scope.yaml: option {opt} names unknown view {view!r}")
    return s


def _pack_folder(state: Any) -> Path | None:
    held = (getattr(state, "derived", None) or {}).get(("pack_folder",))
    if held is not None and Path(held).is_dir():
        return Path(held)
    return None


def _table_path(folder: Path | None, name: str) -> Path | None:
    if folder is None:
        return None
    for cand in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
        if cand.is_file():
            return cand
    return None


def read_table(
    state: Any = None,
    *,
    folder: Path | str | None = None,
    settings: Mapping[str, Any] | None = None,
) -> pd.DataFrame | None:
    """The pack's ``rule_area_weights`` table, or ``None`` when the pack has none.

    Looks in ``state.derived[TABLE_KEY]`` first (tests), then the folder the
    state was read from (or ``folder``). Codes are read as text; lots as numbers.
    """
    settings = settings or load_settings()
    if state is not None:
        held = (getattr(state, "derived", None) or {}).get(TABLE_KEY)
        if held is not None:
            return typed_table(held)
    where = Path(folder) if folder is not None else _pack_folder(state)
    path = _table_path(where, str(settings["table"]))
    if path is None:
        return None
    return typed_table(pd.read_csv(path, dtype=_TEXT, low_memory=False))


def table_fingerprint(
    state: Any = None,
    *,
    folder: Path | str | None = None,
    settings: Mapping[str, Any] | None = None,
) -> str | None:
    """SHA-256 (first 12 hex digits) of the table file read, or ``None`` (none, or in memory)."""
    settings = settings or load_settings()
    where = Path(folder) if folder is not None else _pack_folder(state)
    path = _table_path(where, str(settings["table"]))
    if path is None:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def typed_table(t: pd.DataFrame) -> pd.DataFrame:
    """The table with codes as text and lots, homes and weights as numbers."""
    t = t.copy()
    for c in ("jurisdiction", "county", "tract"):
        if c in t.columns:
            t[c] = t[c].astype("string").fillna("").astype(str)
    for c in ("sf_lots", "housing_units", "weight"):
        if c in t.columns:
            t[c] = pd.to_numeric(t[c], errors="coerce").fillna(0.0)
    return t


def _zone_rows(table: pd.DataFrame, benchmark: str, period: str) -> pd.DataFrame:
    return table[
        (table["level"] == "zone") & (table["benchmark"] == benchmark) & (table["period"] == period)
    ]


def counted_fraction(
    table: pd.DataFrame, benchmark: str, review_states: Sequence[str], *, period: str = "window"
) -> pd.DataFrame:
    """Per jurisdiction and class: lots, counted lots and their ratio ``counted``.

    ``counted`` is the share of the class's lots (zone rows) whose review state
    is one of ``review_states``; the rest is read as unresolved.
    """
    z = _zone_rows(table, benchmark, period).copy()
    # An unresolved class stays unresolved whatever its row says, so it always counts.
    z["ok"] = z["review_state"].astype(str).isin(set(review_states)) | (
        z["opportunity_class"].astype(str) == "unresolved"
    )
    z["ok_lots"] = np.where(z["ok"], z["sf_lots"], 0.0)
    g = z.groupby(["jurisdiction", "opportunity_class"], as_index=False)[["sf_lots", "ok_lots"]]
    out = g.sum()
    out["counted"] = np.where(out["sf_lots"] > 0, out["ok_lots"] / out["sf_lots"].where(
        out["sf_lots"] > 0, 1.0), 0.0)  # fmt: skip
    return out


@dataclass(frozen=True)
class TractClasses:
    """Each tract's share of single-family lots in the sample, by counted class.

    Attributes:
        shares: Class to ``(tracts,)`` share of the tract's lots (ACS one-unit
            detached homes) in the sample and in that class; a class not counted
            by the view is already moved to ``unresolved``.
        inside: ``(tracts,)`` sum of the shares (share of the tract in the sample).
        sample_lots: Lots of the sample in the tracts given (from the table).
        lots_by_class: Class to sample lots after the view (moved lots in ``unresolved``).
        moved_lots: Lots moved to ``unresolved`` because their row is not counted.
        mixed: ``(jurisdiction, class)`` pairs whose lots rest partly on counted rows.
        jurisdictions: Sample jurisdictions found in the table.
        rule_ids: Zone rule ids behind the window rows, sorted.
    """

    shares: dict[str, np.ndarray]
    inside: np.ndarray
    sample_lots: float
    lots_by_class: dict[str, float]
    moved_lots: float
    mixed: tuple[tuple[str, str], ...] = ()
    jurisdictions: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()


def tract_classes(
    table: pd.DataFrame | None,
    tracts: pd.DataFrame,
    *,
    benchmark: str,
    review_states: Sequence[str],
    period: str = "window",
) -> TractClasses:
    """The :class:`TractClasses` of a tract table (zeros when there is no table).

    ``z_k = lots of the tract's sample part in class k / the tract's ACS
    one-unit detached homes``. The table builds its lots from those same homes,
    so a tract wholly inside the sample sums to 1; a tract only partly inside
    sums to its sample share; a rounding excess is scaled back to 1.
    """
    n = len(tracts)
    zero = {k: np.zeros(n) for k in CLASSES}
    if table is None or table.empty:
        return TractClasses(zero, np.zeros(n), 0.0, dict.fromkeys(CLASSES, 0.0), 0.0)
    frac = counted_fraction(table, benchmark, review_states, period=period)
    counted = {(r.jurisdiction, r.opportunity_class): float(r.counted) for r in frac.itertuples()}
    mixed = tuple(sorted(k for k, v in counted.items() if 1e-9 < v < 1 - 1e-9))
    t = table[
        (table["level"] == "tract")
        & (table["benchmark"] == benchmark)
        & (table["period"] == period)
    ].copy()
    t["counted"] = [
        counted.get((j, c), 0.0)
        for j, c in zip(t["jurisdiction"], t["opportunity_class"], strict=True)
    ]
    keep = t["sf_lots"] * t["counted"]
    moved = t["sf_lots"] - keep
    t["cls"] = t["opportunity_class"].astype(str)
    t["lots"] = keep
    extra = t.assign(cls="unresolved", lots=moved)
    both = pd.concat([t, extra], ignore_index=True)
    lots = both.groupby(["tract", "cls"])["lots"].sum().unstack(fill_value=0.0)
    lots = lots.reindex(columns=list(CLASSES), fill_value=0.0)
    geoids = tracts["geoid"].astype(str)
    acs = pd.to_numeric(tracts.get("units_1_detached"), errors="coerce").fillna(0.0).to_numpy()
    lots = lots.reindex(geoids.to_numpy(), fill_value=0.0)
    total = lots.sum(axis=1).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(acs > 0, np.minimum(1.0, acs / np.where(total > 0, total, 1.0)), 0.0)
        denom = np.where(acs > 0, acs, 1.0)
    shares = {k: np.where(acs > 0, lots[k].to_numpy() * scale / denom, 0.0) for k in CLASSES}
    inside = np.clip(sum(shares.values()), 0.0, 1.0)
    in_tracts = t[t["tract"].isin(set(geoids))]
    by_class = {k: float(lots[k].sum()) for k in CLASSES}
    zr = _zone_rows(table, benchmark, period)
    ids = sorted({i for s in zr.get("rule_ids", pd.Series(dtype=str)).dropna().astype(str)
                  for i in s.split("+") if i})  # fmt: skip
    return TractClasses(
        shares=shares,
        inside=inside,
        sample_lots=float(in_tracts["sf_lots"].sum()),
        lots_by_class=by_class,
        moved_lots=float(moved[t["tract"].isin(set(geoids))].sum()),
        mixed=mixed,
        jurisdictions=tuple(sorted(set(in_tracts["jurisdiction"]))),
        rule_ids=tuple(ids),
    )


@dataclass(frozen=True)
class DistrictRule:
    """The variant's already-legal rule (``legal_rule`` of :func:`scenarios.adu.adu_funnel`).

    ``blends_base`` tells the funnel to pass the benchmark's own share as
    ``base``; :meth:`share` keeps it outside the sample.
    """

    ids: tuple[str, ...]
    classes: TractClasses
    legal_words: Mapping[str, str]
    statewide_id: str
    conditions_id: str
    view: str
    view_words: str
    source_ids: tuple[str, ...]
    fingerprint: str | None
    notes: tuple[str, ...] = ()
    blends_base: bool = True
    record_key: str = "already_legal_share_zoning_districts"
    record_name: str = (
        "share of reached lots where the benchmark ADU is already legal (zoning-district classes "
        "in the sample, the benchmark's share elsewhere)"
    )
    record_expression: str = (
        "sum over tracts of lots x [(1 - z) x benchmark share + sum over classes of z_k x "
        "legal_k] / sum of lots, in each draw"
    )

    def _legal(self, word: str, col: Mapping[str, np.ndarray]) -> np.ndarray | float:
        if word == "one":
            return 1.0
        if word == "zero":
            return 0.0
        if word == "one_minus_conditions":
            return 1.0 - col[self.conditions_id]
        return col[self.statewide_id]

    def zone_part(self, col: Mapping[str, np.ndarray]) -> np.ndarray:
        """``sum over classes of z_k x legal_k``, draws x tracts."""
        n = col[self.statewide_id].shape[0]
        out = np.zeros((n, len(self.classes.inside)))
        for k in CLASSES:
            z = self.classes.shares[k]
            if not z.any():
                continue
            out = out + z[None, :] * self._legal(self.legal_words[k], col)
        return out

    def share(self, col: Mapping[str, np.ndarray], base: np.ndarray | None = None) -> np.ndarray:
        """Already-legal share, draws x tracts: ``(1 - z) x base + zone part``."""
        s = col[self.statewide_id]
        n = s.shape[0]
        m = len(self.classes.inside)
        b = np.broadcast_to(s if base is None else base, (n, m))
        if not self.classes.inside.any():
            return np.array(b, dtype=float)
        z = self.classes.inside[None, :]
        return np.clip(b * (1.0 - z) + self.zone_part(col), 0.0, 1.0)

    def records(
        self, lots: np.ndarray, legal: np.ndarray, col: Mapping[str, np.ndarray]
    ) -> dict[str, Record]:
        """Sample-only records: reached lots in the sample and their already-legal share.

        ``lots`` are the reached lots before the legal share (draws x tracts),
        ``col`` the drawn ledger columns; ``legal`` is the funnel's share (unused
        here, kept so every rule's hook has the same arguments).
        """
        if not self.classes.inside.any():
            return {}
        tot = (lots * self.classes.inside[None, :]).sum(axis=1)
        zone = (lots * self.zone_part(col)).sum(axis=1)
        share = np.where(tot > 0, zone / np.where(tot > 0, tot, 1.0), 0.0)
        sources = list(self.source_ids)
        ids = list(self.ids)
        return {
            "zoning_districts_sample_lots": rec.from_draws(
                "reached lots in the zoning-district sample (" + ", ".join(
                    _names(self.classes.jurisdictions)) + ")",
                tot, "lots",
                expression="sum over tracts of reached lots x z (the tract's share of "
                "single-family lots in the sample, rule_area_weights tract rows / ACS one-unit "
                "detached homes)",
                assumption_ids=ids, source_ids=sources,
            ),
            "zoning_districts_sample_legal_share": rec.from_draws(
                "share of the sample's reached lots where the benchmark ADU is already legal "
                f"({self.view_words})",
                share, "share",
                expression="sum over tracts of reached lots x sum over classes of z_k x legal_k "
                "/ sum over tracts of reached lots x z, in each draw (legal_k: 1 known by right, "
                f"1 - {self.conditions_id} conditions relaxed, 0 newly eligible or discretionary, "
                f"{self.statewide_id} unresolved)",
                assumption_ids=ids, source_ids=sources,
            ),
        }  # fmt: skip


def detached_share(
    tracts: pd.DataFrame,
    building_types: Sequence[str],
    ledger: Any = None,
    settings: Mapping[str, Any] | None = None,
) -> np.ndarray:
    """Per tract, single-family detached lots / all lots the scenario reaches.

    ``D / (D + A + units_2 / h2 + units_3_4 / h34)`` over the building types in
    scope (``A``: one-unit attached homes; ``h2``, ``h34``: homes per building,
    ledger centrals from ``lots.homes_per_building``). 0 where the scenario
    reaches no detached lot; 1 where it reaches only detached lots.
    """
    settings = settings or load_settings()

    def col(name: str) -> np.ndarray:
        if name not in tracts:
            return np.zeros(len(tracts))
        return pd.to_numeric(tracts[name], errors="coerce").fillna(0.0).clip(lower=0).to_numpy()

    types = set(building_types)
    det = col("units_1_detached") if "sf_detached" in types else np.zeros(len(tracts))
    total = det.copy()
    if "sf_attached" in types:
        total = total + col("units_1_attached")
    if "two_to_four" in types:
        hp = settings["lots"]["homes_per_building"]
        for column, pid in hp.items():
            h = float(ledger[pid].central) if ledger is not None and pid in ledger else 1.0
            total = total + col(column) / max(h, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, det / np.where(total > 0, total, 1.0), 0.0)


ZONE_WEIGHTS_PATH = ROOT / "assumptions" / "law" / "zone_weights.yaml"
"""The pilot's settings; its ``sample`` list names the jurisdictions."""


def _names(geoids: Sequence[str]) -> list[str]:
    """Sample jurisdiction names from ``zone_weights.yaml`` (the code when not listed)."""
    try:
        sample = yaml.safe_load(ZONE_WEIGHTS_PATH.read_text(encoding="utf-8")).get("sample") or []
    except OSError:
        sample = []
    named = {str(r.get("geoid")): str(r.get("name")) for r in sample}
    return [f"{named.get(g, g)} {g}" if g in named else g for g in geoids]


def district_rule(
    state: Any,
    *,
    option: str,
    settings: Mapping[str, Any] | None = None,
    table: pd.DataFrame | None = None,
    building_types: Sequence[str] = ("sf_detached",),
    ledger: Any = None,
) -> DistrictRule:
    """The :class:`DistrictRule` of ADU option ``legal = option`` for a run context.

    The table counts single-family **detached** lots only, so in each tract the
    class shares are scaled by the detached lots' share of the scenario's reached
    lots (:func:`detached_share`): attached and 2-4 unit lots keep the
    benchmark's share.

    Args:
        state: The ``StateContext`` (tracts; its pack folder for the table).
        option: ``zoning_districts`` (verified code only) or
            ``zoning_districts_guidance`` (also published guidance).
        settings: ``assumptions/law/zone_scope.yaml`` (read when not given).
        table: The table (default: :func:`read_table` of the state).
        building_types: The scenario's building types (``AduScope.building_types``).
        ledger: The run's ledger (homes per building of 2-4 unit lots, centrals).

    Raises:
        ValueError: ``option`` is not one of the settings' ``legal_options``.
    """
    settings = settings or load_settings()
    opts = dict(settings["legal_options"])
    if option not in opts:
        raise ValueError(f"ADU option legal must be one of {sorted(opts)}, got {option!r}")
    view = str(opts[option])
    spec = settings["views"][view]
    if table is None:
        table = read_table(state, settings=settings)
    led = settings["ledger"]
    classes = tract_classes(
        table,
        state.tracts,
        benchmark=str(settings["benchmark"]),
        review_states=list(spec["review_states"]),
        period=str(settings["period"]),
    )
    w = detached_share(state.tracts, building_types, ledger, settings)
    classes = replace(
        classes,
        shares={k: v * w for k, v in classes.shares.items()},
        inside=classes.inside * w,
    )
    fp = table_fingerprint(state, settings=settings)
    if not classes.inside.any():
        notes = (
            f"Variant reads the zoning-district table ({settings['table']}), but this pack has no "
            "rows for its tracts: every tract keeps the benchmark's already-legal share.",
        )
    else:
        lots = classes.lots_by_class
        total = sum(lots.values())
        mix = ", ".join(
            f"{k.replace('_', ' ')} {lots[k] / total:.1%}" for k in CLASSES if lots[k] > 0
        ) if total > 0 else "none"  # fmt: skip
        notes = (
            f"In the zoning-district sample ({', '.join(_names(classes.jurisdictions))}; "
            f"{classes.sample_lots:,.0f} single-family lots, table {settings['table']}"
            + (f" sha256 {fp}" if fp else "")
            + f"), the already-legal ADU share comes from the district classes ({spec['words']}): "
            f"{mix}. Known by right counts as legal, conditions relaxed as 1 - "
            f"{led['conditions']}, newly eligible and discretionary as not legal, unresolved at "
            f"the statewide draw of {led['statewide']}. The table counts single-family detached "
            "lots only, so attached and 2-4 unit lots, and every lot outside the sample, keep "
            "the benchmark's share.",
        )
        if classes.moved_lots > 0:
            notes += (
                f"{classes.moved_lots:,.0f} sample lots rest on rows this view does not count "
                "and are read as unresolved (unknown stays unknown).",
            )
        if classes.mixed:
            notes += (
                "Classes resting partly on counted rows, spread evenly over the jurisdiction's "
                "tracts: " + ", ".join(f"{j} {c}" for j, c in classes.mixed) + ".",
            )
    return DistrictRule(
        ids=(str(led["statewide"]), str(led["conditions"])),
        classes=classes,
        legal_words={k: str(v) for k, v in settings["already_legal"].items()},
        statewide_id=str(led["statewide"]),
        conditions_id=str(led["conditions"]),
        view=view,
        view_words=str(spec["words"]),
        source_ids=tuple(settings.get("source_ids") or ()),
        fingerprint=fp,
        notes=notes,
    )


def jurisdiction_mix(
    table: pd.DataFrame | None,
    benchmark: str,
    review_states: Sequence[str],
    *,
    weight: str = "sf_lots",
    period: str = "window",
) -> pd.DataFrame:
    """Per sample jurisdiction, its lots (or homes) by class and law-changes answer.

    A zone row whose review state is not counted is read as ``unresolved``.
    Returns ``jurisdiction``, ``opportunity_class``, ``answer``, ``value`` and
    ``share`` (of the jurisdiction's total), and ``why`` (the zone rows' reasons).
    """
    cols = ["jurisdiction", "opportunity_class", "answer", "value", "share", "why"]
    if table is None or table.empty:
        return pd.DataFrame(columns=cols)
    z = _zone_rows(table, benchmark, period).copy()
    if z.empty:
        return pd.DataFrame(columns=cols)
    ok = z["review_state"].astype(str).isin(set(review_states))
    z["opportunity_class"] = np.where(ok, z["opportunity_class"].astype(str), "unresolved")
    z["value"] = pd.to_numeric(z[weight], errors="coerce").fillna(0.0)
    skip = "Rests on a row this view does not count."
    z["why"] = np.where(ok, z["why"].astype(str) if "why" in z else "", skip)
    g = z.groupby(["jurisdiction", "opportunity_class"], as_index=False).agg(
        value=("value", "sum"), why=("why", lambda s: " | ".join(sorted(set(map(str, s))))[:300])
    )
    tot = g.groupby("jurisdiction")["value"].transform("sum")
    g["share"] = np.where(tot > 0, g["value"] / tot.where(tot > 0, 1.0), 0.0)
    g["answer"] = g["opportunity_class"].map(U13_ANSWER)
    return g[cols]
