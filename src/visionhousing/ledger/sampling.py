"""Seeded random draws from the ledger's likely ranges.

The Monte Carlo step needs many plausible values of each parameter. This module
draws them, all at once for each parameter (vectorised with numpy).

Shapes:
- ``fixed``: every draw is the central value.
- ``triangular``: a straight-sided peak at the central value, falling to zero
  at low and high.
- ``pert``: a smooth hump, also peaking at the central value, that puts less
  weight near the ends than a triangle. It is a beta distribution scaled to
  ``[low, high]`` with shape values ``1 + 4 (central - low) / (high - low)`` and
  ``1 + 4 (high - central) / (high - low)``. Its mean is
  ``(low + 4 central + high) / 6``.

Same seed, same draws. Each parameter gets its own random stream, built from the
seed and the parameter's id, so adding or removing one parameter never changes
the draws of any other.

**Joint order (sensitivity runs only).** :func:`joint_ranks` lets a named
sensitivity run (``engine.correlated``) say, for some parameters, in which
order their draws should fall: each such parameter's own draws are sorted and
placed by the given ranks. Every parameter keeps exactly its own values (its
range and shape are unchanged); only which draw gets which value changes, so
parameters can move together. Outside that context nothing changes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

import numpy as np

from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.schema import Parameter

DEFAULT_DRAWS = 2000

_JOINT: ContextVar[tuple[int, int, Mapping[str, np.ndarray]] | None] = ContextVar(
    "visionhousing_joint_ranks", default=None
)


@contextmanager
def joint_ranks(seed: int, n: int, ranks: Mapping[str, np.ndarray]) -> Iterator[None]:
    """Within the block, :func:`sample` places some parameters' draws in a given order.

    Args:
        seed: The run's seed (other seeds are not touched).
        n: The run's number of draws (other sizes are not touched).
        ranks: Parameter id to the rank (0 to ``n - 1``) of each draw's value.
    """
    for pid, r in ranks.items():
        if np.asarray(r).shape != (n,):
            raise ValueError(f"ranks for '{pid}' must have shape ({n},)")
    token = _JOINT.set((int(seed), int(n), dict(ranks)))
    try:
        yield
    finally:
        _JOINT.reset(token)


def stream(seed: int, parameter_id: str) -> np.random.Generator:
    """Return the random generator for one parameter under one seed.

    Args:
        seed: The run's seed (a non-negative integer).
        parameter_id: The parameter's id.

    Returns:
        A numpy generator that depends only on ``seed`` and ``parameter_id``.
    """
    if seed < 0:
        raise ValueError("seed must be a non-negative integer")
    key = int.from_bytes(hashlib.sha256(parameter_id.encode()).digest()[:8], "big")
    return np.random.default_rng(np.random.SeedSequence([seed, key]))


def draw(param: Parameter, n: int, rng: np.random.Generator) -> np.ndarray:
    """Draw ``n`` values of one parameter.

    Args:
        param: The parameter.
        n: Number of draws (at least 1).
        rng: Random generator to use.

    Returns:
        A float array of length ``n``, every value inside ``[low, high]``.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    low, mode, high = param.low, param.central, param.high
    if param.shape == "fixed":
        return np.full(n, mode, dtype=float)
    if param.shape == "triangular":
        return rng.triangular(low, mode, high, size=n)
    width = high - low
    alpha = 1.0 + 4.0 * (mode - low) / width
    beta = 1.0 + 4.0 * (high - mode) / width
    return low + width * rng.beta(alpha, beta, size=n)


def sample(
    ledger: Ledger,
    n: int = DEFAULT_DRAWS,
    seed: int = 0,
    ids: Iterable[str] | None = None,
) -> dict[str, np.ndarray]:
    """Draw ``n`` values of every parameter (or of the ids given).

    Args:
        ledger: The ledger to sample.
        n: Number of draws per parameter.
        seed: The run's seed.
        ids: Only these parameters. Defaults to all.

    Returns:
        Parameter id to an array of ``n`` draws.
    """
    chosen = list(ids) if ids is not None else list(ledger)
    out = {pid: draw(ledger[pid], n, stream(seed, pid)) for pid in chosen}
    joint = _JOINT.get()
    if joint is not None and joint[0] == seed and joint[1] == n:
        for pid in chosen:
            r = joint[2].get(pid)
            if r is not None:
                out[pid] = np.sort(out[pid])[np.asarray(r, dtype=int)]
    return out


def percentiles(values: np.ndarray) -> tuple[float, float, float]:
    """Return the 10th, 50th and 90th percentiles of some draws.

    Args:
        values: One-dimensional array of draws.

    Returns:
        ``(p10, p50, p90)``.
    """
    p10, p50, p90 = np.percentile(np.asarray(values, dtype=float), [10, 50, 90])
    return float(p10), float(p50), float(p90)
