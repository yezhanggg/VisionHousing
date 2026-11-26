"""Intake, reproduce and plausibility endpoints.

Plain-language summary
----------------------
Three read-only addresses:

- ``GET /api/v1/intake/{st}``: the state's intake manifest
  (:func:`visionhousing.intake.manifest.build_manifest`: one row per data
  source, ledger entry, law row and comparison record, every blank field
  with its value state, and the verified-exposure and transferred-take-up
  fractions) plus the five-step readiness check
  (:func:`visionhousing.intake.readiness.readiness`).
- ``GET /api/v1/reproduce?id=ST:run:path``: the command, seed, draws, the
  run's three hashes, the pack hash and the code version behind one record
  (:func:`visionhousing.intake.reproduce.reproduce`). It reads the static
  site's saved run; a state with no saved run (Testland) is run once at the
  default seed and draws, and the answer says so.
- ``GET /api/v1/plausibility/{st}?r=adu``: each reform's rate (homes per
  1,000 eligible lots per year, and per 100,000 residents) beside observed
  rates, with the rule-written sentence
  (:func:`visionhousing.validation.plausibility.plausibility`). It takes the
  same scenario fields as the other state addresses (``r``, ``a``, ``seed``,
  ``draws``, ``preset``) and uses the same cached engine run; without ``r``
  it uses the benchmark reforms. A real state answers only the default seed
  and draws (another is a 400 ``default_sampling_only``; ``POST /run`` takes
  those). The browser computes nothing.

The intake and plausibility replies are cached under the stamp of the state's
files and the assumption files (:func:`visionhousing.api.runner.stamp`), so an
edited setting (``assumptions/plausibility.yaml``, an evidence file) or a
rebuilt pack is never served from an old reply. The lever card
(:mod:`visionhousing.api.cards`) does the same.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import ValidationError

from visionhousing.api import service
from visionhousing.api.models import LIMITS, RunRequest, ScenarioError, scenario_from_query
from visionhousing.api.runner import Runner, require_default_sampling, stamp

__all__ = ["register"]


def register(
    app: FastAPI,
    *,
    api: str,
    error: Callable[[int, str, str], Exception],
    context: Callable[[str], Any],
    execute: Callable[[Any], tuple[str, Any]],
    timed: Callable[[str, Any], Any],
    folder: Callable[[str], Path],
) -> None:
    """Add the three endpoints to ``app`` (before its catch-all ``/api/v1/...`` route).

    Args:
        app: The FastAPI app.
        api: The address prefix (``/api/v1``).
        error: Makes the app's JSON error: ``error(status, code, message)``.
        context: State code to its loaded context (raises the app's errors).
        execute: Scenario request to ``(cache key, Result)``.
        timed: Runs a job on the pool within the time limit.
        folder: State code to its data folder.
    """

    @app.get(f"{api}/intake/{{st}}")
    def intake(st: str) -> Response:
        """The state's intake manifest and readiness check."""
        from visionhousing.intake.manifest import build_manifest
        from visionhousing.intake.readiness import readiness

        ctx = context(st)
        path = folder(ctx.state)

        def render() -> bytes:
            manifest = build_manifest(ctx.state, path)
            check = readiness(ctx.state, folder=path, manifest=manifest)
            return service.dumps({**manifest, "readiness": check})

        return Response(
            timed(Runner.key("intake", ctx.state, ctx.data_hash, stamp(path)), render),
            media_type="application/json",
        )

    @app.get(f"{api}/reproduce")
    def reproduce_number(request: Request, id: str = "") -> Response:
        """The command, seed and hashes that reproduce one record id."""
        from visionhousing.intake.reproduce import ReproduceError, parse_id, reproduce

        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        try:
            state, run_name, _path = parse_id(id)
        except ReproduceError as exc:
            raise error(400, "invalid_id", str(exc)) from None
        try:
            return Response(service.dumps(reproduce(id)), media_type="application/json")
        except ReproduceError as exc:
            if "no saved run" not in str(exc):
                raise error(404, "record_not_found", str(exc)) from None
        ctx = context(state)
        reforms = _run_reforms(run_name)
        if reforms is None:
            raise error(404, "run_not_found", f"no saved or known run '{run_name}'")
        key, result = execute(RunRequest(state=ctx.state, reforms=reforms, explain=False))
        source = f"live run (seed {result.seed}, {result.draws} draws; no saved run)"
        data = timed(Runner.key("reproduce", key), result.to_dict)
        try:
            body = reproduce(id, run_data=data, source=source)
        except ReproduceError as exc:
            raise error(404, "record_not_found", str(exc)) from None
        return Response(service.dumps(body), media_type="application/json")

    @app.get(f"{api}/plausibility/{{st}}")
    def plausibility_card(st: str, request: Request) -> Response:
        """Each reform's rate beside the observed rates, with the rule-written sentence."""
        from visionhousing.validation.plausibility import load_settings, plausibility

        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        params = dict(request.query_params)
        if "reforms" in params:
            params["r"] = params.pop("reforms")
        settings = load_settings()
        if not params.get("r"):
            params["r"] = ",".join(settings.get("reforms") or {})
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
        ctx = context(req.state)
        key, result = execute(req)
        # The settings and evidence files are not inputs of the engine run, so their stamp
        # (with the state's files) is in the key: an edited file is never served stale.
        body = timed(
            Runner.key("plausibility", key, stamp(folder(ctx.state))),
            lambda: service.dumps(plausibility(result, settings=settings)),
        )
        return Response(body, media_type="application/json")


def _run_reforms(run_name: str) -> list[str] | None:
    """The reforms of a named run: ``baseline`` none, ``package`` the benchmarks, else itself."""
    from visionhousing.engine.registry import REGISTRY
    from visionhousing.intake.manifest import load_intake_settings

    if run_name == "baseline":
        return []
    if run_name == "package":
        runs = load_intake_settings().get("benchmark_runs", [])
        return [r for r in runs if r in REGISTRY]
    return [run_name] if run_name in REGISTRY else None
