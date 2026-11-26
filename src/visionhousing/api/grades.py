"""``/api/v1/grades``, ``/api/v1/trace`` and ``/api/v1/switch``: the trust tools for a run.

Plain-language summary
----------------------
Three read-only endpoints over a run of the scenario in the query (``r``
reforms, ``a`` overrides, ``seed``, ``draws``, ``preset``, as elsewhere). They
run (or reuse) the engine run and then call :mod:`visionhousing.grades`; they
never change a number.

=============================================  ===============================================
``GET /grades/{st}?r=...&level=state``         each reform's state headline (and the baseline)
                                               graded A to D: letter, score, component scores
                                               and inputs, plain sentence, the rule's id
``GET /grades/{st}?r=...&level=county``        the same for every county (``county=`` for one)
``GET /trace/{st}?r=...&id=...``               the step-by-step waterfall behind one number.
                                               ``id`` is a record id
                                               (``reform_results.adu.added``,
                                               ``reform_results.adu.counties.42003``), or with
                                               one reform in ``r``: ``state``, a county id, or
                                               ``counties`` (every county's trace, for a
                                               static file)
``GET /switch/{st}?r=...&county=``            each reform's top three drivers at low /
                                               central / high (the tornado), for a three-way
                                               switch; with ``ranges`` (state, and the
                                               ``county`` if given) when the engine's switch
                                               runs are cached or exported
=============================================  ===============================================

With no ``r``, the five benchmark reforms are used. A real state answers only the
default seed and draws (another seed or draw count is a 400
``default_sampling_only``; ``POST /run`` takes those). Grade replies are cached
under the run's key plus the stamp of the state's files (:func:`api.runner.stamp`).
A trace also carries the "reproduce this number" receipt (command, seed, hashes)
from :func:`visionhousing.intake.reproduce.reproduce` when that module is installed;
otherwise ``reproduce`` is ``null``.

Static export (for the final export job): save ``GET /grades/ST?level=state`` as
``ST/grades.json``, ``GET /grades/ST?level=county`` as ``ST/grades-county.json``,
``GET /trace/ST?r=<key>&id=state`` and ``&id=counties`` as ``ST/trace/<key>.json`` and
``ST/trace/<key>-counties.json``, and ``GET /switch/ST`` as ``ST/switch.json``.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import ValidationError

from visionhousing.api import service
from visionhousing.api.models import LIMITS, ScenarioError, scenario_from_query
from visionhousing.api.runner import Runner, require_default_sampling, stamp

__all__ = ["DEFAULT_REFORMS", "register", "reproduce_receipt"]

DEFAULT_REFORMS = ("adu", "missing_middle", "tod", "parking", "fees")
"""The five benchmark reforms (engine keys), used when the query names none."""

LOG = logging.getLogger(__name__)


def reproduce_receipt(result: Any, record_id: str, run_data: Any = None) -> Any:
    """The "reproduce this number" receipt for a record, from the intake lane's lookup.

    Feature-detected: :func:`visionhousing.intake.reproduce.reproduce`, given the
    live run's ``to_dict()`` (``run_data``, made once per request by the caller
    when it traces many records). ``None`` when the lookup is not installed or
    cannot answer.
    """
    try:
        mod = importlib.import_module("visionhousing.intake.reproduce")
    except ImportError:
        return None
    fn = getattr(mod, "reproduce", None)
    if not callable(fn):
        return None
    keys = list(result.reform_ids)
    run = keys[0] if len(keys) == 1 else "package"
    try:
        return fn(
            f"{result.state}:{run}:{record_id}",
            run_data=run_data if run_data is not None else result.to_dict(),
            source="live run",
        )
    except Exception as exc:  # the lookup's own errors: no receipt, the trace still answers
        LOG.debug("reproduce lookup did not answer for %s: %s", record_id, exc)
        return None


def register(
    app: FastAPI,
    *,
    api: str,
    error: Callable[[int, str, str], Exception],
    context: Callable[[str], Any],
    execute: Callable[[Any], tuple[str, Any]],
    timed: Callable[[str, Any], Any],
    folder: Callable[[str], Path],
    data_dir: Path | None = None,
) -> None:
    """Add the grades, trace and switch endpoints to ``app``.

    Args:
        app: The FastAPI app (before its catch-all ``/api/v1/...`` route).
        api: The address prefix (``/api/v1``).
        error: Makes the app's JSON error: ``error(status, code, message)``.
        context: State code to its loaded context (raises the app's errors).
        execute: Scenario request to ``(cache key, Result)``.
        timed: Runs a job on the pool within the time limit.
        folder: State code to its data folder.
        data_dir: The static export folder holding stored tornado runs (optional).
    """
    from visionhousing.grades import TraceError, grade_run, switch_payload, trace
    from visionhousing.sensitivity.switch_runs import cached_switch_runs

    def scenario(st: str, request: Request, extra: tuple[str, ...]) -> tuple[Any, dict[str, str]]:
        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        params = dict(request.query_params)
        picked = {k: params.pop(k) for k in extra if k in params}
        if "reforms" in params:
            if "r" in params:
                raise error(400, "invalid_query", "give r or reforms, not both")
            params["r"] = params.pop("reforms")
        params.setdefault("r", ",".join(DEFAULT_REFORMS))
        try:
            req = scenario_from_query(params, st)
        except ScenarioError as exc:
            raise error(400, "invalid_query", str(exc)) from None
        except ValidationError as exc:
            message = "; ".join(str(e["msg"]) for e in exc.errors())
            raise error(400, "invalid_query", message) from None
        if not req.reform_arg():
            raise error(400, "invalid_query", "name at least one reform in r")
        refused = require_default_sampling(req)
        if refused:
            raise error(400, "default_sampling_only", refused)
        return req, picked

    def reply(body: Any) -> Response:
        return Response(body if isinstance(body, bytes) else service.dumps(body),
                        media_type="application/json")  # fmt: skip

    @app.get(f"{api}/grades/{{st}}")
    def grades(st: str, request: Request) -> Response:
        """Evidence grades A-D for each headline number, state or county level."""
        req, picked = scenario(st, request, ("level", "county"))
        level = picked.get("level", "state")
        if level not in ("state", "county"):
            raise error(400, "invalid_level", "level must be 'state' or 'county'")
        ctx = context(req.state)
        key, result = execute(req)
        county = picked.get("county")
        if county is not None and county not in result.county_ids:
            raise error(404, "county_not_found", f"no county {county[:12]} in {ctx.state}")

        def render() -> bytes:
            body = grade_run(
                ctx, result, level, folder=folder(ctx.state),
                counties=None if county is None else [county],
            )  # fmt: skip
            body["scenario"] = {"reforms": list(result.reform_ids), "seed": result.seed,
                                "draws": result.draws}  # fmt: skip
            return service.dumps(body)

        files = stamp(folder(ctx.state))
        return reply(timed(Runner.key("grades", key, level, county, files), render))

    @app.get(f"{api}/trace/{{st}}")
    def number_trace(st: str, request: Request) -> Response:
        """The step-by-step waterfall behind one reform headline (state or county)."""
        req, picked = scenario(st, request, ("id",))
        ref = picked.get("id")
        if not ref:
            raise error(
                400, "invalid_query", "give id: a record id, 'state', a county id or 'counties'"
            )
        ctx = context(req.state)
        key, result = execute(req)
        keys = list(result.reform_ids)
        short = ref in ("state", "counties") or ref.isdigit()
        if short and len(keys) != 1:
            raise error(400, "invalid_query", f"id '{ref[:12]}' needs exactly one reform in r")

        run_data: dict[str, Any] = {}

        def one(record_id: str) -> dict[str, Any]:
            body = trace(result, record_id)
            body["state"] = ctx.state
            if not run_data:
                run_data.update(result.to_dict())
            body["reproduce"] = reproduce_receipt(result, record_id, run_data)
            return body

        def render() -> bytes:
            try:
                if ref == "counties":
                    k = keys[0]
                    body: Any = {
                        "state": ctx.state, "reform": k,
                        "counties": {c: one(f"reform_results.{k}.counties.{c}")
                                     for c in result.reforms[k].counties},
                    }  # fmt: skip
                elif ref == "state":
                    body = one(f"reform_results.{keys[0]}.added")
                elif ref.isdigit():
                    body = one(f"reform_results.{keys[0]}.counties.{ref}")
                else:
                    body = one(ref)
            except TraceError as exc:
                return b"!" + str(exc).encode()
            return service.dumps(body)

        out = timed(Runner.key("trace", key, ref), render)
        if out.startswith(b"!"):
            raise error(404, "trace_not_found", out[1:].decode()[:200])
        return reply(out)

    @app.get(f"{api}/switch/{{st}}")
    def switch(st: str, request: Request) -> Response:
        """Each reform's top three drivers at low / central / high (stored tornado or live).

        When the engine's switch runs (``sensitivity.switch_runs``) are already
        worked out, each driver also carries the likely ranges at each setting.
        """
        req, picked = scenario(st, request, ("county",))
        ctx = context(req.state)
        key, result = execute(req)
        county = picked.get("county")
        if county is not None and county not in result.county_ids:
            raise error(404, "county_not_found", f"no county {county[:12]} in {ctx.state}")
        # The tornado pins every input, so it does not depend on the seed; a stored one
        # is valid for the default assumptions and housing cycle (and its hashes match).
        default = not req.overrides and req.preset == "central"

        def render() -> bytes:
            body = switch_payload(result, list(result.reform_ids),
                                  data_dir if default else None, county)  # fmt: skip
            body["scenario"] = {"reforms": list(result.reform_ids), "seed": result.seed,
                                "draws": result.draws}  # fmt: skip
            body["state"] = ctx.state
            return service.dumps(body)

        # The reply changes once switch runs are cached, so they are part of its key.
        ready = tuple(cached_switch_runs(result, k) is not None for k in result.reform_ids)
        return reply(timed(Runner.key("switch", key, county, ready), render))
