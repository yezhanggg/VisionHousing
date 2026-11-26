"""Pre-registration check: a study scores only if its fixed rules match the recorded fingerprint.

Plain-language summary
----------------------
Three studies fix their rules in advance: the donor-pool study and its two
later tests (``assumptions/validation_donor_pool.yaml``) and the retrospective
reform panel (``assumptions/evidence/retro_design.yaml``). A rule written in
advance protects against fitting the rule to the answer only if nobody can
change it quietly afterwards.

So each fixed block has a **fingerprint**: the SHA-256 of the block written as
canonical JSON (keys sorted, no spaces; YAML comments do not count, any changed
value does). The expected fingerprints live in a separate file,
``assumptions/preregistration.yaml``, with the date each was recorded. Before a
study computes any score, :func:`verify` recomputes the fingerprint of the
block it is about to use and compares it with the recorded one. On a mismatch
the study refuses to run (:class:`PreregistrationError`) and says which block
changed. Changing a rule on purpose means recording a new fingerprint in that
file, with the date and the reason, so the change is visible.

What this protects, honestly: the fingerprints were first recorded in that
file on 2026-11-24, after the studies had already been run once. Two of them
(the second and third donor-pool tests) had also been logged in the decision
log before their first scores; the others were not. The file therefore proves
that the rules used from now on are the ones published with the results; it
cannot prove what happened before the record was made.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "REGISTRY_PATH",
    "PreregistrationError",
    "block_of",
    "fingerprint",
    "load_registry",
    "verify",
]

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "assumptions" / "preregistration.yaml"
METHOD = "SHA-256 of json.dumps(block, sort_keys=True, separators=(',', ':'), default=str)"


class PreregistrationError(RuntimeError):
    """A pre-registered block differs from its recorded fingerprint, so nothing is scored."""


def fingerprint(block: Any) -> str:
    """SHA-256 of a block as canonical JSON (keys sorted, no spaces; dates as ISO text)."""
    text = json.dumps(block, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """Read ``assumptions/preregistration.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def block_of(entry: Mapping[str, Any], data: Mapping[str, Any]) -> Any:
    """The part of a loaded settings file that one registry entry fingerprints.

    ``key``: one top-level block; ``keys``: those top-level keys as a mapping
    (a missing key counts as ``None``, so removing one changes the fingerprint);
    neither: the whole file.
    """
    if entry.get("key"):
        return data.get(entry["key"])
    if entry.get("keys"):
        return {k: data.get(k) for k in entry["keys"]}
    return dict(data)


def verify(
    name: str,
    data: Mapping[str, Any],
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check one block against its recorded fingerprint; raise before anything is scored.

    Args:
        name: The block's name in the registry (for example ``pooled_states_test``).
        data: The loaded settings file the study is about to use.
        registry: The registry (default: read ``assumptions/preregistration.yaml``).

    Returns:
        ``{"block", "file", "sha256", "recorded_on", "verified": True}`` for the result's
        provenance.

    Raises:
        PreregistrationError: The block is not registered, or its fingerprint differs.
    """
    reg = registry if registry is not None else load_registry()
    entry = (reg.get("blocks") or {}).get(name)
    if entry is None:
        raise PreregistrationError(
            f"block '{name}' has no recorded fingerprint in assumptions/preregistration.yaml"
        )
    got = fingerprint(block_of(entry, data))
    want = str(entry["sha256"])
    if got != want:
        raise PreregistrationError(
            f"pre-registered block '{name}' ({entry.get('file')}) has changed since its "
            f"fingerprint was recorded on {entry.get('recorded_on')}: expected {want[:12]}..., "
            f"got {got[:12]}.... Refusing to score. A deliberate change needs a new entry in "
            "assumptions/preregistration.yaml with its date and reason."
        )
    return {
        "block": name,
        "file": entry.get("file"),
        "sha256": got,
        "recorded_on": str(entry.get("recorded_on")),
        "verified": True,
    }
