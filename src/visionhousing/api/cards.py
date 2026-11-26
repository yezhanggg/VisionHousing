"""Explanation cards: confidence signals, the counterfactual lever and implementation status.

Plain-language summary
----------------------
Three read-only addresses that the browser's small explanation cards read
(``web/js/ui/confidence.js``, ``lever.js``, ``implementation.js``). Each one
only gathers what other modules already work out, adds the text forms the
screens show, and never changes a model number. The words and choices live in
``assumptions/cards.yaml``.

=================================================  =========================================
``GET /confidence/{st}``                            the two confidence signals, side by side
                                                    and never merged: how the baseline's past
                                                    ranges did (hold-out back-test) and how
                                                    much outside evidence each reform type has
                                                    (:func:`visionhousing.validation.donor_pool.
                                                    confidence_signals`, unchanged, plus names)
``GET /lever/{st}?r=adu&county=42003``              the counterfactual lever card for one
                                                    reform: the binding factor and who can act
                                                    (:func:`visionhousing.api.decision.
                                                    binding_constraint`), the effect of each
                                                    named variant as a paired range (variant
                                                    minus benchmark, draw by draw), the
                                                    evidence grade and one next check
``GET /implementation/{st}?r=adu&geoid=42003``      implementation-status rows: for each
                                                    provision that covers the place and the
                                                    reform's housing types, its legal status,
                                                    the local step and the permit step, each
                                                    with its sources and dates
=================================================  =========================================

The lever card runs the benchmark reform and its named variants through the
app's own cached runner (same seed and draws), so a second ask is instant. Its
reply is cached under the runs' keys plus the stamp of the state's files and the
assumption files (:func:`visionhousing.api.runner.stamp`), so an edited
``cards.yaml``, ``plausibility.yaml``, ``variants.yaml`` or ``grades.yaml`` is
never answered from an old reply. Each paired effect carries the variant's
ledger ids (``assumption_ids``) and the two runs' ``source_ids``. Like
the other GET addresses, a real state answers only the default seed (0) and
draws (another is a 400 ``default_sampling_only``); Testland takes any. The
variants are the ones listed for the reform in ``assumptions/cards.yaml``.

Static export (for the export job): ``GET /confidence/ST`` as
``ST/confidence.json``, ``GET /implementation/ST`` (no ``r``, no ``geoid``:
every reform and every place) as ``ST/implementation.json``, and
``GET /lever/ST?r=KEY`` as ``ST/lever/KEY.json``. Without them the static site
says the card needs the local app.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import ValidationError

from visionhousing.api import service
from visionhousing.api.models import LIMITS, RunRequest, ScenarioError, scenario_from_query
from visionhousing.api.runner import Runner, require_default_sampling, stamp
from visionhousing.ledger.loader import ROOT

__all__ = [
    "CARDS_PATH",
    "confidence_payload",
    "implementation_rows",
    "lever_payload",
    "load_cards",
    "paired_effect",
    "register",
]

CARDS_PATH = ROOT / "assumptions" / "cards.yaml"
BENCHMARKS = ("adu", "missing_middle", "tod", "parking", "fees")
LEGAL_ORDER = (
    "pending",
    "enacted",
    "legally_effective",
    "stayed",
    "locally_implemented",
    "practically_usable",
)


@lru_cache(maxsize=4)
def _read(path: str, mtime: int) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_cards(path: Path = CARDS_PATH) -> dict[str, Any]:
    """Read ``assumptions/cards.yaml`` (cached until the file changes)."""
    p = Path(path)
    return _read(str(p), p.stat().st_mtime_ns)


# ------------------------------------------------------------------ confidence


def _files_sig(paths: Iterable[Path]) -> tuple:
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths if p.is_file())


_CONF_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}


def confidence_payload(state: str, cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The two confidence signals for ``state``, unchanged, plus names for the reform types.

    Returns:
        :func:`~visionhousing.validation.donor_pool.confidence_signals` as it
        is, with ``type_labels`` (policy type to name), ``benchmark_types``
        (the five benchmark reforms, in order) and ``coverage_text`` (the
        back-test's caught share as text, or ``None``).
    """
    from visionhousing.evidence.records import PANEL_DIR
    from visionhousing.explain import show
    from visionhousing.validation.donor_pool import OUT_PATH, confidence_signals

    cfg = cfg or load_cards()
    sig = _files_sig([OUT_PATH, CARDS_PATH, *sorted(Path(PANEL_DIR).glob("*.yaml"))])
    held = _CONF_CACHE.get(state)
    if held is not None and held[0] == sig:
        return held[1]
    body = dict(confidence_signals(state))
    bp = dict(body.get("baseline_performance") or {})
    cov = bp.get("held_out_coverage")
    bp["coverage_text"] = None if cov is None else show(float(cov), "share")
    body["baseline_performance"] = bp
    body["type_labels"] = dict(cfg.get("type_labels") or {})
    body["benchmark_types"] = list(cfg.get("benchmark_types") or BENCHMARKS)
    _CONF_CACHE[state] = (sig, body)
    return body


# ------------------------------------------------------------------ lever


def _text3(p10: float, p50: float, p90: float, unit: str) -> dict[str, str]:
    from visionhousing.explain import show
    from visionhousing.explain.numbers import TABLE_SIG

    return {k: show(v, unit, TABLE_SIG) for k, v in (("p10", p10), ("p50", p50), ("p90", p90))}


def paired_effect(
    variant_draws: Sequence[float] | np.ndarray,
    bench_draws: Sequence[float] | np.ndarray,
    *,
    name: str,
    unit: str = "units",
    assumption_ids: Iterable[str] = (),
    source_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """The P10 / P50 / P90 of (variant minus benchmark), draw by draw, with text and formula.

    Both runs use the same seed and draws, so draw *i* of each shares its
    random inputs except the ones the variant swaps; the difference is then the
    variant's own effect, not noise between two separate runs.

    ``assumption_ids`` are the ledger ids the variant swaps (each target and
    the source it takes its range from); ``source_ids`` are the two runs'
    sources. Both are kept sorted, once each, so the number is traceable.
    """
    v = np.asarray(variant_draws, dtype=float)
    b = np.asarray(bench_draws, dtype=float)
    if v.shape != b.shape or v.ndim != 1 or v.size == 0:
        raise ValueError("variant and benchmark draws must be paired one-dimensional arrays")
    diff = v - b
    p10, p50, p90 = (float(q) for q in np.percentile(diff, [10, 50, 90]))
    shown = _text3(p10, p50, p90, unit)
    return {
        "name": name,
        "unit": unit,
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "shown": shown,
        "direction": "more" if p50 > 0 else ("fewer" if p50 < 0 else "same"),
        "assumption_ids": sorted(set(assumption_ids)),
        "source_ids": sorted(set(source_ids)),
        "formula": (
            f"P10, P50, P90 of {diff.size:,} paired draws of (variant added units minus "
            f"benchmark added units), same seed = {shown['p10']}, {shown['p50']}, {shown['p90']}"
        ),
    }


def _record(r: Any) -> dict[str, Any]:
    """An engine range record as the screens read it."""
    return {
        "name": r.name,
        "unit": r.unit,
        "p10": r.p10,
        "p50": r.p50,
        "p90": r.p90,
        "shown": _text3(r.p10, r.p50, r.p90, r.unit) if r.p50 is not None else {},
        "formula": r.formula,
        "assumption_ids": list(getattr(r, "assumption_ids", ()) or ()),
        "source_ids": list(getattr(r, "source_ids", ()) or ()),
    }


def _draws_record(draws: np.ndarray, name: str) -> dict[str, Any]:
    p10, p50, p90 = (float(q) for q in np.percentile(draws, [10, 50, 90]))
    return {"name": name, "unit": "units", "p10": p10, "p50": p50, "p90": p90,
            "shown": _text3(p10, p50, p90, "units"),
            "formula": f"P10, P50, P90 of {draws.size:,} seeded draws"}  # fmt: skip


def lever_payload(
    key: str,
    bench: Any,
    variants: Mapping[str, Any],
    *,
    grade: Mapping[str, Any] | None = None,
    county: str | None = None,
    county_name: str | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The lever card for one reform.

    Args:
        key: Reform key.
        bench: The benchmark run of this reform alone (an engine ``Result``).
        variants: Variant name to its run (same seed, draws and overrides).
        grade: The evidence grade record of this reform (``grade_run``'s entry).
        county: A county id, for the county's added units and paired effect.
        county_name: The county's name, for the heading.
        cfg: The cards file (default ``assumptions/cards.yaml``).

    Returns:
        ``{"reform", "title", "binding", "who", "effects", "added", "grade",
        "verify", "rule", "rule_id", "county"}``.
    """
    from visionhousing.api.decision import binding_constraint
    from visionhousing.engine.registry import get_reform
    from visionhousing.engine.variants import load_variants

    cfg = cfg or load_cards()
    lever = cfg["lever"]
    spec = get_reform(key)
    o = bench.reforms[key]
    bc = binding_constraint(o, spec.family)
    known = load_variants()
    ci = None
    if county is not None:
        ci = list(bench.county_ids).index(county)
    effects = []
    for name, res in variants.items():
        vo = res.reforms[key]
        v = known.get(name)
        ids = [*v.overrides.keys(), *v.overrides.values()] if v is not None else []
        sources = [*(vo.added.source_ids or ()), *(o.added.source_ids or ())]
        item: dict[str, Any] = {
            "variant": name,
            "title": v.title if v else name,
            "question": v.question if v else "",
            "added": _record(vo.added),
            "effect": paired_effect(
                vo.state_draws,
                o.state_draws,
                name=f"change in added units, {name}, state",
                assumption_ids=ids,
                source_ids=sources,
            ),
        }
        if ci is not None:
            item["county_effect"] = paired_effect(
                np.asarray(vo.county_draws)[ci], np.asarray(o.county_draws)[ci],
                name=f"change in added units, {name}, county {county}",
                assumption_ids=ids, source_ids=sources,
            )  # fmt: skip
        from visionhousing.scenarios.adu_levers import card_fields

        item.update(card_fields(vo, county))  # ADU two-lever case: moved vs newly feasible, binding
        effects.append(item)
    binding = bc["binding"] if bc else None
    verify = (lever["reforms"].get(spec.family) or {}).get("verify") or (
        binding.get("question") if binding else ""
    )
    added = _record(o.added)
    county_added = None
    if ci is not None:
        county_added = _draws_record(
            np.asarray(o.county_draws)[ci], f"added units, {spec.title}, county {county}"
        )
    return {
        "reform": key,
        "title": spec.title,
        "family": spec.family,
        "rule": " ".join(str(lever["rule"]).split()),
        "rule_id": lever["id"],
        "binding": binding,
        "by_actor": bc["by_actor"] if bc else [],
        "kind": bc["kind"] if bc else None,
        "who": (
            {"actor": binding["actor"], "word": binding["actor_word"], "who": binding["who"]}
            if binding
            else None
        ),
        "added": added,
        "county": (
            {"geoid": county, "name": county_name, "added": county_added}
            if county is not None
            else None
        ),
        "effects": effects,
        "grade": (
            {
                "grade": grade.get("grade"),
                "sentence": grade.get("sentence"),
                "rule_id": grade.get("rule_id"),
            }
            if grade
            else None
        ),
        "verify": " ".join(str(verify).split()),
        "scenario": {"seed": bench.seed, "draws": bench.draws},
    }


# ------------------------------------------------------------------ implementation


def _covers_place(jurisdiction: str, state: str, geoid: str) -> bool:
    """True when a record's jurisdiction is the state, or holds or lies inside ``geoid``."""
    if jurisdiction.isalpha():
        return jurisdiction == state
    if not geoid:
        return True  # state view: every local record of the state's pack
    return geoid.startswith(jurisdiction) or jurisdiction.startswith(geoid)


def _level(r: Any) -> str:
    if r.area_id:
        return "area"
    return {10: "municipality", 5: "county"}.get(len(r.jurisdiction), "state")


def _when(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _begin(r: Any) -> date | None:
    """First day a timeline record applies (``verified_on`` when its start is unknown)."""
    return getattr(r, "in_force_from", r.start)


def _step(r: Any) -> dict[str, Any]:
    return {
        "record_id": r.record_id,
        "status": r.status,
        "citation": r.citation,
        "start": _when(r.start),
        "start_unknown": bool(getattr(r, "start_unknown", False)),
        "end": _when(r.end),
        "verified_on": _when(r.verified_on),
        "sources": list(r.sources),
        "agency": r.agency,
        "note": " ".join(r.note.split()) if r.note else "",
        "court_status": r.court_status,
        "implementation_evidence": r.implementation_evidence,
    }


def implementation_rows(
    records: Iterable[Any],
    *,
    state: str,
    reforms: Sequence[str],
    geoid: str = "",
    on: date | None = None,
    state_fips: str = "",
    cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Implementation-status rows for a place: one row per provision and reform.

    Args:
        records: Law timeline records (:func:`visionhousing.law.timeline.load_timeline`).
            Only ``review: verified`` records count; a record verified after
            ``on`` is ignored.
        state: Two-letter state code.
        reforms: Reform keys (benchmark families).
        geoid: County (5) or municipality (10 digits); empty = the state and
            every local record in it.
        on: The date the row describes (default today).
        state_fips: The state's two-digit code (records written that way).
        cfg: The cards file.

    Returns:
        ``{"state", "geoid", "on", "rows", "none_text", "pending_note"}``. Each
        row: ``provision_id``, ``reform``, ``housing_type``, ``jurisdiction``,
        ``level``, ``citation``, ``legal`` / ``local`` / ``permits`` (each
        ``{"state": done|missing|not_yet|..., "text", "record"}``), ``line``
        (the three texts joined with " · "), ``pending`` and ``history``
        (earlier records of the same provision, with their end dates). Rows are
        ordered by reform, then state before local, in force before pending.
    """
    cfg = (cfg or load_cards())["implementation"]
    on = on or date.today()
    types = cfg["housing_types"]
    words_legal, words_local, words_permit = (
        cfg["legal_words"], cfg["local_words"], cfg["permit_words"]
    )  # fmt: skip
    rows: list[dict[str, Any]] = []
    recs = [
        r
        for r in records
        if r.review == "verified" and (r.verified_on is None or r.verified_on <= on)
    ]
    for key in reforms:
        wanted = set(types.get(key, [key, "all"]))
        mine = [
            r
            for r in recs
            if r.housing_type in wanted
            and _covers_place(r.jurisdiction, state, geoid)
            and (r.jurisdiction.isalpha() or (state_fips and r.jurisdiction.startswith(state_fips)))
        ]
        groups: dict[str, list[Any]] = {}
        for r in mine:
            groups.setdefault(r.provision_id or r.record_id, []).append(r)
        for pid, group in sorted(groups.items()):
            group.sort(key=lambda r: (_begin(r) or date.min, LEGAL_ORDER.index(r.status)))
            live = [
                r
                for r in group
                if (_begin(r) is None or _begin(r) <= on) and (r.end is None or on < r.end)
            ]
            current = live[-1] if live else group[-1]
            ended = not live
            status = "ended" if ended else current.status
            if current.court_status == "stayed" and not ended:
                status = "stayed"
            is_law = status in ("legally_effective", "locally_implemented", "practically_usable")
            local = next(
                (r for r in reversed(group)
                 if r.status in ("locally_implemented", "practically_usable")),
                None,
            )  # fmt: skip
            permit = next((r for r in reversed(group) if r.status == "practically_usable"), None)
            local_has = local is not None or bool(current.implementation_evidence)
            if not is_law:
                lstate, pstate = "not_yet", "not_yet"
            else:
                lstate = "done" if local_has else "missing"
                pstate = "done" if permit is not None else "missing"
            legal = {"state": status, "text": words_legal[status], "record": _step(current)}
            local_step = {"state": lstate, "text": words_local[lstate],
                          "record": _step(local) if local is not None else None}  # fmt: skip
            permit_step = {"state": pstate, "text": words_permit[pstate],
                           "record": _step(permit) if permit is not None else None}  # fmt: skip
            line = (
                legal["text"]
                if not is_law
                else f"{legal['text']} · {local_step['text']} · {permit_step['text']}"
            )
            rows.append(
                {
                    "provision_id": pid,
                    "reform": key,
                    "housing_type": current.housing_type,
                    "jurisdiction": current.jurisdiction,
                    "level": _level(current),
                    "citation": current.citation,
                    "scope": current.scope,
                    "pending": status == "pending",
                    "legal": legal,
                    "local": local_step,
                    "permits": permit_step,
                    "line": line,
                    "history": [_step(r) for r in group if r is not current],
                }
            )
    rank = {"state": 0, "area": 1, "county": 2, "municipality": 3}
    rows.sort(
        key=lambda r: (
            list(reforms).index(r["reform"]),
            rank.get(r["level"], 4),
            r["pending"],
            r["citation"],
        )
    )
    return {
        "state": state,
        "geoid": geoid or None,
        "on": on.isoformat(),
        "reforms": list(reforms),
        "rows": rows,
        "none_text": cfg["none_text"],
        "pending_note": cfg["pending_note"],
        "source": "assumptions/law/timeline.yaml",
    }


# ------------------------------------------------------------------ endpoints

_GEOID = re.compile(r"^(\d{5}|\d{10})$")


def register(
    app: FastAPI,
    *,
    api: str,
    error: Callable[[int, str, str], Exception],
    context: Callable[[str], Any],
    execute: Callable[[Any], tuple[str, Any]],
    timed: Callable[[str, Any], Any],
    folder: Callable[[str], Path] | None = None,
) -> None:
    """Add the three card endpoints to ``app`` (before its catch-all ``/api/v1/...`` route).

    Args:
        app: The FastAPI app.
        api: The address prefix (``/api/v1``).
        error: Makes the app's JSON error: ``error(status, code, message)``.
        context: State code to its loaded context (raises the app's errors).
        execute: Scenario request to ``(cache key, Result)`` (cached).
        timed: Runs a job on the pool within the time limit.
        folder: State code to its data folder (for county names).
    """

    def reply(body: Any) -> Response:
        return Response(body if isinstance(body, bytes) else service.dumps(body),
                        media_type="application/json")  # fmt: skip

    def code(st: str) -> str:
        c = st.upper()
        if len(c) != 2 or not c.isalpha():
            raise error(400, "invalid_state", "state must be a two-letter code")
        return c

    def check_query(request: Request) -> dict[str, str]:
        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        return dict(request.query_params)

    @app.get(f"{api}/confidence/{{st}}")
    def confidence(st: str) -> Response:
        """Baseline performance and reform evidence, side by side (never merged)."""
        return reply(confidence_payload(code(st)))

    @app.get(f"{api}/lever/{{st}}")
    def lever(st: str, request: Request) -> Response:
        """The counterfactual lever card for one reform (state, or one county)."""
        from visionhousing.engine.registry import get_reform
        from visionhousing.grades import grade_run

        params = check_query(request)
        county = params.pop("county", None)
        if "reforms" in params:
            params["r"] = params.pop("reforms")
        keys = [k for k in params.get("r", "").split(",") if k]
        if len(keys) != 1:
            raise error(400, "invalid_query", "name exactly one reform in r")
        try:
            req = scenario_from_query(params, st)
        except ScenarioError as exc:
            raise error(400, "invalid_query", str(exc)) from None
        except ValidationError as exc:
            message = "; ".join(str(e["msg"]) for e in exc.errors())
            raise error(400, "invalid_query", message) from None
        refused = require_default_sampling(req)
        if refused:
            raise error(400, "default_sampling_only", refused)
        key = keys[0]
        try:
            family = get_reform(key).family
        except KeyError:
            raise error(404, "reform_not_found", f"no reform {key[:32]!r}") from None
        if county is not None and not re.match(r"^\d{5}$", county):
            raise error(400, "invalid_county", "county must be a 5-digit code")
        ctx = context(req.state)
        base = {"overrides": req.overrides, "preset": req.preset, "seed": req.seed,
                "draws": req.draws, "explain": False}  # fmt: skip
        bkey, bench = execute(RunRequest(state=req.state, reforms=[key], **base))
        if county is not None and county not in bench.county_ids:
            raise error(404, "county_not_found", f"no county {county} in {ctx.state}")
        names = (load_cards()["lever"]["reforms"].get(family) or {}).get("variants") or []
        runs = {}
        for name in names:
            _vk, runs[name] = execute(
                RunRequest(state=req.state, reforms={key: {"variant": name}}, **base)
            )

        def render() -> bytes:
            level = "county" if county is not None else "state"
            graded = grade_run(ctx, bench, level, keys=[key],
                               counties=None if county is None else [county])  # fmt: skip
            g = (graded.get("grades") if county is None
                 else (graded.get("counties") or {}).get(county)) or {}  # fmt: skip
            cname = None
            if county is not None and folder is not None:
                cname = service._county_facts(folder(ctx.state)).get(county, {}).get("name")
            body = lever_payload(key, bench, runs, grade=g.get(key), county=county,
                                 county_name=cname)  # fmt: skip
            body["state"] = ctx.state
            return service.dumps(body)

        # The reply also depends on files the engine run does not read (the cards, plausibility,
        # variants and grades settings, and the state's grade inputs), so their stamp is in
        # the key: an edited file is never answered from an old reply.
        files = stamp(folder(ctx.state)) if folder is not None else stamp(ROOT / "assumptions")
        return reply(timed(Runner.key("lever", bkey, tuple(names), county, files), render))

    @app.get(f"{api}/implementation/{{st}}")
    def implementation(st: str, request: Request) -> Response:
        """Implementation-status rows for a place and reform, with sources and dates."""
        from visionhousing.law.timeline import TIMELINE_PATH, load_timeline

        params = check_query(request)
        unknown = sorted(set(params) - {"r", "geoid", "on"})
        if unknown:
            raise error(400, "invalid_query", f"unknown query fields {unknown[:5]}")
        state = code(st)
        geoid = params.get("geoid", "")
        if geoid and not _GEOID.match(geoid):
            raise error(400, "invalid_place", "geoid must be a 5-digit county or 10-digit place")
        keys = [k for k in params.get("r", "").split(",") if k] or list(BENCHMARKS)
        bad = [k for k in keys if k not in BENCHMARKS]
        if bad:
            raise error(400, "invalid_reform", f"r must be benchmark reforms; got {bad[:3]}")
        on = None
        if params.get("on"):
            try:
                on = date.fromisoformat(params["on"])
            except ValueError:
                raise error(400, "invalid_date", "on must be a date (YYYY-MM-DD)") from None
        fips = _state_fips(state)
        if geoid and fips and not geoid.startswith(fips):
            raise error(404, "place_not_found", f"{geoid} is not in {state}")
        return reply(
            implementation_rows(load_timeline(TIMELINE_PATH), state=state, reforms=keys,
                                geoid=geoid, on=on, state_fips=fips)
        )  # fmt: skip


def _state_fips(state: str) -> str:
    """The state's two-digit code (empty for Testland or an unknown state)."""
    from visionhousing.data.sources import STATE_FIPS

    return STATE_FIPS.get(state, "")
