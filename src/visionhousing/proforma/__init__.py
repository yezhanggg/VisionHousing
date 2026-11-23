"""Simple development pro formas: does a project pencil out?

Pure functions that compare the cost of building an accessory dwelling, a small
multifamily building or a larger multifamily building with what it would earn.

- :func:`adu_proforma`: a homeowner adds an ADU; gives the break-even rent and
  the break-even home value.
- :func:`redevelopment_proforma`: a 2-6 unit building replaces a single-family
  home; gives the residual land value.
- :func:`midrise_proforma`: a mid-rise apartment building with structured
  parking; gives the residual land value.

Each takes a parameter dict (ledger id to a number or to an array of draws) and
tract context, works on whole arrays of tracts and draws at once, and returns
every line plus a ``records()`` method that writes out each line's formula with
the ledger ids behind it. Fees and parking are always separate lines.
See ``docs/modules/proforma.md``.
"""

from visionhousing.proforma._calc import Input, ProFormaResult, as_draw_column, payment_factor
from visionhousing.proforma.adu import adu_proforma
from visionhousing.proforma.residual import (
    MIDRISE,
    REDEVELOPMENT,
    Intensity,
    midrise_proforma,
    redevelopment_proforma,
    residual_proforma,
)

__all__ = [
    "MIDRISE",
    "REDEVELOPMENT",
    "Input",
    "Intensity",
    "ProFormaResult",
    "adu_proforma",
    "as_draw_column",
    "midrise_proforma",
    "payment_factor",
    "redevelopment_proforma",
    "residual_proforma",
]
