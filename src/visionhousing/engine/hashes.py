"""The three fingerprints that identify a run.

Plain-language summary
----------------------
A result is only reproducible if you know exactly what produced it. Every run
carries three hashes (SHA-256 fingerprints; change one byte and the
fingerprint changes):

- **code**: the engine's source code, every ``.py`` file of the
  ``visionhousing`` package, in a fixed order.
- **data**: the state's data files (the state pack, or Testland).
- **assumptions**: the Assumption Ledger after this run's overrides, plus the
  calibration tables and the scenario file.

They are printed on every export. Two runs with the same three hashes, the
same reforms, the same seed and the same number of draws give the same numbers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from visionhousing.ledger import Ledger

__all__ = ["Hashes", "assumption_hash", "code_hash"]

PACKAGE_DIR = Path(__file__).resolve().parents[1]
"""The ``visionhousing`` package folder."""


@dataclass(frozen=True)
class Hashes:
    """The three fingerprints of a run.

    Attributes:
        code: Hash of the package's source files.
        data: Hash of the state's data files.
        assumptions: Hash of the ledger (after overrides) and calibration files.
    """

    code: str
    data: str
    assumptions: str

    def short(self) -> dict[str, str]:
        """Return the first 12 characters of each hash, for footers and labels."""
        return {
            "code": self.code[:12],
            "data": self.data[:12],
            "assumptions": self.assumptions[:12],
        }

    def as_tuple(self) -> tuple[str, str, str]:
        """Return ``(code, data, assumptions)``."""
        return (self.code, self.data, self.assumptions)


@lru_cache(maxsize=1)
def _code_hash(folder: str) -> str:
    root = Path(folder)
    digest = hashlib.sha256(b"visionhousing-code-v1")
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def code_hash(folder: Path | None = None) -> str:
    """Return the fingerprint of the package's source code.

    Computed once per process (source files do not change while it runs).

    Args:
        folder: Package folder; defaults to the installed ``visionhousing`` package.

    Returns:
        A 64-character SHA-256 hex digest.
    """
    return _code_hash(str(folder or PACKAGE_DIR))


def assumption_hash(ledger: Ledger, calibration_hash: str) -> str:
    """Return the fingerprint of the assumption set used by a run.

    Args:
        ledger: The ledger after the run's overrides.
        calibration_hash: Fingerprint of the calibration tables and scenario file.

    Returns:
        A 64-character SHA-256 hex digest.
    """
    text = f"visionhousing-assumptions-v1\n{ledger.hash()}\n{calibration_hash}"
    return hashlib.sha256(text.encode()).hexdigest()
