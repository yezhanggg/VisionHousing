"""Load and check every comparison record, study, discovery candidate and tracker.

Plain-language summary
----------------------
:func:`load_panel` reads ``assumptions/evidence/states/``:

- every ``<state>.yaml`` file and ``studies.yaml`` hold ``records:`` lists of
  :class:`~visionhousing.evidence.schema.ComparisonRecord`;
- ``discovery.yaml`` holds ``trackers:`` (each tracker and what its terms allow)
  and ``entries:`` (candidate reforms not yet read in official text).

It checks each entry against the schema, refuses a duplicate record id, and
refuses a record filed under the wrong state (a Vermont record must be in
``vt.yaml``; multi-state studies, ``state: US``, go in ``studies.yaml``). Any
problem stops the load with a message that names the file and the record.

:class:`Panel` is the loaded result. :meth:`Panel.summary` gives the counts the
report and the interface show (records, verified, unverified, by kind).
Nothing here reaches the network.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from visionhousing.evidence.schema import ComparisonRecord, DiscoveryEntry, Tracker
from visionhousing.ledger.loader import ROOT

__all__ = ["PANEL_DIR", "Panel", "PanelError", "load_panel"]

PANEL_DIR = ROOT / "assumptions" / "evidence" / "states"
STUDIES_FILE = "studies.yaml"
DISCOVERY_FILE = "discovery.yaml"


class PanelError(ValueError):
    """A panel file is malformed; the message names the file and the record."""


@dataclass(frozen=True)
class Panel:
    """Every comparison record, discovery candidate and tracker, as loaded and checked."""

    records: tuple[ComparisonRecord, ...]
    discovery: tuple[DiscoveryEntry, ...] = ()
    trackers: tuple[Tracker, ...] = ()
    files: tuple[str, ...] = field(default=())

    def summary(self) -> dict[str, Any]:
        """Counts of records by kind and verification, and of discovery candidates."""
        verified = sum(r.verified for r in self.records)
        return {
            "records": len(self.records),
            "verified": verified,
            "unverified": len(self.records) - verified,
            "by_kind": dict(Counter(r.kind for r in self.records)),
            "states_with_records": len({r.state for r in self.records if r.state != "US"}),
            "discovery_candidates": len(self.discovery),
            "discovery_states": len({d.state for d in self.discovery}),
        }

    def by_id(self) -> dict[str, ComparisonRecord]:
        """Records keyed by id."""
        return {r.id: r for r in self.records}


def _describe(err: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'entry'}: {e['msg']}" for e in err.errors()
    )[:600]


def _read(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PanelError(f"{path.name}: not valid YAML ({exc})") from None
    if not isinstance(data, dict):
        raise PanelError(f"{path.name}: the file must be a mapping with a 'records' list")
    return data


def _records(path: Path, data: dict[str, Any]) -> list[ComparisonRecord]:
    out = []
    for i, raw in enumerate(data.get("records") or []):
        label = raw.get("id") if isinstance(raw, dict) else None
        try:
            rec = ComparisonRecord.model_validate(raw)
        except ValidationError as err:
            raise PanelError(
                f"{path.name}: record {label or f'#{i + 1}'}: {_describe(err)}"
            ) from None
        expected = STUDIES_FILE if rec.state == "US" else f"{rec.state.lower()}.yaml"
        if path.name not in (expected, STUDIES_FILE):
            raise PanelError(f"{path.name}: record {rec.id} belongs in {expected}")
        out.append(rec)
    return out


def load_panel(folder: Path | None = None) -> Panel:
    """Read and check every panel file in ``folder`` (default ``assumptions/evidence/states``).

    Raises:
        PanelError: a file is malformed, a record fails a check, or an id repeats.
    """
    folder = Path(folder) if folder is not None else PANEL_DIR
    if not folder.is_dir():
        raise PanelError(f"no panel folder at {folder}")
    records: list[ComparisonRecord] = []
    discovery: list[DiscoveryEntry] = []
    trackers: list[Tracker] = []
    files = []
    for path in sorted(folder.glob("*.yaml")):
        data = _read(path)
        files.append(path.name)
        if path.name == DISCOVERY_FILE:
            for key, model, sink in (
                ("trackers", Tracker, trackers),
                ("entries", DiscoveryEntry, discovery),
            ):
                for i, raw in enumerate(data.get(key) or []):
                    try:
                        sink.append(model.model_validate(raw))
                    except ValidationError as err:
                        raise PanelError(f"{path.name}: {key} #{i + 1}: {_describe(err)}") from None
            continue
        records.extend(_records(path, data))
    seen: Counter[str] = Counter(r.id for r in records)
    dupes = sorted(k for k, n in seen.items() if n > 1)
    if dupes:
        raise PanelError(f"record ids repeat: {', '.join(dupes)}")
    records.sort(key=lambda r: (r.state, r.id))
    return Panel(tuple(records), tuple(discovery), tuple(trackers), tuple(files))
