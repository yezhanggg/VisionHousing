"""The ``visionhousing`` command line.

One entry point for every task: check the machine (``doctor``), fetch data
(``fetch``), build a state data pack (``build``), run reforms (``run``), write a
report (``report``), write a place profile (``profile``), serve the interface
(``serve``) and save a state's default results as files for the static site
(``export-static``). All work today except ``run``, which says what it will do once built.

It uses the standard library's ``argparse`` so it adds no dependency.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from visionhousing import __version__

MIN_FREE_GB = 15.0
MIN_PYTHON = (3, 12)
OPTIONAL_KEYS = ("CENSUS_API_KEY",)


@dataclass(frozen=True)
class Check:
    """The result of one doctor check: its name, pass or fail, and a short detail."""

    name: str
    ok: bool
    detail: str
    required: bool = True


def check_python(version: Sequence[int] = sys.version_info) -> Check:
    """Check that Python is 3.12 or newer, because the engine relies on 3.12 features."""
    found = ".".join(str(v) for v in version[:3])
    ok = tuple(version[:2]) >= MIN_PYTHON
    return Check("python", ok, f"{found} (need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)")


def check_uv(which: Callable[[str], str | None] = shutil.which) -> Check:
    """Check that the uv package manager can be found, because setup and runs use it."""
    path = os.environ.get("UV") or which("uv")
    return Check("uv", bool(path), "found" if path else "not found: install uv first")


def check_disk(folder: Path, min_gb: float = MIN_FREE_GB) -> Check:
    """Check free disk space, because data downloads stop below the safety margin."""
    free_gb = shutil.disk_usage(folder).free / 1e9
    ok = free_gb >= min_gb
    return Check("disk", ok, f"{free_gb:.1f} GB free (need {min_gb:.0f} GB)")


def check_keys(env: dict[str, str] | None = None) -> list[Check]:
    """Report whether each optional key is set, without ever showing its value."""
    env = dict(os.environ) if env is None else env
    return [
        Check(
            name.lower(),
            True,
            "present" if env.get(name) else "absent (optional; downloads still work)",
            required=False,
        )
        for name in OPTIONAL_KEYS
    ]


def check_write(folder: Path) -> Check:
    """Check that the data folder can be written, because fetched files are saved there."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".doctor-", delete=True) as fh:
            fh.write(b"ok")
        return Check("write access", True, f"can write to {folder.name}/")
    except OSError as err:
        return Check("write access", False, f"cannot write to {folder.name}/: {err.strerror}")


def run_doctor(checks: list[Check]) -> int:
    """Print the checks as a table and return 1 if any required check failed."""
    width = max(len(c.name) for c in checks)
    print(f"{'check':<{width}}  result  detail")
    for c in checks:
        status = "pass" if c.ok else "FAIL"
        if not c.required:
            status = "info"
        print(f"{c.name:<{width}}  {status:<6}  {c.detail}")
    failed = [c for c in checks if c.required and not c.ok]
    print("doctor: all checks passed" if not failed else f"doctor: {len(failed)} check(s) failed")
    return 1 if failed else 0


def doctor(data_dir: Path) -> int:
    """Run every machine check against ``data_dir`` and print the table."""
    checks = [
        check_python(),
        check_uv(),
        check_disk(data_dir if data_dir.exists() else data_dir.parent),
        *check_keys(),
        check_write(data_dir),
    ]
    return run_doctor(checks)


DATA_COMMANDS = {
    "fetch": "Download every source for a state into the cache (only what is missing)",
    "build": "Build the state data pack from the cache and print its size report",
}


def fetch(state: str, raw: Path) -> int:
    """Download what the state pack needs into ``raw`` and list the cached files.

    Each source keeps only the state's rows; large national files are deleted
    after use. Files already in the cache are reused, so a second run is offline.
    """
    from visionhousing.data import pack

    inputs = pack.load_inputs(state, raw)
    total = 0
    print(f"fetch {inputs.state}: cached inputs in {raw}/")
    for source_id, path in inputs.input_files:
        size = path.stat().st_size
        total += size
        print(f"  {source_id:<24} {path.relative_to(raw).as_posix():<52} {size:>12,}")
    print(f"  {'total':<77} {total:>12,}")
    for note in inputs.notes:
        print(f"  note: {note}")
    return 0


def build(state: str, raw: Path, out: Path) -> int:
    """Build ``out/{STATE}/`` from the cache and print the size report."""
    from visionhousing.data import pack

    report = pack.build_pack(state, raw, out)
    print(pack.size_report(report))
    return 0


STUBS = {
    "run": (
        "Run the baseline and the chosen reforms",
        "will estimate five-year permitted units for the baseline and each reform, as "
        "likely ranges (P10 / P50 / P90), each with its formula, sources and assumptions.",
    ),
}


DEFAULT_REPORT_REFORMS = "adu,fees,parking"


def report(
    state: str,
    reforms: str,
    fmt: str,
    out: Path | None,
    *,
    seed: int,
    draws: int | None,
    run_date: str | None,
) -> int:
    """Run the engine and write its brief (md, html, json) or CSV tables; list the files.

    ``reforms`` is a comma-separated list of reform keys, or ``none`` for the
    baseline only. The same arguments give byte-identical files; only the run
    date printed in the stamp can differ, and ``--date`` fixes it.
    """
    from visionhousing.engine import run
    from visionhousing.ledger import DEFAULT_DRAWS
    from visionhousing.report import write_report

    keys = (
        []
        if reforms.strip().lower() in ("", "none")
        else [k.strip() for k in reforms.split(",") if k.strip()]
    )
    try:
        result = run(state.upper(), keys, seed=seed, draws=draws or DEFAULT_DRAWS)
    except (FileNotFoundError, KeyError, ValueError) as err:
        print(f"report: {err}", file=sys.stderr)
        return 1
    for path in write_report(result, fmt, out, run_date=run_date):
        print(f"report: wrote {path}")
    return 0


def profile(state: str, county: str | None, tract: str | None, fmt: str, out: Path | None) -> int:
    """Write a place profile (a county, or a tract with ``--tract``) as md, html, csv or json.

    Without ``--out`` the profile is printed. See ``docs/modules/profile.md``.
    """
    from visionhousing.profile import PlaceNotFoundError, build_profile, render

    if not county and not tract:
        print("profile: give --county (5 digits) or --tract (11 digits)", file=sys.stderr)
        return 2
    if county and tract and not tract.startswith(county):
        print(f"profile: tract {tract} is not in county {county}", file=sys.stderr)
        return 2
    geoid = tract or county or ""
    try:
        place = build_profile(state, geoid)
    except FileNotFoundError as err:
        print(f"profile: {err}", file=sys.stderr)
        return 1
    except PlaceNotFoundError:
        print(f"profile: no county or tract {geoid} in {state.upper()}", file=sys.stderr)
        return 1
    text = render(place, fmt)
    if out is None:
        sys.stdout.write(text)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"profile: wrote {out}")
    return 0


def _run_date(text: str) -> str | None:
    """Check a ``--date`` value: ``YYYY-MM-DD``, ``today`` or ``none``."""
    import datetime as dt

    if text.lower() == "none":
        return None
    if text.lower() == "today":
        return dt.date.today().isoformat()
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as err:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD, 'today' or 'none'") from err


def serve(host: str, port: int, timeout: float | None = None) -> int:
    """Start the interface (web shell and JSON endpoints) with uvicorn.

    The server banner header is switched off; the app adds its own safety
    headers. ``timeout`` is how long a request waits for a run, in seconds.
    """
    import uvicorn

    from visionhousing.api import create_app

    app = create_app(run_timeout=timeout)
    print(f"serve: VisionHousing at http://{host}:{port}/  (interface: /api/v1/states)")
    uvicorn.run(app, host=host, port=port, server_header=False, log_level="info")
    return 0


def export_static_cmd(
    state: str,
    out: Path,
    *,
    seed: int,
    draws: int | None,
    tracts: str,
    budget: float,
    explain: str = "auto",
) -> int:
    """Save the state's default-scenario results as JSON files for the static site.

    See ``visionhousing.export_static``. Returns 1 if the back end refused a
    request or the folder ends up over the static site's size limit.
    """
    from visionhousing.export_static import ExportError, export_static

    try:
        rep = export_static(
            state,
            out,
            seed=seed,
            draws=draws,
            tracts=tracts,
            tract_budget_mb=budget,
            explain=explain,
        )
    except ExportError as err:
        print(f"export-static: {err}", file=sys.stderr)
        return 1
    print(rep.text())
    return 1 if any("over the" in n and "limit" in n for n in rep.notes) else 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one sub-command per task."""
    parser = argparse.ArgumentParser(
        prog="visionhousing",
        description="Estimate how state housing reforms change five-year housing production.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    doc = sub.add_parser("doctor", help="Check this machine is ready (Python, uv, disk, access)")
    doc.add_argument("--data-dir", type=Path, default=Path("data"), help="data folder to check")
    for name, summary in DATA_COMMANDS.items():
        p = sub.add_parser(name, help=summary)
        p.add_argument("--state", default="PA", help="two-letter state code (default PA)")
        p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
        if name == "build":
            p.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    for name, (summary, _) in STUBS.items():
        p = sub.add_parser(name, help=f"{summary} (not built yet)")
        if name == "run":
            p.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    rep = sub.add_parser("report", help="Write a run's brief (md, html), CSV tables or JSON")
    rep.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    rep.add_argument(
        "--reforms",
        default=DEFAULT_REPORT_REFORMS,
        help=f"comma-separated reform keys, or 'none' (default {DEFAULT_REPORT_REFORMS})",
    )
    rep.add_argument(
        "--format", dest="fmt", choices=["md", "html", "csv", "json"], default="md",
        help="output format (default md)",
    )  # fmt: skip
    rep.add_argument(
        "--out", type=Path, default=None,
        help="file to write (a folder for csv; default outputs/reports/)",
    )  # fmt: skip
    rep.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    rep.add_argument("--draws", type=int, default=None, help="Monte Carlo draws (default 2,000)")
    rep.add_argument(
        "--date", dest="run_date", type=_run_date, default="today",
        help="run date in the stamp: YYYY-MM-DD, 'today' (default) or 'none'",
    )  # fmt: skip
    pro = sub.add_parser("profile", help="Write a place profile for a county or tract")
    pro.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    pro.add_argument("--county", default=None, help="county code, 5 digits (for example 42003)")
    pro.add_argument("--tract", default=None, help="tract code, 11 digits (optional)")
    pro.add_argument(
        "--format", dest="fmt", choices=["md", "html", "csv", "json"], default="md",
        help="output format (default md)",
    )  # fmt: skip
    pro.add_argument("--out", type=Path, default=None, help="file to write (default: print)")
    srv = sub.add_parser("serve", help="Serve the interactive tool on this computer")
    srv.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    srv.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    srv.add_argument(
        "--timeout", type=float, default=None, help="seconds a request may wait for a run"
    )
    exp = sub.add_parser(
        "export-static", help="Save a state's default results as JSON for the static site"
    )
    exp.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    exp.add_argument(
        "--out", type=Path, default=Path("web/public/data"),
        help="data folder (default web/public/data)",
    )  # fmt: skip
    exp.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    exp.add_argument("--draws", type=int, default=None, help="Monte Carlo draws (default 2,000)")
    exp.add_argument(
        "--tracts", choices=["auto", "yes", "no"], default="auto",
        help="tract profiles: auto keeps them only under the budget (default auto)",
    )  # fmt: skip
    exp.add_argument(
        "--tract-budget-mb", type=float, default=4.0,
        help="size budget for tract profiles with --tracts auto (default 4)",
    )  # fmt: skip
    exp.add_argument(
        "--explain", choices=["auto", "yes", "no"], default="auto",
        help="save each run's explanation; auto drops it only if over the 30 MB limit",
    )  # fmt: skip
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "doctor":
        return doctor(args.data_dir)
    if args.command == "fetch":
        return fetch(args.state, args.raw)
    if args.command == "build":
        return build(args.state, args.raw, args.out)
    if args.command == "serve":
        return serve(args.host, args.port, args.timeout)
    if args.command == "export-static":
        return export_static_cmd(
            args.state,
            args.out,
            seed=args.seed,
            draws=args.draws,
            tracts=args.tracts,
            budget=args.tract_budget_mb,
            explain=args.explain,
        )
    if args.command == "profile":
        return profile(args.state, args.county, args.tract, args.fmt, args.out)
    if args.command == "report":
        return report(
            args.state,
            args.reforms,
            args.fmt,
            args.out,
            seed=args.seed,
            draws=args.draws,
            run_date=args.run_date,
        )
    _summary, plan = STUBS[args.command]
    state = getattr(args, "state", None)
    where = f" for {state}" if state else ""
    print(f"{args.command}{where}: not built yet. When built, this command {plan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
