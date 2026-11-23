"""The FastAPI app: the JSON interface under ``/api/v1`` and the web shell at ``/``.

Plain-language summary
----------------------
:func:`create_app` builds the app. It serves the files in ``web/`` (the
browser front end) at ``/`` and these JSON endpoints under ``/api/v1``:

=========================================  ==============================================
``GET  /states``                           Testland plus every state with a built pack
``GET  /states/{st}/summary``              the state's facts and its baseline
``GET  /states/{st}/counties/{fips}``      one county: baseline, each reform, the package
``GET  /reforms`` · ``/reforms/{id}``      reform definitions and their ledger parameters
``POST /run``                              one run: ``Result.to_dict()`` + explanation
``POST /compare``                          reforms (or up to six scenarios) side by side
``GET  /ledger``                           the Assumption Ledger, searchable
``GET  /provenance``                       where every dataset comes from
``GET  /validation``                       the baseline backtest skill table
``GET  /export/{fmt}``                     the run as ``csv``, ``json`` or ``html`` (the brief)
``GET  /places/{st}?q=&level=``            counties (or tracts) whose name contains ``q``
``GET  /profile/{st}/{geoid}``             a place profile (county or tract); ``format=``
                                           ``json`` (default), ``md``, ``html`` or ``csv``
=========================================  ==============================================

Every number comes from the engine unchanged. Requests are checked (see
``api.models``); bad input gets a 400, a body over 64 KB a 413, an unknown
state or county a 404, and a run past the time limit a 504, each as JSON with
an ``error`` code and a plain ``message``.
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
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from visionhousing import __version__
from visionhousing.api import service
from visionhousing.api.models import (
    LIMITS,
    CompareRequest,
    RunRequest,
    ScenarioError,
    scenario_from_query,
)
from visionhousing.api.runner import ContextStore, Runner, RunTimeoutError, _signature
from visionhousing.api.security import (
    BodyLimit,
    SecurityHeaders,
    content_security_policy,
    page_hashes,
)
from visionhousing.engine.context import PACKS_DIR
from visionhousing.engine.core import Result, run
from visionhousing.ledger import LedgerError, load_ledger
from visionhousing.ledger.loader import ROOT

__all__ = ["API", "ApiError", "create_app"]

API = "/api/v1"
WEB_DIR = ROOT / "web"
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


def create_app(
    *,
    web_dir: Path | None = None,
    packs_dir: Path | None = None,
    run_timeout: float | None = None,
    workers: int = 2,
    cache_size: int = 32,
) -> Any:
    """Build the app.

    Args:
        web_dir: Folder of the front end (default ``web/``).
        packs_dir: Folder of the built state packs (default ``data/packs/``).
        run_timeout: Seconds a request waits for a run (default 120, or the
            ``VISIONHOUSING_RUN_TIMEOUT`` environment variable).
        workers: Threads that run the engine.
        cache_size: Run results kept in memory.

    Returns:
        The ASGI app: the FastAPI app wrapped in the body-size limit and the
        security headers (outermost, so every response gets them). The FastAPI
        object itself is at ``.fastapi``.
    """
    web = Path(web_dir) if web_dir is not None else WEB_DIR
    contexts = ContextStore(Path(packs_dir) if packs_dir is not None else PACKS_DIR)
    runner = Runner(workers, DEFAULT_TIMEOUT if run_timeout is None else run_timeout, cache_size)
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/json", ".json")
    mimetypes.add_type("application/geo+json", ".geojson")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
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
    app.state.contexts = contexts
    profile_contexts: dict[str, tuple[tuple, Any]] = {}

    # ------------------------------------------------------------ errors

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> Response:
        return _error(exc.status, exc.code, exc.message)

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

    def context(state: str):
        code = state.upper()
        if len(code) != 2 or not code.isalpha():
            raise ApiError(400, "invalid_state", "state must be a two-letter code")
        try:
            return contexts.get(code)
        except FileNotFoundError as exc:
            raise ApiError(404, "state_not_built", str(exc)) from None

    def execute(req: RunRequest) -> tuple[str, Result]:
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
        )

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
            )

        return key, timed(key, job)

    def timed(key: str, job: Any) -> Any:
        """Run ``job`` on the pool within the time limit; map errors to clear JSON."""
        try:
            return runner.submit(key, job)
        except RunTimeoutError as exc:
            raise ApiError(
                504,
                "run_timeout",
                f"{exc}. The run continues in the background; ask again in a minute "
                "for the result, or use fewer draws or fewer reforms.",
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
            return service.dumps(
                {**result.to_dict(), "explanation": payload, "explanation_status": status}
            )

        return timed(Runner.key("json", key, with_explanation, drivers), render)

    def from_query(request: Request, state: str | None = None) -> RunRequest:
        if len(request.url.query) > LIMITS["query_chars"]:
            raise ApiError(400, "query_too_long", "the address is too long")
        try:
            return scenario_from_query(dict(request.query_params), state)
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
        _key, result = execute(RunRequest(state=ctx.state))
        return _json(service.state_summary(ctx, contexts.folder(ctx.state), result))

    @app.get(f"{API}/states/{{st}}/counties/{{fips}}")
    def county(st: str, fips: str, request: Request) -> Response:
        """One county under the scenario in the query (``r``, ``a``, ``seed``, ...)."""
        if not fips.isdigit() or len(fips) != 5:
            raise ApiError(400, "invalid_county", "county must be a five-digit FIPS code")
        req = from_query(request, st)
        _key, result = execute(req)
        ctx = context(req.state)
        try:
            data = service.county_detail(ctx, contexts.folder(ctx.state), result, fips)
        except KeyError:
            raise ApiError(404, "county_not_found", f"no county {fips} in {ctx.state}") from None
        return _json(data)

    @app.get(f"{API}/reforms")
    def reforms() -> Response:
        """Every runnable reform."""
        return _json({"reforms": service.reform_list()})

    @app.get(f"{API}/reforms/{{key}}")
    def reform(key: str) -> Response:
        """One reform's definition and its ledger parameters."""
        if len(key) > 32:
            raise ApiError(400, "invalid_reform", "reform id is too long")
        try:
            return _json(service.reform_detail(key))
        except KeyError:
            raise ApiError(404, "reform_not_found", f"no reform {key!r}") from None

    @app.post(f"{API}/run")
    def run_endpoint(req: RunRequest) -> Response:
        """One run: the engine's ``Result.to_dict()`` plus the explanation payload."""
        key, result = execute(req)
        return Response(
            run_json(key, result, req.explain, req.drivers), media_type="application/json"
        )

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
        return _json(runner.submit(key, lambda: service.validation_table(ctx)))

    @app.get(f"{API}/export/{{fmt}}")
    def export(fmt: str, request: Request) -> Response:
        """The scenario in the query (``s``, ``r``, ``a``, ...) as a CSV, JSON or HTML file."""
        if fmt not in ("csv", "json", "html"):
            raise ApiError(400, "invalid_format", "format must be csv, json or html")
        req = from_query(request)
        key, result = execute(req)
        name = f"visionhousing-{result.state}-{result.hashes.data[:8]}-seed{result.seed}.{fmt}"
        disposition = {"content-disposition": f'attachment; filename="{name}"'}
        if fmt == "csv":
            body = timed(Runner.key("csv", key), lambda: service.export_csv(result))
            return Response(body, headers=disposition, media_type="text/csv; charset=utf-8")
        if fmt == "html":
            body = timed(Runner.key("html", key), lambda: service.export_html(result))
            return Response(body, headers=disposition, media_type="text/html; charset=utf-8")
        return Response(run_json(key, result), headers=disposition, media_type="application/json")

    def profile_context(state: str):
        """The state's profile context (small pack tables), loaded once per pack version."""
        from visionhousing.profile import load_profile_context

        code = state.upper()
        if len(code) != 2 or not code.isalpha():
            raise ApiError(400, "invalid_state", "state must be a two-letter code")
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
    def places(st: str, q: str = "", level: str = "county", limit: int = 25) -> Response:
        """Counties (or tracts) of a state whose name or code contains ``q``."""
        from visionhousing.profile import list_places

        if len(q) > LIMITS["search_chars"]:
            raise ApiError(400, "query_too_long", "the search is too long")
        if level not in ("county", "tract"):
            raise ApiError(400, "invalid_level", "level must be county or tract")
        if not 1 <= limit <= 500:
            raise ApiError(400, "invalid_limit", "limit must be between 1 and 500")
        ctx = profile_context(st)
        return _json(
            {"state": ctx.state, "level": level, "places": list_places(ctx, q, level=level,
                                                                       limit=limit)}
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

    @app.api_route(f"{API}/{{rest:path}}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(rest: str) -> Response:
        """Unknown interface addresses get a JSON 404, never the web page."""
        raise ApiError(404, "not_found", "no such interface address")

    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")

    csp = content_security_policy(page_hashes(web))
    wrapped = SecurityHeaders(BodyLimit(app, LIMITS["body_bytes"]), csp, api_prefix=f"{API}/")
    wrapped.fastapi = app  # type: ignore[attr-defined]
    return wrapped
