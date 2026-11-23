"""Read the ledger file, check every entry, merge overrides, and hash the set.

``load_ledger`` reads ``assumptions/parameters.yaml``. Each parameter is checked
on its own, so an error message always names the parameter that is wrong.

``apply_overrides`` returns a new ledger with some values changed, for example
when a user picks the high end of a range or types their own value. The original
ledger is never modified.

``Ledger.hash`` is a fingerprint of the whole assumption set. The same values give
the same hash on any machine, whatever order the file lists them in; change any
value and the hash changes. Every run and every export prints it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from visionhousing.ledger.schema import PARAMETER_ID, Parameter

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = ROOT / "assumptions" / "parameters.yaml"
HASH_PREFIX = "visionhousing-ledger-v1"


class LedgerError(ValueError):
    """The ledger file, or an override, is not valid. The message names the parameter."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that refuses a key listed twice in the same mapping."""


def _no_duplicate_keys(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise LedgerError(f"key '{key}' appears twice (line {key_node.start_mark.line + 1})")
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_duplicate_keys,
)


def read_yaml(path: Path) -> Any:
    """Read a YAML file safely, refusing duplicate keys.

    Args:
        path: File to read.

    Returns:
        The parsed content.

    Raises:
        LedgerError: The file is missing or is not valid YAML.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"{path.name}: cannot read the file ({exc.strerror})") from exc
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 (a SafeLoader subclass)
    except yaml.YAMLError as exc:
        raise LedgerError(f"{path.name}: not valid YAML: {exc}") from exc


def describe_errors(exc: ValidationError) -> str:
    """Turn a pydantic error into one short line per problem.

    Args:
        exc: The validation error.

    Returns:
        Problems joined by "; ", each as "field: message".
    """
    parts = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "entry"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)


class Ledger:
    """A checked, read-only set of parameters.

    Args:
        parameters: Parameter id to its checked entry.
        version: The ledger file's format version.
    """

    def __init__(self, parameters: Mapping[str, Parameter], version: int = 1):
        self._params = dict(sorted(parameters.items()))
        self.version = version

    def __getitem__(self, parameter_id: str) -> Parameter:
        try:
            return self._params[parameter_id]
        except KeyError:
            raise LedgerError(f"parameter '{parameter_id}' is not in the ledger") from None

    def __contains__(self, parameter_id: object) -> bool:
        return parameter_id in self._params

    def __iter__(self) -> Iterator[str]:
        return iter(self._params)

    def __len__(self) -> int:
        return len(self._params)

    def items(self) -> Iterator[tuple[str, Parameter]]:
        """Yield ``(id, parameter)`` pairs in id order."""
        yield from self._params.items()

    def central(self) -> dict[str, float]:
        """Return every parameter's central value, by id."""
        return {pid: p.central for pid, p in self._params.items()}

    def used_by(self, user: str) -> list[str]:
        """Return the ids of parameters that list ``user`` in ``used_by``."""
        return [pid for pid, p in self._params.items() if user in p.used_by]

    def as_dict(self) -> dict[str, Any]:
        """Return the ledger as plain JSON-ready data, ids sorted."""
        return {
            "version": self.version,
            "parameters": {pid: p.model_dump(mode="json") for pid, p in self._params.items()},
        }

    def hash(self) -> str:
        """Return a stable SHA-256 fingerprint of the whole assumption set.

        The text hashed is canonical JSON (sorted keys, no spaces), so it does not
        depend on file order, comments, or YAML layout.
        """
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{HASH_PREFIX}\n{canonical}".encode()).hexdigest()

    def short_hash(self) -> str:
        """Return the first 12 characters of ``hash()``, for footers and labels."""
        return self.hash()[:12]


def parse_ledger(data: Any, name: str = "parameters.yaml") -> Ledger:
    """Check already-parsed ledger data and build a ``Ledger``.

    Args:
        data: The parsed YAML: a mapping with ``version`` and ``parameters``.
        name: File name used in error messages.

    Returns:
        The checked ledger.

    Raises:
        LedgerError: Something is wrong. Every bad parameter is named.
    """
    if not isinstance(data, dict) or not isinstance(data.get("parameters"), dict):
        raise LedgerError(f"{name}: expected a top-level 'parameters' mapping")
    # Keys starting with "_" hold shared YAML anchors (for example "_sources") and are skipped.
    unknown_top = sorted(
        k for k in data if k not in ("version", "parameters") and not str(k).startswith("_")
    )
    if unknown_top:
        raise LedgerError(f"{name}: unknown top-level keys {unknown_top}")
    problems: list[str] = []
    params: dict[str, Parameter] = {}
    for pid, entry in data["parameters"].items():
        if not isinstance(pid, str) or not PARAMETER_ID.match(pid):
            problems.append(f"parameter '{pid}': id must look like 'group.name' in lower case")
            continue
        if not isinstance(entry, dict):
            problems.append(f"parameter '{pid}': expected a mapping of fields")
            continue
        try:
            params[pid] = Parameter.model_validate(entry)
        except ValidationError as exc:
            problems.append(f"parameter '{pid}': {describe_errors(exc)}")
    if problems:
        raise LedgerError(f"{name}: " + " | ".join(problems))
    if not params:
        raise LedgerError(f"{name}: the 'parameters' mapping is empty")
    return Ledger(params, version=int(data.get("version", 1)))


def load_ledger(path: Path | str | None = None) -> Ledger:
    """Load and check the ledger file.

    Args:
        path: File to read. Defaults to ``assumptions/parameters.yaml``.

    Returns:
        The checked ledger.

    Raises:
        LedgerError: The file is missing, is not YAML, or has a bad entry.
    """
    path = Path(path) if path is not None else DEFAULT_PATH
    return parse_ledger(read_yaml(path), name=path.name)


Override = float | int | str | Mapping[str, Any]
"""One override: a number, ``"low"`` / ``"central"`` / ``"high"``, or a mapping of fields."""


def _merged_entry(pid: str, param: Parameter, change: Override) -> dict[str, Any]:
    entry = param.model_dump()
    if isinstance(change, bool):
        raise LedgerError(f"override for parameter '{pid}': expected a number, got {change!r}")
    if isinstance(change, int | float):
        value = float(change)
        entry.update(central=value, low=value, high=value, shape="fixed")
    elif isinstance(change, str):
        if change not in ("low", "central", "high"):
            raise LedgerError(
                f"override for parameter '{pid}': expected 'low', 'central' or 'high', "
                f"got {change!r}"
            )
        value = entry[change]
        entry.update(central=value, low=value, high=value, shape="fixed")
    elif isinstance(change, Mapping):
        entry.update(change)
    else:
        raise LedgerError(f"override for parameter '{pid}': unsupported value {change!r}")
    return entry


def apply_overrides(ledger: Ledger, overrides: Mapping[str, Override] | None) -> Ledger:
    """Return a new ledger with some parameters changed.

    A number pins the parameter at that value (shape ``fixed``). ``"low"``,
    ``"central"`` or ``"high"`` pins it at that end of its range. A mapping replaces
    only the fields it names, for example ``{"low": 0.1, "high": 0.4}``. Every
    changed entry is checked again.

    Args:
        ledger: The ledger to start from. It is not modified.
        overrides: Parameter id to its change.

    Returns:
        A new ledger.

    Raises:
        LedgerError: An id is unknown or a changed entry is not valid.
    """
    if not overrides:
        return ledger
    params = dict(ledger.items())
    for pid, change in overrides.items():
        if pid not in params:
            raise LedgerError(f"override for parameter '{pid}': not in the ledger")
        try:
            params[pid] = Parameter.model_validate(_merged_entry(pid, params[pid], change))
        except ValidationError as exc:
            raise LedgerError(f"override for parameter '{pid}': {describe_errors(exc)}") from exc
    return Ledger(params, version=ledger.version)
