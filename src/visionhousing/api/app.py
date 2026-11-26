"""The FastAPI app: the JSON interface under ``/api/v1`` and the web shell at ``/``.

Plain-language summary
----------------------
:func:`create_app` builds the app. It serves the built browser front end
(``web/dist/``, made by ``npm run build`` in ``web/``; ``make app`` does both) at
``/`` and these JSON endpoints under ``/api/v1``:

=========================================  ==============================================
``GET  /states``                           Testland plus every state with a built pack
``GET  /states/{st}/summary``              the state's facts and its baseline
``GET  /states/{st}/counties/{fips}``      one county: baseline, each reform, the package
``GET  /reforms`` · ``/reforms/{id}``      reform definitions and their ledger parameters
``GET  /scenarios``                        every scenario (benchmarks, bills, extra scenarios):
                                           kind, description, how to run it, variants
``POST /run``                              one run: ``Result.to_dict()`` + explanation
``POST /compare``                          reforms (or up to six scenarios) side by side
``GET  /ledger``                           the Assumption Ledger, searchable
``GET  /provenance``                       where every dataset comes from
``GET  /validation``                       the backtest skill table and error by kind of place
``GET  /export/{fmt}``                     the run as ``csv``, ``json``, ``html`` (the brief)
                                          or ``hearing`` (the two-page hearing brief)
``GET  /states/{st}/map``                  each county's baseline, added homes, change and map class
``GET  /states/{st}/tract-points``         every tract's id, county and interior point (once)
``GET  /states/{st}/tracts``               every tract under the scenario, column by column: added
                                           homes, each reform, class; county-level reforms in
                                           ``county_only``
``GET  /states/{st}/tract-funnel``         each tract-level reform's map funnel, tract by tract
``GET  /states/{st}/permit-years``         each county's homes permitted per year (past), per
                                           1,000 homes and class, plus the baseline's 2027-2031
``GET  /states/{st}/outlines``             the county outlines (GeoJSON)
``GET  /states/{st}/adu-budget-grid``      the ADU budget-and-target grid and one query on it
                                           (``api.budget_grid``)
``GET  /states/{st}/law-changes?r=``      what one reform or bill changes in each county and
                                           municipality: four answers with citations, dates,
                                           denominators, capacity and units (``api.law_changes``)
``GET  /states/{st}/law-changes/read-next`` bounding runs for the top unread ordinances
                                           (``?r=``; slow the first time, then cached)
``GET  /states/{st}/districts``            each state legislative district under the scenario:
                                           baseline, each reform, package (P10/P50/P90 of
                                           per-draw sums), split share (``api.districts``)
``GET  /evidence?reform=``                 the evidence library entries (all, or one reform's)
``GET  /evidence/states?state=``           cross-state comparison records and the state-by-policy-
                                           type completeness matrix (``evidence_states``)
``GET  /report-card?state=``               the state's data report card
``GET  /places/{st}?q=&level=&county=``   counties (or tracts) whose name contains ``q``
                                           (``county=`` keeps one county's, with ``total``)
``GET  /profile/{st}/{geoid}``             a place profile (county or tract); ``format=``
                                           ``json`` (default), ``md``, ``html`` or ``csv``
``GET  /grades/{st}?r=&level=``            evidence grades A-D for each headline number, state
                                           or county (``api.grades``)
``GET  /trace/{st}?r=&id=``                the step-by-step waterfall behind one number
``GET  /switch/{st}?r=``                   each reform's top three drivers at low / central /
                                           high, for a three-way switch
``GET  /confidence/{st}``                  baseline performance and reform evidence, side by
                                           side, never merged (``api.cards``)
``GET  /lever/{st}?r=&county=``            the counterfactual lever card for one reform
``GET  /implementation/{st}?r=&geoid=``    implementation-status rows with sources and dates
=========================================  ==============================================

If ``web/dist/`` has not been built, every page address answers with a short
plain-language page that says how to build it; the JSON endpoints still work.

Every number comes from the engine unchanged. Requests are checked (see
``api.models``); bad input gets a 400, a body over 64 KB a 413, an unknown
state or county a 404, a full work queue a 503 (with ``Retry-After``), and a run
past the time limit a 504, each as JSON with
an ``error`` code and a plain ``message``. A real state's scenario GET addresses
(county, map, tracts, tract-funnel, export) answer only the default seed and
draws (400 ``default_sampling_only``; ``POST /run`` takes any). Every GET looks up
its rendered reply in the cache before it asks for the engine run behind it.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from visionhousing import __version__
from visionhousing.api import service, views
from visionhousing.api.models import (
    LIMITS,
    CompareRequest,
    RunRequest,
    ScenarioError,
    scenario_from_query,
)
from visionhousing.api.runner import (
    ContextStore,
    MemoryWatchdog,
    Runner,
    RunnerBusyError,
    RunTimeoutError,
    _signature,
    is_default_scenario,
    require_default_sampling,
    stamp,
)
from visionhousing.api.security import (
    BodyLimit,
    SecurityHeaders,
    content_security_policy,
    page_hashes,
)
from visionhousing.api.status_view import status_views
from visionhousing.engine.context import PACKS_DIR
from visionhousing.engine.core import Result, run
from visionhousing.ledger import LedgerError, load_ledger
from visionhousing.ledger.loader import ROOT
from visionhousing.paths import state_code

__all__ = ["API", "ApiError", "create_app"]

API = "/api/v1"
WEB_DIR = ROOT / "web" / "dist"
NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>VisionHousing: interface not built</title>
</head><body style="font-family: system-ui, sans-serif; max-width: 36rem; margin: 15vh auto;
padding: 0 1rem; line-height: 1.5">
<h1>The interface is not built yet</h1>
<p>The engine is running: its JSON interface is at <a href="/api/v1/states">/api/v1/states</a>.
The web pages are made by a build step that has not been run on this computer.</p>
<p>Stop this server and run <code>make app</code>. It builds the pages (Vite, in
<code>web/</code>) and starts the tool again. Or build them by hand:
<code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code>.</p>
</body></html>
"""
DEFAULT_TIMEOUT = float(os.environ.get("VISIONHOUSING_RUN_TIMEOUT", "120"))
LOG = logging.getLogger("visionhousing.api")


class ApiError(Exception):
    """An error with a status code, an error code and a plain message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _json(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(service.dumps(data), status, headers, media_type="application/json")


def _error(status: int, code: str, message: str, detail: Any = None) -> Response:
    body: dict[str, Any] = {"error": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return _json(body, status)


def _plain_errors(exc: ValidationError | RequestValidationError) -> list[dict[str, Any]]:
    """Return validation errors without echoing the input back."""
    return [
        {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))}
        for e in exc.errors()
    ][:20]


class SpaFiles(StaticFiles):
    """The built front end. An unknown page address (no file extension) gets ``index.html``.

    The screens use hash routes (``/#/place/42003``), so this is only a safety net for a
    hand-typed address such as ``/place``; a missing file such as ``/x.js`` stays a 404.
    """

    async def get_response(self, path: str, scope: Any) -> Any:
        """Serve the file, or the page itself for an extension-less address."""
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


def create_app(
    *,
    web_dir: Path | None = None,
    packs_dir: Path | None = None,
    run_timeout: float | None = None,
    workers: int = 2,
    cache_size: int = 4,
    result_mb: float | None = None,
    any_sampling: bool = False,
) -> Any:
    """Build the app.

    Args:
        web_dir: Folder of the built front end (default ``web/dist/``).
        packs_dir: Folder of the built state packs (default ``data/packs/``).
        run_timeout: Seconds a request waits for a run (default 120, or the
            ``VISIONHOUSING_RUN_TIMEOUT`` environment variable).
        workers: Threads that run the engine.
        cache_size: Whole run results kept in memory, at most (rendered replies have a
            byte budget of their own; see ``api.runner``).
        result_mb: Megabytes of whole results kept (default 3,072, or the
            ``VISIONHOUSING_RESULT_MB`` environment variable; ``math.inf`` for no
            byte limit, so only ``cache_size`` counts).
        any_sampling: Let a real state's scenario GET addresses take any seed and
            draws. Off for the server; the static exporter, which asks for its own
            seed and draws, turns it on.

    Returns:
        The ASGI app: the FastAPI app wrapped in the body-size limit and the
        security headers (outermost, so every response gets them). The FastAPI
        object itself is at ``.fastapi``.
    """
    web = Path(web_dir) if web_dir is not None else WEB_DIR
    contexts = ContextStore(Path(packs_dir) if packs_dir is not None else PACKS_DIR)
    runner = Runner(
        workers,
        DEFAULT_TIMEOUT if run_timeout is None else run_timeout,
        cache_size,
        result_budget=None if result_mb is None else result_mb * 1024 * 1024,
    )
    watchdog = MemoryWatchdog(runner)
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/json", ".json")
    mimetypes.add_type("application/geo+json", ".geojson")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        watchdog.start()  # only a real server runs the lifespan; in-memory callers do not
        yield
        watchdog.stop()
        runner.shutdown()

    app = FastAPI(
        title="VisionHousing",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API}/openapi.json",
        lifespan=lifespan,
    )
    app.state.runner = runner
    app.state.watchdog = watchdog
    app.state.contexts = contexts
    profile_contexts: dict[str, tuple[tuple, Any]] = {}

    # ------------------------------------------------------------ errors

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> Response:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RunnerBusyError)
    async def _busy(_req: Request, exc: RunnerBusyError) -> Response:
        reply = _error(503, "busy", str(exc))
        reply.headers["Retry-After"] = str(exc.retry_after)
        return reply

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_req: Request, exc: RequestValidationError) -> Response:
        return _error(400, "invalid_request", "the request is not valid", _plain_errors(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(req: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return _error(404, "not_found", f"nothing at {req.url.path[:80]}")
        if exc.status_code == 405:
            return _error(405, "method_not_allowed", "this address does not accept that method")
        return _error(exc.status_code, "http_error", str(exc.detail))

    # ------------------------------------------------------------ helpers

    def checked_state(state: str) -> str:
        try:
            return state_code(state)
        except ValueError:
            raise ApiError(400, "invalid_state", "state must be a two-letter code") from None

    def context(state: str):
        code = checked_state(state)
        try:
            return contexts.get(code)
        except FileNotFoundError as exc:
            raise ApiError(404, "state_not_built", str(exc)) from None

    def run_key(req: RunRequest) -> tuple[Any, Any, str]:
        """The state context, the reforms and the cache key of a run; runs nothing."""
        ctx = context(req.state)
        reforms = req.reform_arg()
        key = Runner.key(
            "run",
            ctx.state,
            ctx.data_hash,
            ctx.calibration_hash,
            ctx.ledger.hash(),
            reforms,
            req.overrides,
            req.seed,
            req.draws,
            req.cycle,
            *(() if req.market is None else (("market", req.market),)),
            *((("capacity_screen", True),) if req.capacity_screen else ()),
        )
        return ctx, reforms, key

    def execute(req: RunRequest) -> tuple[str, Result]:
        ctx, reforms, key = run_key(req)

        def job() -> Result:
            return run(
                ctx.state,
                reforms,
                req.overrides,
                seed=req.seed,
                draws=req.draws,
                cycle=req.cycle,
                context=ctx,
                use_cache=False,
                market=req.market,
                capacity_screen=req.capacity_screen,
            )

        return key, timed(key, job, pin=is_default_scenario(req))

    def timed(key: str, job: Any, pin: bool = False) -> Any:
        """Run ``job`` on the pool within the time limit; map errors to clear JSON."""
        try:
            return runner.submit(key, job, pin=pin)
        except RunnerBusyError:
            raise
        except RunTimeoutError as exc:
            raise ApiError(
                504,
                "run_timeout",
                f"{exc}. A run already started continues in the background; ask again "
                "while it runs to pick it up (a custom scenario's result is not kept once "
                "no one waits for it), or use fewer draws or fewer reforms.",
            ) from None
        except LedgerError as exc:
            raise ApiError(400, "invalid_override", str(exc)) from None
        except KeyError as exc:
            raise ApiError(400, "unknown_reform", str(exc.args[0] if exc.args else exc)) from None
        except ValueError as exc:
            raise ApiError(400, "invalid_scenario", str(exc)) from None
        except Exception as exc:
            LOG.exception("run failed", exc_info=exc)
            raise ApiError(500, "run_failed", "the engine hit an unexpected error") from None

    def run_json(
        key: str, result: Result, with_explanation: bool = True, drivers: bool = False
    ) -> bytes:
        """The run as JSON bytes (``to_dict`` plus the explanation), made once and cached."""

        def render() -> bytes:
            if with_explanation:
                payload, status = service.explanation(result, drivers=drivers)
            else:
                payload, status = None, "skipped"
            reply = {**result.to_dict(), "explanation": payload, "explanation_status": status}
            # The transit status ladder as shown text (api.status_view), for every screen.
            reply["status_views"] = status_views(reply)
            return service.dumps(reply)

        return timed(Runner.key("json", key, with_explanation, drivers), render)

    def scenario_query(
        request: Request, state: str | None = None, aliases: dict[str, str] | None = None
    ) -> RunRequest:
        """The scenario of a GET address; a real state answers only the default sampling."""
        req = from_query(request, state, aliases)
        refused = None if any_sampling else require_default_sampling(req)
        if refused:
            raise ApiError(400, "default_sampling_only", refused)
        return req

    def cached_reply(req: RunRequest, kind: str, make: Any, *extra: Any) -> Any:
        """Return the rendered reply ``kind`` of the run ``req``.

        Looked up in the cache first, so a cached reply never needs its run; else
        the run (cached or new) is rendered with ``make(result)`` and kept.
        """
        _ctx, _reforms, key = run_key(req)
        reply_key = Runner.key(kind, key, *extra)
        found, body = runner.peek(reply_key)
        if found:
            return body
        _key, result = execute(req)
        return timed(reply_key, lambda: make(result))

    def from_query(
        request: Request, state: str | None = None, aliases: dict[str, str] | None = None
    ) -> RunRequest:
        if len(request.url.query) > LIMITS["query_chars"]:
            raise ApiError(400, "query_too_long", "the address is too long")
        params = dict(request.query_params)
        for alias, name in (aliases or {}).items():
            if alias in params:
                if name in params:
                    raise ApiError(400, "invalid_query", f"give {name} or {alias}, not both")
                params[name] = params.pop(alias)
        try:
            return scenario_from_query(params, state)
        except ScenarioError as exc:
            raise ApiError(400, "invalid_query", str(exc)) from None
        except ValidationError as exc:
            message = "; ".join(e["msg"] for e in _plain_errors(exc))
            raise ApiError(400, "invalid_query", message) from None

    # ------------------------------------------------------------ endpoints

    @app.get(f"{API}/states")
    def states() -> Response:
        """Testland plus every state with a built pack."""
        return _json({"states": service.available_states(contexts.packs_dir)})

    @app.get(f"{API}/states/{{st}}/summary")
    def state_summary(st: str) -> Response:
        """The state's facts and its baseline at the default seed and draws."""
        ctx = context(st)
        folder = contexts.folder(ctx.state)
        body = cached_reply(
            RunRequest(state=ctx.state), "summary",
            lambda result: service.dumps(service.state_summary(ctx, folder, result)),
            stamp(folder),
        )  # fmt: skip
        return Response(body, media_type="application/json")

    @app.get(f"{API}/states/{{st}}/counties/{{fips}}")
    def county(st: str, fips: str, request: Request) -> Response:
        """One county under the scenario in the query (``r``, ``a``, ``seed``, ...)."""
        if not fips.isdigit() or len(fips) != 5:
            raise ApiError(400, "invalid_county", "county must be a five-digit FIPS code")
        req = scenario_query(request, st)
        ctx, _reforms, key = run_key(req)
        folder = contexts.folder(ctx.state)
        reply_key = Runner.key("county", key, fips, stamp(folder))
        found, body = runner.peek(reply_key)
        if not found:
            _key, result = execute(req)
            try:
                data = service.county_detail(ctx, folder, result, fips)
            except KeyError:
                raise ApiError(
                    404, "county_not_found", f"no county {fips} in {ctx.state}"
                ) from None
            data["tract_results"] = views.county_tracts(result, fips)
            body = service.dumps(data)
            runner.put(reply_key, body)
        return Response(body, media_type="application/json")

    @app.get(f"{API}/states/{{st}}/map")
    def county_map(st: str, request: Request) -> Response:
        """Each county under the scenario in the query: baseline, added, change, map class."""
        req = scenario_query(request, st)
        ctx = context(req.state)
        folder = contexts.folder(ctx.state)

        def make(result: Result) -> bytes:
            facts = service._county_facts(folder)
            there = folder if folder.is_dir() else None
            return service.dumps(views.county_map(ctx, facts, result, there))

        return Response(cached_reply(req, "map_json", make), media_type="application/json")

    @app.get(f"{API}/states/{{st}}/tract-points")
    def tract_points(st: str) -> Response:
        """Every tract's id, county and interior point (does not depend on the scenario)."""
        ctx = context(st)
        key = Runner.key("tract_points", ctx.state, ctx.data_hash)
        body = timed(key, lambda: service.dumps(views.state_tract_points(ctx)))
        return Response(body, media_type="application/json")

    def tract_reply(kind: str, view: Any, st: str, request: Request) -> Response:
        """A tract reply for the scenario in the query, made once per run and cached."""
        req = scenario_query(request, st, aliases={"reforms": "r"})
        ctx = context(req.state)
        body = cached_reply(req, kind, lambda result: service.dumps(view(result, ctx)))
        return Response(body, media_type="application/json")

    @app.get(f"{API}/states/{{st}}/tracts")
    def state_tracts(st: str, request: Request) -> Response:
        """Every tract under the scenario in the query (``r`` or ``reforms``, ``a``, ...).

        Column by column, keyed by geoid: homes added by the tract-level reforms,
        each reform's P50, class; county-level reforms in ``county_only``.
        """
        return tract_reply("tracts", views.state_tracts, st, request)

    @app.get(f"{API}/states/{{st}}/tract-funnel")
    def tract_funnel(st: str, request: Request) -> Response:
        """Each tract-level reform's map funnel (run alone), per tract, county and state."""
        return tract_reply("tract_funnel", views.state_tract_funnel, st, request)

    @app.get(f"{API}/states/{{st}}/permit-years")
    def permit_years(st: str) -> Response:
        """Each county's homes permitted per year (past) and the baseline's years 2027-2031."""
        ctx = context(st)
        folder = contexts.folder(ctx.state)
        body = cached_reply(
            RunRequest(state=ctx.state), "permit_years",
            lambda result: service.dumps(
                views.county_permit_years(ctx, service._county_facts(folder), result)
            ),
        )  # fmt: skip
        return Response(body, media_type="application/json")

    @app.get(f"{API}/states/{{st}}/outlines")
    def outlines(st: str) -> Response:
        """The state's county outlines as GeoJSON (empty when the pack has none)."""
        return _json(views.county_outlines(context(st)))

    @app.get(f"{API}/evidence")
    def evidence(reform: str | None = None) -> Response:
        """The evidence library: every entry, or one reform's."""
        if reform is not None and (len(reform) > 40 or not reform.replace("_", "").isalnum()):
            raise ApiError(400, "invalid_reform", "reform must be a short id")
        return _json(views.evidence_cards(reform))

    @app.get(f"{API}/report-card")
    def report_card(state: str = "TL") -> Response:
        """The state's data report card: each model input used, available, assumed or missing."""
        ctx = context(state)
        key = Runner.key("report_card", ctx.state, ctx.data_hash)
        folder = contexts.folder(ctx.state)
        return _json(runner.submit(key, lambda: views.pack_report_card(folder)))

    @app.get(f"{API}/reforms")
    def reforms() -> Response:
        """Every runnable reform."""
        return _json({"reforms": service.reform_list()})

    @app.get(f"{API}/scenarios")
    def scenarios() -> Response:
        """Every scenario (baseline, benchmarks, bills, extra scenarios) with a description."""
        return _json({"scenarios": service.scenario_list()})

    @app.get(f"{API}/reforms/{{key}}")
    def reform(key: str) -> Response:
        """One reform's definition and its ledger parameters."""
        if len(key) > 40:
            raise ApiError(400, "invalid_reform", "reform id is too long")
        try:
            return _json(service.reform_detail(key))
        except KeyError:
            raise ApiError(404, "reform_not_found", f"no reform {key!r}") from None

    @app.post(f"{API}/run")
    def run_endpoint(req: RunRequest) -> Response:
        """One run: the engine's ``Result.to_dict()`` plus the explanation payload."""
        _ctx, _reforms, key = run_key(req)
        found, body = runner.peek(Runner.key("json", key, req.explain, req.drivers))
        if not found:
            body = run_json(key, execute(req)[1], req.explain, req.drivers)
        return Response(body, media_type="application/json")

    @app.post(f"{API}/compare")
    def compare(req: CompareRequest) -> Response:
        """Each reform alone and the package, or several scenarios side by side."""
        if req.scenarios is None:
            _key, result = execute(req)
            columns = [service.compare_rows("this scenario", result)]
        else:
            columns = []
            for item in req.scenarios:
                one = RunRequest(
                    state=req.state,
                    reforms=item.reforms,
                    overrides=item.overrides,
                    preset=item.preset,
                    market=item.market,
                    capacity_screen=item.capacity_screen,
                    seed=req.seed,
                    draws=req.draws,
                )
                _key, result = execute(one)
                columns.append(service.compare_rows(item.label, result))
        return _json({"state": req.state, "seed": req.seed, "draws": req.draws, "columns": columns})

    @app.get(f"{API}/ledger")
    def ledger(
        q: str = "",
        used_by: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        state: str | None = None,
    ) -> Response:
        """The Assumption Ledger, filtered by a search (``q``) and fields."""
        if len(q) > LIMITS["search_chars"]:
            raise ApiError(400, "query_too_long", "the search is too long")
        led = context(state).ledger if state else load_ledger()
        return _json(service.ledger_entries(led, q, used_by, kind, status))

    @app.get(f"{API}/provenance")
    def provenance(state: str | None = None) -> Response:
        """Every source's version, access date, licence, cleaning and limits."""
        folder = contexts.folder(context(state).state) if state else None
        return _json(service.provenance(folder))

    @app.get(f"{API}/validation")
    def validation(state: str = "TL") -> Response:
        """The baseline backtest: each candidate rule's error and interval coverage."""
        ctx = context(state)
        key = Runner.key("validation", ctx.state, ctx.data_hash, ctx.ledger.hash())

        def build() -> dict:
            out = service.validation_table(ctx, contexts.folder(ctx.state))
            # The Method screen's plain rule list on the contest's own measure.
            return {**out, "contest_view": views.contest_view(out)}

        return _json(runner.submit(key, build))

    @app.get(f"{API}/export/{{fmt}}")
    def export(fmt: str, request: Request) -> Response:
        """The scenario in the query (``s``, ``r``, ``a``, ...) as a CSV, JSON or HTML file."""
        if fmt == "hearing.css":
            # The hearing brief's style rules (the page links them; no inline styles).
            from visionhousing.report.hearing import CSS

            return Response(CSS + "\n", media_type="text/css; charset=utf-8")
        if fmt not in ("csv", "json", "html", "hearing"):
            raise ApiError(400, "invalid_format", "format must be csv, json, html or hearing")
        req = scenario_query(request)
        ctx = context(req.state)
        # The run's state and data hash are the context's (engine.run copies them).
        name = f"visionhousing-{ctx.state}-{ctx.data_hash[:8]}-seed{req.seed}.{fmt}"
        disposition = {"content-disposition": f'attachment; filename="{name}"'}
        if fmt == "hearing":
            # The two-page hearing brief (report.hearing), shown in the browser to print.
            from visionhousing.report.hearing import hearing_reply

            folder = contexts.folder(ctx.state)
            body = cached_reply(req, "hearing", lambda r: hearing_reply(ctx, folder, r))
            return Response(body, media_type="text/html; charset=utf-8")
        if fmt == "csv":
            body = cached_reply(req, "csv", service.export_csv)
            return Response(body, headers=disposition, media_type="text/csv; charset=utf-8")
        if fmt == "html":
            body = cached_reply(req, "html", service.export_html)
            return Response(body, headers=disposition, media_type="text/html; charset=utf-8")
        _ctx, _reforms, key = run_key(req)
        found, body = runner.peek(Runner.key("json", key, True, False))
        if not found:
            body = run_json(key, execute(req)[1])
        return Response(body, headers=disposition, media_type="application/json")

    def profile_context(state: str):
        """The state's profile context (small pack tables), loaded once per pack version."""
        from visionhousing.profile import load_profile_context

        code = checked_state(state)
        folder = contexts.folder(code)
        if not folder.is_dir():
            raise ApiError(
                404, "state_not_built", f"no state pack for {code}; build it with `make data`"
            )
        sig = _signature(folder)
        held = profile_contexts.get(code)
        if held is None or held[0] != sig:
            held = (sig, load_profile_context(code, folder=folder))
            profile_contexts[code] = held
        return held[1]

    @app.get(f"{API}/places/{{st}}")
    def places(
        st: str, q: str = "", level: str = "county", limit: int = 25, county: str = ""
    ) -> Response:
        """Counties (or tracts) of a state whose name or code contains ``q``.

        ``county`` (a 5-digit code) keeps only the places in that county; the reply then
        also says how many matched in all (``total``), so a caller can tell a cut list.
        """
        from visionhousing.profile import list_places

        if len(q) > LIMITS["search_chars"]:
            raise ApiError(400, "query_too_long", "the search is too long")
        if level not in ("county", "tract"):
            raise ApiError(400, "invalid_level", "level must be county or tract")
        if not 1 <= limit <= 500:
            raise ApiError(400, "invalid_limit", "limit must be between 1 and 500")
        if county and not (county.isdigit() and len(county) == 5):
            raise ApiError(400, "invalid_county", "county must be a 5-digit code")
        ctx = profile_context(st)
        if not county:
            return _json(
                {"state": ctx.state, "level": level, "places": list_places(ctx, q, level=level,
                                                                           limit=limit)}
            )  # fmt: skip
        found = [p for p in list_places(ctx, q, level=level, limit=10**9) if p["county"] == county]
        return _json(
            {"state": ctx.state, "level": level, "county": county, "total": len(found),
             "places": found[:limit]}
        )  # fmt: skip

    @app.get(f"{API}/profile/{{st}}/{{geoid}}")
    def place_profile(st: str, geoid: str, format: str = "json") -> Response:
        """One county (5 digits) or tract (11 digits) profile, as JSON or a download."""
        from visionhousing.profile import PlaceNotFoundError, build_profile, render

        if not geoid.isdigit() or len(geoid) not in (5, 11):
            raise ApiError(400, "invalid_place", "place must be a 5-digit county or 11-digit tract")
        if format not in ("json", "md", "html", "csv"):
            raise ApiError(400, "invalid_format", "format must be json, md, html or csv")
        ctx = profile_context(st)
        try:
            prof = build_profile(ctx.state, geoid, ctx=ctx)
        except PlaceNotFoundError:
            raise ApiError(
                404, "place_not_found", f"no county or tract {geoid} in {ctx.state}"
            ) from None
        if format == "json":
            return _json(prof.to_dict())
        media = {"md": "text/markdown", "html": "text/html", "csv": "text/csv"}[format]
        name = f"visionhousing-profile-{ctx.state}-{geoid}.{format}"
        return Response(
            render(prof, format),
            headers={"content-disposition": f'attachment; filename="{name}"'},
            media_type=f"{media}; charset=utf-8",
        )

    from visionhousing.api.evidence_states import make_router as evidence_states_router

    app.include_router(evidence_states_router(API, _error))

    from visionhousing.api import districts as districts_api

    districts_api.register(
        app, api=API, error=ApiError, context=context, execute=execute, timed=timed,
        folder=contexts.folder,
    )  # fmt: skip

    from visionhousing.api import intake as intake_api

    intake_api.register(
        app, api=API, error=ApiError, context=context, execute=execute, timed=timed,
        folder=contexts.folder,
    )  # fmt: skip

    from visionhousing.api import grades as grades_api

    grades_api.register(
        app, api=API, error=ApiError, context=context, execute=execute, timed=timed,
        folder=contexts.folder, data_dir=web / "data",
    )  # fmt: skip

    service.register_comparisons(app, api=API, error=ApiError, context=context, timed=timed)

    from visionhousing.api import budget_grid as budget_grid_api

    budget_grid_api.register(app, api=API, error=ApiError, context=context, timed=timed)

    from visionhousing.api import cards as cards_api

    cards_api.register(
        app, api=API, error=ApiError, context=context, execute=execute, timed=timed,
        folder=contexts.folder,
    )  # fmt: skip

    from visionhousing.api import answers as answers_api

    answers_api.register(app, api=API, error=ApiError)

    from visionhousing.api import law_changes as law_changes_api

    law_changes_api.register(
        app, api=API, error=ApiError, context=context, execute=execute, timed=timed,
        folder=contexts.folder,
    )  # fmt: skip

    @app.api_route(f"{API}/{{rest:path}}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(rest: str) -> Response:
        """Unknown interface addresses get a JSON 404, never the web page."""
        raise ApiError(404, "not_found", "no such interface address")

    if (web / "index.html").is_file():
        app.mount("/", SpaFiles(directory=web, html=True), name="web")
    else:

        @app.get("/{rest:path}", include_in_schema=False)
        def not_built(rest: str) -> Response:
            """No build yet: say so in plain words instead of a bare 404."""
            return HTMLResponse(NOT_BUILT, status_code=503)

    csp = content_security_policy(page_hashes(web))
    wrapped = SecurityHeaders(BodyLimit(app, LIMITS["body_bytes"]), csp, api_prefix=f"{API}/")
    wrapped.fastapi = app  # type: ignore[attr-defined]
    return wrapped
