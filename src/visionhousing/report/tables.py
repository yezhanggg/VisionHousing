"""CSV exports: the run's numbers at full precision, one table per file.

Plain-language summary
----------------------
The brief rounds numbers for reading. The CSV files keep the engine's full
precision so an analyst can check or reuse them. Each file follows RFC 4180
(fields with commas, quotes or line breaks are quoted; quotes are doubled),
uses CRLF line ends, and starts with a UTF-8 byte-order mark so spreadsheet
programs read symbols such as the dollar and multiplication signs correctly.

Tables (file name suffix):

- ``summary``: baseline, each reform alone, the package steps and the total;
- ``counties``: every county, for the baseline, each reform and the package;
- ``funnels``: every reform's steps with what each means and its formula;
- ``drivers``: the top inputs from the sensitivity tornado;
- ``assumptions``: every ledger entry the run's numbers depend on;
- ``numbers``: every record in the run with its formula, sources and tier;
- ``run``: the stamp (hashes, seed, draws, date) and the disclaimer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from visionhousing.explain.ids import record_index

__all__ = ["BOM", "TABLE_NAMES", "csv_tables", "to_csv"]

BOM = "﻿"
TABLE_NAMES = ("summary", "counties", "funnels", "drivers", "assumptions", "numbers", "run")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list | tuple):
        value = ";".join(str(v) for v in value)
    text = str(value)
    if any(ch in text for ch in ',"\r\n') or text != text.strip():
        return '"' + text.replace('"', '""') + '"'
    return text


def to_csv(columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> str:
    """Write rows as RFC 4180 CSV text with a byte-order mark and CRLF line ends.

    Args:
        columns: Column names, in order.
        rows: One mapping per row; missing keys are empty cells.

    Returns:
        The CSV text.
    """
    lines = [",".join(_cell(c) for c in columns)]
    lines += [",".join(_cell(r.get(c)) for c in columns) for r in rows]
    return BOM + "\r\n".join(lines) + "\r\n"


def _rng(ref: str, r: Any, **extra: Any) -> dict[str, Any]:
    return {
        "id": ref,
        "measure": r.name,
        "unit": r.unit,
        "p10": r.p10,
        "p50": r.p50,
        "p90": r.p90,
        "value": r.value,
        **extra,
    }


def csv_tables(result: Any, e: Mapping[str, Any], *, run_date: str | None) -> dict[str, str]:
    """Every CSV table of a run.

    Args:
        result: The engine result.
        e: Its explanation (``explain.explain``).
        run_date: Date for the ``run`` table, or ``None``.

    Returns:
        Table name (see :data:`TABLE_NAMES`) to CSV text.
    """
    out: dict[str, str] = {}
    verdicts = e["verdicts"]
    rows = [_rng("baseline", result.baseline)]
    for k, o in result.reforms.items():
        rows.append(_rng(f"reform_results.{k}.added", o.added, verdict=verdicts[k]["word"]))
        rows.append(_rng(f"reform_results.{k}.pct_change", o.pct_change))
    p = result.package
    if p is not None and p.reforms:
        for name in ("sum_of_parts", "overlap", "interaction", "combined", "pct_change"):
            rows.append(_rng(f"package.{name}", getattr(p, name)))
    if result.total is not None:
        rows.append(_rng("total", result.total))
    cols = ["id", "measure", "unit", "p10", "p50", "p90", "value", "verdict"]
    out["summary"] = to_csv(cols, rows)

    names: dict[str, str] = {}
    ctx = getattr(result, "run_context", None)
    if ctx is not None and ctx.state.boundaries:
        for f in ctx.state.boundaries.get("features", ()):
            pr = f.get("properties") or {}
            if pr.get("level") == "county":
                names[str(pr.get("geoid"))] = str(pr.get("name"))
    series: list[tuple[str, str, Mapping[str, Any]]] = [
        ("baseline", "baseline_counties", result.baseline_counties)
    ]
    for k, o in result.reforms.items():
        series.append((f"{k} added", f"reform_results.{k}.counties", o.counties))
    if p is not None and p.reforms:
        series.append(("package added", "package.counties", p.counties))
    rows = []
    for c in result.county_ids:
        for label, base, recs in series:
            if c in recs:
                r = recs[c]
                rows.append(
                    {
                        "county_id": c,
                        "county_name": names.get(c, ""),
                        "series": label,
                        "id": f"{base}.{c}",
                        "unit": r.unit,
                        "p10": r.p10,
                        "p50": r.p50,
                        "p90": r.p90,
                        "value": r.value,
                    }
                )
    out["counties"] = to_csv(
        ["county_id", "county_name", "series", "id", "unit", "p10", "p50", "p90", "value"], rows
    )

    rows = []
    for k, f in e["funnels"].items():
        for n, s in enumerate(f["steps"], start=1):
            rows.append(
                {
                    "reform": k,
                    "step": n,
                    "id": s["ref"],
                    "label": s["label"]["text"],
                    "meaning": s["meaning"]["text"],
                    "kind": s["kind"],
                    "unit": s["unit"],
                    "p10": s["p10"],
                    "p50": s["p50"],
                    "p90": s["p90"],
                    "value": s["value"],
                    "tier": s["tier"],
                    "formula": s["formula"],
                }
            )
    cols = ["reform", "step", "id", "label", "meaning", "kind", "unit"]
    out["funnels"] = to_csv([*cols, "p10", "p50", "p90", "value", "tier", "formula"], rows)

    rows = [
        {
            "reform": k,
            **{c: i[c] for c in i if c not in ("text", "ref")},
            "sentence": i["text"]["text"],
        }
        for k, d in e["drivers"].items()
        for i in d["items"]
    ]
    out["drivers"] = to_csv(
        [
            "reform", "rank", "parameter", "label", "unit", "low", "central", "high",
            "total_low", "total_central", "total_high", "swing", "rho", "sentence",
        ],
        rows,
    )  # fmt: skip

    rows = [
        {
            "id": pid,
            **{c: a.get(c) for c in ("label", "unit", "low", "central", "high", "shape")},
            **{c: a.get(c) for c in ("kind", "status")},
            "used_by": a.get("used_by", []),
            "source_ids": a.get("source_ids", []),
        }
        for pid, a in e["assumptions"].items()
    ]
    out["assumptions"] = to_csv(
        ["id", "label", "unit", "low", "central", "high", "shape", "kind", "status", "used_by",
         "source_ids"],
        rows,
    )  # fmt: skip

    rows = [
        {
            "id": ref,
            "name": r.name,
            "unit": r.unit,
            "p10": r.p10,
            "p50": r.p50,
            "p90": r.p90,
            "value": r.value,
            "tier": r.tier,
            "assumption_ids": list(r.assumption_ids),
            "source_ids": list(r.source_ids),
            "formula": r.formula,
        }
        for ref, r in record_index(result).items()
    ]
    out["numbers"] = to_csv(
        ["id", "name", "unit", "p10", "p50", "p90", "value", "tier", "assumption_ids",
         "source_ids", "formula"],
        rows,
    )  # fmt: skip

    st = e["stamp"]
    rows = [
        {"key": "state", "value": st["state"]},
        {"key": "reforms", "value": st["reforms"]},
        {"key": "code_hash", "value": st["code"]},
        {"key": "data_hash", "value": st["data"]},
        {"key": "assumption_hash", "value": st["assumptions"]},
        {"key": "seed", "value": st["seed"]},
        {"key": "draws", "value": st["draws"]},
        {"key": "cycle", "value": st["cycle"]},
        {"key": "synthetic", "value": st["synthetic"]},
        {"key": "run_date", "value": run_date},
        {"key": "disclaimer", "value": st["disclaimer"]},
    ]
    out["run"] = to_csv(["key", "value"], rows)
    return out
