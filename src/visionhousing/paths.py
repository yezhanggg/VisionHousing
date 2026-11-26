"""Safe file paths built from outside input.

Plain-language summary
----------------------
Some values that reach the code come from outside: a state code or a place id
in a web address, a reform id in a request. When such a value becomes part of
a file path, a crafted value such as ``../../etc/passwd`` could point the code
at a file outside the data folders ("path traversal"). This module is the one
place that turns outside values into paths, so the check is written once.

- :func:`state_code` accepts only two capital letters (``PA``, ``TL``) and,
  when given the list of known codes, only one of those.
- :func:`safe_child` joins a base folder with one or more name parts. Each part
  must be a plain name: letters, digits, ``_``, ``-`` and ``.``, not starting
  with a dot, with no ``..``, no slash or backslash, no NUL byte and not an
  absolute path. The joined path is then normalised and checked to lie inside
  the base folder, both as written and with links resolved.

Both raise :class:`ValueError` (as :class:`UnsafePathError`) on a bad value.
The web interface turns that into a 400 reply, so a bad value never touches a
file. This module imports nothing from the rest of the package, so every part
of the code can use it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = ["UnsafePathError", "safe_child", "state_code"]

_STATE = re.compile(r"[A-Z]{2}")
_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}")


class UnsafePathError(ValueError):
    """An outside value that cannot safely become part of a file path."""


def state_code(value: Any, known: Iterable[str] | None = None) -> str:
    """Check a state code and return it in capitals.

    Args:
        value: The code as given (``"pa"`` is accepted and returned as ``"PA"``).
        known: Optional list of allowed codes (for example the built packs).

    Returns:
        The two-letter code.

    Raises:
        UnsafePathError: Not exactly two letters, or not in ``known``.
    """
    if not isinstance(value, str):
        raise UnsafePathError("state must be a two-letter code")
    code = value.upper()
    if not _STATE.fullmatch(code):
        raise UnsafePathError(f"state must be a two-letter code, got {value[:20]!r}")
    if known is not None and code not in set(known):
        raise UnsafePathError(f"unknown state code {code!r}")
    return code


def _check_part(part: Any) -> str:
    if not isinstance(part, str) or not _PART.fullmatch(part) or ".." in part:
        shown = repr(part)[:40]
        raise UnsafePathError(f"{shown} is not a plain file or folder name")
    return part


def safe_child(base: Path | str, *parts: str) -> Path:
    """Return ``base / part / ...`` after checking it stays inside ``base``.

    Args:
        base: The trusted folder (a constant of the code, never outside input).
        *parts: One or more plain names; each is checked on its own.

    Returns:
        The joined, normalised absolute path.

    Raises:
        UnsafePathError: A part is not a plain name, or the result (as written
            or with links resolved) is not inside ``base``.
    """
    if not parts:
        raise UnsafePathError("no file or folder name given")
    names = [_check_part(p) for p in parts]
    root = os.path.normpath(os.path.abspath(os.fspath(base)))
    full = os.path.normpath(os.path.join(root, *names))
    if not full.startswith(root + os.sep):
        raise UnsafePathError("the path leads outside its data folder")
    real_root = os.path.realpath(root)
    if not os.path.realpath(full).startswith(real_root + os.sep):
        raise UnsafePathError("the path leads outside its data folder through a link")
    return Path(full)
